# F5 — Conectores SurfSense → Pipeline Brain
**Duración:** 1 semana  
**Equipo:** Backend Senior (1)  
**Dependencias:** F3 completada  
**Entregable:** Cada documento indexado por cualquier conector SurfSense pasa también por el pipeline Brain (síntesis → pasaporte → Qdrant). Un hook en `IndexingPipelineService.index()`. Sin romper nada del pipeline existente.

---

## Objetivo

Conectar el pipeline de ingestión de SurfSense (`IndexingPipelineService`) con el pipeline Brain de Second Brain (F1-F3). Actualmente son dos pipelines paralelos y desconectados:

```
SurfSense (hoy):
  ConnectorDocument → IndexingPipelineService.index()
      → chunk_text_hybrid()           ← chunking SurfSense
      → embed_texts()                 ← pgvector embeddings
      → PostgreSQL (Document + Chunk) ← almacenamiento SurfSense
      ← FIN —  Brain no se entera

Brain (F1-F3):
  DocumentSynthesizer.synthesize()    ← pasaporte .md
  IngestRouter.route()                ← Qdrant (brain/knowledge/code)
  ← NO recibe documentos de conectores
```

**Después de F5:**
```
ConnectorDocument → IndexingPipelineService.index()
    → chunk_text_hybrid()         ← pipeline SurfSense — sin cambios
    → embed_texts() → PostgreSQL  ← pipeline SurfSense — sin cambios
    → [NUEVO HOOK F5]
        → DocumentSynthesizer.synthesize()   ← síntesis + pasaporte
        → BrainWriter.write()                ← pasaporte .md en /data/brain/
        → IngestRouter.route()               ← vectores en Qdrant
```

Un único punto de extensión. Todos los conectores lo usan automáticamente sin cambios individuales.

---

## F5.0 — Estado real del codebase (Día 0)

### Lo que ya existe y NO hay que tocar

| Componente | Fichero | Estado |
|-----------|---------|--------|
| `ConnectorDocument` | `app/indexing_pipeline/connector_document.py` | ✅ Existe — Pydantic model con `unique_id`, `source_markdown`, `document_type`, `search_space_id` (int), `should_use_code_chunker` |
| `IndexingPipelineService` | `app/indexing_pipeline/indexing_pipeline_service.py` | ✅ Existe — orquesta chunking, embedding, persistencia |
| `IndexingPipelineService.index()` | misma clase | ✅ Existe — método async que procesa un documento completo |
| Deduplicación | `app/indexing_pipeline/document_hashing.py` | ✅ Existe — `compute_content_hash()`, `compute_unique_identifier_hash()` |
| 20 indexers de conectores | `app/tasks/connector_indexers/*.py` | ✅ Existen todos — github, jira, confluence, slack, notion, etc. |
| `chunk_text_hybrid()` | `app/indexing_pipeline/document_chunker.py` | ✅ Existe — table-aware chunker |

### Lo que F5 debe crear

| Componente | Fichero | Descripción |
|-----------|---------|-------------|
| Brain hook | `app/indexing_pipeline/brain_hook.py` | Lógica de conexión SurfSense → Brain |
| Configuración hook | `.env.dev` | `BRAIN_HOOK_ENABLED=true` |

### Interfaz real de `ConnectorDocument` (NOT lo que proponía F5)

```python
# app/indexing_pipeline/connector_document.py  ← ya existe
class ConnectorDocument(BaseModel):
    title: str
    source_markdown: str           # ← texto completo del documento
    unique_id: str                 # ← ID único en el sistema origen
    document_type: DocumentType    # ← enum de app.db (no ConnectorType)
    search_space_id: int           # ← int FK, NO string UUID
    should_use_code_chunker: bool = False   # ← True para GitHub código
    metadata: dict = {}
    connector_id: int | None = None
    created_by_id: str
    folder_id: int | None = None
```

### Interfaz real de `IndexingPipelineService.index()` (punto de extensión)

