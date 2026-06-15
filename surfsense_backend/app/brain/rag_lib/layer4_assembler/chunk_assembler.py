"""
chunk_assembler.py
------------------
Capa 4 — Última capa antes del IngestRouter.

Construye CleanChunk con context_prefix basado en lo que sabe el preprocesador.
El IngestRouter enriquecerá después con los datos del pasaporte semántico
(dominio, subdomain, tags) cuando ya exista.

Responsabilidades:
  1. Filtrar bloques por quality_score (umbral por content_type)
  2. Filtrar bloques por longitud mínima (por content_type)
  3. Deduplicar por hash SHA-256 del contenido
  4. Construir context_prefix: [Fuente: X | Formato: Y | Sección: Z | Página: N]
  5. Validación final de 4 checks antes de aceptar el chunk
"""

import hashlib
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class CleanChunk:
    """Chunk listo para vectorizar y enviar al IngestRouter."""
    text: str                           # context_prefix + "\n" + content
    metadata: dict = field(default_factory=dict)
    quality_score: float = 1.0
    source_format: str = ""
    chunk_index: int = 0


class ChunkAssembler:
    """
    Última capa antes del IngestRouter.
    Construye CleanChunk con context_prefix basado en lo que sabe
    el preprocesador. El IngestRouter enriquecerá después con
    los datos del pasaporte semántico.
    """

    # Umbrales de quality_score por content_type
    # Código y tabla tienen bajo ratio alfabético por definición → umbrales bajos
    QUALITY_THRESHOLDS = {
        "text":           0.40,
        "table":          0.10,
        "code":           0.05,
        "callout":        0.30,
        "footnote":       0.30,
        "figure_caption": 0.50,
        "glossary_entry": 0.60,
        "list":           0.35,
        "separator":      2.0,   # siempre descartar separadores decorativos
    }

    # Longitudes mínimas por content_type
    MIN_LENGTH = {
        "text":           80,
        "table":          20,
        "code":           10,
        "callout":        20,
        "footnote":       30,
        "figure_caption": 15,
        "glossary_entry": 10,
        "list":           20,
    }

    def assemble(
        self,
        blocks: list,
        doc_meta: dict,
    ) -> list:
        """
        Ensambla bloques en CleanChunks validados y deduplicados.

        Args:
            blocks:   Lista de dicts con campos content, content_type,
                      page, quality_score, metadata (salida del extractor).
            doc_meta: Metadatos del documento conocidos en preprocesado:
                      source, format, total_pages, etc.
                      NO contiene datos del pasaporte (aún no existe).

        Returns:
            Lista de CleanChunk listos para el IngestRouter.
        """
        chunks = []
        seen_hashes: set = set()
        discarded = 0

        for idx, block in enumerate(blocks):
            content = block.get("content", "").strip()
            content_type = block.get("content_type", "text")

            if not content:
                discarded += 1
                continue

            # 1. Quality filter con umbral por content_type
            quality = block.get("quality_score", 1.0)
            threshold = self.QUALITY_THRESHOLDS.get(content_type, 0.40)
            if quality < threshold:
                log.debug(
                    "Chunk descartado quality=%.2f < %.2f tipo=%s fuente=%s",
                    quality, threshold, content_type, doc_meta.get("source", ""),
                )
                discarded += 1
                continue

            # 2. Longitud mínima
            min_len = self.MIN_LENGTH.get(content_type, 80)
            if len(content) < min_len:
                discarded += 1
                continue

            # 3. Deduplicación por hash SHA-256
            text_hash = hashlib.sha256(content.encode()).hexdigest()
            if text_hash in seen_hashes:
                discarded += 1
                continue
            seen_hashes.add(text_hash)

            # 4. Construir context_prefix con datos del preprocesador
            prefix = self._build_context_prefix(block, doc_meta)

            # 5. Texto vectorizable = prefix + content
            vectorizable = f"{prefix}\n{content}" if prefix else content

            # 6. Validación final — 4 checks obligatorios
            if not self._validate(vectorizable, prefix, quality, content):
                discarded += 1
                continue

            chunks.append(CleanChunk(
                text=vectorizable,
                metadata={
                    **block.get("metadata", {}),
                    "context_prefix": prefix,
                    "content_type":   content_type,
                    "source":         doc_meta.get("source", ""),
                    "source_format":  doc_meta.get("format", ""),
                    "quality_score":  quality,
                    "chunk_hash":     text_hash,
                    # Placeholders para el IngestRouter — se rellenan con el pasaporte
                    "domain":         "",
                    "subdomain":      "",
                    "tags":           [],
                },
                quality_score=quality,
                source_format=doc_meta.get("format", ""),
                chunk_index=idx,
            ))

        log.info(
            "Assembler: %d chunks válidos, %d descartados — fuente: %s",
            len(chunks), discarded, doc_meta.get("source", ""),
        )
        return chunks

    def _build_context_prefix(self, block: dict, doc_meta: dict) -> str:
        """
        Construye el context_prefix con la información disponible
        en el momento del preprocesado.

        Formato: [Fuente: X | Formato: Y | Sección: Z | Página: N | Tipo: T]

        El IngestRouter añadirá Dominio y tags cuando el pasaporte esté listo.
        """
        parts = []

        source = doc_meta.get("source", "")
        if source:
            parts.append(f"Fuente: {source}")

        fmt = doc_meta.get("format", "")
        if fmt:
            parts.append(f"Formato: {fmt.upper()}")

        # Sección — DOCX headings, MD secciones, PPTX slide title, XLSX sheet, etc.
        meta = block.get("metadata", {})
        section = (
            meta.get("section")
            or meta.get("slide_title")
            or meta.get("sheet")
            or meta.get("page_name")
        )
        if section:
            parts.append(f"Sección: {section}")

        # Página o posición ordinal
        page = block.get("page")
        total = doc_meta.get("total_pages")
        if page and total:
            parts.append(f"Página: {page}/{total}")
        elif page:
            parts.append(f"Página: {page}")

        # Tipo de contenido no trivial
        ct = block.get("content_type", "")
        if ct and ct not in ("text", ""):
            parts.append(f"Tipo: {ct}")

        if ct == "code":
            module = block.get("module") or meta.get("module")
            symbol = block.get("function") or meta.get("function")
            obj_type = meta.get("type")
            lang = block.get("language") or meta.get("language")
            if module:
                parts.append(f"Módulo: {module}")
            if symbol:
                parts.append(f"Símbolo: {symbol}")
            if obj_type:
                parts.append(f"Clase: {obj_type}")
            if lang:
                parts.append(f"Lenguaje: {lang}")

        return "[" + " | ".join(parts) + "]" if parts else ""

    def _validate(
        self,
        vectorizable: str,
        prefix: str,
        quality: float,
        content: str,
    ) -> bool:
        """4 checks obligatorios antes de aceptar un chunk."""
        # Check 1: texto vectorizable no vacío
        if not vectorizable.strip():
            return False
        # Check 2: prefix construido (doc_meta mínimo necesario)
        if not prefix:
            return False
        # Check 3: quality score positivo
        if quality <= 0:
            return False
        # Check 4: al menos una palabra reconocible (longitud 2-20 chars)
        words = [w for w in content.split() if 2 <= len(w) <= 20]
        if not words:
            return False
        return True
