"""
brain/prompts/preprocessing/pptx.py
------------------------------------
Preprocesado de presentaciones Microsoft PowerPoint (.pptx).

v3 — build_focused desde bloques del extractor:
  build_focused(blocks, full_text, max_chars) usa los bloques del PptxExtractor
  que ya detecta secciones lógicas (nativas o heurística) y títulos de slide.
  Agrupa slides por sección con ## para chunking semántico correcto.

v2 — preprocess sobre full_text:
  Solo limpia boilerplate. Fallback cuando no hay bloques.
"""

from __future__ import annotations
import re as _re
from collections import Counter


def _remove_boilerplate(full_text: str) -> str:
    lines = full_text.split("\n")
    stripped = [l.strip() for l in lines]
    freq = Counter(l for l in stripped if 3 < len(l) < 80)
    boilerplate = {text for text, count in freq.items() if count >= 4}
    cleaned = []
    for line in lines:
        s = line.strip()
        if _re.match(r"^\d{1,4}$", s):
            continue
        if len(s) < 2:
            continue
        if s in boilerplate:
            continue
        cleaned.append(line)
    result = "\n".join(cleaned)
    return _re.sub(r"\n{4,}", "\n\n\n", result)


def build_focused(blocks: list[dict], full_text: str, max_chars: int) -> str:
    """
    v3 — Construye el processed_text desde los bloques del PptxExtractor.

    El PptxExtractor produce bloques con:
      - metadata.section: nombre de la sección lógica de la presentación
      - metadata.slide_title: título del slide
      - metadata.slide: número de slide (1-N)
      - content_type: text / table

    Agrupa por sección lógica con ## para que el chunker corte correctamente.

    FALLBACK DE SECCIÓN:
      Si un bloque no tiene seción nativa (presentaciones sin secciones PPTX
      definidas, como la mayoría de exportaciones sencillas), usa el número
      de slide como separador: ## Slide N: título.
      Esto garantiza que cada slide sea una unidad semántica independiente,
      evitando que el LLM reciba todas las slides como un bloque plano.

    Fallback a preprocess(full_text) si no hay bloques o son insuficientes.
    """
    if not blocks:
        return preprocess(full_text, max_chars)

    # Detectar si los bloques tienen secciones lógicas reales
    # (distintas a "Documento" o vacías) — más de 1 sección distinta
    distinct_sections = set(
        (b.get("metadata") or {}).get("section", "") or ""
        for b in blocks
    ) - {"", "Documento"}
    has_real_sections = len(distinct_sections) >= 2

    parts: list[str] = []
    current_section: str = ""
    section_parts: list[str] = []

    def _flush_section():
        nonlocal section_parts
        if section_parts:
            header = f"## {current_section}" if current_section else "## Diapositivas"
            parts.append(header + "\n" + "\n\n".join(section_parts))
            section_parts = []

    for block in blocks:
        content = block.get("content", "").strip()
        if not content:
            continue

        content_type = block.get("content_type", "text")
        meta = block.get("metadata") or {}
        section = meta.get("section", "") or ""
        slide_title = meta.get("slide_title", "") or ""
        slide_num = meta.get("slide", 0)
        is_agenda = meta.get("is_agenda", False)

        # Determinar la sección efectiva:
        # • Si hay secciones reales (PPTX con secciones definidas): usar section
        # • Si no: cada slide es su propia sección usando el número de slide
        if has_real_sections:
            effective_section = section or "Documento"
        else:
            # Sin secciones nativas: slide N como sección — FIX PRINCIPAL
            # Esto garantiza que cada slide genere un bloque ## propio
            slide_label = slide_title or f"slide_{slide_num}"
            effective_section = f"Slide {slide_num}: {slide_label}" if slide_num else section or "Documento"

        # Cambio de sección → nuevo bloque ##
        if effective_section != current_section:
            _flush_section()
            current_section = effective_section

        if content_type == "table":
            section_parts.append(content)
            continue

        # Para slides de agenda/portada: incluir pero marcar
        if is_agenda:
            if slide_title:
                section_parts.append(f"### {slide_title} [agenda]")
            continue

        # Con secciones reales: añadir ### por slide dentro de la sección
        # Sin secciones reales: el bloque ## ya es la slide, no hace falta ###
        if has_real_sections and slide_title and slide_title not in content[:100]:
            section_parts.append(f"### {slide_title}\n{content}")
        else:
            section_parts.append(content)

    _flush_section()

    result = "\n\n".join(parts)

    if len(result) < 200:
        return preprocess(full_text, max_chars)

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Fallback: preprocesado desde full_text plano (solo limpieza de boilerplate).
    """
    result = _remove_boilerplate(full_text)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result
