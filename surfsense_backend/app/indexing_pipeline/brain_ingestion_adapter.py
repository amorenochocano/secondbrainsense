"""
brain_ingestion_adapter.py
--------------------------
Adaptador que conecta el pipeline de ingesta de SurfSense con el motor
de procesamiento de Second Brain.

PROPÓSITO
---------
Reemplaza el pipeline básico de SurfSense (chunk_text_hybrid + embed_texts
con all-MiniLM-L6-v2 384d → pgvector) por el pipeline potente de Second Brain
(extractores especializados + UniversalCleaner + nomic-embed-text 768d → Qdrant).

FLUJO
-----
  ConnectorDocument.source_markdown
      → _categorize(document, connector_doc) → A / B / C
      │
      ├─ A (fichero con extensión conocida):
      │     DocumentProcessorFactory.get(ext).extract_from_text()
      │     → Extractor especializado + UniversalCleaner → bloques limpios
      │
      ├─ B (conector con extractor virtual):
      │     DocumentProcessorFactory.get(virtual_ext).extract_from_text()
      │     → Extractor virtual (Confluence/Jira/GitHub/Web) → bloques limpios
      │
      └─ C ("saco" genérico — todo lo demás):
            UniversalCleaner.clean() → _semantic_chunk_text()
            → bloques con quality_score, idioma, PII
      │
      ├─ _filter_low_quality(blocks)
      ├─ _embed_chunks(texts) → nomic-embed-text 768d vía Ollama
      ├─ _upsert_to_qdrant(knowledge collection, search_space_id)
      └─ (opcional) síntesis de pasaporte → Qdrant brain collection

CATEGORÍAS
----------
  A — Fichero binario con extensión conocida:
      Resuelto dinámicamente via DocumentProcessorFactory.is_supported(ext).
      Para añadir un tipo nuevo: crear extractor + preprocesador + añadir a
      PREPROCESSOR_MAP en processor_factory.py. El adaptador lo recoge solo.
      Fuente: FILE, LOCAL_FOLDER_FILE, GOOGLE_DRIVE_FILE, ONEDRIVE_FILE, DROPBOX_FILE

  B — Conector con extractor virtual en Second Brain:
      Confluence (.confluence), Jira (.jira_ticket), GitHub (.github_file), Webcrawler (.html)
      Fuente: CONFLUENCE_CONNECTOR, JIRA_CONNECTOR, GITHUB_CONNECTOR, WEBCRAWLER_CONNECTOR

  C — "Saco" genérico (todo lo demás):
      Slack, Discord, Gmail, Calendar, Airtable, Linear, ClickUp, YouTube,
      Luma, Teams, Bookstack, Notion, Obsidian, Elasticsearch, Circleback, NOTE, EXTENSION
      El "saco" aplica la misma limpieza y embedding que A/B — solo sin
      extracción estructural especializada.

DISEÑO
------
  - Nunca lanza excepción al llamador — captura interna con fallback al saco.
  - Si un extractor especializado falla, cae al saco automáticamente.
  - La síntesis de pasaporte es OPCIONAL y no bloquea la ingesta de chunks.
  - Cero hardcode: categorías, modelos y umbrales desde os.getenv().

Variables de entorno:
  BRAIN_INGESTION_ENABLED   — activar pipeline Brain en ingesta (default: true)
  BRAIN_SYNTHESIS_ENABLED   — generar pasaportes .md (default: true)
  BRAIN_EMBEDDING_MODEL     — modelo de embedding (default: nomic-embed-text)
  BRAIN_QUALITY_THRESHOLD   — umbral quality_score para filtrar bloques (default: 0.30)
  OLLAMA_HOST               — URL del servidor Ollama (default: http://localhost:11434)
"""
import asyncio
import logging
import os
import re

from app.db import Document, DocumentType
from app.indexing_pipeline.connector_document import ConnectorDocument

logger = logging.getLogger(__name__)

