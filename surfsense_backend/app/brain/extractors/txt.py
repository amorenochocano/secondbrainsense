"""
txt.py — Extractor para ficheros .txt.

v2 — Mejora respecto a v1:
  - SECCIONES SEPARADAS: v1 emitía 1 solo bloque con todo el documento (summary +
    candidate headings + 2500 chars de muestra). Eso significaba que un .txt de
    100KB con 10 capítulos perdía 97KB y todo se buscaba como un solo chunk.
    v2 detecta secciones por headings tipo "CAPÍTULO N", "N.M Título", líneas
    en mayúsculas, y emite UN bloque por sección con su contenido completo.
  - Bloque 'metadata' opcional con summary general al inicio (para que el
    pasaporte tenga visión global) + N bloques 'text' con cada sección.
"""
import logging
import re
from pathlib import Path

from .base import BaseExtractor

log = logging.getLogger(__name__)

# Patrones para detectar headings en texto plano
_HEADING_PATTERNS = [
    # CAPÍTULO N, SECCIÓN N, PARTE N, MÓDULO N (en mayúsculas, opcionalmente con :)
    re.compile(r"^(?:CAP[IÍ]TULO|SECCI[OÓ]N|PARTE|M[OÓ]DULO|CHAPTER|SECTION|PART)\s+[\dIVX]+",
               re.IGNORECASE),
    # N. Título, N.M Título, N.M.K Título
    re.compile(r"^\d+(?:\.\d+){0,2}\.?\s+[A-ZÁÉÍÓÚÑa-záéíóúñ]"),
    # Línea completamente en mayúsculas (>=4 chars, sin pasarse de 100)
    re.compile(r"^[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s:,.\-]{3,99}$"),
]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return False
    for pat in _HEADING_PATTERNS:
        if pat.match(stripped):
            return True
    return False


class TxtExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        path = Path(source)
        is_temp_name = path.name.lower().startswith("tmp")
        display_name = path.name if not is_temp_name else ""
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = raw.replace("\r\n", "\n").replace("\r", "\n")

        if not text.strip():
            log.warning("[txt] '%s' vacio", source)
            return [{"page": 1, "text": "", "content": "", "content_type": "text"}]

        lines = text.split("\n")
        non_empty_lines = [ln for ln in lines if ln.strip()]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

        # ── Detectar secciones ─────────────────────────────────────────
        section_headings: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if _is_heading(line):
                section_headings.append((idx, line.strip()))

        # ── Bloque 1: summary general ──────────────────────────────────
        blocks: list[dict] = []
        sample = "\n".join(paragraphs[:2]) if paragraphs else "\n".join(non_empty_lines[:8])

        summary = (
            f"# Text File{f': {display_name}' if display_name else ''}\n"
            f"lines: {len(non_empty_lines)}\n"
            f"paragraphs: {len(paragraphs)}\n"
            f"sections: {len(section_headings)}\n"
            "\n"
            "## Section headings\n"
            + ("\n".join(f"- {h}" for _, h in section_headings[:20]) if section_headings else "- (sin secciones detectadas)")
            + "\n\n"
            "## Initial content\n"
            f"{sample[:1500]}"
        ).strip()

        blocks.append({
            "content": summary,
            "content_type": "text",
            "page": 1,
            "text": summary,
            "metadata": {
                "format": "txt",
                "section": "summary",
                "line_count": len(non_empty_lines),
                "paragraph_count": len(paragraphs),
                "section_count": len(section_headings),
            },
        })

        # ── Bloques 2..N: una sección por bloque ───────────────────────
        if section_headings:
            # Añadir final del fichero como límite
            section_bounds = section_headings + [(len(lines), "")]
            for i in range(len(section_bounds) - 1):
                start_line, heading = section_bounds[i]
                end_line, _ = section_bounds[i + 1]
                section_text = "\n".join(lines[start_line:end_line]).strip()
                if not section_text or len(section_text) < 30:
                    continue
                blocks.append({
                    "content": section_text,
                    "content_type": "text",
                    "page": i + 2,
                    "text": section_text,
                    "metadata": {
                        "format": "txt",
                        "section": heading,
                        "line_start": start_line + 1,
                        "line_end": end_line,
                    },
                })
        else:
            # Sin secciones: emitir el texto completo como un bloque
            # (excluyendo el resumen para no duplicar)
            full = "\n".join(non_empty_lines).strip()
            if full and len(full) > len(sample):
                blocks.append({
                    "content": full,
                    "content_type": "text",
                    "page": 2,
                    "text": full,
                    "metadata": {
                        "format": "txt",
                        "section": "body",
                    },
                })

        log.debug("[txt] '%s' -> lines=%d paragraphs=%d sections=%d blocks=%d",
                  source, len(non_empty_lines), len(paragraphs),
                  len(section_headings), len(blocks))
        return blocks
