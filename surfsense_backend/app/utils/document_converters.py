import asyncio
import hashlib
import logging
import threading
import warnings

import numpy as np
from litellm import get_model_info, token_counter

from app.config import config
from app.db import Chunk, DocumentType

logger = logging.getLogger(__name__)

# HuggingFace fast tokenizers (Rust-backed) are not thread-safe — concurrent
# access from multiple threads causes "RuntimeError: Already borrowed".
# This reentrant lock serialises tokenizer + embedding model access so that
# asyncio.to_thread calls from index_batch_parallel don't collide.
_embedding_lock = threading.RLock()


def _get_embedding_max_tokens() -> int:
    """Get the max token limit for the configured embedding model.

    Checks model properties in order: max_seq_length, _max_tokens.
    Falls back to 8192 (OpenAI embedding default).
    """
    model = config.embedding_model_instance
    for attr in ("max_seq_length", "_max_tokens"):
        val = getattr(model, attr, None)
        if isinstance(val, int) and val > 0:
            return val
    return 8192


def truncate_for_embedding(text: str) -> str:
    """Truncate text to fit within the embedding model's context window.

    Uses the embedding model's own tokenizer for accurate token counting,
    so the result is model-agnostic regardless of the underlying provider.
    """
    max_tokens = _get_embedding_max_tokens()
    if len(text) // 3 <= max_tokens:
        return text

    with _embedding_lock:
        tokenizer = config.embedding_model_instance.get_tokenizer()
        tokens = tokenizer.encode(text)
        if len(tokens) <= max_tokens:
            return text

        warnings.warn(
            f"Truncating text from {len(tokens)} to {max_tokens} tokens for embedding.",
            stacklevel=2,
        )
        return tokenizer.decode(tokens[:max_tokens])


def embed_text(text: str) -> np.ndarray:
    """Truncate text to fit and embed it. Drop-in replacement for
    ``config.embedding_model_instance.embed(text)`` that never exceeds the
    model's context window."""
    with _embedding_lock:
        return config.embedding_model_instance.embed(truncate_for_embedding(text))


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    """Batch-embed multiple texts in a single call.

    Each text is truncated to fit the model's context window before embedding.
    For API-based models (``://`` in the model string) this uses
    ``embed_batch`` to collapse many network round-trips into one.
    For local models (SentenceTransformers) it falls back to sequential
    ``embed`` calls to avoid padding overhead.
    """
    if not texts:
        return []
    with _embedding_lock:
        truncated = [truncate_for_embedding(t) for t in texts]
        if config.is_local_embedding_model:
            return [config.embedding_model_instance.embed(t) for t in truncated]
        return config.embedding_model_instance.embed_batch(truncated)


def get_model_context_window(model_name: str) -> int:
    """Get the total context window size for a model (input + output tokens)."""
    try:
        model_info = get_model_info(model_name)
        context_window = model_info.get("max_input_tokens")
        # Handle case where key exists but value is None
        if context_window is None:
            print(
                f"Warning: max_input_tokens is None for {model_name}, using default 4096 tokens."
            )
            return 4096  # Conservative fallback
        return context_window
    except Exception as e:
        print(
            f"Warning: Could not get model info for {model_name}, using default 4096 tokens. Error: {e}"
        )
        return 4096  # Conservative fallback


def optimize_content_for_context_window(
    content: str, document_metadata: dict | None, model_name: str
) -> str:
    """
    Optimize content length to fit within model context window using binary search.

    Args:
        content: Original document content
        document_metadata: Optional metadata dictionary
        model_name: Model name for token counting

    Returns:
        Optimized content that fits within context window
    """
    if not content:
        return content

    # Get model context window
    context_window = get_model_context_window(model_name)

    # Reserve tokens for: system prompt, metadata, template overhead, and output
    # Conservative estimate: 2000 tokens for prompt + metadata + output buffer
    # TODO: Calculate Summary System Prompt Token Count Here
    reserved_tokens = 2000

    # Add metadata token cost if present
    if document_metadata:
        metadata_text = (
            f"<DOCUMENT_METADATA>\n\n{document_metadata}\n\n</DOCUMENT_METADATA>"
        )
        metadata_tokens = token_counter(
            messages=[{"role": "user", "content": metadata_text}], model=model_name
        )
        reserved_tokens += metadata_tokens

    available_tokens = context_window - reserved_tokens

    if available_tokens <= 100:  # Minimum viable content
        print(f"Warning: Very limited tokens available for content: {available_tokens}")
        return content[:500]  # Fallback to first 500 chars

    # Binary search to find optimal content length
    left, right = 0, len(content)
    optimal_length = 0

    while left <= right:
        mid = (left + right) // 2
        test_content = content[:mid]

        # Test token count for this content length
        test_document = f"<DOCUMENT_CONTENT>\n\n{test_content}\n\n</DOCUMENT_CONTENT>"
        test_tokens = token_counter(
            messages=[{"role": "user", "content": test_document}], model=model_name
        )

        if test_tokens <= available_tokens:
            optimal_length = mid
            left = mid + 1
        else:
            right = mid - 1

    optimized_content = (
        content[:optimal_length] if optimal_length > 0 else content[:500]
    )

    if optimal_length < len(content):
        print(
            f"Content optimized: {len(content)} -> {optimal_length} chars "
            f"to fit in {available_tokens} available tokens"
        )

    return optimized_content


