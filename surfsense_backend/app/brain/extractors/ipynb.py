"""
ipynb.py
--------
Extractor para Jupyter Notebooks (.ipynb).
Parsea el JSON del notebook y extrae celdas de código y markdown como texto.

Para celdas de código:
  - Si la celda contiene definiciones de funciones/clases top-level, genera un
    bloque por función/clase (mismo enfoque que PythonExtractor con AST).
  - Si la celda es código suelto sin definiciones top-level (imports, sentencias,
    asignaciones), genera un bloque con function=None.
"""
import ast
import json
import logging
import re
from pathlib import Path
from .base import BaseExtractor

log = logging.getLogger(__name__)

# Control characters that commonly break JSON in notebook outputs
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Elimina bloques "outputs": [...] de las celdas (incluyendo contenido anidado)
# para recuperar notebooks con outputs binarios/corruptos.
_OUTPUTS_RE = re.compile(
    r'"outputs"\s*:\s*\[(?:[^\[\]]|\[(?:[^\[\]]|\[(?:[^\[\]]|\[[^\[\]]*\])*\])*\])*\]',
    re.DOTALL,
)
# Elimina bloques "execution_count": <valor> que pueden quedar huérfanos
_EXEC_COUNT_RE = re.compile(r'"execution_count"\s*:\s*(?:\d+|null)')


def _strip_outputs(raw: str) -> str:
    """Elimina los outputs de las celdas y deja solo source/metadata."""
    stripped = _OUTPUTS_RE.sub('"outputs": []', raw)
    return stripped


def _parse_notebook_json(raw: str, source: str) -> tuple[dict, str, bool]:
    """
    Intenta parsear el JSON de un notebook con múltiples estrategias de
    recuperación para notebooks con outputs corruptos o caracteres de control.
    """
    # 1. Intento estricto normal
    try:
        return json.loads(raw), "normal", False
    except json.JSONDecodeError:
        pass

    # 2. Modo no-estricto: permite caracteres de control literales en strings
    try:
        return json.loads(raw, strict=False), "no_strict", False
    except json.JSONDecodeError:
        pass

    # 3. Sanear caracteres de control (excepto \n, \r, \t) y reintentar
    cleaned = _CTRL_CHARS_RE.sub("", raw)
    try:
        return json.loads(cleaned, strict=False), "sanitized", False
    except json.JSONDecodeError:
        pass

    # 4. JSON con datos extra después del primer objeto válido
    try:
        notebook, _ = json.JSONDecoder().raw_decode(raw.lstrip())
        log.warning("[ipynb] '%s' → JSON con datos extra; se usa solo el primer objeto válido", source)
        return notebook, "raw_decode", False
    except json.JSONDecodeError:
        pass

    try:
        notebook, _ = json.JSONDecoder().raw_decode(cleaned.lstrip())
        log.warning("[ipynb] '%s' → JSON saneado con datos extra; se usa solo el primer objeto válido", source)
        return notebook, "raw_decode_sanitized", False
    except json.JSONDecodeError:
        pass

    # 5. Eliminar outputs de celdas (pueden contener datos binarios/surrogates)
    stripped = _strip_outputs(raw)
    stripped_cleaned = _CTRL_CHARS_RE.sub("", stripped)
    try:
        notebook = json.loads(stripped_cleaned, strict=False)
        log.warning("[ipynb] '%s' → parseado tras eliminar outputs corruptos; "
                    "solo se indexa source de celdas", source)
        return notebook, "stripped", True
    except json.JSONDecodeError:
        pass

    try:
        notebook, _ = json.JSONDecoder().raw_decode(stripped_cleaned.lstrip())
        log.warning("[ipynb] '%s' → parseado (raw_decode) tras eliminar outputs; "
                    "solo se indexa source de celdas", source)
        return notebook, "stripped_raw_decode", True
    except json.JSONDecodeError as exc:
        raise ValueError(f"No se puede parsear el notebook '{source}': {exc}") from exc


