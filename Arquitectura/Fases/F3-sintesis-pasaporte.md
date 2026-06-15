# F3 — Síntesis Multi-call y Pasaporte Semántico
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + IA Engineer (1)  
**Dependencias:** F2 completada  
**Entregable:** Cada documento ingestado genera un pasaporte .md en 7 secciones, persiste en /data/brain/, y se vectoriza en la colección `brain`

---

## Objetivo

Sustituir el `SUMMARY_PROMPT_TEMPLATE` genérico de SurfSense por el sistema completo de síntesis híbrida de Second Brain: `passport_builder` (determinista) + `synthesizer` (LLM multi-call con Planner).

**Antes (SurfSense):**
```
processed_text → 1 llamada LLM → string summary plano
```

**Después (BrainSense F3):**
```
processed_text → passport_builder (determinista, sin LLM)
              → Planner (FOCUSED vs CHUNKED)
              → N llamadas LLM
              → pasaporte .md 7 secciones
              → BrainWriter → /data/brain/{source}.md
              → IngestRouter → brain collection (Qdrant)
```

---

## F3.1 — Integrar passport_builder (Día 1)

`passport_builder.py` ya existe en Second Brain. Es determinista — no hace llamadas LLM.
Extrae del `processed_text` y los metadatos del fichero:
- Frontmatter YAML (id, title, type, domain, created_at, tags, entities, related, drill_down_triggers)
- Secciones estructurales de Core Knowledge (los `###` del preprocesador)

**Punto de integración:** añadir llamada en `document_converters.py` después de la Fase 3.

```python
# document_converters.py — añadir tras preprocess()

from app.brain.passport_builder import PassportBuilder

# Después de: processed_text = processor.preprocess(blocks)

passport_partial = PassportBuilder.build(
    filename=filename,
    processed_text=processed_text,
    blocks=blocks,
    metadata=metadata,
    block_metadata=block_metadata,
)
# passport_partial tiene frontmatter completo pero secciones narrativas vacías
# Las secciones narrativas las rellena el synthesizer en F3.2
```

---

## F3.2 — Integrar Planner y Synthesizer (Día 2-3)

El `Planner` decide si el documento necesita plan FOCUSED (1 llamada) o CHUNKED (N+1 llamadas).

```python
# document_converters.py — sección síntesis

from app.brain.prompts.planner import Planner
from app.brain.synthesizer import DocumentSynthesizer
from app.brain.llm_client import BrainLLMClient

async def _run_synthesis(
    processed_text: str,
    passport_partial: dict,
    filename: str,
    llm_client: BrainLLMClient,
    synthesis_model: str = None,
) -> str:
    """
    Orquesta la síntesis multi-call.
    Devuelve el pasaporte .md completo como string.
    """
    ext = Path(filename).suffix.lower()

    # El Planner decide FOCUSED vs CHUNKED
    plan = Planner.plan(
        processed_text=processed_text,
        extension=ext,
        model_profile=llm_client.get_model_profile(synthesis_model),
    )

    synthesizer = DocumentSynthesizer(llm_client)

    if plan.strategy == "focused":
        # 1 llamada LLM con el processed_text completo
        passport_md = await synthesizer.synthesize_focused(
            processed_text=processed_text,
            passport_partial=passport_partial,
            extension=ext,
            model=synthesis_model,
        )
    else:
        # N+1 llamadas LLM: overview + chunks de Core Knowledge
        passport_md = await synthesizer.synthesize_chunked(
            processed_text=processed_text,
            passport_partial=passport_partial,
            extension=ext,
            chunks=plan.chunks,
            model=synthesis_model,
        )

    return passport_md
```

**Adaptar `synthesizer.py` de Second Brain:**

El synthesizer usa `llm_client.py` propio. En BrainSense hay que adaptarlo para usar el LLM client de SurfSense (que ya gestiona Ollama/Claude/OpenAI). El contrato es:

