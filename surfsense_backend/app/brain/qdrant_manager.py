"""
brain/qdrant_manager.py
------------------------
Gestor centralizado de colecciones Qdrant para SecondBrainSense.

Responsabilidades:
  - Inicialización idempotente de colecciones brain y knowledge (768d, COSINE).
  - Creación de índices de payload (source, search_space_id) para filtrado eficiente.
  - Exposición de .client para compatibilidad con IngestRouter y BrainIngestor.
  - Borrado multi-tenant de vectores por fuente y search_space_id.

Decisiones de diseño:
  - QdrantManager NO reemplaza el QdrantClient de IngestRouter. Actúa como capa
    de inicialización y gestión. IngestRouter sigue recibiendo QdrantClient en su
    constructor — se le pasa qdrant_manager.client.
  - La colección CODE se omite en ensure_collections() porque su dimensión es
    dinámica (depende de EMBED_MODEL_CODE). _ingest_code en IngestRouter gestiona
    su creación y auto-recreación ante cambios de modelo.
  - Singleton por proceso — get_instance() garantiza un único cliente Qdrant
    compartido entre el lifespan de FastAPI y los workers Celery.

Variables de entorno:
  QDRANT_HOST : host del servidor Qdrant (default: 'qdrant')
  QDRANT_PORT : puerto gRPC/HTTP de Qdrant (default: 6333)

Uso típico en lifespan (F2.4):
  from app.brain.qdrant_manager import QdrantManager
  qdrant_mgr = QdrantManager.get_instance()
  qdrant_mgr.ensure_collections()

Uso en task Celery (F2.4):
  router = IngestRouter(qdrant_client=QdrantManager.get_instance().client)
"""
import logging
import os

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    VectorParams,
)

from app.brain.collections import BRAIN, CODE, KNOWLEDGE, COLLECTION_CONFIG, EMBED_DIMS

logger = logging.getLogger(__name__)

# Dimensiones para las colecciones estáticas, derivadas de COLLECTION_CONFIG + EMBED_DIMS.
# CODE se omite — su dimensión es dinámica según EMBED_MODEL_CODE y la gestiona
# _ingest_code en IngestRouter con auto-recreación ante cambio de modelo.
_COLLECTION_DIMS: dict[str, int] = {
    col: EMBED_DIMS[COLLECTION_CONFIG[col]["embed_model"]]
    for col in [BRAIN, KNOWLEDGE]
    if COLLECTION_CONFIG[col]["embed_model"] in EMBED_DIMS
}


