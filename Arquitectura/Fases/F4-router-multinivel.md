# F4 — Router Multinivel L1→L2→L0
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + IA Engineer (1)  
**Dependencias:** F3 completada  
**Entregable:** Las consultas se resuelven en cascade: L1 (pasaportes) → L2 (chunks detalle) → L0 (LLM libre)

---

## Objetivo

Sustituir la búsqueda híbrida plana de SurfSense por el router multinivel de Second Brain. La diferencia clave: SurfSense busca en todos los chunks simultáneamente. BrainSense primero identifica qué documentos son relevantes (L1), luego profundiza en sus chunks (L2), y solo cae a LLM libre (L0) cuando no hay contexto relevante.

**Antes (SurfSense):**
```
query → hybrid search (pgvector + BM25) → top-K chunks → LLM → respuesta
```

**Después (BrainSense F4):**
```
query → L1: buscar en brain (pasaportes) ─── relevante? ──→ L2: buscar en knowledge+code
                                          └── no relevante → L0: LLM libre sin RAG
```

---

## F4.1 — BrainRouter: el cascade L1→L2→L0 (Día 1-2)

**Fichero:** `surfsense_backend/app/brain/router.py` ← ya existe en Second Brain, adaptar

```python
# router.py — BrainRouter
import logging
from dataclasses import dataclass
from app.brain.qdrant_manager import QdrantManager
from app.brain.llm_client import BrainLLMClient

logger = logging.getLogger(__name__)

@dataclass
class QueryResult:
    answer: str
    level: int              # 0=LLM libre, 1=pasaporte, 2=chunks detalle
    sources: list[str]      # slugs de documentos fuente
    chunks_used: list[dict] # chunks que formaron el contexto
    level_label: str        # "🧠 Brain", "📚 Knowledge", "🤖 LLM"

class BrainRouter:
    """
    Router multinivel: L1 → L2 → L0
    L1: colección brain   (pasaportes semánticos)
    L2: colecciones knowledge + code (chunks de detalle)
    L0: LLM sin RAG (fallback cuando no hay contexto relevante)
    """

    def __init__(
        self,
        llm_client: BrainLLMClient,
        qdrant: QdrantManager,
        search_space_id: str,
        top_k_l1: int = 5,
        top_k_l2: int = 10,
        l1_threshold: float = 0.45,  # score mínimo para considerar L1 relevante
        l2_threshold: float = 0.40,
        force_l0: bool = False,
    ):
        self.llm = llm_client
        self.qdrant = qdrant
        self.search_space_id = search_space_id
        self.top_k_l1 = top_k_l1
        self.top_k_l2 = top_k_l2
        self.l1_threshold = l1_threshold
        self.l2_threshold = l2_threshold
        self.force_l0 = force_l0

    async def query(self, question: str, chat_history: list[dict] = None) -> QueryResult:
        """Punto de entrada principal del router."""

        if self.force_l0:
            return await self._resolve_l0(question, chat_history)

        # Extraer keywords para detección de relevancia
        keywords = _keywords_from(question)

        # ── NIVEL 1: buscar en pasaportes ─────────────
        l1_results = await self._search_l1(question)
        l1_relevant = [r for r in l1_results if r["score"] >= self.l1_threshold]

        if not l1_relevant:
            logger.info(f"L1 sin resultados relevantes (threshold={self.l1_threshold}) → L0")
            return await self._resolve_l0(question, chat_history)

        # Verificar que los chunks L1 contienen las keywords
        l1_with_keywords = [
            r for r in l1_relevant
            if _chunks_contain_keywords(r["text"], keywords)
        ]

        if not l1_with_keywords:
            logger.info("L1 sin matches de keywords → L0")
            return await self._resolve_l0(question, chat_history)

        # Fuentes identificadas en L1
        l1_sources = list(dict.fromkeys(r["source"] for r in l1_with_keywords))
        logger.info(f"L1: {len(l1_with_keywords)} chunks relevantes, sources: {l1_sources}")

        # ── ¿La respuesta está en L1? ──────────────────
        # Si el score es muy alto y el chunk es completo, responder desde L1
        best_l1 = l1_with_keywords[0]
        if best_l1["score"] >= 0.75 and _is_complete_answer(best_l1["text"], question):
            return await self._resolve_l1(question, l1_with_keywords, chat_history)

        # ── NIVEL 2: profundizar en chunks de detalle ──
        l2_results = await self._search_l2(question, filter_sources=l1_sources)
        l2_relevant = [r for r in l2_results if r["score"] >= self.l2_threshold]

        if not l2_relevant:
            logger.info("L2 sin resultados → responder desde L1")
            return await self._resolve_l1(question, l1_with_keywords, chat_history)

        logger.info(f"L2: {len(l2_relevant)} chunks de detalle")
        return await self._resolve_l2(question, l1_with_keywords, l2_relevant, chat_history)

    async def _search_l1(self, question: str) -> list[dict]:
        """Buscar en colección brain (pasaportes)."""
        vector = await self.llm.embed_text(question, model="nomic-embed-text")
        return self.qdrant.search(
            collection_name="brain",
            query_vector=vector,
            search_space_id=self.search_space_id,
            top_k=self.top_k_l1,
        )

    async def _search_l2(self, question: str, filter_sources: list[str]) -> list[dict]:
        """Buscar en knowledge + code, filtrando por fuentes identificadas en L1."""
        # Embedding para knowledge (nomic 768d)
        vector_text = await self.llm.embed_text(question, model="nomic-embed-text")
        knowledge_results = self.qdrant.search(
            collection_name="knowledge",
            query_vector=vector_text,
            search_space_id=self.search_space_id,
            top_k=self.top_k_l2,
            filter_by_sources=filter_sources,
        )

        # Embedding para code (qwen3 2560d) — solo si la query parece técnica
        code_results = []
        if _is_code_query(question):
            vector_code = await self.llm.embed_text(question, model="qwen3-embedding:4b")
            code_results = self.qdrant.search(
                collection_name="code",
                query_vector=vector_code,
                search_space_id=self.search_space_id,
                top_k=self.top_k_l2,
                filter_by_sources=filter_sources,
            )

        # Merge y RRF fusion
        all_results = knowledge_results + code_results
        return _rrf_fusion(all_results, k=60)

    async def _resolve_l1(self, question: str, chunks: list[dict],
                           history: list[dict]) -> QueryResult:
        context = "\n\n---\n\n".join(c["text"] for c in chunks[:3])
        answer = await self.llm.generate(
            prompt=f"Pregunta: {question}\n\nContexto (resumen del conocimiento):\n{context}",
            system=L1_SYSTEM_PROMPT,
        )
        return QueryResult(
            answer=answer, level=1,
            sources=list(dict.fromkeys(c["source"] for c in chunks)),
            chunks_used=chunks[:3],
            level_label="🧠 Brain",
        )

    async def _resolve_l2(self, question: str, l1_chunks: list[dict],
                           l2_chunks: list[dict], history: list[dict]) -> QueryResult:
        # Combinar contexto L1 (resumen) + L2 (detalle)
        l1_context = "\n\n".join(c["text"] for c in l1_chunks[:2])
        l2_context = "\n\n---\n\n".join(c["text"] for c in l2_chunks[:5])
        context = f"## Resumen\n{l1_context}\n\n## Detalle\n{l2_context}"

        answer = await self.llm.generate(
            prompt=f"Pregunta: {question}\n\nContexto:\n{context}",
            system=L2_SYSTEM_PROMPT,
        )
        all_sources = list(dict.fromkeys(
            c["source"] for c in (l1_chunks + l2_chunks)
        ))
        return QueryResult(
            answer=answer, level=2,
            sources=all_sources,
            chunks_used=l2_chunks[:5],
            level_label="📚 Knowledge",
        )

    async def _resolve_l0(self, question: str, history: list[dict]) -> QueryResult:
        """LLM libre sin RAG."""
        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in (history or [])[-6:]
        )
        answer = await self.llm.generate(
            prompt=f"{history_text}\nUsuario: {question}",
            system=L0_SYSTEM_PROMPT,
        )
        return QueryResult(
            answer=answer, level=0,
            sources=[], chunks_used=[],
            level_label="🤖 LLM",
        )


# ── Helpers ────────────────────────────────────────────────────

def _keywords_from(question: str) -> list[str]:
    """Extrae keywords significativas de la pregunta."""
    stopwords = {"el","la","los","las","un","una","de","del","en","que","es","se",
                 "por","para","con","como","qué","cómo","cuál","cuáles","dónde"}
    words = question.lower().split()
    keywords = [w.strip("?¿.,;:") for w in words
                if len(w) > 3 and w not in stopwords]
    return keywords[:10]

def _chunks_contain_keywords(text: str, keywords: list[str]) -> bool:
    """Al menos 1 keyword debe estar en el chunk."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)

def _is_code_query(question: str) -> bool:
    """Detecta si la query probablemente necesita buscar en código."""
    code_signals = ["función","función","clase","class","def ","import","query",
                    "sql","procedure","método","implementación","código","script"]
    q = question.lower()
    return any(s in q for s in code_signals)

def _is_complete_answer(text: str, question: str) -> bool:
    """Heurística: el chunk tiene suficiente contenido para responder."""
    return len(text) > 500

def _rrf_fusion(results: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion para mergear resultados de knowledge y code."""
    scores = {}
    for rank, r in enumerate(results):
        key = r.get("source", "") + str(r.get("chunk_index", 0))
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)

    seen_keys = set()
    merged = []
    for r in results:
        key = r.get("source", "") + str(r.get("chunk_index", 0))
        if key not in seen_keys:
            seen_keys.add(key)
            r["rrf_score"] = scores[key]
            merged.append(r)

    return sorted(merged, key=lambda x: x["rrf_score"], reverse=True)


# ── System prompts ─────────────────────────────────────────────

L0_SYSTEM_PROMPT = """Eres un asistente técnico experto. Responde basándote en tu conocimiento general.
No tienes contexto de documentos específicos para esta pregunta."""

L1_SYSTEM_PROMPT = """Eres un asistente técnico. Tienes acceso a resúmenes ejecutivos de documentos.
Responde con precisión citando el documento fuente. Si el resumen no es suficiente, indícalo."""

L2_SYSTEM_PROMPT = """Eres un asistente técnico experto. Tienes acceso al detalle completo de los documentos.
Responde con precisión técnica, cita las fuentes específicas y menciona el nivel de detalle disponible."""
```