```python
# BrainLLMClient — adaptador entre synthesizer y SurfSense LLM
class BrainLLMClient:
    """Adaptador que envuelve el LLM client de SurfSense."""

    def __init__(self, surfsense_llm_config: dict):
        self._config = surfsense_llm_config

    async def generate(self, prompt: str, system: str, model: str = None,
                       max_tokens: int = 4096, num_ctx: int = None) -> str:
        """Llamada LLM unificada — delega al provider configurado en SurfSense."""
        # SurfSense ya tiene su abstracción de LLM providers
        # (Ollama, OpenAI, Anthropic) — la reutilizamos aquí
        from app.services.llm_service import generate_completion
        return await generate_completion(
            prompt=prompt,
            system=system,
            model=model or self._config.get("synthesis_model"),
            max_tokens=max_tokens,
        )

    async def embed_text(self, text: str, model: str = None) -> list[float]:
        """Embedding vía Ollama."""
        from app.services.embedding_service import get_embedding
        return await get_embedding(text, model=model)

    def get_model_profile(self, model_name: str = None):
        """ModelProfile con context_tokens calibrado por modelo."""
        from app.brain.prompts.type_specs import MODEL_PROFILES
        model = model_name or self._config.get("synthesis_model", "qwen2.5-coder:7b")
        return MODEL_PROFILES.get(model, MODEL_PROFILES["default"])
```

---

## F3.3 — BrainWriter: persistir el pasaporte .md (Día 3)

```python
# writer.py — ya existe en Second Brain, adaptar path

from pathlib import Path
import os

class BrainWriter:
    """Persiste el pasaporte .md en /data/brain/."""

    def __init__(self, brain_path: str = None):
        self.brain_path = Path(brain_path or os.getenv("BRAIN_DATA_PATH", "/data/brain"))
        self.brain_path.mkdir(parents=True, exist_ok=True)

    def write(self, source: str, passport_md: str) -> Path:
        """Escribe el pasaporte. Devuelve la ruta del fichero."""
        passport_path = self.brain_path / f"{source}.md"
        passport_path.write_text(passport_md, encoding="utf-8")
        return passport_path

    def read(self, source: str) -> str:
        """Lee un pasaporte existente."""
        passport_path = self.brain_path / f"{source}.md"
        if not passport_path.exists():
            raise FileNotFoundError(f"Pasaporte no encontrado: {source}")
        return passport_path.read_text(encoding="utf-8")

    def delete(self, source: str):
        """Elimina el pasaporte."""
        passport_path = self.brain_path / f"{source}.md"
        if passport_path.exists():
            passport_path.unlink()

    def list_all(self) -> list[str]:
        """Lista todos los slugs con pasaporte."""
        return [p.stem for p in self.brain_path.glob("*.md") if p.stem != "index"]
```

---

## F3.4 — Añadir passport_path al modelo Document de PostgreSQL (Día 3)

```python
# surfsense_backend/app/db.py — añadir campo al modelo Document

class Document(Base):
    __tablename__ = "document"
    # ... campos existentes de SurfSense ...

    # BRAINSENSE F3: ruta al pasaporte semántico
    passport_path = Column(String, nullable=True)
    passport_generated_at = Column(DateTime, nullable=True)
    avg_quality_score = Column(Float, nullable=True)
    has_pii = Column(Boolean, default=False)
    detected_languages = Column(ARRAY(String), nullable=True)
    embedding_scope = Column(ARRAY(String), default=["knowledge", "code"])

# Migración Alembic
# alembic revision --autogenerate -m "add passport fields to document"
# alembic upgrade head
```

---

## F3.5 — Pipeline completo en document_converters.py (Día 4)

El flujo completo de ingesta, con todas las piezas:

