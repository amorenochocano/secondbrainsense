"""
unified_embedder.py
-------------------
Módulo centralizado de embedding para toda la plataforma SecondBrainSense.

PROPÓSITO
---------
Antes de F5, el embedding estaba duplicado en tres ficheros con Ollama hardcodeado.
Este módulo los unifica con un patrón multi-provider idéntico al de llm_client.py:
  - El PROVIDER se elige por variable de entorno (no hardcodeado en código)
  - Añadir un nuevo provider = añadir un elif en embed_single()
  - El modelo y el provider son parámetros independientes

PROVIDERS SOPORTADOS
--------------------
  "ollama"                → Ollama local (nomic-embed-text, nomic-embed-code, etc.)
  "sentence-transformers" → Modelos locales de HuggingFace (all-MiniLM-L6-v2, etc.)
                            Requiere: pip install sentence-transformers

  Futuros (no implementados, pero la arquitectura está lista):
  "openai"                → OpenAI Embeddings API (text-embedding-3-small, etc.)
  "cohere"                → Cohere Embed API (embed-multilingual-v3.0, etc.)
  "azure"                 → Azure OpenAI Embeddings

Para añadir un nuevo provider:
  1. Añadir elif en embed_single() con la lógica del provider
  2. Documentar las variables de entorno necesarias
  3. Listo — el resto del sistema lo usa automáticamente

MODELO
------
El modelo se configura independientemente del provider:
  BRAIN_EMBEDDING_MODEL=nomic-embed-text         (provider=ollama)
  BRAIN_EMBEDDING_MODEL=all-MiniLM-L6-v2         (provider=sentence-transformers)
  BRAIN_EMBEDDING_MODEL=text-embedding-3-small    (provider=openai, futuro)

API
---
  embed_query(text)          → list[float]        — para búsqueda (una query)
  embed_chunks(texts)        → list[list[float]]  — para indexación (batch de chunks)
  embed_single(text, model)  → list[float]        — uso interno con modelo específico

THREAD-SAFETY
-------------
  ollama.Client y SentenceTransformer son thread-safe.
  No se necesita lock adicional.

Variables de entorno:
  BRAIN_EMBEDDING_PROVIDER — provider de embedding (default: ollama)
  BRAIN_EMBEDDING_MODEL    — modelo principal (default: nomic-embed-text)
  OLLAMA_HOST              — URL de Ollama (solo si provider=ollama)
  OLLAMA_EMBED_TIMEOUT     — timeout en segundos (solo si provider=ollama, default: 120)
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

# ── Configuración (cero hardcode — todo desde entorno) ────────────────────────
BRAIN_EMBEDDING_PROVIDER = os.getenv("BRAIN_EMBEDDING_PROVIDER", "ollama")
BRAIN_EMBEDDING_MODEL    = os.getenv("BRAIN_EMBEDDING_MODEL",    "nomic-embed-text")
OLLAMA_HOST              = os.getenv("OLLAMA_HOST",              "http://localhost:11434")
OLLAMA_EMBED_TIMEOUT     = int(os.getenv("OLLAMA_EMBED_TIMEOUT", "120"))


# ── Caché lazy de modelos pesados ─────────────────────────────────────────────
# SentenceTransformer tarda ~2-5s en cargar — se cachea tras la primera llamada.
_st_model_cache: dict[str, object] = {}


# ── API pública ───────────────────────────────────────────────────────────────

def embed_query(text: str) -> list[float]:
    """
    Genera embedding para una query de búsqueda.

    Usa BRAIN_EMBEDDING_PROVIDER + BRAIN_EMBEDDING_MODEL — el mismo modelo
    y provider que se usa en la ingesta (brain_ingestion_adapter.py).
    Garantiza que query y documento están en el mismo espacio semántico.

    Es el reemplazo centralizado de:
      - config.embedding_model_instance.embed(text) en knowledge_search.py
      - _embed_nomic(text) en chunks_hybrid_search.py
      - _get_embedding(text) en router.py

    Args:
        text: Texto de la query a embedir.

    Returns:
        Vector de embedding (dimensión depende del modelo).
    """
    return embed_single(text, BRAIN_EMBEDDING_MODEL)


def embed_chunks(texts: list[str]) -> list[list[float]]:
    """
    Genera embeddings para una lista de chunks (batch).

    Procesa secuencialmente con el modelo activo. Si un chunk individual
    falla, se incluye un vector de ceros como placeholder para no romper
    el batch (el chunk sigue en PostgreSQL para BM25).

    Args:
        texts: Lista de textos a embedir.

    Returns:
        Lista de vectores (uno por texto), en el mismo orden.

    Nota sobre rendimiento por provider:
      ollama (nomic-embed-text CPU):        ~50ms por chunk
      sentence-transformers (MiniLM CPU):   ~20ms por chunk
      openai (text-embedding-3-small):      ~100ms por chunk (red)
    """
    if not texts:
        return []

    t0 = time.perf_counter()
    results: list[list[float]] = []

    for i, text in enumerate(texts):
        try:
            vec = embed_single(text, BRAIN_EMBEDDING_MODEL)
            results.append(vec)
        except Exception as exc:
            logger.warning(
                "[unified_embedder] chunk %d/%d falló (provider=%s model=%s): %s — vector ceros",
                i + 1, len(texts), BRAIN_EMBEDDING_PROVIDER, BRAIN_EMBEDDING_MODEL, exc,
            )
            dim = _get_model_dimension()
            results.append([0.0] * dim)

    elapsed = time.perf_counter() - t0
    logger.info(
        "[unified_embedder] embed_chunks: %d chunks en %.3fs (%.1f ms/chunk) provider=%s model=%s",
        len(texts), elapsed, (elapsed / len(texts) * 1000) if texts else 0,
        BRAIN_EMBEDDING_PROVIDER, BRAIN_EMBEDDING_MODEL,
    )
    return results


def embed_single(text: str, model: str) -> list[float]:
    """
    Genera embedding de un solo texto con el provider y modelo configurados.

    Dispatch por BRAIN_EMBEDDING_PROVIDER:
      "ollama"                → ollama.Client().embeddings(model, text)
      "sentence-transformers" → SentenceTransformer(model).encode(text)

    Si el modelo solicitado no está disponible, hace fallback automático
    al modelo por defecto del provider.

    Args:
        text:  Texto a embedir.
        model: Nombre del modelo (interpretado según el provider).

    Returns:
        Vector de embedding.

    Raises:
        ConnectionError: Si el provider no está disponible.
        ValueError: Si el provider no está soportado.
    """
    provider = BRAIN_EMBEDDING_PROVIDER.lower()

    if provider == "ollama":
        return _embed_ollama(text, model)

    if provider == "sentence-transformers":
        return _embed_sentence_transformers(text, model)

    # Futuros providers:
    # if provider == "openai":
    #     return _embed_openai(text, model)
    # if provider == "cohere":
    #     return _embed_cohere(text, model)

    raise ValueError(
        f"Provider de embedding no soportado: '{provider}'. "
        f"Valores válidos: 'ollama', 'sentence-transformers'. "
        f"Configura BRAIN_EMBEDDING_PROVIDER en el .env."
    )


# ── Implementaciones por provider ─────────────────────────────────────────────

def _embed_ollama(text: str, model: str) -> list[float]:
    """
    Embedding vía Ollama local.

    Variables: OLLAMA_HOST, OLLAMA_EMBED_TIMEOUT
    Modelos típicos: nomic-embed-text (768d), nomic-embed-code (2560d)
    Fallback: si el modelo no está disponible, intenta BRAIN_EMBEDDING_MODEL.
    """
    import ollama

    client = ollama.Client(host=OLLAMA_HOST, timeout=OLLAMA_EMBED_TIMEOUT)

    try:
        response = client.embeddings(model=model, prompt=text)
        return response["embedding"]
    except Exception as exc:
        if model != BRAIN_EMBEDDING_MODEL:
            logger.warning(
                "[unified_embedder] ollama modelo '%s' no disponible: %s → fallback '%s'",
                model, exc, BRAIN_EMBEDDING_MODEL,
            )
            try:
                response = client.embeddings(model=BRAIN_EMBEDDING_MODEL, prompt=text)
                return response["embedding"]
            except Exception as fallback_exc:
                logger.error(
                    "[unified_embedder] ollama fallback '%s' también falló: %s",
                    BRAIN_EMBEDDING_MODEL, fallback_exc, exc_info=True,
                )
                raise
        logger.error(
            "[unified_embedder] ollama embedding falló model=%s host=%s: %s",
            model, OLLAMA_HOST, exc, exc_info=True,
        )
        raise


def _embed_sentence_transformers(text: str, model: str) -> list[float]:
    """
    Embedding vía sentence-transformers (modelos HuggingFace locales).

    Modelos típicos: all-MiniLM-L6-v2 (384d), all-mpnet-base-v2 (768d)
    El modelo se cachea tras la primera carga (~2-5s) para evitar
    recargarlo en cada llamada.

    Requiere: pip install sentence-transformers
    """
    global _st_model_cache

    if model not in _st_model_cache:
        logger.info(
            "[unified_embedder] cargando SentenceTransformer '%s' (primera vez)...", model,
        )
        try:
            from sentence_transformers import SentenceTransformer
            _st_model_cache[model] = SentenceTransformer(model)
            logger.info("[unified_embedder] SentenceTransformer '%s' cargado OK", model)
        except ImportError:
            raise ImportError(
                "sentence-transformers no instalado. "
                "Instala con: pip install sentence-transformers, "
                "o cambia BRAIN_EMBEDDING_PROVIDER=ollama en el .env."
            )

    st_model = _st_model_cache[model]
    embedding = st_model.encode(text)
    return embedding.tolist()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_model_dimension() -> int:
    """
    Devuelve la dimensión del modelo de embedding activo.

    Usado como fallback cuando un embed falla (vector de ceros del tamaño correcto).

    Dimensiones conocidas por provider:
      ollama / nomic-embed-text  → 768
      ollama / nomic-embed-code  → 2560
      sentence-transformers / all-MiniLM-L6-v2 → 384
      sentence-transformers / all-mpnet-base-v2 → 768

    Si no se puede determinar, devuelve 768 (nomic-embed-text por defecto).
    """
    try:
        from app.brain.qdrant_manager import _COLLECTION_DIMS
        from app.brain.collections import KNOWLEDGE
        return _COLLECTION_DIMS.get(KNOWLEDGE, 768)
    except ImportError:
        return 768


def get_embedding_info() -> dict:
    """
    Devuelve información del modelo y provider de embedding activo.

    Usado por:
      - GET /api/v1/brain/stats (F9)
      - Admin Brain UI (F6.10)
      - Tests de verificación

    Returns:
        Dict con provider, model, host (si aplica), timeout, dimension.
    """
    info = {
        "provider":  BRAIN_EMBEDDING_PROVIDER,
        "model":     BRAIN_EMBEDDING_MODEL,
        "dimension": _get_model_dimension(),
    }
    if BRAIN_EMBEDDING_PROVIDER == "ollama":
        info["host"]    = OLLAMA_HOST
        info["timeout"] = OLLAMA_EMBED_TIMEOUT
    return info
