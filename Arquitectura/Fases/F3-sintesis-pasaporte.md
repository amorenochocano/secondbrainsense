# F3 â€” SÃ­ntesis Multi-call y Pasaporte SemÃ¡ntico
**DuraciÃ³n:** 1 semana  
**Equipo:** Backend Senior (1) + IA Engineer (1)  
**Dependencias:** F2 completada. F1 completada (incluye `masters.py` o `BrainMetadataService` operativo â€” `passport_builder.py` lo importa)  
**Entregable:** Cada documento ingestado genera un pasaporte `.md` en 7 secciones, persiste en `/data/brain/`, y se vectoriza en la colecciÃ³n `brain`. `BrainWatcher` funcional con imports corregidos.

---

## Objetivo

Sustituir el `SUMMARY_PROMPT_TEMPLATE` genÃ©rico de SurfSense por el sistema completo de sÃ­ntesis hÃ­brida de Second Brain: `build_partial_passport` (determinista) + `DocumentSynthesizer.synthesize()` (LLM multi-call con Planner).

**Antes (SurfSense):**
```
processed_text â†’ 1 llamada LLM â†’ string summary plano
```

**DespuÃ©s (BrainSense F3):**
```
processed_text â†’ build_partial_passport() (determinista, sin LLM)
              â†’ DocumentSynthesizer.synthesize() (Planner â†’ N llamadas LLM)
              â†’ pasaporte .md 7 secciones
              â†’ BrainWriter.write() â†’ /data/brain/{slug}.md
              â†’ IngestRouter.route() â†’ brain collection (Qdrant)
```

---

## F3.0 â€” Estado del codebase: quÃ© existe y quÃ© es nuevo (DÃ­a 0)

| Fichero | Estado | AcciÃ³n F3 |
|---------|--------|-----------|
| `app/brain/passport_builder.py` | âœ… Existe | Usar `build_partial_passport()` â€” **no** `PassportBuilder.build()` |
| `app/brain/synthesizer.py` | âœ… Existe â€” `DocumentSynthesizer` **sÃ­ncrono** | Llamar `synthesize()` â€” **no** async ni `synthesize_focused()` |
| `app/brain/llm_client.py` | âœ… Existe â€” `LLMClient` + singleton `llm_client` | Sin cambios â€” `synthesizer.py` ya lo usa internamente |
| `app/brain/writer.py` | âœ… Existe â€” `BrainWriter(brain_dir)` | Usar correctamente: `.write()` devuelve `dict`, no `Path` |
| `app/brain/brain_watcher.py` | âœ… Existe â€” import roto (`ingest_utils`) | **Corregir** import + aÃ±adir `search_space_id` en delete |
| `app/brain/masters.py` | âš ï¸ Dependencia de F1 | `passport_builder.py` importa `from app.brain.masters import classify_document` â€” F1 debe estar completo |

**Dependencia crÃ­tica de F1:**  
`passport_builder.py` lÃ­nea ~47 importa `from app.brain.masters import classify_document`. Si F1.2 reemplazÃ³ `masters.py` por `BrainMetadataService`, debe existir un shim `app/brain/masters.py` que re-exporte `classify_document` delegando a `BrainMetadataService`. Sin esto, toda la sÃ­ntesis falla en import.

```python
# app/brain/masters.py â€” shim de compatibilidad (crear en F1 si no existe)
"""Shim de compatibilidad: delega a BrainMetadataService."""
from app.brain.metadata_service import get_brain_metadata_service

def classify_document(filename: str, content: str = "") -> dict:
    svc = get_brain_metadata_service()
    return svc.classify_document(filename=filename, content=content, search_space_id=None)
```

---

## F3.1 â€” Contratos reales de `passport_builder` y `synthesizer` (DÃ­a 1)

### `build_partial_passport()` â€” funciÃ³n, no clase

> **âš ï¸ Error del documento original**: propone `PassportBuilder.build()` como clase. La funciÃ³n real es `build_partial_passport()` a nivel de mÃ³dulo.

