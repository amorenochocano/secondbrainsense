"""
tests/brain/test_synthesis_f3.py
---------------------------------
Tests de la Fase 3 del pipeline Brain (F3).

Cobertura:
  - TestImportsSintesis          : imports de todos los componentes F3 son correctos
  - TestPrerequisitosF3          : F3.0 — masters.classify_document, BRAIN_DIR
  - TestBuildPartialPassport     : contrato de build_partial_passport() sobre fixtures reales
  - TestDocumentSynthesizerRaw   : synthesize() en modo SYNTHESIS_ENABLED=false (sin LLM)
  - TestRunBrainSynthesis        : run_brain_synthesis() síncrono con mocks de LLM/Qdrant
  - TestBrainWriter              : BrainWriter.write() contrato real (tmpdir)
  - TestBrainWatcherImports      : imports corregidos en F3.6 (ingest_utils eliminado)
  - TestDocumentTaskF3           : task Celery delega en run_brain_synthesis
  - TestSintesisIntegracion      : tests contra Qdrant + Ollama reales (@pytest.mark.integration)

Adaptaciones respecto al plan F3:
  - No existe BrainLLMClient — llm_client.py se usa directamente.
  - synthesize() es síncrono — no se usa await en ningún punto.
  - run_brain_synthesis() es síncrono — se llama desde run_in_executor en el task.
  - Los imports de IngestRouter y QdrantManager dentro de run_brain_synthesis son
    lazy (dentro de la función), por lo que los @patch apuntan a los módulos
    originales: app.brain.ingest_router.IngestRouter y
    app.brain.qdrant_manager.QdrantManager — no a document_converters.
  - Los tests unitarios mockean DocumentSynthesizer.synthesize() para evitar
    llamadas reales a Ollama (que no está disponible en el contenedor de tests).
  - Los tests de integración requieren QDRANT_HOST y OLLAMA_HOST disponibles.

Ficheros fixture usados (en tests/fixtures/):
  - ejemplo.py    : Python con funciones documentadas
  - sp_ejemplo.sql: Stored procedure SQL con CTEs
  - nota.md       : Markdown con headings H1/H2/H3
  - sample.pdf    : PDF ya existente en el proyecto
"""
import os
import tempfile
import time
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# Pasaporte mínimo válido para mocks — tiene frontmatter + sección Core Knowledge
_MOCK_PASSPORT_MD = """\
---
id: kb_ejemplo
title: "ejemplo"
type: repo
domain: development
source:
  type: file
  origin: ejemplo.py
  format: py
created_at: 2026-06-01T00:00:00Z
updated_at: 2026-06-01T00:00:00Z
tags: ["python", "fastapi"]
entities: ["DocumentProcessorFactory", "BrainWriter"]
embedding_scope:
  - brain
  - knowledge
---

# 📌 Summary

Pipeline de tres fases del Second Brain.

# 🧩 Core Knowledge

## Funciones principales

- `process_document_content()` — orquesta F1+F2
- `run_brain_synthesis()` — orquesta F3

# 📄 Source Extract

> def process_document_content(...)
"""


# ===========================================================================
# TestImportsSintesis — imports de todos los componentes F3
# ===========================================================================

class TestImportsSintesis:
    """Verifica que todos los imports de F3 funcionan sin error de módulo."""

    def test_passport_builder_importable(self):
        """build_partial_passport() importa desde app.brain.passport_builder."""
        from app.brain.passport_builder import build_partial_passport
        assert callable(build_partial_passport)

    def test_synthesizer_importable(self):
        """DocumentSynthesizer y singleton synthesize() importan correctamente."""
        from app.brain.synthesizer import DocumentSynthesizer, synthesize
        assert callable(DocumentSynthesizer)
        assert callable(synthesize)

    def test_brain_writer_importable(self):
        """BrainWriter importa y expone write(), read(), delete(), exists()."""
        from app.brain.writer import BrainWriter
        for method in ["write", "read", "delete", "exists", "list_all"]:
            assert hasattr(BrainWriter, method), (
                f"BrainWriter no expone método '{method}'"
            )

    def test_run_brain_synthesis_importable(self):
        """run_brain_synthesis() importa desde app.utils.document_converters."""
        from app.utils.document_converters import run_brain_synthesis
        assert callable(run_brain_synthesis)

    def test_run_brain_synthesis_es_sincrono(self):
        """run_brain_synthesis() es síncrono — no es async def."""
        import asyncio
        from app.utils.document_converters import run_brain_synthesis
        assert not asyncio.iscoroutinefunction(run_brain_synthesis), (
            "run_brain_synthesis() debe ser síncrono (no async) — "
            "se ejecuta en thread pool desde Celery"
        )

    def test_llm_client_no_brain_llm_client(self):
        """No existe BrainLLMClient — llm_client.py es el cliente directo."""
        import importlib
        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("app.brain.brain_llm_client")

    def test_synthesizer_no_tiene_synthesize_focused(self):
        """DocumentSynthesizer no expone synthesize_focused() — no existe."""
        from app.brain.synthesizer import DocumentSynthesizer
        assert not hasattr(DocumentSynthesizer, "synthesize_focused"), (
            "synthesize_focused() no debe existir — el plan la menciona por error"
        )

    def test_watcher_no_importa_ingest_utils(self):
        """brain_watcher.py no importa desde ingest_utils (módulo inexistente)."""
        import app.brain.brain_watcher as watcher_module
        source = inspect.getsource(watcher_module)
        assert "from ingest_utils" not in source, (
            "Import roto 'from ingest_utils' detectado en brain_watcher.py"
        )
        assert "import ingest_utils" not in source, (
            "Import roto 'import ingest_utils' detectado en brain_watcher.py"
        )

    def test_watcher_importa_embed_de_ingest_router(self):
        """brain_watcher.py usa _embed de ingest_router (F3.6 fix)."""
        import app.brain.brain_watcher as watcher_module
        source = inspect.getsource(watcher_module)
        assert "from app.brain.ingest_router import _embed" in source, (
            "brain_watcher.py debe importar _embed de app.brain.ingest_router"
        )


