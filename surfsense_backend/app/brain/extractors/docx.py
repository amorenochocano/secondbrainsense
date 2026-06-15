"""
docx.py — Extractor de Word para pasaportes semánticos.

Mejoras sobre versión anterior:
  - Respeta jerarquía Heading 1/2/3 → '# ', '## ', '### ' (no aplana todo a '#').
  - Detecta listas (numeradas y bullets) y las preserva como '- item' en lugar
    de mezclarlas con párrafos.
  - Las tablas incluyen el heading de sección anterior como contexto
    ('Sección: X / Tabla N') para que el LLM sepa de qué habla la tabla.
  - Textboxes flotantes incluyen el heading actual del flujo.
  - Maneja Track Changes correctamente (ignora w:del, acepta w:ins).
"""
import logging
from .base import BaseExtractor
from docx import Document
from docx.oxml.ns import qn
try:
    from rag_lib.layer1_universal import UniversalCleaner as _UniversalCleaner
    _CLEANER = _UniversalCleaner()
except Exception:
    _CLEANER = None

log = logging.getLogger(__name__)

# Filas por bloque de tabla
_TABLE_CHUNK = 20

# Mapeo de niveles de heading a markdown
_HEADING_LEVELS = {
    "heading 1": "#",
    "heading 2": "##",
    "heading 3": "###",
    "heading 4": "####",
    "heading 5": "#####",
    "heading 6": "######",
    "title":     "#",
    "subtitle":  "##",
}


def _heading_marker(style_name: str) -> str | None:
    """Devuelve el marcador markdown ('#', '##', ...) según el style del párrafo."""
    if not style_name:
        return None
    sn = style_name.lower().strip()
    return _HEADING_LEVELS.get(sn)


def _is_list_paragraph(paragraph) -> tuple[bool, str]:
    """
    Detecta si un párrafo pertenece a una lista (numerada o con viñetas).
    Devuelve (es_lista, marcador) donde marcador es '-' (bullet) o '1.' (numbered).

    docx no expone esto directamente; hay que mirar el XML del párrafo:
      - Si tiene <w:numPr> → es lista
      - El estilo 'List Bullet'/'List Number' también lo indica
    """
    style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
    if "bullet" in style_name or "list bullet" in style_name:
        return True, "-"
    if "number" in style_name or "list number" in style_name:
        return True, "1."

    # Mirar el XML para detectar numbering
    pPr = paragraph._element.find(qn("w:pPr"))
    if pPr is not None:
        numPr = pPr.find(qn("w:numPr"))
        if numPr is not None:
            # Determinar bullet vs numbered por el numId
            # Convención: cualquier numbering aplicado = lista
            # Sin acceso al numbering.xml para distinguir, usar '-' por defecto
            return True, "-"

    return False, ""


