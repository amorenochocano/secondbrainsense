"""
brain/prompts/preprocessing/docx.py
------------------------------------
Preprocesado de documentos Microsoft Word (.docx).

v3 — build_focused desde bloques del extractor:
  build_focused(blocks, full_text, max_chars) usa los bloques ricos del
  DocxExtractor directamente. El extractor ya detecta headings reales de Word
  (Heading 1/2/3/4) y los marca con #/##/###. Todos los niveles (incluyendo
  Heading 4 = subservicios) llegan al LLM correctamente.

v2 — chunked-friendly (preprocess sobre full_text):
  Usa el TOC detectado para inyectar headers ## en el cuerpo del documento.
  Es el fallback cuando no hay bloques disponibles.
"""

from __future__ import annotations
import re as _re
from collections import Counter


_P_TAB  = _re.compile(r"^(.+?)\t(\d{1,3})\s*$")
_P_DOTS = _re.compile(r"^(.+?)\s*\.{3,}\s*(\d{1,3})\s*$")
_CLEAN_PAGE = _re.compile(r'\t\d+\s*$')
_P_PART = _re.compile(
    r"^(PARTE\s+\w+[\.\-].+|Cap[ií]tulo\s+\d+.+|Sección\s+\d+.+)",
    _re.IGNORECASE,
)


def _extract_toc_entries(full_text: str) -> list[tuple[int, str]]:
    lines = full_text.split("\n")
    toc_entries: list[tuple[int, str]] = []

    for line in lines[:200]:
        stripped = line.strip()
        if not stripped or len(stripped) < 4:
            continue

        m_part = _P_PART.match(stripped)
        if m_part and len(stripped) < 120:
            clean_title = _CLEAN_PAGE.sub("", stripped).strip().rstrip(".")
            toc_entries.append((0, clean_title))
            continue

        m_tab = _P_TAB.match(stripped)
        if m_tab:
            title = m_tab.group(1).strip().rstrip(".")
            title = _re.sub(r"^\d+\.?\t", "", title).strip()
            if len(title) > 3 and not title.isdigit():
                num_match = _re.match(r"^(\d+\.\d+)\s+(.+)", title)
                level = 1 if num_match else 0
                clean = num_match.group(2) if num_match else title
                toc_entries.append((level, clean))
            continue

        m_dots = _P_DOTS.match(stripped)
        if m_dots:
            title = m_dots.group(1).strip().rstrip(".")
            if len(title) > 3:
                level = 1 if line.startswith(("  ", "\t")) else 0
                toc_entries.append((level, title))
            continue

    return toc_entries if len(toc_entries) >= 3 else []


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


def _inject_section_headers(body: str, toc_entries: list[tuple[int, str]]) -> str:
    lines = body.split("\n")
    result_lines: list[str] = []

    def _normalize(text: str) -> str:
        return _re.sub(r"\s+", " ", text.lower().strip().rstrip("."))

    toc_normalized = [_normalize(title) for _, title in toc_entries]
    header_injected: set[int] = set()

    for li, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            result_lines.append(line)
            continue

        norm_stripped = _normalize(stripped)

        for idx, norm_title in enumerate(toc_normalized):
            if not norm_title:
                continue
            words_title = norm_title.split()[:5]
            words_line  = norm_stripped.split()[:5]
            if (norm_stripped == norm_title or
                    (len(words_title) >= 3 and words_title == words_line)):
                level = toc_entries[idx][0]
                title = toc_entries[idx][1]
                hashes = "##" if level == 0 else "###"
                if li not in header_injected:
                    result_lines.append(f"{hashes} {title}")
                    header_injected.add(li)
                break

        result_lines.append(line)

    return "\n".join(result_lines)


def build_focused(blocks: list[dict], full_text: str, max_chars: int) -> str:
    """
    v3 — Construye el processed_text desde los bloques del DocxExtractor.

    El DocxExtractor ya produce bloques con headings reales de Word marcados:
      - Heading 1 → '# Título'
      - Heading 2 → '## Título'
      - Heading 3 → '### Título'
      - Heading 4 → '#### Título'  (subservicios, subsecciones)

    Ventaja sobre preprocess(full_text): todos los niveles de heading llegan
    al LLM, incluyendo Heading 4 que no aparece en el TOC del documento.
    Las tablas también se incluyen con su contexto de sección.

    Fallback a preprocess(full_text) si no hay bloques o son insuficientes.
    """
    if not blocks:
        return preprocess(full_text, max_chars)

    parts: list[str] = []

    for block in blocks:
        content = block.get("content", "").strip()
        if not content:
            continue

        content_type = block.get("content_type", "text")

        if content_type == "table":
            parts.append(content)
            continue

        # Normalizar headings: el chunker split_doc_text_for_chunked corta en ##
        # El DocxExtractor emite '# Heading1', '## Heading2', '### Heading3', etc.
        # Convertimos un nivel hacia abajo para que ## sea el separador de chunk:
        #   '#  Título'   → '## Título'   (Heading 1 → separador de chunk)
        #   '## Título'   → '### Título'  (Heading 2 → subsección)
        #   '### Título'  → '#### Título' (Heading 3 → sub-subsección)
        #   '#### Título' → queda como #### (Heading 4 visible en el texto)
        lines = content.split("\n")
        converted: list[str] = []
        for line in lines:
            if _re.match(r'^#{1,3} [^#]', line):
                converted.append('#' + line)  # añadir un # más
            else:
                converted.append(line)
        parts.append("\n".join(converted))

    result = "\n\n".join(parts)

    if len(result) < 200:
        return preprocess(full_text, max_chars)

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Fallback: preprocesado desde full_text plano cuando no hay bloques.
    Detecta TOC e inyecta headers ## alineados con el índice del documento.
    """
    body = _remove_boilerplate(full_text)
    toc_entries = _extract_toc_entries(full_text)

    if toc_entries:
        toc_lines = ["## Índice del documento"]
        for level, title in toc_entries[:50]:
            toc_lines.append(("  " * level) + f"- {title}")
        toc_block = "\n".join(toc_lines)

        body_with_headers = _inject_section_headers(body, toc_entries)

        toc_budget  = min(len(toc_block), max_chars // 6)
        body_budget = max_chars - toc_budget - 4
        result = toc_block[:toc_budget] + "\n\n" + body_with_headers[:body_budget]
    else:
        result = body

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result
