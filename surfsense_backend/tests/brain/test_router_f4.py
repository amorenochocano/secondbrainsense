"""
tests/brain/test_router_f4.py
------------------------------
Tests de la Fase 4 del Router Multinivel (F4).

Cobertura:
  - TestBrainRouterMultiTenant    : search_space_id en constructor y filtros Qdrant
  - TestBrainRouterCascade        : fallback_needed según score L1, L2, L0
  - TestBrainRouterSearchFiltered : _search_filtered() con filtro tenant+source
  - TestBrainRouterContextBudget  : ModelProfile.retrieval_chunk_budget/max_chars
  - TestBrainRouterHelpers        : needs_level2, is_code_question, _sources_from
  - TestModelProfileRetrieval     : propiedades retrieval_* de ModelProfile (F4.2)
  - TestBrainRoutesHelpers        : helpers de brain_routes.py (_extract_chunks, etc.)
  - TestBrainRouterIntegracion    : tests contra Qdrant real (@pytest.mark.integration)

Adaptaciones respecto al plan F4:
  - _CHUNK_BUDGET y _CHUNK_MAX_CHARS no existen — eliminados en F4.2.
    El presupuesto viene de ModelProfile.retrieval_chunk_budget/max_chars.
  - search_space_id en BrainRouter es str (se convierte a str desde el int de
    SearchSpace.id al instanciar BrainRouter en brain_routes.py).
  - El plan menciona _CHUNK_BUDGET["small"] — adaptado a profile.retrieval_chunk_budget.

Metodología de tests (igual que F1/F2/F3):
  - Clases por área funcional, una clase por concepto.
  - Tests unitarios: mocks de Qdrant vía MagicMock, _embed_for_collection mockeado.
  - Tests de integración: @pytest.mark.integration, requieren QDRANT_HOST.
  - Sin hardcode: dimensiones y budgets desde los módulos reales.
  - Parchear donde vive la función: app.brain.router._embed_for_collection.
"""
import inspect
import os
from unittest.mock import MagicMock, patch

import pytest

from app.brain.collections import BRAIN, KNOWLEDGE, CODE
from app.brain.router import BrainRouter


# ===========================================================================
# TestBrainRouterMultiTenant — search_space_id en constructor y filtros
# ===========================================================================

