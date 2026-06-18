import asyncio
import contextlib
import functools
import logging
import os
import time
from datetime import datetime

from app.observability import metrics as ot_metrics, otel as ot
from app.utils.perf import get_perf_logger

_MAX_FETCH_CHUNKS_PER_DOC = 20

# ── F5.3 — Configuración del pipeline de búsqueda ─────────────────────────
# BRAIN_INGESTION_ENABLED=true → vector search en Qdrant (nomic-embed-text 768d)
# BRAIN_INGESTION_ENABLED=false → vector search en pgvector (all-MiniLM-L6-v2 384d)
# BM25 siempre en PostgreSQL (to_tsvector) independientemente del flag.
_BRAIN_INGESTION_ENABLED = os.getenv("BRAIN_INGESTION_ENABLED", "true").lower() == "true"

logger = logging.getLogger(__name__)


def _instrument_search(mode: str):
    def _decorator(func):
        @functools.wraps(func)
        async def _wrapper(
            self, query_text: str, top_k: int, search_space_id: int, *args, **kwargs
        ):
            t0 = time.perf_counter()
            with ot.kb_search_span(
                search_space_id=search_space_id,
                query_chars=len(query_text),
                extra={"search.surface": "chunks", "search.mode": mode},
            ) as sp:
                try:
                    result = await func(
                        self, query_text, top_k, search_space_id, *args, **kwargs
                    )
                except Exception:
                    ot_metrics.record_kb_search_duration(
                        (time.perf_counter() - t0) * 1000,
                        search_space_id=search_space_id,
                        surface="chunks",
                    )
                    raise
                sp.set_attribute("result.count", len(result))
                ot_metrics.record_kb_search_duration(
                    (time.perf_counter() - t0) * 1000,
                    search_space_id=search_space_id,
                    surface="chunks",
                )
                return result

        return _wrapper

    return _decorator