```python
from app.brain.passport_builder import build_partial_passport

# Firma real:
passport_result = build_partial_passport(
    source=source,          # nombre fichero original (ej: "informe.pdf")
    file_type=ext,          # extensiÃ³n sin punto (ej: "pdf", "py")
    blocks=blocks,          # lista de bloques del extractor
    full_text=processed_text,
    metadata=metadata or {},
    source_location="",     # URL o ruta absoluta de origen (opcional)
    source_type="upload",   # "upload" | "github" | "sharepoint" | "url"
    source_repo="",
    source_repo_path="",
)

# Retorna dict con:
# {
#   "passport_partial": str,        â† YAML frontmatter + placeholders para LLM
#   "entities": list[str],
#   "tags_base": list[str],
#   "triggers": list[str],
#   "entities_for_extraction": list[str],
# }
passport_partial_md = passport_result["passport_partial"]
```

### `DocumentSynthesizer.synthesize()` â€” sÃ­ncrono, una sola entrada

> **âš ï¸ Error del documento original**: propone `async def _run_synthesis()` llamando `await synthesizer.synthesize_focused()` y `await synthesizer.synthesize_chunked()`. Esos mÃ©todos no existen. El mÃ©todo real es `synthesize()` y es **sÃ­ncrono**.

```python
from app.brain.synthesizer import DocumentSynthesizer

synth = DocumentSynthesizer()

# Firma real:
result = synth.synthesize(
    source=source,          # nombre fichero
    file_type=ext,          # extensiÃ³n sin punto
    full_text=processed_text,
    blocks=blocks,          # bloques tipados del extractor
    metadata=metadata or {},
    provider=None,          # None â†’ usa DEFAULT_PROVIDER del .env
    model=None,             # None â†’ usa SYNTHESIS_MODEL del .env
    current_md=None,        # str: contenido actual si es modo mejora
    ingest_metadata=ingest_metadata or {},
)

# Retorna dict con:
# {
#   "md_content": str,      â† pasaporte .md completo con frontmatter
#   "tags": list[str],
#   "entities": list[str],
#   "drill_down_triggers": list[str],
#   "llm_ok": bool,
# }
passport_md = result["md_content"]
```

**El synthesizer ya integra internamente:**
- `build_partial_passport()` (Paso 1 â€” sin LLM)
- `ModelProfile` y `Planner` para decidir `single-call` / `2-calls` / `chunked` (Paso 2)
- `normalize_tags()` sobre el resultado final

Por tanto, **no se debe llamar a `build_partial_passport()` y `synthesizer.synthesize()` por separado**. El synthesizer es el punto de entrada Ãºnico cuando hay LLM activo.

**Solo llamar a `build_partial_passport()` directamente en modo raw** (sin LLM, `SYNTHESIS_ENABLED=false`):
```python
if not synthesis_enabled:
    passport_result = build_partial_passport(source=source, file_type=ext, blocks=blocks,
                                              full_text=processed_text, metadata=metadata)
    passport_md = passport_result["passport_partial"]
```

---

## F3.2 â€” No se necesita `BrainLLMClient` (DÃ­a 1)

> **âš ï¸ Error del documento original**: propone crear `BrainLLMClient` como adaptador entre el synthesizer y SurfSense. **No se crea.**

`synthesizer.py` ya importa y usa `llm_client` directamente:
```python
from app.brain.llm_client import llm_client, DEFAULT_PROVIDER, SYNTHESIS_MODEL
```

El `LLMClient` en `llm_client.py` ya abstrae Ollama y Claude con configuraciÃ³n por `.env`. SurfSense no tiene su propio servicio LLM separado â€” el mÃ³dulo `app.brain.llm_client` es el servicio LLM del proyecto. No hay nada que adaptar.

**Lo que sÃ­ hay que verificar en `.env.dev`:**
```bash
SYNTHESIS_MODEL=qwen2.5-coder:7b
OLLAMA_HOST=http://host.docker.internal:11434
LLM_PROVIDER=ollama
SYNTHESIS_ENABLED=true
SYNTHESIS_V5_ENABLED=true
SYNTHESIS_HYBRID=false
```

---

## F3.3 â€” `BrainWriter`: contrato real (DÃ­a 2)

**Fichero:** `surfsense_backend/app/brain/writer.py` â† **ya existe, NO reemplazar**

> **âš ï¸ Error del documento original**: muestra un `BrainWriter` simplificado que no coincide con el real. El constructor usa `brain_dir` (no `brain_path`), y `.write()` devuelve un `dict`, no un `Path`.