def _fallback_text_blocks(raw: str, source: str) -> list[dict]:
    """
    Fallback de último recurso: trata el .ipynb como texto plano y extrae
    las líneas de 'source' mediante regex sin parsear el JSON.
    Genera bloques de texto de ~100 líneas cada uno.
    """
    log.warning("[ipynb] '%s' → FALLBACK texto plano (JSON irrecuperable)", source)

    # Intentar extraer strings de 'source' con regex (formato JSON parcialmente válido)
    source_re = re.compile(r'"source"\s*:\s*\[(.*?)\]', re.DOTALL)
    chunks = []
    for m in source_re.finditer(raw):
        # Extraer strings individuales dentro del array
        str_re = re.compile(r'"((?:[^"\\]|\\.)*)"')
        lines = [s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
                 for s in str_re.findall(m.group(1))]
        text = "".join(lines).strip()
        if text:
            chunks.append(text)

    if not chunks:
        # Si ni siquiera eso funciona, indexar el texto crudo truncado
        chunks = [raw[:8000]]

    # Agrupar en bloques de ~50 líneas para no sobrecargar el chunker
    blocks = []
    buffer, buf_lines = [], 0
    for chunk in chunks:
        chunk_lines = chunk.count("\n") + 1
        if buf_lines + chunk_lines > 50 and buffer:
            text = "\n".join(buffer)
            blocks.append({
                "content": text,
                "content_type": "text",
                "page": len(blocks) + 1,
                "metadata": {"cell_type": "fallback_text", "parse_error": True},
            })
            buffer, buf_lines = [], 0
        buffer.append(chunk)
        buf_lines += chunk_lines

    if buffer:
        text = "\n".join(buffer)
        blocks.append({
            "content": text,
            "content_type": "text",
            "page": len(blocks) + 1,
            "metadata": {"cell_type": "fallback_text", "parse_error": True},
        })

    log.info("[ipynb] '%s' → fallback generó %d bloques de texto", source, len(blocks))
    return blocks


class IpynbExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        """
        Lee un fichero .ipynb, extrae las celdas de código y markdown.
        Devuelve bloques tipados para IngestRouter: code/text.
        Si el JSON no es parseable por ninguna estrategia, hace fallback
        a extracción de texto plano mediante regex (siempre ingesta algo).
        """
        with open(source, "r", encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()

        try:
            notebook, parse_strategy, outputs_stripped = _parse_notebook_json(raw, source)
        except ValueError:
            # JSON irrecuperable → fallback texto plano
            return _fallback_text_blocks(raw, source)

        module_name = Path(source).stem
        cells = notebook.get("cells", [])
        blocks = []

        idx = 1
        while idx <= len(cells):
            cell = cells[idx - 1]
            cell_type = cell.get("cell_type", "")
            source_lines = cell.get("source", [])
            text = "".join(source_lines).strip()

            if not text:
                idx += 1
                continue

            if cell_type == "code":
                code_blocks = self._extract_code_blocks(
                    text,
                    module_name,
                    cell_index=idx,
                    total_cells=len(cells),
                    parse_strategy=parse_strategy,
                    outputs_stripped=outputs_stripped,
                    paired_markdown="",
                )
                blocks.extend(code_blocks)
            elif cell_type == "markdown":
                next_cell = cells[idx] if idx < len(cells) else None
                next_type = next_cell.get("cell_type", "") if isinstance(next_cell, dict) else ""
                if self._should_pair_with_next(text, next_type):
                    next_text = "".join(next_cell.get("source", [])).strip() if isinstance(next_cell, dict) else ""
                    if next_text:
                        code_blocks = self._extract_code_blocks(
                            next_text,
                            module_name,
                            cell_index=idx + 1,
                            total_cells=len(cells),
                            parse_strategy=parse_strategy,
                            outputs_stripped=outputs_stripped,
                            paired_markdown=text,
                        )
                        blocks.extend(code_blocks)
                        idx += 2
                        continue
                blocks.append({
                    "content": text,
                    "content_type": "text",
                    "page": idx,
                    "metadata": {
                        "cell_type": "markdown",
                        "cell_index": idx,
                        "total_cells": len(cells),
                        "parse_strategy": parse_strategy,
                        "outputs_stripped": outputs_stripped,
                    }
                })
            else:
                # Otras celdas (raw, etc.) como texto plano
                blocks.append({
                    "content": text,
                    "content_type": "text",
                    "page": idx,
                    "metadata": {
                        "cell_type": cell_type,
                        "cell_index": idx,
                        "total_cells": len(cells),
                        "parse_strategy": parse_strategy,
                        "outputs_stripped": outputs_stripped,
                    }
                })
            idx += 1

        if not blocks:
            log.warning("[ipynb] '%s' → notebook vacío (sin celdas con contenido)", source)
            return [{"content": "(notebook vacío)", "content_type": "text", "page": 1, "metadata": {}}]
        log.debug("[ipynb] '%s' → %d bloques (%d celdas)", source, len(blocks), len(cells))
        return blocks

    @staticmethod
    def _should_pair_with_next(markdown_text: str, next_cell_type: str) -> bool:
        if next_cell_type != "code":
            return False
        if len(markdown_text) > 300:
            return False
        return True

    @staticmethod
    def _as_comment_prefix(markdown_text: str) -> str:
        if not markdown_text:
            return ""
        lines = [line.strip() for line in markdown_text.splitlines() if line.strip()]
        if not lines:
            return ""
        return "\n".join(f"# {line}" for line in lines)

    def _extract_code_blocks(
        self,
        cell_code: str,
        module_name: str,
        cell_index: int,
        total_cells: int,
        parse_strategy: str,
        outputs_stripped: bool,
        paired_markdown: str,
    ) -> list[dict]:
        """
        Analiza una celda de código con AST.
        - Si hay funciones/clases top-level: un bloque por cada una (con function=nombre).
        - Si no hay definiciones top-level (código suelto): un bloque con function=None.
        """
        try:
            tree = ast.parse(cell_code)
        except SyntaxError:
            # Celdas con magia IPython (%magic, !cmd) u otro código no parseable por AST
            prefix = self._as_comment_prefix(paired_markdown)
            content = f"{prefix}\n\n{cell_code}".strip() if prefix else cell_code
            return [{
                "content": content,
                "content_type": "code",
                "language": "python",
                "page": cell_index,
                "module": module_name,
                "function": None,
                "metadata": {
                    "cell_type": "code+markdown" if paired_markdown else "code",
                    "cell_index": cell_index,
                    "total_cells": total_cells,
                    "parse_error": True,
                    "parse_strategy": parse_strategy,
                    "outputs_stripped": outputs_stripped,
                    "has_paired_markdown": bool(paired_markdown),
                    "paired_markdown": paired_markdown,
                },
            }]

        lines = cell_code.splitlines()
        func_blocks = []
        md_prefix = self._as_comment_prefix(paired_markdown)

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno - 1
                while start > 0 and lines[start - 1].strip().startswith("@"):
                    start -= 1
                end = node.end_lineno
                chunk_code = "\n".join(lines[start:end])
                if md_prefix:
                    chunk_code = f"{md_prefix}\n\n{chunk_code}"
                func_blocks.append({
                    "content": chunk_code,
                    "content_type": "code",
                    "language": "python",
                    "page": cell_index,
                    "module": module_name,
                    "function": node.name,
                    "metadata": {
                        "cell_type": "code+markdown" if paired_markdown else "code",
                        "cell_index": cell_index,
                        "total_cells": total_cells,
                        "type": type(node).__name__,
                        "line_start": start + 1,
                        "line_end": node.end_lineno,
                        "parse_strategy": parse_strategy,
                        "outputs_stripped": outputs_stripped,
                        "has_paired_markdown": bool(paired_markdown),
                        "paired_markdown": paired_markdown,
                    },
                })

        if func_blocks:
            return func_blocks

        # Celda sin definiciones top-level (imports, sentencias, asignaciones, etc.)
        content = f"{md_prefix}\n\n{cell_code}".strip() if md_prefix else cell_code
        return [{
            "content": content,
            "content_type": "code",
            "language": "python",
            "page": cell_index,
            "module": module_name,
            "function": None,
            "metadata": {
                "cell_type": "code+markdown" if paired_markdown else "code",
                "cell_index": cell_index,
                "total_cells": total_cells,
                "parse_strategy": parse_strategy,
                "outputs_stripped": outputs_stripped,
                "has_paired_markdown": bool(paired_markdown),
                "paired_markdown": paired_markdown,
            },
        }]