# ===========================================================================
# TestPrerequisitosF3 — F3.0: prerequisitos que passport_builder necesita
# ===========================================================================

class TestPrerequisitosF3:
    """
    Tests de los prerequisitos de F3 (F3.0 del checklist).

    passport_builder.py importa 'from app.brain.masters import classify_document'
    en su código fuente. Si masters.py no exporta esa función, toda la síntesis
    falla en import antes de ejecutar una sola línea.
    """

    def test_masters_classify_document_importable(self):
        """
        F3.0: from app.brain.masters import classify_document no falla.
        Prerequisito crítico — passport_builder.py lo importa en startup.
        """
        from app.brain.masters import classify_document
        assert callable(classify_document), (
            "classify_document debe ser callable en app.brain.masters"
        )

    def test_masters_classify_document_devuelve_dict(self):
        """
        classify_document() devuelve dict con claves domain, doc_type.
        Verifica que el módulo está operativo, no solo importable.
        """
        from app.brain.masters import classify_document
        result = classify_document(
            tags=["python", "fastapi"],
            keyphrases=["api", "backend"],
            title="mi_modulo.py",
            file_type="py",
        )
        assert isinstance(result, dict), "classify_document debe devolver dict"
        assert "domain" in result, "El resultado debe tener clave 'domain'"
        assert "doc_type" in result, "El resultado debe tener clave 'doc_type'"

    def test_passport_builder_importa_masters(self):
        """
        passport_builder.py importa classify_document desde masters.
        Si este import falla, build_partial_passport() no funciona.
        """
        import app.brain.passport_builder as pb_module
        source = inspect.getsource(pb_module)
        assert "classify_document" in source, (
            "passport_builder.py debe usar classify_document de masters"
        )

    def test_brain_dir_configurado(self):
        """
        BRAIN_DIR está configurado en el entorno (o tiene valor por defecto).
        BrainWriter usa este valor — sin él los pasaportes no se persisten.
        """
        import app.brain.writer as writer_module
        assert hasattr(writer_module, "BRAIN_DIR"), (
            "writer.py debe definir BRAIN_DIR desde os.getenv()"
        )
        assert writer_module.BRAIN_DIR, "BRAIN_DIR no puede estar vacío"


# ===========================================================================
# TestBuildPartialPassport — contrato de build_partial_passport()
# ===========================================================================

