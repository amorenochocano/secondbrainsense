"""
crag_evaluator.py
-----------------
Agente Evaluador CRAG (Corrective RAG) para el Router Multinivel F4.

PROPÓSITO
---------
El router de F4 decide relevancia por score threshold — una heurística ciega.
Un chunk con score 0.72 puede no contener la respuesta real a la pregunta.
El evaluador CRAG añade una dimensión cualitativa:

  Sin CRAG:  "¿supera el umbral numérico?" (0.65 threshold)
  Con CRAG:  "¿este texto REALMENTE responde mi pregunta?" (LLM judge)

POSICIÓN EN LA CASCADA
-----------------------
Se inserta entre L2 (Qdrant) y L2.b (BM25), solo cuando L1+L2 devuelven
resultados pero se quiere validar antes de responder:

  L2 (Qdrant) → chunks con score ≥ threshold
                     ↓
               CRAG_EVALUATOR_ENABLED?
                     ├── false → responder directamente (F4 sin CRAG)
                     └── true  → Evaluador LLM (temperature=0, JSON)
                                      ├── es_relevante: true  → responder
                                      └── es_relevante: false → continuar a L2.b

CUÁNDO ACTIVAR
--------------
CRAG_EVALUATOR_ENABLED=false es el default. Solo activar cuando:
  - El proveedor LLM sea Claude o GPT-4 (latencia ~200ms por evaluación)
  - NO con Ollama CPU local: qwen2.5-coder:3b añade ~3-5s por evaluación,
    con 3 chunks = +15s de latencia total por consulta.

DISEÑO
------
- evaluar_chunk()  : evalúa un solo chunk, SYNC, nunca lanza excepción.
- evaluar_chunks() : evalúa hasta CRAG_MAX_EVAL_CHUNKS con early exit.
- EvaluationResult : dataclass inmutable con el resultado de cada evaluación.
- Todos los razonamientos se loguean para auditoría de decisiones.
- JSON corrupto o error LLM → EvaluationResult(es_relevante=False) — la
  cascada continúa a L2.b de forma segura.

Variables de entorno:
  CRAG_EVALUATOR_ENABLED — activar el evaluador (default: false)
  CRAG_EVALUATOR_MODEL   — modelo LLM para la evaluación (default: SYNTHESIS_MODEL)
  CRAG_MAX_EVAL_CHUNKS   — máximo de chunks a evaluar por consulta (default: 3)
  CRAG_EVAL_TIMEOUT      — timeout en segundos para cada evaluación (default: 15)
                           Referencia únicamente — LLMClient.generate() es síncrono
                           y no soporta timeout nativo; es documentación de intención.
"""
import json
import logging
import os
from dataclasses import dataclass

from app.brain.llm_client import LLMClient, SYNTHESIS_MODEL, DEFAULT_PROVIDER

logger = logging.getLogger(__name__)

# ── Configuración (cero hardcode — todo desde entorno) ────────────────────────
CRAG_EVALUATOR_ENABLED = os.getenv("CRAG_EVALUATOR_ENABLED", "false").lower() == "true"
CRAG_EVALUATOR_MODEL   = os.getenv("CRAG_EVALUATOR_MODEL",   SYNTHESIS_MODEL)
CRAG_MAX_EVAL_CHUNKS   = int(os.getenv("CRAG_MAX_EVAL_CHUNKS", "3"))
CRAG_EVAL_TIMEOUT      = int(os.getenv("CRAG_EVAL_TIMEOUT",   "15"))

