"""brain_search_tool.py
----------------------
LangChain StructuredTool que expone el pipeline Brain (BrainRouter) al
agente SurfSense como una tool nativa de primer nivel.

Decisión de diseño (ver plan-conectores-chat-unificado.md § Fase 3):
  - brain_search es un Tool, no un subagente. El agente principal decide cuándo invocarlo.
  - Ejecuta la cascada completa Brain: L1 (brain) → L2 (knowledge/code, reranking) → fallback.
  - BrainRouter es síncrono; se ejecuta en thread pool con asyncio.to_thread().
  - La respuesta incluye metadatos de nivel (level_used, level_label, sources)
    para que la UI pueda renderizar level badges.
  - Si BrainRouter devuelve fallback_needed=True (L1+L2 vacíos), la respuesta
    lo indica explícitamente para que el agente pueda complementar con web_search.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from langchain_core.tools import BaseTool, StructuredTool

log = logging.getLogger(__name__)

_MAX_CHARS_PER_CHUNK = 800
_MAX_TOTAL_CHARS = 12_000

# Etiquetas de nivel para la UI (idénticas a brain_routes.py)
_LEVEL_LABELS: dict[int, str] = {
    0: "🤖 Sin contexto Brain",
    1: "🧠 Brain (nivel 1)",
    2: "📚 Knowledge (nivel 2)",
}


def _format_brain_results(result: dict[str, Any], query: str) -> str:
    """Formatea los ScoredPoints del BrainRouter como bloque legible por el modelo."""
    level_used: int = result.get("level_used", 0)
    level_label: str = result.get("level_label") or _LEVEL_LABELS.get(level_used, "Brain")
    sources: list[str] = result.get("sources_consulted") or []
    points: list[Any] = result.get("results") or []
    fallback_needed: bool = result.get("fallback_needed", False)

    if fallback_needed or not points:
        return (
            f"<brain_search_results query={query!r} level=\"{level_label}\">\n"
            "No se encontró información relevante en las colecciones Brain para esta consulta.\n"
            "Sugerencia: complementa con `web_search` o responde desde conocimiento general.\n"
            "</brain_search_results>"
        )

    lines: list[str] = [
        f"<brain_search_results query={query!r} level=\"{level_label}\" "
        f"sources_consulted={len(sources)}>"
    ]
    total = len(lines[0])

    for rank, point in enumerate(points, start=1):
        payload: dict[str, Any] = point.payload or {}
        text: str = str(payload.get("text") or payload.get("content") or "").strip()
        source: str = str(payload.get("source") or payload.get("filename") or "desconocido")
        score: float = getattr(point, "score", 0.0)
        page: Any = payload.get("page") or payload.get("section") or ""

        snippet = text[:_MAX_CHARS_PER_CHUNK]
        if len(text) > _MAX_CHARS_PER_CHUNK:
            snippet += " …"

        header = f"\n[{rank}] {source}"
        if page:
            header += f" (pág./sección: {page})"
        header += f" — score: {score:.3f}"
        body = "\n   " + snippet.replace("\n", "\n   ")

        entry = header + body
        if total + len(entry) > _MAX_TOTAL_CHARS:
            lines.append("\n<!-- resultados adicionales truncados para ajustar al contexto -->")
            break
        lines.append(entry)
        total += len(entry)

    if sources:
        lines.append(f"\n\nFuentes consultadas: {', '.join(sources[:8])}")

    lines.append("\n</brain_search_results>")
    return "".join(lines)


def create_brain_search_tool(*, search_space_id: int) -> BaseTool:
    """Factory para la tool brain_search.

    Args:
        search_space_id: ID del search space activo (aislamiento multi-tenant).

    Returns:
        StructuredTool listo para registrar en el agente principal.
    """
    _space_id = search_space_id

    async def _impl(
        query: Annotated[
            str,
            "Pregunta o consulta en lenguaje natural. Sé específico: incluye nombres, "
            "entidades, acrónimos, proyectos o términos concretos.",
        ],
        force_level: Annotated[
            int | None,
            "Forzar nivel de retrieval: 1 = resumen/pasaportes Brain, "
            "2 = detalle documental (knowledge/code). None = automático (recomendado).",
        ] = None,
        top_k: Annotated[
            int,
            "Número máximo de chunks a devolver (default 4, max 10).",
        ] = 4,
    ) -> str:
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            return "Error: proporciona una consulta no vacía."

        clamped_k = min(max(1, top_k), 10)
        clamped_level = force_level if force_level in (1, 2) else None

        log.debug(
            "[brain_search] query=%r space=%s force_level=%s top_k=%d",
            cleaned_query[:80], _space_id, clamped_level, clamped_k,
        )

        try:
            from app.brain.router import BrainRouter

            router = BrainRouter(search_space_id=str(_space_id))
            result: dict[str, Any] = await asyncio.to_thread(
                router.route,
                cleaned_query,
                clamped_k,
                clamped_level,
            )
        except Exception as exc:
            log.error("[brain_search] Error en BrainRouter: %s", exc, exc_info=True)
            return (
                f"Error al consultar el Brain: {exc}. "
                "Intenta con web_search o responde desde conocimiento general."
            )

        rendered = _format_brain_results(result, cleaned_query)
        log.debug(
            "[brain_search] level=%s sources=%d chars=%d",
            result.get("level_used"), len(result.get("sources_consulted") or []), len(rendered),
        )
        return rendered

    return StructuredTool.from_function(
        name="brain_search",
        description=(
            "Busca en el Second Brain de Enagás (colecciones indexadas Brain + Knowledge + Code) "
            "usando retrieval en cascada multinivel con RRF y reranking semántico.\n\n"
            "Úsalo como PRIMERA opción para preguntas sobre documentación interna, "
            "procedimientos, decisiones técnicas, proyectos o cualquier contenido "
            "específico de Enagás. Es superior a search_knowledge_base para este dominio "
            "porque aplica cascada L1→L2 con cross-encoder reranking.\n\n"
            "Devuelve fragmentos relevantes con fuente, página/sección y score de relevancia. "
            "Los metadatos de nivel (Brain L1 vs Knowledge L2) se incluyen para la UI."
        ),
        coroutine=_impl,
    )