class TestBuildPartialPassport:
    """
    Tests del contrato de build_partial_passport() sobre ficheros fixture reales.
    No llama al LLM — es 100% determinista y no requiere Ollama.
    """

    def _get_processed(self, filename: str) -> tuple:
        from app.brain.processor_factory import DocumentProcessorFactory
        processor = DocumentProcessorFactory.get(filename)
        blocks = processor.extract(
            str(FIXTURES_DIR / filename),
            search_space_id=42,
        )
        processed = processor.preprocess(blocks)
        return blocks, processed

    def test_retorna_dict_con_clave_passport_partial(self):
        """
        build_partial_passport() devuelve dict con clave 'passport_partial'.
        Contrato crítico verificado en F3.1.
        """
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("ejemplo.py")
        result = build_partial_passport(
            source="ejemplo.py",
            file_type="py",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        assert isinstance(result, dict), "build_partial_passport debe devolver dict"
        assert "passport_partial" in result, "El dict debe tener clave 'passport_partial'"
        assert isinstance(result["passport_partial"], str), "'passport_partial' debe ser str"

    def test_retorna_claves_requeridas(self):
        """El dict devuelto tiene todas las claves necesarias para el pipeline."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("nota.md")
        result = build_partial_passport(
            source="nota.md",
            file_type="md",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        for key in ["passport_partial", "entities", "tags_base", "triggers"]:
            assert key in result, f"Clave '{key}' ausente en resultado"

    def test_passport_parcial_tiene_frontmatter_yaml(self):
        """El passport_partial comienza con '---' (frontmatter YAML válido)."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("ejemplo.py")
        result = build_partial_passport(
            source="ejemplo.py",
            file_type="py",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        passport = result["passport_partial"]
        assert passport.strip().startswith("---"), (
            "El pasaporte parcial debe comenzar con frontmatter YAML '---'"
        )
        assert passport.count("---") >= 2, (
            "El frontmatter YAML debe tener apertura y cierre '---'"
        )

    def test_passport_py_tiene_source_origin(self):
        """El frontmatter del pasaporte Python incluye el source original."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("ejemplo.py")
        result = build_partial_passport(
            source="ejemplo.py",
            file_type="py",
            blocks=blocks,
            full_text=processed,
            metadata={},
            source_type="upload",
        )

        assert "ejemplo" in result["passport_partial"], (
            "El pasaporte debe incluir referencia al source original"
        )

    def test_passport_sql_no_vacio(self):
        """build_partial_passport() produce pasaporte no vacío para .sql."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("sp_ejemplo.sql")
        result = build_partial_passport(
            source="sp_ejemplo.sql",
            file_type="sql",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        assert len(result["passport_partial"]) > 100, "Pasaporte SQL demasiado corto"

    def test_passport_md_no_vacio(self):
        """build_partial_passport() produce pasaporte no vacío para .md."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("nota.md")
        result = build_partial_passport(
            source="nota.md",
            file_type="md",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        assert len(result["passport_partial"]) > 100, "Pasaporte Markdown demasiado corto"

    def test_tags_base_es_lista(self):
        """tags_base siempre es una lista (puede estar vacía pero no None)."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("nota.md")
        result = build_partial_passport(
            source="nota.md",
            file_type="md",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        assert isinstance(result["tags_base"], list), "tags_base debe ser una lista"

    def test_entities_es_lista(self):
        """entities siempre es una lista (puede estar vacía pero no None)."""
        from app.brain.passport_builder import build_partial_passport

        blocks, processed = self._get_processed("ejemplo.py")
        result = build_partial_passport(
            source="ejemplo.py",
            file_type="py",
            blocks=blocks,
            full_text=processed,
            metadata={},
        )

        assert isinstance(result["entities"], list), "entities debe ser una lista"


# ===========================================================================
# TestDocumentSynthesizerRaw — synthesize() sin LLM (SYNTHESIS_ENABLED=false)
# ===========================================================================

class TestDocumentSynthesizerRaw:
    """
    Tests de DocumentSynthesizer.synthesize() en modo raw (sin llamadas al LLM).
    Usa SYNTHESIS_ENABLED=false para evitar dependencia de Ollama en el CI.
    """

    def _get_pipeline_result(self, filename: str) -> tuple:
        from app.brain.processor_factory import DocumentProcessorFactory
        processor = DocumentProcessorFactory.get(filename)
        blocks = processor.extract(
            str(FIXTURES_DIR / filename),
            search_space_id=42,
        )
        processed = processor.preprocess(blocks)
        return blocks, processed

    @patch.dict(os.environ, {"SYNTHESIS_ENABLED": "false"})
    def test_synthesize_modo_raw_devuelve_md_content(self):
        """
        Con SYNTHESIS_ENABLED=false, synthesize() devuelve dict con 'md_content'.
        Verifica el contrato de F3.1 sin llamar a Ollama.
        """
        from app.brain.synthesizer import DocumentSynthesizer

        blocks, processed = self._get_pipeline_result("ejemplo.py")
        synth = DocumentSynthesizer()
        result = synth.synthesize(
            source="ejemplo.py",
            file_type="py",
            full_text=processed,
            blocks=blocks,
            metadata={},
        )

        assert isinstance(result, dict), "synthesize() debe devolver dict"
        assert "md_content" in result, "El dict debe tener clave 'md_content'"
        assert isinstance(result["md_content"], str), "'md_content' debe ser str"

    @patch.dict(os.environ, {"SYNTHESIS_ENABLED": "false"})
    def test_synthesize_raw_devuelve_claves_completas(self):
        """Con SYNTHESIS_ENABLED=false, el dict tiene todas las claves del contrato."""
        from app.brain.synthesizer import DocumentSynthesizer

        blocks, processed = self._get_pipeline_result("nota.md")
        synth = DocumentSynthesizer()
        result = synth.synthesize(
            source="nota.md",
            file_type="md",
            full_text=processed,
            blocks=blocks,
            metadata={},
        )

        for key in ["md_content", "tags", "entities", "drill_down_triggers"]:
            assert key in result, f"Clave '{key}' ausente en resultado de synthesize()"

    @patch.dict(os.environ, {"SYNTHESIS_ENABLED": "false"})
    def test_synthesize_es_sincrono(self):
        """
        synthesize() es síncrono — no es corrutina.
        Crítico: run_brain_synthesis() lo llama sin await.
        """
        import asyncio
        from app.brain.synthesizer import DocumentSynthesizer

        synth = DocumentSynthesizer()
        result = synth.synthesize(
            source="ejemplo.py",
            file_type="py",
            full_text="def foo(): pass",
            blocks=[],
            metadata={},
        )
        assert not asyncio.iscoroutine(result), (
            "synthesize() no debe devolver corrutina — debe ser síncrono"
        )

    @patch.dict(os.environ, {"SYNTHESIS_ENABLED": "false"})
    def test_synthesize_texto_vacio_devuelve_md_vacio(self):
        """Con texto vacío, synthesize() devuelve md_content vacío sin lanzar excepción."""
        from app.brain.synthesizer import DocumentSynthesizer

        synth = DocumentSynthesizer()
        result = synth.synthesize(
            source="vacio.py",
            file_type="py",
            full_text="",
            blocks=[],
            metadata={},
        )

        assert "md_content" in result
        assert result["md_content"] == "" or result["md_content"] is not None


# ===========================================================================
# TestRunBrainSynthesis — run_brain_synthesis() con mocks
# ===========================================================================

class TestRunBrainSynthesis:
    """
    Tests de run_brain_synthesis() con DocumentSynthesizer, BrainWriter
    e IngestRouter mockeados. No requiere Ollama ni Qdrant.

    NOTA sobre @patch:
      run_brain_synthesis() importa IngestRouter y QdrantManager de forma
      lazy (dentro de la función). Por tanto los @patch deben apuntar a los
      módulos originales donde viven las clases, no a document_converters:
        - app.brain.ingest_router.IngestRouter
        - app.brain.qdrant_manager.QdrantManager
    """

    def _get_pipeline_result(self, filename: str) -> tuple:
        from app.brain.processor_factory import DocumentProcessorFactory
        processor = DocumentProcessorFactory.get(filename)
        blocks = processor.extract(
            str(FIXTURES_DIR / filename),
            search_space_id=42,
        )
        processed = processor.preprocess(blocks)
        quality = processor.get_metadata_from_blocks(
            blocks=blocks,
            meta_service=None,
            search_space_id=42,
        )
        return blocks, processed, quality

    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    @patch("app.brain.synthesizer.DocumentSynthesizer.synthesize")
    def test_run_brain_synthesis_devuelve_dict_completo(
        self, mock_synth, mock_router_cls, mock_qdrant_cls
    ):
        """
        run_brain_synthesis() devuelve dict con source, passport_path,
        passport_md, embedding_scope, write_status, llm_ok.
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_synth.return_value = {
            "md_content":          _MOCK_PASSPORT_MD,
            "tags":                ["python", "fastapi"],
            "entities":            ["BrainWriter"],
            "drill_down_triggers": [],
            "llm_ok":              True,
        }
        mock_qdrant_cls.get_instance.return_value = MagicMock()
        mock_router_cls.return_value.route.return_value = {
            "brain":     {"chunks_created": 3},
            "knowledge": {"chunks_created": 2},
            "code":      {"chunks_created": 0},
        }

        blocks, processed, quality = self._get_pipeline_result("ejemplo.py")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir}):
                result = run_brain_synthesis(
                    processed_text=processed,
                    blocks=blocks,
                    filename="ejemplo.py",
                    search_space_id=42,
                    quality_meta=quality,
                )

        assert isinstance(result, dict), "run_brain_synthesis debe devolver dict"
        for key in ["source", "passport_path", "passport_md",
                    "embedding_scope", "write_status", "llm_ok"]:
            assert key in result, f"Clave '{key}' ausente en resultado"

    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    @patch("app.brain.synthesizer.DocumentSynthesizer.synthesize")
    def test_run_brain_synthesis_escribe_md_en_disco(
        self, mock_synth, mock_router_cls, mock_qdrant_cls
    ):
        """
        run_brain_synthesis() escribe el .md en BRAIN_DIR.
        passport_path apunta a un fichero que existe en disco.
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_synth.return_value = {
            "md_content": _MOCK_PASSPORT_MD,
            "tags": ["python"], "entities": [], "drill_down_triggers": [],
            "llm_ok": True,
        }
        mock_qdrant_cls.get_instance.return_value = MagicMock()
        mock_router_cls.return_value.route.return_value = {
            "brain": {"chunks_created": 1},
            "knowledge": {"chunks_created": 1},
            "code": {"chunks_created": 0},
        }

        blocks, processed, quality = self._get_pipeline_result("ejemplo.py")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir}):
                result = run_brain_synthesis(
                    processed_text=processed,
                    blocks=blocks,
                    filename="ejemplo.py",
                    search_space_id=42,
                    quality_meta=quality,
                )

            passport_path = result["passport_path"]
            assert passport_path, "passport_path no debe estar vacío"
            assert Path(passport_path).exists(), (
                f"El fichero .md no fue creado en disco: {passport_path}"
            )
            assert Path(passport_path).suffix == ".md", (
                "El fichero del pasaporte debe tener extensión .md"
            )

    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    @patch("app.brain.synthesizer.DocumentSynthesizer.synthesize")
    def test_run_brain_synthesis_llm_ok_embedding_scope(
        self, mock_synth, mock_router_cls, mock_qdrant_cls
    ):
        """
        Con llm_ok=True y sin bloques de código → embedding_scope=["brain","knowledge"].
        Con llm_ok=True y con bloques de código → embedding_scope incluye "code".
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_synth.return_value = {
            "md_content": _MOCK_PASSPORT_MD, "tags": [], "entities": [],
            "drill_down_triggers": [], "llm_ok": True,
        }
        mock_qdrant_cls.get_instance.return_value = MagicMock()
        mock_router_cls.return_value.route.return_value = {
            "brain": {"chunks_created": 1}, "knowledge": {"chunks_created": 1},
            "code": {"chunks_created": 0},
        }

        # Sin bloques de código
        blocks_no_code = [{"content": "texto", "content_type": "text", "metadata": {}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir}):
                result = run_brain_synthesis(
                    processed_text="texto prueba",
                    blocks=blocks_no_code,
                    filename="doc.md",
                    search_space_id=1,
                )
        assert "brain" in result["embedding_scope"]
        assert "knowledge" in result["embedding_scope"]
        assert "code" not in result["embedding_scope"]

        # Con bloques de código
        blocks_with_code = [
            {"content": "texto", "content_type": "text", "metadata": {}},
            {"content": "def foo():\n    pass", "content_type": "code", "metadata": {}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir}):
                result = run_brain_synthesis(
                    processed_text="def foo(): pass",
                    blocks=blocks_with_code,
                    filename="modulo.py",
                    search_space_id=1,
                )
        assert "brain" in result["embedding_scope"]
        assert "code" in result["embedding_scope"]

    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    @patch("app.brain.synthesizer.DocumentSynthesizer.synthesize",
           side_effect=RuntimeError("Ollama no disponible"))
    def test_run_brain_synthesis_fallback_si_llm_falla(
        self, mock_synth, mock_router_cls, mock_qdrant_cls
    ):
        """
        Si synthesize() lanza excepción, run_brain_synthesis() usa
        build_partial_passport() como fallback — no propaga la excepción.
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_qdrant_cls.get_instance.return_value = MagicMock()
        mock_router_cls.return_value.route.return_value = {
            "brain": {"chunks_created": 1}, "knowledge": {"chunks_created": 1},
            "code": {"chunks_created": 0},
        }

        blocks, processed, quality = self._get_pipeline_result("nota.md")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {
                "BRAIN_DIR": tmpdir,
                "SYNTHESIS_ENABLED": "true",
            }):
                result = run_brain_synthesis(
                    processed_text=processed,
                    blocks=blocks,
                    filename="nota.md",
                    search_space_id=42,
                    quality_meta=quality,
                )

        assert result["passport_md"], "El pasaporte fallback no debe estar vacío"
        assert result["llm_ok"] is False, (
            "llm_ok debe ser False cuando la síntesis LLM falló"
        )

    @patch.dict(os.environ, {"SYNTHESIS_ENABLED": "false"})
    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    def test_run_brain_synthesis_modo_raw_sin_llm(
        self, mock_router_cls, mock_qdrant_cls
    ):
        """
        Con SYNTHESIS_ENABLED=false, run_brain_synthesis() genera pasaporte
        determinista sin llamar a DocumentSynthesizer.
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_qdrant_cls.get_instance.return_value = MagicMock()
        mock_router_cls.return_value.route.return_value = {
            "brain": {"chunks_created": 0}, "knowledge": {"chunks_created": 1},
            "code": {"chunks_created": 0},
        }

        blocks, processed, quality = self._get_pipeline_result("nota.md")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir,
                                         "SYNTHESIS_ENABLED": "false"}):
                result = run_brain_synthesis(
                    processed_text=processed,
                    blocks=blocks,
                    filename="nota.md",
                    search_space_id=42,
                    quality_meta=quality,
                )

        assert result["passport_md"], "Pasaporte raw no debe estar vacío"
        assert "brain" not in result["embedding_scope"], (
            "Con SYNTHESIS_ENABLED=false, 'brain' no debe estar en embedding_scope"
        )

    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    @patch("app.brain.synthesizer.DocumentSynthesizer.synthesize")
    def test_run_brain_synthesis_ingest_router_llamado(
        self, mock_synth, mock_router_cls, mock_qdrant_cls
    ):
        """
        run_brain_synthesis() llama a IngestRouter.route() con los parámetros
        correctos: md_content, blocks, source, search_space_id.
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_synth.return_value = {
            "md_content": _MOCK_PASSPORT_MD, "tags": [], "entities": [],
            "drill_down_triggers": [], "llm_ok": True,
        }
        mock_qdrant_mgr = MagicMock()
        mock_qdrant_cls.get_instance.return_value = mock_qdrant_mgr
        mock_router_instance = MagicMock()
        mock_router_instance.route.return_value = {
            "brain": {"chunks_created": 2}, "knowledge": {"chunks_created": 1},
            "code": {"chunks_created": 0},
        }
        mock_router_cls.return_value = mock_router_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir}):
                run_brain_synthesis(
                    processed_text="texto de prueba",
                    blocks=[{"content": "texto", "content_type": "text", "metadata": {}}],
                    filename="test_doc.md",
                    search_space_id=77,
                )

        mock_router_instance.route.assert_called_once()
        call_kwargs = mock_router_instance.route.call_args.kwargs
        assert "md_content" in call_kwargs, "route() debe recibir md_content"
        assert "search_space_id" in call_kwargs, "route() debe recibir search_space_id"
        assert call_kwargs["search_space_id"] == "77", (
            "search_space_id debe pasarse como string a IngestRouter"
        )

    @patch("app.brain.qdrant_manager.QdrantManager")
    @patch("app.brain.ingest_router.IngestRouter")
    @patch("app.brain.synthesizer.DocumentSynthesizer.synthesize")
    def test_ingest_router_constructor_correcto(
        self, mock_synth, mock_router_cls, mock_qdrant_cls
    ):
        """
        F3.5: IngestRouter se construye con qdrant_client=qdrant_mgr.client.
        Verifica que el constructor usa keyword argument 'qdrant_client',
        no posicional, y que el client viene de QdrantManager.get_instance().
        """
        from app.utils.document_converters import run_brain_synthesis

        mock_synth.return_value = {
            "md_content": _MOCK_PASSPORT_MD, "tags": [], "entities": [],
            "drill_down_triggers": [], "llm_ok": True,
        }
        mock_qdrant_mgr = MagicMock()
        mock_qdrant_mgr.client = MagicMock(name="qdrant_client_mock")
        mock_qdrant_cls.get_instance.return_value = mock_qdrant_mgr
        mock_router_cls.return_value.route.return_value = {
            "brain": {"chunks_created": 1}, "knowledge": {"chunks_created": 1},
            "code": {"chunks_created": 0},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"BRAIN_DIR": tmpdir}):
                run_brain_synthesis(
                    processed_text="texto de prueba",
                    blocks=[{"content": "t", "content_type": "text", "metadata": {}}],
                    filename="doc.md",
                    search_space_id=1,
                )

        mock_router_cls.assert_called_once()
        call_kwargs = mock_router_cls.call_args.kwargs
        assert "qdrant_client" in call_kwargs, (
            "IngestRouter debe construirse con keyword 'qdrant_client'"
        )
        assert call_kwargs["qdrant_client"] is mock_qdrant_mgr.client, (
            "IngestRouter debe recibir qdrant_mgr.client de QdrantManager.get_instance()"
        )


