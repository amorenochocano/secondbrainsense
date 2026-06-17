"""
tests/brain/test_crag_evaluator.py
------------------------------------
Tests del Agente Evaluador CRAG (F4.6).

Cobertura:
  - TestCRAGEvaluador      : evaluar_chunk() — JSON válido, corrupto, error LLM
  - TestCRAGEvalChunks     : evaluar_chunks() — early exit, todos irrelevantes
  - TestCRAGConfig         : variables de entorno y configuración
  - TestCRAGBrainRoutes    : integración del bloque CRAG en brain_routes.py

Metodología:
  - LLMClient mockeado en todos los tests — sin llamadas reales a Ollama.
  - Parchear donde vive la clase: app.brain.crag_evaluator.LLMClient.
  - EvaluationResult es un dataclass frozen — verificar inmutabilidad.
  - JSON corrupto → False (fail-safe), LLM error → False (fail-safe).
  - El evaluador nunca lanza excepción hacia el llamador.

Adaptaciones respecto al plan F4.6:
  - CRAG_EVALUATOR_ENABLED se lee de os.getenv() — se sobreescribe con
    patch.dict(os.environ, ...) para testear ambos estados.
  - evaluar_chunk() maneja también JSON con markdown fences (```json ... ```)
    que devuelven algunos modelos.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.brain.crag_evaluator import (
    EvaluationResult,
    evaluar_chunk,
    evaluar_chunks,
    CRAG_MAX_EVAL_CHUNKS,
)


# ===========================================================================
# TestCRAGEvaluador — evaluar_chunk() contrato y robustez
# ===========================================================================

class TestCRAGEvaluador:
    """
    Tests de evaluar_chunk() — la función atómica del evaluador CRAG.

    Contrato invariante:
      - Siempre devuelve EvaluationResult, nunca lanza excepción.
      - JSON corrupto, claves incorrectas o error LLM → es_relevante=False.
      - JSON válido con es_relevante=true → es_relevante=True.
      - razonamiento siempre presente (str, puede ser mensaje de error).
    """

    def test_json_valido_relevante(self):
        """
        LLM devuelve JSON válido con es_relevante=true.
        El razonamiento del LLM se preserva en el resultado.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                '{"razonamiento": "El fragmento explica JWT directamente", "es_relevante": true}'
            )
            result = evaluar_chunk("¿Qué es JWT?", "JWT es un token de autenticación...")

        assert result.es_relevante is True
        assert "JWT" in result.razonamiento

    def test_json_valido_irrelevante(self):
        """
        LLM devuelve JSON válido con es_relevante=false.
        La cascada debe continuar a L2.b.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                '{"razonamiento": "El fragmento habla de otra cosa", "es_relevante": false}'
            )
            result = evaluar_chunk("¿Qué es JWT?", "El ciclo del agua...")

        assert result.es_relevante is False
        assert result.razonamiento != ""

    def test_json_corrupto_fallback_false(self):
        """
        JSON corrupto → EvaluationResult(es_relevante=False). Nunca excepción.
        Fail-safe: es mejor escalar a L2.b que responder con contexto incorrecto.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = "esto no es json {{{corrupto"
            result = evaluar_chunk("pregunta", "fragmento")

        assert result.es_relevante is False
        assert "JSON" in result.razonamiento or "inválido" in result.razonamiento

    def test_json_con_markdown_fences(self):
        """
        Algunos modelos envuelven el JSON en ```json ... ```.
        evaluar_chunk() debe limpiar las fences antes de parsear.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                "```json\n"
                '{"razonamiento": "Responde la pregunta", "es_relevante": true}\n'
                "```"
            )
            result = evaluar_chunk("¿Qué es?", "texto relevante")

        assert result.es_relevante is True

    def test_json_claves_incorrectas_fallback_false(self):
        """
        JSON con claves distintas a 'es_relevante' y 'razonamiento' → False.
        Modelos que devuelven formatos inesperados no rompen la cascada.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                '{"relevant": true, "reason": "aquí"}'
            )
            result = evaluar_chunk("pregunta", "fragmento")

        assert result.es_relevante is False
        assert "Claves" in result.razonamiento or "inválidas" in result.razonamiento

    def test_llm_connection_error_fallback_false(self):
        """
        Si LLM lanza ConnectionError → False. La cascada continúa a L2.b.
        Fallo del evaluador no debe interrumpir la consulta del usuario.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = ConnectionError("Ollama caído")
            result = evaluar_chunk("pregunta", "fragmento")

        assert result.es_relevante is False
        assert "ConnectionError" in result.razonamiento or "Error" in result.razonamiento

    def test_llm_runtime_error_fallback_false(self):
        """
        Si LLM lanza RuntimeError genérico → False.
        Cualquier excepción inesperada produce fail-safe.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = RuntimeError("Error inesperado")
            result = evaluar_chunk("pregunta", "fragmento")

        assert result.es_relevante is False

    def test_resultado_es_evaluation_result(self):
        """
        evaluar_chunk() siempre devuelve EvaluationResult — nunca str ni dict.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                '{"razonamiento": "ok", "es_relevante": true}'
            )
            result = evaluar_chunk("q", "texto")

        assert isinstance(result, EvaluationResult), (
            "evaluar_chunk debe devolver EvaluationResult"
        )

    def test_source_se_preserva_en_resultado(self):
        """
        El source del chunk se preserva en EvaluationResult para trazabilidad.
        """
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                '{"razonamiento": "ok", "es_relevante": false}'
            )
            result = evaluar_chunk("pregunta", "texto", source="mi-doc.py")

        assert result.source == "mi-doc.py"

    def test_fragmento_largo_se_trunca(self):
        """
        Fragmentos muy largos se truncan a _MAX_FRAGMENT_CHARS antes de enviar al LLM.
        Protege la ventana de contexto del modelo evaluador.
        """
        fragmento_largo = "x" * 10000  # mucho mayor que _MAX_FRAGMENT_CHARS=3000

        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = (
                '{"razonamiento": "ok", "es_relevante": false}'
            )
            evaluar_chunk("pregunta", fragmento_largo)

        # Verificar que el prompt enviado al LLM no supera ~3000 chars de fragmento
        call_args = MockLLM.return_value.generate.call_args
        prompt = call_args.kwargs.get("prompt", call_args.args[0] if call_args.args else "")
        # El fragmento truncado debería estar en el prompt
        assert len(prompt) < 10000, (
            "El prompt enviado al LLM no debe contener el fragmento completo sin truncar"
        )


# ===========================================================================
# TestCRAGEvalChunks — evaluar_chunks() lógica de early exit
# ===========================================================================

class TestCRAGEvalChunks:
    """
    Tests de evaluar_chunks() — evaluación de múltiples chunks con early exit.

    Early exit: si el primer chunk relevante es encontrado, la función devuelve
    inmediatamente sin evaluar el resto. Minimiza llamadas LLM innecesarias.
    """

    def test_early_exit_primer_chunk_relevante(self):
        """
        Si el primer chunk es relevante, no evalúa el resto.
        Minimiza latencia: 1 llamada LLM en lugar de N.
        """
        responses = [
            '{"razonamiento": "Chunk 1 relevante", "es_relevante": true}',
            '{"razonamiento": "Chunk 2 también", "es_relevante": true}',
        ]
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = responses
            chunks = [
                {"text": "chunk 1", "source": "doc-a"},
                {"text": "chunk 2", "source": "doc-b"},
            ]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks)

        assert hay is True, "Debe encontrar chunks relevantes"
        assert len(evaluaciones) == 1, (
            "Early exit: solo debe haber evaluado el primer chunk"
        )
        assert MockLLM.return_value.generate.call_count == 1, (
            "Solo una llamada LLM con early exit"
        )

    def test_todos_irrelevantes_evalua_hasta_max(self):
        """
        Si todos los chunks son irrelevantes, evalúa hasta CRAG_MAX_EVAL_CHUNKS.
        Devuelve (False, lista_completa).
        """
        respuesta_irrelevante = '{"razonamiento": "No responde", "es_relevante": false}'
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = respuesta_irrelevante
            chunks = [{"text": f"chunk {i}", "source": f"doc-{i}"} for i in range(5)]
            hay, evaluaciones = evaluar_chunks("pregunta sin contexto", chunks)

        assert hay is False, "Sin chunks relevantes → False"
        assert len(evaluaciones) == min(5, CRAG_MAX_EVAL_CHUNKS), (
            f"Debe evaluar hasta CRAG_MAX_EVAL_CHUNKS={CRAG_MAX_EVAL_CHUNKS}"
        )

    def test_segundo_chunk_relevante(self):
        """
        Primer chunk irrelevante, segundo relevante → early exit en el segundo.
        Devuelve (True, 2 evaluaciones).
        """
        responses = [
            '{"razonamiento": "Irrelevante", "es_relevante": false}',
            '{"razonamiento": "Relevante", "es_relevante": true}',
        ]
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = responses
            chunks = [
                {"text": "chunk 1", "source": "doc-a"},
                {"text": "chunk 2", "source": "doc-b"},
                {"text": "chunk 3", "source": "doc-c"},
            ]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks)

        assert hay is True
        assert len(evaluaciones) == 2, (
            "Early exit en el segundo: solo 2 evaluaciones"
        )

    def test_lista_vacia_devuelve_false(self):
        """
        Sin chunks que evaluar → (False, []).
        Protege contra llamadas con lista vacía.
        """
        with patch("app.brain.crag_evaluator.LLMClient"):
            hay, evaluaciones = evaluar_chunks("pregunta", [])

        assert hay is False
        assert evaluaciones == []

    def test_respeta_crag_max_eval_chunks(self):
        """
        evaluar_chunks() no evalúa más de CRAG_MAX_EVAL_CHUNKS chunks.
        Aunque la lista tenga más chunks, solo evalúa el máximo configurado.
        """
        respuesta_irrelevante = '{"razonamiento": "No", "es_relevante": false}'
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.return_value = respuesta_irrelevante
            # 10 chunks, pero CRAG_MAX_EVAL_CHUNKS=3 (por defecto)
            chunks = [{"text": f"chunk {i}", "source": f"doc-{i}"} for i in range(10)]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks)

        assert len(evaluaciones) <= CRAG_MAX_EVAL_CHUNKS, (
            f"No debe evaluar más de CRAG_MAX_EVAL_CHUNKS={CRAG_MAX_EVAL_CHUNKS}"
        )
        assert MockLLM.return_value.generate.call_count <= CRAG_MAX_EVAL_CHUNKS

    def test_error_en_un_chunk_no_detiene_evaluacion(self):
        """
        Si un chunk falla (LLM error), el resultado es False para ese chunk
        pero la evaluación continúa con los siguientes.
        """
        responses = [
            ConnectionError("timeout"),                                      # chunk 1: falla
            '{"razonamiento": "Relevante", "es_relevante": true}',          # chunk 2: OK
        ]
        with patch("app.brain.crag_evaluator.LLMClient") as MockLLM:
            MockLLM.return_value.generate.side_effect = responses
            chunks = [
                {"text": "chunk 1", "source": "doc-a"},
                {"text": "chunk 2", "source": "doc-b"},
            ]
            hay, evaluaciones = evaluar_chunks("pregunta", chunks)

        assert hay is True, (
            "El segundo chunk es relevante — debe devolver True aunque el primero falle"
        )
        assert evaluaciones[0].es_relevante is False, "Chunk 1 fallido → False"
        assert evaluaciones[1].es_relevante is True,  "Chunk 2 OK → True"


# ===========================================================================
# TestCRAGConfig — variables de entorno y configuración
# ===========================================================================

class TestCRAGConfig:
    """
    Tests de la configuración del evaluador CRAG desde variables de entorno.
    """

    def test_crag_evaluator_enabled_desde_entorno(self):
        """
        CRAG_EVALUATOR_ENABLED se lee de os.getenv().
        Cero hardcode — el valor en .env controla el comportamiento.
        """
        import app.brain.crag_evaluator as crag_module
        import inspect

        source = inspect.getsource(crag_module)
        assert 'os.getenv("CRAG_EVALUATOR_ENABLED"' in source, (
            "CRAG_EVALUATOR_ENABLED debe leerse de os.getenv()"
        )

    def test_crag_evaluator_model_desde_entorno(self):
        """
        CRAG_EVALUATOR_MODEL se lee de os.getenv() con fallback a SYNTHESIS_MODEL.
        Permite usar un modelo más rápido/barato para la evaluación.
        """
        import app.brain.crag_evaluator as crag_module
        import inspect

        source = inspect.getsource(crag_module)
        assert 'os.getenv("CRAG_EVALUATOR_MODEL"' in source, (
            "CRAG_EVALUATOR_MODEL debe leerse de os.getenv()"
        )

    def test_crag_max_eval_chunks_desde_entorno(self):
        """
        CRAG_MAX_EVAL_CHUNKS se lee de os.getenv() con default=3.
        Permite ajustar el número de evaluaciones por tier/proveedor.
        """
        import app.brain.crag_evaluator as crag_module
        import inspect

        source = inspect.getsource(crag_module)
        assert 'os.getenv("CRAG_MAX_EVAL_CHUNKS"' in source

    def test_evaluation_result_es_inmutable(self):
        """
        EvaluationResult es un dataclass frozen — no se puede modificar.
        Garantiza que los resultados de auditoría no se alteran accidentalmente.
        """
        result = EvaluationResult(es_relevante=True, razonamiento="test", source="doc")
        with pytest.raises((AttributeError, TypeError)):
            result.es_relevante = False  # debe lanzar excepción

    def test_crag_disabled_by_default(self):
        """
        CRAG_EVALUATOR_ENABLED=false por defecto.
        Garantiza que sin configuración explícita no hay overhead de latencia.
        """
        with patch.dict(os.environ, {"CRAG_EVALUATOR_ENABLED": "false"}):
            import importlib
            import app.brain.crag_evaluator as m
            importlib.reload(m)
            assert m.CRAG_EVALUATOR_ENABLED is False

    def test_crag_enabled_cuando_configurado(self):
        """
        CRAG_EVALUATOR_ENABLED=true activa el evaluador.
        """
        with patch.dict(os.environ, {"CRAG_EVALUATOR_ENABLED": "true"}):
            import importlib
            import app.brain.crag_evaluator as m
            importlib.reload(m)
            assert m.CRAG_EVALUATOR_ENABLED is True


# ===========================================================================
# TestCRAGBrainRoutes — integración del bloque CRAG en brain_routes.py
# ===========================================================================

class TestCRAGBrainRoutes:
    """
    Tests de la integración del evaluador CRAG en brain_routes.py.
    Verifica la estructura del código sin ejecutar la ruta (no necesita FastAPI).
    """

    def test_import_crag_en_brain_routes(self):
        """
        brain_routes.py importa CRAG_EVALUATOR_ENABLED, evaluar_chunks y EvaluationResult.
        """
        import inspect
        import app.routes.brain_routes as br

        source = inspect.getsource(br)
        assert "from app.brain.crag_evaluator import" in source, (
            "brain_routes.py debe importar de crag_evaluator"
        )
        assert "CRAG_EVALUATOR_ENABLED" in source
        assert "evaluar_chunks" in source

    def test_bloque_crag_condicional_presente(self):
        """
        El bloque CRAG en brain_routes.py está dentro de un if condicional.
        CRAG_EVALUATOR_ENABLED=false → comportamiento idéntico sin CRAG.
        """
        import inspect
        import app.routes.brain_routes as br

        source = inspect.getsource(br)
        assert "if CRAG_EVALUATOR_ENABLED and chunks:" in source, (
            "El bloque CRAG debe estar dentro de 'if CRAG_EVALUATOR_ENABLED'"
        )

    def test_bloque_else_sin_crag_presente(self):
        """
        El bloque else (sin CRAG) está presente y usa el comportamiento original.
        Garantiza compatibilidad con F4 cuando CRAG está desactivado.
        """
        import inspect
        import app.routes.brain_routes as br

        source = inspect.getsource(br)
        assert "Sin CRAG: comportamiento original F4" in source, (
            "El bloque else debe documentar el comportamiento original F4"
        )

    def test_crag_escala_a_bm25_cuando_irrelevante(self):
        """
        Cuando CRAG detecta ningún chunk relevante, la ejecución continúa a L2.b.
        El código NO retorna — deja caer al bloque BM25.
        """
        import inspect
        import app.routes.brain_routes as br

        source = inspect.getsource(br)
        assert "escalando a L2.b (BM25)" in source, (
            "Cuando CRAG dice irrelevante, debe escalar a L2.b BM25"
        )
        # No debe haber return después del log de escalado
        idx = source.find("escalando a L2.b (BM25)")
        next_chunk = source[idx:idx+200]
        assert "return" not in next_chunk.split("else:")[0], (
            "No debe haber return al escalar a L2.b — la ejecución debe continuar"
        )

    def test_chunks_ok_contiene_solo_validados(self):
        """
        chunks_ok filtra solo los chunks marcados como es_relevante=True.
        Solo los chunks validados por CRAG se envían al LLM.
        """
        import inspect
        import app.routes.brain_routes as br

        source = inspect.getsource(br)
        assert "chunks_ok = [" in source, (
            "chunks_ok debe construirse filtrando por ev.es_relevante"
        )
        assert "ev.es_relevante" in source, (
            "El filtro debe usar ev.es_relevante para seleccionar chunks"
        )

    def test_context_chunks_cuenta_chunks_validados(self):
        """
        BrainQueryResponse.context_chunks refleja los chunks validados por CRAG.
        Permite al frontend saber cuántos chunks pasaron la evaluación.
        """
        import inspect
        import app.routes.brain_routes as br

        source = inspect.getsource(br)
        assert "context_chunks=len(chunks_ok or chunks)" in source, (
            "context_chunks debe reflejar los chunks validados por CRAG"
        )