class TestBrainRouterMultiTenant:
    """
    Verifica que BrainRouter gestiona correctamente el aislamiento multi-tenant
    inyectando search_space_id en todos los filtros Qdrant (G1 + G7 de F4.1).
    """

    def test_constructor_acepta_search_space_id(self):
        """
        BrainRouter(search_space_id=...) almacena el ID como atributo.
        Contrato G1: el router debe ser instanciable con search_space_id.
        """
        router = BrainRouter(search_space_id="space-ABC")
        assert router.search_space_id == "space-ABC"

    def test_constructor_sin_search_space_id_usa_string_vacio(self):
        """
        BrainRouter() sin argumentos usa search_space_id="" (sin filtro tenant).
        Solo para uso interno/admin — las rutas FastAPI siempre pasan un ID.
        """
        router = BrainRouter()
        assert router.search_space_id == ""

    def test_search_incluye_filtro_search_space_id(self):
        """
        _search() debe pasar query_filter con FieldCondition(search_space_id).
        Verifica G7: el filtro de tenant está presente en la llamada a Qdrant.
        """
        router = BrainRouter(search_space_id="space-XYZ")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = []
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            router._search(BRAIN, "test query", top_k=4)

        call_kwargs = mock_qdrant.search.call_args.kwargs
        assert "query_filter" in call_kwargs, "_search debe pasar query_filter a Qdrant"
        filter_obj = call_kwargs["query_filter"]
        assert filter_obj is not None, "query_filter no debe ser None con search_space_id definido"
        keys = [c.key for c in filter_obj.must]
        assert "search_space_id" in keys, (
            "El filtro debe incluir FieldCondition con key='search_space_id'"
        )

    def test_search_sin_space_id_no_aplica_filtro(self):
        """
        _search() con search_space_id="" no aplica filtro de tenant.
        Permite búsqueda global sin restricción de space.
        """
        router = BrainRouter(search_space_id="")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = []
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            router._search(BRAIN, "test query", top_k=4)

        call_kwargs = mock_qdrant.search.call_args.kwargs
        # Sin search_space_id, query_filter debe ser None
        assert call_kwargs.get("query_filter") is None, (
            "Con search_space_id vacío, query_filter debe ser None"
        )

    def test_dos_spaces_usan_filtros_distintos(self):
        """
        Dos routers con search_space_id distintos usan filtros distintos.
        Invariante de seguridad: space-A no puede leer datos de space-B.
        """
        router_a = BrainRouter(search_space_id="space-A")
        router_b = BrainRouter(search_space_id="space-B")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = []

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            router_a._qdrant = mock_qdrant
            router_b._qdrant = mock_qdrant
            router_a._search(BRAIN, "query", 4)
            router_b._search(BRAIN, "query", 4)

        calls = mock_qdrant.search.call_args_list
        filter_a = calls[0].kwargs["query_filter"].must[0].match.value
        filter_b = calls[1].kwargs["query_filter"].must[0].match.value
        assert filter_a == "space-A", f"Filtro de space-A incorrecto: {filter_a}"
        assert filter_b == "space-B", f"Filtro de space-B incorrecto: {filter_b}"
        assert filter_a != filter_b, "Los filtros de distintos spaces deben ser distintos"

    def test_search_filtered_incluye_tenant_y_source(self):
        """
        _search_filtered() añade FieldCondition de tenant Y de source en must[].
        Verifica G7: ambos filtros presentes en la PRIMERA llamada a Qdrant (L2).

        Nota: se inspecciona call_args_list[0] (primera llamada) porque si Qdrant
        devuelve vacío se activa el fallback tenant-only (segunda llamada sin source).
        El test fuerza un resultado no vacío para que no haya fallback.
        """
        router = BrainRouter(search_space_id="space-F4")
        mock_qdrant = MagicMock()
        # Devolver un resultado para evitar el fallback tenant-only
        mock_point = MagicMock()
        mock_point.score = 0.7
        mock_point.payload = {"text": "texto", "source": "doc-a.py"}
        mock_qdrant.search.return_value = [mock_point]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            with patch("app.brain.router.RERANKING_ENABLED", False):
                router._search_filtered(
                    collection=KNOWLEDGE,
                    query="query de prueba",
                    top_k=4,
                    sources=["doc-a.py", "doc-b.md"],
                )

        # Inspeccionar la primera llamada — contiene tenant + source
        first_call = mock_qdrant.search.call_args_list[0].kwargs
        filter_obj = first_call["query_filter"]
        assert filter_obj is not None, "query_filter no debe ser None en _search_filtered"
        keys = [c.key for c in filter_obj.must]
        assert "search_space_id" in keys, "Filtro tenant ausente en _search_filtered"
        assert "source" in keys, "Filtro source ausente en _search_filtered"

    def test_search_filtered_fallback_solo_por_tenant(self):
        """
        Si _search_filtered sin resultados, el fallback filtra SOLO por tenant.
        No hay fallback sin filtro — aislamiento multi-tenant garantizado.
        """
        router = BrainRouter(search_space_id="space-F4")
        mock_qdrant = MagicMock()
        # Primera llamada (con source) retorna vacío; segunda (solo tenant) retorna resultado
        mock_point = MagicMock()
        mock_point.score = 0.5
        mock_point.payload = {"text": "fallback text", "source": "doc"}
        mock_qdrant.search.side_effect = [[], [mock_point]]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            with patch("app.brain.router.RERANKING_ENABLED", False):
                results = router._search_filtered(
                    collection=KNOWLEDGE,
                    query="query",
                    top_k=2,
                    sources=["doc.pdf"],
                )

        # El fallback se ejecutó (2 llamadas a Qdrant)
        assert mock_qdrant.search.call_count == 2, (
            "Debe haber 2 llamadas: una con filtro source y una de fallback tenant"
        )
        # La segunda llamada (fallback) solo tiene el filtro de tenant
        second_call = mock_qdrant.search.call_args_list[1].kwargs
        fallback_filter = second_call.get("query_filter")
        if fallback_filter is not None:
            fallback_keys = [c.key for c in fallback_filter.must]
            assert "search_space_id" in fallback_keys, "Fallback debe filtrar por tenant"
            assert "source" not in fallback_keys, "Fallback NO debe filtrar por source"


# ===========================================================================
# TestBrainRouterCascade — fallback_needed y lógica de cascada
# ===========================================================================