```python
# app/indexing_pipeline/indexing_pipeline_service.py  ← ya existe
class IndexingPipelineService:
    async def index(
        self,
        document: Document,          # ORM row ya guardada en PostgreSQL
        connector_doc: ConnectorDocument,
    ) -> Document:
        # Orden actual:
        # 1. chunk_text_hybrid(connector_doc.source_markdown)
        # 2. embed_texts([content, *chunk_texts])
        # 3. Asigna Document.embedding, Document.content, chunks
        # 4. commit()
        # 5. document.status = ready
        # ← AQUÍ va el hook Brain (F5)
```

---

## F5.1 — `brain_hook.py`: el punto de conexión (Día 1-2)

**Fichero:** `surfsense_backend/app/indexing_pipeline/brain_hook.py` ← **CREAR**

Este módulo contiene una única función async que recibe el documento ya indexado por SurfSense y lo pasa por el pipeline Brain.

```python
# app/indexing_pipeline/brain_hook.py
"""
Hook de integración SurfSense → Brain Pipeline.
Se llama desde IndexingPipelineService.index() después de que el documento
está listo en PostgreSQL (status=ready, chunks generados).

NO modifica el pipeline SurfSense. Solo añade síntesis + Qdrant.
"""
import logging
import os

from app.db import Document, DocumentType
from app.indexing_pipeline.connector_document import ConnectorDocument

logger = logging.getLogger(__name__)

BRAIN_HOOK_ENABLED = os.getenv("BRAIN_HOOK_ENABLED", "true").lower() == "true"

# Tipos de documento que NO se sintetizan en Brain
# (datos muy efímeros o sin valor de pasaporte)
_SKIP_SYNTHESIS_TYPES = {
    DocumentType.SLACK_MESSAGE,
    DocumentType.GMAIL_EMAIL,
    DocumentType.GOOGLE_CALENDAR_EVENT,
    DocumentType.DISCORD_MESSAGE,
}


async def run_brain_hook(
    document: Document,
    connector_doc: ConnectorDocument,
) -> None:
    """
    Pasa el documento por el pipeline Brain tras indexarlo en SurfSense.

    Flujo:
      1. DocumentSynthesizer.synthesize() → pasaporte .md
      2. BrainWriter.write()              → /data/brain/{slug}.md
      3. IngestRouter.route()             → vectores en Qdrant

    Errores son capturados y logueados — nunca propagan para no romper
    el pipeline SurfSense (el documento ya está en PostgreSQL y listo).
    """
    if not BRAIN_HOOK_ENABLED:
        return

    if document.document_type in _SKIP_SYNTHESIS_TYPES:
        logger.debug(
            "[brain_hook] skip tipo=%s doc=%d", document.document_type, document.id
        )
        return

    source_slug = _build_source_slug(document, connector_doc)
    full_text = connector_doc.source_markdown

    if not full_text or not full_text.strip():
        logger.warning("[brain_hook] doc=%d sin contenido — skip", document.id)
        return

    try:
        await _synthesize_and_store(
            source_slug=source_slug,
            document=document,
            connector_doc=connector_doc,
            full_text=full_text,
        )
    except Exception as exc:
        # Nunca propagar — el documento SurfSense ya está listo
        logger.error(
            "[brain_hook] ERROR doc=%d source=%s: %s",
            document.id, source_slug, exc, exc_info=True,
        )


async def _synthesize_and_store(
    source_slug: str,
    document: Document,
    connector_doc: ConnectorDocument,
    full_text: str,
) -> None:
    import asyncio
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.writer import BrainWriter
    from app.brain.ingest_router import IngestRouter
    from app.brain.qdrant_manager import QdrantManager

    # ── Fase 1: Síntesis → pasaporte ─────────────────────────────────────
    synthesizer = DocumentSynthesizer()
    file_type = _document_type_to_extension(document.document_type, connector_doc)

    synthesis_result = await asyncio.to_thread(
        synthesizer.synthesize,
        source=source_slug,
        file_type=file_type,
        full_text=full_text,
        blocks=[],
        metadata={
            "title":          document.title,
            "connector_id":   connector_doc.connector_id,
            "unique_id":      connector_doc.unique_id,
            "search_space_id": str(connector_doc.search_space_id),
            **(connector_doc.metadata or {}),
        },
        ingest_metadata={
            "search_space_id": str(connector_doc.search_space_id),
        },
    )
    # synthesis_result = {"md_content": str, "tags": list, "entities": list,
    #                     "drill_down_triggers": list, "llm_ok": bool}

    md_content = synthesis_result.get("md_content", "")
    if not md_content:
        logger.warning("[brain_hook] síntesis vacía para doc=%d — usando fallback", document.id)
        md_content = f"# {document.title}\n\n{full_text[:2000]}"

    # ── Fase 2: Guardar pasaporte .md ─────────────────────────────────────
    writer = BrainWriter()
    writer.write(source=source_slug, md_content=md_content, overwrite=True)
    logger.info("[brain_hook] pasaporte guardado: %s", source_slug)

    # ── Fase 3: Vectorizar en Qdrant ──────────────────────────────────────
    ingest_router = IngestRouter(qdrant_client=QdrantManager.get_instance().client)
    blocks = _extract_blocks_from_text(full_text)

    await asyncio.to_thread(
        ingest_router.route,
        md_content=md_content,
        blocks=blocks,
        source=source_slug,
        ingest_metadata={
            "search_space_id": str(connector_doc.search_space_id),
            "title":           document.title,
            "doc_id":          str(document.id),
        },
    )
    logger.info("[brain_hook] Qdrant actualizado para doc=%d source=%s", document.id, source_slug)


def _build_source_slug(document: Document, connector_doc: ConnectorDocument) -> str:
    """
    Genera el slug canónico para el pasaporte Brain.
    Formato: {space_id}_{doc_type}_{unique_id_normalizado}
    Idempotente — mismo documento siempre produce el mismo slug.
    """
    import re
    raw = f"{connector_doc.search_space_id}_{document.document_type.value}_{connector_doc.unique_id}"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _document_type_to_extension(
    doc_type: DocumentType,
    connector_doc: ConnectorDocument,
) -> str:
    """
    Mapea DocumentType al tipo de fichero para DocumentSynthesizer.
    El sintetizador usa la extensión para elegir el prompt correcto.
    """
    if connector_doc.should_use_code_chunker:
        return ".py"   # Sintetizador trata como código — ajustar si hay lenguaje en metadata

    _MAP = {
        DocumentType.GITHUB_REPOSITORY:     ".md",
        DocumentType.CONFLUENCE_PAGE:        ".md",
        DocumentType.NOTION_PAGE:            ".md",
        DocumentType.JIRA_ISSUE:             ".md",
        DocumentType.LINEAR_ISSUE:           ".md",
        DocumentType.GOOGLE_DRIVE_DOCUMENT:  ".md",
        DocumentType.ONEDRIVE_DOCUMENT:      ".md",
        DocumentType.BOOKSTACK_PAGE:         ".md",
        DocumentType.SLACK_MESSAGE:          ".txt",
        DocumentType.DISCORD_MESSAGE:        ".txt",
        DocumentType.GMAIL_EMAIL:            ".txt",
        DocumentType.GOOGLE_CALENDAR_EVENT:  ".txt",
        DocumentType.YOUTUBE_TRANSCRIPT:     ".txt",
        DocumentType.WEBCRAWLER_PAGE:        ".html",
        DocumentType.LOCAL_FILE:             ".txt",
    }
    return _MAP.get(doc_type, ".txt")


def _extract_blocks_from_text(text: str) -> list[dict]:
    """
    Extrae bloques básicos del texto para IngestRouter.
    Para conectores: cada sección de nivel 2 (##) es un bloque.
    """
    import re
    blocks = []
    sections = re.split(r'\n(?=## )', text)
    for s in sections:
        s = s.strip()
        if s:
            lines = s.split('\n')
            title = lines[0].lstrip('#').strip() if lines else ""
            content = '\n'.join(lines[1:]).strip() if len(lines) > 1 else s
            blocks.append({"type": "section", "title": title, "content": content or s})
    return blocks or [{"type": "document", "title": "", "content": text}]
```

