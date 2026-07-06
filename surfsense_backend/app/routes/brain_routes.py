"""
brain_routes.py
---------------
Router FastAPI para consultas al Second Brain — cascada multinivel.

Endpoints principales:
  POST /api/v1/brain/query              — Consulta con cascada L1→L2 Hybrid→Web→L0
  GET  /api/v1/brain/passport/{source}  — Lee el pasaporte .md de un documento
  DELETE /api/v1/brain/document/{source} — Elimina .md + vectores Qdrant

Cascada de retrieval (3 niveles + fallbacks):
  L1  : Brain — pasaportes semánticos, colección Qdrant 'brain'.
        Síntesis de alto nivel de cada documento. Solo recuperación semántica.

  L2  : Knowledge Hybrid — recuperación HÍBRIDA en dos patas:
          • Qdrant semántico: colección 'knowledge' (docs/PDFs/md) o 'code'
            (código fuente). BrainRouter selecciona la colección según el tipo
            de la pregunta.
          • BM25 PostgreSQL: full_text_search() via ChucksHybridSearchRetriever.
            Captura coincidencias léxicas exactas que el vector pierde.
        Ambas patas aportan el doble del budget de candidatos. La fusión usa
        RRF (Reciprocal Rank Fusion, Cormack 2009, k=60): combina listas por
        posición sin calibrar pesos. Documentos bien posicionados en ambas
        listas reciben mayor score final.
        Controlado por BRAIN_BM25_ENABLED (default: true).

  [CRAG]: Evaluador cualitativo opcional post-L2 (crag_evaluator.py).
          Solo recomendado con Claude/GPT-4. Con Ollama añade ~3-5 s por chunk.
          Si CRAG rechaza todos los chunks → escala directamente a Web/L0.

  Web : Búsqueda SearXNG en tiempo real cuando L1 y L2-hybrid no tienen
        contexto relevante. Query rewriting via LLM (keywords optimizadas).
        Controlado por BRAIN_WEB_ENABLED (default: true).

  L0  : LLM libre — conocimiento paramétrico sin retrieval. Último recurso.
        Controlado por BRAIN_L0_ENABLED (default: true).

Separación de concerns:
  - BrainRouter (síncrono): gestiona L1 (brain) y la parte Qdrant de L2.
  - brain_routes.py (async FastAPI): gestiona la parte BM25 del hybrid L2,
    la fusión RRF, Web y L0 — requieren DB session/web service.
  - crag_evaluator.py: evaluación cualitativa post-L2 (si activado).
  - _rrf_fuse(): helper de fusión RRF entre Qdrant y BM25.

Variables de entorno (cero hardcode):
  BRAIN_BM25_ENABLED     — activa la pata BM25 del hybrid L2 (default: true)
  BRAIN_WEB_ENABLED      — activa búsqueda web como fallback (default: true)
  BRAIN_L0_ENABLED       — permite L0 libre como último recurso (default: true)
  BRAIN_WEB_MAX_RESULTS  — máximo de resultados web (default: 5)
  CRAG_EVALUATOR_ENABLED — evaluador CRAG post-L2 (default: false)
  SYNTHESIS_MODEL        — modelo LLM activo (leído de llm_client.py)
  LLM_PROVIDER           — proveedor LLM activo (leído de llm_client.py)
"""
import asyncio
import datetime
import logging
import os
import time
import uuid

import redis as redis_lib
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client import models as qdrant_models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.qdrant_manager import QdrantManager
from app.brain.router import BrainRouter
from app.brain.llm_client import LLMClient, SYNTHESIS_MODEL, DEFAULT_PROVIDER
from app.brain.model_profiles import get_profile
from app.brain.crag_evaluator import (
    CRAG_EVALUATOR_ENABLED,
    evaluar_chunks,
    EvaluationResult,
)
from app.db import (
    User,
    get_async_session,
    BrainDomain,
    BrainDocType,
    BrainEntityHint,
    BrainVocabulary,
)
from app.users import current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/brain", tags=["brain"])

# ── Configuración de la cascada (cero hardcode — todo desde entorno) ──────────
BRAIN_BM25_ENABLED    = os.getenv("BRAIN_BM25_ENABLED",    "true").lower() == "true"
BRAIN_WEB_ENABLED     = os.getenv("BRAIN_WEB_ENABLED",     "true").lower() == "true"
BRAIN_L0_ENABLED      = os.getenv("BRAIN_L0_ENABLED",      "true").lower() == "true"
BRAIN_WEB_MAX_RESULTS = int(os.getenv("BRAIN_WEB_MAX_RESULTS", "5"))

# NOTA sobre presupuesto de contexto (chunk_budget y chunk_max_chars):
# Los valores NO se definen aquí como constantes hardcodeadas.
# Se derivan en cada petición desde ModelProfile.retrieval_chunk_budget y
# ModelProfile.retrieval_chunk_max_chars, calculados desde el context_tokens
# del modelo activo (SYNTHESIS_MODEL en .env).
#
# Al cambiar de modelo el presupuesto se adapta automáticamente:
#   qwen2.5-coder:3b  (small)  → budget=2,  max_chars=500
#   qwen2.5-coder:7b  (medium) → budget=6,  max_chars=1200
#   claude-sonnet     (claude) → budget=15, max_chars=3000
#
# Ver ModelProfile.retrieval_chunk_budget y retrieval_chunk_max_chars
# en app/brain/model_profiles.py para la documentación completa.

# ── Historial de conversación por tier ───────────────────────────────────────
# Controla cuántos turnos anteriores (user+assistant) se incluyen en el prompt.
#
# POR QUÉ VARÍA POR TIER:
#   small  → 0 turnos: modelos ≤4B tienen ventana de ~6K tokens. Con contexto
#             RAG ya incluido, no queda espacio para historial sin degradar la
#             respuesta. El modelo priorizará el historial sobre el contexto.
#   medium → 4 turnos: ventana ~28K. Suficiente para incluir 4 intercambios
#             previos (~800 tokens) sin comprometer el contexto RAG.
#   claude → 8 turnos: ventana ~180K. Sin restricción práctica. 8 turnos
#             permiten conversaciones largas con memoria contextual completa.
#
# CASO DE USO:
#   Se usa en _call_llm() para truncar req.chat_history antes de concatenarlo
#   al prompt. history[-N:] toma los N turnos más recientes.
#   Un turno = 1 mensaje user + 1 mensaje assistant = 2 entradas en la lista.
_HISTORY_TURNS: dict[str, int] = {
    "small":  0,   # sin historial — ventana insuficiente
    "medium": 4,   # 4 turnos recientes (~800 tokens)
    "claude": 8,   # 8 turnos recientes — memoria larga
}


# ── Schemas Pydantic ──────────────────────────────────────────────────────────

class BrainQueryRequest(BaseModel):
    """
    Petición de consulta al Brain con cascada multinivel.

    Campos:
        question:
            Pregunta en lenguaje natural del usuario.
            Ejemplo: "¿Cómo se configura el pipeline de Fabric?"
            Se usa directamente en L1/L2 Qdrant y como base para el query
            rewriting de L2.c (web). Longitud recomendada: ≤80 chars para
            modelos small, sin límite práctico para medium/claude.

        search_space_id:
            ID entero del search space (SearchSpace.id en PostgreSQL).
            Garantiza aislamiento multi-tenant: BrainRouter lo inyecta como
            FieldCondition en todos los filtros Qdrant para que un usuario
            no vea chunks de otro space.
            Ejemplo: 42

        chat_history:
            Historial de conversación para contexto multi-turno.
            Lista de dicts {"role": "user"|"assistant", "content": str}.
            Se trunca a _HISTORY_TURNS[tier] entradas antes de incluirlo
            en el prompt para no saturar la ventana del modelo.
            Ejemplo: [{"role": "user", "content": "qué es el pipeline?"},
                      {"role": "assistant", "content": "El pipeline..."}]
            Dejar [] en la primera pregunta de una conversación.

        top_k:
            Número de chunks a recuperar en L1/L2 Qdrant por búsqueda vectorial.
            NO es el número final de chunks en el prompt — ese lo controla
            ModelProfile.retrieval_chunk_budget.
            top_k > budget: Qdrant devuelve más candidatos, el reranker
            cross-encoder selecciona los mejores hasta 'budget'.
            Valor recomendado: 4-6. No subir de 10 en modelos small.

        force_level:
            Fuerza un nivel específico del router Qdrant, saltando el
            enrutamiento automático (score + triggers de detalle).
            1 → coleción 'brain' (pasaportes semánticos)
            2 → colección 'knowledge' o 'code' (chunks raw)
            None → enrutamiento automático (recomendado)
            Usar para depuración o cuando se conoce exactamente qué
            nivel contiene la información buscada.

        force_l0:
            True → salta TODA la cascada (L1, L2, BM25, Web) y llama al
            LLM directamente sin contexto (modo parametric).
            Útil para preguntas generales donde el corpus no aporta valor,
            o para comparar calidad RAG vs. conocimiento parametrico.
            False (default) → ejecuta la cascada completa.
    """
    question:        str
    search_space_id: int
    chat_history:    list[dict] = []
    top_k:           int = 4
    force_level:     int | None = None
    force_l0:        bool = False


class BrainQueryResponse(BaseModel):
    """
    Respuesta de la cascada de consulta al Brain.

    Campos:
        answer:
            Respuesta generada por el LLM. Puede incluir citas de fuente
            si el system prompt las solicita (nivel L1/L2/BM25/Web).
            En L0 (sin contexto), la respuesta viene del conocimiento
            paramétrico del modelo con un disclaimer explícito.

        level_used:
            Nivel de la cascada que produjo el contexto para la respuesta.
            1 → Brain      — pasaportes semánticos (colección Qdrant 'brain')
            2 → Knowledge  — hybrid Qdrant (knowledge/code) + BM25, fusión RRF
            4 → Web        — búsqueda SearXNG en tiempo real
            0 → LLM libre  — conocimiento paramétrico sin retrieval
            (El nivel 3 ya no se usa: BM25 está integrado en el hybrid L2)
            Usar para depuración y para mostrar al usuario el origen.

        level_label:
            Etiqueta legible del nivel para la UI.
            Valores: "🧠 Brain", "📚 Knowledge", "🌐 Web", "🤖 LLM"

        sources:
            Lista de identificadores de fuente incluidos en el contexto.
            L1/L2: slugs de source de Qdrant (ej. "pipeline-fabric")
            BM25: títulos de documento de PostgreSQL
            Web: títulos de página de SearXNG
            L0: lista vacía (sin fuente)
            Mostrar al usuario como citas o referencias de la respuesta.

        context_chunks:
            Número de chunks efectivamente incluidos en el prompt del LLM.
            Acotado por ModelProfile.retrieval_chunk_budget.
            Con CRAG activo: solo los chunks validados como relevantes.
            0 si level_used == 0 (L0 libre sin contexto).
            Útil para depuración y para entender la densidad de contexto.

        model_tier:
            Tier del modelo activo en el momento de la consulta.
            "small" | "medium" | "claude"
            Refleja SYNTHESIS_MODEL del .env via get_profile().
            Permite al frontend adaptar expectativas de calidad/latencia.
    """
    answer:         str
    level_used:     int
    level_label:    str
    sources:        list[str]
    context_chunks: int
    model_tier:     str


# ── Endpoint principal: cascada multinivel ────────────────────────────────────