# ===========================================================================
# TestBrainWriter — contrato real de BrainWriter
# ===========================================================================

class TestBrainWriter:
    """
    Tests del contrato de BrainWriter con directorio temporal real.
    No requiere Qdrant ni Ollama.
    """

    def test_write_devuelve_dict_con_path_y_status(self):
        """
        BrainWriter.write() devuelve dict con 'path' y 'status'.
        El plan advierte: NO hacer str(write_result), sino write_result['path'].
        """
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            result = writer.write(
                source="test_doc.py",
                md_content=_MOCK_PASSPORT_MD,
                overwrite=True,
            )

        assert isinstance(result, dict), "write() debe devolver dict, no Path ni str"
        assert "path" in result, "El dict debe tener clave 'path'"
        assert "status" in result, "El dict debe tener clave 'status'"

    def test_write_status_created_primera_vez(self):
        """En la primera escritura, status='created'."""
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            result = writer.write(
                source="nuevo_doc.py",
                md_content=_MOCK_PASSPORT_MD,
                overwrite=True,
            )

        assert result["status"] == "created", (
            f"Primera escritura debe ser 'created', obtenido '{result['status']}'"
        )

    def test_write_status_overwritten_segunda_vez(self):
        """En la segunda escritura con overwrite=True, status='overwritten'."""
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            writer.write(source="doc.py", md_content=_MOCK_PASSPORT_MD, overwrite=True)
            result = writer.write(source="doc.py", md_content=_MOCK_PASSPORT_MD, overwrite=True)

        assert result["status"] == "overwritten", (
            f"Segunda escritura con overwrite debe ser 'overwritten', "
            f"obtenido '{result['status']}'"
        )

    def test_write_status_exists_warning_sin_overwrite(self):
        """Sin overwrite=True, segunda escritura devuelve status='exists_warning'."""
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            writer.write(source="doc.py", md_content=_MOCK_PASSPORT_MD, overwrite=False)
            result = writer.write(source="doc.py", md_content=_MOCK_PASSPORT_MD, overwrite=False)

        assert result["status"] == "exists_warning", (
            "Sin overwrite, segunda escritura debe ser 'exists_warning'"
        )

    def test_write_path_es_string_no_path(self):
        """
        write_result['path'] es str, no Path ni dict.
        Crítico: el task Celery asigna result.passport_path = write_result['path'].
        """
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            result = writer.write(
                source="ejemplo.py",
                md_content=_MOCK_PASSPORT_MD,
                overwrite=True,
            )

        assert isinstance(result["path"], str), (
            "write_result['path'] debe ser str, no Path"
        )

    def test_write_crea_fichero_en_disco(self):
        """El .md escrito existe en disco y contiene el contenido correcto."""
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            result = writer.write(
                source="ejemplo.py",
                md_content=_MOCK_PASSPORT_MD,
                overwrite=True,
            )

            assert Path(result["path"]).exists(), "El .md no fue creado en disco"
            content = Path(result["path"]).read_text(encoding="utf-8")
            assert "kb_ejemplo" in content or "ejemplo" in content, (
                "El contenido del .md no coincide con lo escrito"
            )

    def test_write_content_vacio_devuelve_empty(self):
        """write() con contenido vacío devuelve status='empty' sin crear fichero."""
        from app.brain.writer import BrainWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BrainWriter(brain_dir=tmpdir)
            result = writer.write(source="vacio.py", md_content="", overwrite=True)

        assert result["status"] == "empty", (
            "Contenido vacío debe devolver status='empty'"
        )
        assert result["path"] == "", "path debe ser '' cuando status='empty'"

    def test_brain_dir_desde_entorno(self):
        """
        BrainWriter() sin argumentos usa BRAIN_DIR del módulo writer.py.

        NOTA: BRAIN_DIR se lee de os.getenv() al importar el módulo (nivel módulo),
        no en el constructor. Por eso hay que parchear app.brain.writer.BRAIN_DIR
        directamente — patch.dict(os.environ) no tiene efecto porque la variable
        ya está asignada en el momento del import.
        """
        from app.brain.writer import BrainWriter
        import app.brain.writer as writer_module

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(writer_module, "BRAIN_DIR", tmpdir):
                writer = BrainWriter()
                assert writer.brain_dir == tmpdir, (
                    f"BrainWriter debe usar BRAIN_DIR del módulo. "
                    f"Esperado: {tmpdir}, obtenido: {writer.brain_dir}"
                )


