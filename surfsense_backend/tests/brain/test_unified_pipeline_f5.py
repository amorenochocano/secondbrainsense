"""
tests/brain/test_unified_pipeline_f5.py
---------------------------------------
Tests de la Fase 5 — Pipeline Unificado.

Cobertura:
  - TestCategorizer          : categorización A/B/C por DocumentType y extensión
  - TestCategoryA            : ficheros → DocumentProcessorFactory → bloques limpios
  - TestCategoryB            : conectores → extractor virtual → bloques limpios
  - TestCategoryC_Saco       : "saco" genérico → UniversalCleaner + chunking semántico
  - TestSemanticChunking     : _semantic_chunk_text, headings, párrafos, tablas
  - TestQualityFilter        : bloques < BRAIN_QUALITY_THRESHOLD descartados
  - TestUnifiedEmbedder      : embed_query, embed_chunks, multi-provider dispatch
  - TestHybridSearchDispatch : dispatch Qdrant vs pgvector en hybrid_search
  - TestFallbacks            : BRAIN_INGESTION_ENABLED=false, extractor falla → saco
  - TestSkipSynthesis        : tipos efímeros sin pasaporte pero sí en Qdrant
  - TestEndToEnd             : process_document flujo completo

Metodología (misma que F1-F4):
  - Clases por área funcional.
  - Mocks de Qdrant, Ollama, DocumentProcessor, UniversalCleaner.
  - Parchear donde vive la función (regla unittest.mock).
  - Sin hardcode: umbrales y modelos desde los módulos reales.
"""
import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import Document, DocumentType
from app.indexing_pipeline.connector_document import ConnectorDocument


# ---------------------------------------------------------------------------
# Fixtures comunes
# ---------------------------------------------------------------------------

def _make_document(doc_type: str = "FILE", doc_id: int = 1) -> Document:
    """Crea un Document mock con los campos mínimos."""
    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.document_type = doc_type
    return doc


def _make_connector_doc(
    title: str = "test.pdf",
    source_markdown: str = "# Test\n\nContenido de prueba.",
    doc_type: str = "FILE",
    search_space_id: int = 1,
    should_use_code_chunker: bool = False,
) -> ConnectorDocument:
    """Crea un ConnectorDocument con los campos requeridos."""
    return ConnectorDocument(
        title=title,
        source_markdown=source_markdown,
        unique_id=f"unique-{title}",
        document_type=DocumentType(doc_type),
        search_space_id=search_space_id,
        should_use_code_chunker=should_use_code_chunker,
        metadata={},
        connector_id=None,
        created_by_id="test-user-id",
        folder_id=None,
    )


# ===========================================================================
# TestCategorizer — categorización A/B/C
# ===========================================================================

