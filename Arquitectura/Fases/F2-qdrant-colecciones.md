# F2 — Qdrant y Tres Colecciones
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + Backend Mid (1)  
**Dependencias:** F1 completada  
**Entregable:** Qdrant con tres colecciones activas (`brain`, `knowledge`, `code`). PostgreSQL mantiene lo relacional. Embeddings diferenciados por dominio.

---

## Objetivo

Sustituir pgvector como almacén vectorial por Qdrant con tres colecciones diferenciadas. PostgreSQL sigue siendo la fuente de verdad para todo lo relacional. Solo los vectores migran.

**Antes (SurfSense):**
```
document_segment (PostgreSQL + pgvector)
  → 1 colección implícita, dimensión fija, sin diferenciación por tipo
```

**Después (BrainSense F2):**
```
Qdrant:brain      → embeddings de pasaportes .md (768d, nomic-embed-text)
Qdrant:knowledge  → embeddings de chunks de texto (768d, nomic-embed-text)
Qdrant:code       → embeddings de bloques de código (2560d, qwen3-embedding:4b)
PostgreSQL        → usuarios, documentos, conectores, chats (sin cambios)
```

---

## F2.1 — Servicio de colecciones Qdrant (Día 1)

**Fichero:** `surfsense_backend/app/brain/collections.py` ← ya existe en Second Brain, adaptar

```python
# collections.py — Fuente única de verdad para colecciones y modelos
from dataclasses import dataclass
from enum import Enum
import os

class CollectionName(str, Enum):
    BRAIN = "brain"
    KNOWLEDGE = "knowledge"
    CODE = "code"

@dataclass
class CollectionConfig:
    name: str
    vector_size: int
    embed_model: str
    description: str

COLLECTIONS = {
    CollectionName.BRAIN: CollectionConfig(
        name="brain",
        vector_size=768,
        embed_model=os.getenv("EMBED_MODEL", "nomic-embed-text"),
        description="Pasaportes semánticos — secciones ## del .md"
    ),
    CollectionName.KNOWLEDGE: CollectionConfig(
        name="knowledge",
        vector_size=768,
        embed_model=os.getenv("EMBED_MODEL", "nomic-embed-text"),
        description="Chunks de texto semántico de documentos"
    ),
    CollectionName.CODE: CollectionConfig(
        name="code",
        vector_size=2560,
        embed_model=os.getenv("EMBED_MODEL_CODE", "qwen3-embedding:4b"),
        description="Bloques de código fuente"
    ),
}
```

---

## F2.2 — QdrantManager: cliente centralizado (Día 1-2)

**Fichero:** `surfsense_backend/app/brain/qdrant_manager.py` ← NUEVO