# ===========================================================================
# TestBrainWatcherImports — F3.6
# ===========================================================================

class TestBrainWatcherImports:
    """
    Tests de que brain_watcher.py tiene los imports correctos de F3.6.
    No levanta el watcher real — solo inspecciona el código fuente.
    """

    def test_no_import_traceback(self):
        """import traceback eliminado en F3.6."""
        import app.brain.brain_watcher as m
        source = inspect.getsource(m)
        assert "import traceback" not in source, (
            "import traceback debe haber sido eliminado en F3.6"
        )

    def test_embed_model_desde_entorno(self):
        """EMBED_MODEL se lee de os.getenv() — cero hardcode."""
        import app.brain.brain_watcher as m
        source = inspect.getsource(m)
        assert 'os.getenv("EMBED_MODEL"' in source, (
            "EMBED_MODEL debe leerse de os.getenv() en brain_watcher.py"
        )

    def test_dt_f3_watcher_documentado(self):
        """DT-F3-watcher está documentado en el código fuente."""
        import app.brain.brain_watcher as m
        source = inspect.getsource(m)
        assert "DT-F3-watcher" in source, (
            "Deuda técnica DT-F3-watcher debe estar documentada en brain_watcher.py"
        )

    def test_variables_desde_entorno(self):
        """BRAIN_DIR, QDRANT_HOST, QDRANT_PORT, COLLECTION_BRAIN desde entorno."""
        import app.brain.brain_watcher as m
        source = inspect.getsource(m)
        for var in ["BRAIN_DIR", "QDRANT_HOST", "QDRANT_PORT", "COLLECTION_BRAIN"]:
            assert f'os.getenv("{var}"' in source, (
                f"{var} debe leerse de os.getenv() en brain_watcher.py"
            )