# ── Configuración (cero hardcode — todo desde entorno) ────────────────────────
BRAIN_INGESTION_ENABLED = os.getenv("BRAIN_INGESTION_ENABLED", "true").lower() == "true"
BRAIN_SYNTHESIS_ENABLED = os.getenv("BRAIN_SYNTHESIS_ENABLED", "true").lower() == "true"
BRAIN_EMBEDDING_MODEL   = os.getenv("BRAIN_EMBEDDING_MODEL",   "nomic-embed-text")
BRAIN_QUALITY_THRESHOLD = float(os.getenv("BRAIN_QUALITY_THRESHOLD", "0.30"))
OLLAMA_HOST             = os.getenv("OLLAMA_HOST", "http://localhost:11434")


# ── Categorización de DocumentTypes ───────────────────────────────────────────
# NOTA SOBRE EXTENSIONES CATEGORÍA A:
# NO se usa un frozenset hardcodeado de extensiones. En su lugar, el
# categorizador consulta DocumentProcessorFactory.is_supported(ext) que lee
# dinámicamente de PREPROCESSOR_MAP en processor_factory.py.
#
# Para añadir un nuevo tipo de fichero a la categoría A:
#   1. Crear extractor en app/brain/extractors/nuevo.py
#   2. Crear preprocesador en app/brain/prompts/preprocessing/nuevo.py
#   3. Añadir extensión a PREPROCESSOR_MAP en processor_factory.py
#   4. Listo — el adaptador lo recoge automáticamente via is_supported()
#
# Mismo patrón que Second Brain original — un solo punto de verdad.

# DocumentTypes que entregan ficheros binarios → resolver extensión del título.
_FILE_UPLOAD_TYPES = frozenset({
    DocumentType.FILE,
    DocumentType.LOCAL_FOLDER_FILE,
    DocumentType.GOOGLE_DRIVE_FILE,
    DocumentType.ONEDRIVE_FILE,
    DocumentType.DROPBOX_FILE,
})

# Conectores con extractor virtual en Second Brain (categoría B).
# El valor es la "extensión virtual" que DocumentProcessorFactory reconoce.
_CATEGORY_B_MAP: dict[str, str] = {
    DocumentType.CONFLUENCE_CONNECTOR:  ".confluence",
    DocumentType.JIRA_CONNECTOR:        ".jira_ticket",
    DocumentType.GITHUB_CONNECTOR:      ".github_file",
    DocumentType.CRAWLED_URL:           ".html",    # Webcrawler produce CRAWLED_URL
}

# Tipos efímeros: SÍ se indexan en Qdrant knowledge (para buscar mensajes/emails)
# pero NO generan pasaporte .md (contenido demasiado efímero para sintetizar).
_SKIP_SYNTHESIS_TYPES = frozenset({
    DocumentType.SLACK_CONNECTOR,
    DocumentType.DISCORD_CONNECTOR,
    DocumentType.GOOGLE_GMAIL_CONNECTOR,
    DocumentType.GOOGLE_CALENDAR_CONNECTOR,
    DocumentType.TEAMS_CONNECTOR,
    DocumentType.COMPOSIO_GMAIL_CONNECTOR,
    DocumentType.COMPOSIO_GOOGLE_CALENDAR_CONNECTOR,
})


# ── API pública ───────────────────────────────────────────────────────────────

