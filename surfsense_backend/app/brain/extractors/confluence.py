"""
api/extractors/confluence.py
==============================
Extractor para ficheros virtuales en formato canónico .confluence.

Lee el texto serializado por ConfluenceConnector y lo transforma en bloques
tipados {content, content_type, page, metadata} — exactamente igual que
cualquier otro BaseExtractor del sistema.

FORMATO DE ENTRADA
------------------
El texto tiene siempre esta estructura (generada por ConfluenceConnector):

  ## Metadata
  space: TEC
  title: Nombre de la Página
  labels: architecture, data-pipeline
  author: Juan García
  last_modified: 2026-01-15T10:30:00.000Z
  url: https://empresa.atlassian.net/wiki/spaces/TEC/pages/123456789
  parent_page: Arquitectura > Decisiones Técnicas
  children_count: 3

  ## Contenido
  # Primera sección

  Texto del cuerpo.

  ```python
  def ejemplo():
      return "valor"
  ```

BLOQUES GENERADOS
-----------------
  - 1 bloque "text" con el metadata serializado (sección ## Metadata)
  - N bloques "text" o "code" del cuerpo (sección ## Contenido)
    - Secciones por headings → "text"
    - Bloques de código fenced ``` → "code"
"""

import re
import logging
from .base import BaseExtractor

log = logging.getLogger(__name__)

_SECTION_RE   = re.compile(r"^## (.+)$", re.MULTILINE)
_HEADING_RE   = re.compile(r"^#{1,4} (.+)$", re.MULTILINE)
_CODE_FENCE   = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)
_URL_FIELDS   = {"url", "source_url", "webui", "link"}


def _parse_metadata_lines(text: str) -> dict[str, str]:
    """
    Parsea un bloque de metadata 'campo: valor' a dict.
    Maneja correctamente campos que contienen ':' en el valor (URLs, timestamps ISO 8601).
    """
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip().lower()
        # Para campos que típicamente contienen URLs o ISO timestamps,
        # tomar el resto completo de la línea desde el primer ':'
        if key in _URL_FIELDS or key in {"last_modified", "created", "resolved", "last_date"}:
            colon_idx = line.index(":")
            val = line[colon_idx + 1:].strip()
        else:
            val = rest.strip()
        if key:
            meta[key] = val
    return meta