```python
# qdrant_manager.py
from qdrant_client import QdrantClient, models
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from app.brain.collections import COLLECTIONS, CollectionName, CollectionConfig
import logging
import os

logger = logging.getLogger(__name__)

class QdrantManager:
    """Gestión centralizada de colecciones Qdrant."""

    _instance = None

    def __init__(self):
        self.client = QdrantClient(
            host=os.getenv("QDRANT_HOST", "qdrant"),
            port=int(os.getenv("QDRANT_PORT", 6333))
        )

    @classmethod
    def get_instance(cls) -> "QdrantManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def ensure_collections(self):
        """Crear colecciones si no existen. Idempotente."""
        existing = {c.name for c in self.client.get_collections().collections}

        for col_name, config in COLLECTIONS.items():
            if config.name not in existing:
                self.client.create_collection(
                    collection_name=config.name,
                    vectors_config=VectorParams(
                        size=config.vector_size,
                        distance=Distance.COSINE
                    )
                )
                # Índices de payload para filtrado eficiente
                self.client.create_payload_index(
                    collection_name=config.name,
                    field_name="source",
                    field_schema="keyword"
                )
                self.client.create_payload_index(
                    collection_name=config.name,
                    field_name="search_space_id",
                    field_schema="keyword"
                )
                logger.info(f"Colección Qdrant creada: {config.name} ({config.vector_size}d)")
            else:
                # Verificar que la dimensión coincide
                col_info = self.client.get_collection(config.name)
                existing_size = col_info.config.params.vectors.size
                if existing_size != config.vector_size:
                    raise ValueError(
                        f"Colección {config.name} tiene {existing_size}d "
                        f"pero se esperan {config.vector_size}d. "
                        f"Ejecutar POST /admin/collection/recreate"
                    )

    def recreate_collection(self, collection_name: str):
        """Recrear colección (borra todos los vectores). Solo admin."""
        config = next(
            (c for c in COLLECTIONS.values() if c.name == collection_name), None
        )
        if not config:
            raise ValueError(f"Colección desconocida: {collection_name}")

        self.client.delete_collection(collection_name)
        self.ensure_collections()
        logger.warning(f"Colección {collection_name} recreada — todos los vectores borrados")

    def upsert_points(
        self,
        collection_name: str,
        points: list[PointStruct]
    ) -> int:
        """Insertar/actualizar puntos. Devuelve número de puntos procesados."""
        if not points:
            return 0

        self.client.upsert(
            collection_name=collection_name,
            points=points,
            wait=True
        )
        return len(points)

    def delete_by_source(self, source: str):
        """Borrar todos los vectores de un documento (todas las colecciones)."""
        for config in COLLECTIONS.values():
            self.client.delete(
                collection_name=config.name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[models.FieldCondition(
                            key="source",
                            match=models.MatchValue(value=source)
                        )]
                    )
                )
            )
        logger.info(f"Vectores borrados para source: {source}")

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        search_space_id: str,
        top_k: int = 10,
        filter_by_sources: list[str] = None,
    ) -> list[dict]:
        """Búsqueda vectorial con filtro por search_space."""
        must_conditions = [
            models.FieldCondition(
                key="search_space_id",
                match=models.MatchValue(value=search_space_id)
            )
        ]

        if filter_by_sources:
            must_conditions.append(
                models.FieldCondition(
                    key="source",
                    match=models.MatchAny(any=filter_by_sources)
                )
            )

        results = self.client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=models.Filter(must=must_conditions),
            limit=top_k,
            with_payload=True,
        )

        return [
            {
                "score": r.score,
                "text": r.payload.get("text", ""),
                "source": r.payload.get("source", ""),
                "chunk_index": r.payload.get("chunk_index", 0),
                "metadata": r.payload,
            }
            for r in results
        ]
```

---

## F2.3 — IngestRouter: distribuir a las tres colecciones (Día 2-3)

**Fichero:** `surfsense_backend/app/brain/ingest_router.py` ← ya existe en Second Brain, adaptar