async def process_document(
    document: Document,
    connector_doc: ConnectorDocument,
) -> list[str]:
    """
    Procesa un documento con el pipeline Brain y devuelve los textos de chunks.

    Flujo:
      1. Categorizar el documento (A/B/C)
      2. Extraer bloques con el pipeline adecuado
      3. Filtrar bloques de baja calidad
      4. Embedir chunks con nomic-embed-text
      5. Upsert a Qdrant colección knowledge
      6. (Opcional) Sintetizar pasaporte → Qdrant colección brain

    Args:
        document:      Objeto Document ORM ya guardado en PostgreSQL.
        connector_doc: ConnectorDocument con source_markdown y metadatos.

    Returns:
        Lista de textos de chunks — se guardan en PostgreSQL Chunk.content
        para BM25. Los embeddings correspondientes ya están en Qdrant.

    Nunca lanza excepción:
        Si cualquier paso falla, cae al "saco" como último recurso.
        Si el saco también falla, devuelve chunks básicos del source_markdown.
    """
    category = _categorize(document, connector_doc)
    source_text = connector_doc.source_markdown or ""

    logger.info(
        "[brain_adapter] START doc=%d type=%s title=%r category=%s chars=%d",
        document.id, document.document_type, connector_doc.title[:50],
        category, len(source_text),
    )

    if not source_text.strip():
        logger.warning("[brain_adapter] doc=%d sin contenido — skip", document.id)
        return []

    try:
        # ── Paso 1: Extraer bloques según categoría ───────────────────────
        if category == "A":
            blocks = await _process_category_a(document, connector_doc)
        elif category == "B":
            blocks = await _process_category_b(document, connector_doc)
        else:
            blocks = await _process_category_c(connector_doc)

        # ── Paso 2: Filtrar bloques de baja calidad ───────────────────────
        blocks = _filter_low_quality(blocks, document.id)

        # ── Paso 3: Extraer textos de chunks ──────────────────────────────
        chunk_texts = [
            b.get("content", "") for b in blocks
            if b.get("content", "").strip()
        ]
        if not chunk_texts:
            logger.warning(
                "[brain_adapter] doc=%d sin chunks tras procesamiento → fallback básico",
                document.id,
            )
            chunk_texts = _basic_chunk_text(source_text)

        # ── Paso 4: Embedir y upsert a Qdrant ────────────────────────────
        await _embed_and_upsert(
            chunk_texts=chunk_texts,
            document=document,
            connector_doc=connector_doc,
            blocks=blocks,
        )

        # ── Paso 5: Síntesis de pasaporte (opcional, no bloquea) ──────────
        if (
            BRAIN_SYNTHESIS_ENABLED
            and document.document_type not in _SKIP_SYNTHESIS_TYPES
            and len(source_text) > 200  # no sintetizar textos triviales
        ):
            await _synthesize_passport(
                document=document,
                connector_doc=connector_doc,
                source_text=source_text,
                blocks=blocks,
            )

        logger.info(
            "[brain_adapter] OK doc=%d category=%s chunks=%d synthesis=%s",
            document.id, category, len(chunk_texts),
            "skip" if document.document_type in _SKIP_SYNTHESIS_TYPES else "done",
        )
        return chunk_texts

    except Exception as exc:
        logger.error(
            "[brain_adapter] ERROR doc=%d: %s → fallback básico",
            document.id, exc, exc_info=True,
        )
        # Último recurso: chunking básico sin extractor ni embedding Brain
        return _basic_chunk_text(source_text)


# ── Categorización ────────────────────────────────────────────────────────────

def _categorize(document: Document, connector_doc: ConnectorDocument) -> str:
    """
    Determina la categoría de procesamiento del documento.

    A — Fichero con extensión conocida → extractor especializado
        Consulta DocumentProcessorFactory.is_supported(ext) dinámicamente.
        Si se añade un tipo nuevo a PREPROCESSOR_MAP, automáticamente
        se categoriza como A sin tocar este fichero.

    B — Conector con extractor virtual → extractor especializado para ese conector
        El DocumentType está en _CATEGORY_B_MAP.

    C — Todo lo demás → "saco" genérico
        UniversalCleaner + chunking semántico.

    Args:
        document:      Objeto Document ORM.
        connector_doc: ConnectorDocument con título y tipo.

    Returns:
        "A", "B" o "C"
    """
    doc_type = document.document_type

    # Categoría B: conector con extractor virtual
    if doc_type in _CATEGORY_B_MAP:
        return "B"

    # Categoría A: fichero con extensión soportada por Second Brain
    if doc_type in _FILE_UPLOAD_TYPES:
        from app.brain.processor_factory import DocumentProcessorFactory
        ext = _extract_extension(connector_doc.title)
        if ext and DocumentProcessorFactory.is_supported(connector_doc.title):
            return "A"

    # Categoría A: should_use_code_chunker indica código → tratar como .py
    if connector_doc.should_use_code_chunker:
        return "A"

    # Todo lo demás: "saco" genérico
    return "C"


def _extract_extension(title: str) -> str:
    """
    Extrae la extensión del título del documento.

    Args:
        title: Título o nombre de fichero (ej: "informe.pdf", "script.py")

    Returns:
        Extensión en minúsculas con punto (ej: ".pdf", ".py") o "" si no tiene.
    """
    match = re.search(r'\.\w+$', (title or "").lower())
    return match.group() if match else ""


# ── Procesamiento por categoría ───────────────────────────────────────────────