---

## F5.2 — Añadir el hook en `IndexingPipelineService.index()` (Día 2)

**Fichero:** `surfsense_backend/app/indexing_pipeline/indexing_pipeline_service.py` ← **MODIFICAR**

El hook se añade justo después de que el documento queda en estado `ready` y el commit se ha hecho. El hook no puede fallar silenciosamente el documento SurfSense — se ejecuta en `try/except` propio:

```python
# En IndexingPipelineService.index(), DESPUÉS de:
#   document.status = DocumentStatus.ready()
#   await self.session.commit()
#   log_index_success(ctx, chunk_count=len(chunks))

# AÑADIR (las líneas del pipeline SurfSense no cambian):
from app.indexing_pipeline.brain_hook import run_brain_hook, BRAIN_HOOK_ENABLED
if BRAIN_HOOK_ENABLED:
    await run_brain_hook(document=document, connector_doc=connector_doc)
```

> **Posición exacta**: tras `log_index_success(ctx, chunk_count=len(chunks))` y antes de `outcome_status = "success"`. Así el hook no afecta al `outcome_status` ni a las métricas de SurfSense.

**Regla crítica**: si `run_brain_hook()` lanza excepción, el documento SurfSense ya está `ready` en PostgreSQL — la excepción no debe propagarse al bloque de error de `index()`. `run_brain_hook()` captura internamente todas las excepciones.