```python
from app.brain.writer import BrainWriter

writer = BrainWriter()  # usa BRAIN_DIR = /data/brain del .env

# write() devuelve dict {"path": str, "status": "created"|"overwritten"|"exists_warning"|"empty"}
write_result = writer.write(source=source, md_content=passport_md, overwrite=True)

# Extraer path del resultado:
passport_path = write_result["path"]   # â† str, no Path
write_status = write_result["status"]  # "created" | "overwritten" | "exists_warning"

if write_status == "exists_warning":
    logger.warning("Pasaporte ya existÃ­a para '%s', no se sobreescribiÃ³", source)
elif passport_path:
    logger.info("Pasaporte escrito en '%s'", passport_path)
```

**Verificar `BRAIN_DIR` en docker-compose.dev.yml:**
```yaml
# En el servicio backend y worker:
environment:
  BRAIN_DIR: /data/brain
volumes:
  - brain_data:/data/brain  # volumen persistente
```

---

## F3.4 â€” MigraciÃ³n Alembic 161: campos de pasaporte en Document (DÃ­a 2)

**NÃºmero de migraciÃ³n:** `161` (siguiente tras `160_brain_metadata_tables.py` de F1)  
**Fichero:** `surfsense_backend/alembic/versions/161_passport_fields_to_document.py`

```python
"""add passport fields to document

Revision ID: 161
Revises: 160
Create Date: 2026-06-XX
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '161'
down_revision = '160'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('document', sa.Column('passport_path',         sa.String(),            nullable=True))
    op.add_column('document', sa.Column('passport_generated_at', sa.DateTime(),           nullable=True))
    op.add_column('document', sa.Column('avg_quality_score',     sa.Float(),              nullable=True))
    op.add_column('document', sa.Column('has_pii',               sa.Boolean(),            nullable=True, server_default='false'))
    op.add_column('document', sa.Column('detected_languages',    postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('document', sa.Column('embedding_scope',       postgresql.ARRAY(sa.String()), nullable=True, server_default="'{knowledge,code}'"))

def downgrade():
    for col in ['passport_path', 'passport_generated_at', 'avg_quality_score',
                'has_pii', 'detected_languages', 'embedding_scope']:
        op.drop_column('document', col)
```

**AÃ±adir en `surfsense_backend/app/db.py`** (modelo `Document` existente):
```python
# En la clase Document (aÃ±adir campos):
passport_path         = Column(String,            nullable=True)
passport_generated_at = Column(DateTime,          nullable=True)
avg_quality_score     = Column(Float,             nullable=True)
has_pii               = Column(Boolean,           nullable=True, default=False)
detected_languages    = Column(ARRAY(String),     nullable=True)
embedding_scope       = Column(ARRAY(String),     nullable=True, default=["knowledge", "code"])
```

---

## F3.5 â€” Pipeline completo en `document_converters.py` (DÃ­a 3)

**Fichero:** `surfsense_backend/app/utils/document_converters.py` â† extender (ya modificado en F1)