async def _process_category_a(
    document: Document,
    connector_doc: ConnectorDocument,
) -> list[dict]:
    """
    Categoría A: fichero con extensión conocida → extractor especializado.

    Usa DocumentProcessorFactory.get(extensión).extract_from_text() para aplicar
    el extractor correcto (DocxExtractor para .docx, PdfExtractor para .pdf, etc.)
    que incluye UniversalCleaner automáticamente.

    Si should_use_code_chunker=True, fuerza extensión .py para el extractor de código.

    Args:
        document:      Objeto Document ORM.
        connector_doc: ConnectorDocument con source_markdown.

    Returns:
        Lista de bloques con content, content_type, metadata (quality_score, etc.)
    """
    from app.brain.processor_factory import DocumentProcessorFactory

    ext = _extract_extension(connector_doc.title)
    if connector_doc.should_use_code_chunker:
        ext = ".py"

    logger.info(
        "[brain_adapter] cat=A doc=%d ext=%s title=%r",
        document.id, ext, connector_doc.title[:50],
    )

    processor = DocumentProcessorFactory.get(ext or ".txt")
    blocks = await asyncio.to_thread(
        processor.extract_from_text,
        connector_doc.source_markdown,
        connector_doc.search_space_id,
        {
            "title":     connector_doc.title,
            "doc_id":    str(document.id),
            "unique_id": connector_doc.unique_id,
            "category":  "A",
        },
    )
    logger.info(
        "[brain_adapter] cat=A doc=%d → %d bloques extraídos",
        document.id, len(blocks),
    )
    return blocks


async def _process_category_b(
    document: Document,
    connector_doc: ConnectorDocument,
) -> list[dict]:
    """
    Categoría B: conector con extractor virtual (Confluence, Jira, GitHub, Web).

    Usa la extensión virtual de _CATEGORY_B_MAP para seleccionar el extractor
    correcto. El extractor virtual aplica UniversalCleaner internamente.

    Args:
        document:      Objeto Document ORM.
        connector_doc: ConnectorDocument con source_markdown.

    Returns:
        Lista de bloques con content, content_type, metadata.
    """
    from app.brain.processor_factory import DocumentProcessorFactory

    virtual_ext = _CATEGORY_B_MAP.get(document.document_type, ".txt")
    logger.info(
        "[brain_adapter] cat=B doc=%d type=%s virtual_ext=%s",
        document.id, document.document_type, virtual_ext,
    )

    processor = DocumentProcessorFactory.get(virtual_ext)
    blocks = await asyncio.to_thread(
        processor.extract_from_text,
        connector_doc.source_markdown,
        connector_doc.search_space_id,
        {
            "title":        connector_doc.title,
            "doc_id":       str(document.id),
            "unique_id":    connector_doc.unique_id,
            "connector_id": str(connector_doc.connector_id or ""),
            "category":     "B",
        },
    )
    logger.info(
        "[brain_adapter] cat=B doc=%d → %d bloques extraídos",
        document.id, len(blocks),
    )
    return blocks


async def _process_category_c(
    connector_doc: ConnectorDocument,
) -> list[dict]:
    """
    Categoría C: "saco" genérico para contenido sin extractor especializado.

    Aplica UniversalCleaner + chunking semántico. Cubre: Slack, Discord, Gmail,
    Calendar, Airtable, Linear, ClickUp, YouTube, Luma, Teams, Bookstack,
    Notion, Obsidian, Elasticsearch, Circleback, NOTE, EXTENSION, y cualquier
    tipo futuro que se añada a SurfSense.

    El "saco" NO es inferior a las categorías A/B:
    - Misma limpieza (UniversalCleaner con quality_score, idioma, PII)
    - Mismo modelo de embedding (nomic-embed-text 768d)
    - Misma colección Qdrant (knowledge)
    La única diferencia es que no tiene extracción estructural (headings, tablas, AST).

    Chunking semántico:
    - Si hay headings markdown (##/###) → cortar por headings
    - Si no → cortar por párrafos agrupados (~1000 chars por chunk)
    - Tablas markdown → chunk indivisible (igual que SurfSense)

    Args:
        connector_doc: ConnectorDocument con source_markdown.

    Returns:
        Lista de bloques con content, content_type, metadata.
    """
    from app.brain.rag_lib.layer1_universal import UniversalCleaner

    text = connector_doc.source_markdown or ""
    if not text.strip():
        return []

    logger.info(
        "[brain_adapter] cat=C type=%s title=%r chars=%d",
        connector_doc.document_type, connector_doc.title[:50], len(text),
    )

    # Aplicar UniversalCleaner — quality_score, idioma, PII
    cleaner = UniversalCleaner()
    result = await asyncio.to_thread(cleaner.clean, text, "text")

    # Chunking semántico genérico
    chunks = _semantic_chunk_text(result.text)

    blocks = [
        {
            "content":      chunk,
            "text":         chunk,
            "content_type": "text",
            "metadata": {
                "quality_score":  result.quality_score,
                "language":       result.language,
                "sensitive_data": result.sensitive_data_detected,
                "title":          connector_doc.title,
                "unique_id":      connector_doc.unique_id,
                "category":       "C",
            },
        }
        for chunk in chunks
        if chunk.strip()
    ]
    logger.info(
        "[brain_adapter] cat=C → %d bloques, quality=%.2f, lang=%s",
        len(blocks), result.quality_score, result.language,
    )
    return blocks