# Longitud máxima del fragmento enviado al evaluador.
# Protege la ventana de contexto del modelo evaluador (puede ser más pequeño
# que el modelo de síntesis, ej: claude-3-haiku vs claude-sonnet).
_MAX_FRAGMENT_CHARS = 3000


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvaluationResult:
    """
    Resultado inmutable de la evaluación CRAG de un chunk.

    Campos:
        es_relevante:  True si el chunk contiene información suficiente para
                       responder la pregunta. False en cualquier caso de duda,
                       error o JSON inválido (fail-safe).
        razonamiento:  Explicación del LLM de 1-2 frases. Siempre presente —
                       se loguea para auditoría de decisiones y debugging.
        source:        Identificador del chunk evaluado (slug del documento).
                       Facilita correlacionar la evaluación con el resultado.
    """
    es_relevante: bool
    razonamiento: str
    source:       str = ""


# ── Prompts del evaluador ─────────────────────────────────────────────────────

_EVAL_SYSTEM = (
    "Eres un evaluador experto de relevancia documental. "
    "Determina si el fragmento contiene información suficiente para responder la pregunta.\n\n"
    "Responde ÚNICAMENTE con JSON válido:\n"
    '{"razonamiento": "análisis en 1-2 frases", "es_relevante": true|false}\n\n'
    "En caso de duda: false. "
    "Buscar más contexto es más seguro que responder con información incorrecta."
)

_EVAL_PROMPT_TEMPLATE = (
    "<pregunta_usuario>\n"
    "{pregunta}\n"
    "</pregunta_usuario>\n\n"
    "<documento_recuperado>\n"
    "{fragmento}\n"
    "</documento_recuperado>\n\n"
    "¿El documento recuperado responde la pregunta? Responde en JSON."
)


# ── API pública ───────────────────────────────────────────────────────────────

def evaluar_chunk(pregunta: str, fragmento: str, source: str = "") -> EvaluationResult:
    """
    Evalúa si un chunk de Qdrant responde la pregunta del usuario.

    Llama al LLM evaluador (CRAG_EVALUATOR_MODEL) con temperatura 0 y
    espera un JSON estructurado. Nunca lanza excepción — cualquier fallo
    produce EvaluationResult(es_relevante=False) para que la cascada
    continúe de forma segura hacia L2.b (BM25) o L2.c (Web).

    El razonamiento del LLM se loguea siempre (INFO) para auditoría —
    visible en `docker compose logs surfsense-backend | grep CRAG`.

    Args:
        pregunta:   Pregunta original del usuario (sin modificar).
        fragmento:  Texto del chunk a evaluar. Se trunca a _MAX_FRAGMENT_CHARS
                    para proteger la ventana del modelo evaluador.
        source:     Identificador del documento de origen (ej: "pipeline-etl").
                    Solo para logging y trazabilidad — no afecta la evaluación.

    Returns:
        EvaluationResult con es_relevante, razonamiento y source.
        es_relevante=False en cualquier caso de error (fail-safe).

    Nota sobre CRAG_EVAL_TIMEOUT:
        LLMClient.generate() es síncrono y no soporta timeout nativo.
        CRAG_EVAL_TIMEOUT es una referencia documentada de la intención —
        no un timeout real aplicado. Para timeout real se necesitaría
        ejecutar en ThreadPoolExecutor con futures.wait(timeout=...).
    """
    logger.debug(
        "[CRAG] evaluar_chunk START source=%r pregunta=%r fragmento_chars=%d model=%s",
        source, pregunta[:60], len(fragmento), CRAG_EVALUATOR_MODEL,
    )

    client = LLMClient()
    prompt = _EVAL_PROMPT_TEMPLATE.format(
        pregunta=pregunta.strip(),
        fragmento=fragmento.strip()[:_MAX_FRAGMENT_CHARS],
    )

    try:
        raw = client.generate(
            prompt=prompt,
            system=_EVAL_SYSTEM,
            provider=DEFAULT_PROVIDER,
            model=CRAG_EVALUATOR_MODEL,
        )

        # Algunos modelos envuelven el JSON en ```json ... ```
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        parsed = json.loads(cleaned)

        if "es_relevante" not in parsed or "razonamiento" not in parsed:
            raise KeyError(f"Claves JSON incorrectas: {list(parsed.keys())}")

        result = EvaluationResult(
            es_relevante=bool(parsed["es_relevante"]),
            razonamiento=str(parsed["razonamiento"]),
            source=source,
        )
        # Log de auditoría — cada decisión del evaluador queda registrada
        logger.info(
            "[CRAG] DECISION source=%r es_relevante=%s razonamiento=%r",
            source, result.es_relevante, result.razonamiento,
        )
        return result

    except json.JSONDecodeError as exc:
        logger.warning(
            "[CRAG] JSONDecodeError source=%r raw=%r: %s → fallback False",
            source, raw[:100] if "raw" in dir() else "?", exc,
        )
        return EvaluationResult(
            es_relevante=False,
            razonamiento=f"JSON inválido: {exc}",
            source=source,
        )
    except KeyError as exc:
        logger.warning(
            "[CRAG] Claves incorrectas source=%r: %s → fallback False",
            source, exc,
        )
        return EvaluationResult(
            es_relevante=False,
            razonamiento=f"Claves inválidas: {exc}",
            source=source,
        )
    except Exception as exc:
        logger.error(
            "[CRAG] Error inesperado source=%r: %s → fallback False",
            source, exc, exc_info=True,
        )
        return EvaluationResult(
            es_relevante=False,
            razonamiento=f"Error: {type(exc).__name__}",
            source=source,
        )