```python
# Imports necesarios en document_converters.py
from app.brain.synthesizer import DocumentSynthesizer
from app.brain.passport_builder import build_partial_passport
from app.brain.writer import BrainWriter
from app.brain.qdrant_manager import QdrantManager
from app.brain.ingest_router import IngestRouter


def process_document_content(
    file_content: bytes,
    filename: str,
    metadata: dict,
    search_space_id: str,
    synthesis_enabled: bool = True,
    synthesis_model: str = None,
    ingest_metadata: dict = None,
) -> dict:
    """
    Pipeline completo F1+F2+F3 (sÃ­ncrono â€” Celery no es async).
    Devuelve dict con passport_md, source, embedding_scope, blocks, processed_text.
    """
    import tempfile, os
    from pathlib import Path

    ext = Path(filename).suffix.lower().lstrip(".")
    source = _slugify(Path(filename).stem)
    ingest_metadata = ingest_metadata or {
        "ingest_origin": "file_upload",
        "ingest_path": filename,
    }

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        # â”€â”€ FASES 1+2: ExtracciÃ³n y limpieza (F1) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        processor = DocumentProcessorFactory.get(filename)
        blocks = processor.extract(tmp_path)
        processed_text = processor.preprocess(blocks)

        # â”€â”€ SÃNTESIS LLM O MODO RAW (F3) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if synthesis_enabled and blocks:
            synth = DocumentSynthesizer()
            result = synth.synthesize(
                source=source,
                file_type=ext,
                full_text=processed_text,
                blocks=blocks,
                metadata=metadata or {},
                provider=None,          # DEFAULT_PROVIDER del .env
                model=synthesis_model,  # None â†’ SYNTHESIS_MODEL del .env
                ingest_metadata=ingest_metadata,
            )
            passport_md = result["md_content"]
            embedding_scope = ["brain", "knowledge", "code"] if any(
                b.get("content_type") == "code" for b in blocks
            ) else ["brain", "knowledge"]
        else:
            # Raw: pasaporte determinista sin LLM
            passport_result = build_partial_passport(
                source=source, file_type=ext, blocks=blocks,
                full_text=processed_text, metadata=metadata or {},
                source_type="upload",
            )
            passport_md = passport_result["passport_partial"]
            embedding_scope = ["knowledge"]  # sin brain â€” pasaporte incompleto

        if not passport_md or not passport_md.strip():
            logger.warning("SÃ­ntesis vacÃ­a para '%s', usando fallback mÃ­nimo", filename)
            passport_md = f"# {filename}\n\nSÃ­ntesis no disponible.\n"

        # â”€â”€ PERSISTIR PASAPORTE (F3) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        writer = BrainWriter()
        write_result = writer.write(source=source, md_content=passport_md, overwrite=True)
        passport_path = write_result.get("path", "")
        logger.info("BrainWriter: status=%s path=%s", write_result["status"], passport_path)

        # â”€â”€ VECTORIZAR EN QDRANT (F2) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        qdrant_mgr = QdrantManager.get_instance()
        router = IngestRouter(qdrant_client=qdrant_mgr.client)   # constructor real
        router.route(                                             # sÃ­ncrono â€” no await
            md_content=passport_md,
            blocks=blocks,
            source=source,
            search_space_id=search_space_id,
            ingest_metadata=ingest_metadata,
        )

        return {
            "source":          source,
            "passport_path":   passport_path,
            "passport_md":     passport_md,
            "embedding_scope": embedding_scope,
            "processed_text":  processed_text,
            "blocks":          blocks,
        }

    except Exception as exc:
        logger.error("process_document_content error para '%s': %s", filename, exc)
        # Pasaporte fallback â€” no dejar al router sin contenido
        fallback_md = f"# {filename}\n\nSÃ­ntesis fallida: {exc}\n"
        try:
            BrainWriter().write(source=source, md_content=fallback_md, overwrite=True)
        except Exception:
            pass
        raise

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
```

> **Nota importante**: `DocumentSynthesizer.synthesize()` es **sÃ­ncrono**. `IngestRouter.route()` es **sÃ­ncrono**. Todo el pipeline corre en el worker de Celery sin necesidad de `asyncio`. No usar `await` en ningÃºn punto de este flujo.

---

## F3.6 â€” `BrainWatcher`: corregir imports rotos (DÃ­a 4)

**Fichero:** `surfsense_backend/app/brain/brain_watcher.py` â† corregir dos problemas

> **Watcher existente ya usa `watchfiles.awatch`** (asyncio puro, no watchdog). Lo de F1.4 es correcto: `asyncio.create_task(watch_brain_dir())` en lifespan. Solo hay que corregir los problemas del cÃ³digo actual:

**Problema 1 â€” Import roto en `_reingest_md()` (lÃ­nea ~85):**
```python
# ANTES (roto â€” ingest_utils no existe en SecondBrainSense):
from ingest_utils import get_embedding

# DESPUÃ‰S â€” usar _embed de ingest_router (funciÃ³n que ya usa el resto del sistema):
from app.brain.ingest_router import _embed
```

Y actualizar la lÃ­nea de creaciÃ³n del modelo de embedding:
```python
# ANTES:
embed_model = type("EmbedModel", (), {"embed": staticmethod(get_embedding)})

# DESPUÃ‰S:
embed_model = type("EmbedModel", (), {"embed": staticmethod(lambda t: _embed(t, "nomic-embed-text"))})
```

**Problema 2 â€” `_delete_old_chunks()` no filtra por `search_space_id`:**