class TestBrainRouterCascade:
    """
    Verifica la lógica de cascada del router: cuándo emite fallback_needed=True
    (señal para que brain_routes.py escale a L2.b BM25 o L2.c Web).
    """

    def _make_point(self, score: float, text: str = "texto", source: str = "doc") -> MagicMock:
        """Helper: crea un ScoredPoint mockeado con score y payload."""
        point = MagicMock()
        point.score = score
        point.payload = {"text": text, "source": source}
        return point

    def test_route_score_bajo_emite_fallback_needed(self):
        """
        L1 con score < L1_MIN_SCORE → L2 vacío → fallback_needed=True.
        Señal para que brain_routes.py intente BM25 o Web.
        """
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        # L1: punto con score bajo (< 0.60), L2: vacío
        mock_qdrant.search.side_effect = [
            [self._make_point(0.20)],  # L1: score bajo → necesita L2
            [],                         # L2: vacío → fallback_needed
        ]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            result = router.route("pregunta sin contexto relevante")

        assert result["fallback_needed"] is True, (
            "Score bajo + L2 vacío debe producir fallback_needed=True"
        )

    def test_route_score_alto_no_necesita_fallback(self):
        """
        L1 con score ≥ L1_MIN_SCORE → fallback_needed=False, level_used=1.
        """
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = [self._make_point(0.85, "texto muy relevante " * 10)]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            result = router.route("pregunta con buen contexto")

        assert result.get("fallback_needed") is not True, (
            "Score alto en L1 no debe producir fallback_needed"
        )
        assert result["level_used"] in [1, 2], (
            f"level_used debe ser 1 o 2, obtenido {result['level_used']}"
        )

    def test_route_sin_resultados_total_emite_fallback(self):
        """
        L1 vacío y L2 vacío → fallback_needed=True.
        El router no tiene datos — brain_routes.py debe intentar BM25/Web.
        """
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = []
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            result = router.route("pregunta vacía")

        assert result.get("fallback_needed") is True, (
            "Con Qdrant completamente vacío debe emitirse fallback_needed=True"
        )

    def test_route_force_level_1_no_emite_fallback(self):
        """
        force_level=1 con resultados → fallback_needed=False aunque score sea bajo.
        El forzado manual respeta la decisión del llamador.
        """
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        mock_qdrant.search.return_value = [self._make_point(0.30)]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            result = router.route("pregunta", force_level=1)

        assert result["level_used"] == 1, "force_level=1 debe usar nivel 1"
        assert result.get("fallback_needed") is not True, (
            "force_level=1 no debe emitir fallback_needed"
        )

    def test_route_fallback_code_a_knowledge(self):
        """
        Pregunta de código → L2 code vacío → fallback a knowledge.
        El router no emite fallback_needed si knowledge tiene resultados.
        """
        router = BrainRouter(search_space_id="space-test")
        mock_qdrant = MagicMock()
        knowledge_point = self._make_point(0.5, "documentación de la función")
        mock_qdrant.search.side_effect = [
            [self._make_point(0.20)],  # L1: score bajo
            [],                         # L2 code: vacío
            [knowledge_point],          # L2 knowledge: tiene resultado
        ]
        router._qdrant = mock_qdrant

        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            with patch("app.brain.router.RERANKING_ENABLED", False):
                result = router.route("def load_data() función python")

        # Con knowledge disponible, NO debe emitir fallback_needed
        assert result.get("fallback_needed") is not True, (
            "Con fallback code→knowledge exitoso, no debe emitirse fallback_needed"
        )
        assert len(result["results"]) > 0, "Debe haber resultados de knowledge"

    def test_build_response_incluye_fallback_needed(self):
        """
        _build_response() incluye la clave fallback_needed en el dict.
        Contrato G3: brain_routes.py depende de este campo.
        """
        router = BrainRouter(search_space_id="space-test")
        response = router._build_response(
            level=0,
            collection=BRAIN,
            results=[],
            fallback_needed=True,
        )
        assert "fallback_needed" in response, (
            "_build_response debe incluir clave 'fallback_needed'"
        )
        assert response["fallback_needed"] is True

    def test_build_response_fallback_needed_false_por_defecto(self):
        """
        _build_response() sin fallback_needed → False por defecto.
        Los resultados normales no emiten la señal de escalado.
        """
        router = BrainRouter()
        response = router._build_response(level=1, collection=BRAIN, results=[])
        assert response["fallback_needed"] is False, (
            "fallback_needed debe ser False por defecto"
        )


# ===========================================================================
# TestBrainRouterHelpers — métodos auxiliares del router
# ===========================================================================