class DocxExtractor(BaseExtractor):

    def extract(self, source: str) -> list[dict]:
        doc = Document(source)
        blocks = []

        # Estado de sección: heading_stack mantiene jerarquía actual
        # [(level, text)] — para que las tablas hereden el contexto
        heading_stack: list = []
        current_heading_path = ""

        section_idx = 1
        body_lines: list = []  # acumulador del cuerpo bajo el heading actual

        def flush_body():
            nonlocal section_idx
            if not body_lines:
                return
            content = "\n".join(body_lines).strip()
            if not content:
                body_lines.clear()
                return
            blocks.append({
                "content": content,
                "content_type": "text",
                "page": section_idx,
                "text": content,
                "metadata": {
                    "format": "docx",
                    "section": current_heading_path or f"section_{section_idx}",
                },
            })
            section_idx += 1
            body_lines.clear()

        def update_heading_stack(level: int, text: str):
            """Mantén la jerarquía: nivel N reemplaza todos los headings >= N."""
            nonlocal current_heading_path
            # Eliminar headings de igual o mayor nivel
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            current_heading_path = " / ".join(h[1] for h in heading_stack)

        # ── Procesar párrafos en orden documental ──────────────────────
        for p in doc.paragraphs:
            text = self._get_clean_paragraph_text(p)
            if not text:
                continue

            style_name = (p.style.name or "") if p.style else ""
            marker = _heading_marker(style_name)

            if marker:
                # Es un heading — volcar cuerpo previo, actualizar stack
                flush_body()
                level = len(marker)  # '##' → 2
                update_heading_stack(level, text)
                # El heading entra como primera línea del nuevo bloque
                body_lines.append(f"{marker} {text}")
                continue

            # ¿Lista?
            is_list, list_marker = _is_list_paragraph(p)
            if is_list:
                body_lines.append(f"{list_marker} {text}")
                continue

            # Párrafo normal
            body_lines.append(text)

        # Volcar último bloque
        flush_body()

        # ── Tablas: serializar con contexto del último heading ─────────
        # (las tablas en docx vienen aparte, no se mezclan con paragraphs)
        for t_idx, table in enumerate(doc.tables, 1):
            rows = [[(c.text or "").strip() for c in r.cells] for r in table.rows]
            rows = [r for r in rows if any(cell for cell in r)]
            if not rows:
                continue

            headers = rows[0]
            data_rows = rows[1:]

            # Contexto: usar el último heading del documento como pista de la tabla.
            # No es perfecto (tablas pueden estar bajo cualquier heading), pero
            # es mejor que nada para que el LLM ubique de qué habla.
            table_context = current_heading_path or "Documento"

            for chunk_start in range(0, max(len(data_rows), 1), _TABLE_CHUNK):
                chunk = data_rows[chunk_start: chunk_start + _TABLE_CHUNK]
                lines = [f"### Tabla {t_idx} (en: {table_context})"]

                if not chunk and chunk_start == 0:
                    # Solo cabecera
                    lines.append(f"columnas: {', '.join(h for h in headers if h)}")
                else:
                    for row in chunk:
                        pairs = " | ".join(
                            f"{h}: {v}"
                            for h, v in zip(headers, row)
                            if h and v
                        )
                        if pairs:
                            lines.append(pairs)

                chunk_text = "\n".join(lines)
                if chunk_text.strip():
                    blocks.append({
                        "content": chunk_text,
                        "content_type": "table",
                        "page": section_idx,
                        "text": chunk_text,
                        "metadata": {
                            "format": "docx",
                            "section": f"table_{t_idx}",
                            "table_index": t_idx,
                            "parent_section": table_context,
                            "columns": [h for h in headers if h][:20],
                        },
                    })
                    section_idx += 1

        # ── Textboxes flotantes ────────────────────────────────────────
        for txbx in doc.element.body.iter(qn("w:txbxContent")):
            txbx_lines = []
            for para in txbx.iter(qn("w:p")):
                parts = []
                for run_el in para.iter(qn("w:r")):
                    # Saltar runs dentro de w:del (Track Changes deletion)
                    parent = run_el.getparent()
                    in_del = False
                    while parent is not None:
                        if parent.tag == qn("w:del"):
                            in_del = True
                            break
                        parent = parent.getparent()
                    if in_del:
                        continue
                    for t in run_el.iter(qn("w:t")):
                        parts.append(t.text or "")
                line = "".join(parts).strip()
                if line:
                    txbx_lines.append(line)

            if txbx_lines:
                txbx_text = "\n".join(txbx_lines)
                # Añadir contexto del heading actual al textbox
                if current_heading_path:
                    txbx_text = f"### Textbox (en: {current_heading_path})\n{txbx_text}"
                blocks.append({
                    "content": txbx_text,
                    "content_type": "text",
                    "page": section_idx,
                    "text": txbx_text,
                    "metadata": {
                        "format": "docx",
                        "section": "textbox",
                        "parent_section": current_heading_path or "",
                    },
                })
                section_idx += 1

        if not blocks:
            log.warning("[docx] '%s' → sin contenido extraído", source)
            return [{"page": 1, "text": "", "content": "", "content_type": "text"}]

        if _CLEANER is not None:
            blocks = _CLEANER.apply_to_blocks(blocks)

        log.debug("[docx] '%s' → %d bloques", source, len(blocks))
        return blocks

    @staticmethod
    def _get_clean_paragraph_text(paragraph) -> str:
        """
        Reconstruye texto del párrafo ignorando runs dentro de w:del (Track Changes).
        Acepta w:ins (cambios aceptados).
        """
        parts = []
        for run_el in paragraph._element.iter(qn("w:r")):
            parent = run_el.getparent()
            in_del = False
            while parent is not None:
                if parent.tag == qn("w:del"):
                    in_del = True
                    break
                parent = parent.getparent()
            if in_del:
                continue
            for t in run_el.iter(qn("w:t")):
                parts.append(t.text or "")
        return "".join(parts).strip()