---

## F4.2 — Endpoint de query en FastAPI (Día 3)

```python
# surfsense_backend/app/routes/brain_routes.py ← NUEVO

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.brain.router import BrainRouter
from app.brain.qdrant_manager import QdrantManager
from app.brain.llm_client import BrainLLMClient
from app.utils.auth import get_current_user

router = APIRouter(prefix="/brain", tags=["brain"])

class QueryRequest(BaseModel):
    question: str
    search_space_id: str
    chat_history: list[dict] = []
    force_l0: bool = False
    top_k_l1: int = 5
    top_k_l2: int = 10

class QueryResponse(BaseModel):
    answer: str
    level: int
    level_label: str
    sources: list[str]
    chunks_used: list[dict]

@router.post("/query", response_model=QueryResponse)
async def brain_query(
    req: QueryRequest,
    current_user = Depends(get_current_user),
):
    """Consulta multinivel L1→L2→L0."""
    # Verificar que el usuario tiene acceso al Search Space
    _verify_search_space_access(current_user, req.search_space_id)

    llm_client = BrainLLMClient()
    qdrant = QdrantManager.get_instance()

    brain_router = BrainRouter(
        llm_client=llm_client,
        qdrant=qdrant,
        search_space_id=req.search_space_id,
        top_k_l1=req.top_k_l1,
        top_k_l2=req.top_k_l2,
        force_l0=req.force_l0,
    )

    result = await brain_router.query(
        question=req.question,
        chat_history=req.chat_history,
    )

    return QueryResponse(
        answer=result.answer,
        level=result.level,
        level_label=result.level_label,
        sources=result.sources,
        chunks_used=result.chunks_used,
    )

@router.get("/{source}")
async def get_passport(source: str, current_user = Depends(get_current_user)):
    """Lee el pasaporte .md de un documento."""
    from app.brain.writer import BrainWriter
    writer = BrainWriter()
    try:
        return {"source": source, "passport_md": writer.read(source)}
    except FileNotFoundError:
        raise HTTPException(404, detail=f"Pasaporte no encontrado: {source}")

@router.delete("/{source}")
async def delete_brain_document(source: str, current_user = Depends(get_current_user)):
    """Elimina un documento: pasaporte .md + vectores Qdrant."""
    from app.brain.writer import BrainWriter
    writer = BrainWriter()
    qdrant = QdrantManager.get_instance()
    writer.delete(source)
    qdrant.delete_by_source(source)
    return {"status": "deleted", "source": source}

@router.post("/{source}/resynthesize")
async def resynthesize_document(source: str, current_user = Depends(get_current_user)):
    """Re-sintetizar pasaporte desde la fuente original."""
    # Buscar el document en PostgreSQL por source slug
    # Re-ejecutar el pipeline desde Fase 1
    from app.tasks.document.resynthesize_task import resynthesize_task
    resynthesize_task.delay(source=source, user_id=str(current_user.id))
    return {"status": "queued", "source": source}
```