---

## F5.3 — Variables de entorno (Día 2)

```bash
# .env.dev

# ── Hook Brain en el pipeline de indexación ──────────────────────────────────
BRAIN_HOOK_ENABLED=true          # Activar integración SurfSense → Brain

# Tipos de documento que se saltarán la síntesis automáticamente:
# SLACK_MESSAGE, GMAIL_EMAIL, GOOGLE_CALENDAR_EVENT, DISCORD_MESSAGE
# (configurados en _SKIP_SYNTHESIS_TYPES de brain_hook.py)

# ── Síntesis (ya definidas en F3) ─────────────────────────────────────────────
SYNTHESIS_MODEL=qwen2.5-coder:7b   # Modelo para síntesis de pasaportes
LLM_PROVIDER=ollama
BRAIN_DIR=/data/brain

# ── Qdrant (ya definidas en F2) ───────────────────────────────────────────────
QDRANT_HOST=qdrant
QDRANT_PORT=6333
```

---

## F5.4 — Tests (Día 3-4)

**Fichero:** `tests/brain/test_brain_hook_f5.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.indexing_pipeline.brain_hook import run_brain_hook, _build_source_slug, _document_type_to_extension
from app.db import DocumentType


def _make_document(doc_type=DocumentType.CONFLUENCE_PAGE, doc_id=42):
    doc = MagicMock()
    doc.id = doc_id
    doc.title = "Guía de arquitectura"
    doc.document_type = doc_type
    return doc


def _make_connector_doc(search_space_id=7, should_use_code=False):
    from app.indexing_pipeline.connector_document import ConnectorDocument
    return ConnectorDocument(
        title="Guía de arquitectura",
        source_markdown="## Sección 1\nContenido relevante.",
        unique_id="conf-page-123",
        document_type=DocumentType.CONFLUENCE_PAGE,
        search_space_id=search_space_id,
        should_use_code_chunker=should_use_code,
        created_by_id="user-abc",
    )


class TestBrainHookSlug:

    def test_slug_es_determinista(self):
        doc = _make_document()
        conn = _make_connector_doc()
        slug1 = _build_source_slug(doc, conn)
        slug2 = _build_source_slug(doc, conn)
        assert slug1 == slug2

    def test_slug_sin_caracteres_invalidos(self):
        doc = _make_document()
        conn = _make_connector_doc()
        slug = _build_source_slug(doc, conn)
        import re
        assert re.fullmatch(r'[a-z0-9\-]+', slug)

    def test_slug_distinto_por_search_space(self):
        doc = _make_document()
        conn_a = _make_connector_doc(search_space_id=1)
        conn_b = _make_connector_doc(search_space_id=2)
        assert _build_source_slug(doc, conn_a) != _build_source_slug(doc, conn_b)


class TestBrainHookExtension:

    def test_code_chunker_devuelve_py(self):
        doc = _make_document(DocumentType.GITHUB_REPOSITORY)
        conn = _make_connector_doc(should_use_code=True)
        ext = _document_type_to_extension(doc.document_type, conn)
        assert ext == ".py"

    def test_confluence_devuelve_md(self):
        doc = _make_document(DocumentType.CONFLUENCE_PAGE)
        conn = _make_connector_doc()
        ext = _document_type_to_extension(doc.document_type, conn)
        assert ext == ".md"

    def test_slack_devuelve_txt(self):
        doc = _make_document(DocumentType.SLACK_MESSAGE)
        conn = _make_connector_doc()
        ext = _document_type_to_extension(doc.document_type, conn)
        assert ext == ".txt"


class TestBrainHookSkip:

    @pytest.mark.asyncio
    async def test_skip_slack_message(self):
        """Slack messages no deben pasar por síntesis."""
        doc = _make_document(DocumentType.SLACK_MESSAGE)
        conn = _make_connector_doc()

        with patch("app.indexing_pipeline.brain_hook._synthesize_and_store") as mock_synth:
            with patch("app.indexing_pipeline.brain_hook.BRAIN_HOOK_ENABLED", True):
                await run_brain_hook(doc, conn)
            mock_synth.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_cuando_hook_desactivado(self):
        doc = _make_document()
        conn = _make_connector_doc()

        with patch("app.indexing_pipeline.brain_hook._synthesize_and_store") as mock_synth:
            with patch("app.indexing_pipeline.brain_hook.BRAIN_HOOK_ENABLED", False):
                await run_brain_hook(doc, conn)
            mock_synth.assert_not_called()


class TestBrainHookIntegration:

    @pytest.mark.asyncio
    async def test_hook_llama_synthesize_writer_ingest(self):
        """El hook debe llamar a los tres componentes en orden."""
        doc = _make_document(DocumentType.CONFLUENCE_PAGE)
        conn = _make_connector_doc()

        mock_synth_result = {
            "md_content": "# Pasaporte\n\n## Resumen\nContenido.", 
            "tags": [], "entities": [], "drill_down_triggers": [], "llm_ok": True
        }

        with (
            patch("app.indexing_pipeline.brain_hook.BRAIN_HOOK_ENABLED", True),
            patch("app.brain.synthesizer.DocumentSynthesizer.synthesize",
                  return_value=mock_synth_result) as mock_synth,
            patch("app.brain.writer.BrainWriter.write") as mock_write,
            patch("app.brain.ingest_router.IngestRouter.route") as mock_route,
            patch("app.brain.qdrant_manager.QdrantManager.get_instance"),
        ):
            await run_brain_hook(doc, conn)

        mock_synth.assert_called_once()
        mock_write.assert_called_once()
        mock_route.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_en_hook_no_propaga(self):
        """Si el hook falla, no debe lanzar excepción (pipeline SurfSense no se ve afectado)."""
        doc = _make_document()
        conn = _make_connector_doc()

        with patch("app.indexing_pipeline.brain_hook.BRAIN_HOOK_ENABLED", True):
            with patch("app.indexing_pipeline.brain_hook._synthesize_and_store",
                       side_effect=RuntimeError("Qdrant no disponible")):
                # No debe lanzar excepción
                await run_brain_hook(doc, conn)
```

