"""
ingest_router.py
----------------
IngestRouter — lee el embedding_scope del frontmatter del .md y distribuye
el contenido a las colecciones brain, knowledge y code.

Diferencias respecto al sketch del plan:
- Constructor toma solo qdrant_client (sin embed_clients dict) para simplificar.
  El modelo de embedding se resuelve por colección en tiempo de ejecución.
- Fallback automático a nomic-embed-text si nomic-embed-code no está disponible.
- Normalización de bloques: compatible con el formato antiguo {text, page}
  (extractores SB-1/SB-2) y el nuevo {content, content_type, ...} (SB-2.6).
- _ingest_brain delega en BrainIngestor para reutilizar la lógica de chunking.
- Todos los métodos privados están completamente implementados (sin ...).
"""
import re
import os
import datetime
import hashlib
import logging
from typing import Optional

import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from app.brain.collections import BRAIN, KNOWLEDGE, CODE
from app.brain.brain_ingest import BrainIngestor
from app.brain.writer import _slugify as _slug
from app.brain.chunking import get_chunks

log = logging.getLogger(__name__)


def _embed(text: str, model: str = "nomic-embed-text") -> list[float]:
    """
    Genera embedding con Ollama.
    - Si el modelo solicitado no está disponible, hace fallback a nomic-embed-text.
    - Capa 2 de seguridad: si el texto supera el contexto del modelo (chunks oversized
      de código Python/SQL/XML/JSON sin separadores de párrafo), trunca a MAX_CHUNK_CHARS
      y reintenta. Esto complementa la Capa 1 en chunking.py (_enforce_max_chunk_size).
    """
    import ollama
    _max_chars = int(os.getenv("MAX_CHUNK_CHARS", 4000))
    client = ollama.Client(host=os.getenv("OLLAMA_HOST", "http://localhost:11434"), timeout=120)

    def _call(t: str, m: str) -> list[float]:
        try:
            return client.embeddings(model=m, prompt=t)["embedding"]
        except Exception as exc:
            err = str(exc).lower()
            if "context length" in err or "input length" in err:
                truncated = t[:_max_chars]
                log.warning(
                    "EMBED_OVERSIZE (capa 2) modelo='%s': chunk de %d chars superó el contexto. "
                    "Truncando a %d chars y reintentando. Preview: %s",
                    m, len(t), len(truncated), t[:80].replace('\n', ' ')
                )
                return client.embeddings(model=m, prompt=truncated)["embedding"]
            raise

    try:
        return _call(text, model)
    except Exception:
        if model != "nomic-embed-text":
            log.warning(
                "Modelo '%s' no disponible para embedding; usando nomic-embed-text como fallback.",
                model,
            )
            return _call(text, "nomic-embed-text")
        raise


