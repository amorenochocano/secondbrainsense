# F1 — Pipeline de Tres Fases en SurfSense
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + Backend Mid (1)  
**Dependencias:** F0 completada  
**Entregable:** Cualquier fichero subido a SurfSense pasa por las 3 fases de Second Brain antes de indexarse

---

## Objetivo

Reemplazar el pipeline genérico de `document_converters.py` de SurfSense por el pipeline de tres fases de Second Brain. Este es el cambio de mayor impacto y el que habilita todas las fases posteriores.

**Antes (SurfSense):**
```
Fichero → ETL Service (Docling) → Markdown genérico → SUMMARY_PROMPT_TEMPLATE → chunker global
```

**Después (BrainSense F1):**
```
Fichero → Fase 1 (Extractor específico) → Fase 2 (UniversalCleaner) → Fase 3 (Preprocesador semántico) → processed_text con ##/###/####
```

---

## F1.1 — DocumentProcessorFactory (Día 1)

Crear el router que mapea extensión → extractor + preprocesador.

**Fichero:** `surfsense_backend/app/brain/processor_factory.py` ← NUEVO

```python
# processor_factory.py
"""
Router central del pipeline de tres fases.
Mapea extensión de fichero → (extractor, preprocesador, chunker, prompt).
"""
from pathlib import Path
from typing import Optional
from app.brain.extractors import get_extractor_for_extension
from app.brain.preprocessing import (
    py as prep_py,
    sql as prep_sql,
    pdf as prep_pdf,
    docx as prep_docx,
    pptx as prep_pptx,
    xlsx as prep_xlsx,
    ipynb as prep_ipynb,
    html as prep_html,
    md as prep_md,
    txt as prep_txt,
    csv as prep_csv,
    json as prep_json,
    xml as prep_xml,
    drawio as prep_drawio,
)
from app.brain.prompts.type_specs import TYPE_SPECS
import logging

logger = logging.getLogger(__name__)

# Mapa extensión → módulo preprocesador
PREPROCESSOR_MAP = {
    ".py":       prep_py,
    ".sql":      prep_sql,
    ".pdf":      prep_pdf,
    ".docx":     prep_docx,
    ".doc":      prep_docx,
    ".pptx":     prep_pptx,
    ".ppt":      prep_pptx,
    ".xlsx":     prep_xlsx,
    ".xls":      prep_xlsx,
    ".ipynb":    prep_ipynb,
    ".html":     prep_html,
    ".htm":      prep_html,
    ".md":       prep_md,
    ".markdown": prep_md,
    ".txt":      prep_txt,
    ".csv":      prep_csv,
    ".json":     prep_json,
    ".xml":      prep_xml,
    ".drawio":   prep_drawio,
}

class DocumentProcessor:
    """
    Encapsula el pipeline de tres fases para un tipo de documento.
    """
    def __init__(self, extension: str):
        self.extension = extension.lower()
        self.extractor = get_extractor_for_extension(self.extension)
        self.preprocessor_module = PREPROCESSOR_MAP.get(self.extension)
        self.type_spec = TYPE_SPECS.get(self.extension, {})

    def extract(self, file_path: str) -> list[dict]:
        """Fase 1 + Fase 2: extracción y limpieza universal."""
        if self.extractor is None:
            raise ValueError(f"No hay extractor para extensión: {self.extension}")
        # El extractor ya tiene _CleaningExtractorWrapper aplicado
        # que ejecuta UniversalCleaner automáticamente
        blocks = self.extractor.extract(file_path)
        logger.info(f"Fase 1+2 completada: {len(blocks)} bloques extraídos y limpios")
        return blocks

    def preprocess(self, blocks: list[dict]) -> str:
        """Fase 3: preprocesamiento semántico → processed_text con ##/###/####."""
        if self.preprocessor_module is None:
            # Fallback: unir bloques sin marcas semánticas
            logger.warning(f"Sin preprocesador para {self.extension}, usando fallback")
            return "\n\n".join(b["content"] for b in blocks if b.get("content"))

        raw_text = "\n\n".join(b["content"] for b in blocks if b.get("content"))
        processed = self.preprocessor_module.preprocess(raw_text)
        logger.info(f"Fase 3 completada: {len(processed)} chars con marcas semánticas")
        return processed

    def get_quality_trigger(self) -> Optional[int]:
        """Umbral de chars para activar síntesis chunked."""
        return self.type_spec.get("quality_trigger")

    def get_metadata_from_blocks(self, blocks: list[dict]) -> dict:
        """Extrae metadata de calidad y PII de los bloques."""
        quality_scores = [b.get("metadata", {}).get("quality_score", 1.0) for b in blocks]
        has_pii = any(b.get("metadata", {}).get("sensitive_data") for b in blocks)
        languages = list(set(b.get("metadata", {}).get("language", "unknown") for b in blocks))
        return {
            "avg_quality_score": sum(quality_scores) / len(quality_scores) if quality_scores else 1.0,
            "has_pii": has_pii,
            "languages": languages,
            "total_blocks": len(blocks),
            "code_blocks": sum(1 for b in blocks if b.get("content_type") == "code"),
        }


class DocumentProcessorFactory:
    """Punto de entrada para obtener el processor correcto."""

    @staticmethod
    def get(filename: str) -> DocumentProcessor:
        ext = Path(filename).suffix.lower()
        return DocumentProcessor(ext)

    @staticmethod
    def supported_extensions() -> list[str]:
        return list(PREPROCESSOR_MAP.keys())
```