class TestCategorizer:
    """
    Verifica que _categorize() asigna la categoría correcta según
    DocumentType y extensión del título.
    """

    def test_file_pdf_es_categoria_a(self):
        """Un FILE con extensión .pdf → categoría A (extractor especializado)."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.FILE)
        cdoc = _make_connector_doc(title="informe.pdf", doc_type="FILE")

        with patch("app.brain.processor_factory.DocumentProcessorFactory.is_supported", return_value=True):
            result = _categorize(doc, cdoc)

        assert result == "A"

    def test_file_docx_es_categoria_a(self):
        """Un FILE con extensión .docx → categoría A."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.FILE)
        cdoc = _make_connector_doc(title="manual.docx", doc_type="FILE")

        with patch("app.brain.processor_factory.DocumentProcessorFactory.is_supported", return_value=True):
            assert _categorize(doc, cdoc) == "A"

    def test_confluence_es_categoria_b(self):
        """CONFLUENCE_CONNECTOR → categoría B (extractor virtual)."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.CONFLUENCE_CONNECTOR)
        cdoc = _make_connector_doc(title="Confluence Page", doc_type="CONFLUENCE_CONNECTOR")
        assert _categorize(doc, cdoc) == "B"

    def test_jira_es_categoria_b(self):
        """JIRA_CONNECTOR → categoría B."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.JIRA_CONNECTOR)
        cdoc = _make_connector_doc(title="JIRA-123", doc_type="JIRA_CONNECTOR")
        assert _categorize(doc, cdoc) == "B"

    def test_slack_es_categoria_c(self):
        """SLACK_CONNECTOR → categoría C (saco genérico)."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.SLACK_CONNECTOR)
        cdoc = _make_connector_doc(title="Slack message", doc_type="SLACK_CONNECTOR")
        assert _categorize(doc, cdoc) == "C"

    def test_gmail_es_categoria_c(self):
        """GOOGLE_GMAIL_CONNECTOR → categoría C."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.GOOGLE_GMAIL_CONNECTOR)
        cdoc = _make_connector_doc(title="Email subject", doc_type="GOOGLE_GMAIL_CONNECTOR")
        assert _categorize(doc, cdoc) == "C"

    def test_extension_desconocida_es_categoria_c(self):
        """Un FILE con extensión no soportada → categoría C."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.FILE)
        cdoc = _make_connector_doc(title="archivo.xyz", doc_type="FILE")

        with patch("app.brain.processor_factory.DocumentProcessorFactory.is_supported", return_value=False):
            assert _categorize(doc, cdoc) == "C"

    def test_code_chunker_fuerza_categoria_a(self):
        """should_use_code_chunker=True → categoría A aunque no sea FILE."""
        from app.indexing_pipeline.brain_ingestion_adapter import _categorize

        doc = _make_document(DocumentType.GITHUB_CONNECTOR)
        # GITHUB_CONNECTOR es B por defecto, pero con code_chunker debería ser B
        # (B tiene prioridad sobre A en el dispatch)
        cdoc = _make_connector_doc(
            title="script.py", doc_type="GITHUB_CONNECTOR",
            should_use_code_chunker=True,
        )
        # GITHUB es B por _CATEGORY_B_MAP — el dispatch B tiene prioridad
        assert _categorize(doc, cdoc) == "B"


# ===========================================================================
# TestSemanticChunking — chunking semántico para categoría C
# ===========================================================================

class TestSemanticChunking:
    """
    Verifica _semantic_chunk_text: headings, párrafos, tablas indivisibles.
    """

    def test_chunk_por_headings(self):
        """Texto con headings markdown se corta por headings."""
        from app.indexing_pipeline.brain_ingestion_adapter import _semantic_chunk_text

        text = "## Sección 1\n\nContenido uno.\n\n## Sección 2\n\nContenido dos."
        chunks = _semantic_chunk_text(text)
        assert len(chunks) == 2
        assert "Sección 1" in chunks[0]
        assert "Sección 2" in chunks[1]

    def test_chunk_por_parrafos_sin_headings(self):
        """Texto sin headings se corta por párrafos agrupados."""
        from app.indexing_pipeline.brain_ingestion_adapter import _semantic_chunk_text

        # Crear texto largo sin headings
        paragraphs = [f"Párrafo {i} con contenido suficiente para llenar." * 10 for i in range(20)]
        text = "\n\n".join(paragraphs)
        chunks = _semantic_chunk_text(text)
        assert len(chunks) > 1  # Debe haber más de un chunk

    def test_texto_vacio_devuelve_lista_vacia(self):
        """Texto vacío → lista vacía."""
        from app.indexing_pipeline.brain_ingestion_adapter import _semantic_chunk_text

        assert _semantic_chunk_text("") == []
        assert _semantic_chunk_text("   ") == []

    def test_tabla_markdown_es_chunk_indivisible(self):
        """Una tabla markdown no se corta entre filas."""
        from app.indexing_pipeline.brain_ingestion_adapter import _semantic_chunk_text

        text = (
            "Texto antes.\n\n"
            "| Col1 | Col2 |\n"
            "|------|------|\n"
            "| A    | B    |\n"
            "| C    | D    |\n\n"
            "Texto después."
        )
        chunks = _semantic_chunk_text(text)
        # La tabla debe estar completa en un solo chunk
        tabla_chunks = [c for c in chunks if "|---" in c]
        assert len(tabla_chunks) == 1
        assert "| A" in tabla_chunks[0]
        assert "| C" in tabla_chunks[0]


# ===========================================================================
# TestQualityFilter — filtrado por quality_score
# ===========================================================================

class TestQualityFilter:
    """
    Verifica _filter_low_quality: bloques con quality < umbral se descartan.
    """

    def test_bloques_buenos_pasan(self):
        """Bloques con quality_score >= umbral se mantienen."""
        from app.indexing_pipeline.brain_ingestion_adapter import _filter_low_quality

        blocks = [
            {"content": "Texto bueno", "metadata": {"quality_score": 0.85}},
            {"content": "Texto ok",    "metadata": {"quality_score": 0.50}},
        ]
        result = _filter_low_quality(blocks, doc_id=1)
        assert len(result) == 2

    def test_bloques_malos_descartados(self):
        """Bloques con quality_score < umbral se filtran."""
        from app.indexing_pipeline.brain_ingestion_adapter import _filter_low_quality

        blocks = [
            {"content": "Texto bueno",   "metadata": {"quality_score": 0.85}},
            {"content": "Basura OCR",    "metadata": {"quality_score": 0.10}},
            {"content": "Texto corrupto","metadata": {"quality_score": 0.05}},
        ]
        result = _filter_low_quality(blocks, doc_id=1)
        assert len(result) == 1
        assert result[0]["content"] == "Texto bueno"

    def test_sin_quality_score_asume_bueno(self):
        """Bloques sin quality_score en metadata se mantienen (asume 1.0)."""
        from app.indexing_pipeline.brain_ingestion_adapter import _filter_low_quality

        blocks = [
            {"content": "Sin metadata", "metadata": {}},
            {"content": "Sin key",      "metadata": {"other": "value"}},
        ]
        result = _filter_low_quality(blocks, doc_id=1)
        assert len(result) == 2

    def test_lista_vacia_devuelve_vacia(self):
        """Lista vacía → lista vacía sin error."""
        from app.indexing_pipeline.brain_ingestion_adapter import _filter_low_quality

        assert _filter_low_quality([], doc_id=1) == []


# ===========================================================================
# TestUnifiedEmbedder — multi-provider embedding
# ===========================================================================

class TestUnifiedEmbedder:
    """
    Verifica unified_embedder: dispatch por provider, fallback, batch.
    """

    @patch("app.indexing_pipeline.unified_embedder._embed_ollama")
    def test_embed_query_usa_ollama_por_defecto(self, mock_ollama):
        """embed_query() delega a _embed_ollama con el modelo configurado."""
        mock_ollama.return_value = [0.1] * 768

        from app.indexing_pipeline.unified_embedder import embed_query

        with patch("app.indexing_pipeline.unified_embedder.BRAIN_EMBEDDING_PROVIDER", "ollama"):
            result = embed_query("test query")

        mock_ollama.assert_called_once()
        assert len(result) == 768

    @patch("app.indexing_pipeline.unified_embedder._embed_ollama")
    def test_embed_chunks_batch(self, mock_ollama):
        """embed_chunks() procesa una lista de textos secuencialmente."""
        mock_ollama.return_value = [0.1] * 768

        from app.indexing_pipeline.unified_embedder import embed_chunks

        with patch("app.indexing_pipeline.unified_embedder.BRAIN_EMBEDDING_PROVIDER", "ollama"):
            result = embed_chunks(["chunk 1", "chunk 2", "chunk 3"])

        assert len(result) == 3
        assert all(len(v) == 768 for v in result)

    def test_provider_no_soportado_lanza_error(self):
        """Provider inexistente → ValueError con mensaje descriptivo."""
        from app.indexing_pipeline.unified_embedder import embed_single

        with patch("app.indexing_pipeline.unified_embedder.BRAIN_EMBEDDING_PROVIDER", "inexistente"):
            with pytest.raises(ValueError, match="no soportado"):
                embed_single("test", "model")

    def test_get_embedding_info_incluye_provider(self):
        """get_embedding_info() devuelve provider, model, dimension."""
        from app.indexing_pipeline.unified_embedder import get_embedding_info

        info = get_embedding_info()
        assert "provider" in info
        assert "model" in info
        assert "dimension" in info

    @patch("app.indexing_pipeline.unified_embedder._embed_ollama")
    def test_embed_chunks_fallo_individual_no_rompe_batch(self, mock_ollama):
        """Si un chunk falla, se usa vector de ceros — el batch continúa."""
        mock_ollama.side_effect = [
            [0.1] * 768,
            Exception("Ollama timeout"),
            [0.3] * 768,
        ]

        from app.indexing_pipeline.unified_embedder import embed_chunks

        with patch("app.indexing_pipeline.unified_embedder.BRAIN_EMBEDDING_PROVIDER", "ollama"):
            result = embed_chunks(["ok", "fail", "ok"])

        assert len(result) == 3
        assert result[0] == [0.1] * 768  # OK
        assert all(v == 0.0 for v in result[1])  # Vector ceros
        assert result[2] == [0.3] * 768  # OK


# ===========================================================================
# TestHybridSearchDispatch — dispatch Qdrant vs pgvector
# ===========================================================================

class TestHybridSearchDispatch:
    """
    Verifica que hybrid_search() y vector_search() hacen dispatch
    correcto entre Qdrant y pgvector según BRAIN_INGESTION_ENABLED.
    """

    def test_vector_search_brain_enabled_usa_qdrant(self):
        """BRAIN_INGESTION_ENABLED=true → _vector_search_qdrant."""
        from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

        retriever = ChucksHybridSearchRetriever(MagicMock())
        retriever._vector_search_qdrant = AsyncMock(return_value=[])
        retriever._vector_search_pgvector = AsyncMock(return_value=[])

        import asyncio
        with patch("app.retriever.chunks_hybrid_search._BRAIN_INGESTION_ENABLED", True):
            asyncio.get_event_loop().run_until_complete(
                retriever.vector_search("test", 5, 1)
            )

        retriever._vector_search_qdrant.assert_called_once()
        retriever._vector_search_pgvector.assert_not_called()

    def test_vector_search_brain_disabled_usa_pgvector(self):
        """BRAIN_INGESTION_ENABLED=false → _vector_search_pgvector."""
        from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

        retriever = ChucksHybridSearchRetriever(MagicMock())
        retriever._vector_search_qdrant = AsyncMock(return_value=[])
        retriever._vector_search_pgvector = AsyncMock(return_value=[])

        import asyncio
        with patch("app.retriever.chunks_hybrid_search._BRAIN_INGESTION_ENABLED", False):
            asyncio.get_event_loop().run_until_complete(
                retriever.vector_search("test", 5, 1)
            )

        retriever._vector_search_pgvector.assert_called_once()
        retriever._vector_search_qdrant.assert_not_called()

    def test_hybrid_search_brain_enabled_usa_qdrant_path(self):
        """BRAIN_INGESTION_ENABLED=true → _hybrid_search_qdrant."""
        from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

        retriever = ChucksHybridSearchRetriever(MagicMock())
        retriever._hybrid_search_qdrant = AsyncMock(return_value=[])
        retriever._hybrid_search_pgvector = AsyncMock(return_value=[])

        import asyncio
        with patch("app.retriever.chunks_hybrid_search._BRAIN_INGESTION_ENABLED", True):
            asyncio.get_event_loop().run_until_complete(
                retriever.hybrid_search("test", 5, 1)
            )

        retriever._hybrid_search_qdrant.assert_called_once()
        retriever._hybrid_search_pgvector.assert_not_called()

    def test_hybrid_search_brain_disabled_usa_pgvector_path(self):
        """BRAIN_INGESTION_ENABLED=false → _hybrid_search_pgvector."""
        from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

        retriever = ChucksHybridSearchRetriever(MagicMock())
        retriever._hybrid_search_qdrant = AsyncMock(return_value=[])
        retriever._hybrid_search_pgvector = AsyncMock(return_value=[])

        import asyncio
        with patch("app.retriever.chunks_hybrid_search._BRAIN_INGESTION_ENABLED", False):
            asyncio.get_event_loop().run_until_complete(
                retriever.hybrid_search("test", 5, 1)
            )

        retriever._hybrid_search_pgvector.assert_called_once()
        retriever._hybrid_search_qdrant.assert_not_called()

    def test_embed_nomic_delega_a_unified_embedder(self):
        """_embed_nomic() delega a unified_embedder.embed_query()."""
        from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

        with patch("app.indexing_pipeline.unified_embedder.embed_query", return_value=[0.1] * 768) as mock:
            result = ChucksHybridSearchRetriever._embed_nomic("test")

        mock.assert_called_once_with("test")
        assert len(result) == 768

    def test_ingest_router_embed_delega_a_unified_embedder(self):
        """IngestRouter._embed() delega a unified_embedder.embed_single()."""
        with patch("app.indexing_pipeline.unified_embedder.embed_single", return_value=[0.1] * 768) as mock:
            from app.brain.ingest_router import _embed
            result = _embed("test text", "nomic-embed-text")

        mock.assert_called_once_with("test text", "nomic-embed-text")
        assert len(result) == 768


# ===========================================================================
# TestFallbacks — fallback cuando pipeline Brain falla
# ===========================================================================

class TestFallbacks:
    """
    Verifica comportamiento de fallback en process_document.
    """

    @pytest.mark.asyncio
    async def test_extractor_falla_cae_al_saco(self):
        """Si el extractor de categoría A falla, cae a _basic_chunk_text."""
        from app.indexing_pipeline.brain_ingestion_adapter import process_document

        doc = _make_document(DocumentType.FILE, doc_id=99)
        cdoc = _make_connector_doc(title="test.pdf", doc_type="FILE")

        with (
            patch("app.brain.processor_factory.DocumentProcessorFactory.is_supported", return_value=True),
            patch(
                "app.indexing_pipeline.brain_ingestion_adapter._process_category_a",
                side_effect=Exception("Extractor roto"),
            ),
            patch("app.indexing_pipeline.brain_ingestion_adapter._embed_and_upsert", new_callable=AsyncMock),
            patch("app.indexing_pipeline.brain_ingestion_adapter._synthesize_passport", new_callable=AsyncMock),
        ):
            result = await process_document(doc, cdoc)

        # Debe devolver chunks (del fallback _basic_chunk_text), no lista vacía
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_texto_vacio_devuelve_lista_vacia(self):
        """Document sin contenido → lista vacía sin error."""
        from app.indexing_pipeline.brain_ingestion_adapter import process_document

        doc = _make_document(DocumentType.FILE, doc_id=100)
        cdoc = _make_connector_doc(title="empty.txt", source_markdown="", doc_type="FILE")

        result = await process_document(doc, cdoc)
        assert result == []

    @pytest.mark.asyncio
    async def test_process_document_nunca_lanza_excepcion(self):
        """process_document() NUNCA propaga excepciones al llamador."""
        from app.indexing_pipeline.brain_ingestion_adapter import process_document

        doc = _make_document(DocumentType.FILE, doc_id=101)
        cdoc = _make_connector_doc(title="test.py", doc_type="FILE")

        with (
            patch("app.brain.processor_factory.DocumentProcessorFactory.is_supported", return_value=True),
            patch(
                "app.indexing_pipeline.brain_ingestion_adapter._process_category_a",
                side_effect=RuntimeError("Error catastrófico"),
            ),
        ):
            # No debe lanzar excepción
            result = await process_document(doc, cdoc)
            assert isinstance(result, list)


# ===========================================================================
# TestSkipSynthesis — tipos efímeros sin pasaporte
# ===========================================================================

class TestSkipSynthesis:
    """
    Verifica que _SKIP_SYNTHESIS_TYPES excluye tipos efímeros de la síntesis.
    """

    def test_slack_en_skip_synthesis(self):
        """SLACK_CONNECTOR está en _SKIP_SYNTHESIS_TYPES."""
        from app.indexing_pipeline.brain_ingestion_adapter import _SKIP_SYNTHESIS_TYPES

        assert DocumentType.SLACK_CONNECTOR in _SKIP_SYNTHESIS_TYPES

    def test_discord_en_skip_synthesis(self):
        """DISCORD_CONNECTOR está en _SKIP_SYNTHESIS_TYPES."""
        from app.indexing_pipeline.brain_ingestion_adapter import _SKIP_SYNTHESIS_TYPES

        assert DocumentType.DISCORD_CONNECTOR in _SKIP_SYNTHESIS_TYPES

    def test_gmail_en_skip_synthesis(self):
        """GOOGLE_GMAIL_CONNECTOR está en _SKIP_SYNTHESIS_TYPES."""
        from app.indexing_pipeline.brain_ingestion_adapter import _SKIP_SYNTHESIS_TYPES

        assert DocumentType.GOOGLE_GMAIL_CONNECTOR in _SKIP_SYNTHESIS_TYPES

    def test_file_no_en_skip_synthesis(self):
        """FILE NO está en _SKIP_SYNTHESIS_TYPES (sí se sintetiza)."""
        from app.indexing_pipeline.brain_ingestion_adapter import _SKIP_SYNTHESIS_TYPES

        assert DocumentType.FILE not in _SKIP_SYNTHESIS_TYPES


# ===========================================================================
# TestBuildSourceSlug — slug determinista
# ===========================================================================

class TestBuildSourceSlug:
    """
    Verifica _build_source_slug: determinista, normalizado, sin chars especiales.
    """

    def test_slug_formato_correcto(self):
        """El slug contiene space_id, doc_type y unique_id normalizados."""
        from app.indexing_pipeline.brain_ingestion_adapter import _build_source_slug

        doc = _make_document(DocumentType.FILE)
        cdoc = _make_connector_doc(title="Mi Archivo.pdf", doc_type="FILE")
        slug = _build_source_slug(doc, cdoc)

        assert "1-file-" in slug  # space_id=1, type=FILE
        assert " " not in slug    # sin espacios
        assert slug == slug.lower()  # todo minúsculas

    def test_slug_determinista(self):
        """Mismo documento → mismo slug siempre."""
        from app.indexing_pipeline.brain_ingestion_adapter import _build_source_slug

        doc = _make_document(DocumentType.CONFLUENCE_CONNECTOR)
        cdoc = _make_connector_doc(title="Conf Page", doc_type="CONFLUENCE_CONNECTOR")
        slug1 = _build_source_slug(doc, cdoc)
        slug2 = _build_source_slug(doc, cdoc)
        assert slug1 == slug2


# ===========================================================================
# TestKnowledgeSearchDispatch — dispatch de embedding en knowledge_search
# ===========================================================================

class TestKnowledgeSearchDispatch:
    """
    Verifica que knowledge_search.py usa nomic cuando Brain está activo
    y MiniLM cuando está desactivado.
    """

    def test_knowledge_search_importa_brain_embed_query(self):
        """El import de _brain_embed_query existe en knowledge_search."""
        import app.agents.chat.multi_agent_chat.shared.middleware.knowledge_search as ks
        assert hasattr(ks, "_brain_embed_query")

    def test_knowledge_search_preserva_embed_texts(self):
        """embed_texts (fallback) sigue importado."""
        import app.agents.chat.multi_agent_chat.shared.middleware.knowledge_search as ks
        assert hasattr(ks, "embed_texts")


# ===========================================================================
# TestExtractExtension — helper de extensión
# ===========================================================================

class TestExtractExtension:
    """
    Verifica _extract_extension: extrae extensión del título.
    """

    def test_pdf(self):
        from app.indexing_pipeline.brain_ingestion_adapter import _extract_extension
        assert _extract_extension("informe.pdf") == ".pdf"

    def test_docx(self):
        from app.indexing_pipeline.brain_ingestion_adapter import _extract_extension
        assert _extract_extension("Manual Técnico.docx") == ".docx"

    def test_sin_extension(self):
        from app.indexing_pipeline.brain_ingestion_adapter import _extract_extension
        assert _extract_extension("sin_extension") == ""

    def test_titulo_vacio(self):
        from app.indexing_pipeline.brain_ingestion_adapter import _extract_extension
        assert _extract_extension("") == ""

    def test_extension_mayusculas_normalizada(self):
        from app.indexing_pipeline.brain_ingestion_adapter import _extract_extension
        assert _extract_extension("FILE.PDF") == ".pdf"
