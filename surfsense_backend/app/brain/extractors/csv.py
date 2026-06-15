"""
csv.py — Extractor para ficheros CSV.

v2 — Mejoras respecto a v1:
  - PROFILE + DATA: v1 solo emitía 1 bloque con perfil de columnas. Para
    búsqueda RAG necesitas que las filas también sean indexables.
    v2 emite 1 bloque 'text' con perfil (igual que antes) + N bloques 'table'
    con chunks de datos (~50 filas cada uno) para búsqueda granular.
  - Detección de fechas mejorada: además de números, identifica columnas
    de fecha por patrones (YYYY-MM-DD, DD/MM/YYYY) → mejor `kind` en perfil.
"""
import csv
import logging
import re
from pathlib import Path

from .base import BaseExtractor

log = logging.getLogger(__name__)

# Tamaño de chunk para bloques de datos
_DATA_CHUNK_SIZE = 50
# Máximo de chunks de datos a emitir (evita explosión en CSVs gigantes)
_MAX_DATA_CHUNKS = 20

# Patrón para detectar fechas en columnas
_DATE_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"   # YYYY-MM-DD
    r"|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"  # DD-MM-YYYY o DD/MM/YYYY
)


class CsvExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        path = Path(source)
        is_temp_name = path.name.lower().startswith("tmp")
        display_name = path.name if not is_temp_name else ""
        raw = path.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            log.warning("[csv] '%s' vacio", source)
            return [{"page": 1, "text": "", "content": "", "content_type": "text"}]

        sample = raw[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample)
            delimiter = dialect.delimiter
        except Exception:
            delimiter = ","

        rows = []
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                rows.append([str(cell).strip() for cell in row])

        if not rows:
            return [{"page": 1, "text": "", "content": "", "content_type": "text"}]

        headers = rows[0]
        data_rows = rows[1:] if len(rows) > 1 else []

        col_profiles = self._profile_columns(headers, data_rows)
        preview_rows = data_rows[:5]
        preview_lines = ["\t".join(r[:20]) for r in preview_rows]

        profile_content = (
            f"# CSV Dataset{f': {display_name}' if display_name else ''}\n"
            f"delimiter: '{delimiter}'\n"
            f"columns: {', '.join(headers[:40]) if headers else 'n/a'}\n"
            f"row_count: {len(data_rows)}\n"
            f"column_count: {len(headers)}\n"
            "\n"
            "## Column profile\n"
            + ("\n".join(col_profiles[:40]) if col_profiles else "- n/a")
            + "\n\n"
            "## Sample rows\n"
            + ("\n".join(preview_lines) if preview_lines else "- n/a")
        ).strip()

        blocks = [{
            "content": profile_content,
            "content_type": "text",
            "page": 1,
            "text": profile_content,
            "metadata": {
                "format": "csv",
                "delimiter": delimiter,
                "columns": headers[:100],
                "row_count": len(data_rows),
                "column_count": len(headers),
                "section": "profile",
            },
        }]

        # ── Chunks de datos para búsqueda granular ─────────────────────
        # Sin esto, el RAG solo encuentra el perfil; las filas individuales
        # no son recuperables. Se serializa cada fila como 'col: val | col: val'.
        for chunk_idx, chunk_start in enumerate(
            range(0, len(data_rows), _DATA_CHUNK_SIZE)
        ):
            if chunk_idx >= _MAX_DATA_CHUNKS:
                log.debug("[csv] '%s' → max chunks alcanzado (%d), saltando resto",
                          source, _MAX_DATA_CHUNKS)
                break
            chunk = data_rows[chunk_start: chunk_start + _DATA_CHUNK_SIZE]
            lines = [f"### Filas {chunk_start + 1}-{chunk_start + len(chunk)} de {len(data_rows)}"]
            for row in chunk:
                pairs = " | ".join(
                    f"{h}: {v}"
                    for h, v in zip(headers, row)
                    if h and v
                )
                if pairs:
                    lines.append(pairs)
            chunk_text = "\n".join(lines)
            if len(lines) > 1:
                blocks.append({
                    "content": chunk_text,
                    "content_type": "table",
                    "page": chunk_idx + 2,
                    "text": chunk_text,
                    "metadata": {
                        "format": "csv",
                        "section": f"data_chunk_{chunk_idx + 1}",
                        "row_start": chunk_start + 1,
                        "row_end": chunk_start + len(chunk),
                        "columns": headers[:100],
                    },
                })

        log.debug("[csv] '%s' -> rows=%d cols=%d chunks=%d",
                  source, len(data_rows), len(headers), len(blocks) - 1)
        return blocks

    @staticmethod
    def _profile_columns(headers: list[str], data_rows: list[list[str]]) -> list[str]:
        profiles = []
        row_count = len(data_rows)
        for idx, col in enumerate(headers[:120]):
            values: list[str] = []
            non_empty = 0
            numeric = 0
            date_like = 0
            for r in data_rows:
                value = r[idx].strip() if idx < len(r) and r[idx] is not None else ""
                if value:
                    non_empty += 1
                    values.append(value)
                    try:
                        float(value.replace(",", "."))
                        numeric += 1
                        continue
                    except Exception:
                        pass
                    if _DATE_RE.match(value):
                        date_like += 1

            if values and numeric >= max(1, int(0.8 * len(values))):
                kind = "numeric"
            elif values and date_like >= max(1, int(0.8 * len(values))):
                kind = "date"
            else:
                kind = "categorical"

            fill_ratio = (non_empty / row_count * 100.0) if row_count else 0.0
            examples = ", ".join(values[:3]) if values else "n/a"
            profiles.append(
                f"- {col or f'column_{idx+1}'}: type={kind}, "
                f"non_empty={non_empty}/{row_count} ({fill_ratio:.1f}%), "
                f"sample=[{examples}]"
            )
        return profiles
