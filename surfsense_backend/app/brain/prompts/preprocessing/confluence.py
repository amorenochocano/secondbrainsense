"""
brain/prompts/preprocessing/confluence.py
==========================================
Preprocesador para el formato virtual .confluence.

ENTRADA
-------
Texto serializado por ConfluenceConnector con estructura:
  ## Metadata
  space: TEC
  title: Nombre de la Página
  labels: architecture
  ...

  ## Contenido
  # Primera sección
  Texto...

SALIDA
------
processed_text con marcas semánticas ## / ### para el chunker y el LLM:

  ## Metadata
  [metadata del ticket]

  ## [Sección 1 del cuerpo]
  [contenido]

  ## [Sección 2 del cuerpo]
  [contenido]

Las marcas ## permiten al chunker cortar en límites semánticos.
El LLM recibe instrucciones (via _TYPE_INSTRUCTIONS_FOCUSED["confluence"])
que describen exactamente esta estructura.
"""

from __future__ import annotations
import re as _re


_METADATA_RE = _re.compile(r"## Metadata\n(.*?)(?=\n## |\Z)", _re.DOTALL)
_CONTENT_RE  = _re.compile(r"## Contenido\n(.*?)(?=\n## |\Z)", _re.DOTALL)
_HEADING_RE  = _re.compile(r"^(#{1,4})\s+(.+)$", _re.MULTILINE)
_CODE_FENCE  = _re.compile(r"^```.*?^```", _re.MULTILINE | _re.DOTALL)


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Preprocesa el texto .confluence para el LLM.

    Estrategia:
      1. Preservar ## Metadata tal cual (fuente primaria de contexto)
      2. Convertir el ## Contenido en secciones ## por heading del cuerpo
         - Heading 1 (#) → ## (sección principal, el chunker corta aquí)
         - Heading 2 (##) → ### (subsección, CK unit)
         - Heading 3+ (###...) → #### (subunidad embebida)
      3. Si el cuerpo no tiene headings → ## por párrafo largo

    El preprocesador respeta los bloques de código y no los divide.
    """
    # ── Extraer secciones ──────────────────────────────────────────────────
    m_meta = _METADATA_RE.search(full_text)
    m_cont = _CONTENT_RE.search(full_text)

    metadata_text = m_meta.group(1).strip() if m_meta else ""
    content_text  = m_cont.group(1).strip() if m_cont else full_text.strip()

    parts: list[str] = []

    # ── Bloque de metadata ─────────────────────────────────────────────────
    if metadata_text:
        parts.append(f"## Metadata\n{metadata_text}")

    # ── Cuerpo del contenido ───────────────────────────────────────────────
    if content_text:
        content_parts = _process_body(content_text)
        parts.extend(content_parts)

    result = "\n\n".join(parts)

    # Truncar si excede max_chars
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"

    return result


def _process_body(body: str) -> list[str]:
    """
    Convierte el cuerpo de la página en secciones ## / ### / ####.
    Respeta bloques de código sin dividirlos.
    """
    # Proteger bloques de código (no deben dividirse por headings)
    placeholders: dict[str, str] = {}
    protected = body

    for i, m in enumerate(_CODE_FENCE.finditer(body)):
        key = f"__CODE_BLOCK_{i}__"
        placeholders[key] = m.group(0)
        protected = protected[:m.start()] + key + protected[m.end():]

    # Detectar headings y dividir en secciones
    lines = protected.splitlines()
    sections: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            if current:
                sections.append(current)
            # Normalizar nivel: # → ##, ## → ###, ###+ → ####
            level = len(m.group(1))
            normalized_level = min(level + 1, 4)
            title = m.group(2).strip()
            current = [f"{'#' * normalized_level} {title}"]
        else:
            current.append(line)

    if current:
        sections.append(current)

    # Si no hay headings, dividir por párrafos
    if len(sections) <= 1 and not _HEADING_RE.search(body):
        return _split_by_paragraphs(body)

    # Restaurar bloques de código y construir partes
    parts: list[str] = []
    for section_lines in sections:
        section_text = "\n".join(section_lines).strip()
        # Restaurar código
        for key, code in placeholders.items():
            section_text = section_text.replace(key, code)
        if section_text:
            parts.append(section_text)

    return parts if parts else [body]


def _split_by_paragraphs(body: str) -> list[str]:
    """
    Fallback: divide el cuerpo en ## por párrafos largos.
    Para páginas Confluence sin estructura de headings.
    """
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    parts: list[str] = []
    idx = 1
    for para in paragraphs:
        if len(para) > 100:
            first = para.split(".")[0].strip()[:60]
            parts.append(f"## Sección {idx}: {first}\n{para}")
            idx += 1
        else:
            parts.append(para)
    return parts if parts else [body]
