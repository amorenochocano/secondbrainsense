"""
router.py
---------
BrainRouter — router inteligente de tres colecciones para el Second Brain.

Lógica de enrutamiento (cascada multinivel F4):
  L1: Busca en 'brain' (pasaportes semánticos).
      Score ≥ L1_MIN_SCORE → respuesta directa.
  L2: Baja a 'knowledge' o 'code' si L1 es insuficiente.
      Reranking con cross-encoder si RERANKING_ENABLED=true.
  L2.b: BM25 PostgreSQL (tsvector) si L1+L2 no tienen resultados.
        Delgado a brain_routes.py (async, necesita DB session).
  L2.c: Búsqueda web SearXNG/Tavily si BM25 también falla.
        Delegado a brain_routes.py (async, necesita web_search_service).
  L0:   LLM libre — último recurso cuando todo lo demás falla.

Características F4:
  - Multi-tenant: search_space_id inyectado en todos los filtros Qdrant.
  - fallback_needed: flag en _build_response() que indica a brain_routes.py
    que debe intentar L2.b/L2.c antes de llegar a L0.
  - El router es síncrono. Los niveles async (BM25, Web) se gestionan
    en brain_routes.py que sí tiene event loop y DB session.

El router puede ser forzado manualmente con force_level (1 ó 2) y
force_collection ('brain' | 'knowledge' | 'code').
"""
import os
import logging
from typing import Optional

import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

from app.brain.collections import BRAIN, KNOWLEDGE, CODE, COLLECTION_CONFIG

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración de entorno
# ---------------------------------------------------------------------------
QDRANT_HOST  = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT  = int(os.getenv("QDRANT_PORT", 6333))
OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Umbral de score para considerar que Nivel 1 es suficiente
L1_HIGH_SCORE  = float(os.getenv("ROUTER_L1_HIGH_SCORE",  "0.75"))
L1_MIN_SCORE   = float(os.getenv("ROUTER_L1_MIN_SCORE",   "0.60"))
RERANKING_ENABLED = os.getenv("RERANKING_ENABLED", "true").lower() == "true"
RERANKING_MODEL   = os.getenv("RERANKING_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
L2_CANDIDATE_MULTIPLIER = max(1, int(os.getenv("L2_CANDIDATE_MULTIPLIER", "2")))


# Cross-encoder cargado lazy al primer uso (igual estrategia que rag_utils)
_reranker = None


def _get_reranker():
    """Carga el cross-encoder una sola vez y lo reutiliza en llamadas sucesivas."""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANKING_MODEL, trust_remote_code=True, local_files_only=True)
            log.info("[brain_router] Cross-encoder '%s' cargado correctamente (solo local).", RERANKING_MODEL)
        except Exception as exc:
            log.warning(
                "[brain_router] Cross-encoder '%s' NO disponible localmente. Reranking L2 desactivado. (%s)",
                RERANKING_MODEL,
                exc,
            )
            _reranker = False
    return _reranker if _reranker is not False else None


# ---------------------------------------------------------------------------
# Helpers de embedding
# ---------------------------------------------------------------------------

def _get_embedding(text: str, model: str = "nomic-embed-text") -> list[float]:
    """
    Genera embedding con Ollama.
    Si el modelo solicitado falla (p.ej. nomic-embed-code no instalado),
    hace fallback automático a nomic-embed-text.
    """
    client = ollama.Client(host=OLLAMA_HOST)
    try:
        return client.embeddings(model=model, prompt=text)["embedding"]
    except Exception:
        if model != "nomic-embed-text":
            log.warning(
                "Modelo de embedding '%s' no disponible; usando nomic-embed-text como fallback.",
                model,
            )
            return client.embeddings(model="nomic-embed-text", prompt=text)["embedding"]
        raise


def _embed_for_collection(text: str, collection: str) -> list[float]:
    """Selecciona el modelo de embedding correcto según la colección."""
    model = COLLECTION_CONFIG.get(collection, {}).get("embed_model", "nomic-embed-text")
    return _get_embedding(text, model)


