"""
pdf.py — Extractor de PDF para pasaportes semánticos.

Mejoras sobre versión anterior:
  - Detección de HEADINGS por tamaño de fuente (no solo posición/coords).
    Los headings se marcan como '# Heading' al inicio del bloque, dando al
    pasaporte estructura jerárquica clara para el LLM (esencial con modelos 3B).
  - Agrupación de párrafos consecutivos bajo el mismo heading.
  - Detección de bullets/lists por marcadores estándar (●, •, -, *) → se
    conservan como items en el texto para el LLM, no se aplanan.
  - Detección mejorada de headers/footers: prueba ubicación (top/bottom)
    y patrones (números de página, fechas).
  - Bloques de código por fenced ``` o por monospace consistente (>3 líneas
    seguidas con espacios al inicio y fuente monoespaciada).
"""
import re
import io
import logging
from collections import Counter, defaultdict
import fitz
from .base import BaseExtractor
try:
    from rag_lib.layer1_universal import UniversalCleaner as _UniversalCleaner
    _CLEANER = _UniversalCleaner()
except Exception:
    _CLEANER = None

log = logging.getLogger(__name__)

# Patrones que indican línea de código (solo dentro de fenced blocks)
_CODE_FENCE_START = re.compile(r"^```[\w]*\s*$")
_CODE_FENCE_END   = re.compile(r"^```\s*$")

# Callouts: líneas que empiezan por palabra clave reservada
_CALLOUT_PATTERN = re.compile(
    r"^(NOTA|NOTE|ADVERTENCIA|WARNING|IMPORTANTE|IMPORTANT|ATENCIÓN|PRECAUCIÓN|TIP)\s*[:\-]",
    re.IGNORECASE,
)

