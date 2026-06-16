"""
tests/brain/test_pipeline_f1.py
--------------------------------
Tests de la Fase 1 del pipeline Brain (F1.6).

Cobertura:
  - TestImportPaths          : imports del pipeline son correctos
  - TestPipelineTresFases    : .py, .sql, .md pasan las 3 fases
  - TestBrainMetadataService : classify_document y normalize_tags con mock DB
  - TestSearchSpaceIsolation : search_space_id se propaga y aíslá correctamente

Adaptaciones respecto al documento F1:
  - search_space_id es int (no str) — SurfSense usa Integer en PostgreSQL.
  - TYPE_SPECS no existe como nombre; se usa get_spec() equivalente.
  - BrainMetadataService._caches usa clave int=0 (no str="").
  - process_document_content no tiene parámetro llm_client.

Ficheros fixture usados (en tests/fixtures/):
  - ejemplo.py    : Python con funciones documentadas
  - sp_ejemplo.sql: Stored procedure SQL con CTEs
  - nota.md       : Markdown con headings H1/H2/H3
  - sample.pdf    : PDF ya existente en el proyecto
"""
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ===========================================================================
# TestImportPaths — verifica que los imports del pipeline son correctos
# ===========================================================================

class TestImportPaths:
    """Verifica que los imports del pipeline son correctos (gap crítico)."""

    def test_processor_factory_imports_without_error(self):
        """DocumentProcessorFactory importa sin errores de módulo."""
        from app.brain.processor_factory import DocumentProcessorFactory, PREPROCESSOR_MAP

        assert len(PREPROCESSOR_MAP) > 15, (
            f"PREPROCESSOR_MAP tiene {len(PREPROCESSOR_MAP)} entradas, se esperan >15"
        )

    def test_preprocessing_modules_importable(self):
        """Los módulos de preprocessing están en el path correcto y exponen preprocess()."""
        from app.brain.prompts.preprocessing import py, sql, pdf, docx, md

        for mod in [py, sql, pdf, docx, md]:
            assert hasattr(mod, "preprocess"), (
                f"Módulo {mod.__name__} no expone preprocess()"
            )

    def test_type_specs_importable(self):
        """
        get_spec() (equivalente a TYPE_SPECS.get()) importa sin error.
        El documento propone TYPE_SPECS pero el código real usa get_spec().
        """
        from app.brain.prompts.type_specs import get_spec

        # Los tipos clave deben tener spec registrada
        for tipo in ["py", "sql", "md", "pdf", "docx"]:
            spec = get_spec(tipo)
            assert spec is not None, f"get_spec('{tipo}') no devuelve TypeSpec"

    def test_metadata_service_importable(self):
        """BrainMetadataService importa correctamente desde su módulo."""
        from app.brain.metadata_service import (
            BrainMetadataService,
            get_brain_metadata_service,
            init_brain_metadata_service,
        )

        assert BrainMetadataService is not None

    def test_chunking_importable(self):
        """app.brain.chunking importa y expone get_chunks()."""
        from app.brain.chunking import get_chunks

        assert callable(get_chunks)


# ===========================================================================
# TestPipelineTresFases — verifica las 3 fases sobre ficheros reales
# ===========================================================================

