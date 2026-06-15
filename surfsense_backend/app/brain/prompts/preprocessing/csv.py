"""
brain/prompts/preprocessing/csv.py
-----------------------------------
Preprocesado de ficheros CSV para síntesis del pasaporte semántico.

Produce texto estructurado con:
  - Catálogo de columnas con tipo y estadísticas básicas
  - Muestra de filas representativa
  - Claves y relaciones inferidas

No vectoriza los datos — eso lo hace el extractor CsvExtractor.
"""

from __future__ import annotations
import re as _re


_DATE_RE = _re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
)


def _detect_kind(values: list[str]) -> str:
    if not values:
        return "empty"
    numeric = date_like = 0
    for v in values:
        try:
            float(v.replace(",", "."))
            numeric += 1
            continue
        except ValueError:
            pass
        if _DATE_RE.match(v):
            date_like += 1
    threshold = max(1, int(0.7 * len(values)))
    if numeric >= threshold:
        return "numeric"
    if date_like >= threshold:
        return "date"
    return "categorical"


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Para CSV: produce catálogo de columnas + tipos + muestra.
    Chunked-friendly: cada columna es una línea con ### si hay muchas.
    """
    lines = [l for l in full_text.split("\n") if l.strip()]
    if not lines:
        return full_text[:max_chars]

    # Detectar delimitador
    first = lines[0]
    delimiter = ","
    for d in ("\t", ";", "|"):
        if d in first:
            delimiter = d
            break

    rows = [l.split(delimiter) for l in lines]
    headers = [h.strip().strip('"').strip("'") for h in rows[0]]
    data_rows = rows[1:] if len(rows) > 1 else []

    # Perfil por columna
    output: list[str] = []
    output.append(f"## Catálogo de columnas ({len(headers)} cols, {len(data_rows)} filas)")
    output.append("")

    for idx, col in enumerate(headers[:60]):
        values = [
            r[idx].strip().strip('"').strip("'")
            for r in data_rows[:200]
            if idx < len(r) and r[idx].strip()
        ]
        kind = _detect_kind(values)
        non_empty = len(values)
        fill = f"{non_empty}/{min(len(data_rows), 200)}"
        sample = ", ".join(values[:3]) if values else "—"

        # Si hay muchas columnas, cada una con ### para que el chunker las separe
        if len(headers) > 20:
            output.append(f"### Columna `{col}` ({kind})")
            output.append(f"- Completitud: {fill}")
            output.append(f"- Muestra: {sample}")
            output.append("")
        else:
            output.append(f"- **{col}** (`{kind}`): completitud={fill}, muestra=[{sample}]")

    # Claves y relaciones inferidas
    output.append("")
    output.append("## Claves y relaciones")
    id_cols = [h for h in headers if _re.search(r"\bid\b|_id$|^id_", h.lower())]
    date_cols = [h for h in headers[:60] if _re.search(r"fecha|date|time|timestamp", h.lower())]
    if id_cols:
        output.append(f"- Clave candidata: {', '.join(id_cols[:3])}")
    if date_cols:
        output.append(f"- Columnas temporales: {', '.join(date_cols[:3])}")
    if not id_cols and not date_cols:
        output.append("- Sin claves explícitas detectadas")

    result = "\n".join(output)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result


def build_focused(full_text: str, max_chars: int) -> str:
    """Alias — para CSV el focused es igual que preprocess."""
    return preprocess(full_text, max_chars)
