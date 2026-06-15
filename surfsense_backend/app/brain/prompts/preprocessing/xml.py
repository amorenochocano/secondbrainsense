"""
brain/prompts/preprocessing/xml.py
-----------------------------------
Preprocesado de ficheros XML.

v2 — chunked-friendly:
  ### Elemento `<nombre>` por cada hijo directo del root.
  Si detecta drawio → delega en drawio.py.
"""

from __future__ import annotations
import re as _re


def preprocess(full_text: str, max_chars: int) -> str:
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(full_text.strip())

        tag_local = root.tag.split("}")[-1].lower()
        if tag_local in ("mxfile", "mxgraphmodel", "diagram"):
            from .drawio import preprocess as drawio_preprocess
            return drawio_preprocess(full_text, max_chars, root=root)

        SKIP_ATTRS = {"style", "class", "id", "xmlns", "version"}
        lines = [f"## Elemento raíz `<{root.tag.split('}')[-1]}>` ({len(root)} hijos)"]
        lines.append("")

        for child in root:
            tag = child.tag.split("}")[-1]
            attrs = {k: v for k, v in child.attrib.items()
                     if k.lower() not in SKIP_ATTRS}
            text = (child.text or "").strip()[:100]

            lines.append(f"### Elemento `<{tag}>`")
            if attrs:
                for k, v in list(attrs.items())[:4]:
                    lines.append(f"- **{k}**: {v}")
            if text:
                lines.append(f"- **Contenido**: {text}")

            # Hijos del hijo (nivel 2)
            child_tags = [c.tag.split("}")[-1] for c in child]
            if child_tags:
                lines.append(f"- **Hijos**: {', '.join(child_tags[:10])}")

            lines.append("")

        result = "\n".join(lines)
    except Exception:
        result = _re.sub(r'<[^>]+>', ' ', full_text)
        result = _re.sub(r'\s{3,}', '\n', result).strip()

    return result[:max_chars] + "\n\n[... truncado ...]" if len(result) > max_chars else result


def build_focused(full_text: str, max_chars: int) -> str:
    """Con comentarios XML embebidos primero, luego estructura."""
    comments = _re.findall(r"<!--(.*?)-->", full_text, _re.DOTALL)
    comment_text = "\n".join(c.strip() for c in comments if c.strip())
    structure = preprocess(full_text, max_chars // 2)
    parts = []
    if comment_text:
        parts.append("## Comentarios\n" + comment_text)
    parts.append(structure)
    result = "\n\n".join(parts)
    return result[:max_chars] + "\n\n[... truncado ...]" if len(result) > max_chars else result
