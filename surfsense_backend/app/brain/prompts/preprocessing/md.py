"""
brain/prompts/preprocessing/md.py
----------------------------------
Preprocesado de ficheros Markdown (.md).

v2 — chunked-friendly:
  Los .md con headers propios (## / ###) ya son chunked-friendly.
  Para .md sin estructura (notas informales), añade ## por párrafos temáticos.
"""

from __future__ import annotations
import re as _re


_PASSPORT_HEADING_RE = _re.compile(
    r"^#+\s+(?:📌|📄|🧩|🧠|🔗|⚙️|⚠️)\s.*$",
    _re.MULTILINE,
)
_HAS_HEADERS_RE = _re.compile(r"^#{1,3}\s+\S", _re.MULTILINE)


def preprocess(full_text: str, max_chars: int) -> str:
    # 1. Quitar frontmatter
    text = _re.sub(r"^---\s*\n.*?\n---\s*\n?", "", full_text, count=1, flags=_re.DOTALL)

    # 2. Si es pasaporte existente, quitar headings de secciones estándar
    if len(_PASSPORT_HEADING_RE.findall(text)) >= 3:
        text = _PASSPORT_HEADING_RE.sub("", text)

    # 3. Colapsar líneas vacías múltiples
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()

    # 4. Si ya tiene headers propios → chunked-friendly, no tocar
    if _HAS_HEADERS_RE.search(text):
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[... truncado ...]"
        return text

    # 5. Sin headers: añadir ## por párrafo temático largo
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    result_parts: list[str] = []
    section_idx = 1

    for para in paragraphs:
        if len(para) > 100:
            # Usar la primera frase como título de sección
            first_sentence = para.split(".")[0].strip()[:60]
            result_parts.append(f"## Sección {section_idx}: {first_sentence}\n{para}")
            section_idx += 1
        else:
            result_parts.append(para)

    result = "\n\n".join(result_parts)
    if len(result) > max_chars:
        return result[:max_chars] + "\n\n[... truncado ...]"
    return result
