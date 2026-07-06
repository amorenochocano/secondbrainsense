"""
fallback.py — Extractor genérico para formatos no reconocidos.

Estrategia:
  1. Intenta leer como texto plano (UTF-8, errors="replace").
  2. Si el contenido decodificado tiene ratio de caracteres legibles >= 0.85 →
     trata como texto: reutiliza lógica de secciones de TxtExtractor.
  3. Si es binario puro (ratio < 0.85) → emite 1 bloque con metadata del
     fichero (nombre, tamaño, extensión, fecha modificación).

Garantía: cualquier fichero pasa por el pipeline sin lanzar excepción.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseExtractor

log = logging.getLogger(__name__)

_TEXT_RATIO_THRESHOLD = 0.85

# Reutilización de patrones de TxtExtractor (sin herencia para evitar acoplamiento)
_HEADING_PATTERNS = [
    re.compile(
        r"^(?:CAP[IÍ]TULO|SECCI[OÓ]N|PARTE|M[OÓ]DULO|CHAPTER|SECTION|PART)\s+[\dIVX]+",
        re.IGNORECASE,
    ),
    re.compile(r"^\d+(?:\.\d+){0,2}\.?\s+[A-ZÁÉÍÓÚÑa-záéíóúñ]"),
    re.compile(r"^[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s:,.\-]{3,99}$"),
]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return False
    return any(pat.match(stripped) for pat in _HEADING_PATTERNS)


def _text_ratio(raw_bytes: bytes) -> float:
    """Fracción de bytes que son caracteres ASCII imprimibles o UTF-8 comunes."""
    if not raw_bytes:
        return 0.0
    printable = sum(1 for b in raw_bytes if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D) or b >= 0x80)
    return printable / len(raw_bytes)


class FallbackExtractor(BaseExtractor):
    """Extractor de último recurso para formatos no registrados en ExtractorFactory."""

    def extract(self, source: str) -> list[dict]:
        path = Path(source)
        ext = path.suffix.lower() or "(sin extensión)"

        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            log.error("[fallback] No se puede leer '%s': %s", source, exc)
            return [self._make_block(
                content=f"[Error al leer fichero: {exc}]",
                content_type="text",
                page=1,
                metadata={"format": "fallback", "error": str(exc), "extension": ext},
            )]

        ratio = _text_ratio(raw_bytes)
        log.debug("[fallback] '%s' ext=%s ratio=%.2f", source, ext, ratio)

        if ratio >= _TEXT_RATIO_THRESHOLD:
            return self._extract_as_text(path, raw_bytes, ext)
        else:
            return self._extract_as_binary(path, raw_bytes, ext)

    def _extract_as_text(self, path: Path, raw_bytes: bytes, ext: str) -> list[dict]:
        text = raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

        if not text.strip():
            return [self._make_block(
                content="",
                content_type="text",
                page=1,
                metadata={"format": "fallback", "extension": ext, "empty": True},
            )]

        lines = text.split("\n")
        non_empty_lines = [ln for ln in lines if ln.strip()]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        section_headings: list[tuple[int, str]] = [
            (idx, line.strip()) for idx, line in enumerate(lines) if _is_heading(line)
        ]

        sample = "\n".join(paragraphs[:2]) if paragraphs else "\n".join(non_empty_lines[:8])
        summary = (
            f"# Fichero {ext}{f': {path.name}'}\n"
            f"lines: {len(non_empty_lines)}\n"
            f"paragraphs: {len(paragraphs)}\n"
            f"sections: {len(section_headings)}\n"
            "\n## Section headings\n"
            + ("\n".join(f"- {h}" for _, h in section_headings[:20])
               if section_headings else "- (sin secciones detectadas)")
            + f"\n\n## Initial content\n{sample[:1500]}"
        ).strip()

        blocks: list[dict] = [self._make_block(
            content=summary,
            content_type="text",
            page=1,
            metadata={
                "format": "fallback",
                "extension": ext,
                "fallback_mode": "text",
                "line_count": len(non_empty_lines),
                "paragraph_count": len(paragraphs),
                "section_count": len(section_headings),
            },
        )]

        if section_headings:
            section_bounds = section_headings + [(len(lines), "")]
            for i in range(len(section_bounds) - 1):
                start_line, heading = section_bounds[i]
                end_line, _ = section_bounds[i + 1]
                section_text = "\n".join(lines[start_line:end_line]).strip()
                if not section_text or len(section_text) < 30:
                    continue
                blocks.append(self._make_block(
                    content=section_text,
                    content_type="text",
                    page=i + 2,
                    metadata={
                        "format": "fallback",
                        "extension": ext,
                        "fallback_mode": "text",
                        "section": heading,
                        "line_start": start_line + 1,
                        "line_end": end_line,
                    },
                ))
        else:
            full = "\n".join(non_empty_lines).strip()
            if full and len(full) > len(sample):
                blocks.append(self._make_block(
                    content=full,
                    content_type="text",
                    page=2,
                    metadata={
                        "format": "fallback",
                        "extension": ext,
                        "fallback_mode": "text",
                        "section": "body",
                    },
                ))

        log.debug("[fallback] '%s' texto -> blocks=%d", path.name, len(blocks))
        return blocks

    def _extract_as_binary(self, path: Path, raw_bytes: bytes, ext: str) -> list[dict]:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        size_kb = round(stat.st_size / 1024, 1)

        content = (
            f"# Fichero binario: {path.name}\n"
            f"extension: {ext}\n"
            f"size: {size_kb} KB\n"
            f"modified: {mtime}\n"
            f"\n[Contenido binario no extraíble. "
            f"Formato {ext} no soportado por ningún extractor especializado.]"
        )

        log.warning("[fallback] '%s' binario puro (%s KB) — metadata-only block", path.name, size_kb)
        return [self._make_block(
            content=content,
            content_type="text",
            page=1,
            metadata={
                "format": "fallback",
                "extension": ext,
                "fallback_mode": "binary",
                "file_name": path.name,
                "size_bytes": stat.st_size,
                "modified_utc": mtime,
            },
        )]
