"""
tests/brain/test_qdrant_f2.py
------------------------------
Tests de la Fase 2 del pipeline Brain (F2.6).

Cobertura:
  - TestQdrantManager          : ensure_collections, idempotencia, mismatch dim, delete_by_source
  - TestIngestRouterSearchSpaceId : search_space_id en firmas y payloads de IngestRouter
  - TestColeccionesQdrantIntegracion : tests contra Qdrant real (marca @pytest.mark.integration)

Adaptaciones respecto al documento F2:
  - test_upsert_delete_aislamiento_multi_tenant: IDs como int (Qdrant no acepta string IDs)
  - _COLLECTION_DIMS se deriva de COLLECTION_CONFIG + EMBED_DIMS (sin hardcode de 768)
  - Los tests unitarios usan QdrantManager.__new__() para evitar conectar a Qdrant real

Fixtures usados:
  - Qdrant real disponible via sbs-dev-qdrant (para tests @integration)
  - mock_client MagicMock para tests unitarios (sin Qdrant)
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.brain.collections import (
    ALL_COLLECTIONS,
    BRAIN,
    CODE,
    COLLECTION_CONFIG,
    EMBED_DIMS,
    KNOWLEDGE,
)
from app.brain.ingest_router import IngestRouter
from app.brain.qdrant_manager import QdrantManager, _COLLECTION_DIMS


# ===========================================================================
# TestQdrantManager — tests unitarios con mock de cliente Qdrant
# ===========================================================================

class TestQdrantManager:
    """Tests unitarios de QdrantManager con cliente Qdrant mockeado."""

    def _make_manager(self, mock_client: MagicMock) -> QdrantManager:
        """
        Crea un QdrantManager sin conectar a Qdrant real.
        Usa __new__() para saltarse __init__ e inyecta el mock directamente.
        """
        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = mock_client
        return mgr

    def test_ensure_collections_creates_brain_and_knowledge(self):
        """
        ensure_collections() crea brain y knowledge con dimensiones correctas.
        CODE NO se crea aquí — su dimensión es dinámica y la gestiona _ingest_code.
        """
        mock_client = MagicMock()
        mock_client.get_collections.return_value = MagicMock(collections=[])
        mgr = self._make_manager(mock_client)

        mgr.ensure_collections()

        # Extraer nombres de las colecciones creadas
        created = [
            c.kwargs["collection_name"]
            for c in mock_client.create_collection.call_args_list
        ]
        assert BRAIN in created, f"Colección '{BRAIN}' no fue creada"
        assert KNOWLEDGE in created, f"Colección '{KNOWLEDGE}' no fue creada"
        assert CODE not in created, f"Colección '{CODE}' NO debe crearse aquí (dim dinámica)"

    def test_ensure_collections_crea_con_dim_correcta(self):
        """
        Las colecciones se crean con la dimensión derivada de COLLECTION_CONFIG + EMBED_DIMS.
        Sin hardcode — si cambia el modelo en collections.py, el test se adapta.
        """
        mock_client = MagicMock()
        mock_client.get_collections.return_value = MagicMock(collections=[])
        mgr = self._make_manager(mock_client)

        mgr.ensure_collections()

        for call in mock_client.create_collection.call_args_list:
            col_name = call.kwargs["collection_name"]
            vector_size = call.kwargs["vectors_config"].size
            expected_dim = _COLLECTION_DIMS[col_name]
            assert vector_size == expected_dim, (
                f"Colección '{col_name}' creada con dim={vector_size}, "
                f"se esperaba {expected_dim}"
            )

    def test_ensure_collections_idempotente_dim_correcta(self):
        """
        ensure_collections() no falla si las colecciones ya existen con dim correcta.
        No llama a create_collection en ese caso.
        """
        mock_client = MagicMock()
        # Simular que brain y knowledge ya existen
        brain_col = MagicMock()
        brain_col.name = BRAIN
        know_col = MagicMock()
        know_col.name = KNOWLEDGE
        mock_client.get_collections.return_value = MagicMock(
            collections=[brain_col, know_col]
        )
        # Dimensión correcta para ambas
        mock_client.get_collection.return_value.config.params.vectors.size = (
            _COLLECTION_DIMS[BRAIN]
        )

        mgr = self._make_manager(mock_client)
        mgr.ensure_collections()  # No debe lanzar excepción

        mock_client.create_collection.assert_not_called()

    def test_ensure_collections_raises_on_dim_mismatch(self):
        """
        ensure_collections() lanza ValueError si una colección existe con dim incorrecta.
        El mensaje incluye la dim esperada para facilitar el diagnóstico.
        """
        mock_client = MagicMock()
        brain_col = MagicMock()
        brain_col.name = BRAIN
        mock_client.get_collections.return_value = MagicMock(
            collections=[brain_col]
        )
        # Dimensión incorrecta — simula un cambio de modelo sin recrear la colección
        mock_client.get_collection.return_value.config.params.vectors.size = 384

        mgr = self._make_manager(mock_client)

        expected_dim = str(_COLLECTION_DIMS[BRAIN])
        with pytest.raises(ValueError, match=expected_dim):
            mgr.ensure_collections()

    def test_delete_by_source_filtra_por_source_y_search_space_id(self):
        """
        delete_by_source() filtra por AMBOS source y search_space_id.
        Garantía de aislamiento multi-tenant: un tenant no puede borrar datos de otro.
        """
        mock_client = MagicMock()
        mgr = self._make_manager(mock_client)

        mgr.delete_by_source(source="doc.pdf", search_space_id="space-A")

        # Debe intentar borrar en las 3 colecciones
        assert mock_client.delete.call_count == 3, (
            f"delete() debe llamarse 3 veces (brain+knowledge+code), "
            f"se llamó {mock_client.delete.call_count}"
        )

        # Verificar que ambos filtros están presentes en cada llamada
        for call_args in mock_client.delete.call_args_list:
            filter_obj = call_args.kwargs["points_selector"].filter
            keys = [c.key for c in filter_obj.must]
            assert "source" in keys, "Filtro 'source' ausente en delete()"
            assert "search_space_id" in keys, "Filtro 'search_space_id' ausente en delete()"

    def test_delete_by_source_no_lanza_si_coleccion_no_existe(self):
        """
        delete_by_source() no lanza excepción si una colección no existe (ej: code).
        Registra WARNING pero continúa con las demás colecciones.
        """
        mock_client = MagicMock()
        # Simular que code no existe — lanza excepción en delete
        mock_client.delete.side_effect = [None, None, Exception("Collection not found")]
        mgr = self._make_manager(mock_client)

        # No debe lanzar
        mgr.delete_by_source(source="doc.pdf", search_space_id="space-A")


# ===========================================================================
# TestIngestRouterSearchSpaceId — verifica firmas y propagación de search_space_id
# ===========================================================================

class TestIngestRouterSearchSpaceId:
    """
    Tests que verifican que search_space_id está en todas las firmas
    y se propaga correctamente a los payloads de Qdrant.
    """

    def test_route_requiere_search_space_id(self):
        """route() debe recibir search_space_id como parámetro requerido."""
        import inspect
        sig = inspect.signature(IngestRouter.route)
        assert "search_space_id" in sig.parameters, (
            "route() debe tener parámetro search_space_id"
        )

    def test_route_raw_requiere_search_space_id(self):
        """route_raw() debe recibir search_space_id como parámetro requerido."""
        import inspect
        sig = inspect.signature(IngestRouter.route_raw)
        assert "search_space_id" in sig.parameters, (
            "route_raw() debe tener parámetro search_space_id"
        )

    def test_ingest_brain_tiene_search_space_id(self):
        """_ingest_brain() debe recibir search_space_id como keyword argument."""
        import inspect
        sig = inspect.signature(IngestRouter._ingest_brain)
        assert "search_space_id" in sig.parameters, (
            "_ingest_brain() debe tener kwarg search_space_id"
        )

    def test_ingest_knowledge_raw_tiene_search_space_id(self):
        """_ingest_knowledge_raw() debe recibir search_space_id."""
        import inspect
        sig = inspect.signature(IngestRouter._ingest_knowledge_raw)
        assert "search_space_id" in sig.parameters

    def test_upsert_text_chunks_tiene_search_space_id(self):
        """_upsert_text_chunks() debe recibir search_space_id."""
        import inspect
        sig = inspect.signature(IngestRouter._upsert_text_chunks)
        assert "search_space_id" in sig.parameters

    def test_ingest_code_tiene_search_space_id(self):
        """_ingest_code() debe recibir search_space_id."""
        import inspect
        sig = inspect.signature(IngestRouter._ingest_code)
        assert "search_space_id" in sig.parameters

    def test_ingest_full_document_tiene_search_space_id(self):
        """_ingest_full_document() debe recibir search_space_id."""
        import inspect
        sig = inspect.signature(IngestRouter._ingest_full_document)
        assert "search_space_id" in sig.parameters

    @patch("app.brain.ingest_router._embed", return_value=[0.1] * 768)
    @patch("app.brain.ingest_router.get_chunks", return_value=["chunk de prueba"])
    def test_knowledge_payload_contiene_search_space_id(self, mock_chunks, mock_embed):
        """
        El payload de cada punto en knowledge incluye search_space_id.
        Verifica la propagación real al PointStruct de Qdrant.
        """
        mock_client = MagicMock()
        router = IngestRouter(qdrant_client=mock_client)

        router._ingest_knowledge_raw(
            text_blocks=[{
                "content":      "texto de prueba para el test",
                "content_type": "text",
                "metadata":     {},
            }],
            source="test-doc-f2",
            meta={},
            ingest_metadata=None,
            search_space_id="space-test-001",
        )

        upsert_call = mock_client.upsert.call_args
        assert upsert_call is not None, "upsert() no fue llamado"
        points = upsert_call.kwargs["points"]
        assert len(points) > 0, "No se generaron puntos para upsert"
        assert points[0].payload["search_space_id"] == "space-test-001", (
            f"search_space_id incorrecto en payload: {points[0].payload}"
        )

    @patch("app.brain.ingest_router._embed", return_value=[0.1] * 768)
    @patch("app.brain.ingest_router.get_chunks", return_value=["chunk de prueba"])
    def test_upsert_text_chunks_payload_contiene_search_space_id(
        self, mock_chunks, mock_embed
    ):
        """
        El payload de _upsert_text_chunks incluye search_space_id.
        """
        mock_client = MagicMock()
        router = IngestRouter(qdrant_client=mock_client)

        router._upsert_text_chunks(
            text="texto de prueba",
            source="test-doc-f2",
            meta={},
            collection=KNOWLEDGE,
            raw_ingest=False,
            ingest_metadata=None,
            search_space_id="space-test-002",
        )

        upsert_call = mock_client.upsert.call_args
        points = upsert_call.kwargs["points"]
        assert points[0].payload["search_space_id"] == "space-test-002"

    def test_import_chunking_correcto(self):
        """
        Verifica que el import de get_chunks usa la ruta correcta del proyecto.
        'from chunking import get_chunks' estaba roto en Docker (PYTHONPATH=/app).
        """
        import inspect
        import app.brain.ingest_router as ir_module

        source = inspect.getsource(ir_module)
        assert "from app.brain.chunking import get_chunks" in source, (
            "Import de get_chunks debe ser 'from app.brain.chunking import get_chunks'"
        )
        assert "from chunking import get_chunks" not in source, (
            "Import roto 'from chunking import get_chunks' detectado"
        )


# ===========================================================================
# TestColeccionesQdrantIntegracion — tests contra Qdrant real
# ===========================================================================

class TestColeccionesQdrantIntegracion:
    """
    Tests de integración contra Qdrant real.

    Requieren que el servicio qdrant esté healthy (disponible en el contenedor
    de tests via QDRANT_HOST=qdrant). Marcados con @pytest.mark.integration.
    """

    @pytest.mark.integration
    def test_brain_y_knowledge_existen_tras_ensure(self):
        """
        Con Qdrant real: brain y knowledge existen tras ensure_collections().
        CODE puede no existir — se crea en el primer _ingest_code.
        """
        mgr = QdrantManager.get_instance()
        mgr.ensure_collections()

        existing = {c.name for c in mgr.client.get_collections().collections}
        assert BRAIN in existing, f"Colección '{BRAIN}' no existe en Qdrant"
        assert KNOWLEDGE in existing, f"Colección '{KNOWLEDGE}' no existe en Qdrant"

    @pytest.mark.integration
    def test_brain_dim_correcta(self):
        """brain tiene la dimensión derivada de COLLECTION_CONFIG + EMBED_DIMS."""
        mgr = QdrantManager.get_instance()
        info = mgr.client.get_collection(BRAIN)
        expected = _COLLECTION_DIMS[BRAIN]
        assert info.config.params.vectors.size == expected, (
            f"brain tiene dim={info.config.params.vectors.size}, esperado {expected}"
        )

    @pytest.mark.integration
    def test_knowledge_dim_correcta(self):
        """knowledge tiene la dimensión derivada de COLLECTION_CONFIG + EMBED_DIMS."""
        mgr = QdrantManager.get_instance()
        info = mgr.client.get_collection(KNOWLEDGE)
        expected = _COLLECTION_DIMS[KNOWLEDGE]
        assert info.config.params.vectors.size == expected

    @pytest.mark.integration
    def test_payload_indexes_existen(self):
        """
        Las colecciones brain y knowledge tienen índices de payload
        para 'source' y 'search_space_id' (filtrado eficiente en F4).
        """
        mgr = QdrantManager.get_instance()
        for col in [BRAIN, KNOWLEDGE]:
            info = mgr.client.get_collection(col)
            schema_keys = list(info.payload_schema.keys()) if info.payload_schema else []
            assert "source" in schema_keys, (
                f"Índice 'source' ausente en colección '{col}'"
            )
            assert "search_space_id" in schema_keys, (
                f"Índice 'search_space_id' ausente en colección '{col}'"
            )

    @pytest.mark.integration
    def test_aislamiento_multi_tenant(self):
        """
        Insertar en space-A y space-B, borrar con space-A no afecta space-B.
        Verifica la invariante de seguridad multi-tenant de F2.
        IDs como int (Qdrant requiere int o UUID, no strings).
        """
        from qdrant_client.models import PointStruct

        mgr = QdrantManager.get_instance()

        # IDs únicos para no interferir con otros tests
        id_space_a = abs(hash("pt-space-a-f2-test")) % (10**15)
        id_space_b = abs(hash("pt-space-b-f2-test")) % (10**15)
        dim = _COLLECTION_DIMS[BRAIN]

        # Insertar un punto en space-A y otro en space-B
        mgr.client.upsert(
            collection_name=BRAIN,
            points=[
                PointStruct(
                    id=id_space_a,
                    vector=[0.1] * dim,
                    payload={"source": "doc-test.md", "search_space_id": "space-A-f2"},
                ),
                PointStruct(
                    id=id_space_b,
                    vector=[0.2] * dim,
                    payload={"source": "doc-test.md", "search_space_id": "space-B-f2"},
                ),
            ],
        )

        # Borrar solo space-A
        mgr.delete_by_source(
            source="doc-test.md",
            search_space_id="space-A-f2",
        )

        # Verificar que space-B sigue intacto
        results = mgr.client.retrieve(
            collection_name=BRAIN,
            ids=[id_space_b],
        )
        assert len(results) == 1, (
            "El punto de space-B fue borrado incorrectamente al borrar space-A"
        )
        assert results[0].payload["search_space_id"] == "space-B-f2"

        # Limpiar space-B al terminar
        mgr.delete_by_source(
            source="doc-test.md",
            search_space_id="space-B-f2",
        )