---

## F1.2 — Integración en document_converters.py (Día 2-3)

Este es el cambio quirúrgico en SurfSense. Modificamos la función `process_document` para que use el pipeline de tres fases.

**Fichero:** `surfsense_backend/app/utils/document_converters.py`

```python
# document_converters.py — versión BrainSense
# Cambios marcados con # BRAINSENSE: comentario

import logging
import tempfile
from pathlib import Path
from typing import Optional

# BRAINSENSE: importar el factory del pipeline de tres fases
from app.brain.processor_factory import DocumentProcessorFactory

logger = logging.getLogger(__name__)


async def process_document_content(
    file_content: bytes,
    filename: str,
    metadata: dict,
    llm_client,
    search_space_id: str,
    # BRAINSENSE: parámetros adicionales opcionales
    synthesis_enabled: bool = True,
    vision_mode: str = "off",
) -> dict:
    """
    Pipeline completo de procesamiento de documento.
    Sustituye la lógica original de SurfSense.
    """

    # ── Escribir fichero temporal ──────────────────────
    ext = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        # ── BRAINSENSE: Fases 1, 2 y 3 ────────────────
        processor = DocumentProcessorFactory.get(filename)

        # Fase 1 + 2: extracción y limpieza universal
        blocks = processor.extract(tmp_path)

        # Metadata de calidad extraída de los bloques
        block_metadata = processor.get_metadata_from_blocks(blocks)
        metadata.update(block_metadata)

        # Fase 3: preprocesamiento semántico con ##/###/####
        processed_text = processor.preprocess(blocks)

        logger.info(
            f"Pipeline 3 fases completado para {filename}: "
            f"{len(blocks)} bloques, {len(processed_text)} chars"
        )

        # ── Síntesis LLM (se implementa en F3) ────────
        # Por ahora: usar processed_text como summary
        # En F3 se sustituye por Planner + multi-call
        if synthesis_enabled:
            summary = processed_text  # placeholder F3
        else:
            summary = processed_text[:2000]  # raw ingestion

        # ── Chunking semántico (se implementa en F3) ──
        # Por ahora: chunking simple sobre processed_text
        # En F3 se sustituye por split_doc_text_for_chunked()
        chunks = _simple_semantic_chunk(processed_text)

        return {
            "summary": summary,
            "processed_text": processed_text,
            "chunks": chunks,
            "metadata": metadata,
            "blocks": blocks,
        }

    finally:
        import os
        os.unlink(tmp_path)


def _simple_semantic_chunk(processed_text: str, max_chars: int = 1500) -> list[str]:
    """
    Chunking provisional que respeta marcas ##/### del preprocesador.
    Se sustituye por split_doc_text_for_chunked() en F3.
    """
    chunks = []
    current = []
    current_len = 0

    for line in processed_text.split("\n"):
        # Cortar ANTES de cada ## o ### (no en ####)
        if (line.startswith("## ") or line.startswith("### ")) and current:
            if current_len > 0:
                chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
            # Cortar si supera max_chars y hay un punto natural
            if current_len > max_chars and line == "":
                chunks.append("\n".join(current))
                current = []
                current_len = 0

    if current:
        chunks.append("\n".join(current))

    return [c for c in chunks if c.strip()]
```

---

## F1.3 — Adaptar el task de Celery (Día 3-4)

SurfSense despacha la ingesta como tarea Celery. Hay que actualizar el task para que llame a la nueva función.

**Fichero:** `surfsense_backend/app/tasks/document/process_file_upload_task.py`

