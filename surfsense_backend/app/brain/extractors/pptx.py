"""
pptx.py — Extractor de PowerPoint para pasaportes semánticos.

Mejoras sobre versión anterior:
  - Agrupa slides por SECCIÓN lógica detectada vía:
      a) Title slides (layout='Section Header' o título solo sin contenido)
      b) Cambios marcados de slide section en el XML
    Esto da al pasaporte estructura por sección, no solo por slide.
  - Procesa shapes dentro de GROUPS recursivamente (grupos = slides
    estructurados como diagramas; antes se ignoraban).
  - Extrae SmartArt (organigramas, flow) iterando descendientes texto.
  - Ordena shapes por coordenadas (top, left) para reconstruir orden visual
    de lectura, no orden XML arbitrario.
  - Combina speaker notes en el mismo bloque que el slide, con marcador
    '## Notas:' para que el LLM las identifique.
  - Detecta diapositivas de "agenda/índice" y las marca con menor importancia.
"""
import logging
from .base import BaseExtractor
from pptx import Presentation
try:
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    _HAS_MSO = True
except ImportError:
    _HAS_MSO = False

log = logging.getLogger(__name__)

# Palabras que indican slide de agenda/índice (baja señal)
_AGENDA_KEYWORDS = ("agenda", "indice", "índice", "portada", "contents",
                    "contenido", "outline", "tabla de contenido", "summary",
                    "resumen", "objetivos", "objectives")

# Palabras en TÍTULO que pueden indicar inicio de sección lógica
_SECTION_START_KEYWORDS = ("parte", "part", "sección", "section", "capítulo",
                           "capitulo", "chapter", "módulo", "modulo", "module")


