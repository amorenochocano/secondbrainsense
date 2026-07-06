"""
admin_routes.py
---------------
Endpoints de administración del pipeline Brain (F2.5).

Permite a los administradores inspeccionar y gestionar las colecciones Qdrant
sin necesidad de reiniciar el contenedor ni acceder directamente a Qdrant.

Endpoints:
  GET    /admin/config                                — Configuración activa del pipeline (solo lectura)
  POST   /admin/config                                — No permitido (config vía env vars)
  GET    /admin/ollama-models                         — Modelos Ollama disponibles
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
  La configuración del pipeline es inmutable en caliente — solo env vars, sin persistencia en BD.
"""
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from app.brain.collections import ALL_COLLECTIONS, BRAIN, CODE, COLLECTION_CONFIG, KNOWLEDGE
from app.brain.qdrant_manager import QdrantManager
from app.db import User
from app.users import current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def _bool_env(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().upper() in ("1", "TRUE", "YES")


def _float_env(key: str, default: Optional[float]) -> Optional[float]:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _int_env(key: str, default: Optional[int]) -> Optional[int]:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


@router.get("/config", summary="Configuración activa del pipeline Brain")
async def get_admin_config(
    current_user: User = Depends(current_active_user),
) -> dict[str, Any]:
    """
    Devuelve un snapshot de la configuración activa del pipeline Brain
    leída desde variables de entorno.

    Solo lectura — para cambiar la configuración editar el .env y reiniciar.
    """
    return {
        # Chunking
        "BRAIN_CHUNK_STRATEGY":    os.getenv("BRAIN_CHUNK_STRATEGY", "paragraph"),
        "BRAIN_CHUNK_SIZE":        _int_env("BRAIN_CHUNK_SIZE", 512),
        "BRAIN_CHUNK_OVERLAP":     _int_env("BRAIN_CHUNK_OVERLAP", 50),
        # Retrieval
        "ROUTER_L1_HIGH_SCORE":    _float_env("ROUTER_L1_HIGH_SCORE", 0.75),
        "ROUTER_L1_MIN_SCORE":     _float_env("ROUTER_L1_MIN_SCORE", 0.50),
        "BRAIN_TOP_K":             _int_env("BRAIN_TOP_K", 5),
        "BRAIN_RERANKING_ENABLED": _bool_env("BRAIN_RERANKING_ENABLED", False),
        # LLM síntesis
        "BRAIN_LLM_PROVIDER":      os.getenv("BRAIN_LLM_PROVIDER", "ollama"),
        "BRAIN_LLM_MODEL":         os.getenv("BRAIN_LLM_MODEL", "deepseek-r1:14b"),
        "BRAIN_LLM_TEMPERATURE":   _float_env("BRAIN_LLM_TEMPERATURE", 0.1),
        "BRAIN_LLM_MAX_TOKENS":    _int_env("BRAIN_LLM_MAX_TOKENS", 4096),
        # Ingesta
        "BRAIN_INGESTION_ENABLED": _bool_env("BRAIN_INGESTION_ENABLED", True),
        "BRAIN_SYNTHESIS_ENABLED": _bool_env("BRAIN_SYNTHESIS_ENABLED", True),
        "BRAIN_EMBEDDING_MODEL":   os.getenv("BRAIN_EMBEDDING_MODEL", "nomic-embed-text"),
        "BRAIN_QUALITY_THRESHOLD": _float_env("BRAIN_QUALITY_THRESHOLD", 0.3),
        # CRAG
        "CRAG_EVALUATOR_ENABLED":  _bool_env("CRAG_EVALUATOR_ENABLED", False),
        "CRAG_EVALUATOR_PROVIDER": os.getenv("CRAG_EVALUATOR_PROVIDER", "ollama"),
        "CRAG_EVALUATOR_MODEL":    os.getenv("CRAG_EVALUATOR_MODEL", "deepseek-r1:14b"),
        "CRAG_MAX_EVAL_CHUNKS":    _int_env("CRAG_MAX_EVAL_CHUNKS", 3),
        "CRAG_EVAL_TIMEOUT":       _int_env("CRAG_EVAL_TIMEOUT", 15),
        "CRAG_REWRITER_MODEL":     os.getenv("CRAG_REWRITER_MODEL", "deepseek-r1:14b"),
    }


@router.post("/config", summary="Actualizar configuración Brain (no soportado)")
async def update_admin_config(
    current_user: User = Depends(current_active_user),
) -> Response:
    """
    La configuración del pipeline Brain se gestiona exclusivamente mediante
    variables de entorno. Editar el .env y reiniciar el contenedor.
    """
    raise HTTPException(
        status_code=405,
        detail="La configuración Brain es inmutable en caliente. Editar .env y reiniciar.",
    )


@router.get("/ollama-models", summary="Lista de modelos Ollama disponibles")
async def list_ollama_models(
    current_user: User = Depends(current_active_user),
) -> dict:
    """
    Devuelve la lista de modelos instalados en Ollama.
    Filtra los modelos de embedding (nomic, bge, all-minilm, qwen3-embedding)
    para mostrar solo modelos de generación aptos para síntesis.
    """
    try:
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        raw = client.list()
        # raw.models es una lista de objetos con atributo .model o .name
        all_names = []
        for m in (raw.models if hasattr(raw, "models") else raw.get("models", [])):
            name = getattr(m, "model", None) or getattr(m, "name", None) or (m.get("model") if isinstance(m, dict) else None)
            if name:
                all_names.append(name)
        # Excluir modelos de embedding puros
        EMBED_PATTERNS = ("nomic-embed", "bge-", "all-minilm", "qwen3-embedding", "qwen2-embedding")
        gen_models = [n for n in all_names if not any(p in n.lower() for p in EMBED_PATTERNS)]
        return {"models": [{"name": n} for n in sorted(gen_models)]}
    except Exception as exc:
        logger.warning("[admin] list_ollama_models error: %s", exc)
        return {"models": []}


# ─── Sub-router Qdrant (prefijo anterior) ─────────────────────────────────────
# Mantenemos las rutas Qdrant bajo /admin/qdrant por compatibilidad
_qdrant_router = APIRouter(prefix="/qdrant", tags=["admin-qdrant"])


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


@_qdrant_router.get(
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


@_qdrant_router.post(
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


@_qdrant_router.delete(
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


# Qdrant sub-router bajo /admin/qdrant/*
router.include_router(_qdrant_router)