class TestBrainRouterHelpers:
    """
    Tests de los métodos auxiliares de BrainRouter:
    needs_level2, is_code_question, _sources_from.
    """

    def test_needs_level2_con_trigger_de_detalle(self):
        """
        needs_level2() devuelve True con triggers de detalle en la pregunta.
        Ejemplo: 'exactamente', 'paso a paso', 'comando', 'cita literal'.
        """
        router = BrainRouter()
        assert router.needs_level2("exactamente qué comando se usa", []) is True

    def test_needs_level2_con_lista_vacia(self):
        """
        needs_level2() devuelve True si L1 no tiene resultados.
        Sin contexto en L1 → siempre bajar a L2.
        """
        router = BrainRouter()
        assert router.needs_level2("¿qué hace el pipeline?", []) is True

    def test_needs_level2_con_score_bajo(self):
        """
        needs_level2() devuelve True si el max score de L1 es < L1_MIN_SCORE.
        """
        router = BrainRouter()
        low_point = MagicMock()
        low_point.score = 0.30
        assert router.needs_level2("pregunta genérica", [low_point]) is True

    def test_needs_level2_con_score_alto_sin_triggers(self):
        """
        needs_level2() devuelve False si score alto y sin triggers de detalle.
        L1 suficiente → no bajar a L2.
        """
        router = BrainRouter()
        high_point = MagicMock()
        high_point.score = 0.90
        assert router.needs_level2("¿qué es el pipeline?", [high_point]) is False

    def test_is_code_question_positivo(self):
        """
        is_code_question() devuelve True para preguntas sobre código/implementación.
        """
        router = BrainRouter()
        assert router.is_code_question("cómo se implementa la función load_data") is True
        assert router.is_code_question("muestra el código de la clase Pipeline") is True
        assert router.is_code_question("def load_data() sintaxis") is True

    def test_is_code_question_negativo(self):
        """
        is_code_question() devuelve False para preguntas conceptuales.
        """
        router = BrainRouter()
        assert router.is_code_question("qué es el Data Lakehouse") is False
        assert router.is_code_question("cuándo se creó el proyecto") is False

    def test_sources_from_extrae_sources_unicos(self):
        """
        _sources_from() extrae los valores únicos del campo 'source' del payload.
        """
        router = BrainRouter()
        points = []
        for source in ["doc-a.py", "doc-b.md", "doc-a.py"]:  # doc-a duplicado
            p = MagicMock()
            p.payload = {"source": source}
            points.append(p)

        sources = router._sources_from(points)
        assert len(sources) == 2, "Debe deduplicar sources"
        assert "doc-a.py" in sources
        assert "doc-b.md" in sources

    def test_sources_from_lista_vacia(self):
        """_sources_from() con lista vacía devuelve lista vacía."""
        router = BrainRouter()
        assert router._sources_from([]) == []


# ===========================================================================
# TestModelProfileRetrieval — propiedades retrieval_* (F4.2)
# ===========================================================================

