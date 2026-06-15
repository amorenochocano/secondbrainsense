"""
brain/prompts/preprocessing/ipynb.py
-------------------------------------
Preprocesado de Jupyter Notebooks (.ipynb).

v2 — chunked-friendly:
  Cada par (celda_markdown + celda_código) va precedido de ### `nombre()`
  extraído de la firma de la función documentada.
  Si no hay función identificable, usa el título del markdown.

Salida:
  ### `funcion_a()`
  [celda markdown que la documenta]
  ```python
  def funcion_a(...):
  ```

  ### `funcion_b()`
  [celda markdown]
  ```python
  def funcion_b(...):
  ```
"""

from __future__ import annotations
import re as _re


def preprocess(full_text: str, max_chars: int) -> str:
    """Sin blocks: delega en py.py (trata el notebook como código Python)."""
    from .py import preprocess as _py_preprocess
    return _py_preprocess(full_text, max_chars)


def build_focused(blocks: list[dict], full_text: str, max_chars: int) -> str:
    """
    Con blocks: empareja celdas markdown+código y añade ### por función.
    Cada par queda como un bloque semántico independiente para el chunker.
    """
    sections: list[str] = []
    seen_code_indices: set[int] = set()
    i = 0

    while i < len(blocks):
        b = blocks[i]
        is_md   = b.get("content_type") == "text" and b.get("content", "").strip()
        is_code = b.get("content_type") == "code"

        if is_md:
            md_content = b["content"].strip()
            next_b = blocks[i + 1] if i + 1 < len(blocks) else None

            if next_b and next_b.get("content_type") == "code":
                code_content = next_b.get("content", "")
                seen_code_indices.add(i + 1)

                # Extraer firmas y nombre de función
                sig_lines: list[str] = []
                func_name: str | None = None
                for ln in code_content.split("\n"):
                    s = ln.strip()
                    m = _re.match(r"^def\s+([A-Za-z][A-Za-z0-9_]*)\s*\(", s)
                    if m and not func_name:
                        func_name = m.group(1)
                    if any(s.startswith(p) for p in ("def ", "class ", "@")):
                        sig_lines.append(ln)
                    elif s.startswith('"""') or s.startswith("'''"):
                        sig_lines.append(ln)

                # Header ### basado en el nombre de la función o en el markdown
                if func_name:
                    header = f"### `{func_name}()`"
                else:
                    # Usar primera línea del markdown como header
                    first_line = md_content.split("\n")[0].lstrip("#").strip()
                    header = f"### {first_line[:60]}" if first_line else "### [bloque]"

                block_parts = [header, md_content]
                if sig_lines:
                    block_parts.append("```python")
                    block_parts.extend(sig_lines)
                    block_parts.append("```")

                sections.append("\n".join(block_parts))
                i += 2
            else:
                # Celda markdown sin código siguiente
                first_line = md_content.split("\n")[0].lstrip("#").strip()
                header = f"### {first_line[:60]}" if first_line else "### [nota]"
                sections.append(f"{header}\n{md_content}")
                i += 1

        elif is_code and i not in seen_code_indices:
            # Celda código sin markdown previo
            code_content = b.get("content", "")
            sig_lines = []
            func_name = None
            for ln in code_content.split("\n"):
                s = ln.strip()
                m = _re.match(r"^def\s+([A-Za-z][A-Za-z0-9_]*)\s*\(", s)
                if m and not func_name:
                    func_name = m.group(1)
                if any(s.startswith(p) for p in ("def ", "class ", "@")):
                    sig_lines.append(ln)
                elif s.startswith('"""') or s.startswith("'''"):
                    sig_lines.append(ln)
            if sig_lines:
                header = f"### `{func_name}()`" if func_name else "### [función]"
                block = f"{header}\n```python\n" + "\n".join(sig_lines) + "\n```"
                sections.append(block)
            i += 1
        else:
            i += 1

    result = "\n\n".join(sections)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result or preprocess(full_text, max_chars)