async def create_document_chunks(content: str) -> list[Chunk]:
    """
    Create chunks from document content.

    Args:
        content: Document content to chunk

    Returns:
        List of Chunk objects with embeddings
    """
    chunk_texts = [c.text for c in config.chunker_instance.chunk(content)]
    chunk_embeddings = await asyncio.to_thread(embed_texts, chunk_texts)
    return [
        Chunk(content=text, embedding=emb)
        for text, emb in zip(chunk_texts, chunk_embeddings, strict=False)
    ]


async def convert_element_to_markdown(element) -> str:
    """
    Convert an Unstructured element to markdown format based on its category.

    Args:
        element: The Unstructured API element object

    Returns:
        str: Markdown formatted string
    """
    element_category = element.metadata["category"]
    content = element.page_content

    if not content:
        return ""

    markdown_mapping = {
        "Formula": lambda x: f"```math\n{x}\n```",
        "FigureCaption": lambda x: f"*Figure: {x}*",
        "NarrativeText": lambda x: f"{x}\n\n",
        "ListItem": lambda x: f"- {x}\n",
        "Title": lambda x: f"# {x}\n\n",
        "Address": lambda x: f"> {x}\n\n",
        "EmailAddress": lambda x: f"`{x}`",
        "Image": lambda x: f"![{x}]({x})",
        "PageBreak": lambda x: "\n---\n",
        "Table": lambda x: f"```html\n{element.metadata['text_as_html']}\n```",
        "Header": lambda x: f"## {x}\n\n",
        "Footer": lambda x: f"*{x}*\n\n",
        "CodeSnippet": lambda x: f"```\n{x}\n```",
        "PageNumber": lambda x: f"*Page {x}*\n\n",
        "UncategorizedText": lambda x: f"{x}\n\n",
    }

    converter = markdown_mapping.get(element_category, lambda x: x)
    return converter(content)


async def convert_document_to_markdown(elements):
    """
    Convert all document elements to markdown.

    Args:
        elements: List of Unstructured API elements

    Returns:
        str: Complete markdown document
    """
    markdown_parts = []

    for element in elements:
        markdown_text = await convert_element_to_markdown(element)
        if markdown_text:
            markdown_parts.append(markdown_text)

    return "".join(markdown_parts)


def generate_content_hash(content: str, search_space_id: int) -> str:
    """Generate SHA-256 hash for the given content combined with search space ID."""
    combined_data = f"{search_space_id}:{content}"
    return hashlib.sha256(combined_data.encode("utf-8")).hexdigest()


def generate_unique_identifier_hash(
    document_type: DocumentType,
    unique_identifier: str | int | float,
    search_space_id: int,
) -> str:
    """
    Generate SHA-256 hash for a unique document identifier from connector sources.

    This function creates a consistent hash based on the document type, its unique
    identifier from the source system, and the search space ID. This helps prevent
    duplicate documents when syncing from various connectors like Slack, Notion, Jira, etc.

    Args:
        document_type: The type of document (e.g., SLACK_CONNECTOR, NOTION_CONNECTOR)
        unique_identifier: The unique ID from the source system (e.g., message ID, page ID)
        search_space_id: The search space this document belongs to

    Returns:
        str: SHA-256 hash string representing the unique document identifier

    Example:
        >>> generate_unique_identifier_hash(
        ...     DocumentType.SLACK_CONNECTOR,
        ...     "1234567890.123456",
        ...     42
        ... )
        'a1b2c3d4e5f6...'
    """
    # Convert unique_identifier to string to handle different types
    identifier_str = str(unique_identifier)

    # Combine document type value, unique identifier, and search space ID
    combined_data = f"{document_type.value}:{identifier_str}:{search_space_id}"

    return hashlib.sha256(combined_data.encode("utf-8")).hexdigest()