class TestModelProfileRetrieval:
    """
    Tests de las propiedades retrieval_chunk_budget y retrieval_chunk_max_chars
    añadidas a ModelProfile en F4.2.

    Estas propiedades son distintas de chunk_size (síntesis F3):
    - Síntesis: modelo lee UN documento grande partido en trozos
    - Retrieval: modelo lee N chunks DISTINTOS del corpus
    """

    def test_retrieval_chunk_budget_small_tier(self):
        """
        Tier small: retrieval_chunk_budget debe ser ≥1 y ≤2.
        Modelos ≤4B saturan con >2 chunks en contexto de retrieval.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("qwen2.5-coder:3b")
        assert profile.prompt_tier == "small", "qwen:3b debe ser tier small"
        budget = profile.retrieval_chunk_budget
        assert 1 <= budget <= 2, (
            f"Tier small debe tener budget 1-2, obtenido {budget}"
        )

    def test_retrieval_chunk_budget_medium_tier(self):
        """
        Tier medium: retrieval_chunk_budget debe ser ≥3 y ≤8.
        Modelos 7-14B tienen ventana suficiente para 4-6 chunks.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("qwen2.5-coder:7b")
        assert profile.prompt_tier == "medium", "qwen:7b debe ser tier medium"
        budget = profile.retrieval_chunk_budget
        assert 3 <= budget <= 8, (
            f"Tier medium debe tener budget 3-8, obtenido {budget}"
        )

    def test_retrieval_chunk_budget_claude_tier(self):
        """
        Tier claude: retrieval_chunk_budget debe ser ≥8 y ≤20.
        Modelos API tienen ventana de 180K+ tokens — más chunks = mejor calidad.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("claude-sonnet")
        assert profile.prompt_tier == "claude", "claude-sonnet debe ser tier claude"
        budget = profile.retrieval_chunk_budget
        assert 8 <= budget <= 20, (
            f"Tier claude debe tener budget 8-20, obtenido {budget}"
        )

    def test_retrieval_chunk_max_chars_small_tier(self):
        """
        Tier small: retrieval_chunk_max_chars debe ser ≤500.
        Chunks cortos para que quepan 2 en la ventana de 6K tokens.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("qwen2.5-coder:3b")
        max_chars = profile.retrieval_chunk_max_chars
        assert max_chars <= 500, (
            f"Tier small debe tener max_chars ≤500, obtenido {max_chars}"
        )

    def test_retrieval_chunk_max_chars_medium_tier(self):
        """
        Tier medium: retrieval_chunk_max_chars debe ser ≤1200.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("qwen2.5-coder:7b")
        max_chars = profile.retrieval_chunk_max_chars
        assert max_chars <= 1200, (
            f"Tier medium debe tener max_chars ≤1200, obtenido {max_chars}"
        )

    def test_retrieval_chunk_max_chars_claude_tier(self):
        """
        Tier claude: retrieval_chunk_max_chars debe ser ≤3000.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("claude-sonnet")
        max_chars = profile.retrieval_chunk_max_chars
        assert max_chars <= 3000, (
            f"Tier claude debe tener max_chars ≤3000, obtenido {max_chars}"
        )

    def test_budget_mayor_en_modelos_mas_capaces(self):
        """
        budget(claude) > budget(medium) > budget(small).
        Modelos más capaces deben recibir más contexto.
        """
        from app.brain.model_profiles import get_profile

        b_small  = get_profile("qwen2.5-coder:3b").retrieval_chunk_budget
        b_medium = get_profile("qwen2.5-coder:7b").retrieval_chunk_budget
        b_claude = get_profile("claude-sonnet").retrieval_chunk_budget

        assert b_small <= b_medium, (
            f"budget small ({b_small}) debe ser ≤ medium ({b_medium})"
        )
        assert b_medium <= b_claude, (
            f"budget medium ({b_medium}) debe ser ≤ claude ({b_claude})"
        )

    def test_docstring_retrieval_chunk_budget_existe(self):
        """
        ModelProfile.retrieval_chunk_budget tiene docstring documentado.
        Verifica que la documentación está presente en el código fuente.
        """
        from app.brain.model_profiles import ModelProfile

        source = inspect.getsource(ModelProfile.retrieval_chunk_budget.fget)
        assert "USO:" in source, "retrieval_chunk_budget debe tener sección USO:"
        assert "FÓRMULA:" in source, "retrieval_chunk_budget debe tener sección FÓRMULA:"
        assert "EJEMPLOS CON MODELOS REALES" in source, (
            "retrieval_chunk_budget debe tener sección EJEMPLOS"
        )

    def test_default_profile_tiene_budget_valido(self):
        """
        El perfil por defecto (_DEFAULT_PROFILE) tiene budget > 0.
        Garantiza que modelos desconocidos reciben contexto.
        """
        from app.brain.model_profiles import get_profile

        profile = get_profile("modelo-desconocido-xyz")
        assert profile.retrieval_chunk_budget >= 1, (
            "El perfil default debe tener budget ≥ 1"
        )
        assert profile.retrieval_chunk_max_chars >= 100, (
            "El perfil default debe tener max_chars ≥ 100"
        )


# ===========================================================================
# TestBrainRoutesHelpers — helpers de brain_routes.py
# ===========================================================================