def evaluar_chunks(
    pregunta: str,
    chunks: list[dict],
) -> tuple[bool, list[EvaluationResult]]:
    """
    Evalúa hasta CRAG_MAX_EVAL_CHUNKS chunks en orden de score descendente.

    Implementa early exit: si el primer chunk relevante es encontrado, detiene
    la evaluación inmediatamente — evita llamadas LLM innecesarias.

    Los chunks se esperan en el formato de _extract_chunks():
      [{"text": str, "source": str}, ...]

    Args:
        pregunta: Pregunta original del usuario.
        chunks:   Lista de dicts con "text" y "source". Se evalúan en orden,
                  máximo CRAG_MAX_EVAL_CHUNKS (default: 3 desde .env).

    Returns:
        Tuple (hay_relevante: bool, evaluaciones: list[EvaluationResult])
          hay_relevante: True si al menos un chunk es relevante.
          evaluaciones:  Lista de resultados — puede ser menor que len(chunks)
                         si hay early exit en el primer chunk relevante.

    Ejemplo de uso en brain_routes.py:
        hay_relevante, evals = evaluar_chunks(req.question, chunks)
        if hay_relevante:
            chunks_ok = [chunks[i] for i, ev in enumerate(evals) if ev.es_relevante]
            context = _build_context(chunks_ok or chunks)
            # → responder con chunks validados
        else:
            # → escalar a L2.b BM25
    """
    logger.info(
        "[CRAG] evaluar_chunks START pregunta=%r n_chunks=%d max_eval=%d model=%s",
        pregunta[:60], len(chunks), CRAG_MAX_EVAL_CHUNKS, CRAG_EVALUATOR_MODEL,
    )

    evaluaciones: list[EvaluationResult] = []

    for i, chunk in enumerate(chunks[:CRAG_MAX_EVAL_CHUNKS]):
        result = evaluar_chunk(
            pregunta=pregunta,
            fragmento=chunk.get("text", ""),
            source=chunk.get("source", ""),
        )
        evaluaciones.append(result)

        if result.es_relevante:
            logger.info(
                "[CRAG] EARLY EXIT en chunk %d/%d source=%r — es_relevante=True",
                i + 1, min(len(chunks), CRAG_MAX_EVAL_CHUNKS), result.source,
            )
            return True, evaluaciones

    # Ningún chunk relevante encontrado
    logger.info(
        "[CRAG] NINGÚN chunk relevante tras evaluar %d/%d chunks → escalar a L2.b",
        len(evaluaciones), min(len(chunks), CRAG_MAX_EVAL_CHUNKS),
    )
    return False, evaluaciones