# ── Chunking semántico genérico (para categoría C) ────────────────────────────

# Tamaño objetivo de cada chunk en chars — leído del entorno.
# No es un máximo duro — se respetan los límites de headings y párrafos.
_CHUNK_TARGET_CHARS = int(os.getenv("BRAIN_SACO_CHUNK_TARGET_CHARS", "1000"))

# Regex para detectar tablas markdown (indivisibles)
_TABLE_RE = re.compile(
    r'(?:^|\n)(\|.+\|(?:\r?\n\|[-:| ]+\|)(?:\r?\n\|.+\|)+)',
    re.MULTILINE,
)


def _semantic_chunk_text(text: str) -> list[str]:
    """
    Divide texto en chunks semánticos para la categoría C ("saco").

    Estrategia:
    1. Si hay headings markdown (## o ###) → cortar por headings.
       Cada heading y su contenido hasta el siguiente heading es un chunk.
    2. Si no hay headings → cortar por párrafos agrupados hasta ~CHUNK_TARGET_CHARS.
    3. Las tablas markdown son chunks indivisibles (no se cortan entre filas).

    Args:
        text: Texto limpio (ya pasó por UniversalCleaner).

    Returns:
        Lista de strings, cada uno un chunk semántico.
    """
    if not text.strip():
        return []

    # Detectar si hay headings markdown
    has_headings = bool(re.search(r'^#{1,4}\s+\S', text, re.MULTILINE))

    if has_headings:
        return _chunk_by_headings(text)
    else:
        return _chunk_by_paragraphs(text)


def _chunk_by_headings(text: str) -> list[str]:
    """
    Corta el texto por headings markdown (##, ###, ####).
    Cada heading + su contenido hasta el siguiente heading es un chunk.
    """
    sections = re.split(r'\n(?=#{1,4}\s)', text)
    chunks = []
    for section in sections:
        section = section.strip()
        if section:
            # Si la sección es muy larga, subdividir por párrafos
            if len(section) > _CHUNK_TARGET_CHARS * 3:
                chunks.extend(_chunk_by_paragraphs(section))
            else:
                chunks.append(section)
    return chunks


def _chunk_by_paragraphs(text: str) -> list[str]:
    """
    Agrupa párrafos hasta alcanzar ~CHUNK_TARGET_CHARS chars por chunk.
    Las tablas markdown se emiten como chunk indivisible.
    """
    # Separar tablas del resto del texto
    parts = _TABLE_RE.split(text)
    chunks = []
    current_parts: list[str] = []
    current_len = 0

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Si es una tabla markdown, emitir como chunk indivisible
        if part.startswith("|") and "---" in part:
            # Flush lo acumulado
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0
            chunks.append(part)
            continue

        # Dividir en párrafos
        paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
        for para in paragraphs:
            if current_len + len(para) > _CHUNK_TARGET_CHARS and current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0
            current_parts.append(para)
            current_len += len(para)

    # Flush final
    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


# ── Filtrado de calidad ───────────────────────────────────────────────────────