class IngestRouter:
    """
    Distribuye el contenido de un documento a las tres colecciones Qdrant
    según el campo embedding_scope del frontmatter del .md generado.

    Flujo:
        brain     — siempre, chunks del .md por sección ##
        knowledge — si en scope: Source Extract (o raw completo si raw_ingest: true)
        code      — si en scope y hay bloques con content_type: code
    """

    def __init__(self, qdrant_client: QdrantClient):
        self.qdrant = qdrant_client

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def route(self, md_content: str, blocks: list[dict], source: str, ingest_metadata: dict = None) -> dict:
        """
        Distribuye el contenido a las colecciones según el frontmatter.

        Args:
            md_content:      contenido completo del .md generado por el synthesizer.
            blocks:          lista de bloques del extractor.
            source:          nombre del fichero original (ej: informe.pdf)
            ingest_metadata: dict opcional con trazabilidad de ingesta:
                             {ingest_origin, ingest_date, ingest_time, ingest_path}

        Returns:
            {
                "brain":     {"chunks_created": N},
                "knowledge": {"chunks_created": N},
                "code":      {"chunks_created": N},
            }
        """
        log.info("[router] route START source='%s' bloques=%d", source, len(blocks))
        normalized = self._normalize_blocks(blocks)
        meta = self._parse_frontmatter(md_content)

        scope = meta.get("embedding_scope") or ["brain", "knowledge"]
        if isinstance(scope, str):
            scope = [scope]
        # brain siempre debe estar en scope
        if BRAIN not in scope:
            scope = [BRAIN] + list(scope)
        # Auto-detectar: si hay bloques de código, añadir 'code' al scope
        # aunque el LLM no lo haya incluido en el frontmatter
        has_code_blocks = any(b.get("content_type") == "code" for b in normalized)
        if has_code_blocks and CODE not in scope:
            log.info(
                "Bloques de código detectados en '%s'; añadiendo 'code' al scope automáticamente.",
                source,
            )
            scope = list(scope) + [CODE]

        raw_ingest_flag = bool(meta.get("raw_ingest", False))
        log.info("[router] route '%s' → scope=%s raw_ingest=%s", source, scope, raw_ingest_flag)
        results = {}

        # Fuente canónica = slug (igual en todas las colecciones para que el router
        # de consulta pueda cruzar brain → knowledge/code por source).
        # El nombre original queda en ingest_metadata["original_source"].
        canonical_source = _slug(source)
        if ingest_metadata is None:
            ingest_metadata = {}
        ingest_metadata.setdefault("original_source", source)

        # Calcular formato fuente una sola vez — se usa en Step 2 y Step 4
        _DOC_RICH_FORMATS = {
            # Familia CÓDIGO
            "ipynb", "py", "sql",
            # Familia CONFIG/ORQUESTACIÓN
            "json", "xml",
            # Familia DATOS TABULARES
            "csv", "xlsx",
            # Familia DOCUMENTOS RICOS
            "pdf", "docx", "pptx", "html",
            # Familia MARKDOWN / TEXTO PLANO
            "md", "txt",
            # Familia VISUAL
            "drawio",
        }
        _source_format = ""
        _src = meta.get("source")
        if isinstance(_src, dict):
            _source_format = _src.get("format", "").lower().lstrip(".")
        elif isinstance(_src, str) and _src:
            _source_format = os.path.splitext(_src)[-1].lstrip(".").lower()
        if not _source_format:
            _source_format = os.path.splitext(canonical_source)[-1].lstrip(".").lower()

        # 1. BRAIN — siempre
        results[BRAIN] = self._ingest_brain(md_content, canonical_source, meta, ingest_metadata)

        # 2. KNOWLEDGE — si está en scope
        # Para tipos doc-rich (no md): los bloques originales del extractor son la fuente
        # de calidad → Step 4 los ingesta con _ingest_knowledge_raw. El Source Extract
        # sería redundante y de menor calidad, se omite.
        # Para md: blocks=[] (edición manual de pasaporte), Step 4 no puede hacer nada;
        # el Source Extract del .md ES el contenido de referencia → se preserva.
        # Para tipos fuera de _DOC_RICH_FORMATS: Source Extract es la única fuente disponible.
        if KNOWLEDGE in scope:
            text_blocks = [b for b in normalized if b["content_type"] == "text"]
            if raw_ingest_flag and text_blocks:
                log.info(
                    "[router] KNOWLEDGE route '%s' → raw_ingest inmediato con %d bloques de texto",
                    canonical_source,
                    len(text_blocks),
                )
                results[KNOWLEDGE] = self._ingest_knowledge_raw(text_blocks, canonical_source, meta, ingest_metadata)
            elif _source_format in _DOC_RICH_FORMATS and _source_format != "md":
                # Defer to Step 4 — bloques originales del extractor son mejores
                log.info(
                    "[router] KNOWLEDGE route '%s' → diferido a Step 4 (source_format='%s', text_blocks=%d)",
                    canonical_source,
                    _source_format,
                    len(text_blocks),
                )
            else:
                extract = self._parse_source_extract(md_content)
                if not extract:
                    # Fallback: usar el cuerpo completo del .md sin frontmatter
                    extract = self._strip_frontmatter(md_content)
                log.info(
                    "[router] KNOWLEDGE route '%s' → Source Extract / body md_chars=%d",
                    canonical_source,
                    len(extract),
                )
                results[KNOWLEDGE] = self._ingest_knowledge_extract(extract, canonical_source, meta, ingest_metadata)
        else:
            log.info("[router] KNOWLEDGE route '%s' → omitido por scope=%s", canonical_source, scope)

        # 3. CODE — si está en scope y hay bloques de código
        if CODE in scope:
            code_blocks = [b for b in normalized if b["content_type"] == "code"]
            if code_blocks:
                log.info(
                    "[router] CODE route '%s' → %d bloques de código detectados",
                    canonical_source,
                    len(code_blocks),
                )
                results[CODE] = self._ingest_code(code_blocks, canonical_source, meta, ingest_metadata)
            else:
                log.info("[router] CODE route '%s' → 0 bloques de código, no se ingesta", canonical_source)
        else:
            log.info("[router] CODE route '%s' → omitido por scope=%s", canonical_source, scope)

        # 4. FULL DOCUMENT + KNOWLEDGE RAW — para ficheros con documentación rica (no md)
        # Para md: blocks=[] en re-ingesta de pasaportes, text_blocks estará vacío,
        # _ingest_full_document y _ingest_knowledge_raw no harían nada útil.
        if _source_format in _DOC_RICH_FORMATS:
            log.info(
                "[router] STEP4 '%s' → full_doc source_format='%s' bloques=%d",
                canonical_source,
                _source_format,
                len(normalized),
            )
            self._ingest_full_document(normalized, canonical_source, meta, ingest_metadata)
            # Ingestar bloques originales en knowledge (sustituye al Source Extract del Step 2)
            if KNOWLEDGE in scope and not raw_ingest_flag and _source_format != "md":
                _text_blocks = [b for b in normalized if b.get("content_type") == "text"]
                if _text_blocks:
                    log.info(
                        "[router] STEP4 KNOWLEDGE '%s' → raw original text_blocks=%d",
                        canonical_source,
                        len(_text_blocks),
                    )
                    _raw_r = self._ingest_knowledge_raw(
                        _text_blocks, canonical_source, meta, ingest_metadata
                    )
                    results[KNOWLEDGE] = {"chunks_created": _raw_r.get("chunks_created", 0)}
                else:
                    log.info("[router] STEP4 KNOWLEDGE '%s' → 0 bloques de texto, no se ingesta", canonical_source)

        log.info(
            "[router] route DONE '%s' (slug='%s') → brain=%d knowledge=%d code=%d",
            source,
            canonical_source,
            results.get(BRAIN,     {}).get("chunks_created", 0),
            results.get(KNOWLEDGE, {}).get("chunks_created", 0),
            results.get(CODE,      {}).get("chunks_created", 0),
        )
        return results

    # ------------------------------------------------------------------
    # Ingesta por colección
    # ------------------------------------------------------------------

    def _ingest_brain(self, md_content: str, source: str, meta: dict, ingest_metadata: dict = None) -> dict:
        """Delega en BrainIngestor para chunking por secciones ## del .md."""
        log.info(
            "[router] _ingest_brain START source='%s' md_chars=%d colección='%s'",
            source, len(md_content), BRAIN,
        )
        embed_model = type("_M", (), {"embed": staticmethod(lambda t: _embed(t, "nomic-embed-text"))})
        ingestor = BrainIngestor(self.qdrant, embed_model, collection=BRAIN)
        metadata = {
            "kb_id":          meta.get("id", ""),
            "type":           meta.get("type", ""),
            "domain":         meta.get("domain", ""),
            "subdomain":      meta.get("subdomain", ""),
            "importance":     meta.get("importance", "medium"),
            "confidence":     float(meta.get("confidence", 0.5)),
            "refresh_policy": meta.get("refresh_policy", "never"),
            "projects":       meta.get("projects") or [],
        }
        if ingest_metadata:
            metadata.update(ingest_metadata)
        count = ingestor.ingest_md(source, md_content, metadata)
        return {"chunks_created": count}

    def _ingest_knowledge_extract(self, text: str, source: str, meta: dict, ingest_metadata: dict = None) -> dict:
        """Vectoriza el Source Extract (o cuerpo del .md) en knowledge."""
        log.info(
            "[router] _ingest_knowledge_extract START source='%s' chars=%d colección='%s'",
            source,
            len(text),
            KNOWLEDGE,
        )
        return self._upsert_text_chunks(
            text=text,
            source=source,
            meta=meta,
            collection=KNOWLEDGE,
            raw_ingest=False,
            ingest_metadata=ingest_metadata,
        )

    def _ingest_knowledge_raw(self, text_blocks: list[dict], source: str, meta: dict, ingest_metadata: dict = None) -> dict:
        """
        Vectoriza los bloques del extractor en knowledge respetando su estructura.

        Cada bloque (sección de Word, página de PDF, par md+código de notebook...)
        se chunkea individualmente en lugar de concatenar todo en texto corrido.
        Esto preserva las fronteras semánticas que el extractor ya detectó
        (headings, secciones, content_type) y permite al chunker semántico operar
        sobre bloques coherentes en lugar de texto mezclado de 54K chars.

        El payload de cada chunk incluye 'section' con el nombre de la sección
        original, lo que mejora la precisión del RAG al recuperar y citar fuentes.
        """
        total_chars = sum(len(b.get("content", "")) for b in text_blocks)
        log.info(
            "[router] _ingest_knowledge_raw START source='%s' text_blocks=%d chars=%d colección='%s'",
            source,
            len(text_blocks),
            total_chars,
            KNOWLEDGE,
        )

        points = []
        now = datetime.datetime.utcnow()
        ingested_at = now.isoformat()
        base_ingest = {
            "ingest_origin": "file_upload",
            "ingest_date": now.strftime("%Y-%m-%d"),
            "ingest_time": now.strftime("%H:%M:%S"),
            "ingest_path": None,
        }
        if ingest_metadata:
            base_ingest.update(ingest_metadata)

        chunk_counter = 0
        for block_idx, block in enumerate(text_blocks):
            content = block.get("content", "").strip()
            if not content:
                continue

            content_type = block.get("content_type", "text")
            block_meta   = block.get("metadata") or {}
            # Extraer nombre de sección del metadata del extractor
            section = (
                block_meta.get("section")
                or block_meta.get("slide_title")
                or block_meta.get("sheet")
                or block_meta.get("page_name")
                or ""
            )
            page = block.get("page", 1)

            # get_chunks respeta ATOMIC_CONTENT_TYPES (table, callout, code...)
            # y aplica la estrategia semantic/paragraph/fixed configurada
            chunks = get_chunks(content, content_type=content_type)

            for chunk in chunks:
                if not chunk.strip():
                    continue
                vector = _embed(chunk, "nomic-embed-text")
                point_id = int(
                    hashlib.md5(
                        f"{source}::{KNOWLEDGE}::{chunk_counter}".encode()
                    ).hexdigest(),
                    16,
                ) % (10 ** 15)
                if chunk_counter % 10 == 0 or chunk_counter < 3:
                    log.info(
                        "[router] embedding chunk %d | bloque %d/%d | sección='%s'",
                        chunk_counter, block_idx + 1, len(text_blocks),
                        section[:40] if section else "—",
                    )
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "text":           chunk,
                            "source":         source,
                            "kb_id":          meta.get("id", ""),
                            "chunk_index":    chunk_counter,
                            "section":        section,
                            "page":           page,
                            "content_type":   content_type,
                            "domain":         meta.get("domain", ""),
                            "subdomain":      meta.get("subdomain", ""),
                            "tags":           meta.get("tags") or [],
                            "raw_ingest":     True,
                            "refresh_policy": meta.get("refresh_policy", "never"),
                            "level":          2,
                            "ingested_at":    ingested_at,
                            **base_ingest,
                        },
                    )
                )
                chunk_counter += 1

        if points:
            self.qdrant.upsert(collection_name=KNOWLEDGE, points=points)
            log.info(
                "[router] _upsert_text_chunks CHUNKED source='%s' colección='%s' → %d chunks",
                source, KNOWLEDGE, len(points),
            )
            log.info(
                "[router] UPSERT nivel-2 '%s' → %d chunks en '%s'",
                source, len(points), KNOWLEDGE,
            )
        else:
            log.warning(
                "[router] '%s' → 0 chunks para '%s' (texto vacío o sin secciones válidas)",
                source, KNOWLEDGE,
            )
        return {"chunks_created": len(points)}

    def _ingest_full_document(
        self,
        blocks: list[dict],
        source: str,
        meta: dict,
        ingest_metadata: dict | None = None,
    ) -> None:
        """Almacena el documento completo como punto único en KNOWLEDGE con full_doc=True.

        Este punto es recuperado con prioridad absoluta en full-source retrieval,
        garantizando que el usuario recibe el documento original íntegro sin
        artefactos de hallucination del LLM.
        """
        all_text = "\n\n".join(
            b["content"] for b in blocks if b.get("content", "").strip()
        )
        if not all_text.strip():
            return
        embed_text = all_text[:4000]
        vector = _embed(embed_text, "nomic-embed-text")
        point_id = (
            int(hashlib.md5(f"{source}::full_doc".encode()).hexdigest(), 16) % (10 ** 15)
        )
        now = datetime.datetime.utcnow()
        base_ingest: dict = {
            "ingest_origin": "file_upload",
            "ingest_date": now.strftime("%Y-%m-%d"),
            "ingest_time": now.strftime("%H:%M:%S"),
            "ingest_path": None,
        }
        if ingest_metadata:
            base_ingest.update(ingest_metadata)
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "text": all_text,
                "source": source,
                "full_doc": True,
                "chunk_index": -1,
                "level": 2,
                "raw_ingest": True,
                "kb_id": meta.get("id", ""),
                "domain": meta.get("domain", ""),
                "tags": meta.get("tags") or [],
                "ingested_at": now.isoformat(),
                **base_ingest,
            },
        )
        self.qdrant.upsert(collection_name=KNOWLEDGE, points=[point])
        log.info(
            "[router] full_doc upsert '%s' → %d chars en knowledge", source, len(all_text)
        )

    def _upsert_text_chunks(
        self,
        text: str,
        source: str,
        meta: dict,
        collection: str,
        raw_ingest: bool,
        ingest_metadata: dict = None,
    ) -> dict:
        if not text.strip():
            log.info("[router] _upsert_text_chunks SKIP source='%s' colección='%s' (texto vacío)", source, collection)
            return {"chunks_created": 0}
        log.info(
            "[router] _upsert_text_chunks START source='%s' colección='%s' raw=%s chars=%d",
            source,
            collection,
            raw_ingest,
            len(text),
        )
        chunks = get_chunks(text)
        log.info(
            "[router] _upsert_text_chunks CHUNKED source='%s' colección='%s' → %d chunks",
            source,
            collection,
            len(chunks),
        )
        points = []
        now = datetime.datetime.utcnow()
        ingested_at = now.isoformat()
        base_ingest = {
            "ingest_origin": "file_upload",
            "ingest_date": now.strftime("%Y-%m-%d"),
            "ingest_time": now.strftime("%H:%M:%S"),
            "ingest_path": None,
        }
        if ingest_metadata:
            base_ingest.update(ingest_metadata)
        for idx, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            vector = _embed(chunk, "nomic-embed-text")
            point_id = int(
                hashlib.md5(f"{source}::{collection}::{idx}".encode()).hexdigest(), 16
            ) % (10 ** 15)
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "text":           chunk,
                        "source":         source,
                        "kb_id":          meta.get("id", ""),
                        "chunk_index":    idx,
                        "domain":         meta.get("domain", ""),
                        "subdomain":      meta.get("subdomain", ""),
                        "tags":           meta.get("tags") or [],
                        "raw_ingest":     raw_ingest,
                        "refresh_policy": meta.get("refresh_policy", "never"),
                        "level":          2,
                        "ingested_at":    ingested_at,
                        **base_ingest,
                    },
                )
            )
        if points:
            log.debug(
                "[router] upsert nivel-2 '%s' → %d chunks en '%s' (raw=%s)",
                source, len(points), collection, raw_ingest,
            )
            self.qdrant.upsert(collection_name=collection, points=points)
            log.info("[router] UPSERT nivel-2 '%s' → %d chunks en '%s'", source, len(points), collection)
        else:
            log.warning(
                "[router] '%s' → 0 chunks para '%s' (texto vacío o sin secciones válidas)",
                source, collection,
            )
        return {"chunks_created": len(points)}

    def _ingest_code(self, code_blocks: list[dict], source: str, meta: dict, ingest_metadata: dict = None) -> dict:
        """
        Vectoriza bloques de código en la colección code.
        Cada bloque se chunkea antes de embeddear para evitar superar el
        contexto del modelo de embedding (crítico para JSONs/SQL grandes).
        Crea la colección si no existe o si la dimensión del modelo ha cambiado.
        """
        code_model = os.getenv("EMBED_MODEL_CODE", "nomic-embed-code")
        log.info(
            "[router] _ingest_code START source='%s' code_blocks=%d colección='%s' modelo='%s'",
            source,
            len(code_blocks),
            CODE,
            code_model,
        )
        points = []
        now = datetime.datetime.utcnow()
        ingested_at = now.isoformat()
        base_ingest = {
            "ingest_origin": "file_upload",
            "ingest_date": now.strftime("%Y-%m-%d"),
            "ingest_time": now.strftime("%H:%M:%S"),
            "ingest_path": None,
        }
        if ingest_metadata:
            base_ingest.update(ingest_metadata)

        # Obtener dimensión real del modelo de código y asegurar colección
        sample_vector = _embed("test", code_model)
        vector_size = len(sample_vector)
        existing = {c.name: c for c in self.qdrant.get_collections().collections}
        if CODE in existing:
            current_dim = self.qdrant.get_collection(CODE).config.params.vectors.size
            if current_dim != vector_size:
                log.warning(
                    "Colección '%s' tiene dim=%d pero el modelo '%s' produce dim=%d. "
                    "Eliminando y recreando con la dimensión correcta.",
                    CODE, current_dim, code_model, vector_size,
                )
                self.qdrant.delete_collection(CODE)
                existing = {}  # forzar recreación
        if CODE not in existing or CODE not in {c.name for c in self.qdrant.get_collections().collections}:
            from qdrant_client.models import VectorParams, Distance
            self.qdrant.create_collection(
                collection_name=CODE,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            log.info("Colección '%s' creada con dim=%d (modelo: %s).", CODE, vector_size, code_model)

        for block_idx, block in enumerate(code_blocks):
            content = block.get("content", "")
            if not content.strip():
                continue
            # Chunkear el bloque antes de embeddear — fallback para bloques grandes
            chunks = get_chunks(content)
            for chunk_idx, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                vector = _embed(chunk, code_model)
                point_id = int(
                    hashlib.md5(f"{source}::{CODE}::{block_idx}::{chunk_idx}".encode()).hexdigest(), 16
                ) % (10 ** 15)
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "text":        chunk,
                            "source":      source,
                            "kb_id":       meta.get("id", ""),
                            "language":    block.get("language", "unknown"),
                            "module":      block.get("module", ""),
                            "function":    block.get("function", ""),
                            "subdomain":   meta.get("subdomain", ""),
                            "tags":        meta.get("tags") or [],
                            "level":       2,
                            "ingested_at": ingested_at,
                            "block_index": block_idx,
                            "chunk_index": chunk_idx,
                            **base_ingest,
                        },
                    )
                )
        if points:
            log.debug("[router] upsert nivel-2 '%s' → %d chunks en 'code'", source, len(points))
            self.qdrant.upsert(collection_name=CODE, points=points)
            log.info("[router] UPSERT nivel-2 '%s' → %d chunks en 'code'", source, len(points))
        else:
            log.warning("[router] '%s' → 0 chunks de código generados para 'code'", source)
        return {"chunks_created": len(points)}

    def route_raw(self, blocks: list[dict], source: str, ingest_metadata: dict = None) -> dict:
        """
        Enruta bloques directamente a knowledge y/o code SIN síntesis LLM.

        Lógica:
          - Bloques con content_type='code' → colección code (EMBED_MODEL_CODE).
          - Bloques con content_type='text' → colección knowledge (nomic-embed-text).
          - Colección brain: OMITIDA (requiere .md de síntesis).

        Usado por:
          - POST /ingest/file/raw  (ingesta manual sin LLM)
          - Fallback de POST /ingest/file cuando el LLM falla (OOM, timeout)

        Returns:
            {"knowledge": {"chunks_created": N}, "code": {"chunks_created": N}}
        """
        normalized = self._normalize_blocks(blocks)
        empty_meta = {}
        results: dict = {}

        text_blocks = [b for b in normalized if b.get("content_type") != "code"]
        code_blocks = [b for b in normalized if b.get("content_type") == "code"]
        log.info(
            "[router] route_raw START source='%s' bloques=%d (texto=%d código=%d)",
            source, len(normalized), len(text_blocks), len(code_blocks),
        )

        if text_blocks:
            results[KNOWLEDGE] = self._ingest_knowledge_raw(
                text_blocks, source, empty_meta, ingest_metadata
            )
        else:
            results[KNOWLEDGE] = {"chunks_created": 0}

        if code_blocks:
            results[CODE] = self._ingest_code(
                code_blocks, source, empty_meta, ingest_metadata
            )
        else:
            results[CODE] = {"chunks_created": 0}

        log.info(
            "[router] route_raw DONE '%s' → knowledge=%d code=%d",
            source,
            results.get(KNOWLEDGE, {}).get("chunks_created", 0),
            results.get(CODE,      {}).get("chunks_created", 0),
        )
        return results

    # ------------------------------------------------------------------
    # Helpers de parseo
    # ------------------------------------------------------------------

    def _normalize_blocks(self, blocks: list[dict]) -> list[dict]:
        """
        Normaliza bloques al formato {content, content_type, ...}.
        Compatible con el formato antiguo {text, page} de los extractores SB-1/SB-2.
        """
        normalized = []
        for b in blocks:
            if "content_type" in b:
                normalized.append(b)
            else:
                normalized.append(
                    {
                        "content":      b.get("text", b.get("content", "")),
                        "content_type": "text",
                        "page":         b.get("page", 1),
                        "metadata":     b.get("metadata", {}),
                    }
                )
        return normalized

    def _parse_frontmatter(self, md_content: str) -> dict:
        """Extrae y parsea el bloque YAML frontmatter entre delimitadores ---."""
        match = re.match(r"^---\n(.*?)\n---", md_content, re.DOTALL)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            log.warning("Error parseando frontmatter YAML: %s", exc)
            return {}

    def _parse_source_extract(self, md_content: str) -> str:
        """Extrae el contenido de la sección '# 📄 Source Extract' del .md."""
        match = re.search(
            r"#\s+📄\s+Source Extract\s*\n(.*?)(?=\n#\s+|\Z)",
            md_content,
            re.DOTALL,
        )
        return match.group(1).strip() if match else ""

    def _strip_frontmatter(self, md_content: str) -> str:
        """Devuelve el .md sin el bloque frontmatter."""
        return re.sub(r"^---\n.*?\n---\n?", "", md_content, flags=re.DOTALL).strip()