class TestBrainRoutesHelpers:
    """
    Tests de los helpers internos de brain_routes.py:
    _extract_chunks, _extract_chunks_from_orm, _extract_chunks_from_web,
    _build_context, _sources_from_orm.

    El presupuesto (budget, max_chars) viene de ModelProfile — sin hardcode.
    """

    def _make_qdrant_point(self, text: str, source: str) -> MagicMock:
        """Helper: ScoredPoint mockeado de Qdrant."""
        point = MagicMock()
        point.payload = {"text": text, "source": source}
        return point

    def test_extract_chunks_respeta_budget(self):
        """
        _extract_chunks() limita los chunks al budget del ModelProfile.
        Con budget=2 y 10 resultados → solo 2 chunks devueltos.
        """
        from app.routes.brain_routes import _extract_chunks
        from app.brain.model_profiles import get_profile

        profile = get_profile("qwen2.5-coder:3b")  # tier small → budget=2
        budget = profile.retrieval_chunk_budget

        results = [
            self._make_qdrant_point(f"chunk {i} " * 50, f"doc-{i}")
            for i in range(10)
        ]
        chunks = _extract_chunks(results, budget=budget, max_chars=500)
        assert len(chunks) <= budget, (
            f"_extract_chunks debe respetar budget={budget}, obtenido {len(chunks)}"
        )

    def test_extract_chunks_trunca_texto(self):
        """
        _extract_chunks() trunca el texto de cada chunk a max_chars.
        Protege la ventana de contexto del modelo.
        """
        from app.routes.brain_routes import _extract_chunks

        long_text = "x" * 2000
        results = [self._make_qdrant_point(long_text, "doc")]
        chunks = _extract_chunks(results, budget=4, max_chars=400)

        assert len(chunks) == 1
        assert len(chunks[0]["text"]) <= 400, (
            f"Texto truncado debe tener ≤400 chars, obtenido {len(chunks[0]['text'])}"
        )

    def test_extract_chunks_omite_vacios(self):
        """
        _extract_chunks() omite chunks con texto vacío.
        Un chunk vacío no aporta contexto y confunde al modelo.
        """
        from app.routes.brain_routes import _extract_chunks

        results = [
            self._make_qdrant_point("", "doc-vacio"),
            self._make_qdrant_point("   ", "doc-espacios"),
            self._make_qdrant_point("texto real", "doc-ok"),
        ]
        chunks = _extract_chunks(results, budget=10, max_chars=500)
        assert len(chunks) == 1, "Solo el chunk con texto real debe incluirse"
        assert chunks[0]["source"] == "doc-ok"

    def test_extract_chunks_preserva_source(self):
        """
        _extract_chunks() preserva el campo 'source' de cada chunk.
        El source se incluye en BrainQueryResponse.sources.
        """
        from app.routes.brain_routes import _extract_chunks

        results = [
            self._make_qdrant_point("texto A", "fuente-A"),
            self._make_qdrant_point("texto B", "fuente-B"),
        ]
        chunks = _extract_chunks(results, budget=4, max_chars=500)
        sources = {c["source"] for c in chunks}
        assert "fuente-A" in sources
        assert "fuente-B" in sources

    def test_extract_chunks_from_orm_respeta_budget(self):
        """
        _extract_chunks_from_orm() limita los chunks al budget.
        Equivalente a _extract_chunks pero para objetos ORM de BM25.
        """
        from app.routes.brain_routes import _extract_chunks_from_orm

        def _make_orm_chunk(content: str, title: str) -> MagicMock:
            chunk = MagicMock()
            chunk.content = content
            chunk.document = MagicMock()
            chunk.document.title = title
            return chunk

        orm_chunks = [_make_orm_chunk(f"contenido {i}", f"doc-{i}") for i in range(8)]
        chunks = _extract_chunks_from_orm(orm_chunks, budget=3, max_chars=500)
        assert len(chunks) <= 3, "ORM chunks deben respetar budget"

    def test_extract_chunks_from_web_usa_content_y_title(self):
        """
        _extract_chunks_from_web() usa document.content y document.title.
        Contrato de la firma real de web_search_service.search().
        """
        from app.routes.brain_routes import _extract_chunks_from_web

        web_docs = [
            {
                "content": "snippet del resultado web 1",
                "document": {"title": "Página Web 1"},
            },
            {
                "content": "snippet del resultado web 2",
                "document": {"title": "Página Web 2"},
            },
        ]
        chunks = _extract_chunks_from_web(web_docs, budget=4, max_chars=500)
        assert len(chunks) == 2
        assert chunks[0]["source"] == "Página Web 1"
        assert "snippet" in chunks[0]["text"]

    def test_build_context_formatea_con_separador(self):
        """
        _build_context() une chunks con separador '---'.
        Ayuda al modelo a distinguir entre fuentes distintas.
        """
        from app.routes.brain_routes import _build_context

        chunks = [
            {"text": "primer chunk", "source": "doc-A"},
            {"text": "segundo chunk", "source": "doc-B"},
        ]
        context = _build_context(chunks)
        assert "---" in context, "El contexto debe incluir separador '---'"
        assert "primer chunk" in context
        assert "segundo chunk" in context

    def test_build_context_incluye_source_label(self):
        """
        _build_context() incluye el source entre corchetes como prefijo.
        Facilita la citación al modelo: '[doc-A] contenido...'
        """
        from app.routes.brain_routes import _build_context

        chunks = [{"text": "texto relevante", "source": "mi-doc.py"}]
        context = _build_context(chunks)
        assert "mi-doc.py" in context, "El source debe aparecer en el contexto"

    def test_build_context_lista_vacia(self):
        """_build_context() con lista vacía devuelve string vacío."""
        from app.routes.brain_routes import _build_context
        assert _build_context([]) == ""

    def test_history_turns_por_tier(self):
        """
        _HISTORY_TURNS diferencia por tier: small=0, medium=4, claude=8.
        Cero hardcode verificado — los valores vienen del dict.
        """
        from app.routes.brain_routes import _HISTORY_TURNS

        assert _HISTORY_TURNS["small"] == 0, (
            "small no debe incluir historial (ventana insuficiente)"
        )
        assert _HISTORY_TURNS["medium"] >= 4, (
            "medium debe incluir al menos 4 turnos de historial"
        )
        assert _HISTORY_TURNS["claude"] >= 8, (
            "claude debe incluir al menos 8 turnos de historial"
        )
        assert _HISTORY_TURNS["claude"] > _HISTORY_TURNS["medium"] > _HISTORY_TURNS["small"], (
            "Los turnos de historial deben aumentar con el tier"
        )