---

## F5.5 — Verificación de la cadena completa (Día 4-5)

Con F5 completo, el flujo end-to-end de ingestión queda así:

```
Usuario conecta Confluence
    ↓
CeleryTask: index_confluence_pages
    ↓
confluence_indexer.py → ConnectorDocument(
    title="Página X",
    source_markdown="...",
    document_type=DocumentType.CONFLUENCE_PAGE,
    search_space_id=7,
    should_use_code_chunker=False,
)
    ↓
IndexingPipelineService.prepare_for_indexing()
    → Document(status=pending) guardado en PostgreSQL
    → UI muestra el documento inmediatamente
    ↓
IndexingPipelineService.index(document, connector_doc)
    → chunk_text_hybrid(source_markdown)    ← SurfSense — sin cambios
    → embed_texts() → pgvector              ← SurfSense — sin cambios
    → Document.status = ready               ← SurfSense — sin cambios
    → commit()
    ↓
    [HOOK F5] run_brain_hook(document, connector_doc)
        → DocumentSynthesizer.synthesize()
            → pasaporte .md con 7 secciones (F3)
        → BrainWriter.write("7-confluence-page-123.md", ...)
            → /data/brain/7-confluence-page-123.md
        → IngestRouter.route(md_content, blocks, source)
            → Qdrant colección brain   (pasaporte vectorizado)
            → Qdrant colección knowledge (chunks semánticos)
    ↓
Router F4 ya puede responder consultas sobre esta página
```