# Patrones que un header/footer típicos
_PAGE_NUM_PATTERN = re.compile(
    r"^(página|page|pág\.?|p\.)?\s*\d{1,4}\s*(?:[/|]\s*\d{1,4}|de\s+\d{1,4})?\s*$",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\s*$")

# Umbral para considerar página como escaneada (OCR fallback)
_OCR_THRESHOLD = 50
# Banda vertical para agrupar bloques en la misma línea horizontal (en pt)
_COLUMN_BAND = 20

# Bullets reconocidos como inicio de item (incluye middot · que PyMuPDF
# a veces produce al renderizar bullets nativos del PDF)
_BULLET_CHARS = "●•◦▪○-*■►▸·"


class PDFExtractor(BaseExtractor):

    def extract(self, source: str) -> list[dict]:
        blocks = []
        with fitz.open(source) as doc:
            # Pre-procesado: detectar headers/footers y umbral de heading
            repeated = self._detect_repeated_lines(doc)
            heading_threshold = self._compute_heading_threshold(doc)
            log.debug(
                "[pdf] '%s' → headers/footers detectados: %d | heading_threshold: %.1fpt",
                source, len(repeated), heading_threshold,
            )

            for i, page in enumerate(doc):
                page_blocks = self._extract_page(page, i + 1, repeated, heading_threshold)
                blocks.extend(page_blocks)

        blocks = [b for b in blocks if b is not None]
        if not blocks:
            log.warning("[pdf] '%s' → sin texto extraído (PDF vacío o sólo imágenes)", source)
        if blocks and _CLEANER is not None:
            blocks = _CLEANER.apply_to_blocks(blocks)
        log.debug("[pdf] '%s' → %d bloques", source, len(blocks))
        return blocks if blocks else []

    # ------------------------------------------------------------------
    # Cálculo del umbral de heading basado en distribución de tamaños
    # ------------------------------------------------------------------

    def _compute_heading_threshold(self, doc) -> float:
        """
        Calcula el tamaño de fuente a partir del cual una línea es heading.

        Lógica: el tamaño dominante (mediana ponderada por caracteres) es
        body text. Un heading suele ser >= body_size * 1.15 (15% más grande).
        Si el PDF no usa diferencias de tamaño, devuelve un umbral imposible
        (1000pt) que desactiva la detección de headings.
        """
        size_counts: dict[float, int] = defaultdict(int)
        for page in doc[:10]:  # muestrear primeras 10 páginas
            try:
                page_dict = page.get_text("dict")
            except Exception:
                continue
            for block in page_dict.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = round(span.get("size", 0), 1)
                        text = span.get("text", "")
                        if size > 0 and text.strip():
                            size_counts[size] += len(text.strip())

        if not size_counts:
            return 1000.0   # sin info → desactivar headings

        # Body = tamaño con más caracteres
        body_size = max(size_counts.items(), key=lambda kv: kv[1])[0]

        # Heading: 15% más grande que body
        threshold = body_size * 1.15

        # Si no hay tamaños mayores que el threshold, no hay headings → desactivar
        sizes_above = [s for s in size_counts if s >= threshold]
        if not sizes_above:
            return 1000.0

        return threshold

    # ------------------------------------------------------------------
    # Extracción de página con metadatos de fuente
    # ------------------------------------------------------------------

    def _extract_page(
        self,
        page,
        page_num: int,
        repeated: set,
        heading_threshold: float,
    ) -> list[dict]:
        """
        Extrae bloques de una página combinando:
          - Estructura tipográfica (tamaño de fuente para headings)
          - Layout (coords para columnas)
          - Eliminación de headers/footers detectados
        """
        # Intentar extracción rica con dict (incluye tamaños de fuente)
        try:
            page_dict = page.get_text("dict")
        except Exception:
            page_dict = None

        # OCR fallback: si hay muy poco texto
        total_chars = 0
        if page_dict:
            for block in page_dict.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        total_chars += len(span.get("text", ""))

        if total_chars < _OCR_THRESHOLD:
            ocr_text = self._ocr_page(page)
            if ocr_text.strip():
                return self._split_text_and_code(ocr_text, page_num, repeated)
            return []

        # Construir líneas con metadatos: (y, x, text, max_size, is_bold)
        page_height = page.rect.height
        lines_meta: list[tuple] = []

        if page_dict:
            for block in page_dict.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    line_text = "".join(s.get("text", "") for s in spans).strip()
                    if not line_text:
                        continue
                    max_size = max((s.get("size", 0) for s in spans), default=0)
                    is_bold = any("Bold" in s.get("font", "") for s in spans)
                    bbox = line.get("bbox", (0, 0, 0, 0))
                    y0 = bbox[1]
                    x0 = bbox[0]
                    lines_meta.append((y0, x0, line_text, max_size, is_bold))

        if not lines_meta:
            # Fallback al método antiguo si dict no funcionó
            return self._extract_page_simple(page, page_num, repeated)

        # Ordenar por banda horizontal → columnas correctas
        lines_meta.sort(key=lambda t: (round(t[0] / _COLUMN_BAND), t[1]))

        # Filtrar headers/footers
        result_lines: list[tuple] = []
        for y0, x0, text, size, is_bold in lines_meta:
            # Filtro por contenido
            if text in repeated:
                continue
            if _PAGE_NUM_PATTERN.match(text):
                continue
            if _DATE_PATTERN.match(text):
                continue
            # Filtro por posición: top 5% o bottom 5% Y poco texto → header/footer
            rel_y = y0 / page_height if page_height > 0 else 0.5
            if (rel_y < 0.05 or rel_y > 0.95) and len(text) < 60:
                continue
            result_lines.append((y0, x0, text, size, is_bold))

        if not result_lines:
            return []

        # Agrupar en bloques: heading inicia uno nuevo, párrafos continúan
        return self._group_into_blocks(result_lines, page_num, heading_threshold)

    def _group_into_blocks(
        self,
        lines: list[tuple],
        page_num: int,
        heading_threshold: float,
    ) -> list[dict]:
        """
        Agrupa líneas en bloques estructurados.

        Estrategia:
          - Una línea con tamaño >= heading_threshold inicia un nuevo bloque
            y se prefija con '# ' en el contenido (marca para el preprocesador).
          - Líneas que empiezan con bullet (• ● - * etc.) se conservan tal cual.
          - Líneas que matchean callout se aíslan como content_type='callout'.
          - El resto se acumula como texto bajo el heading actual.
        """
        blocks = []
        current_heading = ""
        current_lines: list[str] = []

        def flush():
            if not current_lines:
                return
            content_parts = []
            if current_heading:
                content_parts.append(f"# {current_heading}")
            content_parts.extend(current_lines)
            content = "\n".join(content_parts).strip()
            if content:
                blocks.append({
                    "content": content,
                    "content_type": "text",
                    "page": page_num,
                    "text": content,
                    "metadata": {
                        "format": "pdf",
                        "section": current_heading or f"page_{page_num}",
                    },
                })

        for y0, x0, text, size, is_bold in lines:
            # Detectar callout
            if _CALLOUT_PATTERN.match(text):
                flush()
                current_lines = []
                blocks.append({
                    "content": text,
                    "content_type": "callout",
                    "page": page_num,
                    "text": text,
                    "metadata": {"format": "pdf", "section": current_heading or ""},
                })
                continue

            # Detectar heading: tamaño >= threshold (o bold + corto)
            is_heading = (
                size >= heading_threshold
                or (is_bold and len(text) < 80 and not text.endswith((".", ":", ";", ",")))
            )

            if is_heading:
                # Volcar bloque anterior
                flush()
                current_heading = text
                current_lines = []
            else:
                # Bullet: preservar marcador
                stripped = text.lstrip()
                if stripped and stripped[0] in _BULLET_CHARS:
                    # Normalizar a guion para markdown
                    rest = stripped[1:].lstrip()
                    current_lines.append(f"- {rest}")
                else:
                    current_lines.append(text)

        flush()
        return blocks

    # ------------------------------------------------------------------
    # Fallback simple (cuando get_text("dict") falla)
    # ------------------------------------------------------------------

    def _extract_page_simple(self, page, page_num: int, repeated: set) -> list[dict]:
        """Fallback: método anterior con get_text('blocks')."""
        raw_blocks = page.get_text("blocks")
        text_blocks = [b for b in raw_blocks if b[6] == 0 and b[4].strip()]
        text_blocks_sorted = sorted(text_blocks, key=lambda b: (round(b[1] / _COLUMN_BAND), b[0]))

        result = []
        for b in text_blocks_sorted:
            text = b[4].strip()
            if not text:
                continue
            first_line = text.splitlines()[0].strip()
            if first_line in repeated:
                continue
            result.extend(self._split_text_and_code(text, page_num, repeated))
        return result

    # ------------------------------------------------------------------
    # Detección de headers/footers por repetición
    # ------------------------------------------------------------------

    def _detect_repeated_lines(self, doc) -> set:
        """
        Líneas que aparecen en >40% de las páginas son header/footer.
        Antes 60% — bajado porque PDFs cortos (5-10 páginas) no llegaban.
        """
        if len(doc) < 3:
            return set()
        line_counts: Counter = Counter()
        for page in doc:
            page_text = page.get_text("text")
            lines = page_text.split("\n")
            candidates = lines[:3] + lines[-3:]
            for line in candidates:
                stripped = line.strip()
                if stripped and len(stripped) < 100:
                    line_counts[stripped] += 1
        threshold = max(3, len(doc) * 0.4)
        return {line for line, count in line_counts.items() if count >= threshold}

    # ------------------------------------------------------------------
    # Separación texto/code/callout (legado, para fallback OCR)
    # ------------------------------------------------------------------

    def _split_text_and_code(self, text: str, page_num: int, repeated: set = None) -> list[dict]:
        """
        Divide texto en bloques text/code/callout. Usado en OCR fallback y
        cuando get_text('dict') no produce metadatos de fuente.
        """
        repeated = repeated or set()
        lines = text.splitlines()
        result = []
        buffer_type = "text"
        buffer_lines: list[str] = []
        in_fence = False

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped in repeated:
                i += 1
                continue

            if _CODE_FENCE_START.match(stripped):
                if buffer_lines:
                    result.append(self._make_block(buffer_lines, buffer_type, page_num))
                buffer_lines = []
                in_fence = True
                buffer_type = "code"
                i += 1
                continue

            if in_fence and _CODE_FENCE_END.match(stripped):
                if buffer_lines:
                    result.append(self._make_block(buffer_lines, "code", page_num))
                buffer_lines = []
                in_fence = False
                buffer_type = "text"
                i += 1
                continue

            if in_fence:
                buffer_lines.append(line)
                i += 1
                continue

            if _CALLOUT_PATTERN.match(stripped):
                if buffer_lines:
                    result.append(self._make_block(buffer_lines, buffer_type, page_num))
                buffer_lines = [line]
                buffer_type = "callout"
                i += 1
                while i < len(lines) and lines[i].strip():
                    buffer_lines.append(lines[i])
                    i += 1
                result.append(self._make_block(buffer_lines, "callout", page_num))
                buffer_lines = []
                buffer_type = "text"
                continue

            if buffer_type == "callout":
                buffer_type = "text"

            buffer_lines.append(line)
            i += 1

        if buffer_lines:
            result.append(self._make_block(buffer_lines, buffer_type, page_num))

        return [b for b in result if b is not None]

    # ------------------------------------------------------------------
    # OCR fallback
    # ------------------------------------------------------------------

    def _ocr_page(self, page) -> str:
        """OCR con pytesseract. Devuelve '' si no está disponible."""
        try:
            import pytesseract
            from PIL import Image
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            return pytesseract.image_to_string(img, lang="spa+eng")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Constructor de bloque
    # ------------------------------------------------------------------

    @staticmethod
    def _make_block(lines: list, block_type: str, page_num: int) -> dict:
        content = "\n".join(lines).strip()
        if not content:
            return None
        block = {
            "content":      content,
            "content_type": block_type,
            "page":         page_num,
            "text":         content,
            "metadata":     {"format": "pdf"},
        }
        if block_type == "code":
            block["language"] = "unknown"
        return block