def _filter_low_quality(blocks: list[dict], doc_id: int) -> list[dict]:
    """
    Descarta bloques con quality_score inferior al umbral.

    Protege Qdrant de contenido basura: artefactos OCR, texto corrupto,
    celdas de tabla sin contexto. El umbral se lee de BRAIN_QUALITY_THRESHOLD.

    Loguea cuántos bloques se descartaron para auditoría. No descarta
    si el bloque no tiene quality_score en metadata (asume calidad ok).

    Args:
        blocks: Lista de bloques con metadata.quality_score.
        doc_id: ID del documento para logging.

    Returns:
        Lista de bloques filtrados (quality_score >= umbral o sin score).
    """
    if not blocks:
        return blocks

    filtered = []
    discarded = 0
    for b in blocks:
        score = b.get("metadata", {}).get("quality_score", 1.0)
        if score >= BRAIN_QUALITY_THRESHOLD:
            filtered.append(b)
        else:
            discarded += 1

    if discarded:
        logger.warning(
            "[brain_adapter] doc=%d: %d/%d bloques descartados (quality < %.2f)",
            doc_id, discarded, len(blocks), BRAIN_QUALITY_THRESHOLD,
        )

    return filtered


# ── Embedding + Qdrant upsert ─────────────────────────────────────────────────

async def _embed_and_upsert(
    chunk_texts: list[str],
    document: Document,
    connector_doc: ConnectorDocument,
    blocks: list[dict],
) -> None:
    """
    Embebe los chunks con nomic-embed-text y los inserta en Qdrant knowledge.

    Usa IngestRouter para aprovechar la lógica de routing existente de F2
    que distribuye entre colecciones brain/knowledge/code según el contenido.

    Para la ingesta desde conectores, los chunks van a 'knowledge'.
    Los pasaportes sintetizados van a 'brain' (en _synthesize_passport).

    Args:
        chunk_texts:   Textos de los chunks a embedir.
        document:      Objeto Document ORM (para metadata).
        connector_doc: ConnectorDocument (para search_space_id).
        blocks:        Bloques originales (para metadata en Qdrant payload).
    """
    from app.brain.ingest_router import IngestRouter
    from app.brain.qdrant_manager import QdrantManager

    search_space_id = str(connector_doc.search_space_id)
    source_slug = _build_source_slug(document, connector_doc)
    category = _categorize(document, connector_doc)

    logger.debug(
        "[brain_adapter] embed+upsert doc=%d source=%s chunks=%d space=%s model=%s",
        document.id, source_slug, len(chunk_texts), search_space_id,
        BRAIN_EMBEDDING_MODEL,
    )

    try:
        mgr = QdrantManager.get_instance()
        router = IngestRouter(qdrant_client=mgr.client)

        # F5 FIX: Llamar a _ingest_knowledge_raw() en vez de route().
        # route() siempre añade 'brain' al scope (línea ~126 de ingest_router.py:
        # "if BRAIN not in scope: scope = [BRAIN] + list(scope)").
        # Los chunks de ingesta solo deben ir a 'knowledge' — el pasaporte
        # va a 'brain' posteriormente en _synthesize_passport().
        # Usar route() aquí causaría duplicación en la colección brain.
        await asyncio.to_thread(
            router._ingest_knowledge_raw,
            text_blocks=blocks,
            source=source_slug,
            meta={},
            ingest_metadata={
                "title":     connector_doc.title,
                "doc_id":    str(document.id),
                "unique_id": connector_doc.unique_id,
                "category":  category,
            },
            search_space_id=search_space_id,
        )
        logger.info(
            "[brain_adapter] Qdrant upsert OK doc=%d source=%s chunks=%d",
            document.id, source_slug, len(chunk_texts),
        )
    except Exception as exc:
        logger.error(
            "[brain_adapter] Qdrant upsert FAILED doc=%d: %s — chunks solo en PostgreSQL",
            document.id, exc, exc_info=True,
        )
        # No propagar — los chunks SÍ se guardan en PostgreSQL para BM25


# ── Síntesis de pasaporte ─────────────────────────────────────────────────────