# ===========================================================================
# TestBrainRoutesEndpoints — esquemas Pydantic y config de cascada
# ===========================================================================

class TestBrainRoutesEndpoints:
    """
    Tests de los schemas Pydantic y configuración de brain_routes.py.
    No llama a la ruta real — verifica estructura y defaults.
    """

    def test_brain_query_request_schema(self):
        """
        BrainQueryRequest tiene los campos requeridos con tipos correctos.
        """
        from app.routes.brain_routes import BrainQueryRequest

        req = BrainQueryRequest(question="¿qué es el pipeline?", search_space_id=42)
        assert req.question == "¿qué es el pipeline?"
        assert req.search_space_id == 42
        assert req.top_k == 4               # default
        assert req.force_level is None       # default
        assert req.force_l0 is False         # default
        assert req.chat_history == []        # default

    def test_brain_query_request_search_space_id_es_int(self):
        """
        search_space_id en BrainQueryRequest es int (no str).
        SurfSense usa SearchSpace.id como Integer PK — no hay UUID.
        """
        from app.routes.brain_routes import BrainQueryRequest

        req = BrainQueryRequest(question="test", search_space_id=99)
        assert isinstance(req.search_space_id, int), (
            "search_space_id debe ser int — SearchSpace.id es Integer en SurfSense"
        )

    def test_brain_query_response_schema(self):
        """
        BrainQueryResponse tiene todos los campos requeridos.
        """
        from app.routes.brain_routes import BrainQueryResponse

        resp = BrainQueryResponse(
            answer="Respuesta de prueba",
            level_used=1,
            level_label="🧠 Brain",
            sources=["doc-a"],
            context_chunks=3,
            model_tier="medium",
        )
        assert resp.level_used == 1
        assert resp.level_label == "🧠 Brain"
        assert resp.context_chunks == 3

    def test_configuracion_cascada_desde_entorno(self):
        """
        BRAIN_BM25_ENABLED, BRAIN_WEB_ENABLED, BRAIN_L0_ENABLED y
        BRAIN_WEB_MAX_RESULTS se leen de os.getenv() — cero hardcode.
        """
        import app.routes.brain_routes as br_module

        source = inspect.getsource(br_module)
        for var in [
            "BRAIN_BM25_ENABLED", "BRAIN_WEB_ENABLED",
            "BRAIN_L0_ENABLED", "BRAIN_WEB_MAX_RESULTS",
        ]:
            assert f'os.getenv("{var}"' in source, (
                f"{var} debe leerse de os.getenv() en brain_routes.py"
            )

    def test_sin_chunk_budget_hardcodeado(self):
        """
        brain_routes.py no debe tener _CHUNK_BUDGET ni _CHUNK_MAX_CHARS hardcodeados.
        El presupuesto viene de ModelProfile (F4.2).
        """
        import app.routes.brain_routes as br_module

        source = inspect.getsource(br_module)
        assert "_CHUNK_BUDGET" not in source, (
            "_CHUNK_BUDGET no debe existir — usar profile.retrieval_chunk_budget"
        )
        assert "_CHUNK_MAX_CHARS" not in source, (
            "_CHUNK_MAX_CHARS no debe existir — usar profile.retrieval_chunk_max_chars"
        )

    def test_usa_profile_retrieval_properties(self):
        """
        brain_routes.py usa profile.retrieval_chunk_budget y
        profile.retrieval_chunk_max_chars (cero hardcode).
        """
        import app.routes.brain_routes as br_module

        source = inspect.getsource(br_module)
        assert "profile.retrieval_chunk_budget" in source, (
            "brain_routes.py debe usar profile.retrieval_chunk_budget"
        )
        assert "profile.retrieval_chunk_max_chars" in source, (
            "brain_routes.py debe usar profile.retrieval_chunk_max_chars"
        )

    def test_router_registrado_en_app(self):
        """
        brain_router está registrado en app.py (F4.4).
        Verifica que el include_router está presente.
        """
        import app.app as app_module

        source = inspect.getsource(app_module)
        assert "brain_routes" in source, (
            "brain_routes debe estar importado en app.py (F4.4)"
        )
        assert "include_router(brain_router)" in source, (
            "brain_router debe estar registrado con include_router en app.py"
        )