# ===========================================================================
# Brain Pipeline — F1.5
# ===========================================================================
# process_document_content() es el punto de integración entre el task Celery
# de SurfSense y el pipeline de tres fases del Second Brain.
#
# Flujo:
#   task Celery
#     → DocumentProcessorFactory.is_supported(filename)?
#         SÍ → process_document_content()   ← esta función
#                  → Fase 1+2: extract()     (extractor + UniversalCleaner)
#                  → Fase 3:   preprocess()  (preprocesador semántico)
#                  → devuelve processed_text + metadata de calidad
#         NO → pipeline genérico SurfSense (EtlPipelineService)
# ===========================================================================


async def process_document_content(
    file_content: bytes,
    filename: str,
    search_space_id: int,
    metadata: dict | None = None,
    meta_service=None,
) -> dict:
    """
    Orquesta el pipeline de tres fases del Second Brain sobre un fichero.

    Punto de integración entre el task Celery de SurfSense y el pipeline Brain.
    Llamar solo cuando DocumentProcessorFactory.is_supported(filename) es True.

    Fases ejecutadas:
      Fase 1+2: extract()    — extractor específico + UniversalCleaner
      Fase 3:   preprocess() — preprocesador semántico por tipo de fichero

    El guardado en PostgreSQL + Qdrant se realiza en F2/F3.
    En F1 devuelve el processed_text y metadata para que el task lo persista.

    Args:
        file_content:    Bytes del fichero a procesar.
        filename:        Nombre original del fichero (con extensión).
        search_space_id: ID del search space (int, igual que SurfSense).
        metadata:        Metadata adicional a inyectar en los bloques.
        meta_service:    BrainMetadataService opcional para normalizar tags.
                         Si es None, se omite la normalización de vocabulario.

    Returns:
        Dict con:
          processed_text  — texto preprocesado con marcas ##/###/####
          blocks          — bloques limpios con metadata de calidad
          quality_meta    — avg_quality_score, has_pii, languages, normalized_tags
          filename        — nombre del fichero
          search_space_id — ID del search space propagado

    Raises:
        ValueError: si filename no tiene soporte en el pipeline Brain.
        RuntimeError: si la extracción falla y no hay fallback posible.
    """
    import os
    import tempfile

    from app.brain.processor_factory import DocumentProcessorFactory

    if not DocumentProcessorFactory.is_supported(filename):
        raise ValueError(
            f"[process_document_content] Extensión no soportada por el pipeline Brain: "
            f"{filename}. Usar EtlPipelineService como fallback."
        )

    logger.info(
        "[brain_pipeline] Iniciando pipeline 3 fases: file=%s space=%s bytes=%d",
        filename, search_space_id, len(file_content),
    )

    # Escribir bytes a fichero temporal para que el extractor pueda leerlo
    # Los extractores del Second Brain trabajan sobre rutas de fichero en disco.
    suffix = os.path.splitext(filename)[1].lower()
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, prefix="brain_"
        ) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        processor = DocumentProcessorFactory.get(filename)

        # ── Fase 1 + 2: extracción y limpieza universal ────────────────────
        blocks = processor.extract(
            file_path=tmp_path,
            search_space_id=search_space_id,
        )

        logger.info(
            "[brain_pipeline] Fase 1+2 OK: file=%s blocks=%d space=%s",
            filename, len(blocks), search_space_id,
        )

        # ── Fase 3: preprocesado semántico ──────────────────────────────
        processed_text = processor.preprocess(blocks)

        logger.info(
            "[brain_pipeline] Fase 3 OK: file=%s chars=%d space=%s",
            filename, len(processed_text), search_space_id,
        )

        # ── Metadata de calidad ────────────────────────────────────────
        quality_meta = processor.get_metadata_from_blocks(
            blocks=blocks,
            meta_service=meta_service,
            search_space_id=search_space_id,
        )

        logger.info(
            "[brain_pipeline] Pipeline 3 fases completado: file=%s "
            "blocks=%d chars=%d quality=%.2f space=%s",
            filename,
            quality_meta["total_blocks"],
            len(processed_text),
            quality_meta["avg_quality_score"],
            search_space_id,
        )

        return {
            "processed_text":  processed_text,
            "blocks":          blocks,
            "quality_meta":    quality_meta,
            "filename":        filename,
            "search_space_id": search_space_id,
        }

    except Exception as exc:
        logger.error(
            "[brain_pipeline] Error en pipeline 3 fases: file=%s space=%s error=%s",
            filename, search_space_id, exc, exc_info=True,
        )
        raise RuntimeError(
            f"[brain_pipeline] Fallo en pipeline Brain para '{filename}': {exc}"
        ) from exc

    finally:
        # Limpiar fichero temporal siempre, incluso si hay excepción
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as cleanup_exc:
                logger.warning(
                    "[brain_pipeline] No se pudo eliminar fichero temporal %s: %s",
                    tmp_path, cleanup_exc,
                )