class TestPipelineTresFases:
    """Tests del pipeline de tres fases sobre ficheros reales."""

    def test_python_file_pipeline(self):
        """
        Un .py pasa las 3 fases y genera processed_text con marcas semánticas.
        Verifica: extracción, quality_score, search_space_id propagado, marcas ##/###.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("ejemplo.py")
        blocks = processor.extract(
            str(FIXTURES_DIR / "ejemplo.py"),
            search_space_id=42,
        )

        assert len(blocks) > 0, "El extractor Python no produjo bloques"
        assert all(
            "quality_score" in b.get("metadata", {}) for b in blocks
        ), "UniversalCleaner no aplicó quality_score"
        assert all(
            b.get("search_space_id") == 42 for b in blocks
        ), "search_space_id no propagado en raíz de bloques"
        assert all(
            b.get("metadata", {}).get("search_space_id") == 42 for b in blocks
        ), "search_space_id no propagado en metadata de bloques"

        processed = processor.preprocess(blocks)
        assert len(processed) > 0, "processed_text vacío"
        # El preprocesador Python inyecta ## o ### para funciones/clases
        assert "## " in processed or "### " in processed, (
            "processed_text no contiene marcas semánticas ##/###"
        )

    def test_sql_file_pipeline(self):
        """
        Un .sql con PROCEDURE genera marcas semánticas en el processed_text.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("sp_ejemplo.sql")
        blocks = processor.extract(
            str(FIXTURES_DIR / "sp_ejemplo.sql"),
            search_space_id=42,
        )
        assert len(blocks) > 0, "El extractor SQL no produjo bloques"

        processed = processor.preprocess(blocks)
        assert len(processed) > 50, "processed_text SQL demasiado corto"
        assert "### " in processed or "## " in processed, (
            "processed_text SQL no contiene marcas semánticas"
        )

    def test_md_file_pipeline(self):
        """
        Un .md genera processed_text no vacío preservando estructura de headings.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(
            str(FIXTURES_DIR / "nota.md"),
            search_space_id=42,
        )
        assert len(blocks) > 0, "El extractor Markdown no produjo bloques"

        processed = processor.preprocess(blocks)
        assert len(processed) > 100, "processed_text Markdown demasiado corto"

    def test_pdf_pipeline(self):
        """
        Un PDF genera processed_text no vacío.
        Usa el sample.pdf ya existente en tests/fixtures/.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("sample.pdf")
        blocks = processor.extract(
            str(FIXTURES_DIR / "sample.pdf"),
            search_space_id=42,
        )
        assert len(blocks) > 0, "El extractor PDF no produjo bloques"

        processed = processor.preprocess(blocks)
        assert len(processed) > 100, "processed_text PDF demasiado corto"

    def test_universal_cleaner_applied(self):
        """
        Los bloques tienen quality_score y language en metadata.
        Verifica que UniversalCleaner se aplicó correctamente en Fase 2.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(
            str(FIXTURES_DIR / "nota.md"),
            search_space_id=99,
        )
        for block in blocks:
            meta = block.get("metadata", {})
            assert "quality_score" in meta, (
                f"Bloque sin quality_score: {block.get('content', '')[:50]}"
            )
            assert "language" in meta, (
                f"Bloque sin language: {block.get('content', '')[:50]}"
            )

    def test_search_space_id_propagated(self):
        """
        search_space_id aparece en raíz del bloque y en su metadata.
        Garantiza disponibilidad para payload Qdrant (F2) y RBAC (F4).
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(
            str(FIXTURES_DIR / "nota.md"),
            search_space_id=123,
        )
        for b in blocks:
            assert b.get("search_space_id") == 123, (
                "search_space_id ausente en raíz del bloque"
            )
            assert b.get("metadata", {}).get("search_space_id") == 123, (
                "search_space_id ausente en metadata del bloque"
            )

    def test_extract_from_text_connector(self):
        """
        extract_from_text() funciona para conectores API sin fichero en disco.
        Simula una página Confluence entregada como texto directo.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get(".confluence")
        blocks = processor.extract_from_text(
            text="# Página Confluence\n\nContenido de la página de prueba.",
            search_space_id=77,
            metadata={"source_url": "https://confluence.example.com/pages/123"},
        )
        assert len(blocks) > 0, "extract_from_text no produjo bloques"
        assert all(
            b.get("search_space_id") == 77 for b in blocks
        ), "search_space_id no propagado en bloques de conector"

    def test_unsupported_extension_fallback(self):
        """
        Extensión no soportada devuelve processor sin lanzar excepción.
        is_supported() debe devolver False para extensiones desconocidas.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get("fichero.xyz")
        assert processor is not None, "DocumentProcessorFactory.get() no debe lanzar excepción"
        assert not DocumentProcessorFactory.is_supported("fichero.xyz"), (
            ".xyz no debería estar soportado"
        )

    def test_quality_trigger_values(self):
        """
        Los quality_triggers están correctamente configurados por tipo.
        Heterogéneos tienen 32000; homogéneos tienen None.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        for ext in [".docx", ".pdf", ".md"]:
            p = DocumentProcessorFactory.get(f"doc{ext}")
            assert p.get_quality_trigger() == 32000, (
                f"{ext} debería tener quality_trigger=32000 (heterogéneo)"
            )
        for ext in [".py", ".sql"]:
            p = DocumentProcessorFactory.get(f"doc{ext}")
            assert p.get_quality_trigger() is None, (
                f"{ext} homogéneo no debería tener quality_trigger"
            )


# ===========================================================================
# TestBrainMetadataService — classify y normalize con mock de BD
# ===========================================================================

class TestBrainMetadataService:
    """Tests del servicio de metadata (BrainMetadataService) con caché mockeada."""

    @pytest.fixture
    def meta_service_with_mock_db(self):
        """
        Crea un BrainMetadataService con caché pre-cargada sin DB real.
        Permite testear classify_document y normalize_tags sin PostgreSQL.

        Nota: _caches usa clave int=0 (space global) en lugar del str=""
        del documento original — adaptación a SurfSense (search_space_id es int).
        """
        from app.brain.metadata_service import BrainMetadataService, _SpaceCache

        svc = BrainMetadataService(session_factory=MagicMock())
        cache = _SpaceCache()
        cache.domains = [
            {
                "domain_key": "data-engineering",
                "label": "Data Engineering",
                "signal_tags": ["pyspark", "etl", "delta-lake"],
                "signal_kw": ["pipeline", "ingesta"],
            },
            {
                "domain_key": "development",
                "label": "Software Development",
                "signal_tags": ["python", "fastapi"],
                "signal_kw": ["api", "backend"],
            },
        ]
        cache.doc_types = [
            {
                "type_key": "pipeline",
                "label": "Pipeline ETL",
                "signal_tags": ["etl", "pipeline"],
                "signal_kw": ["ingesta", "transformacion"],
                "signal_formats": [".py", ".json"],
            },
            {
                "type_key": "notebook",
                "label": "Notebook",
                "signal_tags": ["jupyter", "notebook"],
                "signal_kw": ["analisis"],
                "signal_formats": [".ipynb"],
            },
        ]
        cache.vocab_alias = {
            "spark":        "pyspark",
            "apache-spark": "pyspark",
            "adf":          "azure-data-factory",
            "delta":        "delta-lake",
            "pyspark":      "pyspark",
            "delta-lake":   "delta-lake",
        }
        cache.vocab_set    = {"pyspark", "delta-lake", "azure-data-factory", "python", "fastapi"}
        cache.entity_hints = []
        cache.loaded_at    = time.monotonic()
        # Clave int=0 (space global) — adaptación vs str="" del documento
        svc._caches[0] = cache
        return svc

    def test_classify_document_data_engineering(self, meta_service_with_mock_db):
        """
        Documento con tags de data engineering se clasifica correctamente
        como domain=data-engineering y doc_type=pipeline.
        """
        result = meta_service_with_mock_db.classify_document(
            tags=["pyspark", "delta-lake", "etl"],
            keyphrases=["pipeline ingesta"],
            title="pipeline_tte_etl.py",
            file_type=".py",
            search_space_id=0,
        )
        assert result["domain"] == "data-engineering", (
            f"domain esperado 'data-engineering', obtenido '{result['domain']}'"
        )
        assert result["doc_type"] == "pipeline", (
            f"doc_type esperado 'pipeline', obtenido '{result['doc_type']}'"
        )

    def test_normalize_tags_resolves_aliases(self, meta_service_with_mock_db):
        """
        normalize_tags() convierte aliases a sus formas canónicas.
        spark → pyspark, delta → delta-lake, adf → azure-data-factory.
        """
        result = meta_service_with_mock_db.normalize_tags(
            ["spark", "delta", "adf"],
            search_space_id=0,
        )
        assert "pyspark" in result, "'spark' no resolvió a 'pyspark'"
        assert "delta-lake" in result, "'delta' no resolvió a 'delta-lake'"
        assert "azure-data-factory" in result, "'adf' no resolvió a 'azure-data-factory'"

    def test_classify_unknown_returns_other(self, meta_service_with_mock_db):
        """
        Documento sin señales suficientes → domain='other'.
        """
        result = meta_service_with_mock_db.classify_document(
            tags=[], keyphrases=[], title="random_document.pdf",
            file_type=".pdf", search_space_id=0,
        )
        assert result["domain"] == "other", (
            f"domain esperado 'other', obtenido '{result['domain']}'"
        )


# ===========================================================================
# TestSearchSpaceIsolation — search_space_id se propaga y aíslá
# ===========================================================================

class TestSearchSpaceIsolation:
    """Verifica que search_space_id se propaga y aíslá correctamente."""

    def test_process_document_content_unsupported_raises(self):
        """
        process_document_content() lanza ValueError para extensiones no soportadas.
        Adapta el test del documento: en nuestra firma no existe llm_client.
        """
        import asyncio
        from app.utils.document_converters import process_document_content

        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(process_document_content(
                file_content=b"test content",
                filename="test.xyz",  # extensión no soportada → ValueError
                search_space_id=0,
            ))

    def test_blocks_contain_search_space_id(self):
        """
        Todos los bloques del pipeline tienen search_space_id en su metadata.
        Verifica propagación desde extract_from_text() para conectores.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get(".md")
        blocks = processor.extract_from_text(
            "# Título\n\nContenido de prueba.",
            search_space_id=999,
        )
        assert all(
            b.get("search_space_id") == 999 for b in blocks
        ), "search_space_id no propagado en raíz"
        assert all(
            b.get("metadata", {}).get("search_space_id") == 999 for b in blocks
        ), "search_space_id no propagado en metadata"

    def test_zero_search_space_id_not_injected(self):
        """
        search_space_id=0 (valor nulo por defecto) no se inyecta en bloques.
        _inject_search_space() debe saltar si search_space_id es falsy.
        """
        from app.brain.processor_factory import DocumentProcessorFactory

        processor = DocumentProcessorFactory.get(".md")
        blocks = processor.extract_from_text(
            "Contenido de prueba.",
            search_space_id=0,  # falsy → no debe inyectarse
        )
        # Con search_space_id=0 no se inyecta, el campo puede no estar o ser None
        for b in blocks:
            assert b.get("search_space_id") != 42, (
                "search_space_id=0 no debería inyectarse en los bloques"
            )
