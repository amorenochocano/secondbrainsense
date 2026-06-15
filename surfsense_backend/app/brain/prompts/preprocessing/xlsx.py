"""
brain/prompts/preprocessing/xlsx.py
------------------------------------
Preprocesado de hojas de cálculo Microsoft Excel (.xlsx).

Casuística típica:
  - Modelo de datos: múltiples hojas relacionadas
  - Catálogo: una hoja con datos tabulares
  - Informe: hojas con fórmulas
  - Configuración: parametrización en hojas

API:
  preprocess(full_text, max_chars)             → str
      Sin blocks: trata como CSV plano.

  build_focused(blocks, full_text, max_chars)  → str
      Con blocks del extractor (uno por hoja): describe cada hoja individualmente.
"""

from __future__ import annotations

import re as _re


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Sin blocks del extractor: trata el XLSX como un único CSV.
    Reutiliza la lógica de csv.py.
    """
    from .csv import preprocess as csv_preprocess
    return csv_preprocess(full_text, max_chars)


def build_focused(blocks: list[dict], full_text: str, max_chars: int) -> str:
    """
    Con blocks: describe cada hoja por separado.

    Cada block típico de XLSX:
      {"content_type": "table", "metadata": {"sheet_name": "Hoja1"}, "content": "csv-like"}

    Output:
      ## Hojas (N)

      ### Hoja `Nombre` (M columnas)
      Columnas: col1, col2, ...
      Muestra:
      [4 primeras líneas]

      ### Hoja `Nombre2` ...
    """
    if not blocks:
        return preprocess(full_text, max_chars)

    sheets: list[tuple[str, str]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        content = b.get("content", "").strip()
        if not content:
            continue
        meta = b.get("metadata", {}) or {}
        sheet_name = meta.get("sheet_name") or meta.get("sheet") or f"Hoja {len(sheets) + 1}"
        sheets.append((sheet_name, content))

    if not sheets:
        return preprocess(full_text, max_chars)

    parts = [f"## Hojas ({len(sheets)})"]
    for sheet_name, content in sheets:
        lines = content.split("\n")
        header = lines[0] if lines else ""
        columns = [c.strip().strip('"') for c in header.split(",")]
        sample = lines[:4]
        parts.append(f"\n### Hoja `{sheet_name}` ({len(columns)} columnas)")
        parts.append("Columnas: " + ", ".join(columns[:20]))
        parts.append("Muestra:")
        parts.append("\n".join(sample))

    result = "\n".join(parts)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result
