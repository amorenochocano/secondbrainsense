"""
brain/prompts/preprocessing/jira_ticket.py
============================================
Preprocesador para el formato virtual .jira_ticket.

ENTRADA
-------
Texto serializado por JiraConnector con estructura:
  ## Metadata
  key: TEC-1234
  tipo: Story
  ...

  ## Descripción
  Texto de la descripción.

  ## Comentarios
  **John Doe** (...):
  Texto del comentario.
  ---
  **Jane Smith** (...):
  Otro comentario.

SALIDA
------
processed_text con marcas semánticas ## / ### para el chunker y el LLM:

  ## TEC-1234 — Story — Done
  [metadata clave]

  ## Descripción
  [descripción]

  ### Comentario 1: John Doe
  [texto del comentario]

  ### Comentario 2: Jane Smith
  [texto del comentario]

Los comentarios individuales se convierten en ### (unidades de CK)
porque cada uno puede contener decisiones técnicas independientes.
"""

from __future__ import annotations
import re as _re


_METADATA_RE    = _re.compile(r"## Metadata\n(.*?)(?=\n## |\Z)", _re.DOTALL)
_DESC_RE        = _re.compile(r"## Descripción\n(.*?)(?=\n## |\Z)", _re.DOTALL)
_COMMENTS_RE    = _re.compile(r"## Comentarios\n(.*?)(?=\n## |\Z)", _re.DOTALL)
_COMMENT_SEP    = _re.compile(r"\n---\n")
_COMMENT_HDR    = _re.compile(r"^\*\*(.+?)\*\*\s+\((.+?)\):\s*\n(.*)", _re.DOTALL)


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Preprocesa el texto .jira_ticket para el LLM.

    Estrategia:
      1. ## Cabecera con key + tipo + estado (sección principal del ticket)
      2. ## Descripción (sección principal)
      3. ### Comentario N: Autor (subsecciones — unidades de CK)

    Los comentarios como ### permiten al LLM documentar
    decisiones individuales en Core Knowledge.
    """
    # ── Extraer secciones ──────────────────────────────────────────────────
    m_meta = _METADATA_RE.search(full_text)
    m_desc = _DESC_RE.search(full_text)
    m_comm = _COMMENTS_RE.search(full_text)

    metadata_text = m_meta.group(1).strip() if m_meta else ""
    desc_text     = m_desc.group(1).strip() if m_desc else ""
    comments_text = m_comm.group(1).strip() if m_comm else ""

    # Parsear metadata para construir cabecera
    meta = _parse_meta(metadata_text)
    key    = meta.get("key", "SIN-CLAVE")
    tipo   = meta.get("tipo", "")
    estado = meta.get("estado", "")

    parts: list[str] = []

    # ── Cabecera del ticket ────────────────────────────────────────────────
    header_lines = [f"## {key}" + (f" — {tipo}" if tipo else "") + (f" — {estado}" if estado else "")]
    # Añadir campos de metadata relevantes
    for field in ["prioridad", "asignado", "reporter", "componentes",
                  "fix_version", "labels", "created", "resolved"]:
        val = meta.get(field, "")
        if val:
            header_lines.append(f"**{field.capitalize()}**: {val}")
    parts.append("\n".join(header_lines))

    # ── Descripción ────────────────────────────────────────────────────────
    if desc_text:
        parts.append(f"## Descripción\n{desc_text}")

    # ── Comentarios → ### por comentario individual ────────────────────────
    if comments_text:
        comment_blocks = _COMMENT_SEP.split(comments_text)
        for i, block in enumerate(comment_blocks, 1):
            block = block.strip()
            if not block:
                continue
            m = _COMMENT_HDR.match(block)
            if m:
                author   = m.group(1).strip()
                date     = m.group(2).strip()
                body     = m.group(3).strip()
                if body:
                    parts.append(f"### Comentario {i}: {author}\n_{date}_\n\n{body}")
            else:
                # Comentario sin header reconocible
                if block:
                    parts.append(f"### Comentario {i}\n{block}")

    result = "\n\n".join(parts)

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"

    return result


def _parse_meta(text: str) -> dict[str, str]:
    """Parsea el bloque de metadata a dict {campo: valor}."""
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()
    return meta