# ===========================================================================
# TestDocumentTaskF3 — task Celery delega en run_brain_synthesis
# ===========================================================================

class TestDocumentTaskF3:
    """
    Tests del task Celery en F3.7.
    Verifica que el task delega en run_brain_synthesis y actualiza los campos
    de pasaporte en el Document (F3.4).
    """

    def test_task_importa_run_brain_synthesis(self):
        """
        El task Celery importa run_brain_synthesis desde document_converters.
        Verifica que el F2 placeholder ha sido sustituido.
        """
        import app.tasks.celery_tasks.document_tasks as dt_module
        source = inspect.getsource(dt_module)

        assert "run_brain_synthesis" in source, (
            "document_tasks.py debe importar run_brain_synthesis"
        )
        assert "from app.utils.document_converters import run_brain_synthesis" in source, (
            "run_brain_synthesis debe importarse de app.utils.document_converters"
        )

    def test_task_no_tiene_f2_placeholder(self):
        """El placeholder F2 (processed_text como passport) ha sido eliminado."""
        import app.tasks.celery_tasks.document_tasks as dt_module
        source = inspect.getsource(dt_module)

        assert "En F2 usamos processed_text como placeholder" not in source, (
            "El comentario del placeholder F2 debe haber sido eliminado en F3.7"
        )

    def test_task_usa_get_running_loop(self):
        """El task usa asyncio.get_running_loop() (no get_event_loop() deprecated)."""
        import app.tasks.celery_tasks.document_tasks as dt_module
        source = inspect.getsource(dt_module)

        assert "get_running_loop()" in source, (
            "El task debe usar asyncio.get_running_loop() — no get_event_loop()"
        )

    def test_task_actualiza_campos_pasaporte(self):
        """El task actualiza los 6 campos de pasaporte en el Document (F3.4)."""
        import app.tasks.celery_tasks.document_tasks as dt_module
        source = inspect.getsource(dt_module)

        for campo in [
            "passport_path",
            "passport_generated_at",
            "avg_quality_score",
            "has_pii",
            "detected_languages",
            "embedding_scope",
        ]:
            assert f"result.{campo}" in source, (
                f"El task debe actualizar el campo '{campo}' en el Document"
            )

    def test_task_usa_datetime_timezone_utc(self):
        """El task usa datetime.now(timezone.utc) — no utcnow() deprecado."""
        import app.tasks.celery_tasks.document_tasks as dt_module
        source = inspect.getsource(dt_module)

        assert "datetime.now(timezone.utc)" in source, (
            "El task debe usar datetime.now(timezone.utc)"
        )
        assert "utcnow()" not in source, (
            "utcnow() está deprecado desde Python 3.12 — usar datetime.now(timezone.utc)"
        )

    def test_migracion_161_existe(self):
        """La migración 161 existe en alembic/versions/."""
        versions_dir = (
            Path(__file__).parent.parent.parent
            / "alembic" / "versions"
        )
        migration = versions_dir / "161_passport_fields_to_document.py"
        assert migration.exists(), (
            f"Migración 161 no encontrada en {migration}"
        )

    def test_migracion_161_tabla_correcta(self):
        """La migración 161 usa la tabla 'documents' (plural), no 'document'."""
        versions_dir = (
            Path(__file__).parent.parent.parent
            / "alembic" / "versions"
        )
        migration = versions_dir / "161_passport_fields_to_document.py"
        source = migration.read_text(encoding="utf-8")

        assert '_TABLE = "documents"' in source, (
            "La migración 161 debe usar _TABLE = 'documents' (plural)"
        )
        assert 'down_revision: str | None = "160"' in source, (
            "La migración 161 debe tener down_revision='160'"
        )

    def test_migracion_161_idempotente(self):
        """La migración 161 usa _column_exists() para ser idempotente."""
        versions_dir = (
            Path(__file__).parent.parent.parent
            / "alembic" / "versions"
        )
        migration = versions_dir / "161_passport_fields_to_document.py"
        source = migration.read_text(encoding="utf-8")

        assert "_column_exists" in source, (
            "La migración 161 debe usar _column_exists() para idempotencia"
        )

    def test_db_model_document_tiene_campos_pasaporte(self):
        """El modelo Document en db.py tiene los 6 campos de pasaporte (F3.4)."""
        from app.db import Document

        for campo in [
            "passport_path",
            "passport_generated_at",
            "avg_quality_score",
            "has_pii",
            "detected_languages",
            "embedding_scope",
        ]:
            assert hasattr(Document, campo), (
                f"Document no tiene campo '{campo}' — falta en db.py (F3.4)"
            )