```python
# ingest_router.py
import uuid
from pathlib import Path
from qdrant_client.http.models import PointStruct
from app.brain.qdrant_manager import QdrantManager
from app.brain.collections import CollectionName
from app.brain.llm_client import BrainLLMClient
import logging

logger = logging.getLogger(__name__)

class IngestRouter:
    """
    Distribuye los vectores a las colecciones correctas.
    brain     ← secciones ## del pasaporte .md
    knowledge ← chunks de bloques de texto (content_type=text)
    code      ← chunks de bloques de código (content_type=code)
    """

    def __init__(self, llm_client: BrainLLMClient, qdrant: QdrantManager):
        self.llm = llm_client
        self.qdrant = qdrant

    async def route(
        self,
        source: str,              # slug canónico — clave de join
        search_space_id: str,
        passport_md: str,         # pasaporte completo en Markdown
        blocks: list[dict],       # bloques de Fase 1+2
        processed_text: str,      # texto con ##/### de Fase 3
        embedding_scope: list[str],  # ["brain", "knowledge", "code"]
    ):
        """Vectoriza y distribuye a las colecciones según embedding_scope."""

        if "brain" in embedding_scope:
            await self._ingest_brain(source, search_space_id, passport_md)

        if "knowledge" in embedding_scope:
            await self._ingest_knowledge(source, search_space_id, processed_text, blocks)

        if "code" in embedding_scope:
            code_blocks = [b for b in blocks if b.get("content_type") == "code"]
            if code_blocks:
                await self._ingest_code(source, search_space_id, code_blocks)

        logger.info(f"IngestRouter: {source} → {embedding_scope}")

    async def _ingest_brain(self, source: str, search_space_id: str, passport_md: str):
        """Vectoriza secciones ## del pasaporte en colección brain."""
        sections = self._split_by_heading(passport_md, level=2)
        if not sections:
            return

        points = []
        for i, section in enumerate(sections):
            vector = await self.llm.embed_text(section, model="nomic-embed-text")
            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "source": source,
                    "search_space_id": search_space_id,
                    "text": section,
                    "level": 1,
                    "chunk_index": i,
                    "collection": "brain",
                }
            ))

        self.qdrant.upsert_points("brain", points)
        logger.debug(f"brain: {len(points)} secciones vectorizadas para {source}")

    async def _ingest_knowledge(
        self, source: str, search_space_id: str,
        processed_text: str, blocks: list[dict]
    ):
        """Vectoriza chunks de texto en colección knowledge."""
        from app.brain.prompts.planner import split_doc_text_for_chunked
        chunks = split_doc_text_for_chunked(processed_text)

        text_blocks = [b for b in blocks if b.get("content_type") == "text"]
        points = []

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue

            # Quality score del bloque correspondiente (si existe)
            block_meta = text_blocks[i].get("metadata", {}) if i < len(text_blocks) else {}

            vector = await self.llm.embed_text(chunk, model="nomic-embed-text")
            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "source": source,
                    "search_space_id": search_space_id,
                    "text": chunk,
                    "level": 2,
                    "chunk_index": i,
                    "quality_score": block_meta.get("quality_score", 1.0),
                    "language": block_meta.get("language", "unknown"),
                    "collection": "knowledge",
                }
            ))

        self.qdrant.upsert_points("knowledge", points)
        logger.debug(f"knowledge: {len(points)} chunks vectorizados para {source}")

    async def _ingest_code(
        self, source: str, search_space_id: str, code_blocks: list[dict]
    ):
        """Vectoriza bloques de código en colección code (2560d)."""
        points = []
        for i, block in enumerate(code_blocks):
            content = block.get("content", "").strip()
            if not content:
                continue

            vector = await self.llm.embed_text(content, model="qwen3-embedding:4b")
            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "source": source,
                    "search_space_id": search_space_id,
                    "text": content,
                    "level": 2,
                    "chunk_index": i,
                    "language": block.get("metadata", {}).get("language", "unknown"),
                    "module": block.get("module", ""),
                    "function": block.get("function", ""),
                    "collection": "code",
                }
            ))

        self.qdrant.upsert_points("code", points)
        logger.debug(f"code: {len(points)} bloques vectorizados para {source}")

    @staticmethod
    def _split_by_heading(text: str, level: int = 2) -> list[str]:
        """Divide el Markdown por headings del nivel indicado."""
        prefix = "#" * level + " "
        sections, current = [], []
        for line in text.split("\n"):
            if line.startswith(prefix) and current:
                sections.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append("\n".join(current))
        return [s for s in sections if s.strip()]
```

---

## F2.4 — Adaptar el task Celery para escribir en Qdrant (Día 3-4)

```python
# En el task de Celery — sección de almacenamiento actualizada

from app.brain.qdrant_manager import QdrantManager
from app.brain.ingest_router import IngestRouter
from app.brain.llm_client import BrainLLMClient

# En process_file_upload_task, después de obtener result de process_document_content:

qdrant = QdrantManager.get_instance()
qdrant.ensure_collections()

llm_client = BrainLLMClient()
router = IngestRouter(llm_client, qdrant)

# El slug canónico como clave de join
source_slug = _slugify(filename)

await router.route(
    source=source_slug,
    search_space_id=search_space_id,
    passport_md=result["summary"],        # placeholder F3 — en F3 es el pasaporte real
    blocks=result["blocks"],
    processed_text=result["processed_text"],
    embedding_scope=["knowledge", "code"], # brain se añade en F3 con el pasaporte real
)

# PostgreSQL sigue guardando metadata del documento (sin vectores)
# document_segment ya NO se usa para vectores — Qdrant es el almacén
```