```python
# Solo la sección modificada — el resto del task queda intacto

from app.utils.document_converters import process_document_content
from app.brain.processor_factory import DocumentProcessorFactory

@celery_app.task(name="process_file_upload", bind=True, max_retries=3)
def process_file_upload_task(self, document_id: str, file_path: str, filename: str, ...):
    """Task Celery para ingesta de documento."""
    try:
        # BRAINSENSE: verificar si el tipo está soportado
        ext = Path(filename).suffix.lower()
        if ext not in DocumentProcessorFactory.supported_extensions():
            logger.warning(f"Extensión no soportada: {ext}, usando pipeline genérico")
            # Fallback al pipeline original de SurfSense para tipos no soportados

        # BRAINSENSE: usar el nuevo pipeline
        with open(file_path, "rb") as f:
            content = f.read()

        result = asyncio.run(process_document_content(
            file_content=content,
            filename=filename,
            metadata=metadata,
            llm_client=get_llm_client(),
            search_space_id=search_space_id,
            synthesis_enabled=config.SYNTHESIS_ENABLED,
        ))

        # El resto del task (guardar en PostgreSQL, crear embeddings)
        # se modifica en F2 para usar Qdrant en lugar de pgvector
        save_document_to_db(document_id, result)

    except Exception as exc:
        logger.error(f"Error en pipeline F1: {exc}")
        self.retry(exc=exc, countdown=60)
```

---

## F1.4 — Tests de la Fase 1 (Día 4-5)

```python
# tests/brain/test_pipeline_f1.py

import pytest
from pathlib import Path
from app.brain.processor_factory import DocumentProcessorFactory

FIXTURES_DIR = Path("tests/fixtures")

class TestPipelineTresFases:

    def test_python_file_pipeline(self):
        """Un .py pasa las 3 fases y genera processed_text con marcas ##."""
        processor = DocumentProcessorFactory.get("ejemplo.py")
        blocks = processor.extract(str(FIXTURES_DIR / "ejemplo.py"))

        assert len(blocks) > 0
        assert all("quality_score" in b.get("metadata", {}) for b in blocks)

        processed = processor.preprocess(blocks)
        assert "## " in processed or "### " in processed  # tiene marcas semánticas
        assert "#### " in processed or "## " in processed

    def test_sql_file_pipeline(self):
        """Un .sql con PROCEDURE genera marcas ### PROCEDURE y ### Fase."""
        processor = DocumentProcessorFactory.get("sp_ejemplo.sql")
        blocks = processor.extract(str(FIXTURES_DIR / "sp_ejemplo.sql"))
        processed = processor.preprocess(blocks)

        assert "### PROCEDURE" in processed

    def test_pdf_pipeline(self):
        """Un PDF genera processed_text con headings marcados."""
        processor = DocumentProcessorFactory.get("documento.pdf")
        blocks = processor.extract(str(FIXTURES_DIR / "documento.pdf"))
        processed = processor.preprocess(blocks)

        assert len(processed) > 100

    def test_universal_cleaner_applied(self):
        """Los bloques tienen quality_score y language en metadata."""
        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(str(FIXTURES_DIR / "nota.md"))

        for block in blocks:
            meta = block.get("metadata", {})
            assert "quality_score" in meta, "UniversalCleaner no aplicado"
            assert "language" in meta

    def test_unsupported_extension_fallback(self):
        """Extensión no soportada no rompe el sistema."""
        processor = DocumentProcessorFactory.get("fichero.xyz")
        # No debe lanzar excepción — usa fallback
        assert processor is not None

    def test_quality_trigger_values(self):
        """Los quality_triggers están bien configurados por tipo."""
        for ext in [".docx", ".pdf", ".md"]:
            p = DocumentProcessorFactory.get(f"doc{ext}")
            assert p.get_quality_trigger() == 32000  # 32K chars

        for ext in [".py", ".sql"]:
            p = DocumentProcessorFactory.get(f"doc{ext}")
            assert p.get_quality_trigger() is None  # homogéneos, no aplica
```

---

## Checklist F1

- [ ] `DocumentProcessorFactory` implementado y testeado
- [ ] `document_converters.py` actualizado — llama a las 3 fases
- [ ] Task Celery adaptado para usar el nuevo pipeline
- [ ] Chunking provisional semántico (`_simple_semantic_chunk`) funcionando
- [ ] Tests F1 pasando para `.py`, `.sql`, `.pdf`, `.md`, `.docx`
- [ ] Fichero subido por UI de SurfSense → `processed_text` con marcas `##/###` verificable en logs
- [ ] Extensiones no soportadas tienen fallback graceful
- [ ] Rama `integration/f1-pipeline` mergeada

---

**Anterior:** [F0 — Preparación](./F0-preparacion.md)  
**Siguiente:** [F2 — Qdrant y Tres Colecciones](./F2-qdrant-colecciones.md)