```python
async def process_document_content(
    file_content: bytes,
    filename: str,
    metadata: dict,
    llm_client: BrainLLMClient,
    search_space_id: str,
    synthesis_enabled: bool = True,
    synthesis_model: str = None,
) -> dict:

    ext = Path(filename).suffix.lower()
    source = _slugify(Path(filename).stem)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        # ── FASE 1 + 2: Extracción y limpieza ─────────
        processor = DocumentProcessorFactory.get(filename)
        blocks = processor.extract(tmp_path)
        block_metadata = processor.get_metadata_from_blocks(blocks)

        # ── FASE 3: Preprocesamiento semántico ─────────
        processed_text = processor.preprocess(blocks)

        # ── PASAPORTE DETERMINISTA (sin LLM) ──────────
        passport_partial = PassportBuilder.build(
            filename=filename,
            processed_text=processed_text,
            blocks=blocks,
            metadata={**metadata, **block_metadata},
        )

        # ── SÍNTESIS LLM (multi-call con Planner) ─────
        if synthesis_enabled:
            passport_md = await _run_synthesis(
                processed_text=processed_text,
                passport_partial=passport_partial,
                filename=filename,
                llm_client=llm_client,
                synthesis_model=synthesis_model,
            )
        else:
            # Raw ingestion: pasaporte solo con la parte determinista
            passport_md = passport_partial["raw_md"]

        # ── PERSISTIR PASAPORTE ────────────────────────
        writer = BrainWriter()
        passport_path = writer.write(source, passport_md)

        # ── VECTORIZAR EN QDRANT ───────────────────────
        embedding_scope = passport_partial.get("embedding_scope", ["brain", "knowledge", "code"])
        qdrant = QdrantManager.get_instance()
        router = IngestRouter(llm_client, qdrant)
        await router.route(
            source=source,
            search_space_id=search_space_id,
            passport_md=passport_md,
            blocks=blocks,
            processed_text=processed_text,
            embedding_scope=embedding_scope,
        )

        return {
            "source": source,
            "passport_path": str(passport_path),
            "passport_md": passport_md,
            "embedding_scope": embedding_scope,
            "block_metadata": block_metadata,
        }

    except Exception as e:
        # Fallback: persiste pasaporte parcial aunque el LLM falle
        if 'passport_partial' in locals():
            writer = BrainWriter()
            writer.write(source, passport_partial.get("raw_md", f"# {filename}\n\nSíntesis fallida: {e}"))
        raise

    finally:
        os.unlink(tmp_path)
```

---

## F3.6 — BrainWatcher: re-indexación automática (Día 4-5)

Si el usuario edita un pasaporte `.md` directamente, el watcher detecta el cambio y re-vectoriza la colección `brain`.

```python
# brain_watcher.py — ya existe en Second Brain, integrar con Celery

from app.brain.writer import BrainWriter
from app.brain.qdrant_manager import QdrantManager
from app.brain.ingest_router import IngestRouter
import asyncio, os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class PassportChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(".md") and not event.src_path.endswith("index.md"):
            source = os.path.splitext(os.path.basename(event.src_path))[0]
            # Despachar tarea Celery de re-vectorización
            reindex_passport_task.delay(source)

def start_watcher():
    brain_path = os.getenv("BRAIN_DATA_PATH", "/data/brain")
    handler = PassportChangeHandler()
    observer = Observer()
    observer.schedule(handler, brain_path, recursive=False)
    observer.start()
    return observer
```

---

## Checklist F3

- [ ] `passport_builder.py` integrado — genera frontmatter determinista sin LLM
- [ ] `Planner` integrado — decide FOCUSED vs CHUNKED con `quality_trigger`
- [ ] `synthesizer.py` adaptado para usar el LLM client de SurfSense
- [ ] `BrainLLMClient` implementado como adaptador
- [ ] `BrainWriter` persistiendo `.md` en `/data/brain/`
- [ ] Campo `passport_path` añadido al modelo `Document` + migración Alembic
- [ ] `IngestRouter` vectorizando pasaporte en colección `brain`
- [ ] Raw ingestion (`/ingest/file/raw`) funcional sin LLM
- [ ] Fallback a pasaporte parcial si el LLM falla (DT robustez)
- [ ] BrainWatcher detectando cambios en `.md` y re-indexando
- [ ] Test: documento `.py` → pasaporte con sección `## 🧩 Core Knowledge` con funciones

---

**Anterior:** [F2 — Qdrant y Tres Colecciones](./F2-qdrant-colecciones.md)  
**Siguiente:** [F4 — Router Multinivel L1→L2→L0](./F4-router-multinivel.md)
