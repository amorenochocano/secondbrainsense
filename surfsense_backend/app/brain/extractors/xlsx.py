import logging
from .base import BaseExtractor
import openpyxl

log = logging.getLogger(__name__)

# Máximo de filas de datos por sheet (sin contar cabecera)
_MAX_ROWS = 200
# Tamaño de chunk por bloque
_CHUNK_SIZE = 20


class XlsxExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        blocks = []
        for ws_idx, ws in enumerate(wb.worksheets, 1):
            rows = []
            for row in ws.iter_rows(values_only=True):
                values = [str(cell).strip() if cell is not None else "" for cell in row]
                # Saltar filas completamente vacías
                if any(v for v in values):
                    rows.append(values)

            if not rows:
                continue

            headers = [h for h in rows[0][:20] if h]
            data_rows = rows[1: _MAX_ROWS + 1]

            # Un bloque por chunk de _CHUNK_SIZE filas
            for chunk_start in range(0, max(len(data_rows), 1), _CHUNK_SIZE):
                chunk = data_rows[chunk_start: chunk_start + _CHUNK_SIZE]
                lines = [f"# Sheet: {ws.title}"]
                if chunk_start == 0:
                    lines.append(f"columns: {', '.join(headers) if headers else 'n/a'}")
                    lines.append(f"total_rows: {len(data_rows)}")

                for row in chunk:
                    pairs = " | ".join(
                        f"{h}: {v}"
                        for h, v in zip(headers, row)
                        if h and v
                    )
                    if pairs:
                        lines.append(pairs)

                sheet_text = "\n".join(lines).strip()
                if not sheet_text or sheet_text == f"# Sheet: {ws.title}":
                    continue

                blocks.append({
                    "content": sheet_text,
                    "content_type": "table",
                    "page": ws_idx,
                    "text": sheet_text,
                    "metadata": {
                        "format": "xlsx",
                        "sheet": ws.title,
                        "columns": headers,
                        "row_count": len(data_rows),
                    },
                })

        if not blocks:
            log.warning("[xlsx] '%s' → workbook vacío o sin datos", source)
            return [{"page": 1, "text": "", "content": "", "content_type": "text"}]
        log.debug("[xlsx] '%s' → %d bloques", source, len(blocks))
        return blocks
