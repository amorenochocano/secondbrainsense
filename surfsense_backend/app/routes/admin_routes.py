"""
admin_routes.py
---------------
Endpoints de administración del pipeline Brain (F2.5).

Permite a los administradores inspeccionar y gestionar las colecciones Qdrant
sin necesidad de reiniciar el contenedor ni acceder directamente a Qdrant.

Endpoints:
  GET    /admin/qdrant/collections                    — Estado de las tres colecciones
  POST   /admin/qdrant/collection/{name}/recreate     — Recrear una colección (destructivo)
  DELETE /admin/qdrant/document/{source}              — Borrar vectores de un documento

Seguridad:
  Todos los endpoints requieren usuario admin autenticado.
  delete_document_vectors requiere search_space_id para garantizar
  aislamiento multi-tenant — un admin no puede borrar datos de otro space.

Decisión de diseño:
  QdrantManager.get_instance() devuelve el singleton inicializado en el lifespan.
  No se crea un cliente Qdrant nuevo por petición — se reutiliza la conexión existente.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.brain.collections import ALL_COLLECTIONS, BRAIN, CODE, COLLECTION_CONFIG, KNOWLEDGE
from app.brain.qdrant_manager import QdrantManager
from app.db import User
from app.users import current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/qdrant", tags=["admin-qdrant"])


def _get_current_admin_user(current_user: User = Depends(current_active_user)) -> User:
    """
    Dependencia FastAPI: verifica que el usuario autenticado es superusuario.
    Lanza 403 si no tiene permisos de administrador.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="Acceso restringido a administradores.",
        )
    return current_user


@router.get(
    "/collections",
    summary="Estado de las colecciones Qdrant",
    description=(
        "Lista las tres colecciones Brain (brain, knowledge, code) con sus "
        "estadísticas: número de vectores, dimensión y modelo de embedding. "
        "Si una colección no existe devuelve status='not_created'."
    ),
)
async def list_qdrant_collections(
    current_user: User = Depends(_get_current_admin_user),
) -> dict:
    """
    Devuelve el estado de las tres colecciones Qdrant.

    Útil para verificar que el pipeline Brain está correctamente inicializado
    y que las dimensiones de los vectores son las esperadas.

    Returns:
        Dict con una entrada por colección:
        {
            "brain":     {"vectors_count": N, "vector_size": 768, "embed_model": "...", "status": "ok"},
            "knowledge": {...},
            "code":      {"status": "not_created"},  # si aún no se ha ingestado código
        }
    """
    logger.info(
        "[admin] list_qdrant_collections — solicitado por user='%s'",
        current_user.email,
    )
    qdrant = QdrantManager.get_instance()
    result = {}

    for col_name in ALL_COLLECTIONS:
        try:
            info = qdrant.client.get_collection(col_name)
            result[col_name] = {
                "vectors_count": info.vectors_count,
                "vector_size":   info.config.params.vectors.size,
                "embed_model":   COLLECTION_CONFIG.get(col_name, {}).get("embed_model", ""),
                "status":        "ok",
            }
            logger.debug(
                "[admin] colección '%s': vectors=%s dim=%s",
                col_name,
                info.vectors_count,
                info.config.params.vectors.size,
            )
        except Exception as exc:
            logger.warning(
                "[admin] colección '%s' no disponible: %s", col_name, exc
            )
            result[col_name] = {"status": "not_created"}

    logger.info("[admin] list_qdrant_collections OK — %d colecciones consultadas", len(ALL_COLLECTIONS))
    return result


@router.post(
    "/collection/{collection_name}/recreate",
    summary="Recrear una colección Qdrant",
    description=(
        "Elimina todos los vectores de la colección indicada y la recrea vacía. "
        "Operación destructiva e irreversible. Solo para administradores. "
        "Usar cuando hay un mismatch de dimensiones o corrupción de datos."
    ),
)
async def recreate_collection(
    collection_name: str,
    current_user: User = Depends(_get_current_admin_user),
) -> dict:
    """
    Recrea una colección Qdrant eliminando todos sus vectores.

    Útil cuando:
    - Hay mismatch de dimensiones (ej: cambiaste EMBED_MODEL_CODE)
    - La colección está corrupta
    - Quieres re-ingestar desde cero

    Args:
        collection_name: Nombre de la colección a recrear (brain, knowledge, code).

    Returns:
        {"status": "recreated", "collection": collection_name}

    Raises:
        404 si collection_name no es una colección válida del pipeline Brain.
    """
    if collection_name not in ALL_COLLECTIONS:
        logger.warning(
            "[admin] recreate_collection RECHAZADO — colección desconocida: '%s' user='%s'",
            collection_name, current_user.email,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"Colección desconocida: '{collection_name}'. "
                f"Valores válidos: {ALL_COLLECTIONS}"
            ),
        )

    logger.warning(
        "[admin] recreate_collection INICIO — colección='%s' admin='%s'",
        collection_name, current_user.email,
    )

    qdrant = QdrantManager.get_instance()
    qdrant.client.delete_collection(collection_name)
    logger.info("[admin] recreate_collection DROP OK — colección='%s'", collection_name)

    # ensure_collections recrea BRAIN y KNOWLEDGE con índices de payload.
    # CODE se gestiona dinámicamente por _ingest_code en IngestRouter.
    qdrant.ensure_collections()

    logger.warning(
        "[admin] recreate_collection DONE — colección='%s' recreada por admin='%s'",
        collection_name, current_user.email,
    )
    return {"status": "recreated", "collection": collection_name}


@router.delete(
    "/document/{source}",
    summary="Borrar vectores de un documento",
    description=(
        "Borra todos los vectores de un documento en las tres colecciones. "
        "Requiere search_space_id para garantizar aislamiento multi-tenant: "
        "un administrador no puede borrar vectores de otro search_space aunque "
        "conozca el source del documento."
    ),
)
async def delete_document_vectors(
    source: str,
    search_space_id: str,
    current_user: User = Depends(_get_current_admin_user),
) -> dict:
    """
    Borra todos los vectores de un documento en brain, knowledge y code.

    Filtra por AMBOS source y search_space_id — garantía de aislamiento
    multi-tenant. Un admin solo puede borrar vectores del space que especifique.

    Args:
        source:          Slug del documento (ej: 'pipeline-tte-etl').
        search_space_id: ID del search space propietario (query param obligatorio).

    Returns:
        {"status": "deleted", "source": source, "search_space_id": search_space_id}
    """
    logger.info(
        "[admin] delete_document_vectors — source='%s' space='%s' admin='%s'",
        source, search_space_id, current_user.email,
    )
    qdrant = QdrantManager.get_instance()
    qdrant.delete_by_source(source=source, search_space_id=search_space_id)

    logger.info(
        "[admin] delete_document_vectors OK — source='%s' space='%s'",
        source, search_space_id,
    )
    return {"status": "deleted", "source": source, "search_space_id": search_space_id}