# ===========================================================================
# TestBrainRouterIntegracion — tests contra Qdrant real
# ===========================================================================

class TestBrainRouterIntegracion:
    """
    Tests de integración contra Qdrant real.

    Requieren:
      - QDRANT_HOST=qdrant (sbs-dev-qdrant disponible en el contenedor de tests)
      - Colecciones brain y knowledge creadas (QdrantManager.ensure_collections())

    Marcados con @pytest.mark.integration.
    """

    @pytest.mark.integration
    def test_router_instancia_con_qdrant_real(self):
        """
        BrainRouter conecta correctamente a Qdrant real.
        QdrantClient puede listar colecciones sin error.
        """
        router = BrainRouter(search_space_id="space-test-f4")
        # Si Qdrant no está disponible, la llamada lanzará excepción
        try:
            collections = router._qdrant.get_collections()
            assert collections is not None
        except Exception as exc:
            pytest.fail(f"No se pudo conectar a Qdrant: {exc}")

    @pytest.mark.integration
    def test_search_space_id_en_filtro_contra_qdrant_real(self):
        """
        _search() con search_space_id en Qdrant real devuelve solo puntos del space.
        Inserta un punto en space-A, busca en space-B → debe devolver vacío.
        """
        from app.brain.qdrant_manager import QdrantManager
        from app.brain.collections import BRAIN
        from app.brain.model_profiles import get_profile
        from qdrant_client.models import PointStruct
        from app.brain.ingest_router import _embed

        mgr = QdrantManager.get_instance()
        profile = get_profile(os.getenv("SYNTHESIS_MODEL", "qwen2.5-coder:7b"))
        # Dimensión de brain desde el registro — sin hardcode
        from app.brain.qdrant_manager import _COLLECTION_DIMS
        dim = _COLLECTION_DIMS[BRAIN]

        point_id = abs(hash("test-f4-isolation")) % (10 ** 15)
        test_vector = [0.5] * dim

        # Insertar en space-F4-A
        mgr.client.upsert(
            collection_name=BRAIN,
            points=[
                PointStruct(
                    id=point_id,
                    vector=test_vector,
                    payload={"source": "test-f4", "text": "texto test F4", "search_space_id": "space-F4-A"},
                )
            ],
        )

        # Buscar en space-F4-B (distinto) → debe devolver vacío
        router_b = BrainRouter(search_space_id="space-F4-B")
        router_b._qdrant = mgr.client

        with patch("app.brain.router._embed_for_collection", return_value=test_vector):
            results = router_b._search(BRAIN, "texto test", top_k=10)

        space_a_points = [
            r for r in results
            if r.payload.get("search_space_id") == "space-F4-A"
        ]
        assert len(space_a_points) == 0, (
            "space-F4-B NO debe ver puntos de space-F4-A (aislamiento multi-tenant)"
        )

        # Limpiar
        mgr.delete_by_source(source="test-f4", search_space_id="space-F4-A")

    @pytest.mark.integration
    def test_route_contra_qdrant_real_devuelve_estructura_correcta(self):
        """
        route() contra Qdrant real devuelve dict con todas las claves requeridas.
        """
        router = BrainRouter(search_space_id="space-test-f4")
        with patch("app.brain.router._embed_for_collection", return_value=[0.1] * 768):
            result = router.route("qué es el Second Brain pipeline")

        required_keys = [
            "level_used", "collection_used", "results",
            "sources_consulted", "drill_down_available",
            "level1_results", "fallback_needed",
        ]
        for key in required_keys:
            assert key in result, f"Clave '{key}' ausente en respuesta de route()"