---

## F4.3 — Registrar las rutas Brain en SurfSense (Día 3)

```python
# surfsense_backend/app/main.py — añadir:

from app.routes.brain_routes import router as brain_router
app.include_router(brain_router)
```

---

## F4.4 — Tests del router (Día 4-5)

```python
# tests/brain/test_router_f4.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.brain.router import BrainRouter, _keywords_from, _rrf_fusion

class TestKeywordsExtraction:
    def test_extrae_keywords_relevantes(self):
        kws = _keywords_from("¿Cómo funciona la función refreshToken del módulo auth?")
        assert "refreshtoken" in kws or "función" in kws
        assert "cómo" not in kws  # stopword

    def test_query_vacia(self):
        assert _keywords_from("") == []

class TestRRFFusion:
    def test_merge_elimina_duplicados(self):
        results = [
            {"source": "doc-a", "chunk_index": 0, "score": 0.9, "text": "x"},
            {"source": "doc-a", "chunk_index": 0, "score": 0.85, "text": "x"},
            {"source": "doc-b", "chunk_index": 1, "score": 0.7, "text": "y"},
        ]
        merged = _rrf_fusion(results)
        keys = [f"{r['source']}{r['chunk_index']}" for r in merged]
        assert len(keys) == len(set(keys))  # sin duplicados

class TestBrainRouterCascade:

    @pytest.fixture
    def mock_router(self):
        llm = AsyncMock()
        llm.embed_text = AsyncMock(return_value=[0.1] * 768)
        llm.generate = AsyncMock(return_value="Respuesta de prueba")
        qdrant = MagicMock()
        return BrainRouter(
            llm_client=llm, qdrant=qdrant,
            search_space_id="test-space"
        )

    @pytest.mark.asyncio
    async def test_sin_resultados_l1_va_a_l0(self, mock_router):
        mock_router.qdrant.search = MagicMock(return_value=[])
        result = await mock_router.query("pregunta sin contexto")
        assert result.level == 0
        assert result.level_label == "🤖 LLM"

    @pytest.mark.asyncio
    async def test_force_l0_bypass_todo(self, mock_router):
        mock_router.force_l0 = True
        result = await mock_router.query("cualquier pregunta")
        assert result.level == 0

    @pytest.mark.asyncio
    async def test_resultado_l1_alto_score_resuelve_en_l1(self, mock_router):
        mock_router.qdrant.search = MagicMock(return_value=[{
            "score": 0.9, "text": "x" * 600,
            "source": "doc-auth", "chunk_index": 0, "metadata": {}
        }])
        result = await mock_router.query("función de autenticación")
        assert result.level in [1, 2]
        assert "doc-auth" in result.sources
```

---

## Checklist F4

- [ ] `BrainRouter` implementado con cascade L1→L2→L0
- [ ] `_keywords_from()` y `_chunks_contain_keywords()` funcionando
- [ ] `_is_code_query()` detecta correctamente queries técnicas
- [ ] RRF fusion entre knowledge y code collections
- [ ] Endpoint `POST /brain/query` registrado en FastAPI
- [ ] Endpoints CRUD de pasaportes (`GET/DELETE /brain/{source}`)
- [ ] `force_l0` funcional desde request y desde UI (F6)
- [ ] Badge de nivel (`🧠 Brain`, `📚 Knowledge`, `🤖 LLM`) en respuesta
- [ ] Tests unitarios y de integración pasando
- [ ] La consulta existente de SurfSense (`/api/v1/chat`) sigue funcionando en paralelo

---

**Anterior:** [F3 — Síntesis Multi-call y Pasaporte Semántico](./F3-sintesis-pasaporte.md)  
**Siguiente:** [F5 — Conectores SurfSense → Pipeline Brain](./F5-conectores-pipeline.md)
