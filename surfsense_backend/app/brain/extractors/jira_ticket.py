"""
api/extractors/jira_ticket.py
================================
Extractor para ficheros virtuales en formato canónico .jira_ticket.

Lee el texto serializado por JiraConnector y lo transforma en bloques
tipados {content, content_type, page, metadata}.

FORMATO DE ENTRADA
------------------
  ## Metadata
  key: TEC-1234
  tipo: Story
  estado: Done
  prioridad: High
  asignado: Jane Smith
  reporter: John Doe
  componentes: Data Pipeline
  fix_version: 3.2.0
  labels: data-engineering
  created: 2026-01-10T09:00:00.000+0000
  resolved: 2026-01-20T16:30:00.000+0000

  ## Descripción
  Texto de la descripción del ticket.
  Criterios de aceptación:
  - Criterio 1
  - Criterio 2

  ## Comentarios
  **John Doe** (2026-01-15T10:00:00.000+0000):
  Texto del comentario humano.
  ---
  **Jane Smith** (2026-01-18T14:30:00.000+0000):
  Otro comentario con decisión técnica.

BLOQUES GENERADOS
-----------------
  - 1 bloque "text" con el metadata del ticket
  - 1 bloque "text" con la descripción (si existe)
  - 1 bloque "text" con los comentarios humanos (si existen)
    Los comentarios se agrupan en un solo bloque para mantener el contexto
    de la conversación y las decisiones técnicas documentadas en los hilos.
"""

import re
import logging
from .base import BaseExtractor

log = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_URL_FIELDS = {"url", "source_url"}
_DATE_FIELDS = {"created", "resolved", "last_modified"}


def _parse_metadata_lines(text: str) -> dict[str, str]:
    """
    Parsea un bloque de metadata 'campo: valor' a dict.
    Maneja URLs e ISO timestamps que contienen ':' adicionales.
    """
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip().lower()
        if key in _URL_FIELDS or key in _DATE_FIELDS:
            colon_idx = line.index(":")
            val = line[colon_idx + 1:].strip()
        else:
            val = rest.strip()
        if key:
            meta[key] = val
    return meta


class JiraTicketExtractor(BaseExtractor):
    """
    Extractor para el formato virtual .jira_ticket.

    Acepta tanto ruta a fichero temporal como texto directo.
    """

    def extract(self, source: str) -> list[dict]:
        """
        Args:
            source: ruta al fichero .jira_ticket temporal, o texto directo
                    si empieza con "## Metadata".

        Returns:
            Lista de bloques {content, content_type, page, metadata}.
        """
        if source.startswith("## Metadata"):
            raw = source
        else:
            try:
                with open(source, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except OSError as exc:
                log.error("[jira_extractor] No se pudo leer '%s': %s", source, exc)
                return []

        blocks: list[dict] = []
        page_idx = 1

        # ── Separar las tres secciones ─────────────────────────────────────
        sections = _SECTION_RE.split(raw)
        # sections = ["", "Metadata", "...text...", "Descripción", "...text...", ...]

        metadata_text    = ""
        description_text = ""
        comments_text    = ""
        parsed_meta: dict[str, str] = {}

        i = 0
        while i < len(sections):
            part = sections[i].strip()
            if part == "Metadata" and i + 1 < len(sections):
                metadata_text = sections[i + 1].strip()
                parsed_meta   = self._parse_metadata(metadata_text)
                i += 2
            elif part == "Descripción" and i + 1 < len(sections):
                description_text = sections[i + 1].strip()
                i += 2
            elif part == "Comentarios" and i + 1 < len(sections):
                comments_text = sections[i + 1].strip()
                i += 2
            else:
                i += 1

        # ── Bloque de metadata ─────────────────────────────────────────────
        if metadata_text:
            blocks.append(self._make_block(
                content=f"## Metadata\n{metadata_text}",
                content_type="text",
                page=page_idx,
                metadata={
                    "format":     "jira_ticket",
                    "section":    "metadata",
                    "issue_key":  parsed_meta.get("key", ""),
                    "issue_type": parsed_meta.get("tipo", ""),
                    "status":     parsed_meta.get("estado", ""),
                    "priority":   parsed_meta.get("prioridad", ""),
                },
            ))
            page_idx += 1

        # ── Bloque de descripción ──────────────────────────────────────────
        if description_text:
            blocks.append(self._make_block(
                content=f"## Descripción\n{description_text}",
                content_type="text",
                page=page_idx,
                metadata={
                    "format":    "jira_ticket",
                    "section":   "description",
                    "issue_key": parsed_meta.get("key", ""),
                },
            ))
            page_idx += 1

        # ── Bloque de comentarios ──────────────────────────────────────────
        # Agrupamos todos los comentarios en un solo bloque:
        # la conversación tiene valor como unidad semántica (decisiones, contexto)
        if comments_text:
            comment_count = comments_text.count("\n---\n") + 1
            blocks.append(self._make_block(
                content=f"## Comentarios\n{comments_text}",
                content_type="text",
                page=page_idx,
                metadata={
                    "format":        "jira_ticket",
                    "section":       "comments",
                    "issue_key":     parsed_meta.get("key", ""),
                    "comment_count": comment_count,
                },
            ))
            page_idx += 1

        if not blocks:
            log.warning("[jira_extractor] '%s' → sin bloques extraídos, usando fallback", source)
            return [self._make_block(
                content=raw[:50000],
                content_type="text",
                page=1,
                metadata={"format": "jira_ticket", "section": "fallback"},
            )]

        log.debug("[jira_extractor] '%s' → %d bloques", source, len(blocks))
        return blocks

    # ── Helpers privados ───────────────────────────────────────────────────

    @staticmethod
    def _parse_metadata(text: str) -> dict[str, str]:
        """Parsea el bloque de metadata. Usa _parse_metadata_lines para manejar URLs."""
        return _parse_metadata_lines(text)