async def process_document_from_text(
    text: str,
    filename: str,
    search_space_id: int,
    metadata: dict | None = None,
    meta_service=None,
) -> dict:
    """
    Orquesta el pipeline Brain para conectores API que entregan texto directo.

    Variante de process_document_content() para conectores como GitHub, Jira,
    Confluence o Slack que no tienen fichero en disco sino texto ya disponible.
    Usa extract_from_text() en lugar de extract() para evitar escribir a disco.

    Args:
        text:            Contenido textual del documento.
        filename:        Nombre virtual con extensión que identifica el tipo
                         (ej: "page.confluence", "ticket.jira_ticket").
        search_space_id: ID del search space.
        metadata:        Metadata adicional (source_url, connector_id, etc.).
        meta_service:    BrainMetadataService opcional para normalizar tags.

    Returns:
        Mismo formato que process_document_content().

    Raises:
        ValueError: si filename no tiene soporte en el pipeline Brain.
    """
    from app.brain.processor_factory import DocumentProcessorFactory

    if not DocumentProcessorFactory.is_supported(filename):
        raise ValueError(
            f"[process_document_from_text] Extensión no soportada: {filename}"
        )

    logger.info(
        "[brain_pipeline] Iniciando pipeline texto directo: file=%s space=%s chars=%d",
        filename, search_space_id, len(text),
    )

    processor = DocumentProcessorFactory.get(filename)

    # Fase 1+2: extracción desde texto (sin fichero en disco)
    blocks = processor.extract_from_text(
        text=text,
        search_space_id=search_space_id,
        metadata=metadata,
    )

    logger.info(
        "[brain_pipeline] Fase 1+2 (texto) OK: file=%s blocks=%d space=%s",
        filename, len(blocks), search_space_id,
    )

    # Fase 3: preprocesado semántico
    processed_text = processor.preprocess(blocks)

    logger.info(
        "[brain_pipeline] Fase 3 (texto) OK: file=%s chars=%d space=%s",
        filename, len(processed_text), search_space_id,
    )

    # Metadata de calidad
    quality_meta = processor.get_metadata_from_blocks(
        blocks=blocks,
        meta_service=meta_service,
        search_space_id=search_space_id,
    )

    logger.info(
        "[brain_pipeline] Pipeline texto directo completado: file=%s "
        "blocks=%d chars=%d quality=%.2f space=%s",
        filename,
        quality_meta["total_blocks"],
        len(processed_text),
        quality_meta["avg_quality_score"],
        search_space_id,
    )

    return {
        "processed_text":  processed_text,
        "blocks":          blocks,
        "quality_meta":    quality_meta,
        "filename":        filename,
        "search_space_id": search_space_id,
    }


# ===========================================================================
# Brain Pipeline — F3.5
# ===========================================================================
# run_brain_synthesis() es el punto de integración F3: recibe el resultado
# de process_document_content() (F1+F2) y ejecuta síntesis LLM + BrainWriter
# + IngestRouter de forma síncrona (Celery worker no es async).
#
# Flujo:
#   process_document_content() [async, F1+F2]
#     → run_brain_synthesis()  [síncrono, F3]
#           → SYNTHESIS_ENABLED=true  → DocumentSynthesizer.synthesize()
#           → SYNTHESIS_ENABLED=false → build_partial_passport() directo
#           → BrainWriter.write()     → /data/brain/{slug}.md
#           → IngestRouter.route()    → colecciones Qdrant
#
# Separación de concerns:
#   - process_document_content() permanece async para compatibilidad con
#     callers async (rutas FastAPI, tests).
#   - run_brain_synthesis() es síncrono para Celery (no usa await en ningún
#     punto — DocumentSynthesizer y IngestRouter son síncronos).
# ===========================================================================