def _rerank_scored_points(query: str, points: list, top_k: int) -> list:
    """
    Reordena resultados Qdrant con cross-encoder en Nivel 2.
    Mantiene los objetos ScoredPoint y solo cambia su orden.
    """
    if not points:
        return []

    reranker = _get_reranker()
    if reranker is None:
        return points[:top_k]

    pairs = [(query, str((p.payload or {}).get("text", ""))) for p in points]
    try:
        scores = reranker.predict(pairs)
    except Exception as exc:
        log.warning("[brain_router] Error en reranking L2, usando orden original. (%s)", exc)
        return points[:top_k]

    ranked = sorted(zip(scores, points), key=lambda x: x[0], reverse=True)
    return [p for _, p in ranked[:top_k]]


# ---------------------------------------------------------------------------
# BrainRouter
# ---------------------------------------------------------------------------

class BrainRouter:
    """
    Router inteligente que decide qué colección(es) consultar en Qdrant
    en función de la naturaleza de la pregunta y los resultados de Nivel 1.

    Uso mínimo:
        router = BrainRouter()
        result = router.route("¿Cómo se configura el pipeline de Fabric?", top_k=4)
        # result["level_used"], result["collection_used"], result["results"], ...
    """

    # Triggers que fuerzan bajar a Nivel 2 (detalle)
    DETAIL_TRIGGERS = [
        "exactamente", "literalmente", "cita", "textual",
        "qué dice", "página", "paso a paso", "procedimiento",
        "número exacto", "cuánto exactamente", "comando",
        "configuración de", "valor por defecto", "parámetro",
        "detalle", "concreto", "fragmento", "extracto",
    ]

    # Triggers que indican que el Nivel 2 debe ser 'code'
    # NOTA: evitar palabras ambiguas como 'módulo' o 'método' que pueden referirse
    # a documentación conceptual ("el módulo pathlib") y no a código real.
    CODE_TRIGGERS = [
        "código", "función", "clase", "implementa", "script",
        "ejemplo de código", "sintaxis", "cómo se implementa",
        "query", "sql", "import",
        "pipeline json", "dataflow", "notebook", "pyspark",
        "def ", "class ", "select ", "create table", "insert into",
    ]

    def __init__(self, search_space_id: str = ""):
        """
        Args:
            search_space_id: ID del search space para filtrado multi-tenant.
                             Si está vacío, no se aplica filtro por tenant
                             (solo para uso interno/admin, no desde rutas FastAPI).
        """
        self._qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        self.search_space_id = search_space_id
        if search_space_id:
            log.debug("[brain_router] Instancia con search_space_id='%s'", search_space_id)
        else:
            log.debug("[brain_router] Instancia sin search_space_id (sin filtro tenant)")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def route(
        self,
        question: str,
        top_k: int = 4,
        force_level: Optional[int] = None,
        force_collection: Optional[str] = None,
    ) -> dict:
        """
        Enruta la pregunta y devuelve el resultado de búsqueda más adecuado.

        Args:
            question:         Pregunta en lenguaje natural.
            top_k:            Número de chunks a devolver.
            force_level:      1 ó 2 para forzar el nivel manualmente.
            force_collection: 'brain' | 'knowledge' | 'code' para forzar colección.

        Returns:
            {
                "level_used":          1 | 2,
                "collection_used":     "brain" | "knowledge" | "code",
                "results":             [ScoredPoint, ...],
                "sources_consulted":   ["fichero.pdf", ...],
                "drill_down_available": True | False,
                "level1_results":      [...] | None,   # solo si se usó L2
            }
        """
        # Forzado de coleccion directa (saltarse el enrutamiento)
        if force_collection in (BRAIN, KNOWLEDGE, CODE):
            log.info("[brain_router] START forzado → colección='%s' question='%s...'", force_collection, question[:60])
            if force_collection == BRAIN:
                results = self._search(force_collection, question, top_k)
            else:
                # Para colecciones de nivel 2 mantener el mismo comportamiento:
                # más candidatos + reranking cross-encoder.
                results = self._search_filtered(force_collection, question, top_k, sources=[])
            level = 1 if force_collection == BRAIN else 2
            return self._build_response(level, force_collection, results)

        log.info(
            "[brain_router] START question='%s...' top_k=%d force_level=%s",
            question[:60], top_k, force_level,
        )

        # Nivel 1 — siempre se consulta primero
        l1_results = self._search(BRAIN, question, top_k * 2)
        l1_max = max((r.score for r in l1_results), default=0.0)
        log.info(
            "[brain_router] L1 'brain' → %d resultados, score_max=%.3f (umbral_min=%.2f umbral_alto=%.2f)",
            len(l1_results), l1_max, L1_MIN_SCORE, L1_HIGH_SCORE,
        )

        # Forzado de nivel
        if force_level == 1:
            log.info("[brain_router] → nivel=1 (forzado) colección='brain'")
            return self._build_response(1, BRAIN, l1_results[:top_k])

        if force_level == 2:
            target = CODE if self.is_code_question(question) else KNOWLEDGE
            sources = self._sources_from(l1_results)
            log.info("[brain_router] → nivel=2 (forzado) colección='%s' sources=%s", target, sources[:3])
            l2_results = self._search_filtered(target, question, top_k, sources)
            # Fallback: si code no devuelve nada (docs de URL no tienen chunks de código),
            # reintentar con knowledge
            if not l2_results and target == CODE:
                log.info("[brain_router] nivel=2 code vacío → fallback a knowledge")
                l2_results = self._search_filtered(KNOWLEDGE, question, top_k, sources)
                target = KNOWLEDGE
            return self._build_response(2, target, l2_results, level1=l1_results)

        # Enrutamiento automático
        if self.needs_level2(question, l1_results):
            target = CODE if self.is_code_question(question) else KNOWLEDGE
            sources = self._sources_from(l1_results)
            log.info(
                "[brain_router] → nivel=2 (auto) colección='%s' score_max=%.3f sources=%s space='%s'",
                target, l1_max, sources[:3], self.search_space_id,
            )
            l2_results = self._search_filtered(target, question, top_k, sources)
            # Fallback code → knowledge si code no devuelve nada
            if not l2_results and target == CODE:
                log.info("[brain_router] nivel=2 code vacío → fallback a knowledge")
                l2_results = self._search_filtered(KNOWLEDGE, question, top_k, sources)
                target = KNOWLEDGE

            if not l2_results:
                # L1 y L2 vacíos — señalizar a brain_routes.py para intentar L2.b/L2.c
                log.info(
                    "[brain_router] L1+L2 sin resultados relevantes space='%s' → fallback_needed=True",
                    self.search_space_id,
                )
                return self._build_response(0, BRAIN, [], fallback_needed=True)

            return self._build_response(2, target, l2_results, level1=l1_results)

        # Nivel 1 suficiente
        log.info("[brain_router] → nivel=1 (auto, score_max=%.3f ≥ %.2f) colección='brain'", l1_max, L1_MIN_SCORE)
        return self._build_response(1, BRAIN, l1_results[:top_k])

    def needs_level2(self, question: str, level1_results: list) -> bool:
        """True si la pregunta requiere bajar a Nivel 2."""
        q = question.lower()
        if any(t in q for t in self.DETAIL_TRIGGERS):
            return True
        if not level1_results:
            return True
        max_score = max(r.score for r in level1_results)
        if max_score < L1_MIN_SCORE:
            return True
        return False

    def is_code_question(self, question: str) -> bool:
        """True si la pregunta está orientada a código/implementaciones."""
        q = question.lower()
        return any(t in q for t in self.CODE_TRIGGERS)

    # ------------------------------------------------------------------
    # Búsqueda Qdrant
    # ------------------------------------------------------------------

    def _search(self, collection: str, query: str, top_k: int) -> list:
        """
        Búsqueda semántica en la colección indicada filtrada por search_space_id.

        Si self.search_space_id está definido, añade FieldCondition de tenant
        para garantizar aislamiento multi-tenant.
        """
        from qdrant_client.models import MatchValue
        vector = _embed_for_collection(query, collection)
        qdrant_filter = None
        if self.search_space_id:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(
                        key="search_space_id",
                        match=MatchValue(value=self.search_space_id),
                    )
                ]
            )
        try:
            return self._qdrant.search(
                collection_name=collection,
                query_vector=vector,
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            )
        except Exception as exc:
            log.error(
                "[brain_router] Error buscando en '%s' space='%s': %s",
                collection, self.search_space_id, exc, exc_info=True,
            )
            return []

    def _search_filtered(
        self,
        collection: str,
        query: str,
        top_k: int,
        sources: list[str],
    ) -> list:
        """
        Búsqueda semántica filtrada por source (MatchAny) y search_space_id (tenant).

        Si self.search_space_id está definido se añade como condición extra en
        must[] junto al filtro de source — garantiza aislamiento multi-tenant.
        Si no hay sources o la colección está vacía, hace búsqueda libre con
        solo el filtro de tenant.

        Nivel 2: recupera más candidatos (top_k * L2_CANDIDATE_MULTIPLIER)
        y aplica reranking por cross-encoder si RERANKING_ENABLED=true.
        """
        from qdrant_client.models import MatchValue
        vector = _embed_for_collection(query, collection)
        candidate_k = top_k * L2_CANDIDATE_MULTIPLIER

        # Construir filtro multi-tenant + source
        must_conditions = []
        if self.search_space_id:
            must_conditions.append(
                FieldCondition(
                    key="search_space_id",
                    match=MatchValue(value=self.search_space_id),
                )
            )
        if sources:
            must_conditions.append(
                FieldCondition(
                    key="source",
                    match=MatchAny(any=sources),
                )
            )
        qdrant_filter = Filter(must=must_conditions) if must_conditions else None

        try:
            results = self._qdrant.search(
                collection_name=collection,
                query_vector=vector,
                query_filter=qdrant_filter,
                limit=candidate_k,
                with_payload=True,
            )
        except Exception as exc:
            log.error(
                "[brain_router] Error buscando filtrado en '%s' space='%s': %s",
                collection, self.search_space_id, exc, exc_info=True,
            )
            results = []

        # Fallback: si no hay resultados con filtro source, buscar solo por tenant
        if not results and sources:
            log.info(
                "[brain_router] Sin resultados filtrados por source en '%s'; "
                "ampliando búsqueda (solo filtro tenant space='%s').",
                collection, self.search_space_id,
            )
            # Filtro solo por tenant, sin source
            tenant_filter = None
            if self.search_space_id:
                tenant_filter = Filter(
                    must=[
                        FieldCondition(
                            key="search_space_id",
                            match=MatchValue(value=self.search_space_id),
                        )
                    ]
                )
            try:
                results = self._qdrant.search(
                    collection_name=collection,
                    query_vector=vector,
                    query_filter=tenant_filter,
                    limit=candidate_k,
                    with_payload=True,
                )
            except Exception as exc2:
                log.error(
                    "[brain_router] Error en búsqueda fallback en '%s': %s",
                    collection, exc2, exc_info=True,
                )
                results = []

        if not results:
            return []

        if RERANKING_ENABLED:
            reranked = _rerank_scored_points(query, results, top_k)
            log.info(
                "[brain_router] L2 '%s' space='%s' → %d candidatos, %d finales +rerank",
                collection, self.search_space_id,
                len(results), len(reranked),
            )
            return reranked

        trimmed = results[:top_k]
        log.info(
            "[brain_router] L2 '%s' space='%s' → %d candidatos, %d finales (sin rerank)",
            collection, self.search_space_id,
            len(results), len(trimmed),
        )
        return trimmed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sources_from(self, results: list) -> list[str]:
        """Extrae la lista única de sources de los resultados de Qdrant."""
        seen = set()
        sources = []
        for r in results:
            s = r.payload.get("source", "")
            if s and s not in seen:
                seen.add(s)
                sources.append(s)
        return sources

    def _build_response(
        self,
        level: int,
        collection: str,
        results: list,
        level1: Optional[list] = None,
        fallback_needed: bool = False,
    ) -> dict:
        """
        Construye el dict de respuesta estándar del router.

        Args:
            level:           Nivel usado (1=brain, 2=knowledge/code, 0=sin resultado).
            collection:      Nombre de la colección Qdrant consultada.
            results:         Lista de ScoredPoint de Qdrant.
            level1:          Resultados L1 originales si se usó L2 (para trazabilidad).
            fallback_needed: True cuando L1+L2 no produjeron resultados relevantes.
                             brain_routes.py lo usa como señal para intentar L2.b/L2.c.

        Returns:
            Dict con: level_used, collection_used, results, sources_consulted,
                      drill_down_available, level1_results, fallback_needed.
        """
        return {
            "level_used":           level,
            "collection_used":      collection,
            "results":              results,
            "sources_consulted":    self._sources_from(results),
            "drill_down_available": level == 1 and len(results) > 0,
            "level1_results":       level1,
            "fallback_needed":      fallback_needed,
        }