class ChucksHybridSearchRetriever:
    def __init__(self, db_session):
        """
        Initialize the hybrid search retriever with a database session.

        Args:
            db_session: SQLAlchemy AsyncSession from FastAPI dependency injection
        """
        self.db_session = db_session

    @_instrument_search("vector")
    async def vector_search(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list:
        """
        Perform vector similarity search on chunks.

        F5.3: Dispatches between Qdrant (BRAIN_INGESTION_ENABLED=true) and
        pgvector (BRAIN_INGESTION_ENABLED=false).

        When using Qdrant, queries the 'knowledge' collection with
        nomic-embed-text embeddings (768d). Results are correlated back
        to PostgreSQL Chunk objects via document_id for compatibility.

        When using pgvector (fallback), uses the original Chunk.embedding
        column with all-MiniLM-L6-v2 (384d).

        BM25 full-text search is NOT affected by this dispatch — it always
        runs on PostgreSQL to_tsvector regardless of the flag.

        Args:
            query_text: The search query text
            top_k: Number of results to return
            search_space_id: The search space ID to search within
            start_date: Optional start date for filtering documents by updated_at
            end_date: Optional end date for filtering documents by updated_at

        Returns:
            List of chunks sorted by vector similarity
        """
        if _BRAIN_INGESTION_ENABLED:
            return await self._vector_search_qdrant(
                query_text, top_k, search_space_id, start_date, end_date,
            )
        return await self._vector_search_pgvector(
            query_text, top_k, search_space_id, start_date, end_date,
        )

    async def _vector_search_qdrant(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list:
        """
        F5.3 — Vector search via Qdrant colección 'knowledge'.

        Embede la query con nomic-embed-text (mismo modelo que la ingesta)
        y busca en Qdrant filtrando por search_space_id.

        Devuelve objetos compatibles con el formato esperado por los llamadores
        (Chunk-like dicts con content, document, score).
        """
        from app.brain.collections import KNOWLEDGE
        from app.brain.qdrant_manager import QdrantManager
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        perf = get_perf_logger()
        t0 = time.perf_counter()

        # Embedir con nomic-embed-text vía Ollama (mismo modelo que la ingesta)
        embedding = await asyncio.to_thread(self._embed_nomic, query_text)

        # Buscar en Qdrant
        qdrant = QdrantManager.get_instance().client
        must_conditions = [
            FieldCondition(
                key="search_space_id",
                match=MatchValue(value=str(search_space_id)),
            )
        ]

        try:
            results = qdrant.search(
                collection_name=KNOWLEDGE,
                query_vector=embedding,
                query_filter=Filter(must=must_conditions),
                limit=top_k,
                with_payload=True,
            )
        except Exception as exc:
            logger.error(
                "[chunk_search] Qdrant vector_search FAILED space=%d: %s",
                search_space_id, exc, exc_info=True,
            )
            return []

        perf.info(
            "[chunk_search] vector_search_qdrant in %.3fs results=%d space=%d",
            time.perf_counter() - t0, len(results), search_space_id,
        )
        return results

    async def _vector_search_pgvector(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list:
        """
        Vector search original vía pgvector (fallback cuando BRAIN_INGESTION_ENABLED=false).
        """
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from app.config import config
        from app.db import Chunk, Document

        perf = get_perf_logger()
        t0 = time.perf_counter()

        embedding_model = config.embedding_model_instance
        t_embed = time.perf_counter()
        query_embedding = await asyncio.to_thread(embedding_model.embed, query_text)
        perf.debug(
            "[chunk_search] vector_search_pgvector embedding in %.3fs",
            time.perf_counter() - t_embed,
        )

        query = (
            select(Chunk)
            .options(joinedload(Chunk.document).joinedload(Document.search_space))
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.search_space_id == search_space_id)
        )
        if start_date is not None:
            query = query.where(Document.updated_at >= start_date)
        if end_date is not None:
            query = query.where(Document.updated_at <= end_date)

        query = query.order_by(Chunk.embedding.op("<=>")(query_embedding)).limit(top_k)

        t_db = time.perf_counter()
        result = await self.db_session.execute(query)
        chunks = result.scalars().all()
        perf.info(
            "[chunk_search] vector_search_pgvector in %.3fs results=%d space=%d",
            time.perf_counter() - t_db, len(chunks), search_space_id,
        )
        return chunks

    @_instrument_search("full_text")
    async def full_text_search(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list:
        """
        Perform full-text keyword search on chunks.

        Args:
            query_text: The search query text
            top_k: Number of results to return
            search_space_id: The search space ID to search within
            start_date: Optional start date for filtering documents by updated_at
            end_date: Optional end date for filtering documents by updated_at

        Returns:
            List of chunks sorted by text relevance
        """
        from sqlalchemy import func, select
        from sqlalchemy.orm import joinedload

        from app.db import Chunk, Document

        perf = get_perf_logger()
        t0 = time.perf_counter()

        # Create tsvector and tsquery for PostgreSQL full-text search
        tsvector = func.to_tsvector("english", Chunk.content)
        tsquery = func.plainto_tsquery("english", query_text)

        # Build the query filtered by search space
        query = (
            select(Chunk)
            .options(joinedload(Chunk.document).joinedload(Document.search_space))
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.search_space_id == search_space_id)
            .where(
                tsvector.op("@@")(tsquery)
            )  # Only include results that match the query
        )

        # Add time-based filtering if provided
        if start_date is not None:
            query = query.where(Document.updated_at >= start_date)
        if end_date is not None:
            query = query.where(Document.updated_at <= end_date)

        # Add text search ranking
        query = query.order_by(func.ts_rank_cd(tsvector, tsquery).desc()).limit(top_k)

        # Execute the query
        result = await self.db_session.execute(query)
        chunks = result.scalars().all()
        perf.info(
            "[chunk_search] full_text_search in %.3fs results=%d space=%d",
            time.perf_counter() - t0,
            len(chunks),
            search_space_id,
        )

        return chunks

    @_instrument_search("hybrid")
    async def hybrid_search(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        document_type: str | list[str] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        query_embedding: list | None = None,
    ) -> list:
        """
        Hybrid search that returns **documents** (not individual chunks).

        F5.3: Dispatches between two backends:
          - BRAIN_INGESTION_ENABLED=true: Qdrant (vector) + PostgreSQL (BM25),
            merged with RRF in Python.
          - BRAIN_INGESTION_ENABLED=false: pgvector + BM25 en PostgreSQL,
            merged with RRF en SQL (original SurfSense).

        Ambos paths devuelven el mismo formato de documento agrupado para
        compatibilidad con knowledge_search.py y el chat de SurfSense.

        Args:
            query_text: The search query text
            top_k: Number of documents to return
            search_space_id: The search space ID to search within
            document_type: Optional document type filter
            start_date: Optional start date filter
            end_date: Optional end date filter
            query_embedding: Pre-computed embedding vector. If None, will be computed.

        Returns:
            List of document-grouped dicts with content, chunks, score, document metadata.
        """
        if _BRAIN_INGESTION_ENABLED:
            return await self._hybrid_search_qdrant(
                query_text, top_k, search_space_id,
                document_type, start_date, end_date, query_embedding,
            )
        return await self._hybrid_search_pgvector(
            query_text, top_k, search_space_id,
            document_type, start_date, end_date, query_embedding,
        )

    # ------------------------------------------------------------------
    # F5.3 — Hybrid search con Qdrant (vectores) + PostgreSQL (BM25)
    # ------------------------------------------------------------------

    async def _hybrid_search_qdrant(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        document_type: str | list[str] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        query_embedding: list | None = None,
    ) -> list:
        """
        F5.3 — Hybrid search: Qdrant vectors + PostgreSQL BM25, RRF en Python.

        Flujo:
          1. Búsqueda vectorial en Qdrant colección 'knowledge'
             → doc_ids rankeados por similaridad semántica
          2. Búsqueda BM25 en PostgreSQL to_tsvector
             → doc_ids rankeados por relevancia keyword
          3. RRF (Reciprocal Rank Fusion) en Python
             → top_k doc_ids combinados
          4. Fetch chunks completos de PostgreSQL para los top_k docs
          5. Devolver en el mismo formato que _hybrid_search_pgvector()

        El formato de retorno es idéntico al path pgvector para que
        knowledge_search.py y search_knowledge_base no necesiten cambios.
        """
        from sqlalchemy import func, or_, select
        from sqlalchemy.orm import joinedload

        from app.brain.collections import KNOWLEDGE
        from app.brain.qdrant_manager import QdrantManager
        from app.db import Chunk, Document, DocumentType as DT
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        perf = get_perf_logger()
        t0 = time.perf_counter()
        k_rrf = 60  # constante RRF (mismo valor que el path pgvector original)
        n_candidates = top_k * 5

        # ── Paso 1: Búsqueda vectorial en Qdrant ────────────────────────
        if query_embedding is None:
            query_embedding = await asyncio.to_thread(self._embed_nomic, query_text)

        qdrant = QdrantManager.get_instance().client
        must_conditions = [
            FieldCondition(
                key="search_space_id",
                match=MatchValue(value=str(search_space_id)),
            )
        ]

        try:
            qdrant_results = qdrant.search(
                collection_name=KNOWLEDGE,
                query_vector=(
                    query_embedding if isinstance(query_embedding, list)
                    else query_embedding.tolist()
                ),
                query_filter=Filter(must=must_conditions),
                limit=n_candidates,
                with_payload=True,
            )
        except Exception as exc:
            logger.error(
                "[chunk_search] Qdrant hybrid_search FAILED space=%d: %s",
                search_space_id, exc, exc_info=True,
            )
            qdrant_results = []

        # Extraer doc_ids rankeados por score semántico de Qdrant
        # El payload contiene doc_id (str del Document.id de PostgreSQL)
        semantic_doc_ranks: dict[int, int] = {}  # doc_id → rank (1-based)
        semantic_doc_content: dict[int, list[str]] = {}  # doc_id → textos de chunks
        for rank, point in enumerate(qdrant_results, 1):
            payload = point.payload or {}
            doc_id_str = payload.get("doc_id", "")
            if not doc_id_str:
                continue
            try:
                doc_id = int(doc_id_str)
            except (ValueError, TypeError):
                continue
            if doc_id not in semantic_doc_ranks:
                semantic_doc_ranks[doc_id] = rank
                semantic_doc_content[doc_id] = []
            semantic_doc_content[doc_id].append(payload.get("text", ""))

        perf.debug(
            "[chunk_search] hybrid_qdrant semantic: %d points → %d docs in %.3fs",
            len(qdrant_results), len(semantic_doc_ranks),
            time.perf_counter() - t0,
        )

        # ── Paso 2: Búsqueda BM25 en PostgreSQL ─────────────────────────
        tsvector = func.to_tsvector("english", Chunk.content)
        tsquery = func.plainto_tsquery("english", query_text)

        bm25_conditions = [
            Document.search_space_id == search_space_id,
            func.coalesce(Document.status["state"].astext, "ready") != "deleting",
            tsvector.op("@@")(tsquery),
        ]
        if document_type is not None:
            type_list = document_type if isinstance(document_type, list) else [document_type]
            doc_type_enums = []
            for dt in type_list:
                if isinstance(dt, str):
                    with contextlib.suppress(KeyError):
                        doc_type_enums.append(DT[dt])
                else:
                    doc_type_enums.append(dt)
            if doc_type_enums:
                if len(doc_type_enums) == 1:
                    bm25_conditions.append(Document.document_type == doc_type_enums[0])
                else:
                    bm25_conditions.append(Document.document_type.in_(doc_type_enums))
        if start_date is not None:
            bm25_conditions.append(Document.updated_at >= start_date)
        if end_date is not None:
            bm25_conditions.append(Document.updated_at <= end_date)

        bm25_query = (
            select(
                Chunk.id,
                Chunk.document_id,
                func.ts_rank_cd(tsvector, tsquery).label("bm25_score"),
            )
            .join(Document, Chunk.document_id == Document.id)
            .where(*bm25_conditions)
            .order_by(func.ts_rank_cd(tsvector, tsquery).desc())
            .limit(n_candidates)
        )

        t_bm25 = time.perf_counter()
        bm25_result = await self.db_session.execute(bm25_query)
        bm25_rows = bm25_result.all()

        # Extraer doc_ids rankeados por BM25
        keyword_doc_ranks: dict[int, int] = {}  # doc_id → rank (1-based)
        rank_counter = 0
        for row in bm25_rows:
            doc_id = row.document_id
            if doc_id not in keyword_doc_ranks:
                rank_counter += 1
                keyword_doc_ranks[doc_id] = rank_counter

        perf.debug(
            "[chunk_search] hybrid_qdrant bm25: %d chunks → %d docs in %.3fs",
            len(bm25_rows), len(keyword_doc_ranks),
            time.perf_counter() - t_bm25,
        )

        # ── Paso 3: RRF merge en Python ────────────────────────────────
        all_doc_ids = set(semantic_doc_ranks) | set(keyword_doc_ranks)
        rrf_scores: dict[int, float] = {}
        for doc_id in all_doc_ids:
            sem_rank = semantic_doc_ranks.get(doc_id)
            kw_rank = keyword_doc_ranks.get(doc_id)
            score = 0.0
            if sem_rank is not None:
                score += 1.0 / (k_rrf + sem_rank)
            if kw_rank is not None:
                score += 1.0 / (k_rrf + kw_rank)
            rrf_scores[doc_id] = score

        # Ordenar por RRF score descendente, tomar top_k
        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        doc_ids = [doc_id for doc_id, _ in sorted_docs[:top_k]]
        doc_scores = {doc_id: score for doc_id, score in sorted_docs[:top_k]}

        if not doc_ids:
            return []

        perf.debug(
            "[chunk_search] hybrid_qdrant RRF: %d sem + %d kw → %d merged docs",
            len(semantic_doc_ranks), len(keyword_doc_ranks), len(doc_ids),
        )

        # ── Paso 4: Fetch chunks + metadata de PostgreSQL ──────────────
        # Cargar Document metadata para los doc_ids seleccionados
        doc_meta_query = (
            select(Document)
            .where(Document.id.in_(doc_ids))
        )
        doc_meta_result = await self.db_session.execute(doc_meta_query)
        doc_meta_cache: dict[int, dict] = {}
        for doc in doc_meta_result.scalars().all():
            doc_meta_cache[doc.id] = {
                "id": doc.id,
                "title": doc.title,
                "document_type": doc.document_type.value if hasattr(doc, "document_type") else None,
                "metadata": doc.document_metadata,
            }

        # Fetch chunks con límite por documento (mismo patrón que pgvector path)
        numbered = (
            select(
                Chunk.id.label("chunk_id"),
                func.row_number()
                .over(partition_by=Chunk.document_id, order_by=Chunk.id)
                .label("rn"),
            )
            .where(Chunk.document_id.in_(doc_ids))
            .subquery("numbered")
        )
        chunk_query = (
            select(Chunk.id, Chunk.content, Chunk.document_id)
            .join(numbered, Chunk.id == numbered.c.chunk_id)
            .where(numbered.c.rn <= _MAX_FETCH_CHUNKS_PER_DOC)
            .order_by(Chunk.document_id, Chunk.id)
        )

        t_fetch = time.perf_counter()
        chunks_result = await self.db_session.execute(chunk_query)
        fetched_chunks = chunks_result.all()
        perf.debug(
            "[chunk_search] hybrid_qdrant chunk fetch in %.3fs rows=%d",
            time.perf_counter() - t_fetch, len(fetched_chunks),
        )

        # ── Paso 5: Ensamblar resultado (mismo formato que pgvector path) ──
        doc_map: dict[int, dict] = {
            doc_id: {
                "document_id": doc_id,
                "content": "",
                "score": float(doc_scores.get(doc_id, 0.0)),
                "chunks": [],
                "matched_chunk_ids": [],
                "document": doc_meta_cache.get(doc_id, {}),
                "source": (doc_meta_cache.get(doc_id) or {}).get("document_type"),
            }
            for doc_id in doc_ids
        }

        for row in fetched_chunks:
            doc_id = row.document_id
            if doc_id not in doc_map:
                continue
            doc_map[doc_id]["chunks"].append({"chunk_id": row.id, "content": row.content})

        final_docs: list[dict] = []
        for doc_id in doc_ids:
            entry = doc_map[doc_id]
            entry["content"] = "\n\n".join(
                c["content"] for c in entry.get("chunks", []) if c.get("content")
            )
            final_docs.append(entry)

        perf.info(
            "[chunk_search] hybrid_search_qdrant TOTAL in %.3fs docs=%d space=%d type=%s",
            time.perf_counter() - t0, len(final_docs), search_space_id, document_type,
        )
        return final_docs

    # ------------------------------------------------------------------
    # Fallback: Hybrid search con pgvector + PostgreSQL BM25 (original)
    # ------------------------------------------------------------------

    async def _hybrid_search_pgvector(
        self,
        query_text: str,
        top_k: int,
        search_space_id: int,
        document_type: str | list[str] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        query_embedding: list | None = None,
    ) -> list:
        """
        Fallback: hybrid search original de SurfSense con pgvector + BM25.

        Se usa cuando BRAIN_INGESTION_ENABLED=false. Código original de SurfSense
        sin modificaciones: dos CTEs (semántico pgvector + keyword BM25) combinados
        con RRF via FULL OUTER JOIN en SQL.
        """
        from sqlalchemy import func, or_, select, text
        from sqlalchemy.orm import joinedload

        from app.config import config
        from app.db import Chunk, Document, DocumentType as DT

        perf = get_perf_logger()
        t0 = time.perf_counter()

        if query_embedding is None:
            embedding_model = config.embedding_model_instance
            t_embed = time.perf_counter()
            query_embedding = await asyncio.to_thread(embedding_model.embed, query_text)
            perf.debug(
                "[chunk_search] hybrid_search_pgvector embedding in %.3fs",
                time.perf_counter() - t_embed,
            )

        k = 60
        n_results = top_k * 5

        tsvector = func.to_tsvector("english", Chunk.content)
        tsquery = func.plainto_tsquery("english", query_text)

        base_conditions = [
            Document.search_space_id == search_space_id,
            func.coalesce(Document.status["state"].astext, "ready") != "deleting",
        ]

        if document_type is not None:
            type_list = (
                document_type if isinstance(document_type, list) else [document_type]
            )
            doc_type_enums = []
            for dt in type_list:
                if isinstance(dt, str):
                    with contextlib.suppress(KeyError):
                        doc_type_enums.append(DT[dt])
                else:
                    doc_type_enums.append(dt)
            if not doc_type_enums:
                return []
            if len(doc_type_enums) == 1:
                base_conditions.append(Document.document_type == doc_type_enums[0])
            else:
                base_conditions.append(Document.document_type.in_(doc_type_enums))

        if start_date is not None:
            base_conditions.append(Document.updated_at >= start_date)
        if end_date is not None:
            base_conditions.append(Document.updated_at <= end_date)

        semantic_search_cte = (
            select(
                Chunk.id,
                func.rank()
                .over(order_by=Chunk.embedding.op("<=>")(query_embedding))
                .label("rank"),
            )
            .join(Document, Chunk.document_id == Document.id)
            .where(*base_conditions)
        )
        semantic_search_cte = (
            semantic_search_cte.order_by(Chunk.embedding.op("<=>")(query_embedding))
            .limit(n_results)
            .cte("semantic_search")
        )

        keyword_search_cte = (
            select(
                Chunk.id,
                func.rank()
                .over(order_by=func.ts_rank_cd(tsvector, tsquery).desc())
                .label("rank"),
            )
            .join(Document, Chunk.document_id == Document.id)
            .where(*base_conditions)
            .where(tsvector.op("@@")(tsquery))
        )
        keyword_search_cte = (
            keyword_search_cte.order_by(func.ts_rank_cd(tsvector, tsquery).desc())
            .limit(n_results)
            .cte("keyword_search")
        )

        final_query = (
            select(
                Chunk,
                (
                    func.coalesce(1.0 / (k + semantic_search_cte.c.rank), 0.0)
                    + func.coalesce(1.0 / (k + keyword_search_cte.c.rank), 0.0)
                ).label("score"),
            )
            .select_from(
                semantic_search_cte.outerjoin(
                    keyword_search_cte,
                    semantic_search_cte.c.id == keyword_search_cte.c.id,
                    full=True,
                )
            )
            .join(
                Chunk,
                Chunk.id
                == func.coalesce(semantic_search_cte.c.id, keyword_search_cte.c.id),
            )
            .options(joinedload(Chunk.document))
            .order_by(text("score DESC"))
            .limit(top_k)
        )

        t_rrf = time.perf_counter()
        result = await self.db_session.execute(final_query)
        chunks_with_scores = result.all()
        perf.info(
            "[chunk_search] hybrid_search_pgvector RRF in %.3fs results=%d space=%d type=%s",
            time.perf_counter() - t_rrf, len(chunks_with_scores),
            search_space_id, document_type,
        )

        if not chunks_with_scores:
            return []

        serialized_chunk_results: list[dict] = []
        for chunk, score in chunks_with_scores:
            serialized_chunk_results.append({
                "chunk_id": chunk.id,
                "content": chunk.content,
                "score": float(score),
                "document": {
                    "id": chunk.document.id,
                    "title": chunk.document.title,
                    "document_type": chunk.document.document_type.value
                    if hasattr(chunk.document, "document_type") else None,
                    "metadata": chunk.document.document_metadata,
                },
            })

        doc_scores: dict[int, float] = {}
        doc_order: list[int] = []
        for item in serialized_chunk_results:
            doc_id = item.get("document", {}).get("id")
            if doc_id is None:
                continue
            if doc_id not in doc_scores:
                doc_scores[doc_id] = item.get("score", 0.0)
                doc_order.append(doc_id)
            else:
                doc_scores[doc_id] = max(doc_scores[doc_id], item.get("score", 0.0))

        doc_ids = doc_order[:top_k]
        if not doc_ids:
            return []

        matched_chunk_ids: set[int] = {
            item["chunk_id"] for item in serialized_chunk_results
        }
        doc_meta_cache: dict[int, dict] = {}
        for item in serialized_chunk_results:
            did = item["document"]["id"]
            if did not in doc_meta_cache:
                doc_meta_cache[did] = item["document"]

        numbered = (
            select(
                Chunk.id.label("chunk_id"),
                func.row_number()
                .over(partition_by=Chunk.document_id, order_by=Chunk.id)
                .label("rn"),
            )
            .where(Chunk.document_id.in_(doc_ids))
            .subquery("numbered")
        )

        matched_list = list(matched_chunk_ids)
        if matched_list:
            chunk_filter = or_(
                numbered.c.rn <= _MAX_FETCH_CHUNKS_PER_DOC,
                Chunk.id.in_(matched_list),
            )
        else:
            chunk_filter = numbered.c.rn <= _MAX_FETCH_CHUNKS_PER_DOC

        chunk_query = (
            select(Chunk.id, Chunk.content, Chunk.document_id)
            .join(numbered, Chunk.id == numbered.c.chunk_id)
            .where(chunk_filter)
            .order_by(Chunk.document_id, Chunk.id)
        )

        t_fetch = time.perf_counter()
        chunks_result = await self.db_session.execute(chunk_query)
        fetched_chunks = chunks_result.all()
        perf.debug(
            "[chunk_search] hybrid_pgvector chunk fetch in %.3fs rows=%d",
            time.perf_counter() - t_fetch, len(fetched_chunks),
        )

        doc_map: dict[int, dict] = {
            doc_id: {
                "document_id": doc_id,
                "content": "",
                "score": float(doc_scores.get(doc_id, 0.0)),
                "chunks": [],
                "matched_chunk_ids": [],
                "document": doc_meta_cache.get(doc_id, {}),
                "source": (doc_meta_cache.get(doc_id) or {}).get("document_type"),
            }
            for doc_id in doc_ids
        }

        for row in fetched_chunks:
            doc_id = row.document_id
            if doc_id not in doc_map:
                continue
            doc_entry = doc_map[doc_id]
            doc_entry["chunks"].append({"chunk_id": row.id, "content": row.content})
            if row.id in matched_chunk_ids:
                doc_entry["matched_chunk_ids"].append(row.id)

        final_docs: list[dict] = []
        for doc_id in doc_ids:
            entry = doc_map[doc_id]
            entry["content"] = "\n\n".join(
                c["content"] for c in entry.get("chunks", []) if c.get("content")
            )
            final_docs.append(entry)

        perf.info(
            "[chunk_search] hybrid_search_pgvector TOTAL in %.3fs docs=%d space=%d type=%s",
            time.perf_counter() - t0, len(final_docs), search_space_id, document_type,
        )
        return final_docs

    # ------------------------------------------------------------------
    # Helper: embedding centralizado via unified_embedder (F5.4)
    # ------------------------------------------------------------------

    @staticmethod
    def _embed_nomic(text: str) -> list[float]:
        """
        Genera embedding con el modelo unificado de SecondBrainSense.

        Delega a unified_embedder.embed_query() que centraliza toda la
        lógica de embedding en un solo módulo (F5.4). Garantiza que
        la búsqueda usa el mismo modelo que la ingesta.

        Args:
            text: Texto a embedir.

        Returns:
            Vector de 768 dimensiones (nomic-embed-text por defecto).
        """
        from app.indexing_pipeline.unified_embedder import embed_query
        return embed_query(text)