def run_brain_synthesis(
    processed_text: str,
    blocks: list[dict],
    filename: str,
    search_space_id: int,
    quality_meta: dict | None = None,
    ingest_metadata: dict | None = None,
) -> dict:
    """
    Ejecuta F3 del pipeline Brain: síntesis semántica, persistencia y vectorización.

    Síncrono — diseñado para ejecutarse en un worker Celery. No usar await.

    Args:
        processed_text:  Texto preprocesado de F1+F2 (marcas ##/###/####).
        blocks:          Bloques limpios del extractor (F1+F2).
        filename:        Nombre original del fichero (con extensión).
        search_space_id: ID del search space (multi-tenancy).
        quality_meta:    Dict con avg_quality_score, has_pii, languages
                         (salida de get_metadata_from_blocks). Opcional.
        ingest_metadata: Dict de trazabilidad (ingest_origin, ingest_path, etc.).
                         Si es None se genera uno por defecto.

    Returns:
        Dict con:
          source           — slug del fichero (ej: "mi-informe")
          passport_path    — ruta del .md en disco (ej: "/data/brain/mi-informe.md")
          passport_md      — contenido completo del pasaporte generado
          embedding_scope  — colecciones Qdrant donde se vectorizó
          write_status     — "created" | "overwritten" | "exists_warning" | "empty"
          llm_ok           — True si la síntesis LLM se completó correctamente

    Raises:
        RuntimeError: si la síntesis y el fallback mínimo fallan.
    """
    import os
    import re
    from datetime import datetime, timezone

    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.passport_builder import build_partial_passport
    from app.brain.writer import BrainWriter
    from app.brain.qdrant_manager import QdrantManager
    from app.brain.ingest_router import IngestRouter

    # ── Configuración desde entorno (cero hardcode) ────────────────────────────
    synthesis_enabled = os.getenv("SYNTHESIS_ENABLED", "true").lower() == "true"

    # Derivar extensión y slug del nombre de fichero
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    # Slug: minúsculas, guiones, máx 80 chars — igual que BrainWriter._slugify()
    _name = os.path.splitext(os.path.basename(filename))[0]
    source = re.sub(r"[^\w\s-]", "", _name.lower())
    source = re.sub(r"[\s_]+", "-", source).strip("-")[:80] or "unnamed"

    # Metadata de ingesta por defecto
    if ingest_metadata is None:
        ingest_metadata = {
            "ingest_origin": "file_upload",
            "ingest_path":   filename,
        }

    quality_meta = quality_meta or {}

    logger.info(
        "[brain_synthesis] Iniciando F3: file=%s source=%s space=%s "
        "synthesis_enabled=%s blocks=%d chars=%d",
        filename, source, search_space_id,
        synthesis_enabled, len(blocks), len(processed_text),
    )

    # ── PASO 1: Síntesis LLM o modo raw ───────────────────────────────────────
    passport_md = ""
    llm_ok = False

    if synthesis_enabled and blocks:
        try:
            synth = DocumentSynthesizer()
            result = synth.synthesize(
                source=source,
                file_type=ext,
                full_text=processed_text,
                blocks=blocks,
                metadata=quality_meta,
                provider=None,   # DEFAULT_PROVIDER del .env (LLM_PROVIDER)
                model=None,      # SYNTHESIS_MODEL del .env
                ingest_metadata=ingest_metadata,
            )
            passport_md = result.get("md_content", "")
            llm_ok = result.get("llm_ok", False)
            logger.info(
                "[brain_synthesis] Síntesis LLM completada: file=%s llm_ok=%s "
                "passport_chars=%d tags=%d entities=%d",
                filename, llm_ok,
                len(passport_md),
                len(result.get("tags", [])),
                len(result.get("entities", [])),
            )
        except Exception as exc:
            logger.error(
                "[brain_synthesis] Error en síntesis LLM para '%s': %s. "
                "Usando pasaporte parcial programático.",
                filename, exc, exc_info=True,
            )
    else:
        if not synthesis_enabled:
            logger.info(
                "[brain_synthesis] SYNTHESIS_ENABLED=false — "
                "generando pasaporte parcial sin LLM para '%s'.", filename,
            )
        else:
            logger.warning(
                "[brain_synthesis] Sin bloques para '%s' — "
                "generando pasaporte parcial sin LLM.", filename,
            )

    # Fallback al pasaporte parcial determinista si LLM no produjo resultado
    if not passport_md or not passport_md.strip():
        try:
            partial = build_partial_passport(
                source=source,
                file_type=ext,
                blocks=blocks,
                full_text=processed_text,
                metadata=quality_meta,
                source_type=ingest_metadata.get("ingest_origin", "upload"),
                source_location=ingest_metadata.get("ingest_path", ""),
            )
            passport_md = partial["passport_partial"]
            logger.info(
                "[brain_synthesis] Pasaporte parcial generado para '%s' "
                "(%d chars).", filename, len(passport_md),
            )
        except Exception as exc:
            logger.error(
                "[brain_synthesis] Error generando pasaporte parcial para '%s': %s.",
                filename, exc, exc_info=True,
            )
            # Pasaporte mínimo de último recurso
            passport_md = f"---\nid: kb_{source}\ntitle: \"{source}\"\n---\n# {filename}\n\nSíntesis no disponible.\n"
            logger.warning(
                "[brain_synthesis] Usando pasaporte mínimo de emergencia para '%s'.",
                filename,
            )

    # ── PASO 2: Calcular embedding_scope ──────────────────────────────────────
    # Si hay bloques de código → añadir colección code
    has_code = any(b.get("content_type") == "code" for b in blocks)
    if llm_ok:
        embedding_scope = ["brain", "knowledge", "code"] if has_code else ["brain", "knowledge"]
    else:
        # Pasaporte parcial/mínimo: no entra en brain (incompleto)
        embedding_scope = ["knowledge", "code"] if has_code else ["knowledge"]

    logger.debug(
        "[brain_synthesis] embedding_scope=%s has_code=%s llm_ok=%s",
        embedding_scope, has_code, llm_ok,
    )

    # ── PASO 3: Persistir pasaporte en disco ──────────────────────────────────
    writer = BrainWriter()   # usa BRAIN_DIR del .env
    write_result = writer.write(source=source, md_content=passport_md, overwrite=True)
    passport_path = write_result.get("path", "")
    write_status  = write_result.get("status", "unknown")

    logger.info(
        "[brain_synthesis] BrainWriter: file=%s source=%s "
        "status=%s path=%s",
        filename, source, write_status, passport_path,
    )

    if write_status == "empty":
        logger.warning(
            "[brain_synthesis] BrainWriter devolvió 'empty' para '%s' — "
            "el pasaporte no se escribió en disco.", filename,
        )

    # ── PASO 4: Vectorizar en Qdrant ──────────────────────────────────────────
    try:
        qdrant_mgr = QdrantManager.get_instance()
        router = IngestRouter(qdrant_client=qdrant_mgr.client)
        route_result = router.route(
            md_content=passport_md,
            blocks=blocks,
            source=source,
            search_space_id=str(search_space_id),
            ingest_metadata=ingest_metadata,
        )
        logger.info(
            "[brain_synthesis] IngestRouter completado: file=%s space=%s "
            "brain=%d knowledge=%d code=%d",
            filename, search_space_id,
            route_result.get("brain", {}).get("chunks_created", 0),
            route_result.get("knowledge", {}).get("chunks_created", 0),
            route_result.get("code", {}).get("chunks_created", 0),
        )
    except Exception as exc:
        logger.error(
            "[brain_synthesis] Error en IngestRouter para '%s' space=%s: %s",
            filename, search_space_id, exc, exc_info=True,
        )
        # No re-raise: el pasaporte ya está en disco; la vectorización
        # puede reintentarse en F3.7 con el mecanismo de reintento de Celery.

    logger.info(
        "[brain_synthesis] F3 completado: file=%s source=%s "
        "llm_ok=%s write_status=%s passport_path=%s embedding_scope=%s",
        filename, source, llm_ok, write_status, passport_path, embedding_scope,
    )

    return {
        "source":          source,
        "passport_path":   passport_path,
        "passport_md":     passport_md,
        "embedding_scope": embedding_scope,
        "write_status":    write_status,
        "llm_ok":          llm_ok,
    }
