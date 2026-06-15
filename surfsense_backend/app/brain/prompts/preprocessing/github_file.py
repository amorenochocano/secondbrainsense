"""
brain/prompts/preprocessing/github_file.py
============================================
Preprocesador para el formato virtual .github_file.

ENTRADA
-------
  ## Metadata
  repo: owner/repo
  path: ruta/al/fichero.py
  language: python
  ...

  ## Contenido
  [contenido del fichero]

SALIDA
------
El preprocesador delega en el preprocesador del lenguaje detectado:
  - python  → py.py
  - sql     → sql.py
  - jupyter → ipynb.py (si es .ipynb)
  - markdown → md.py
  - json    → json.py
  - yaml/toml/ini → txt.py (nota técnica)
  - otros   → txt.py

Añade una cabecera ## con el contexto del fichero antes del contenido procesado.
Esto permite al LLM saber de qué repo y ruta viene el código.
"""

from __future__ import annotations
import logging
import re as _re

log = logging.getLogger(__name__)


_METADATA_RE = _re.compile(r"## Metadata\n(.*?)(?=\n## |\Z)", _re.DOTALL)
_CONTENT_RE  = _re.compile(r"## Contenido\n(.*?)(?=\n## [A-Z]|\Z)", _re.DOTALL)


def preprocess(full_text: str, max_chars: int) -> str:
    """
    Preprocesa el texto .github_file delegando en el preprocesador del lenguaje.

    Estrategia:
      1. Extraer metadata → construir cabecera ## con repo + path + url
      2. Extraer contenido → detectar lenguaje → delegar en el preprocesador correcto
      3. Concatenar cabecera + contenido preprocesado
    """
    m_meta = _METADATA_RE.search(full_text)
    m_cont = _CONTENT_RE.search(full_text)

    metadata_text = m_meta.group(1).strip() if m_meta else ""
    content_text  = m_cont.group(1).strip() if m_cont else full_text.strip()

    # Parsear metadata
    meta = {}
    for line in metadata_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()

    repo     = meta.get("repo", "")
    path     = meta.get("path", "")
    language = meta.get("language", "unknown")
    url      = meta.get("url", "")
    commit   = meta.get("last_commit", "")

    # ── Cabecera con contexto del fichero ─────────────────────────────────
    header_parts = [f"## {repo}: {path}"]
    if language and language != "unknown":
        header_parts.append(f"**Lenguaje**: {language}")
    if url:
        header_parts.append(f"**URL**: {url}")
    if commit:
        header_parts.append(f"**Último commit**: {commit}")
    header = "\n".join(header_parts)

    # ── Preprocesar el contenido según el lenguaje ─────────────────────────
    # Reservar chars para la cabecera
    content_budget = max_chars - len(header) - 10
    if content_budget < 500:
        content_budget = 500

    processed_content = _preprocess_by_language(content_text, language, path, content_budget)

    result = header + "\n\n" + processed_content

    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"

    return result


def _preprocess_by_language(content: str, language: str, path: str, max_chars: int) -> str:
    """Delega en el preprocesador del lenguaje correcto."""

    # Python
    if language == "python" or path.endswith(".py"):
        try:
            from .py import preprocess as _py_preprocess
            return _py_preprocess(content, max_chars)
        except Exception as exc:
            log.warning("[github_preprocess] py.py falló: %s", exc)
            return content[:max_chars]

    # SQL
    if language == "sql" or path.endswith(".sql"):
        try:
            from .sql import preprocess as _sql_preprocess
            return _sql_preprocess(content, max_chars)
        except Exception as exc:
            log.warning("[github_preprocess] sql.py falló: %s", exc)
            return content[:max_chars]

    # Jupyter Notebook
    if language == "jupyter" or path.endswith(".ipynb"):
        try:
            from .ipynb import preprocess as _ipynb_preprocess
            return _ipynb_preprocess(content, max_chars)
        except Exception as exc:
            log.warning("[github_preprocess] ipynb.py falló: %s", exc)
            return content[:max_chars]

    # Markdown
    if language == "markdown" or path.endswith(".md"):
        try:
            from .md import preprocess as _md_preprocess
            return _md_preprocess(content, max_chars)
        except Exception as exc:
            log.warning("[github_preprocess] md.py falló: %s", exc)
            return content[:max_chars]

    # JSON
    if language == "json" or path.endswith(".json"):
        try:
            from .json import preprocess as _json_preprocess
            return _json_preprocess(content, max_chars)
        except Exception as exc:
            log.warning("[github_preprocess] json.py falló: %s", exc)
            return content[:max_chars]

    # YAML / TOML / INI → tratados como nota técnica
    if language in ("yaml", "toml", "ini") or path.endswith((".yaml", ".yml", ".toml", ".ini", ".cfg")):
        try:
            from .txt import preprocess as _txt_preprocess
            return _txt_preprocess(content, max_chars)
        except Exception as exc:
            log.warning("[github_preprocess] txt.py falló: %s", exc)
            return content[:max_chars]

    # Fallback genérico
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n[... truncado ...]"