class PptxExtractor(BaseExtractor):

    def extract(self, source: str) -> list[dict]:
        prs = Presentation(source)
        blocks = []
        block_idx = 1

        # Detectar secciones lógicas y agrupar slides
        section_groups = self._detect_sections(prs)
        log.debug("[pptx] '%s' → %d secciones detectadas", source, len(section_groups))

        for section_name, slide_indices in section_groups:
            for slide_idx in slide_indices:
                slide = prs.slides[slide_idx]
                slide_blocks = self._extract_slide(
                    slide, slide_idx + 1, section_name, block_idx,
                )
                blocks.extend(slide_blocks)
                block_idx += len(slide_blocks)

        log.debug("[pptx] '%s' → %d bloques", source, len(blocks))
        return blocks

    # ------------------------------------------------------------------
    # Detección de secciones lógicas
    # ------------------------------------------------------------------

    def _detect_sections(self, prs) -> list:
        """
        Agrupa slides en secciones lógicas.

        Estrategia:
          1. Intenta leer las secciones nativas de PPTX (p:sldIdLst con
             metadata de secciones, si están definidas).
          2. Si no hay secciones definidas, hace heurística:
             - Slides con layout 'Section Header' inician sección
             - Slides con título tipo "Parte 1: X" o "Sección N" también
             - Si nada, todo va en "Documento"

        Devuelve [(nombre_sección, [indices_slides]), ...].
        """
        # Intentar secciones nativas vía XML de la presentación
        try:
            from pptx.oxml.ns import qn as _qn
            xml_sections = self._extract_native_sections(prs, _qn)
            if xml_sections:
                return xml_sections
        except Exception as e:
            log.debug("[pptx] secciones nativas no detectadas: %s", e)

        # Heurística por layout y título
        return self._detect_sections_heuristic(prs)

    def _extract_native_sections(self, prs, qn_func) -> list:
        """Lee secciones definidas en p:sectionLst del XML de la presentación."""
        prs_element = prs.part.element
        # Buscar extLst > ext > sectionLst
        ext_lst = prs_element.find(qn_func("p:extLst"))
        if ext_lst is None:
            return []

        sections = []
        # Mapeo slideId → slide_index
        slide_id_to_idx = {}
        sld_id_lst = prs_element.find(qn_func("p:sldIdLst"))
        if sld_id_lst is None:
            return []

        for idx, sld_id in enumerate(sld_id_lst.findall(qn_func("p:sldId"))):
            slide_id_to_idx[sld_id.get("id")] = idx

        # Buscar sections en cualquier ext
        for ext in ext_lst.iter():
            tag_local = ext.tag.split("}")[-1] if "}" in ext.tag else ext.tag
            if "sectionLst" not in tag_local and "section" not in tag_local.lower():
                continue
            # Iterar secciones
            for sec in ext.iter():
                sec_tag = sec.tag.split("}")[-1] if "}" in sec.tag else sec.tag
                if sec_tag != "section":
                    continue
                name = sec.get("name", "Sección")
                slide_indices = []
                for sld in sec.iter():
                    sld_tag = sld.tag.split("}")[-1] if "}" in sld.tag else sld.tag
                    if sld_tag == "sldId":
                        sid = sld.get("id")
                        if sid in slide_id_to_idx:
                            slide_indices.append(slide_id_to_idx[sid])
                if slide_indices:
                    sections.append((name, slide_indices))

        return sections

    def _detect_sections_heuristic(self, prs) -> list:
        """Heurística: detecta secciones por layout o título."""
        sections = []
        current_section = "Documento"
        current_indices = []

        for i, slide in enumerate(prs.slides):
            # Comprobar layout
            layout_name = ""
            try:
                layout_name = (slide.slide_layout.name or "").lower()
            except Exception:
                pass

            # Título de la slide
            title = ""
            if slide.shapes.title and getattr(slide.shapes.title, "text", "").strip():
                title = slide.shapes.title.text.strip()
            title_lower = title.lower()

            is_section_start = (
                "section" in layout_name
                or any(kw in title_lower for kw in _SECTION_START_KEYWORDS)
            )

            if is_section_start and current_indices:
                # Cerrar sección actual
                sections.append((current_section, current_indices))
                current_section = title or f"Sección {len(sections) + 1}"
                current_indices = [i]
            elif is_section_start:
                # Primera sección
                current_section = title or "Documento"
                current_indices = [i]
            else:
                current_indices.append(i)

        if current_indices:
            sections.append((current_section, current_indices))

        return sections or [("Documento", list(range(len(prs.slides))))]

    # ------------------------------------------------------------------
    # Extracción de slide individual
    # ------------------------------------------------------------------

    def _extract_slide(self, slide, slide_num: int, section_name: str, block_idx: int) -> list:
        """
        Extrae el contenido de una slide:
          - Texto (incluyendo grupos y smartart) ordenado por coords
          - Tablas como bloques separados
          - Speaker notes adjuntas al bloque principal
        """
        blocks = []
        title = ""
        if slide.shapes.title and getattr(slide.shapes.title, "text", "").strip():
            title = slide.shapes.title.text.strip()

        # Recopilar todos los shapes (resolviendo grupos)
        all_shapes = self._collect_shapes(slide.shapes)

        # Separar tablas del resto
        text_shapes = []
        table_shapes = []
        for sh, depth in all_shapes:
            if _HAS_MSO and sh.shape_type == MSO_SHAPE_TYPE.TABLE:
                table_shapes.append(sh)
            else:
                text_shapes.append((sh, depth))

        # Ordenar text_shapes por coordenadas (top, left) para orden visual
        def shape_position(sh):
            try:
                top = getattr(sh, "top", None)
                left = getattr(sh, "left", None)
                if top is None or left is None:
                    return (999999, 999999)
                return (top, left)
            except Exception:
                return (999999, 999999)

        text_shapes.sort(key=lambda pair: shape_position(pair[0]))

        # Extraer texto de cada shape (omitiendo el title que va al inicio)
        text_parts = []
        if title:
            text_parts.append(f"# {title}")

        for shape, depth in text_shapes:
            text = self._extract_shape_text(shape)
            if not text:
                continue
            if title and text.strip() == title:
                continue
            text_parts.append(text)

        # Filtrar slides de baja señal (agenda/portada vacía)
        if self._is_low_signal_slide(title, text_parts):
            # No descartar del todo, marcar metadata
            is_agenda = True
        else:
            is_agenda = False

        # Speaker notes
        notes_text = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = (slide.notes_slide.notes_text_frame.text or "").strip()
        except Exception:
            notes_text = ""

        # Ensamblar bloque principal
        if text_parts or notes_text:
            content_lines = list(text_parts)
            if notes_text:
                content_lines.append(f"\n## Notas del presentador\n{notes_text}")
            content = "\n".join(content_lines).strip()
            if content:
                blocks.append({
                    "content": content,
                    "content_type": "text",
                    "page": block_idx,
                    "format": "pptx",
                    "text": content,
                    "metadata": {
                        "format": "pptx",
                        "slide": slide_num,
                        "slide_title": title or f"slide_{slide_num}",
                        "section": section_name,
                        "is_agenda": is_agenda,
                        "has_notes": bool(notes_text),
                    },
                })

        # Tablas como bloques independientes (con contexto)
        for t_idx, table_shape in enumerate(table_shapes, 1):
            table_text = self._extract_table(table_shape.table)
            if not table_text:
                continue
            ctx = f"### Tabla {t_idx} (slide {slide_num}: {title or 'sin título'})"
            full_text = f"{ctx}\n{table_text}"
            blocks.append({
                "content": full_text,
                "content_type": "table",
                "page": block_idx + len(blocks),
                "format": "pptx",
                "text": full_text,
                "metadata": {
                    "format": "pptx",
                    "slide": slide_num,
                    "slide_title": title or f"slide_{slide_num}",
                    "section": section_name,
                    "table_index": t_idx,
                },
            })

        return blocks

    # ------------------------------------------------------------------
    # Resolver grupos: extraer shapes recursivamente
    # ------------------------------------------------------------------

    def _collect_shapes(self, shapes, depth: int = 0, max_depth: int = 5) -> list:
        """
        Itera shapes resolviendo grupos recursivamente.
        Devuelve [(shape, depth), ...] donde depth indica anidamiento (útil
        para indentar si se quiere preservar la jerarquía).
        """
        result = []
        if depth > max_depth:
            return result

        for shape in shapes:
            if _HAS_MSO and shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                # Es un grupo: recurseion sobre sus shapes hijos
                try:
                    result.extend(self._collect_shapes(shape.shapes, depth + 1, max_depth))
                except Exception:
                    pass
            else:
                result.append((shape, depth))

        return result

    # ------------------------------------------------------------------
    # Extracción de texto de un shape
    # ------------------------------------------------------------------

    def _extract_shape_text(self, shape) -> str:
        """
        Extrae texto de cualquier shape: text frames, smartart, etc.
        Para SmartArt itera descendientes con texto.
        """
        # 1) text_frame estándar
        if hasattr(shape, "text_frame") and shape.text_frame is not None:
            paragraphs_text = []
            for paragraph in shape.text_frame.paragraphs:
                p_text = (paragraph.text or "").strip()
                if p_text:
                    paragraphs_text.append(p_text)
            if paragraphs_text:
                return "\n".join(paragraphs_text)

        # 2) Fallback a shape.text
        if hasattr(shape, "text"):
            txt = (shape.text or "").strip()
            if txt:
                return txt

        # 3) SmartArt: iterar XML buscando texto
        try:
            xml_text_parts = []
            if hasattr(shape, "_element"):
                for elem in shape._element.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    # 't' es el elemento de texto en SmartArt y otros gráficos
                    if tag == "t" and elem.text:
                        t = elem.text.strip()
                        if t:
                            xml_text_parts.append(t)
                if xml_text_parts:
                    return "\n".join(xml_text_parts)
        except Exception:
            pass

        return ""

    # ------------------------------------------------------------------
    # Heurística de slide de baja señal
    # ------------------------------------------------------------------

    @staticmethod
    def _is_low_signal_slide(title: str, text_parts: list) -> bool:
        if not text_parts:
            return True
        title_norm = (title or "").lower()
        combined = " ".join(text_parts).strip()
        if any(k in title_norm for k in _AGENDA_KEYWORDS) and len(combined) < 200:
            return True
        # Slide solo con título
        if len(combined) < 40 and bool(title):
            return True
        return False

    # ------------------------------------------------------------------
    # Tabla → texto estructurado
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_table(table) -> str:
        """Serializa tabla PPTX como 'col: val | col: val' por fila."""
        rows = []
        for row in table.rows:
            rows.append([(cell.text or "").strip() for cell in row.cells])
        rows = [r for r in rows if any(r)]
        if not rows:
            return ""
        headers = rows[0]
        if len(rows) == 1:
            return "columnas: " + ", ".join(h for h in headers if h)
        lines = []
        for row in rows[1:]:
            pairs = " | ".join(
                f"{h}: {v}"
                for h, v in zip(headers, row)
                if h and v
            )
            if pairs:
                lines.append(pairs)
        return "\n".join(lines) if lines else "columnas: " + ", ".join(h for h in headers if h)