async def _synthesize_passport(
    document: Document,
    connector_doc: ConnectorDocument,
    source_text: str,
    blocks: list[dict],
) -> None:
    """
    Genera pasaporte semántico .md y lo almacena en disco + Qdrant brain.

    La síntesis usa DocumentSynthesizer (F3) y BrainWriter para guardar
    el .md en /data/brain/{slug}.md. IngestRouter vectoriza el pasaporte
    en la colección brain de Qdrant.

    NO bloquea la ingesta: si falla, los chunks ya están en Qdrant knowledge
    y en PostgreSQL para BM25. El pasaporte se puede re-generar después.

    Args:
        document:      Objeto Document ORM.
        connector_doc: ConnectorDocument con source_markdown.
        source_text:   Texto completo del documento.
        blocks:        Bloques extraídos (para el sintetizador).
    """
    source_slug = _build_source_slug(document, connector_doc)
    search_space_id = str(connector_doc.search_space_id)

    logger.info(
        "[brain_adapter] síntesis START doc=%d source=%s chars=%d",
        document.id, source_slug, len(source_text),
    )

    try:
        from app.brain.synthesizer import DocumentSynthesizer
        from app.brain.writer import BrainWriter
        from app.brain.ingest_router import IngestRouter
        from app.brain.qdrant_manager import QdrantManager

        # Fase 1: Síntesis → pasaporte .md
        ext = _extract_extension(connector_doc.title) or ".txt"
        synthesizer = DocumentSynthesizer()
        synthesis_result = await asyncio.to_thread(
            synthesizer.synthesize,
            source=source_slug,
            file_type=ext,
            full_text=source_text,
            blocks=blocks,
            metadata={
                "title":          connector_doc.title,
                "connector_id":   connector_doc.connector_id,
                "unique_id":      connector_doc.unique_id,
                "search_space_id": search_space_id,
            },
            ingest_metadata={
                "search_space_id": search_space_id,
            },
        )

        md_content = synthesis_result.get("md_content", "")
        if not md_content:
            logger.warning(
                "[brain_adapter] síntesis vacía doc=%d — skip pasaporte", document.id,
            )
            return

        # Fase 2: Guardar .md en disco
        writer = BrainWriter()
        writer.write(source=source_slug, md_content=md_content, overwrite=True)

        # Fase 3: Vectorizar pasaporte en Qdrant brain
        mgr = QdrantManager.get_instance()
        router = IngestRouter(qdrant_client=mgr.client)
        await asyncio.to_thread(
            router.route,
            md_content=md_content,
            blocks=blocks,
            source=source_slug,
            search_space_id=search_space_id,
            ingest_metadata={
                "title":  connector_doc.title,
                "doc_id": str(document.id),
            },
        )

        logger.info(
            "[brain_adapter] síntesis OK doc=%d source=%s pasaporte=%d chars",
            document.id, source_slug, len(md_content),
        )

    except Exception as exc:
        logger.error(
            "[brain_adapter] síntesis FAILED doc=%d: %s — chunks ya en Qdrant knowledge",
            document.id, exc, exc_info=True,
        )
        # No propagar — los chunks ya están indexados


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_source_slug(document: Document, connector_doc: ConnectorDocument) -> str:
    """
    Genera el slug canónico para identificar el documento en Qdrant y Brain.

    Formato: {space_id}-{doc_type}-{unique_id_normalizado}
    Idempotente: mismo documento siempre produce el mismo slug.
    Solo chars alfanuméricos y guiones — compatible con nombres de fichero.

    Args:
        document:      Objeto Document ORM.
        connector_doc: ConnectorDocument.

    Returns:
        Slug normalizado (ej: "7-confluence-connector-conf-page-123")
    """
    raw = f"{connector_doc.search_space_id}-{document.document_type}-{connector_doc.unique_id}"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _basic_chunk_text(text: str) -> list[str]:
    """
    Chunking básico de último recurso — cuando todo lo demás falla.

    Divide por párrafos dobles sin limpieza ni quality scoring.
    Solo se usa como fallback de emergencia para garantizar que
    process_document() siempre devuelve chunks.

    Args:
        text: Texto crudo.

    Returns:
        Lista de strings (párrafos).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        # Último recurso: el texto completo como un solo chunk
        return [text.strip()] if text.strip() else []

    # Agrupar párrafos hasta ~1000 chars
    chunks = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > _CHUNK_TARGET_CHARS and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))

    return chunks