El watcher actual borra chunks de cualquier tenant que tenga el mismo slug. Esto es un problema de seguridad multi-tenant idÃ©ntico al G9 de F2.

```python
# ANTES: busca por slug sin filtro de tenant
ids = [
    p.id for p in result
    if p.payload and _slugify(p.payload.get("source", "")) == slug
]

# DESPUÃ‰S: buscar usando Filter de Qdrant por source slug y NO borrar si search_space_id
# no estÃ¡ disponible (el watcher no conoce el space â€” registrar advertencia)
# SoluciÃ³n pragmÃ¡tica para F3: el watcher re-ingesta sin borrar por tenant
# (el BrainIngestor hace upsert â€” los IDs son deterministas por md5(source+collection+idx))
# â†’ duplicados imposibles, re-ingestiÃ³n segura. Borrado selectivo se delega a F4/F6.
```

> **DecisiÃ³n de diseÃ±o para F3**: el watcher usa `BrainIngestor.ingest_md()` que genera IDs deterministas con `hashlib.md5`. Re-ingestar el mismo `.md` hace upsert idempotente â€” no crea duplicados. El borrado selectivo por `search_space_id` se documenta como deuda tÃ©cnica (DT-F3-watcher) para F6 (Admin UI).

**Resultado final de `_reingest_md()` corregido:**
```python
def _reingest_md(md_path: str):
    """
    Lee el .md del disco y lo re-vectoriza en BRAIN.
    Import corregido: usa _embed de ingest_router (no ingest_utils inexistente).
    """
    from qdrant_client import QdrantClient
    from app.brain.ingest_router import _embed     # â† FIX: import correcto
    from app.brain.brain_ingest import BrainIngestor

    slug = _source_from_md_path(md_path)
    log.info("[watcher] Re-ingestiÃ³n de '%s' (slug='%s')", md_path, slug)

    try:
        content = Path(md_path).read_text(encoding="utf-8").lstrip("\ufeff")
    except Exception as exc:
        log.error("[watcher] No se pudo leer '%s': %s", md_path, exc)
        return

    if not content.strip():
        log.warning("[watcher] '%s' vacÃ­o, se omite.", md_path)
        return

    try:
        qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        _delete_old_chunks(qdrant_client, slug)

        # Fix: _embed toma (text, model) â€” misma funciÃ³n que usa IngestRouter
        embed_model = type("EmbedModel", (), {
            "embed": staticmethod(lambda t: _embed(t, "nomic-embed-text"))
        })
        ingestor = BrainIngestor(qdrant_client, embed_model, collection=C_BRAIN)
        chunks_created = ingestor.ingest_md(source=slug, md_content=content, metadata={"md_path": md_path})
        log.info("[watcher] '%s' â†’ %d chunks en '%s'", md_path, chunks_created, C_BRAIN)
    except Exception as exc:
        log.error("[watcher] Error re-ingestando '%s': %s", md_path, exc)
```

---

## F3.7 â€” Task Celery actualizado (DÃ­a 4)

El task Celery ya no gestiona sÃ­ntesis directamente â€” delega completamente en `process_document_content()`:

```python
# En el task de ingesta Celery (reemplazar secciÃ³n de sÃ­ntesis):
from app.utils.document_converters import process_document_content
from app.db import Document
import datetime

@celery_app.task(bind=True, max_retries=3)
def process_file_upload_task(self, document_id: str, search_space_id: str, ...):
    try:
        # Leer fichero del storage
        file_content = read_file_from_storage(document_id)
        filename = get_document_filename(document_id)

        # Pipeline completo F1+F2+F3
        result = process_document_content(
            file_content=file_content,
            filename=filename,
            metadata={"document_id": document_id},
            search_space_id=search_space_id,
            synthesis_enabled=True,
        )

        # Actualizar PostgreSQL con campos de pasaporte (F3.4)
        with get_db_session() as db:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if doc:
                doc.passport_path         = result["passport_path"]
                doc.passport_generated_at = datetime.datetime.utcnow()
                doc.embedding_scope       = result["embedding_scope"]
                db.commit()

        return {"status": "ok", "source": result["source"]}

    except Exception as exc:
        countdown = 60 * (self.request.retries + 1)
        raise self.retry(exc=exc, countdown=countdown)
```

---

## Checklist F3