---

## F2.5 — Endpoint admin para gestión de colecciones (Día 4)

```python
# En surfsense_backend/app/routes/admin_routes.py — añadir:

@router.get("/admin/qdrant/collections")
async def list_qdrant_collections(current_user: User = Depends(get_current_admin_user)):
    """Lista las colecciones Qdrant con estadísticas."""
    qdrant = QdrantManager.get_instance()
    result = {}
    for col_name, config in COLLECTIONS.items():
        try:
            info = qdrant.client.get_collection(config.name)
            result[config.name] = {
                "vectors_count": info.vectors_count,
                "vector_size": config.vector_size,
                "embed_model": config.embed_model,
                "description": config.description,
            }
        except Exception:
            result[config.name] = {"status": "not_created"}
    return result

@router.post("/admin/qdrant/collection/{collection_name}/recreate")
async def recreate_collection(
    collection_name: str,
    current_user: User = Depends(get_current_admin_user)
):
    """Recrear una colección (borra todos los vectores). Solo admin."""
    qdrant = QdrantManager.get_instance()
    qdrant.recreate_collection(collection_name)
    return {"status": "recreated", "collection": collection_name}
```

---

## F2.6 — Tests F2

```python
# tests/brain/test_qdrant_f2.py

import pytest
from app.brain.qdrant_manager import QdrantManager
from app.brain.collections import CollectionName

class TestQdrantColecciones:

    def test_ensure_collections_creates_three(self):
        qdrant = QdrantManager.get_instance()
        qdrant.ensure_collections()
        existing = {c.name for c in qdrant.client.get_collections().collections}
        assert "brain" in existing
        assert "knowledge" in existing
        assert "code" in existing

    def test_brain_collection_768d(self):
        qdrant = QdrantManager.get_instance()
        info = qdrant.client.get_collection("brain")
        assert info.config.params.vectors.size == 768

    def test_code_collection_2560d(self):
        qdrant = QdrantManager.get_instance()
        info = qdrant.client.get_collection("code")
        assert info.config.params.vectors.size == 2560

    def test_upsert_and_delete_by_source(self):
        from qdrant_client.http.models import PointStruct
        qdrant = QdrantManager.get_instance()
        points = [PointStruct(
            id="test-point-1",
            vector=[0.1] * 768,
            payload={"source": "test-doc", "search_space_id": "test-space", "text": "hola"}
        )]
        qdrant.upsert_points("brain", points)
        qdrant.delete_by_source("test-doc")
        # Verificar que no quedan vectores
        results = qdrant.search("brain", [0.1]*768, "test-space", top_k=1)
        test_results = [r for r in results if r["source"] == "test-doc"]
        assert len(test_results) == 0
```

---

## Checklist F2

- [ ] Tres colecciones Qdrant creadas con dimensiones correctas (768/768/2560)
- [ ] `QdrantManager` singleton funcional con `ensure_collections()` idempotente
- [ ] `IngestRouter` distribuyendo a `brain`, `knowledge`, `code`
- [ ] Task Celery escribe en Qdrant (no en pgvector para vectores)
- [ ] `DELETE /brain/{source}` borra de las tres colecciones
- [ ] Endpoint admin de gestión de colecciones funcional
- [ ] Tests F2 pasando
- [ ] `document_segment` en PostgreSQL marcada como deprecated (no eliminada aún)

---

**Anterior:** [F1 — Pipeline de Tres Fases](./F1-pipeline-tres-fases.md)  
**Siguiente:** [F3 — Síntesis Multi-call y Pasaporte Semántico](./F3-sintesis-pasaporte.md)
