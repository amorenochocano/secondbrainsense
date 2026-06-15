"""
collections.py
--------------
Fuente única de verdad para nombres y configuración de las tres colecciones Qdrant.
Todas las referencias a nombres de colección deben importarse desde aquí.
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