### F3.0 â€” Prerrequisitos
- [ ] F1.2 completo: `BrainMetadataService` operativo
- [ ] `app/brain/masters.py` existe como shim exportando `classify_document` que delega a `BrainMetadataService`
- [ ] `from app.brain.masters import classify_document` no falla en import (verificar en contenedor)
- [ ] `BRAIN_DIR=/data/brain` en docker-compose.dev.yml y volumen persistente mapeado

### F3.1 â€” Contratos de passport_builder y synthesizer
- [ ] `build_partial_passport(source, file_type, blocks, full_text, metadata, ...)` devuelve dict con clave `"passport_partial"`
- [ ] `DocumentSynthesizer().synthesize(source, file_type, full_text, blocks, metadata, ...)` devuelve dict con clave `"md_content"`
- [ ] No existe ningÃºn `BrainLLMClient`, `synthesize_focused()` ni `synthesize_chunked()` en el cÃ³digo
- [ ] `SYNTHESIS_MODEL=qwen2.5-coder:7b` en `.env.dev`

### F3.2 â€” Sin `BrainLLMClient`
- [ ] NingÃºn fichero nuevo de adaptador LLM creado â€” `llm_client.py` no se modifica
- [ ] Variables de entorno LLM verificadas en `.env.dev`

### F3.3 â€” BrainWriter
- [ ] `BrainWriter().write(source, md_content, overwrite=True)` usado correctamente (devuelve `dict`)
- [ ] `passport_path = write_result["path"]` â€” no `str(write_result)`
- [ ] Volumen `/data/brain` accesible desde los contenedores `backend` y `worker`

### F3.4 â€” MigraciÃ³n Alembic 161
- [ ] `161_passport_fields_to_document.py` creado con `down_revision = '160'`
- [ ] Campos aÃ±adidos al modelo `Document` en `db.py`
- [ ] `alembic upgrade head` ejecutado sin errores

### F3.5 â€” Pipeline en document_converters.py
- [ ] `process_document_content()` llama a `synth.synthesize()` (sÃ­ncrono, sin await)
- [ ] `IngestRouter(qdrant_client=qdrant_mgr.client)` â€” constructor correcto
- [ ] `router.route(md_content=..., blocks=..., source=..., search_space_id=...)` â€” sÃ­ncrono
- [ ] Fallback: pasaporte mÃ­nimo escrito en caso de error de sÃ­ntesis
- [ ] Modo raw (`synthesis_enabled=False`) usa `build_partial_passport()` directamente

### F3.6 â€” BrainWatcher
- [ ] `from ingest_utils import get_embedding` reemplazado por `from app.brain.ingest_router import _embed`
- [ ] `embed_model = type("EmbedModel", (), {"embed": staticmethod(lambda t: _embed(t, "nomic-embed-text"))})`
- [ ] `watch_brain_dir()` arranca en lifespan con `asyncio.create_task()` (ya en F1.4)
- [ ] DT-F3-watcher documentado: borrado selectivo por `search_space_id` pendiente para F6

### F3.7 â€” Celery task
- [ ] Task delega en `process_document_content()` â€” no gestiona sÃ­ntesis directamente
- [ ] `Document.passport_path` y `Document.passport_generated_at` actualizados tras sÃ­ntesis
- [ ] Backoff lineal en reintentos

### Criterio de aceptaciÃ³n global F3
- [ ] Un `.py` ingestado â†’ `/data/brain/{slug}.md` existe con secciÃ³n `## ðŸ§© Core Knowledge`
- [ ] `docker compose logs worker | grep "BrainWriter"` â†’ `status=created`
- [ ] ColecciÃ³n `brain` en Qdrant tiene vectores del pasaporte con `search_space_id` en payload
- [ ] `SYNTHESIS_ENABLED=false` â†’ pasaporte determinista sin llamar a Ollama
- [ ] `docker compose logs worker | grep "watcher"` â†’ `Monitorizando cambios en '/data/brain'`
- [ ] Editar manualmente un `.md` en `/data/brain/` â†’ watcher lo re-indexa en < 5 segundos

---

**Anterior:** [F2 â€” Qdrant y Tres Colecciones](./F2-qdrant-colecciones.md)  
**Siguiente:** [F4 â€” Router Multinivel L1â†’L2â†’L0](./F4-router-multinivel.md)