@router.post("/query", response_model=BrainQueryResponse)
async def brain_query(
    req: BrainQueryRequest,
    current_user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Consulta al Second Brain con cascada multinivel L1→L2→[CRAG]→BM25→Web→L0.

    La cascada se detiene en el primer nivel que produce contexto relevante.
    El presupuesto de chunks y la longitud máxima se adaptan automáticamente
    al tier del modelo activo (small/medium/claude) para no saturar el contexto.

    Cascada:
      L1    → brain (pasaportes semánticos Qdrant, score ≥ ROUTER_L1_MIN_SCORE)
      L2    → knowledge/code (chunks Qdrant con reranking cross-encoder)
      [CRAG]→ Evaluador cualitativo (si CRAG_EVALUATOR_ENABLED=true)
              Solo con Claude/GPT-4. Con Ollama añade ~3-5s por chunk.
      L2.b  → BM25 PostgreSQL (tsvector, <100ms, si BRAIN_BM25_ENABLED=true)
      L2.c  → Web SearXNG (si BRAIN_WEB_ENABLED=true)
      L0    → LLM libre sin contexto (si BRAIN_L0_ENABLED=true, else 404)
    """
    # ── Perfil del modelo activo ──────────────────────────────────────────────
    profile         = get_profile(SYNTHESIS_MODEL)
    tier            = profile.prompt_tier if profile else "medium"

    # Presupuesto derivado del ModelProfile del modelo activo — cero hardcode.
    # Ver ModelProfile.retrieval_chunk_budget / retrieval_chunk_max_chars
    # en app/brain/model_profiles.py para la documentación y fórmulas.
    chunk_budget    = profile.retrieval_chunk_budget    if profile else 6
    chunk_max_chars = profile.retrieval_chunk_max_chars if profile else 800

    logger.info(
        "[brain_query] START question=%r space=%d model=%s tier=%s budget=%d max_chars=%d force_l0=%s",
        req.question[:60], req.search_space_id, SYNTHESIS_MODEL,
        tier, chunk_budget, chunk_max_chars, req.force_l0,
    )

    # ── L0 forzado — saltar toda la cascada ──────────────────────────────────
    if req.force_l0:
        logger.info("[brain_query] force_l0=True → LLM directo sin RAG")
        answer = _call_llm(req.question, context="", tier=tier, history=req.chat_history)
        _track_level_usage(0, req.search_space_id)
        return BrainQueryResponse(
            answer=answer, level_used=0, level_label="🤖 LLM",
            sources=[], context_chunks=0, model_tier=tier,
        )

    # ── L1 + L2 hybrid: router síncrono Qdrant ───────────────────────────────
    # search_space_id se pasa como str al router (filtra en Qdrant por payload).
    # BrainRouter decide qué colección Qdrant usar en L2:
    #   knowledge → documentos, PDFs, markdown
    #   code      → ficheros de código fuente
    brain_router = BrainRouter(search_space_id=str(req.search_space_id))
    route_result = brain_router.route(
        question=req.question,
        top_k=req.top_k,
        force_level=req.force_level,
    )

    # ── L1: Brain (pasaportes semánticos) — sin hybrid ───────────────────────
    # Los pasaportes son síntesis de alto nivel: semántica pura es suficiente.
    # No se aplica BM25 aquí para no contaminar con chunks raw.
    if (
        not route_result.get("fallback_needed")
        and route_result.get("results")
        and route_result.get("level_used") == 1
    ):
        chunks  = _extract_chunks(route_result["results"], chunk_budget, chunk_max_chars)
        sources = route_result["sources_consulted"]

        logger.info(
            "[brain_query] L1 Brain → %d chunks, %d sources space=%d",
            len(chunks), len(sources), req.search_space_id,
        )

        if CRAG_EVALUATOR_ENABLED and chunks:
            logger.info(
                "[brain_query] CRAG activado → evaluando %d chunks (L1) space=%d",
                min(len(chunks), 3), req.search_space_id,
            )
            hay_relevante, evaluaciones = evaluar_chunks(req.question, chunks)
            logger.info(
                "[brain_query] CRAG L1: hay_relevante=%s evaluaciones=%d",
                hay_relevante, len(evaluaciones),
            )
            if hay_relevante:
                chunks_ok = [chunks[i] for i, ev in enumerate(evaluaciones) if ev.es_relevante]
                context   = _build_context(chunks_ok or chunks)
                answer    = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
                _track_level_usage(1, req.search_space_id)
                return BrainQueryResponse(
                    answer=answer, level_used=1, level_label="🧠 Brain",
                    sources=sources, context_chunks=len(chunks_ok or chunks), model_tier=tier,
                )
            # CRAG rechaza chunks L1 → continúa a L2 hybrid
            logger.info("[brain_query] CRAG L1: sin chunks relevantes → L2 hybrid")
        else:
            context = _build_context(chunks)
            answer  = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
            _track_level_usage(1, req.search_space_id)
            return BrainQueryResponse(
                answer=answer, level_used=1, level_label="🧠 Brain",
                sources=sources, context_chunks=len(chunks), model_tier=tier,
            )

    # ── L2 hybrid: Qdrant (knowledge/code) + BM25, fusión RRF ───────────────
    #
    # Qdrant semántico captura similitud conceptual y sinónimos.
    # BM25 captura coincidencias léxicas exactas (identificadores, nombres propios).
    # RRF (Reciprocal Rank Fusion, Cormack et al. 2009, k=60) combina ambas listas
    # por posición — sin calibrar pesos — premiando documentos bien posicionados
    # en ambas. BrainRouter selecciona knowledge o code según el tipo de fichero.
    logger.info(
        "[brain_query] L2 hybrid START space=%d (qdrant_fallback=%s results=%d)",
        req.search_space_id,
        route_result.get("fallback_needed"),
        len(route_result.get("results") or []),
    )

    # Parte 1 — Qdrant L2 (knowledge/code): puede estar vacío si fallback_needed
    qdrant_l2_chunks: list[dict] = []
    if (
        not route_result.get("fallback_needed")
        and route_result.get("results")
        and route_result.get("level_used") == 2
    ):
        qdrant_l2_chunks = _extract_chunks(
            route_result["results"],
            budget=chunk_budget * 2,   # doble de candidatos para el pool RRF
            max_chars=chunk_max_chars,
        )
        logger.info(
            "[brain_query] L2 hybrid Qdrant: %d chunks (col=%s)",
            len(qdrant_l2_chunks),
            route_result.get("collection_used", "knowledge"),
        )

    # Parte 2 — BM25 PostgreSQL (siempre que esté habilitado)
    bm25_l2_chunks: list[dict] = []
    if BRAIN_BM25_ENABLED:
        try:
            from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever
            retriever     = ChucksHybridSearchRetriever(db)
            bm25_raw      = await retriever.full_text_search(
                query_text=req.question,
                top_k=chunk_budget * 2,
                search_space_id=req.search_space_id,
            )
            bm25_l2_chunks = _extract_chunks_from_orm(
                bm25_raw, budget=chunk_budget * 2, max_chars=chunk_max_chars,
            )
            logger.info("[brain_query] L2 hybrid BM25: %d chunks", len(bm25_l2_chunks))
        except Exception as exc:
            logger.warning(
                "[brain_query] L2 hybrid BM25 falló: %s — continuando solo con Qdrant",
                exc, exc_info=True,
            )
    else:
        logger.debug("[brain_query] BM25 desactivado (BRAIN_BM25_ENABLED=false)")

    # Parte 3 — RRF fusion
    fused_chunks = _rrf_fuse(qdrant_l2_chunks, bm25_l2_chunks, budget=chunk_budget)

    if fused_chunks:
        _qdrant_ok = len(qdrant_l2_chunks) > 0
        _bm25_ok   = len(bm25_l2_chunks)   > 0
        _src_label = (
            "Qdrant+BM25" if (_qdrant_ok and _bm25_ok)
            else ("Qdrant" if _qdrant_ok else "BM25")
        )
        logger.info(
            "[brain_query] L2 hybrid RRF OK: %d chunks fusionados (%s) space=%d",
            len(fused_chunks), _src_label, req.search_space_id,
        )
        sources = list({c["source"] for c in fused_chunks if c.get("source")})

        if CRAG_EVALUATOR_ENABLED and fused_chunks:
            logger.info(
                "[brain_query] CRAG activado → evaluando %d chunks (L2 hybrid) space=%d",
                min(len(fused_chunks), 3), req.search_space_id,
            )
            hay_relevante, evaluaciones = evaluar_chunks(req.question, fused_chunks)
            logger.info(
                "[brain_query] CRAG L2 hybrid: hay_relevante=%s evaluaciones=%d",
                hay_relevante, len(evaluaciones),
            )
            if hay_relevante:
                chunks_ok = [fused_chunks[i] for i, ev in enumerate(evaluaciones) if ev.es_relevante]
                context   = _build_context(chunks_ok or fused_chunks)
                answer    = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
                _track_level_usage(2, req.search_space_id)
                return BrainQueryResponse(
                    answer=answer, level_used=2, level_label="📚 Knowledge",
                    sources=sources, context_chunks=len(chunks_ok or fused_chunks), model_tier=tier,
                )
            logger.info("[brain_query] CRAG L2 hybrid: sin relevantes → Web/L0")
        else:
            context = _build_context(fused_chunks)
            answer  = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
            _track_level_usage(2, req.search_space_id)
            return BrainQueryResponse(
                answer=answer, level_used=2, level_label="📚 Knowledge",
                sources=sources, context_chunks=len(fused_chunks), model_tier=tier,
            )

    logger.info(
        "[brain_query] L2 hybrid sin chunks (Qdrant=%d BM25=%d) → Web/L0 space=%d",
        len(qdrant_l2_chunks), len(bm25_l2_chunks), req.search_space_id,
    )

    # ── L2.c: Web SearXNG ────────────────────────────────────────────────────
    if BRAIN_WEB_ENABLED:
        logger.info("[brain_query] Intentando L2.c web search")
        try:
            from app.services import web_search_service
            if web_search_service.is_available():
                # Query rewriting: pregunta natural → keywords optimizadas para SearXNG
                search_query = await _rewrite_query_for_web(req.question)
                logger.info(
                    "[brain_query] QueryRewrite: %r → %r",
                    req.question[:50], search_query,
                )
                # Firma real: search(query, top_k) — sin search_space_id
                _, web_docs = await web_search_service.search(
                    query=search_query,
                    top_k=BRAIN_WEB_MAX_RESULTS,
                )
                if web_docs:
                    chunks = _extract_chunks_from_web(web_docs, chunk_budget, chunk_max_chars)
                    if chunks:
                        context = _build_context(chunks)
                        sources = [c["source"] for c in chunks]
                        logger.info(
                            "[brain_query] L2.c Web → %d snippets, %d sources",
                            len(chunks), len(sources),
                        )
                        answer = _call_llm(
                            req.question, context=context, tier=tier, history=req.chat_history,
                        )
                        _track_level_usage(4, req.search_space_id)
                        return BrainQueryResponse(
                            answer=answer, level_used=4, level_label="🌐 Web",
                            sources=sources, context_chunks=len(chunks), model_tier=tier,
                        )
                logger.info("[brain_query] L2.c Web sin resultados útiles")
            else:
                logger.info("[brain_query] L2.c Web no disponible (SearXNG no configurado)")
        except Exception as exc:
            logger.warning(
                "[brain_query] L2.c Web falló: %s — cayendo a L0",
                exc, exc_info=True,
            )
    else:
        logger.debug("[brain_query] L2.c Web desactivado (BRAIN_WEB_ENABLED=false)")

    # ── L0: LLM libre — último recurso ────────────────────────────────────────
    if not BRAIN_L0_ENABLED:
        logger.warning(
            "[brain_query] Cascada agotada y BRAIN_L0_ENABLED=false → 404 space=%d",
            req.search_space_id,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                "No se encontró contexto relevante y L0 está deshabilitado. "
                "Activa BRAIN_BM25_ENABLED o BRAIN_WEB_ENABLED, o sube documentos al space."
            ),
        )

    logger.info(
        "[brain_query] Cascada agotada → L0 libre (tier=%s space=%d). "
        "Para modelos ≤7B considera activar BRAIN_BM25_ENABLED/BRAIN_WEB_ENABLED.",
        tier, req.search_space_id,
    )
    answer = _call_llm(req.question, context="", tier=tier, history=req.chat_history)
    _track_level_usage(0, req.search_space_id)
    return BrainQueryResponse(
        answer=answer, level_used=0, level_label="🤖 LLM",
        sources=[], context_chunks=0, model_tier=tier,
    )


# ── F6.B.02 — Estadísticas del Brain ─────────────────────────────────────────

_stats_logger = logging.getLogger("surfsense.brain.stats")

# Colecciones Qdrant que gestiona el Brain — leído desde env para extensibilidad.
# El default refleja la arquitectura actual: brain (pasaportes), knowledge (chunks),
# code (fragmentos de código). Sobreescribir con BRAIN_COLLECTIONS si se añaden más.
_BRAIN_COLLECTIONS = [
    c.strip()
    for c in os.getenv("BRAIN_COLLECTIONS", "brain,knowledge,code").split(",")
    if c.strip()
]


def _normalize_iso_datetime(value: str | None) -> str | None:
    """Normaliza un timestamp a ISO 8601 con offset de timezone explícito.

    El schema Zod del frontend (`z.string().datetime({ offset: true })`) exige
    que el string lleve sufijo de zona (`Z` o `±HH:MM`). Si el valor leído
    de Redis no lo trae (timestamps naïve), asumimos UTC.
    """
    if not value:
        return None
    try:
        # fromisoformat acepta tanto 'Z' (3.11+) como offsets ±HH:MM
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


@router.get("/stats")
async def brain_stats(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Estadísticas agregadas del Brain para el search space indicado.

    Combina tres fuentes de datos en paralelo:
      - **Qdrant**: vectores totales, documentos únicos y dimensión por colección.
      - **Redis**: último timestamp de ingesta, último documento ingestado y
        contadores de nivel de búsqueda (L0–L4) de las últimas 24 h.

    Requiere autenticación. El usuario debe ser miembro del search space
    (o superusuario) para obtener sus estadísticas.

    Respuesta:
        collections   — dict con métricas por colección Qdrant.
        last_ingest   — ISO timestamp de la última ingesta (null si no hay).
        last_ingest_doc — fuente del último documento ingestado (null si no hay).
        level_usage   — contadores de nivel de retrieval últimas 24 h.
        brain_count   — vectores totales en la colección 'brain'.
        knowledge_count — vectores totales en la colección 'knowledge'.
        code_count    — vectores totales en la colección 'code'.
    """
    await _require_space_access(search_space_id, db, current_user)

    _stats_logger.info(
        "[brain_stats] solicitadas por user=%s space=%d",
        current_user.id, search_space_id,
    )

    # ── Qdrant: vectores y fuentes únicas por colección (síncrono → thread pool) ─
    def _qdrant_stats() -> dict[str, dict]:
        mgr = QdrantManager.get_instance()
        collections: dict[str, dict] = {}

        for col_name in _BRAIN_COLLECTIONS:
            try:
                info = mgr.client.get_collection(col_name)

                # Documentos del space: scroll filtrando por search_space_id en payload
                result, _ = mgr.client.scroll(
                    collection_name=col_name,
                    scroll_filter=qdrant_models.Filter(must=[
                        qdrant_models.FieldCondition(
                            key="search_space_id",
                            match=qdrant_models.MatchValue(value=str(search_space_id)),
                        )
                    ]),
                    with_payload=["source"],
                    limit=10_000,
                )
                unique_sources = len({
                    p.payload.get("source") for p in result if p.payload
                })

                # Dimensión del vector: compatible con distintas versiones de qdrant-client
                vectors_cfg = getattr(info.config.params, "vectors", None)
                dimension = getattr(vectors_cfg, "size", None) or 768

                collections[col_name] = {
                    "count":     info.points_count or 0,
                    "sources":   unique_sources,
                    "dimension": dimension,
                }
                _stats_logger.debug(
                    "[brain_stats] col=%s count=%d sources=%d dim=%d",
                    col_name, collections[col_name]["count"],
                    unique_sources, dimension,
                )
            except Exception as exc:
                _stats_logger.warning(
                    "[brain_stats] No se pudo obtener stats de col=%s: %s", col_name, exc,
                )
                collections[col_name] = {"count": 0, "sources": 0, "dimension": 768}

        return collections

    # ── Redis: contadores de nivel + timestamps de ingesta (síncrono → thread pool) ─
    def _redis_stats() -> tuple[dict[str, int], str | None, str | None]:
        redis_url = os.getenv("REDIS_APP_URL", "redis://redis:6379/0")
        level_usage: dict[str, int] = {}
        last_ingest: str | None = None
        last_ingest_doc: str | None = None

        try:
            r = redis_lib.from_url(redis_url, socket_timeout=2)

            # Niveles L0–L4 (cascada completa)
            for lvl in range(5):
                raw = r.get(f"brain:level:{lvl}:space:{search_space_id}")
                if raw:
                    level_usage[str(lvl)] = int(raw)

            li  = r.get(f"brain:last_ingest:space:{search_space_id}")
            lid = r.get(f"brain:last_ingest_doc:space:{search_space_id}")
            last_ingest     = _normalize_iso_datetime(li.decode())  if li  else None
            last_ingest_doc = lid.decode() if lid else None

        except Exception as exc:
            _stats_logger.warning("[brain_stats] Redis no disponible: %s", exc)

        return level_usage, last_ingest, last_ingest_doc

    # ── Ejecutar ambas consultas en paralelo para minimizar latencia ──────────
    collections, redis_result = await asyncio.gather(
        asyncio.to_thread(_qdrant_stats),
        asyncio.to_thread(_redis_stats),
    )
    level_usage, last_ingest, last_ingest_doc = redis_result

    _stats_logger.info(
        "[brain_stats] OK space=%d collections=%s level_usage=%s last_ingest=%s",
        search_space_id, list(collections.keys()), level_usage, last_ingest,
    )

    return {
        "collections":      collections,
        "last_ingest":      last_ingest,
        "last_ingest_doc":  last_ingest_doc,
        "level_usage":      level_usage,
        # Shortcuts para que la UI no tenga que navegar el dict collections
        "brain_count":      collections.get("brain",     {}).get("count", 0),
        "knowledge_count":  collections.get("knowledge", {}).get("count", 0),
        "code_count":       collections.get("code",      {}).get("count", 0),
    }


# ── F6.B.03 — Uso de niveles de retrieval ────────────────────────────────────

_level_usage_logger = logging.getLogger("surfsense.brain.level_usage")

# Etiquetas descriptivas por nivel — alineadas con BrainQueryResponse.level_label.
# Cargadas una sola vez al inicio del módulo para evitar reconstrucción por petición.
_LEVEL_LABELS: dict[int, str] = {
    0: "🤖 LLM (L0)",
    1: "🧠 Brain (L1)",
    2: "📚 Knowledge (L2)",
    3: "🔍 BM25 (L2.b)",
    4: "🌐 Web (L2.c)",
}

# Ventana de retención de contadores en Redis — debe coincidir con el TTL
# usado en _track_level_usage() (86400 s = 24 h).
_LEVEL_USAGE_PERIOD_HOURS = int(os.getenv("BRAIN_LEVEL_TTL_HOURS", "24"))


@router.get("/level-usage")
async def brain_level_usage(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Contadores de uso por nivel de retrieval de las últimas 24 h para el space.

    Cada vez que ``brain_query`` devuelve una respuesta, incrementa un contador
    Redis para el nivel utilizado (L0–L4). Este endpoint expone esos contadores
    para que la UI pueda mostrar un gráfico de distribución de retrieval.

    Niveles de la cascada:
      - **0** — LLM libre (L0, sin contexto RAG)
      - **1** — Brain / pasaportes semánticos (L1)
      - **2** — Knowledge / chunks semánticos (L2)
      - **3** — BM25 PostgreSQL (L2.b)
      - **4** — Web SearXNG (L2.c)

    Respuesta:
        usage         — dict ``{level_str: count}`` solo con niveles con uso > 0.
        period_hours  — ventana temporal de los contadores (TTL Redis, default 24 h).
        labels        — etiquetas descriptivas por nivel para renderizado directo en UI.

    Requiere autenticación. El usuario debe ser miembro del search space
    (o superusuario).
    """
    await _require_space_access(search_space_id, db, current_user)

    _level_usage_logger.info(
        "[level_usage] solicitado por user=%s space=%d",
        current_user.id, search_space_id,
    )

    def _read_redis() -> dict[str, int]:
        """Lee los contadores de nivel L0–L4 desde Redis."""
        redis_url = os.getenv("REDIS_APP_URL", "redis://redis:6379/0")
        usage: dict[str, int] = {}
        try:
            r = redis_lib.from_url(redis_url, socket_timeout=2)
            for lvl in range(5):
                raw = r.get(f"brain:level:{lvl}:space:{search_space_id}")
                if raw:
                    usage[str(lvl)] = int(raw)
            _level_usage_logger.debug(
                "[level_usage] Redis OK space=%d counters=%s", search_space_id, usage,
            )
        except Exception as exc:
            _level_usage_logger.warning(
                "[level_usage] Redis no disponible, devolviendo vacío: %s", exc,
            )
        return usage

    usage = await asyncio.to_thread(_read_redis)

    _level_usage_logger.info(
        "[level_usage] OK space=%d usage=%s period_hours=%d",
        search_space_id, usage, _LEVEL_USAGE_PERIOD_HOURS,
    )

    return {
        "usage":        usage,
        "period_hours": _LEVEL_USAGE_PERIOD_HOURS,
        # Etiquetas para renderizado directo en la UI sin lógica de mapeo en el cliente
        "labels":       _LEVEL_LABELS,
    }


# ── F6.B.04 — Listado de pasaportes del space ────────────────────────────────

_list_logger = logging.getLogger("surfsense.brain.list")


@router.get("/list")
async def brain_list(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Lista todos los pasaportes .md del Brain visibles para el search space.

    Combina dos fuentes para filtrar los pasaportes que pertenecen al space:

    1. **BrainWriter.list_all()** — lee todos los ``.md`` del directorio Brain
       (``BRAIN_DIR``) extrayendo el frontmatter YAML (título, dominio, tags,
       importancia, etc.).

    2. **Qdrant colección 'brain'** — scroll con filtro ``search_space_id`` para
       obtener el conjunto de fuentes indexadas en ese space. Solo se devuelven
       pasaportes cuyo ``source`` (filename) esté presente en Qdrant para el space.
       Si Qdrant no está disponible se devuelven todos los .md como fallback.

    Cada pasaporte incluye:
        source      — nombre del fichero .md (slug).
        title       — título extraído del frontmatter.
        domain      — dominio funcional (``other`` si no definido).
        subdomain   — subdominio (vacío si no definido).
        tags        — lista de etiquetas semánticas.
        importance  — nivel de importancia (1–5, int).
        doc_type    — tipo documental (``technical_doc`` si no definido).
        updated_at  — ISO timestamp de última modificación del .md.
        scopes      — colecciones Qdrant donde está vectorizado.
        confidence  — score de confianza del pasaporte (0.0–1.0).

    Respuesta:
        count       — número total de pasaportes del space.
        passports   — lista de objetos pasaporte.

    Requiere autenticación. El usuario debe ser miembro del search space
    (o superusuario).
    """
    await _require_space_access(search_space_id, db, current_user)

    _list_logger.info(
        "[brain_list] solicitado por user=%s space=%d",
        current_user.id, search_space_id,
    )

    passports = await _list_passports_for_space(search_space_id)

    _list_logger.info(
        "[brain_list] OK space=%d count=%d",
        search_space_id, len(passports),
    )

    return passports


# ── F6.B.05 — GET + PUT /api/v1/brain/passport/{source} ──────────────────────
#
# El GET existente devolvía {source, passport_md}. La UI espera {source, content, metadata}.
# Se reemplaza el GET por la versión ampliada y se añade el PUT con re-vectorización.

_passport_logger = logging.getLogger("surfsense.brain.passport")


class PassportUpdateRequest(BaseModel):
    """Cuerpo del PUT /passport/{source}. El campo content es el .md completo."""
    content: str


@router.get("/passport/{source}")
async def get_passport(
    source: str,
    search_space_id: int = 1,
    current_user: User = Depends(current_active_user),
):
    """
    Lee el pasaporte .md semántico con contenido completo y metadata del frontmatter.

    Reemplaza la versión anterior que devolvía ``{source, passport_md}``.
    La UI Brain espera ``{source, content, metadata}`` con todos los campos
    del frontmatter YAML parseados para poder renderizar el editor de pasaporte.

    Args:
        source:          Nombre del fichero original o slug (ej: ``informe.pdf``
                         o ``informe``). BrainWriter.read() resuelve ambos.
        search_space_id: ID del search space (requerido para logs de auditoría;
                         no se usa para filtrar — el pasaporte es global en disco).

    Respuesta:
        source    — identificador del pasaporte (slug o filename).
        content   — texto completo del .md incluyendo frontmatter YAML.
        metadata  — campos del frontmatter parseados listos para la UI:
                    domain, subdomain, tags, importance (int 1–5),
                    doc_type, scopes, confidence.

    Lanza 404 si el pasaporte no existe en disco.
    No requiere membresía al space — la lectura es pública dentro del tenant.
    """
    import re as _re
    import yaml as _yaml
    from app.brain.writer import BrainWriter

    def _read_and_parse() -> tuple[str, dict] | None:
        """Lee el .md y parsea el frontmatter YAML en thread pool."""
        content = BrainWriter().read(source)
        if content is None:
            return None

        meta: dict = {}
        fm_match = _re.match(r"^---\n(.*?)\n---\n", content, _re.DOTALL)
        if fm_match:
            try:
                meta = _yaml.safe_load(fm_match.group(1)) or {}
            except _yaml.YAMLError as exc:
                _passport_logger.warning(
                    "[passport_get] YAML inválido source='%s': %s", source, exc,
                )
        return content, meta

    _passport_logger.info(
        "[passport_get] solicitado source='%s' user=%s space=%d",
        source, current_user.id, search_space_id,
    )

    result = await asyncio.to_thread(_read_and_parse)
    if result is None:
        _passport_logger.info("[passport_get] no encontrado source='%s'", source)
        raise HTTPException(status_code=404, detail=f"Pasaporte no encontrado: '{source}'")

    content, meta = result

    _passport_logger.info(
        "[passport_get] OK source='%s' chars=%d domain=%s",
        source, len(content), meta.get("domain"),
    )

    return {
        "source":  source,
        "content": content,
        "metadata": {
            "domain":     meta.get("domain"),
            "subdomain":  meta.get("subdomain"),
            "tags":       meta.get("tags", []),
            # importance puede llegar como string ('high'/'medium') o int — se normaliza a int
            "importance": int(meta.get("importance", 3))
                          if str(meta.get("importance", "3")).isdigit()
                          else 3,
            "doc_type":   meta.get("type"),
            "scopes":     meta.get("embedding_scope", ["brain", "knowledge"]),
            "confidence": meta.get("confidence"),
        },
    }


@router.put("/passport/{source}")
async def update_passport(
    source: str,
    body: PassportUpdateRequest,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Actualiza el contenido de un pasaporte .md y re-vectoriza en Qdrant.

    Flujo:
      1. Verificar acceso al search space.
      2. Leer el contenido actual del .md (404 si no existe).
      3. Guardar versión histórica en ``.history/{slug}/vNNN_TS.md`` antes de sobrescribir.
      4. Escribir el nuevo contenido en disco via BrainWriter.
      5. Re-vectorizar en las colecciones Qdrant (brain + knowledge + code según scope).
      6. Actualizar timestamps de última ingesta en Redis.

    La re-vectorización es síncrona (en thread pool) para garantizar coherencia:
    cuando el endpoint responde, Qdrant ya contiene los nuevos vectores.

    Args:
        source:          Slug o filename del pasaporte a actualizar.
        body.content:    Contenido completo del nuevo .md (incluye frontmatter).
        search_space_id: ID del search space para filtrar la re-vectorización.

    Respuesta:
        source   — identificador del pasaporte actualizado.
        content  — contenido tal como fue guardado.
        updated  — siempre True si no hay error.

    Lanza 403 si el usuario no tiene acceso al space.
    Lanza 404 si el pasaporte no existe en disco (usar POST /ingest/url para crear).
    """
    import datetime as _dt
    from app.brain.writer import BrainWriter
    from app.brain.ingest_router import IngestRouter

    await _require_space_access(search_space_id, db, current_user)

    _passport_logger.info(
        "[passport_put] START source='%s' user=%s space=%d chars=%d",
        source, current_user.id, search_space_id, len(body.content),
    )

    # ── 1. Verificar que el pasaporte existe antes de sobrescribir ────────────
    current_content = await asyncio.to_thread(BrainWriter().read, source)
    if current_content is None:
        _passport_logger.warning("[passport_put] no encontrado source='%s'", source)
        raise HTTPException(
            status_code=404,
            detail=f"Pasaporte no encontrado: '{source}'. Usa POST /ingest/url para crear uno nuevo.",
        )

    # ── 2. Guardar versión histórica antes de sobrescribir ────────────────────
    await _save_passport_version(source, current_content)
    _passport_logger.debug("[passport_put] versión histórica guardada source='%s'", source)

    # ── 3. Escribir nuevo contenido y re-vectorizar (síncrono en thread pool) ──
    def _write_and_revectorize() -> dict:
        mgr = QdrantManager.get_instance()

        # Sobreescribir el .md en disco
        BrainWriter().write(source, body.content)

        # Re-vectorizar en las colecciones Qdrant del scope del frontmatter
        results = IngestRouter(mgr.client).route(
            md_content=body.content,
            blocks=[],
            source=source,
            search_space_id=str(search_space_id),
        )
        total_chunks = sum(v.get("chunks_created", 0) for v in results.values())

        # Registrar timestamps de ingesta en Redis (fire-and-forget)
        try:
            r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
            now_iso = _dt.datetime.utcnow().isoformat()
            r.set(f"brain:last_ingest:space:{search_space_id}", now_iso)
            r.set(f"brain:last_ingest_doc:space:{search_space_id}", source)
        except Exception as exc:
            _passport_logger.warning(
                "[passport_put] Redis no actualizado source='%s': %s", source, exc,
            )

        return results, total_chunks

    results, total_chunks = await asyncio.to_thread(_write_and_revectorize)

    _passport_logger.info(
        "[passport_put] DONE source='%s' space=%d chunks_revectorizados=%d collections=%s",
        source, search_space_id, total_chunks, list(results.keys()),
    )

    return {
        "source":  source,
        "content": body.content,
        "updated": True,
    }


# ── F6.B.06 — Historial de versiones de un pasaporte ─────────────────────────

_history_logger = logging.getLogger("surfsense.brain.history")

# Número máximo de versiones a devolver — configurable sin tocar código.
# Aumentar si la UI necesita paginación futura.
_HISTORY_MAX_VERSIONS = int(os.getenv("BRAIN_HISTORY_MAX_VERSIONS", "20"))


@router.get("/passport/{source}/history")
async def passport_history(
    source: str,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Devuelve el historial de versiones de un pasaporte .md.

    Cada vez que se llama al PUT ``/passport/{source}``, el contenido anterior
    se archiva en ``.history/{slug}/vNNN_TS.md`` antes de sobrescribirse.
    Este endpoint expone esas versiones ordenadas de más reciente a más antigua
    para que la UI pueda mostrar un diff o restaurar una versión anterior.

    El directorio de historial se calcula como:
        ``{BRAIN_DIR}/.history/{_slugify(source)}/``

    Cada versión incluye:
        version_id  — nombre del fichero sin extensión (ej: ``v001_20260626T073607``).
        created_at  — ISO timestamp de cuando se archivó la versión (mtime del fichero).
        content     — texto completo del .md archivado.

    Respuesta:
        source    — identificador del pasaporte.
        versions  — lista de versiones, máximo ``BRAIN_HISTORY_MAX_VERSIONS`` (default 20),
                    ordenadas de más reciente a más antigua.

    Devuelve ``versions: []`` (no 404) si el pasaporte nunca ha sido editado.
    Requiere autenticación y membresía al search space (o superusuario).
    """
    import pathlib
    from app.brain.writer import _slugify

    await _require_space_access(search_space_id, db, current_user)

    _history_logger.info(
        "[passport_history] solicitado source='%s' user=%s space=%d",
        source, current_user.id, search_space_id,
    )

    def _read_versions() -> list[dict]:
        """
        Lee los ficheros de historial en thread pool.
        Devuelve lista ordenada de más reciente a más antigua, limitada a
        BRAIN_HISTORY_MAX_VERSIONS para no saturar la respuesta.
        """
        brain_dir = pathlib.Path(os.getenv("BRAIN_DIR", "/data/brain"))
        slug      = _slugify(source)
        hdir      = brain_dir / ".history" / slug

        if not hdir.exists():
            _history_logger.debug(
                "[passport_history] directorio de historial no existe: %s", hdir,
            )
            return []

        version_files = sorted(hdir.glob("v*.md"), reverse=True)[:_HISTORY_MAX_VERSIONS]

        versions = []
        for vf in version_files:
            try:
                created_at = datetime.datetime.utcfromtimestamp(
                    vf.stat().st_mtime
                ).isoformat()
                content = vf.read_text(encoding="utf-8")
                versions.append({
                    "version_id": vf.stem,           # ej: "v001_20260626T073607"
                    "created_at": created_at,
                    "content":    content,
                })
            except Exception as exc:
                _history_logger.warning(
                    "[passport_history] error leyendo versión '%s': %s", vf.name, exc,
                )

        return versions

    versions = await asyncio.to_thread(_read_versions)

    _history_logger.info(
        "[passport_history] OK source='%s' space=%d versions=%d max=%d",
        source, search_space_id, len(versions), _HISTORY_MAX_VERSIONS,
    )

    return {
        "source":   source,
        "versions": versions,
    }


# ── F6.B.07 — Re-síntesis de pasaporte desde chunks Qdrant ───────────────────

_resynth_logger = logging.getLogger("surfsense.brain.resynthesize")


class ResynthesizeRequest(BaseModel):
    """
    Cuerpo del POST /document/{source}/resynthesize.

    Campos:
        search_space_id: ID del search space del que recuperar los chunks.
        model:           Modelo Ollama para la síntesis (opcional).
                         Si se omite se usa SYNTHESIS_MODEL del entorno.
        provider:        Proveedor LLM (opcional). Si se omite se usa DEFAULT_PROVIDER.
    """
    search_space_id: int
    model:    str | None = None
    provider: str | None = None


@router.post("/document/{source}/resynthesize")
async def resynthesize_passport(
    source: str,
    body: ResynthesizeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Re-sintetiza el pasaporte de un documento a partir de sus chunks en Qdrant.

    Útil cuando el pasaporte .md está desactualizado respecto al contenido indexado,
    o cuando se quiere regenerar el pasaporte con un modelo LLM más potente.

    Flujo (ejecutado en background para no bloquear la UI):
      1. Scroll de chunks en la colección ``knowledge`` filtrando por source + space.
      2. Concatenar el texto de todos los chunks en orden.
      3. Re-sintetizar vía ``DocumentSynthesizer`` con el modelo indicado.
      4. Archivar la versión actual del .md en ``.history/`` antes de sobrescribir.
      5. Escribir el nuevo .md en disco.
      6. Re-vectorizar en Qdrant via ``IngestRouter``.

    La operación es **asíncrona**: el endpoint devuelve ``status: queued``
    inmediatamente y la re-síntesis se ejecuta en background. La UI puede
    refrescar el pasaporte con GET /passport/{source} cuando termine.

    Args:
        source:               Slug o filename del documento a re-sintetizar.
        body.search_space_id: Space del que recuperar los chunks de Qdrant.
        body.model:           Modelo LLM (default: ``SYNTHESIS_MODEL`` del entorno).
        body.provider:        Proveedor LLM (default: ``DEFAULT_PROVIDER`` del entorno).

    Respuesta:
        status     — siempre ``"queued"`` si no hay error previo.
        source     — identificador del documento.
        background — siempre ``True`` (la operación se ejecuta en background).

    Lanza 403 si el usuario no tiene acceso al space.
    Lanza 404 si no hay chunks en Qdrant para el source+space indicado.
    """
    await _require_space_access(body.search_space_id, db, current_user)

    effective_model    = body.model    or SYNTHESIS_MODEL
    effective_provider = body.provider or DEFAULT_PROVIDER

    _resynth_logger.info(
        "[resynthesize] START source='%s' space=%d model=%s provider=%s user=%s",
        source, body.search_space_id, effective_model, effective_provider, current_user.id,
    )

    # ── Verificar que existen chunks antes de encolar el background task ──────
    # IngestRouter almacena en Qdrant el slug (_slugify(source)), no el filename raw.
    # Aplicamos el mismo slugify para que el filtro coincida con lo almacenado.
    def _check_chunks_exist() -> bool:
        from app.brain.writer import _slugify
        mgr   = QdrantManager.get_instance()
        slug  = _slugify(source)
        result, _ = mgr.client.scroll(
            collection_name="knowledge",
            scroll_filter=qdrant_models.Filter(must=[
                qdrant_models.FieldCondition(
                    key="source",
                    match=qdrant_models.MatchValue(value=slug),
                ),
                qdrant_models.FieldCondition(
                    key="search_space_id",
                    match=qdrant_models.MatchValue(value=str(body.search_space_id)),
                ),
            ]),
            with_payload=False,
            limit=1,
        )
        return len(result) > 0

    has_chunks = await asyncio.to_thread(_check_chunks_exist)
    if not has_chunks:
        _resynth_logger.warning(
            "[resynthesize] sin chunks en Qdrant source='%s' space=%d",
            source, body.search_space_id,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"No se encontraron chunks para '{source}' en el space {body.search_space_id}. "
                "Asegúrate de que el documento ha sido ingestado previamente."
            ),
        )

    background_tasks.add_task(
        _run_resynthesize,
        source=source,
        search_space_id=body.search_space_id,
        model=effective_model,
        provider=effective_provider,
    )

    _resynth_logger.info(
        "[resynthesize] QUEUED source='%s' space=%d", source, body.search_space_id,
    )

    return {
        "status":     "queued",
        "source":     source,
        "background": True,
    }


async def _run_resynthesize(
    source: str,
    search_space_id: int,
    model: str,
    provider: str,
) -> None:
    """
    Tarea de background para re-síntesis completa.

    Se ejecuta fuera del ciclo de vida de la request — no puede lanzar
    HTTPException. Los errores se loguean como ERROR y la operación se aborta
    sin dejar el estado inconsistente (el .md original no se sobrescribe si
    la síntesis falla).
    """
    import pathlib
    from app.brain.writer import BrainWriter, _slugify
    from app.brain.ingest_router import IngestRouter
    from app.brain.synthesizer import DocumentSynthesizer

    def _do_resynthesize():
        mgr  = QdrantManager.get_instance()
        # Mismo slugify que en _check_chunks_exist — Qdrant almacena el slug
        slug = _slugify(source)

        # ── PASO 1: Recuperar todos los chunks del source en Qdrant ───────────
        result, _ = mgr.client.scroll(
            collection_name="knowledge",
            scroll_filter=qdrant_models.Filter(must=[
                qdrant_models.FieldCondition(
                    key="source",
                    match=qdrant_models.MatchValue(value=slug),
                ),
                qdrant_models.FieldCondition(
                    key="search_space_id",
                    match=qdrant_models.MatchValue(value=str(search_space_id)),
                ),
            ]),
            with_payload=True,
            limit=500,
        )

        raw_text = "\n\n".join(
            p.payload.get("text", "") for p in result if p.payload
        ).strip()

        if not raw_text:
            _resynth_logger.warning(
                "[resynthesize] chunks vacíos tras scroll source='%s' space=%d — abortando",
                source, search_space_id,
            )
            return

        _resynth_logger.info(
            "[resynthesize] %d chunks recuperados source='%s' total_chars=%d",
            len(result), source, len(raw_text),
        )

        # ── PASO 2: Re-sintetizar pasaporte con DocumentSynthesizer ───────────
        # synthesize() devuelve dict {"md_content": str, "tags": list, ...}
        synth_result = DocumentSynthesizer().synthesize(
            source=source,
            file_type="md",
            full_text=raw_text,
            model=model,
            provider=provider,
        )
        new_md = synth_result.get("md_content", "")

        if not new_md.strip():
            _resynth_logger.error(
                "[resynthesize] síntesis devolvió contenido vacío source='%s' — abortando",
                source,
            )
            return

        # ── PASO 3: Archivar versión actual antes de sobrescribir ─────────────
        writer = BrainWriter()
        old_content = writer.read(source) or ""

        if old_content:
            hdir = (
                pathlib.Path(os.getenv("BRAIN_DIR", "/data/brain"))
                / ".history"
                / _slugify(source)
            )
            hdir.mkdir(parents=True, exist_ok=True)
            n  = len(list(hdir.glob("v*.md"))) + 1
            ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            (hdir / f"v{n:03d}_{ts}.md").write_text(old_content, encoding="utf-8")
            _resynth_logger.debug(
                "[resynthesize] versión histórica guardada source='%s' v%03d", source, n,
            )

        # ── PASO 4: Escribir nuevo .md y re-vectorizar ────────────────────────
        writer.write(source, new_md)

        ingest_results = IngestRouter(mgr.client).route(
            md_content=new_md,
            blocks=[],
            source=source,
            search_space_id=str(search_space_id),
        )
        total_chunks = sum(v.get("chunks_created", 0) for v in ingest_results.values())

        # ── PASO 5: Actualizar timestamps Redis ───────────────────────────────
        try:
            r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
            now_iso = datetime.datetime.utcnow().isoformat()
            r.set(f"brain:last_ingest:space:{search_space_id}", now_iso)
            r.set(f"brain:last_ingest_doc:space:{search_space_id}", source)
        except Exception as exc:
            _resynth_logger.warning(
                "[resynthesize] Redis no actualizado source='%s': %s", source, exc,
            )

        _resynth_logger.info(
            "[resynthesize] DONE source='%s' space=%d model=%s "
            "new_chars=%d chunks_revectorizados=%d collections=%s",
            source, search_space_id, model,
            len(new_md), total_chunks, list(ingest_results.keys()),
        )

    try:
        await asyncio.to_thread(_do_resynthesize)
    except Exception as exc:
        _resynth_logger.error(
            "[resynthesize] ERROR inesperado source='%s' space=%d: %s",
            source, search_space_id, exc, exc_info=True,
        )


# ── F6.B.08 — Grafo de relaciones entre pasaportes ────────────────────────────

_graph_logger = logging.getLogger("surfsense.brain.graph")


@router.get("/graph")
async def brain_graph(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Construye y devuelve el grafo de relaciones entre pasaportes del Brain.

    El grafo se calcula desde los pasaportes ``.md`` del search space combinando
    tres tipos de relaciones:

      - **related** (peso 3) — enlaces explícitos declarados en el frontmatter YAML
        (campo ``related:`` con filenames o [[wikilinks]]).
      - **tag_overlap** (peso = nº de tags compartidos) — pasaportes que comparten
        al menos una etiqueta semántica; peso mayor = mayor afinidad temática.
      - **serie** (peso 2) — documentos que pertenecen a la misma serie detectada
        por el prefijo del nombre de fichero (módulos, capítulos, partes...).

    Cada nodo incluye id, label, tipo, dominio, color hex, tags, icono y
    ``size_factor`` para que la UI ajuste el tamaño visual por importancia.

    Cada edge incluye source, target, tipo, peso y label descriptivo.
    Los edges duplicados (mismo par + mismo tipo) se eliminan antes de devolver.

    El grafo se persiste automáticamente en ``{BRAIN_DIR}/_graph.json`` tras
    cada construcción para que la UI pueda servirlo desde caché si lo desea.

    Respuesta:
        nodes       — lista de nodos (uno por pasaporte del space).
        edges       — lista de relaciones deduplicadas.
        node_count  — total de nodos (shortcut).
        edge_count  — total de edges (shortcut).
        meta        — ``{total_docs, total_edges}`` del constructor BrainGraph.

    Devuelve ``nodes: [], edges: []`` si el space no tiene pasaportes.
    Requiere autenticación y membresía al search space (o superusuario).
    """
    from app.brain.graph import BrainGraph

    await _require_space_access(search_space_id, db, current_user)

    _graph_logger.info(
        "[brain_graph] solicitado por user=%s space=%d",
        current_user.id, search_space_id,
    )

    # ── 1. Obtener pasaportes del space (mismo helper que /list) ─────────────
    passports = await _list_passports_for_space(search_space_id)

    _graph_logger.debug(
        "[brain_graph] %d pasaportes recuperados para space=%d",
        len(passports), search_space_id,
    )

    if not passports:
        _graph_logger.info(
            "[brain_graph] sin pasaportes en space=%d → grafo vacío", search_space_id,
        )
        return {
            "nodes":      [],
            "edges":      [],
            "node_count": 0,
            "edge_count": 0,
            "meta":       {"total_docs": 0, "total_edges": 0},
        }

    # ── 2. Construir grafo (síncrono: I/O disco + cálculo → thread pool) ─────
    # BrainGraph.build() lee el cuerpo de cada .md para extraer el resumen.
    # Se delega al thread pool para no bloquear el event loop.
    def _build_and_save() -> dict:
        graph_data = BrainGraph().build(passports)

        # Persistencia opcional: permite servir el grafo desde disco en el futuro
        try:
            BrainGraph().save(graph_data)
        except Exception as exc:
            _graph_logger.warning(
                "[brain_graph] No se pudo persistir _graph.json: %s", exc,
            )

        return graph_data

    graph_data = await asyncio.to_thread(_build_and_save)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    meta  = graph_data.get("meta", {})

    _graph_logger.info(
        "[brain_graph] OK space=%d nodes=%d edges=%d",
        search_space_id, len(nodes), len(edges),
    )

    return {
        "nodes":      nodes,
        "edges":      edges,
        # Shortcuts para que la UI no calcule .length del array
        "node_count": len(nodes),
        "edge_count": len(edges),
        "meta":       meta,
    }


# ── F6.B.09 — Ingesta de URL (POST + pipeline en background) ─────────────────
# ── F6.B.10 — Stream SSE del progreso de ingesta (GET)        ─────────────────

_ingest_logger = logging.getLogger("surfsense.brain.ingest")


class IngestUrlRequest(BaseModel):
    """Body de POST /ingest/url."""
    url: str
    search_space_id: int
    model: str | None = None
    provider: str | None = None


@router.post("/ingest/url")
async def ingest_url(
    body: IngestUrlRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Inicia la ingesta de una URL.

    Flujo de 4 fases (todas en background):
      1. extraction  — WebExtractor.extract_url() → bloques
      2. cleaning    — concatenación de texto limpio
      3. embedding   — DocumentSynthesizer → .md → BrainWriter
      4. qdrant      — IngestRouter → vectores Qdrant + Redis timestamps

    Devuelve job_id para suscribirse a GET /ingest/stream?job_id=<id>.
    """
    await _require_space_access(body.search_space_id, db, current_user)

    _cleanup_stale_jobs()

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _ingest_jobs[job_id] = {"queue": queue, "created_at": time.monotonic()}

    _ingest_logger.info(
        "[ingest_url] job=%s url='%s' space=%d user=%s",
        job_id, body.url, body.search_space_id, current_user.id,
    )

    background_tasks.add_task(
        _run_ingest_pipeline,
        url=body.url,
        search_space_id=body.search_space_id,
        model=body.model,
        provider=body.provider,
        queue=queue,
        job_id=job_id,
    )

    return {"job_id": job_id, "status": "queued", "background": True}


async def _run_ingest_pipeline(
    url: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
    queue: asyncio.Queue,
    job_id: str,
) -> None:
    """
    Pipeline de ingesta de 4 fases. Emite eventos SSE a la cola.
    None en la cola = señal de fin de stream.

    Cada evento tiene la forma:
      {"phase": str, "status": "running"|"ok"|"error", "chunks": int, "detail": str}
    """
    from app.brain.extractors.web import WebExtractor
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.ingest_router import IngestRouter
    from app.brain.writer import BrainWriter

    async def _emit(phase: str, status: str, chunks: int = 0, detail: str = "") -> None:
        event = {"phase": phase, "status": status, "chunks": chunks, "detail": detail}
        _ingest_logger.debug("[ingest_url] job=%s event=%s", job_id, event)
        await queue.put(event)

    try:
        # ── FASE 1: Extracción HTML/Markdown ──────────────────────────────────
        await _emit("extraction", "running")
        blocks = await asyncio.to_thread(WebExtractor().extract_url, url)
        if not blocks:
            await _emit("extraction", "error", detail="Sin contenido extraíble de la URL")
            return
        await _emit("extraction", "ok", chunks=len(blocks))
        _ingest_logger.info(
            "[ingest_url] job=%s extraction OK: %d bloques", job_id, len(blocks)
        )

        # ── FASE 2: Limpieza — concatenación del texto ────────────────────────
        await _emit("cleaning", "running")
        full_text = "\n\n".join(
            b.get("content", "") for b in blocks if b.get("content")
        ).strip()
        if not full_text:
            await _emit("cleaning", "error", detail="El contenido extraído está vacío")
            return
        await _emit("cleaning", "ok", chunks=len(blocks))

        # ── FASE 3: Síntesis del pasaporte con LLM ────────────────────────────
        await _emit("embedding", "running")
        synth_model    = model    or os.getenv("SYNTHESIS_MODEL",    "qwen2.5-coder:3b")
        synth_provider = provider or os.getenv("BRAIN_LLM_PROVIDER", "ollama")

        def _synthesize() -> str:
            result = DocumentSynthesizer().synthesize(
                source=url,
                file_type="html",
                full_text=full_text,
                blocks=blocks,
                model=synth_model,
                provider=synth_provider,
            )
            return result.get("md_content", "") if isinstance(result, dict) else str(result)

        new_md = await asyncio.to_thread(_synthesize)
        if not new_md.strip():
            await _emit("embedding", "error", detail="El sintetizador devolvió contenido vacío")
            return

        await asyncio.to_thread(BrainWriter().write, url, new_md)
        await _emit("embedding", "ok")
        _ingest_logger.info("[ingest_url] job=%s synthesis OK: %d chars", job_id, len(new_md))

        # ── FASE 4: Vectorización en Qdrant ───────────────────────────────────
        await _emit("qdrant", "running")

        def _vectorize() -> int:
            results = IngestRouter(QdrantManager.get_instance().client).route(
                md_content=new_md,
                blocks=blocks,
                source=url,
                search_space_id=str(search_space_id),
            )
            if isinstance(results, dict):
                return sum(v.get("chunks_created", 0) for v in results.values() if isinstance(v, dict))
            return 0

        total_chunks = await asyncio.to_thread(_vectorize)

        # Registrar timestamps en Redis (fire-and-forget)
        def _register_redis() -> None:
            try:
                r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
                now_iso = datetime.datetime.utcnow().isoformat()
                r.set(f"brain:last_ingest:space:{search_space_id}", now_iso)
                r.set(f"brain:last_ingest_doc:space:{search_space_id}", url)
            except Exception as exc:
                _ingest_logger.warning(
                    "[ingest_url] job=%s Redis timestamp error: %s", job_id, exc
                )

        await asyncio.to_thread(_register_redis)

        await _emit("qdrant", "ok", chunks=total_chunks)
        _ingest_logger.info(
            "[ingest_url] job=%s DONE url='%s' chunks=%d space=%d",
            job_id, url, total_chunks, search_space_id,
        )

    except Exception as exc:
        _ingest_logger.error(
            "[ingest_url] job=%s ERROR url='%s': %s", job_id, url, exc, exc_info=True
        )
        await _emit("qdrant", "error", detail=str(exc))
    finally:
        # None = señal de fin de stream para el generador SSE
        await queue.put(None)


@router.post("/ingest/file")
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    search_space_id: int = Form(...),
    model: str | None = Form(None),
    provider: str | None = Form(None),
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Ingesta de fichero subido por el usuario (multipart/form-data).

    El fichero se guarda temporalmente, se procesa con el extractor adecuado
    según la extensión y sigue el mismo pipeline de 4 fases que /ingest/url.
    """
    await _require_space_access(search_space_id, db, current_user)
    _cleanup_stale_jobs()

    filename = file.filename or "upload"
    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _ingest_jobs[job_id] = {"queue": queue, "created_at": time.monotonic()}

    _ingest_logger.info(
        "[ingest_file] job=%s filename='%s' space=%d user=%s",
        job_id, filename, search_space_id, current_user.id,
    )

    content = await file.read()

    background_tasks.add_task(
        _run_ingest_file_pipeline,
        filename=filename,
        content=content,
        search_space_id=search_space_id,
        model=model,
        provider=provider,
        queue=queue,
        job_id=job_id,
    )

    return {"job_id": job_id, "status": "queued", "background": True}


async def _run_ingest_file_pipeline(
    filename: str,
    content: bytes,
    search_space_id: int,
    model: str | None,
    provider: str | None,
    queue: asyncio.Queue,
    job_id: str,
) -> None:
    """Pipeline de 4 fases para ingesta de fichero subido."""
    import tempfile
    import pathlib
    import tempfile as _tempfile
    from app.brain.extractors.factory import ExtractorFactory
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.ingest_router import IngestRouter
    from app.brain.writer import BrainWriter

    async def _emit(phase: str, status: str, chunks: int = 0, detail: str = "") -> None:
        event = {"phase": phase, "status": status, "chunks": chunks, "detail": detail}
        _ingest_logger.debug("[ingest_file] job=%s event=%s", job_id, event)
        await queue.put(event)

    suffix = pathlib.Path(filename).suffix.lower() or ".bin"
    tmp_path = None

    try:
        with _tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # ── FASE 1: Extracción ────────────────────────────────────────────────
        await _emit("extraction", "running")

        def _extract():
            return ExtractorFactory.extract(tmp_path)

        blocks = await asyncio.to_thread(_extract)
        if not blocks:
            await _emit("extraction", "error", detail="Sin contenido extraíble del fichero")
            return
        await _emit("extraction", "ok", chunks=len(blocks))

        # ── FASE 2: Síntesis ──────────────────────────────────────────────────
        await _emit("synthesis", "running")
        synth_model    = model    or os.getenv("SYNTHESIS_MODEL",    "qwen2.5-coder:3b")
        synth_provider = provider or os.getenv("BRAIN_LLM_PROVIDER", "ollama")

        def _synthesize():
            full_text = "\n\n".join(b.get("content", "") for b in blocks if b.get("content")).strip()
            result = DocumentSynthesizer().synthesize(
                source=filename,
                file_type=suffix.lstrip("."),
                full_text=full_text,
                blocks=blocks,
                model=synth_model,
                provider=synth_provider,
                ingest_metadata={"ingest_origin": "file_upload", "ingest_path": filename},
            )
            return result.get("md_content", "") if isinstance(result, dict) else str(result)

        new_md = await asyncio.to_thread(_synthesize)
        if not new_md.strip():
            await _emit("synthesis", "error", detail="El sintetizador devolvió contenido vacío")
            return
        await asyncio.to_thread(BrainWriter().write, filename, new_md)
        await _emit("synthesis", "ok")

        # ── FASE 3: Chunking + vectorización ─────────────────────────────────
        await _emit("chunking", "running")

        def _vectorize():
            results = IngestRouter(QdrantManager.get_instance().client).route(
                md_content=new_md,
                blocks=blocks,
                source=filename,
                search_space_id=str(search_space_id),
            )
            if isinstance(results, dict):
                return sum(v.get("chunks_created", 0) for v in results.values() if isinstance(v, dict))
            return 0

        total_chunks = await asyncio.to_thread(_vectorize)
        await _emit("chunking", "ok", chunks=total_chunks)

        # ── FASE 4: Vectorización Qdrant confirmada ───────────────────────────
        await _emit("vectorization", "ok", chunks=total_chunks)
        _ingest_logger.info(
            "[ingest_file] job=%s DONE filename='%s' chunks=%d space=%d",
            job_id, filename, total_chunks, search_space_id,
        )

    except Exception as exc:
        _ingest_logger.error(
            "[ingest_file] job=%s ERROR filename='%s': %s", job_id, filename, exc, exc_info=True,
        )
        await _emit("vectorization", "error", detail=str(exc))
    finally:
        import os as _os
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass
        await queue.put(None)


@router.post("/ingest/path")
async def ingest_local_path(
    body: "IngestPathRequest",
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Ingesta de fichero o directorio por ruta local del servidor.

    Útil en despliegues on-premise donde los documentos ya residen
    en el sistema de ficheros del servidor o en un volumen montado.
    """
    from app.brain.extractors.factory import ExtractorFactory
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.ingest_router import IngestRouter
    from app.brain.writer import BrainWriter
    import pathlib

    await _require_space_access(body.search_space_id, db, current_user)
    _cleanup_stale_jobs()

    path_obj = pathlib.Path(body.local_path)
    if not path_obj.exists():
        raise HTTPException(status_code=400, detail=f"Ruta no encontrada: {body.local_path}")

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _ingest_jobs[job_id] = {"queue": queue, "created_at": time.monotonic()}

    _ingest_logger.info(
        "[ingest_path] job=%s path='%s' space=%d user=%s",
        job_id, body.local_path, body.search_space_id, current_user.id,
    )

    background_tasks.add_task(
        _run_ingest_path_pipeline,
        local_path=body.local_path,
        search_space_id=body.search_space_id,
        model=body.model,
        provider=body.provider,
        queue=queue,
        job_id=job_id,
    )

    return {"job_id": job_id, "status": "queued", "background": True}


class IngestPathRequest(BaseModel):
    """Body de POST /ingest/path."""
    local_path: str
    search_space_id: int
    model: str | None = None
    provider: str | None = None


async def _run_ingest_path_pipeline(
    local_path: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
    queue: asyncio.Queue,
    job_id: str,
) -> None:
    """Pipeline de 4 fases para ingesta desde ruta local."""
    import pathlib
    from app.brain.extractors.factory import ExtractorFactory
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.ingest_router import IngestRouter
    from app.brain.writer import BrainWriter

    async def _emit(phase: str, status: str, chunks: int = 0, detail: str = "") -> None:
        event = {"phase": phase, "status": status, "chunks": chunks, "detail": detail}
        _ingest_logger.debug("[ingest_path] job=%s event=%s", job_id, event)
        await queue.put(event)

    path_obj = pathlib.Path(local_path)
    filename = path_obj.name
    suffix   = path_obj.suffix.lower()

    try:
        # ── FASE 1: Extracción ────────────────────────────────────────────────
        await _emit("extraction", "running")

        def _extract():
            return ExtractorFactory.extract(local_path)

        blocks = await asyncio.to_thread(_extract)
        if not blocks:
            await _emit("extraction", "error", detail="Sin contenido extraíble del fichero")
            return
        await _emit("extraction", "ok", chunks=len(blocks))

        # ── FASE 2: Síntesis ──────────────────────────────────────────────────
        await _emit("synthesis", "running")
        synth_model    = model    or os.getenv("SYNTHESIS_MODEL",    "qwen2.5-coder:3b")
        synth_provider = provider or os.getenv("BRAIN_LLM_PROVIDER", "ollama")

        def _synthesize():
            full_text = "\n\n".join(b.get("content", "") for b in blocks if b.get("content")).strip()
            result = DocumentSynthesizer().synthesize(
                source=local_path,
                file_type=suffix.lstrip("."),
                full_text=full_text,
                blocks=blocks,
                model=synth_model,
                provider=synth_provider,
                ingest_metadata={"ingest_origin": "local", "ingest_path": local_path},
            )
            return result.get("md_content", "") if isinstance(result, dict) else str(result)

        new_md = await asyncio.to_thread(_synthesize)
        if not new_md.strip():
            await _emit("synthesis", "error", detail="El sintetizador devolvió contenido vacío")
            return
        await asyncio.to_thread(BrainWriter().write, local_path, new_md)
        await _emit("synthesis", "ok")

        # ── FASE 3: Chunking + vectorización ─────────────────────────────────
        await _emit("chunking", "running")

        def _vectorize():
            results = IngestRouter(QdrantManager.get_instance().client).route(
                md_content=new_md,
                blocks=blocks,
                source=local_path,
                search_space_id=str(search_space_id),
            )
            if isinstance(results, dict):
                return sum(v.get("chunks_created", 0) for v in results.values() if isinstance(v, dict))
            return 0

        total_chunks = await asyncio.to_thread(_vectorize)
        await _emit("chunking", "ok", chunks=total_chunks)

        # ── FASE 4: Vectorización Qdrant confirmada ───────────────────────────
        await _emit("vectorization", "ok", chunks=total_chunks)
        _ingest_logger.info(
            "[ingest_path] job=%s DONE path='%s' chunks=%d space=%d",
            job_id, local_path, total_chunks, search_space_id,
        )

    except Exception as exc:
        _ingest_logger.error(
            "[ingest_path] job=%s ERROR path='%s': %s", job_id, local_path, exc, exc_info=True,
        )
        await _emit("vectorization", "error", detail=str(exc))
    finally:
        await queue.put(None)


@router.get("/ingest/stream")
async def ingest_stream(
    job_id: str,
    current_user: User = Depends(current_active_user),
):
    """
    Server-Sent Events (SSE) del progreso de ingesta.

    El cliente debe usar fetch() + 'Authorization: Bearer <token>'
    ya que el EventSource nativo del navegador no soporta cabeceras personalizadas.

    Protocolo de eventos:
      data: {"phase":"extraction","status":"running","chunks":0,"detail":""}
      data: {"phase":"extraction","status":"ok","chunks":15,"detail":""}
      ...
      data: [DONE]

    Fases: extraction → cleaning → embedding → qdrant
    Status: "running" | "ok" | "error"
    """
    import json as _json

    job_meta = _ingest_jobs.get(job_id)
    if job_meta is None:
        raise HTTPException(status_code=404, detail=f"Job de ingesta no encontrado: {job_id}")

    queue: asyncio.Queue = job_meta["queue"]

    _ingest_logger.info(
        "[ingest_stream] SSE iniciado job=%s user=%s", job_id, current_user.id
    )

    async def _event_generator():
        """
        Genera eventos SSE hasta recibir None (fin normal)
        o hasta timeout de 120 s sin actividad (protección ante pipelines colgados).
        """
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    _ingest_logger.warning(
                        "[ingest_stream] job=%s timeout de 120 s sin eventos → cerrando", job_id
                    )
                    yield "data: [DONE]\n\n"
                    return

                if event is None:
                    # Fin limpio del pipeline
                    yield "data: [DONE]\n\n"
                    return

                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"

        finally:
            # Limpiar el job del registro al cerrar la conexión
            _ingest_jobs.pop(job_id, None)
            _ingest_logger.info("[ingest_stream] SSE cerrado job=%s", job_id)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # desactiva buffering nginx/proxy
            "Connection": "keep-alive",
        },
    )


@router.delete("/document/{source}")
async def delete_brain_document(
    source: str,
    search_space_id: int,              # int — PK de SearchSpace, requerido para multi-tenant
    current_user: User = Depends(current_active_user),
):
    """
    Elimina pasaporte .md del disco y vectores Qdrant del documento.

    Requiere search_space_id para garantizar aislamiento multi-tenant:
    solo se borran vectores del space indicado, nunca de otros tenants.
    """
    from app.brain.writer import BrainWriter
    from app.brain.qdrant_manager import QdrantManager

    logger.info(
        "[brain] delete START source='%s' space=%d user=%s",
        source, search_space_id, current_user.id,
    )
    md_deleted = BrainWriter().delete(source)
    QdrantManager.get_instance().delete_by_source(
        source=source,
        search_space_id=str(search_space_id),
    )
    logger.info(
        "[brain] delete DONE source='%s' md_deleted=%s",
        source, md_deleted,
    )
    return {"status": "deleted", "source": source, "md_deleted": md_deleted}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _rrf_fuse(
    list_a: list[dict],
    list_b: list[dict],
    budget: int,
    k: int = 60,
) -> list[dict]:
    """
    Reciprocal Rank Fusion de dos listas de chunks normalizados.

    Algoritmo (Cormack et al. 2009):
        score(doc) = Σ  1 / (k + rank_i)   para cada lista i donde aparece doc
        k = 60  → valor estándar; suaviza la diferencia entre posiciones altas.

    Ventaja sobre combinar scores: no requiere calibrar pesos ni normalizar
    escalas distintas (cosine similarity vs. BM25 tf-idf).

    Args:
        list_a:  Chunks Qdrant semánticos (ya normalizados a {"text", "source"}).
        list_b:  Chunks BM25 (ya normalizados a {"text", "source"}).
        budget:  Número máximo de chunks en el resultado final.
        k:       Constante RRF (default 60, no tocar salvo experimentos).

    Returns:
        Lista de chunks ordenada por score RRF descendente, máximo budget items.
        Deduplicación por fingerprint de los primeros 80 chars del texto.
    """
    scores: dict[str, float] = {}
    items:  dict[str, dict]  = {}

    for rank, chunk in enumerate(list_a, start=1):
        fp = (chunk.get("text") or "")[:80]
        if not fp:
            continue
        scores[fp] = scores.get(fp, 0.0) + 1.0 / (k + rank)
        items[fp]  = chunk

    for rank, chunk in enumerate(list_b, start=1):
        fp = (chunk.get("text") or "")[:80]
        if not fp:
            continue
        scores[fp] = scores.get(fp, 0.0) + 1.0 / (k + rank)
        if fp not in items:
            items[fp] = chunk   # preferir el de list_a si ya existe (Qdrant tiene más metadata)

    sorted_fps = sorted(scores, key=lambda f: scores[f], reverse=True)
    return [items[fp] for fp in sorted_fps[:budget]]


def _extract_chunks(qdrant_results: list, budget: int, max_chars: int) -> list[dict]:
    """
    Extrae y trunca chunks de resultados Qdrant (objetos ScoredPoint).

    Args:
        qdrant_results: Lista de ScoredPoint de Qdrant.
        budget:         Máximo de chunks a incluir (ModelProfile.retrieval_chunk_budget).
        max_chars:      Longitud máxima de texto por chunk (ModelProfile.retrieval_chunk_max_chars).

    Returns:
        Lista de dicts {"text": str, "source": str} listos para _build_context().
    """
    chunks = []
    for r in qdrant_results[:budget]:
        payload = r.payload if hasattr(r, "payload") else {}
        text    = (payload.get("text", "") or "")[:max_chars]
        source  = payload.get("source", "")
        if text.strip():
            chunks.append({"text": text, "source": source})
    return chunks


def _extract_chunks_from_orm(orm_chunks: list, budget: int, max_chars: int) -> list[dict]:
    """
    Extrae y trunca chunks de objetos ORM Chunk (resultado de BM25).

    Args:
        orm_chunks: Lista de objetos Chunk ORM de ChucksHybridSearchRetriever.
        budget:     Máximo de chunks (ModelProfile.retrieval_chunk_budget).
        max_chars:  Longitud máxima por chunk (ModelProfile.retrieval_chunk_max_chars).

    Returns:
        Lista de dicts {"text": str, "source": str}.
    """
    result = []
    for c in orm_chunks[:budget]:
        text   = (getattr(c, "content", "") or "")[:max_chars]
        doc    = getattr(c, "document", None)
        source = getattr(doc, "title", "") if doc else ""
        if text.strip():
            result.append({"text": text, "source": source})
    return result


def _sources_from_orm(orm_chunks: list, budget: int) -> list[str]:
    """
    Extrae títulos únicos de documentos de chunks ORM (para BrainQueryResponse.sources).

    Args:
        orm_chunks: Lista de objetos Chunk ORM.
        budget:     Máximo de chunks a considerar.

    Returns:
        Lista de títulos de documento únicos (sin vacíos).
    """
    seen    = set()
    sources = []
    for c in orm_chunks[:budget]:
        doc   = getattr(c, "document", None)
        title = getattr(doc, "title", "") if doc else ""
        if title and title not in seen:
            seen.add(title)
            sources.append(title)
    return sources


def _extract_chunks_from_web(web_docs: list, budget: int, max_chars: int) -> list[dict]:
    """
    Extrae chunks de documentos web devueltos por web_search_service.search().

    La firma real de search() devuelve lista de dicts con:
      - content: str — snippet/descripción del resultado
      - document.title: str — título de la página

    Args:
        web_docs:  Lista de dicts devueltos por web_search_service.search().
        budget:    Máximo de chunks (ModelProfile.retrieval_chunk_budget).
        max_chars: Longitud máxima por chunk (ModelProfile.retrieval_chunk_max_chars).

    Returns:
        Lista de dicts {"text": str, "source": str}.
    """
    chunks = []
    for d in web_docs[:budget]:
        content = (d.get("content") or "")[:max_chars]
        title   = (d.get("document") or {}).get("title", "Web")
        if content.strip():
            chunks.append({"text": content, "source": title})
    return chunks


def _build_context(chunks: list[dict]) -> str:
    """
    Construye el string de contexto para el prompt del LLM.

    Cada chunk se formatea con su fuente y se separa con '---' para
    facilitar la citación al modelo.

    Args:
        chunks: Lista de dicts {"text": str, "source": str}.

    Returns:
        String de contexto listo para incluir en el prompt.
    """
    parts = []
    for c in chunks:
        source_label = f"[{c['source']}] " if c.get("source") else ""
        parts.append(f"{source_label}{c['text']}")
    return "\n\n---\n\n".join(parts)


def _call_llm(question: str, context: str, tier: str, history: list[dict]) -> str:
    """
    Llama al LLM activo con el contexto construido por la cascada.

    Adapta el system prompt y la estructura del prompt al tier del modelo:
    - small: prompt compacto sin historial (ventana de contexto limitada)
    - medium/claude: prompt completo con historial de los últimos turnos

    El número de turnos de historial incluidos se controla por _HISTORY_TURNS,
    que refleja la capacidad real de la ventana de contexto por tier.

    El proveedor y modelo se leen de variables de entorno (LLM_PROVIDER,
    SYNTHESIS_MODEL) — cero hardcode.

    Args:
        question: Pregunta del usuario.
        context:  Contexto construido por la cascada (vacío en L0).
        tier:     Tier del modelo ("small" | "medium" | "claude").
        history:  Historial de conversación (lista de dicts role/content).

    Returns:
        Respuesta del LLM como string.
    """
    client = LLMClient()
    max_history_turns = _HISTORY_TURNS.get(tier, 4)

    if context:
        system = (
            "Eres un asistente técnico experto. Responde basándote EXCLUSIVAMENTE en el "
            "contexto proporcionado. Cita la fuente cuando sea relevante. "
            "Si la respuesta no está en el contexto, indícalo explícitamente."
        )
        if tier == "small":
            # Prompt compacto para modelos con ventana reducida — sin historial
            prompt = f"Contexto:\n{context}\n\nPregunta: {question}\nRespuesta:"
        else:
            history_text = "\n".join(
                f"{m['role']}: {m['content']}"
                for m in (history or [])[-max_history_turns:]
            )
            prompt = (
                f"{history_text}\n\n"
                f"Contexto:\n{context}\n\n"
                f"Pregunta: {question}"
            ).lstrip()
    else:
        # L0 — sin contexto: LLM libre con disclaimer
        system = (
            "Eres un asistente técnico. No dispones de documentación específica "
            "sobre esta pregunta. Responde desde tu conocimiento general e indica "
            "que no tienes documentación de referencia en el sistema."
        )
        prompt = question

    logger.debug(
        "[brain_query] _call_llm tier=%s provider=%s model=%s context_chars=%d",
        tier, DEFAULT_PROVIDER, SYNTHESIS_MODEL, len(context),
    )
    return client.generate(
        prompt=prompt,
        system=system,
        provider=DEFAULT_PROVIDER,
        model=SYNTHESIS_MODEL,
    )


# ── Helper: verificar acceso al search space ──────────────────────────────────
async def _require_space_access(
    search_space_id: int,
    db: AsyncSession,
    current_user,
) -> None:
    """
    Lanza HTTP 403 si el usuario no es miembro del search space.
    Los superusuarios tienen acceso a todos los spaces.
    """
    from app.db import SearchSpaceMembership

    if current_user.is_superuser:
        return
    row = await db.execute(
        select(SearchSpaceMembership).where(
            SearchSpaceMembership.search_space_id == search_space_id,
            SearchSpaceMembership.user_id == current_user.id,
        )
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Sin acceso al search space.")


# ── Helper: incrementar contador de nivel en Redis ────────────────────────────
def _track_level_usage(level: int, search_space_id: int) -> None:
    """
    Fire-and-forget: incrementa contador Redis con TTL 24h.
    Nunca lanza excepción — si Redis falla, la respuesta no se ve afectada.
    """
    try:
        r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
        key = f"brain:level:{level}:space:{search_space_id}"
        r.incr(key)
        r.expire(key, 86400)
    except Exception:
        pass


# ── Servicio compartido: lista de pasaportes del space ────────────────────────
async def _list_passports_for_space(search_space_id: int) -> list[dict]:
    """
    Servicio reutilizable — lo usan /list y /graph.
    NO llamar a otro endpoint FastAPI desde aquí (anti-patrón).
    """
    from app.brain.writer import BrainWriter

    all_docs = await asyncio.to_thread(BrainWriter().list_all)

    mgr = QdrantManager.get_instance()
    space_sources: set[str] = set()
    try:
        result, _ = mgr.client.scroll(
            collection_name="brain",
            scroll_filter=qdrant_models.Filter(must=[
                qdrant_models.FieldCondition(
                    key="search_space_id",
                    match=qdrant_models.MatchValue(value=str(search_space_id))
                )
            ]),
            with_payload=["source"],
            limit=10000,
        )
        space_sources = {p.payload.get("source") for p in result if p.payload}
    except Exception:
        pass

    passports = []
    for doc in all_docs:
        slug = doc.get("filename", "")
        if space_sources and slug not in space_sources:
            continue
        passports.append({
            "source":     slug,
            "title":      doc.get("title", slug),
            "domain":     doc.get("domain"),
            "subdomain":  doc.get("subdomain"),
            "tags":       doc.get("tags", []),
            "importance": int(doc.get("importance", 3)),
            "doc_type":   doc.get("type"),
            "updated_at": doc.get("updated_at", ""),
            "scopes":     doc.get("embedding_scope", ["brain", "knowledge"]),
            "confidence": doc.get("confidence"),
        })
    return passports


# ── Helper: guardar versión del pasaporte antes de sobrescribir ───────────────
async def _save_passport_version(source: str, content: str) -> None:
    """Guarda .md actual en .history/{slug}/vNNN_TS.md (thread pool)."""
    import pathlib
    from app.brain.writer import _slugify

    def _write():
        brain_dir = pathlib.Path(os.getenv("BRAIN_DIR", "/data/brain"))
        slug = _slugify(source)
        hdir = brain_dir / ".history" / slug
        hdir.mkdir(parents=True, exist_ok=True)
        n = len(list(hdir.glob("v*.md"))) + 1
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        (hdir / f"v{n:03d}_{ts}.md").write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)


# ── F1 — Conectores SurfSense en el pipeline Brain ────────────────────────────
#
# Dos endpoints nuevos:
#   GET  /api/v1/brain/connectors/available
#       Lista conectores del usuario que tienen tokens válidos y soporte Brain.
#   POST /api/v1/brain/ingest/connector/{connector_type}
#       Ingesta un ítem de un conector upstream usando SSE (mismo patrón que /ingest/file).
#
# Reutiliza: _ingest_jobs, _cleanup_stale_jobs, el stream GET /ingest/stream.

_connector_ingest_logger = logging.getLogger("surfsense.brain.connector_ingest")


@router.get("/connectors/available")
async def brain_connectors_available(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Lista los conectores del usuario que tienen soporte en el pipeline Brain.

    Filtra los SearchSourceConnector del usuario por:
      1. Que estén en el search space indicado.
      2. Que su connector_type tenga soporte en CONNECTOR_FAMILIES.

    Cada conector incluye:
        connector_id    — ID de la fila SearchSourceConnector (para el POST de ingesta).
        connector_type  — tipo canónico (ej: "ONEDRIVE_CONNECTOR").
        name            — nombre legible configurado por el usuario.
        family          — "storage" | "record" | "chat".
        token_ok        — True si el token de acceso parece válido.
        needs_reauth    — True si el usuario necesita re-autenticar.

    Requiere autenticación.
    """
    from app.db import SearchSourceConnector as _SSC
    from app.brain.connectors.surfsense_adapter import (
        is_brain_supported, get_family, verify_connector_token,
    )

    await _require_space_access(search_space_id, db, current_user)

    result = await db.execute(
        select(_SSC).where(
            _SSC.user_id == current_user.id,
            _SSC.search_space_id == search_space_id,
        )
    )
    all_connectors = result.scalars().all()

    available = []
    for conn in all_connectors:
        ctype = str(conn.connector_type).upper()
        if not is_brain_supported(ctype):
            continue
        token_info = await verify_connector_token(conn)
        available.append({
            "connector_id":   conn.id,
            "connector_type": ctype,
            "name":           conn.name,
            "family":         get_family(ctype),
            "token_ok":       token_info["ok"],
            "needs_reauth":   token_info["needs_reauth"],
            "detail":         token_info.get("detail", ""),
        })

    _connector_ingest_logger.info(
        "[connectors_available] user=%s space=%d total=%d supported=%d",
        current_user.id, search_space_id, len(all_connectors), len(available),
    )

    return {"connectors": available, "count": len(available)}


class IngestConnectorRequest(BaseModel):
    """Body de POST /ingest/connector/{connector_type}."""
    connector_id:   int
    item_id:        str
    filename:       str
    search_space_id: int
    model:    str | None = None
    provider: str | None = None


@router.post("/ingest/connector/{connector_type}")
async def ingest_connector_item(
    connector_type: str,
    body: IngestConnectorRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Ingesta un ítem de un conector SurfSense upstream en el pipeline Brain.

    Soporta los tres tipos de familia:
      - storage (OneDrive, Google Drive, Dropbox): descarga el fichero y
        lo ingesta con el extractor adecuado según la extensión.
      - record  (Airtable, ClickUp, Linear...): serializa a Markdown.
      - chat    (Slack, Teams, Discord, Gmail): agrupa mensajes a Markdown.

    Flujo de 4 fases (background + SSE):
      1. extraction   — descarga + extractor adecuado
      2. synthesis    — DocumentSynthesizer → pasaporte .md
      3. chunking     — BrainWriter + IngestRouter
      4. vectorization — Qdrant confirmado

    Devuelve job_id para suscribirse a GET /ingest/stream?job_id=<id>.

    Verifica antes de encolar:
      - El conector pertenece al usuario.
      - El conector está en el search space indicado.
      - El conector tiene token válido (family=storage).
    """
    from app.db import SearchSourceConnector as _SSC
    from app.brain.connectors.surfsense_adapter import (
        is_brain_supported, get_family, verify_connector_token,
    )

    await _require_space_access(body.search_space_id, db, current_user)

    ctype = connector_type.upper()
    if not is_brain_supported(ctype):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Conector '{ctype}' no tiene soporte en el pipeline Brain. "
                f"Soportados: OneDrive, Google Drive, Dropbox, Slack, Teams, "
                f"Discord, Gmail, Airtable, ClickUp, Linear, BookStack, Calendar, Luma, Elasticsearch."
            ),
        )

    # Verificar que el conector pertenece al usuario y al space
    result = await db.execute(
        select(_SSC).where(
            _SSC.id == body.connector_id,
            _SSC.user_id == current_user.id,
            _SSC.search_space_id == body.search_space_id,
        )
    )
    connector_record = result.scalar_one_or_none()
    if connector_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Conector {body.connector_id} no encontrado para este usuario y space.",
        )

    # Verificar token antes de encolar (evita iniciar un job que fallará)
    token_info = await verify_connector_token(connector_record)
    if not token_info["ok"] and token_info.get("needs_reauth"):
        raise HTTPException(
            status_code=401,
            detail=(
                f"Token expirado o inválido para el conector {ctype}. "
                f"Re-autentícalo en Configuración → Conectores antes de ingestar. "
                f"Detalle: {token_info.get('detail', '')}"
            ),
        )

    _cleanup_stale_jobs()

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _ingest_jobs[job_id] = {"queue": queue, "created_at": time.monotonic()}

    _connector_ingest_logger.info(
        "[ingest_connector] job=%s connector=%s item_id=%s filename='%s' space=%d user=%s",
        job_id, ctype, body.item_id, body.filename, body.search_space_id, current_user.id,
    )

    # Pasar config serializado — el conector ORM no se puede pasar entre threads directamente
    connector_config = dict(connector_record.config or {})

    background_tasks.add_task(
        _run_ingest_connector_pipeline,
        connector_type=ctype,
        connector_id=body.connector_id,
        connector_config=connector_config,
        item_id=body.item_id,
        filename=body.filename,
        search_space_id=body.search_space_id,
        model=body.model,
        provider=body.provider,
        queue=queue,
        job_id=job_id,
    )

    return {"job_id": job_id, "status": "queued", "background": True}


async def _run_ingest_connector_pipeline(
    connector_type: str,
    connector_id: int,
    connector_config: dict,
    item_id: str,
    filename: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
    queue: asyncio.Queue,
    job_id: str,
) -> None:
    """
    Pipeline de 4 fases para ingesta desde conector SurfSense upstream.

    Misma estructura que _run_ingest_file_pipeline.
    None en la cola = señal de fin de stream.
    """
    from app.brain.connectors.surfsense_adapter import (
        SurfSenseStorageAdapter, get_family,
    )
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.ingest_router import IngestRouter
    from app.brain.writer import BrainWriter
    from pathlib import Path

    async def _emit(phase: str, status: str, chunks: int = 0, detail: str = "") -> None:
        event = {"phase": phase, "status": status, "chunks": chunks, "detail": detail}
        _connector_ingest_logger.debug("[ingest_connector] job=%s event=%s", job_id, event)
        await queue.put(event)

    family = get_family(connector_type)

    try:
        # ── FASE 1: Extracción desde el conector ──────────────────────────────
        await _emit("extraction", "running")

        if family == "storage":
            # Crear objeto fake del conector con el config serializado
            # para pasarlo al adaptador (evita dependencia de ORM en thread)
            class _FakeConnector:
                def __init__(self, cid, ctype, cfg):
                    self.id = cid
                    self.connector_type = ctype
                    self.config = cfg

            fake_conn = _FakeConnector(connector_id, connector_type, connector_config)
            adapter = SurfSenseStorageAdapter()
            adapted = await adapter.fetch_item(
                connector_record=fake_conn,
                item_id=item_id,
                filename=filename,
                db=None,
            )
        else:
            # record/chat: no implementamos descarga automática en esta fase.
            # El endpoint de record/chat requiere que el caller pase los datos pre-fetched.
            # Para el pipeline automático de storage, family != storage es un no-op aquí.
            await _emit("extraction", "error",
                        detail=f"Ingesta automática no implementada para familia '{family}'. "
                               f"Usa el endpoint de ingesta manual para {connector_type}.")
            return

        blocks = adapted.blocks
        if not blocks:
            await _emit("extraction", "error", detail="Sin contenido extraíble del conector")
            return

        await _emit("extraction", "ok", chunks=len(blocks))
        _connector_ingest_logger.info(
            "[ingest_connector] job=%s extraction OK: %d bloques family=%s",
            job_id, len(blocks), family,
        )

        # ── FASE 2: Síntesis del pasaporte con LLM ────────────────────────────
        await _emit("synthesis", "running")
        synth_model    = model    or os.getenv("SYNTHESIS_MODEL",    "qwen2.5-coder:3b")
        synth_provider = provider or os.getenv("BRAIN_LLM_PROVIDER", "ollama")

        def _synthesize() -> str:
            full_text = "\n\n".join(
                b.get("content", "") for b in blocks if b.get("content")
            ).strip()
            result = DocumentSynthesizer().synthesize(
                source=adapted.source_name,
                file_type=adapted.file_type,
                full_text=full_text,
                blocks=blocks,
                model=synth_model,
                provider=synth_provider,
                ingest_metadata={
                    "ingest_origin": "connector",
                    "connector_type": connector_type,
                    "item_id": item_id,
                    **adapted.metadata,
                },
            )
            return result.get("md_content", "") if isinstance(result, dict) else str(result)

        new_md = await asyncio.to_thread(_synthesize)
        if not new_md.strip():
            await _emit("synthesis", "error", detail="El sintetizador devolvió contenido vacío")
            return

        await asyncio.to_thread(BrainWriter().write, adapted.source_name, new_md)
        await _emit("synthesis", "ok")
        _connector_ingest_logger.info(
            "[ingest_connector] job=%s synthesis OK: %d chars", job_id, len(new_md)
        )

        # ── FASE 3: Chunking + vectorización en Qdrant ────────────────────────
        await _emit("chunking", "running")

        def _vectorize() -> int:
            results = IngestRouter(QdrantManager.get_instance().client).route(
                md_content=new_md,
                blocks=blocks,
                source=adapted.source_name,
                search_space_id=str(search_space_id),
            )
            if isinstance(results, dict):
                return sum(v.get("chunks_created", 0) for v in results.values() if isinstance(v, dict))
            return 0

        total_chunks = await asyncio.to_thread(_vectorize)
        await _emit("chunking", "ok", chunks=total_chunks)

        # ── FASE 4: Confirmación + Redis ─────────────────────────────────────
        import redis as _redis_lib
        def _register_redis() -> None:
            try:
                r = _redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
                now_iso = datetime.datetime.utcnow().isoformat()
                r.set(f"brain:last_ingest:space:{search_space_id}", now_iso)
                r.set(f"brain:last_ingest_doc:space:{search_space_id}", adapted.source_name)
            except Exception as exc:
                _connector_ingest_logger.warning(
                    "[ingest_connector] job=%s Redis timestamp error: %s", job_id, exc
                )

        await asyncio.to_thread(_register_redis)
        await _emit("vectorization", "ok", chunks=total_chunks)

        _connector_ingest_logger.info(
            "[ingest_connector] job=%s DONE connector=%s item='%s' chunks=%d space=%d",
            job_id, connector_type, item_id, total_chunks, search_space_id,
        )

    except PermissionError as exc:
        _connector_ingest_logger.error(
            "[ingest_connector] job=%s AUTH ERROR: %s", job_id, exc
        )
        await _emit("extraction", "error", detail=f"Token expirado: {exc}. Re-autentícalo en Configuración → Conectores.")
    except Exception as exc:
        _connector_ingest_logger.error(
            "[ingest_connector] job=%s ERROR: %s", job_id, exc, exc_info=True
        )
        await _emit("vectorization", "error", detail=str(exc))
    finally:
        await queue.put(None)


# ── Job registry para ingesta SSE ─────────────────────────────────────────────
_ingest_jobs: dict[str, dict] = {}
_JOB_TTL = 3600.0  # 1 hora


def _cleanup_stale_jobs() -> None:
    """Elimina jobs con más de 1 hora de antigüedad."""
    now = time.monotonic()
    stale = [jid for jid, m in _ingest_jobs.items() if now - m["created_at"] > _JOB_TTL]
    for jid in stale:
        _ingest_jobs.pop(jid, None)


# ── BUG-1 — Vocabulary CRUD endpoints ────────────────────────────────────────
#
# Helper: convierte search_space_id → scope label esperado por el frontend.

def _scope(search_space_id) -> str:
    return "space" if search_space_id is not None else "global"


def _domain_to_dict(d: BrainDomain) -> dict:
    return {
        "id":            d.id,
        "domain_key":    d.domain_key,
        "label":         d.label,
        "description":   getattr(d, "description", None) or "",
        "signal_tags":   d.signal_tags or [],
        "signal_kw":     d.signal_kw or [],
        "scope":         _scope(d.search_space_id),
        "is_active":     d.is_active,
        "search_space_id": d.search_space_id,
    }


def _doctype_to_dict(d: BrainDocType) -> dict:
    return {
        "id":             d.id,
        "type_key":       d.type_key,
        "label":          d.label,
        "description":    None,
        "signal_formats": d.signal_formats or [],
        "signal_kw":      d.signal_kw or [],
        "scope":          _scope(d.search_space_id),
        "is_active":      d.is_active,
        "search_space_id": d.search_space_id,
    }


def _hint_to_dict(h: BrainEntityHint) -> dict:
    return {
        "id":           h.id,
        "hint_key":     h.hint_key,
        "label":        h.label,
        "domain_key":   h.domain_key,
        "doc_type_key": h.doc_type_key,
        "patterns":     h.patterns or [],
        "examples":     h.examples or [],
        "is_active":    h.is_active,
        "search_space_id": h.search_space_id,
    }


def _vocab_to_dict(v: BrainVocabulary) -> dict:
    return {
        "id":            v.id,
        "canonical_tag": v.canonical_tag,
        "aliases":       v.aliases or [],
        "scope":         _scope(v.search_space_id),
        "is_active":     v.is_active,
        "search_space_id": v.search_space_id,
    }


# ── Domains ───────────────────────────────────────────────────────────────────

@router.get("/admin/domains")
async def list_domains(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    result = await db.execute(
        select(BrainDomain).where(
            (BrainDomain.search_space_id == search_space_id) |
            (BrainDomain.search_space_id.is_(None))
        ).order_by(BrainDomain.domain_key)
    )
    return [_domain_to_dict(d) for d in result.scalars().all()]


@router.post("/admin/domains", status_code=201)
async def create_domain(
    body: dict,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    domain = BrainDomain(
        domain_key=body["domain_key"],
        label=body["label"],
        description=body.get("description", ""),
        signal_tags=body.get("signal_tags", []),
        signal_kw=body.get("signal_kw", []),
        search_space_id=search_space_id,
        is_active=body.get("is_active", True),
    )
    db.add(domain)
    await db.commit()
    await db.refresh(domain)
    return _domain_to_dict(domain)


@router.put("/admin/domains/{domain_id}")
async def update_domain(
    domain_id: int,
    body: dict,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainDomain).where(BrainDomain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Dominio no encontrado")
    for field in ("label", "description", "signal_tags", "signal_kw", "is_active"):
        if field in body:
            setattr(domain, field, body[field])
    await db.commit()
    await db.refresh(domain)
    return _domain_to_dict(domain)


@router.delete("/admin/domains/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainDomain).where(BrainDomain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Dominio no encontrado")
    await db.delete(domain)
    await db.commit()


@router.patch("/admin/domains/{domain_id}/toggle-active")
async def toggle_domain_active(
    domain_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainDomain).where(BrainDomain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Dominio no encontrado")
    domain.is_active = not domain.is_active
    await db.commit()
    await db.refresh(domain)
    return _domain_to_dict(domain)


# ── Doc Types ─────────────────────────────────────────────────────────────────

@router.get("/admin/doc-types")
async def list_doc_types(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    result = await db.execute(
        select(BrainDocType).where(
            (BrainDocType.search_space_id == search_space_id) |
            (BrainDocType.search_space_id.is_(None))
        ).order_by(BrainDocType.type_key)
    )
    return [_doctype_to_dict(d) for d in result.scalars().all()]


@router.post("/admin/doc-types", status_code=201)
async def create_doc_type(
    body: dict,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    dt = BrainDocType(
        type_key=body["type_key"],
        label=body["label"],
        signal_formats=body.get("signal_formats", []),
        signal_kw=body.get("signal_kw", []),
        search_space_id=search_space_id,
        is_active=body.get("is_active", True),
    )
    db.add(dt)
    await db.commit()
    await db.refresh(dt)
    return _doctype_to_dict(dt)


@router.put("/admin/doc-types/{dt_id}")
async def update_doc_type(
    dt_id: int,
    body: dict,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainDocType).where(BrainDocType.id == dt_id))
    dt = result.scalar_one_or_none()
    if not dt:
        raise HTTPException(status_code=404, detail="Tipo no encontrado")
    for field in ("label", "signal_formats", "signal_kw", "is_active"):
        if field in body:
            setattr(dt, field, body[field])
    await db.commit()
    await db.refresh(dt)
    return _doctype_to_dict(dt)


@router.delete("/admin/doc-types/{dt_id}", status_code=204)
async def delete_doc_type(
    dt_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainDocType).where(BrainDocType.id == dt_id))
    dt = result.scalar_one_or_none()
    if not dt:
        raise HTTPException(status_code=404, detail="Tipo no encontrado")
    await db.delete(dt)
    await db.commit()


# ── Entity Hints ──────────────────────────────────────────────────────────────

@router.get("/admin/entity-hints")
async def list_entity_hints(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    result = await db.execute(
        select(BrainEntityHint).where(
            (BrainEntityHint.search_space_id == search_space_id) |
            (BrainEntityHint.search_space_id.is_(None))
        ).order_by(BrainEntityHint.hint_key)
    )
    return [_hint_to_dict(h) for h in result.scalars().all()]


@router.post("/admin/entity-hints", status_code=201)
async def create_entity_hint(
    body: dict,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    hint = BrainEntityHint(
        hint_key=body["hint_key"],
        label=body["label"],
        domain_key=body.get("domain_key"),
        doc_type_key=body.get("doc_type_key"),
        patterns=body.get("patterns", []),
        examples=body.get("examples", []),
        search_space_id=search_space_id,
        is_active=body.get("is_active", True),
    )
    db.add(hint)
    await db.commit()
    await db.refresh(hint)
    return _hint_to_dict(hint)


@router.put("/admin/entity-hints/{hint_id}")
async def update_entity_hint(
    hint_id: int,
    body: dict,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainEntityHint).where(BrainEntityHint.id == hint_id))
    hint = result.scalar_one_or_none()
    if not hint:
        raise HTTPException(status_code=404, detail="Entity hint no encontrado")
    for field in ("label", "domain_key", "doc_type_key", "patterns", "examples", "is_active"):
        if field in body:
            setattr(hint, field, body[field])
    await db.commit()
    await db.refresh(hint)
    return _hint_to_dict(hint)


@router.delete("/admin/entity-hints/{hint_id}", status_code=204)
async def delete_entity_hint(
    hint_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainEntityHint).where(BrainEntityHint.id == hint_id))
    hint = result.scalar_one_or_none()
    if not hint:
        raise HTTPException(status_code=404, detail="Entity hint no encontrado")
    await db.delete(hint)
    await db.commit()


# ── Vocabulary ────────────────────────────────────────────────────────────────
# IMPORTANTE: /vocabulary/lookup ANTES de /vocabulary/{id} — FastAPI evalúa
# rutas en orden de registro; sin esta precaución "lookup" sería tratado como id.

@router.get("/admin/vocabulary/lookup")
async def lookup_vocabulary(
    tag: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """Busca la tag canónica correspondiente a un alias o a la propia canónica."""
    from sqlalchemy import or_, func
    result = await db.execute(
        select(BrainVocabulary).where(
            or_(
                BrainVocabulary.canonical_tag == tag,
                func.jsonb_exists(BrainVocabulary.aliases, tag),
            )
        ).limit(1)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        return {"canonical_tag": None, "aliases": [], "found": False}
    return {
        "canonical_tag": entry.canonical_tag,
        "aliases":       entry.aliases or [],
        "found":         True,
    }


@router.get("/admin/vocabulary")
async def list_vocabulary(
    search_space_id: int,
    q: str | None = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    stmt = select(BrainVocabulary).where(
        (BrainVocabulary.search_space_id == search_space_id) |
        (BrainVocabulary.search_space_id.is_(None))
    )
    if q:
        stmt = stmt.where(BrainVocabulary.canonical_tag.ilike(f"%{q}%"))
    stmt = stmt.order_by(BrainVocabulary.canonical_tag)
    result = await db.execute(stmt)
    return [_vocab_to_dict(v) for v in result.scalars().all()]


@router.post("/admin/vocabulary", status_code=201)
async def create_vocabulary_entry(
    body: dict,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    entry = BrainVocabulary(
        canonical_tag=body["canonical_tag"],
        aliases=body.get("aliases", []),
        search_space_id=search_space_id,
        is_active=body.get("is_active", True),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _vocab_to_dict(entry)


@router.put("/admin/vocabulary/{vocab_id}")
async def update_vocabulary_entry(
    vocab_id: int,
    body: dict,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainVocabulary).where(BrainVocabulary.id == vocab_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entrada no encontrada")
    for field in ("canonical_tag", "aliases", "is_active"):
        if field in body:
            setattr(entry, field, body[field])
    await db.commit()
    await db.refresh(entry)
    return _vocab_to_dict(entry)


@router.delete("/admin/vocabulary/{vocab_id}", status_code=204)
async def delete_vocabulary_entry(
    vocab_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    result = await db.execute(select(BrainVocabulary).where(BrainVocabulary.id == vocab_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entrada no encontrada")
    await db.delete(entry)
    await db.commit()


# ── F5 — Ingesta de texto desde chat (sin re-fetch del conector) ──────────────

class IngestFromTextRequest(BaseModel):
    """Body de POST /ingest/from-text."""
    content:        str
    filename:       str
    search_space_id: int
    connector_type: str | None = None  # trazabilidad (ej: "ONEDRIVE_CONNECTOR")


class IngestFromTextResponse(BaseModel):
    chunks_created: int
    source: str


@router.post("/ingest/from-text", response_model=IngestFromTextResponse)
async def ingest_from_text(
    body: IngestFromTextRequest,
    db:   AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """
    Ingesta contenido de texto en el Brain directamente, sin re-fetch del conector.

    Usado por el botón "Ingestar en Brain" en el action log del chat unificado (F5).
    El texto ya está disponible en el result del tool call — no hace falta descargarlo
    de nuevo.

    Usa IngestRouter.route_raw() — ingesta directa a knowledge/code sin síntesis LLM.
    Respuesta síncrona (~1-2 s).
    """
    from app.brain.ingest_router import IngestRouter
    from app.brain.processor_factory import DocumentProcessor

    await _require_space_access(body.search_space_id, db, current_user)

    if not body.content or not body.content.strip():
        raise HTTPException(status_code=422, detail="content no puede estar vacío")

    logger.info(
        "[ingest_from_text] START filename='%s' space=%d chars=%d user=%s connector=%s",
        body.filename, body.search_space_id, len(body.content),
        current_user.id, body.connector_type or "—",
    )

    def _run() -> dict:
        import datetime as _dt
        from pathlib import Path
        now = _dt.datetime.utcnow()
        ingest_metadata = {
            "ingest_origin":    "chat_tool_result",
            "ingest_date":      now.strftime("%Y-%m-%d"),
            "ingest_time":      now.strftime("%H:%M:%S"),
            "connector_type":   body.connector_type or "unknown",
        }

        # Elige extensión a partir del filename para seleccionar el extractor adecuado
        ext = Path(body.filename).suffix or ".txt"
        processor = DocumentProcessor(extension=ext)

        # Extrae bloques del texto (cleaner + quality_score, sin LLM)
        blocks = processor.extract_from_text(
            body.content,
            search_space_id=body.search_space_id,
            metadata=ingest_metadata,
        )

        if not blocks:
            return {"knowledge": {"chunks_created": 0}, "code": {"chunks_created": 0}}

        mgr     = QdrantManager.get_instance()
        results = IngestRouter(mgr.client).route_raw(
            blocks=blocks,
            source=body.filename,
            search_space_id=str(body.search_space_id),
            ingest_metadata=ingest_metadata,
        )
        return results

    try:
        results = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.error("[ingest_from_text] ERROR: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    total = sum(v.get("chunks_created", 0) for v in results.values())
    logger.info(
        "[ingest_from_text] DONE filename='%s' space=%d chunks=%d",
        body.filename, body.search_space_id, total,
    )
    return IngestFromTextResponse(chunks_created=total, source=body.filename)


async def _rewrite_query_for_web(pregunta: str) -> str:
    """
    Convierte una pregunta en lenguaje natural en keywords para SearXNG.

    Mejora significativa de la calidad de búsqueda web: una pregunta directa
    ('¿Por qué mi pipeline falla con heading4 en docx?') produce peores
    resultados en SearXNG que sus keywords ('docx heading4 pipeline extraction fail').

    Usa el LLM activo (SYNTHESIS_MODEL) para el rewriting.
    Fallback determinista: si el LLM falla, usa los primeros 6 tokens de la pregunta.

    Args:
        pregunta: Pregunta en lenguaje natural del usuario.

    Returns:
        String de keywords optimizado para SearXNG (max 80 chars).
    """
    rewrite_prompt = (
        "Convierte la siguiente pregunta en 4-6 palabras clave para buscar en Google. "
        "Devuelve SOLO las palabras clave separadas por espacios, sin explicación ni puntuación.\n\n"
        f"Pregunta: {pregunta.strip()}"
    )
    try:
        client   = LLMClient()
        raw      = client.generate(
            prompt=rewrite_prompt,
            system="Optimizador de queries de búsqueda. Responde solo con palabras clave.",
            provider=DEFAULT_PROVIDER,
            model=SYNTHESIS_MODEL,
        )
        keywords = " ".join(raw.strip().replace("\n", " ").split())[:80]
        result   = keywords if keywords else " ".join(pregunta.split()[:6])
        logger.debug("[QueryRewrite] '%s' → '%s'", pregunta[:40], result)
        return result
    except Exception as exc:
        fallback = " ".join(pregunta.split()[:6])
        logger.warning(
            "[QueryRewrite] LLM falló: %s → fallback='%s'",
            exc, fallback, exc_info=True,
        )
        return fallback
