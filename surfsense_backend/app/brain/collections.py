"""
collections.py
--------------
Fuente única de verdad para nombres y configuración de las tres colecciones Qdrant.
Todas las referencias a nombres de colección deben importarse desde aquí.

Arquitectura de colecciones (F2):
  brain     — Nivel 1: pasaportes semánticos .md (un chunk por sección ##)
              Embedding: nomic-embed-text (768d)
  knowledge — Nivel 2 semántico: docs, PDFs, webs, contratos (chunk por párrafo)
              Embedding: nomic-embed-text (768d)
  code      — Nivel 2 técnico: código fuente, APIs, SQL, scripts
              Embedding: EMBED_MODEL_CODE (dim dinámica según modelo)

Decisión de diseño — NO refactorizar a Enum/dataclass:
  Este fichero usa string constants (BRAIN, KNOWLEDGE, CODE) que son importados
  en ingest_router.py, brain_ingest.py y otros módulos. Cambiar la estructura
  rompe todos esos imports sin valor añadido.

Auto-recreación de CODE:
  Si la colección 'code' existe con 768d y se cambia EMBED_MODEL_CODE a
  qwen3-embedding:4b (2560d), _ingest_code en IngestRouter detecta el mismatch,
  elimina la colección y la recrea automáticamente. Solo requiere que .env
  tenga el modelo correcto antes de la primera ingesta.

Variables de entorno relevantes:
  COLLECTION_BRAIN     : nombre de la colección brain (default: 'brain')
  COLLECTION_KNOWLEDGE : nombre de la colección knowledge (default: 'knowledge')
  COLLECTION_CODE      : nombre de la colección code (default: 'code')
  EMBED_MODEL_CODE     : modelo de embedding para código (default: 'nomic-embed-code')
                         Usar 'qwen3-embedding:4b' para embeddings de código de alta calidad.
"""
import os

# Nombres de colección — sobreescribibles con variables de entorno
BRAIN     = os.getenv("COLLECTION_BRAIN",     "brain")
KNOWLEDGE = os.getenv("COLLECTION_KNOWLEDGE", "knowledge")
CODE      = os.getenv("COLLECTION_CODE",      "code")

ALL_COLLECTIONS = [BRAIN, KNOWLEDGE, CODE]

# Dimensiones de vector por modelo de embedding
EMBED_DIMS = {
    "nomic-embed-text": 768,
    "nomic-embed-code": 768,   # misma arquitectura, verificar con: ollama pull nomic-embed-code
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "qwen3-embedding:4b": 2560,  # qwen3-embedding 4B — ollama pull qwen3-embedding:4b
}

# Configuración por colección
COLLECTION_CONFIG = {
    BRAIN: {
        "embed_model": "nomic-embed-text",
        "description": "Nivel 1 — Pasaportes semánticos .md (un chunk por sección ##)",
    },
    KNOWLEDGE: {
        "embed_model": "nomic-embed-text",
        "description": "Nivel 2 semántico — Docs, PDFs, webs, contratos (chunk por párrafo)",
    },
    CODE: {
        "embed_model": os.getenv("EMBED_MODEL_CODE", "nomic-embed-code"),
        "description": "Nivel 2 técnico — Código fuente, APIs, SQL, scripts",
    },
}