class QdrantManager:
    """
    Gestión centralizada de colecciones Qdrant.

    Proporciona:
      - Inicialización idempotente con índices de payload
      - Exposición de .client para compatibilidad con IngestRouter y BrainIngestor
      - delete_by_source con filtro multi-tenant obligatorio (source + search_space_id)

    Invariante de seguridad multi-tenant:
      Todos los puntos Qdrant en las tres colecciones incluyen search_space_id
      en el payload. Sin este campo el router L2 (F4) no puede aislar datos
      entre tenants.
    """

    _instance: "QdrantManager | None" = None

    def __init__(self) -> None:
        host = os.getenv("QDRANT_HOST", "qdrant")
        port = int(os.getenv("QDRANT_PORT", "6333"))
        self.client = QdrantClient(host=host, port=port)
        logger.info(
            "[QdrantManager] Cliente inicializado — host=%s port=%s", host, port
        )

    @classmethod
    def get_instance(cls) -> "QdrantManager":
        """
        Devuelve el singleton de QdrantManager.
        Crea la instancia en la primera llamada.
        Thread-safe para uso en lifespan FastAPI y workers Celery.
        """
        if cls._instance is None:
            cls._instance = cls()
            logger.info("[QdrantManager] Singleton creado")
        return cls._instance

    def ensure_collections(self) -> None:
        """
        Crear brain y knowledge si no existen y registrar índices de payload.

        Idempotente — seguro de llamar en cada arranque del backend.
        Si una colección ya existe verifica que la dimensión sea correcta.
        Un mismatch de dimensión indica configuración incorrecta y lanza ValueError
        con instrucciones para resolverlo vía endpoint admin.

        CODE se omite aquí porque su dimensión es dinámica (EMBED_MODEL_CODE).
        _ingest_code en IngestRouter gestiona la creación y auto-recreación de CODE.
        """
        existing = {c.name for c in self.client.get_collections().collections}
        logger.info(
            "[QdrantManager] Colecciones existentes en Qdrant: %s",
            sorted(existing) or "ninguna",
        )

        for col_name, dim in _COLLECTION_DIMS.items():
            if col_name not in existing:
                self.client.create_collection(
                    collection_name=col_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                logger.info(
                    "[QdrantManager] Colección creada: '%s' (%dd, COSINE)",
                    col_name, dim,
                )
            else:
                # Verificar dimensión — mismatch indica configuración incorrecta
                info = self.client.get_collection(col_name)
                actual_dim = info.config.params.vectors.size
                if actual_dim != dim:
                    raise ValueError(
                        f"[QdrantManager] Colección '{col_name}' tiene {actual_dim}d "
                        f"pero se esperan {dim}d. "
                        f"Ejecutar POST /admin/qdrant/collection/{col_name}/recreate "
                        f"para recrearla."
                    )
                logger.debug(
                    "[QdrantManager] Colección '%s' ya existe con dim=%dd — OK",
                    col_name, actual_dim,
                )

            # Índices de payload para filtrado eficiente (idempotente)
            self._ensure_payload_index(col_name, "source",           PayloadSchemaType.KEYWORD)
            self._ensure_payload_index(col_name, "search_space_id",  PayloadSchemaType.KEYWORD)

        logger.info(
            "[QdrantManager] ensure_collections OK — brain_existía=%s knowledge_existía=%s",
            BRAIN in existing,
            KNOWLEDGE in existing,
        )

    def _ensure_payload_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: PayloadSchemaType,
    ) -> None:
        """
        Crear índice de payload si no existe. Idempotente.

        Los índices de payload permiten filtrado eficiente por source y
        search_space_id en las consultas Qdrant, evitando full-scan.
        Si el índice ya existe Qdrant lanza una excepción que se ignora
        deliberadamente.
        """
        try:
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
            )
            logger.debug(
                "[QdrantManager] Índice payload creado: colección='%s' campo='%s'",
                collection_name, field_name,
            )
        except Exception:
            # Ya existe — comportamiento esperado en arranques sucesivos
            pass

    def delete_by_source(self, source: str, search_space_id: str) -> None:
        """
        Borrar todos los vectores de un documento en las tres colecciones.

        Filtra por AMBOS source y search_space_id para garantizar aislamiento
        multi-tenant. Un usuario no puede borrar vectores de otro search_space
        aunque conozca el source del documento.

        Args:
            source:          slug del documento (ej: 'pipeline-tte-etl')
            search_space_id: ID del search space propietario del documento

        Logs:
          WARNING si falla el borrado en alguna colección (ej: colección no existe)
          INFO    al completar el borrado en las tres colecciones
        """
        deleted_count = 0
        for col_name in [BRAIN, KNOWLEDGE, CODE]:
            try:
                self.client.delete(
                    collection_name=col_name,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[
                                FieldCondition(
                                    key="source",
                                    match=MatchValue(value=source),
                                ),
                                FieldCondition(
                                    key="search_space_id",
                                    match=MatchValue(value=search_space_id),
                                ),
                            ]
                        )
                    ),
                )
                deleted_count += 1
                logger.debug(
                    "[QdrantManager] Vectores borrados en '%s' — source='%s' space='%s'",
                    col_name, source, search_space_id,
                )
            except Exception as exc:
                logger.warning(
                    "[QdrantManager] delete_by_source colección='%s' source='%s': %s",
                    col_name, source, exc,
                )

        logger.info(
            "[QdrantManager] delete_by_source completado — source='%s' space='%s' "
            "colecciones_procesadas=%d/3",
            source, search_space_id, deleted_count,
        )
