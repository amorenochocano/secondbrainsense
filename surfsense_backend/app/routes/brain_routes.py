"""
brain_routes.py
---------------
Router FastAPI para consultas al Second Brain — cascada multinivel F4.

Endpoints:
  POST /api/v1/brain/query              — Consulta con cascada L1→L2→BM25→Web→L0
  GET  /api/v1/brain/passport/{source}  — Lee el pasaporte .md de un documento
  DELETE /api/v1/brain/document/{source} — Elimina .md + vectores Qdrant

Cascada de retrieval:
  L1  : BrainRouter → colección 'brain' (pasaportes semánticos Qdrant)
  L2  : BrainRouter → colección 'knowledge' o 'code' (chunks semánticos Qdrant)
  L2.b: BM25 PostgreSQL via ChucksHybridSearchRetriever.full_text_search()
  L2.c: SearXNG web search via web_search_service.search()
  L0  : LLM libre — último recurso (desactivable con BRAIN_L0_ENABLED=false)

Separación de concerns:
  - BrainRouter (síncrono) gestiona L1 y L2 Qdrant.
  - brain_routes.py (async FastAPI) gestiona L2.b y L2.c porque requieren
    DB session y web service que no pertenecen al router síncrono.

Variables de entorno:
  BRAIN_BM25_ENABLED     — activar BM25 como L2.b (default: true)
  BRAIN_WEB_ENABLED      — activar búsqueda web como L2.c (default: true)
  BRAIN_L0_ENABLED       — permitir L0 libre (default: true)
  BRAIN_WEB_MAX_RESULTS  — nº máximo de resultados web (default: 5)
  SYNTHESIS_MODEL        — modelo LLM activo (leído de llm_client.py)
  LLM_PROVIDER           — proveedor LLM activo (leído de llm_client.py)
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.router import BrainRouter
from app.brain.llm_client import LLMClient, SYNTHESIS_MODEL, DEFAULT_PROVIDER
from app.brain.model_profiles import get_profile
from app.db import User, get_async_session
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
            1 → Brain (pasaportes semánticos Qdrant, colección 'brain')
            2 → Knowledge/Code (chunks Qdrant, colección 'knowledge'/'code')
            3 → BM25 (tsvector PostgreSQL, búsqueda keyword)
            4 → Web (SearXNG, búsqueda en internet en tiempo real)
            0 → LLM libre (sin contexto, conocimiento paramétrico)
            Usar para depuración y para mostrar al usuario el origen
            de la información ("¿de dónde viene esta respuesta?").

        level_label:
            Etiqueta legible del nivel para la UI.
            Valores: "🧠 Brain", "📚 Knowledge", "🔍 BM25", "🌐 Web", "🤖 LLM"

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
    Consulta al Second Brain con cascada multinivel L1→L2→BM25→Web→L0.

    La cascada se detiene en el primer nivel que produce contexto relevante.
    El presupuesto de chunks y la longitud máxima se adaptan automáticamente
    al tier del modelo activo (small/medium/claude) para no saturar el contexto.

    Cascada:
      L1  → brain (pasaportes semánticos Qdrant, score ≥ ROUTER_L1_MIN_SCORE)
      L2  → knowledge/code (chunks Qdrant con reranking cross-encoder)
      L2.b→ BM25 PostgreSQL (tsvector, <100ms, si BRAIN_BM25_ENABLED=true)
      L2.c→ Web SearXNG (si BRAIN_WEB_ENABLED=true)
      L0  → LLM libre sin contexto (si BRAIN_L0_ENABLED=true, else 404)
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
        return BrainQueryResponse(
            answer=answer, level_used=0, level_label="🤖 LLM",
            sources=[], context_chunks=0, model_tier=tier,
        )

    # ── L1 + L2: router síncrono Qdrant ──────────────────────────────────────
    # search_space_id se pasa como str al router (filtra en Qdrant por payload)
    brain_router = BrainRouter(search_space_id=str(req.search_space_id))
    route_result = brain_router.route(
        question=req.question,
        top_k=req.top_k,
        force_level=req.force_level,
    )

    if not route_result.get("fallback_needed") and route_result.get("results"):
        level   = route_result["level_used"]
        chunks  = _extract_chunks(route_result["results"], chunk_budget, chunk_max_chars)
        context = _build_context(chunks)
        sources = route_result["sources_consulted"]
        label   = "🧠 Brain" if level == 1 else "📚 Knowledge"

        logger.info(
            "[brain_query] L%d '%s' → %d chunks, %d sources",
            level, label, len(chunks), len(sources),
        )
        answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
        return BrainQueryResponse(
            answer=answer, level_used=level, level_label=label,
            sources=sources, context_chunks=len(chunks), model_tier=tier,
        )

    logger.info(
        "[brain_query] L1+L2 sin resultados relevantes (fallback_needed=%s) → escalando",
        route_result.get("fallback_needed"),
    )

    # ── L2.b: BM25 PostgreSQL ─────────────────────────────────────────────────
    if BRAIN_BM25_ENABLED:
        logger.info("[brain_query] Intentando L2.b BM25 space=%d", req.search_space_id)
        try:
            from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever
            retriever   = ChucksHybridSearchRetriever(db)
            bm25_chunks = await retriever.full_text_search(
                query_text=req.question,
                top_k=chunk_budget * 2,          # más candidatos para compensar precisión BM25
                search_space_id=req.search_space_id,   # int directo — PK de SearchSpace
            )
            if bm25_chunks:
                chunks  = _extract_chunks_from_orm(bm25_chunks, chunk_budget, chunk_max_chars)
                context = _build_context(chunks)
                sources = _sources_from_orm(bm25_chunks, chunk_budget)
                logger.info(
                    "[brain_query] L2.b BM25 → %d chunks, %d sources",
                    len(chunks), len(sources),
                )
                answer = _call_llm(req.question, context=context, tier=tier, history=req.chat_history)
                return BrainQueryResponse(
                    answer=answer, level_used=3, level_label="🔍 BM25",
                    sources=sources, context_chunks=len(chunks), model_tier=tier,
                )
            logger.info("[brain_query] L2.b BM25 sin resultados")
        except Exception as exc:
            logger.warning(
                "[brain_query] L2.b BM25 falló: %s — continuando cascada",
                exc, exc_info=True,
            )
    else:
        logger.debug("[brain_query] L2.b BM25 desactivado (BRAIN_BM25_ENABLED=false)")

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
    return BrainQueryResponse(
        answer=answer, level_used=0, level_label="🤖 LLM",
        sources=[], context_chunks=0, model_tier=tier,
    )


# ── Endpoints CRUD de pasaportes ──────────────────────────────────────────────

@router.get("/passport/{source}")
async def get_passport(
    source: str,
    current_user: User = Depends(current_active_user),
):
    """
    Lee el pasaporte .md semántico de un documento del Brain.

    El parámetro source puede ser el nombre del fichero original ('doc.pdf')
    o el slug generado por BrainWriter ('doc'). BrainWriter.read() devuelve
    None si no existe — no lanza excepción.
    """
    from app.brain.writer import BrainWriter
    writer  = BrainWriter()
    content = writer.read(source)
    if content is None:
        logger.info("[brain] passport not found: source='%s'", source)
        raise HTTPException(status_code=404, detail=f"Pasaporte no encontrado: '{source}'")
    logger.info("[brain] passport read: source='%s' chars=%d", source, len(content))
    return {"source": source, "passport_md": content}


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