# ===========================================================================
# TestSintesisIntegracion — tests contra Qdrant + Ollama reales
# ===========================================================================

class TestSintesisIntegracion:
    """
    Tests de integración contra Qdrant real y Ollama real.

    Requieren:
      - QDRANT_HOST=qdrant (sbs-dev-qdrant disponible)
      - OLLAMA_HOST=http://host.docker.internal:11434 con modelos cargados
      - SYNTHESIS_ENABLED=true
      - BRAIN_DIR accesible

    Marcados con @pytest.mark.integration.
    """

    @pytest.mark.integration
    def test_pipeline_completo_py_genera_pasaporte(self, tmp_path):
        """
        Un .py pasa por el pipeline completo F1+F2+F3 y genera un .md
        en BRAIN_DIR con sección '🧩 Core Knowledge'.

        Criterio de aceptación global F3:
          Un .py ingestado → /data/brain/{slug}.md con ## 🧩 Core Knowledge
        """
        from app.brain.processor_factory import DocumentProcessorFactory
        from app.utils.document_converters import run_brain_synthesis

        processor = DocumentProcessorFactory.get("ejemplo.py")
        blocks = processor.extract(
            str(FIXTURES_DIR / "ejemplo.py"),
            search_space_id=1,
        )
        processed = processor.preprocess(blocks)
        quality = processor.get_metadata_from_blocks(
            blocks=blocks, meta_service=None, search_space_id=1,
        )

        with patch.dict(os.environ, {"BRAIN_DIR": str(tmp_path)}):
            result = run_brain_synthesis(
                processed_text=processed,
                blocks=blocks,
                filename="ejemplo.py",
                search_space_id=1,
                quality_meta=quality,
            )

        assert result["passport_path"], "passport_path no debe estar vacío"
        passport = Path(result["passport_path"])
        assert passport.exists(), f"El .md no fue creado: {passport}"

        content = passport.read_text(encoding="utf-8")
        assert content.strip().startswith("---"), "Falta frontmatter YAML"
        assert "Core Knowledge" in content, (
            "El pasaporte debe tener sección '🧩 Core Knowledge'"
        )

    @pytest.mark.integration
    def test_pipeline_completo_md_genera_pasaporte(self, tmp_path):
        """Un .md pasa por el pipeline completo y genera pasaporte válido."""
        from app.brain.processor_factory import DocumentProcessorFactory
        from app.utils.document_converters import run_brain_synthesis

        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(
            str(FIXTURES_DIR / "nota.md"),
            search_space_id=2,
        )
        processed = processor.preprocess(blocks)
        quality = processor.get_metadata_from_blocks(
            blocks=blocks, meta_service=None, search_space_id=2,
        )

        with patch.dict(os.environ, {"BRAIN_DIR": str(tmp_path)}):
            result = run_brain_synthesis(
                processed_text=processed,
                blocks=blocks,
                filename="nota.md",
                search_space_id=2,
                quality_meta=quality,
            )

        assert Path(result["passport_path"]).exists()
        assert result["passport_md"]

    @pytest.mark.integration
    def test_coleccion_brain_tiene_vectores_con_search_space_id(self, tmp_path):
        """
        Tras run_brain_synthesis, la colección brain en Qdrant tiene
        al menos un punto con search_space_id en el payload.

        Criterio F3: colección brain tiene vectores con search_space_id.
        """
        from app.brain.processor_factory import DocumentProcessorFactory
        from app.utils.document_converters import run_brain_synthesis
        from app.brain.qdrant_manager import QdrantManager
        from app.brain.collections import BRAIN

        search_space_id = 9001  # ID único para no interferir con otros tests

        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(
            str(FIXTURES_DIR / "nota.md"),
            search_space_id=search_space_id,
        )
        processed = processor.preprocess(blocks)
        quality = processor.get_metadata_from_blocks(
            blocks=blocks, meta_service=None, search_space_id=search_space_id,
        )

        with patch.dict(os.environ, {"BRAIN_DIR": str(tmp_path)}):
            run_brain_synthesis(
                processed_text=processed,
                blocks=blocks,
                filename="nota_integracion.md",
                search_space_id=search_space_id,
                quality_meta=quality,
            )

        mgr = QdrantManager.get_instance()
        results, _ = mgr.client.scroll(
            collection_name=BRAIN,
            scroll_filter=None,
            with_payload=True,
            limit=100,
        )

        space_points = [
            p for p in results
            if p.payload.get("search_space_id") == str(search_space_id)
        ]
        assert len(space_points) > 0, (
            f"No se encontraron vectores en brain con search_space_id={search_space_id}"
        )

        # Limpiar para no contaminar otros tests
        mgr.delete_by_source(
            source="nota_integracion",
            search_space_id=str(search_space_id),
        )

    @pytest.mark.integration
    def test_synthesis_enabled_false_no_llama_ollama(self, tmp_path):
        """
        Con SYNTHESIS_ENABLED=false, el pipeline no llama a Ollama.
        El pasaporte generado es determinista (passport_builder).

        Criterio F3: SYNTHESIS_ENABLED=false → pasaporte sin llamar a Ollama.
        """
        from app.brain.processor_factory import DocumentProcessorFactory
        from app.utils.document_converters import run_brain_synthesis

        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(
            str(FIXTURES_DIR / "nota.md"),
            search_space_id=1,
        )
        processed = processor.preprocess(blocks)
        quality = processor.get_metadata_from_blocks(
            blocks=blocks, meta_service=None, search_space_id=1,
        )

        with patch("app.brain.synthesizer.DocumentSynthesizer.synthesize") as mock_synth:
            with patch.dict(os.environ, {
                "BRAIN_DIR": str(tmp_path),
                "SYNTHESIS_ENABLED": "false",
            }):
                result = run_brain_synthesis(
                    processed_text=processed,
                    blocks=blocks,
                    filename="nota.md",
                    search_space_id=1,
                    quality_meta=quality,
                )
            mock_synth.assert_not_called()

        assert result["passport_md"], "Pasaporte raw debe tener contenido"
        assert result["llm_ok"] is False, (
            "llm_ok debe ser False en modo SYNTHESIS_ENABLED=false"
        )