---

## Checklist F5

### F5.0 — Prerrequisitos
- [ ] `IndexingPipelineService` operativo — documentos llegan a `ready` correctamente
- [ ] `DocumentSynthesizer` operativo (F3 completada)
- [ ] `BrainWriter` operativo (F3 completada)
- [ ] `IngestRouter` operativo (F2 completada)
- [ ] `QdrantManager.get_instance()` disponible (F2 completada)

### F5.1 — `brain_hook.py`
- [ ] `run_brain_hook(document, connector_doc)` creado como función async
- [ ] `BRAIN_HOOK_ENABLED` controlado por variable de entorno
- [ ] `_SKIP_SYNTHESIS_TYPES` excluye Slack, Gmail, Calendar, Discord
- [ ] Errores capturados internamente — nunca propagan al pipeline SurfSense
- [ ] `_build_source_slug()` determinista: mismo doc → mismo slug siempre
- [ ] `_document_type_to_extension()` cubre todos los `DocumentType` del codebase
- [ ] `should_use_code_chunker=True` → extensión `.py` para sintetizador

### F5.2 — Integración en `IndexingPipelineService.index()`
- [ ] Hook añadido DESPUÉS de `log_index_success()` y ANTES de `outcome_status = "success"`
- [ ] Hook dentro de bloque `try/except` propio — no afecta el `try/except` de SurfSense
- [ ] `BRAIN_HOOK_ENABLED` comprobado antes de llamar al hook (importación condicional)
- [ ] El estado `Document.status = ready` no cambia si el hook falla

### F5.3 — Variables de entorno
- [ ] `BRAIN_HOOK_ENABLED=true` en `.env.dev`
- [ ] `BRAIN_HOOK_ENABLED=false` disponible para desactivar sin tocar código

### F5.4 — Tests
- [ ] `TestBrainHookSlug` — slug determinista, sin caracteres inválidos, distinto por space
- [ ] `TestBrainHookExtension` — extensiones correctas por DocumentType
- [ ] `TestBrainHookSkip` — Slack y hook desactivado no llaman a síntesis
- [ ] `TestBrainHookIntegration` — los tres componentes se llaman en orden
- [ ] `test_error_en_hook_no_propaga` — excepción en hook no rompe el pipeline

### Criterio de aceptación global F5
- [ ] Indexar un documento Confluence → aparece en PostgreSQL (SurfSense) **Y** en `/data/brain/` (Brain)
- [ ] El mismo documento aparece en Qdrant colección `brain` con `search_space_id` correcto
- [ ] Re-indexar el mismo documento: SurfSense lo deduplica, hook regenera pasaporte (idempotente)
- [ ] Con `BRAIN_HOOK_ENABLED=false`: el pipeline SurfSense funciona exactamente igual que antes de F5
- [ ] Indexar 10 documentos de un mismo space: el Router F4 puede responder consultas sobre todos ellos

---

**Anterior:** [F4 — Router Multinivel](./F4-router-multinivel.md)  
**Siguiente:** [F6 — UI Integrada](./F6-ui-integracion.md)