"""
api/extractors/github_file.py
================================
Extractor para ficheros virtuales en formato canónico .github_file.

Lee el texto serializado por GitHubConnector y lo transforma en bloques
tipados {content, content_type, page, metadata}.

FORMATO DE ENTRADA
------------------
  ## Metadata
  repo: owner/repo
  path: ruta/al/fichero.py
  branch: main
  language: python
  size_bytes: 1234
  url: https://github.com/owner/repo/blob/main/ruta/al/fichero.py
  last_commit: Mensaje del último commit
  last_author: Nombre del autor
  last_date: 2026-01-15T10:30:00Z

  ## Contenido
  [contenido del fichero]

BLOQUES GENERADOS
-----------------
  - 1 bloque "text" con el metadata (path, repo, url, commit...)
  - 1 bloque "text" o "code" con el contenido del fichero
    El content_type se determina por el lenguaje del fichero:
    - python, sql, javascript, typescript, go, rust, java, bash → "code"
    - markdown, text, yaml, toml, ini, csv → "text"

La separación metadata + contenido permite al LLM tener contexto
del origen del fichero en el bloque de metadata, y al IngestRouter
enrutar el bloque de código a la colección correcta (code vs knowledge).
"""

import re
import logging
from .base import BaseExtractor

log = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)

# Lenguajes que van a content_type="code"
_CODE_LANGUAGES = {
    "python", "sql", "javascript", "typescript", "java",
    "go", "rust", "bash", "r", "jupyter", "json",
}


class GitHubFileExtractor(BaseExtractor):
    """
    Extractor para el formato virtual .github_file.
    Acepta tanto ruta a fichero temporal como texto directo.
    """

    def extract(self, source: str) -> list[dict]:
        if source.startswith("## Metadata"):
            raw = source
        else:
            try:
                with open(source, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except OSError as exc:
                log.error("[github_extractor] No se pudo leer '%s': %s", source, exc)
                return []

        blocks: list[dict] = []
        page_idx = 1

        # ── Separar Metadata y Contenido ──────────────────────────────────
        sections = _SECTION_RE.split(raw)
        metadata_text = ""
        content_text  = ""
        parsed_meta: dict[str, str] = {}

        i = 0
        while i < len(sections):
            part = sections[i].strip()
            if part == "Metadata" and i + 1 < len(sections):
                metadata_text = sections[i + 1].strip()
                parsed_meta   = self._parse_metadata(metadata_text)
                i += 2
            elif part == "Contenido" and i + 1 < len(sections):
                content_text = sections[i + 1].strip()
                i += 2
            else:
                i += 1

        # ── Bloque de metadata ────────────────────────────────────────────
        if metadata_text:
            blocks.append(self._make_block(
                content=f"## Metadata\n{metadata_text}",
                content_type="text",
                page=page_idx,
                metadata={
                    "format":      "github_file",
                    "section":     "metadata",
                    "repo":        parsed_meta.get("repo", ""),
                    "path":        parsed_meta.get("path", ""),
                    "branch":      parsed_meta.get("branch", ""),
                    "source_url":  parsed_meta.get("url", ""),
                    "language":    parsed_meta.get("language", ""),
                    "last_commit": parsed_meta.get("last_commit", ""),
                    "last_author": parsed_meta.get("last_author", ""),
                },
            ))
            page_idx += 1

        # ── Bloque de contenido ───────────────────────────────────────────
        if content_text:
            language = parsed_meta.get("language", "unknown")
            content_type = "code" if language in _CODE_LANGUAGES else "text"

            blocks.append(self._make_block(
                content=content_text,
                content_type=content_type,
                page=page_idx,
                language=language,
                module=parsed_meta.get("repo", ""),
                function=parsed_meta.get("path", ""),
                metadata={
                    "format":     "github_file",
                    "section":    "content",
                    "repo":       parsed_meta.get("repo", ""),
                    "path":       parsed_meta.get("path", ""),
                    "branch":     parsed_meta.get("branch", ""),
                    "source_url": parsed_meta.get("url", ""),
                    "language":   language,
                },
            ))
            page_idx += 1

        if not blocks:
            log.warning("[github_extractor] '%s' → sin bloques, usando fallback", source)
            return [self._make_block(
                content=raw[:50000],
                content_type="text",
                page=1,
                metadata={"format": "github_file", "section": "fallback"},
            )]

        log.debug("[github_extractor] '%s' → %d bloques", source, len(blocks))
        return blocks

    @staticmethod
    def _parse_metadata(text: str) -> dict[str, str]:
        meta: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().lower().replace(" ", "_")
                val = val.strip()
                # Reconstruir URLs que contienen ':' adicionales (ej: https://...)
                # La partición ya tomó solo el primer ':' correctamente
                # pero si el valor empieza por '//' es una URL que perdemos la parte 'https'
                # El connector serializa como "url: https://..." → partition da key="url", val="https"
                # Fix: si el val no parece tener el protocolo, buscar la línea completa
                if key == "url" or key.endswith("_url") or key == "source_url":
                    # Re-parsear la línea completa para URLs
                    colon_idx = line.index(":")
                    val = line[colon_idx + 1:].strip()
                meta[key] = val
        return meta