class ConfluenceExtractor(BaseExtractor):
    """
    Extractor para el formato virtual .confluence.

    Acepta tanto una ruta a fichero temporal (flujo normal de ingesta)
    como texto directo (para tests y uso programático).
    """

    def extract(self, source: str) -> list[dict]:
        """
        Args:
            source: ruta al fichero .confluence temporal, o texto directo
                    si empieza con "## Metadata".

        Returns:
            Lista de bloques {content, content_type, page, metadata}.
        """
        # Leer el contenido
        if source.startswith("## Metadata"):
            raw = source
        else:
            try:
                with open(source, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except OSError as exc:
                log.error("[confluence_extractor] No se pudo leer '%s': %s", source, exc)
                return []

        blocks: list[dict] = []
        page_idx = 1

        # ── Separar secciones ## Metadata y ## Contenido ───────────────────
        sections = _SECTION_RE.split(raw)
        # sections = ["", "Metadata", "...metadata text...", "Contenido", "...body..."]

        metadata_text = ""
        content_text  = ""

        i = 0
        while i < len(sections):
            part = sections[i].strip()
            if part == "Metadata" and i + 1 < len(sections):
                metadata_text = sections[i + 1].strip()
                i += 2
            elif part == "Contenido" and i + 1 < len(sections):
                content_text = sections[i + 1].strip()
                i += 2
            else:
                i += 1

        # ── Bloque de metadata ─────────────────────────────────────────────
        if metadata_text:
            meta = self._parse_metadata(metadata_text)
            blocks.append(self._make_block(
                content=f"## Metadata\n{metadata_text}",
                content_type="text",
                page=page_idx,
                metadata={
                    "format":   "confluence",
                    "section":  "metadata",
                    "source_url": meta.get("url", ""),
                    "space":    meta.get("space", ""),
                    "labels":   meta.get("labels", ""),
                    "author":   meta.get("author", ""),
                },
            ))
            page_idx += 1

        # ── Bloques del cuerpo ─────────────────────────────────────────────
        if content_text:
            body_blocks = self._split_body(content_text, page_idx)
            blocks.extend(body_blocks)
            page_idx += len(body_blocks)

        if not blocks:
            log.warning("[confluence_extractor] '%s' → sin bloques extraídos, usando fallback", source)
            return [self._make_block(
                content=raw[:50000],
                content_type="text",
                page=1,
                metadata={"format": "confluence", "section": "fallback"},
            )]

        log.debug("[confluence_extractor] '%s' → %d bloques", source, len(blocks))
        return blocks

    # ── Helpers privados ───────────────────────────────────────────────────

    def _parse_metadata(self, text: str) -> dict:
        """Parsea el bloque de metadata a dict {campo: valor}. Maneja URLs con ':' correctamente."""
        return _parse_metadata_lines(text)

    def _split_body(self, body: str, start_page: int) -> list[dict]:
        """
        Divide el cuerpo en bloques semánticos:
          - Bloques de código fenced → content_type="code"
          - Secciones por heading → content_type="text"
          - Párrafos sin heading → content_type="text"
        """
        blocks: list[dict] = []
        page_idx = start_page

        # Separar bloques de código del texto narrativo
        segments = self._split_code_segments(body)

        for seg_text, seg_type, seg_lang in segments:
            seg_text = seg_text.strip()
            if not seg_text:
                continue

            if seg_type == "code":
                blocks.append(self._make_block(
                    content=seg_text,
                    content_type="code",
                    page=page_idx,
                    language=seg_lang or "unknown",
                    metadata={"format": "confluence", "section": "code_block"},
                ))
                page_idx += 1
            else:
                # Dividir texto por headings
                sections = self._split_by_headings(seg_text)
                for section_text in sections:
                    section_text = section_text.strip()
                    if not section_text:
                        continue
                    heading = self._extract_first_heading(section_text)
                    blocks.append(self._make_block(
                        content=section_text,
                        content_type="text",
                        page=page_idx,
                        metadata={
                            "format":  "confluence",
                            "section": heading or f"section_{page_idx}",
                        },
                    ))
                    page_idx += 1

        return blocks

    @staticmethod
    def _split_code_segments(text: str) -> list[tuple[str, str, str]]:
        """
        Divide texto en segmentos (contenido, tipo, lenguaje).
        tipo = "code" para bloques ```...``` , "text" para el resto.
        """
        segments: list[tuple[str, str, str]] = []
        last_end = 0

        for m in _CODE_FENCE.finditer(text):
            # Texto antes del bloque de código
            before = text[last_end:m.start()].strip()
            if before:
                segments.append((before, "text", ""))
            # Bloque de código
            lang = m.group(1).strip()
            code = m.group(2).strip()
            if code:
                segments.append((code, "code", lang))
            last_end = m.end()

        # Resto tras el último bloque de código
        remaining = text[last_end:].strip()
        if remaining:
            segments.append((remaining, "text", ""))

        return segments if segments else [(text, "text", "")]

    @staticmethod
    def _split_by_headings(text: str) -> list[str]:
        """Divide el texto en secciones por headings # / ## / ###."""
        lines = text.splitlines()
        sections: list[str] = []
        current: list[str] = []
        heading_re = re.compile(r"^#{1,4}\s+")

        for line in lines:
            if heading_re.match(line) and current:
                sections.append("\n".join(current))
                current = [line]
            else:
                current.append(line)

        if current:
            sections.append("\n".join(current))

        return [s for s in sections if s.strip()] or [text]

    @staticmethod
    def _extract_first_heading(text: str) -> str:
        """Extrae el primer heading del texto para usar como nombre de sección."""
        for line in text.splitlines():
            m = re.match(r"^#{1,4}\s+(.+)$", line.strip())
            if m:
                return m.group(1).strip()
        return ""
