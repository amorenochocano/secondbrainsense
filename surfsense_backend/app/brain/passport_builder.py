"""
passport_builder.py
-------------------
Construye pasaportes semánticos de forma híbrida:
  1. Extrae campos deterministas (frontmatter, entities, tags base) SIN LLM (~1ms)
  2. Pre-rellena el pasaporte YAML
  3. Marca secciones pendientes para LLM (Summary, Core Knowledge, etc.)

Contrato: recibe bloques extraídos por tipo + filename; devuelve pasaporte parcial en YAML.

v2.6 — TAGS:
  Las tags ya NO salen de un diccionario genérico por extensión. Ahora se
  derivan del CONTENIDO mediante:
    · keyphrases (YAKE, con fallback puro-Python si no está instalado)
    · entities ya extraídas
    · tokens del nombre de fichero / URL
  y se NORMALIZAN contra el vocabulario controlado (brain.vocabulary), que es
  la única fuente de verdad. El diccionario por extensión queda reducido a un
  único 'facet' de FORMATO (no inyecta dominios falsos).
"""

import os
import re
import json
import ast
import logging
import datetime
from pathlib import Path

from app.brain.vocabulary import match_text_to_vocab, normalize_tags, in_vocab
from app.brain.masters import classify_document

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Convenciones de naming: extraer dominio, subdomain, scope
# Patrón: {scope}_{domain}_{subdomain}_{descriptor}.{ext}
# Ejemplo: com_nb_tte_lib_std.ipynb → scope=com, domain=tte, subdomain=lib_std
# ---------------------------------------------------------------------------

def _parse_filename_convention(source: str) -> dict:
    """
    Extrae metadatos de la convención de nombre del proyecto.
    Formato: {scope}_{type}_{domain}_{subdomain}_{descriptor}.ext

    Ejemplos:
    - com_nb_tte_lib_std.ipynb       → scope=com, type=nb, domain=tte, subdomain=lib_std
    - com_pl_tte_main_config.json    → scope=com, type=pl, domain=tte, subdomain=main
    - raw_pl_tte_staging_parallel.drawio → scope=raw, type=pl, domain=tte, subdomain=staging
    """
    basename = os.path.splitext(os.path.basename(source))[0]
    parts = basename.split("_")

    meta = {
        "scope": "",      # com, raw, temp, etc.
        "type": "",       # nb (notebook), pl (pipeline), py (python), etc.
        "domain": "",     # tte, ong, sys, etc.
        "subdomain": "",  # lib_std, main, config, staging, etc.
        "projects": [],   # lista de proyectos inferred
    }

    if len(parts) >= 2:
        meta["scope"] = parts[0] if parts[0] in ("com", "raw", "temp", "test") else ""
        idx_start = 1 if meta["scope"] else 0

        if len(parts) > idx_start:
            meta["type"] = parts[idx_start] if len(parts[idx_start]) <= 3 else ""
            idx_domain = (idx_start + 1) if meta["type"] else idx_start

            if len(parts) > idx_domain:
                # Dominio reconocido si está en la lista de proyectos / áreas funcionales.
                # Ampliable: añade aquí los códigos de tu organización.
                _RECOGNIZED_DOMAINS = ("tte", "ong", "sys", "bi", "sa", "rh", "fin", "ops")
                _domain_part = parts[idx_domain].lower()
                meta["domain"] = parts[idx_domain].upper() if _domain_part in _RECOGNIZED_DOMAINS else ""
                idx_subdomain = idx_domain + (1 if meta["domain"] else 0)

                if len(parts) > idx_subdomain:
                    # Resto: subdomain + descriptor
                    remaining = "_".join(parts[idx_subdomain:])
                    meta["subdomain"] = remaining

                    # Inferir proyectos desde domain
                    domain_lower = meta["domain"].lower()
                    _PROJECT_MAP = {
                        "tte": "TTE", "ong": "ONG", "sys": "SYS", "bi": "BI",
                        "sa":  "SA",  "rh":  "RH",  "fin": "FIN", "ops": "OPS",
                    }
                    if domain_lower in _PROJECT_MAP:
                        meta["projects"] = [_PROJECT_MAP[domain_lower]]

    return meta


# ---------------------------------------------------------------------------
# Extracción de entities por tipo de fichero
# ---------------------------------------------------------------------------

def _extract_entities_from_ipynb(blocks: list[dict]) -> list[str]:
    """Extrae nombres de funciones y clases de celdas de código."""
    entities = []
    for block in blocks:
        if block.get("content_type") != "code":
            continue
        code_text = block.get("content", "")
        try:
            tree = ast.parse(code_text)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    entities.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    entities.append(node.name)
        except SyntaxError:
            pass
    return list(set(entities))[:15]  # Top 15 únicos


def _extract_entities_from_code(blocks: list[dict]) -> list[str]:
    """Extrae nombres de funciones/clases de código Python/SQL."""
    entities = []
    for block in blocks:
        if block.get("content_type") != "code":
            continue
        code_text = block.get("content", "")
        # SQL: nombres de tablas/CTEs
        if "SELECT" in code_text.upper() or "CREATE" in code_text.upper():
            tables = re.findall(r"(?:FROM|JOIN|INTO)\s+(\w+)", code_text, re.IGNORECASE)
            entities.extend(tables)
        # Python: función/clase names
        else:
            try:
                tree = ast.parse(code_text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        entities.append(node.name)
                    elif isinstance(node, ast.ClassDef):
                        entities.append(node.name)
            except SyntaxError:
                pass
    return list(set(entities))[:15]


def _extract_entities_from_json(full_text: str) -> list[str]:
    """Extrae nombres de actividades, datasets, linked services."""
    entities = []
    try:
        data = json.loads(full_text)
        props = data.get("properties", data)
        # ADF/Fabric pipelines
        for act in props.get("activities", [])[:20]:
            entities.append(act.get("name", ""))
        # Datasets
        for ds in props.get("datasets", [])[:20]:
            entities.append(ds.get("name", ""))
        # Linked services
        for ls in props.get("linkedServices", [])[:20]:
            entities.append(ls.get("name", ""))
    except (json.JSONDecodeError, KeyError):
        pass
    return [e for e in list(set(entities)) if e][:15]


def _extract_entities_from_markdown_blocks(blocks: list[dict]) -> list[str]:
    """Extrae entidades semánticas de bloques markdown.

    Filtra headings que no son entidades reales:
    - Títulos puramente numéricos de índice  (ej: "10.3 Obtener KEYS generadas" → no)
    - Headings de pasaporte semántico (emojis 📌 📄 🧩 🧠 🔗 ⚙️ ⚠️)
    - Headings de 1-2 palabras genéricas (Objetivo, Causa, etc.)
    Extrae además componentes estructurados: [component] Name de draw.io / listas.
    """
    # Headings de sección de pasaporte (son ruido cuando el fuente es un .md)
    _PASSPORT_EMOJI = re.compile(r"[📌📄🧩🧠🔗⚙️⚠️]")
    # Índices tipo "10.3 Algo" o "3. Algo" (numeración de procedimiento)
    _NUMBERED_SECTION = re.compile(r"^\d+[\.\d]*\s+.+$")
    # Palabras genéricas de un solo token que no aportan semántica
    _GENERIC = {
        "objetivo", "causa", "nota", "resumen", "summary", "introducción",
        "introduction", "conclusión", "conclusion", "anexo", "appendix",
        "referencias", "references", "índice", "index",
    }

    entities = []
    for block in blocks:
        if block.get("content_type") != "text":
            continue
        text = block.get("content", "")
        headings = re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)
        for h in headings:
            h = h.strip()
            # Descartar headings de pasaporte (emoji de sección)
            if _PASSPORT_EMOJI.search(h):
                continue
            # Descartar índices numerados
            if _NUMBERED_SECTION.match(h):
                continue
            # Descartar si es una palabra genérica
            if h.lower() in _GENERIC:
                continue
            # Descartar headings muy cortos (< 4 chars) o muy largos (> 80 chars)
            if len(h) < 4 or len(h) > 80:
                continue
            entities.append(h)

        # draw.io / listas estructuradas: [component] Name
        labels = re.findall(r"^\s*\[[^\]]+\]\s+(.+)$", text, re.MULTILINE)
        entities.extend([l.strip() for l in labels])

    return list(dict.fromkeys(entities))[:15]


def _looks_like_ai_agent_markdown(text: str) -> bool:
    """Heurística para detectar markdowns de instrucciones de agente/prompt."""
    if not text:
        return False

    lowered = text.lower()
    score = 0
    patterns = (
        "eres un",
        "dominas:",
        "objetivo:",
        "cuando respondas:",
        "si falta contexto:",
        "tono:",
        "principios clave",
    )

    for pattern in patterns:
        if pattern in lowered:
            score += 1

    return score >= 3


def _extract_entities_from_ai_agent_markdown(blocks: list[dict]) -> list[str]:
    """Extrae entidades semánticas de markdowns con formato de system prompt."""
    text = "\n".join(
        block.get("content", "")
        for block in blocks
        if block.get("content_type") == "text"
    )

    if not _looks_like_ai_agent_markdown(text):
        return []

    entities: list[str] = []

    # Rol principal: "Eres un ..."
    role_match = re.search(
        r"^Eres un (.+?)(?:,|\.|\n)",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if role_match:
        entities.append(role_match.group(1).strip())

    # Secciones en mayúsculas (OBJETIVO, PRINCIPIOS CLAVE, etc.)
    sections = re.findall(
        r"^([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s\-/()]{3,}):\s*$",
        text,
        re.MULTILINE,
    )
    ignored_sections = {"OBJETIVO", "FORMATO", "TONO"}
    for section in sections:
        section = section.strip()
        if section in ignored_sections:
            continue
        entities.append(section)

    # Bullets técnicos dentro de "Dominas" y listas similares
    bullets = re.findall(r"^\s*-\s+(.+)$", text, re.MULTILINE)
    for bullet in bullets:
        bullet = re.sub(r"\(.*?\)", "", bullet)
        bullet = bullet.strip(" .,;:-")
        if len(bullet) < 4 or len(bullet) > 80:
            continue
        entities.append(bullet)

    return list(dict.fromkeys(entities))[:20]


# Stop-list de tokens UI/navegación que NO son entidades técnicas válidas.
# Se aplica a candidatos CamelCase/snake_case en docs (PDF/DOCX/HTML/PPTX)
# para evitar capturar pasos de UI ("PulsaCtrl", "seleccionaCopiar", etc.)
# como si fueran entidades del dominio.
_UI_ACTION_PREFIXES = (
    "pulsa", "haz", "click", "seleccion", "elige", "marca",
    "rellena", "introduce", "escribe", "abre", "cierra", "guarda",
    "copia", "pega", "borra", "elimina", "arrastra", "suelta",
    "ver", "ir", "salir", "entrar", "buscar", "filtrar", "ordenar",
)
_GENERIC_DOC_WORDS = {
    "notes", "componentes", "conexiones", "rows", "columns",
    "summary", "overview", "intro", "introduction", "introducción",
    "conclusion", "conclusión", "table", "tabla", "figure", "figura",
    "section", "sección", "capitulo", "capítulo", "page", "página",
    "version", "versión", "documento", "document",
}


def _is_ui_action_token(token: str) -> bool:
    """Detecta camelCase de acciones UI: 'PulsaCtrl', 'eligeDelimitado'..."""
    t = token.lower()
    return any(t.startswith(prefix) for prefix in _UI_ACTION_PREFIXES)


def _extract_entities_from_text_sections(blocks: list[dict], full_text: str) -> list[str]:
    """
    Heurística genérica para docs (pdf/docx/pptx/web/xlsx).

    Captura solo entidades plausibles:
      1. Headings (#, ##, ###) — siempre válidos
      2. Claves estructurales (title:, columns:, sheet:, section:)
      3. CamelCase / snake_case candidates SOLO si:
         - No empieza por verbo de acción UI (Pulsa, Elige, Marca...)
         - No es palabra genérica de documento
         - Para CamelCase: requiere ≥2 mayúsculas internas (descarta tokens
           cortos como "Click", "Open", que son verbos en UI)
         - Para snake_case: ≥3 caracteres por segmento

    Esto elimina entidades basura como 'PulsaCtrl', 'eligeDelimitado',
    'seleccionaCopiar' que aparecían en pasaportes de HTML con instrucciones.
    """
    entities: list[str] = []
    text_parts = [b.get("content", "") for b in blocks if b.get("content_type") == "text"]
    text = "\n".join([t for t in text_parts if t.strip()]) or (full_text or "")

    # 1) Headings
    headings = re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)
    entities.extend([h.strip() for h in headings])

    # 2) Claves estructurales
    kv = re.findall(
        r"^(?:title|columns|sheet|section)\s*:\s*(.+)$",
        text, re.MULTILINE | re.IGNORECASE,
    )
    entities.extend([k.strip() for k in kv])

    # 3) Candidatos CamelCase / snake_case con filtros estrictos
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b", text)
    candidates = []
    for t in tokens:
        # Filtrar verbos de acción UI
        if _is_ui_action_token(t):
            continue
        # Filtrar palabras genéricas
        if t.lower() in _GENERIC_DOC_WORDS:
            continue
        # snake_case: válido si tiene al menos un underscore interno
        if "_" in t and len(t) >= 5:
            candidates.append(t)
            continue
        # CamelCase: requerir al menos 2 mayúsculas internas
        # (descarta tokens UI cortos como "Click", "Open", "Read")
        uppers = sum(1 for c in t[1:] if c.isupper())
        if uppers >= 2:
            candidates.append(t)

    entities.extend(candidates[:30])

    # Limpieza final
    cleaned = []
    seen = set()
    for e in entities:
        ee = re.sub(r"\s+", " ", e).strip(" -:\t\n\r")
        if not ee or len(ee) > 80:
            continue
        if ee.lower() in _GENERIC_DOC_WORDS:
            continue
        key = ee.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(ee)

    return cleaned[:15]


def _extract_entities_from_xlsx_blocks(blocks: list[dict]) -> list[str]:
    entities = []
    for b in blocks:
        if b.get("content_type") != "text":
            continue
        meta = b.get("metadata") or {}
        sheet = meta.get("sheet")
        if sheet:
            entities.append(sheet)
        cols = meta.get("columns") or []
        for c in cols[:20]:
            if c and isinstance(c, str):
                entities.append(c.strip())
    return list(set(entities))[:15]


def _extract_entities_from_csv_blocks(blocks: list[dict]) -> list[str]:
    entities = []
    for b in blocks:
        if b.get("content_type") != "text":
            continue
        meta = b.get("metadata") or {}
        cols = meta.get("columns") or []
        for c in cols[:30]:
            if c and isinstance(c, str):
                entities.append(c.strip())
    return list(dict.fromkeys([e for e in entities if e]))[:20]


# ---------------------------------------------------------------------------
# Keyphrases del contenido (señal de tags basada en lo que el doc DICE)
# YAKE si está disponible; si no, fallback por frecuencia (puro Python).
# ---------------------------------------------------------------------------

_STOP_ES = set(
    "de la el en y a los las que con por para una un del se su al lo como mas pero "
    "sus le ya o este si porque esta entre cuando muy sin sobre tambien me hasta hay "
    "donde quien desde todo nos durante todos uno les ni contra otros ese eso ante "
    "ellos esto antes algunos unos otro otras otra tanto esa estos mucho quienes nada "
    "muchos cual poco ella estar estas algunas algo nosotros sea ser son fue han".split()
)
_STOP_EN = set(
    "the of and to in a is that for it on with as are this be by an or from at which "
    "we you your can will not has have was were they their there here then than into "
    "such these those over under more most some any all each".split()
)


def _fallback_keyphrases(text: str, top: int) -> list[str]:
    """Extractor por frecuencia cuando YAKE no está disponible."""
    from collections import Counter
    tokens = re.findall(r"[a-záéíóúñü0-9][a-záéíóúñü0-9\-]{3,}", text.lower())
    counter = Counter(
        t for t in tokens
        if t not in _STOP_ES and t not in _STOP_EN and not t.isdigit()
    )
    return [w for w, _ in counter.most_common(top)]


def _explode_tokens(names: list[str]) -> list[str]:
    """
    Descompone CamelCase y snake_case en subtokens, para que nombres como
    'RunNotebook' o 'copy_from_sql' aporten 'notebook', 'sql', etc. al matching.
    """
    out: list[str] = []
    for name in names:
        if not name:
            continue
        # snake / kebab → espacios
        s = re.sub(r"[_\-]+", " ", name)
        # CamelCase → separar (RunNotebook → Run Notebook)
        s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
        for tok in s.split():
            tok = tok.strip().lower()
            if len(tok) > 2:
                out.append(tok)
    return out


def _extract_keyphrases(full_text: str, lang: str = "es", top: int = 12) -> list[str]:
    """
    Devuelve hasta `top` keyphrases del contenido.
    Usa YAKE (sin GPU, soporta español); si no está instalado, fallback.
    Limita la muestra a 6000 chars para que sea rápido en CPU.
    """
    text = (full_text or "").strip()
    if len(text) < 200:
        return []
    sample = text[:6000]
    try:
        import yake  # opcional
        kw = yake.KeywordExtractor(lan=lang, n=2, top=top, dedupLim=0.7)
        return [k.lower() for k, _score in kw.extract_keywords(sample)]
    except Exception:
        return _fallback_keyphrases(sample, top)


# ---------------------------------------------------------------------------
# Tags de FORMATO por tipo (facet) — SOLO formato, sin dominios falsos.
# El contenido (pyspark, fabric, docker...) lo aporta el vocabulario.
# ---------------------------------------------------------------------------

_FORMAT_TAGS: dict[str, list[str]] = {
    "ipynb": ["notebook", "python"],
    "py": ["python"],
    "sql": ["sql"],
    "json": ["json"],
    "drawio": ["diagram"],
    "pdf": ["pdf"],
    "docx": ["documentation"],
    "pptx": ["presentation"],
    "xlsx": ["spreadsheet"],
    "xml": ["xml"],
    "md": ["markdown"],
    "csv": ["csv", "tabular-data"],
    "txt": ["text-document"],
    "web": ["web"],
    "html": ["web"],
    "htm": ["web"],
}


# Tokens genéricos que no aportan valor como tag tecnológico
_URL_NOISE_HOST = {
    "www", "docs", "learn", "blog", "api", "developer", "developers",
    "support", "help", "go", "aka", "get", "portal",
}
_URL_NOISE_PATH = {
    "en-us", "en", "en-gb", "en-in", "latest", "reference", "api",
    "v1", "v2", "v3", "docs", "documentation", "getting-started",
    "get-started", "fundamentals", "about", "introduction", "guide",
    "overview", "index", "home", "start", "main", "general",
}
_URL_TLDS = {"com", "org", "io", "co", "net", "edu", "gov", "ai", "dev", "html", "htm"}


def _extract_tags_from_url(url: str) -> list[str]:
    """
    Infiere tags de tecnología directamente del hostname y path de la URL.
    Genérico: funciona para cualquier URL sin hardcodear dominios.

    Estrategia:
      1. Hostname: tomar subdominios y dominio principal, descartar TLDs y ruido.
      2. Path: tomar cada segmento, descartar locales (en-us), versiones y genéricos.
      3. Deduplicar y limitar a 10 tags.

    Ejemplos:
      learn.microsoft.com/en-us/fabric/data-engineering/
        → ["microsoft", "fabric", "data-engineering"]
      docs.aws.amazon.com/boto3/latest/reference/services/
        → ["aws", "amazon", "boto3", "services"]
      ollama.com/library
        → ["ollama", "library"]
      huggingface.co/docs/transformers/index
        → ["huggingface", "transformers"]
    """
    from urllib.parse import urlparse as _urlparse

    try:
        parsed = _urlparse(url)
    except Exception:
        return []

    tags = []

    # 1. Hostname → tokens significativos
    host = (parsed.hostname or "").lower()
    for part in host.split("."):
        if part and part not in _URL_NOISE_HOST and part not in _URL_TLDS and len(part) > 1:
            tags.append(part)

    # 2. Path → segmentos significativos (conservar guiones: data-engineering es 1 tag)
    path = parsed.path.strip("/")
    for seg in path.split("/"):
        # Quitar extensión de fichero
        seg = re.sub(r"\.(html?|htm|php|aspx?|json)$", "", seg.lower().strip())
        if (seg
                and seg not in _URL_NOISE_PATH
                and not re.match(r"^\d[\d.]*$", seg)   # versiones numéricas: 3, 3.12, v2
                and len(seg) > 2):
            tags.append(seg)

    # Deduplicar manteniendo orden, máximo 10
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            result.append(t)
        if len(result) >= 10:
            break

    return result


# ---------------------------------------------------------------------------
# Drill-down triggers por defecto
# ---------------------------------------------------------------------------

_DEFAULT_TRIGGERS: dict[str, list[str]] = {
    "ipynb": ["cómo funciona", "qué tabla", "paso a paso", "parámetro", "error"],
    "py": ["cómo usar", "parámetro", "return", "excepción", "ejemplo"],
    "sql": ["tablas", "joins", "filtro", "columnas", "agregación"],
    "json": ["actividad", "parámetro", "dependencia", "dataset", "linked service"],
    "csv": ["columna", "filtro", "métrica", "valor", "agregación"],
    "txt": ["resumen", "sección", "definición", "paso a paso", "cita textual"],
    "default": ["exactamente", "código", "configuración de", "paso a paso", "cita textual"],
}


# ---------------------------------------------------------------------------
# Builder principal
# ---------------------------------------------------------------------------

def build_partial_passport(
    source: str,
    file_type: str,
    blocks: list[dict],
    full_text: str,
    metadata: dict | None = None,
    source_location: str = "",
    source_type: str = "",
    source_repo: str = "",
    source_repo_path: str = "",
) -> dict:
    """
    Construye un pasaporte PARCIAL rellenando todo lo que es determinista.

    Args:
        source: nombre fichero
        file_type: extensión
        blocks: lista de bloques extraídos por tipo [{"content", "content_type": text|code, "page", ...}]
        full_text: texto raw para fallback
        metadata: dict adicional del extractor
        source_location: URL o ruta absoluta de origen (github, sharepoint, local, web)
        source_type: tipo de origen ("github", "local", "sharepoint", "url", "upload")
        source_repo: nombre del repositorio si origin=github
        source_repo_path: ruta relativa dentro del repo (ej: src/etl/load.py)

    Returns:
        {
            "passport_partial": "YAML frontmatter + placeholders para LLM",
            "entities": [...],  # para facilitar search después
            "tags_base": [...],
            "triggers": [...],
            "entities_for_extraction": [...],  # hints para el LLM
        }
    """
    ft = file_type.lower().lstrip(".")
    metadata = metadata or {}

    # Parsing de convención de nombre
    named_meta = _parse_filename_convention(source)

    # Extrae entities según tipo
    entities = []
    if ft == "ipynb":
        entities = _extract_entities_from_ipynb(blocks)
    elif ft in ("py", "sql"):
        entities = _extract_entities_from_code(blocks)
    elif ft == "json":
        entities = _extract_entities_from_json(full_text)
    elif ft == "md":
        # Markdown: headings + heurística de prompts de agente; fallback genérico
        entities = _extract_entities_from_markdown_blocks(blocks)
        ai_entities = _extract_entities_from_ai_agent_markdown(blocks)
        entities = list(dict.fromkeys(entities + ai_entities))
        if not entities:
            entities = _extract_entities_from_text_sections(blocks, full_text)
    elif ft == "drawio":
        entities = _extract_entities_from_markdown_blocks(blocks)
    elif ft == "xlsx":
        entities = _extract_entities_from_xlsx_blocks(blocks)
    elif ft == "csv":
        entities = _extract_entities_from_csv_blocks(blocks)
    elif ft in ("pdf", "docx", "pptx", "web", "html", "htm", "xml", "txt"):
        entities = _extract_entities_from_text_sections(blocks, full_text)

    # =====================================================================
    # TAGS (v2.6): contenido > formato. Única fuente de verdad = vocabulary.
    # =====================================================================
    # 1) Facet de FORMATO (no inyecta dominios falsos)
    format_tags = list(_FORMAT_TAGS.get(ft, []))

    # 2) Pool de candidatos: nombre, URL, entities y keyphrases del contenido
    candidates: list[str] = list(format_tags)
    if named_meta["domain"]:
        candidates.append(named_meta["domain"].lower())
    if named_meta["subdomain"]:
        candidates.extend(named_meta["subdomain"].lower().split("_")[:3])
    if ft in ("web", "html", "htm") and source.startswith("http"):
        candidates = _extract_tags_from_url(source) + candidates
    candidates.extend([e.lower() for e in entities])
    # 2b) Señal desde el nombre del fichero: descomponer en tokens y cruzar
    #     con el vocabulario (alias incluidos). Útil para ficheros con contenido
    #     escaso o en formato de datos (xlsx, json, csv) donde el nombre es la
    #     pista más directa. Ej: 'jira-import.xlsx' → tokens ['jira','import']
    _fname_tokens = re.sub(r"[_\-\.]", " ", os.path.splitext(os.path.basename(source))[0])
    filename_vocab_tags = match_text_to_vocab(_fname_tokens, cap=8)
    candidates.extend(filename_vocab_tags)
    # Descomponer CamelCase/snake de entities (p. ej. RunNotebook → notebook;
    # CopyFromSQL → sql) para que los nombres de actividades/funciones aporten señal
    candidates.extend(_explode_tokens(entities))

    # JSON con estructura de actividades = pipeline de datos (señal estructural,
    # no un dominio falso). Detecta también tipos de actividad notebook/spark.
    if ft == "json":
        try:
            _data = json.loads(full_text)
            _props = _data.get("properties", _data) if isinstance(_data, dict) else {}
            _acts = _props.get("activities") if isinstance(_props, dict) else None
            if isinstance(_acts, list) and _acts:
                candidates.append("data-pipeline")
                candidates.extend(_explode_tokens([a.get("type", "") for a in _acts if isinstance(a, dict)]))
        except Exception:
            pass

    keyphrases = _extract_keyphrases(full_text, lang="es", top=12)
    candidates.extend(keyphrases)

    # AI agent / system-prompt markdowns: inyectar tags controladas de dominio
    if ft == "md" and _looks_like_ai_agent_markdown(full_text):
        candidates.extend([
            "ai-agent",
            "system-prompt",
            "prompt-engineering",
        ])

    # 3) Matching contra vocabulario controlado (texto completo + candidatos)
    vocab_tags = match_text_to_vocab(full_text, extra_candidates=candidates, cap=12)

    # 4) Unas pocas keyphrases fuera de vocabulario, para no perder temas nuevos
    extra_kp = normalize_tags([k for k in keyphrases if not in_vocab(k)], cap=3)

    # 5) Orden final: contenido (vocab) → temas nuevos → facet de formato
    tags_base = normalize_tags(vocab_tags + extra_kp + format_tags, cap=12)

    # Triggers
    triggers = _DEFAULT_TRIGGERS.get(ft, _DEFAULT_TRIGGERS["default"])

    # Slug y KB ID
    slug = _slugify(source)
    kb_id = f"kb_{slug}"
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # ──────────────────────────────────────────────────────────────────────────
    # TÍTULO: política unificada
    #
    # Para FICHEROS (no URLs):
    #   - La fuente primaria SIEMPRE es el nombre del fichero (slug humanizado).
    #   - El H1 del contenido puede ENRIQUECER el título solo si no es genérico
    #     (ver _GENERIC_TITLE_WORDS) y contiene información que el slug no tiene.
    #   - Si el H1 es genérico → se descarta; el título queda como el slug.
    #
    # Para URLs:
    #   - og:title / H1 del contenido son la fuente primaria (el nombre de host
    #     no es informativo).
    # ──────────────────────────────────────────────────────────────────────────

    _GENERIC_TITLE_WORDS = {
        "summary", "resumen", "overview", "index", "índice",
        "introduction", "introducción", "contents", "contenido",
        "readme", "untitled", "sin título", "página", "page",
        "descripción funcional", "descripcion funcional",
        "descripción", "descripcion", "functional description",
        "documentación", "documentacion", "documentation",
        "notas", "notes", "draft", "borrador",
        # Patrones de índice de RFP/documentos estructurados
        "parte a", "parte b", "parte c",
        "capítulo", "capitulo", "sección", "seccion",
        "índice del documento", "indice del documento",
    }

    def _is_generic_h1(raw: str) -> bool:
        """Devuelve True si el H1 capturado es un título genérico no informativo."""
        # Quitar emojis y limpiar
        cleaned = re.sub(r"[^\w\s\-()]", "", raw).strip().lower()
        # Título de una o dos palabras muy corto → posible genérico
        if len(cleaned) < 6:
            return True
        # Coincidencia exacta con blacklist
        if cleaned in _GENERIC_TITLE_WORDS:
            return True
        # Empieza con cualquiera de las palabras clave (ej: "Summary of ...", "Parte A.-...")
        for kw in _GENERIC_TITLE_WORDS:
            if cleaned.startswith(kw):
                return True
        # Patrón explícito: "PARTE X.-" o "Capítulo N"
        if re.match(r"^parte\s+[a-z]\.?", cleaned) or re.match(r"^cap[íi]tulo\s+\d", cleaned):
            return True
        return False

    # Slug humanizado desde el nombre del fichero (base para ficheros, fallback para URLs)
    filename_title = slug.replace("-", " ").title()

    title = ""

    if ft in ("web", "html", "htm") and source.startswith("http"):
        # URLs: og:title o title de metadatos HTML tienen prioridad
        for block in blocks:
            meta = block.get("metadata") or {}
            if meta.get("section") == "metadata" and meta.get("format") == "html":
                content = block.get("content", "")
                og_match = re.search(r"^og:title:\s*(.+)$", content, re.MULTILINE)
                t_match  = re.search(r"^title:\s*(.+)$",    content, re.MULTILINE)
                raw = (og_match or t_match)
                if raw:
                    title = raw.group(1).strip()
                    title = re.sub(
                        r"\s*[-|]\s*(Microsoft Learn|Microsoft Fabric|AWS Documentation|Ollama|HuggingFace|Python).*$",
                        "", title, flags=re.IGNORECASE,
                    ).strip()
                if title:
                    break
        if not title:
            # Fallback URL: H1 del contenido
            for block in blocks:
                if block.get("content_type") == "text":
                    m = re.search(r"^#\s+(.+)$", block.get("content", ""), re.MULTILINE)
                    if m and not _is_generic_h1(m.group(1)):
                        title = m.group(1).strip()
                        break
        if not title:
            # Fallback URL: último segmento del path
            from urllib.parse import urlparse as _urlparse
            _path = _urlparse(source).path.rstrip("/")
            _seg  = _path.split("/")[-1] if _path else ""
            title = _seg.replace("-", " ").title() if _seg else filename_title

        # Enriquecer título de una sola palabra con tecnología del hostname
        if title and len(title.split()) == 1:
            url_tags_for_title = _extract_tags_from_url(source)
            if url_tags_for_title:
                prefix = url_tags_for_title[0].replace("-", " ").title()
                if prefix.lower() not in title.lower():
                    title = f"{prefix} {title}"

    else:
        # FICHEROS: el nombre del fichero es la fuente primaria, siempre presente.
        title = filename_title

        # Buscar H1 en bloques de contenido y usarlo solo si no es genérico
        # y el slug no ya lo contiene todo.
        h1_candidate = ""
        for block in blocks:
            if block.get("content_type") == "text":
                m = re.search(r"^#\s+(.+)$", block.get("content", ""), re.MULTILINE)
                if m:
                    h1_candidate = m.group(1).strip()
                    break

        if h1_candidate and not _is_generic_h1(h1_candidate):
            # El H1 es informativo: úsalo si aporta algo que el slug no dice.
            # Condición: el H1 NO debe ser simplemente el slug reformateado.
            h1_lower = re.sub(r"[^\w\s]", "", h1_candidate).lower().replace(" ", "")
            slug_lower = slug.replace("-", "")
            # Si el H1 contiene explícitamente el nombre del fichero → preferirlo
            if slug_lower in h1_lower:
                title = re.sub(r"[^\w\s\-(),.:;/&]", "", h1_candidate).strip() or filename_title
            # Si el H1 es claramente más largo y descriptivo, usar H1 directamente
            elif len(h1_candidate) > len(filename_title) + 10:
                title = re.sub(r"[^\w\s\-(),.:;/&]", "", h1_candidate).strip() or filename_title
            # En cualquier otro caso: mantener filename_title (slug humanizado)

    # Limpieza YAML-safe
    title = re.sub(r"[^\w\s\-(),.:;/&]", "", title).strip() or slug

    # Clasificación basada en CONTENIDO via masters SQLite
    # (el nombre del fichero y la extensión son solo hints, no la fuente principal)
    classification = classify_document(
        tags=tags_base,
        keyphrases=keyphrases,
        title=title,
        file_type=ft,
    )
    doc_type = classification["doc_type"]
    domain = classification["domain"]
    # Solo heredar subdomain del filename si la convención reconoció un domain válido
    subdomain = classification["subdomain"]
    if not subdomain and named_meta["domain"]:
        subdomain = named_meta["subdomain"] or ""

    # Cascada de fallback para domain cuando masters no clasifica con confianza.
    # Sin esto, los PDFs/HTMLs/Excel sin convención de nombre se quedan en "other"
    # porque sus tags programáticas (["pdf"], ["html"]) no superan _MIN_SCORE_DOMAIN.
    # Orden de prioridad:
    #   1. Filename convention (tte, ong, sys, bi, sa, etc.) — más fiable
    #   2. Coincidencia tag → dominio conocido (data-engineering, testing, etc.)
    #   3. Mantener "other" si nada coincide
    if domain == "other":
        if named_meta["domain"]:
            domain = named_meta["domain"]
        else:
            # Mapping tag canónica → dominio masters. Si una tag de alta señal
            # del vocabulario aparece en los tags extraídos, asignar su dominio.
            _TAG_TO_DOMAIN = {
                # data engineering
                "pyspark": "data-engineering", "delta-lake": "data-engineering",
                "data-pipeline": "data-engineering", "etl": "data-engineering",
                "medallion": "data-engineering", "airflow": "data-engineering",
                "data-warehouse": "data-engineering", "data-lake": "data-engineering",
                "spark-sql": "data-engineering", "dataframe": "data-engineering",
                # cloud azure
                "microsoft-fabric": "cloud-azure", "azure-data-factory": "cloud-azure",
                "azure-synapse": "cloud-azure", "databricks": "cloud-azure",
                "power-bi": "cloud-azure", "azure": "cloud-azure",
                # testing
                "testing": "testing", "test-automation": "testing",
                "selenium": "testing", "cypress": "testing", "pytest": "testing",
                "test-case": "testing", "xray": "testing",
                # devops
                "docker": "devops-infra", "kubernetes": "devops-infra",
                "terraform": "devops-infra", "ci-cd": "devops-infra",
                "github-actions": "devops-infra",
                # development
                "python": "development", "fastapi": "development",
                "rest-api": "development", "react": "development",
                # databases
                "postgresql": "databases", "mongodb": "databases",
                "sqlserver": "databases", "redis": "databases",
                # ai/ml
                "llm": "data-science-ml", "rag": "data-science-ml",
                "embeddings": "data-science-ml", "machine-learning": "data-science-ml",
                "ai-agent": "data-science-ml", "langchain": "data-science-ml",
                # procurement
                "rfp": "procurement", "rfi": "procurement",
                "vct": "procurement", "tender": "procurement",
                # asset operations
                "asset-management": "asset-operations",
                "predictive-maintenance": "asset-operations",
                "scada": "asset-operations", "iiot": "asset-operations",
                # project management
                "jira": "project-management", "confluence": "project-management",
            }
            for tag in tags_base:
                if tag in _TAG_TO_DOMAIN:
                    domain = _TAG_TO_DOMAIN[tag]
                    log.debug("[passport] domain fallback via tag '%s' → '%s'", tag, domain)
                    break

    # Determinar source.type: prioridad parámetro explícito, luego inferencia
    _src_type = source_type or (
        "url" if (source.startswith("http") and "github.com" not in source) else
        "github" if "github.com" in source else
        "file"
    )

    # Construir bloque source: con campos opcionales
    _source_block = (
        "source:\n"
        f"  type: {_src_type}\n"
        f"  origin: {source}\n"
        f"  format: {file_type}\n"
    )
    if source_location:
        _source_block += f"  location: \"{source_location}\"\n"
    if source_repo:
        _source_block += f"  repo: \"{source_repo}\"\n"
    if source_repo_path:
        _source_block += f"  repo_path: \"{source_repo_path}\"\n"

    # Construir frontmatter (pre-rellenado)
    frontmatter = (
        "---\n"
        f"id: {kb_id}\n"
        f"title: \"{title}\"\n"
        f"type: {doc_type}\n"
        f"domain: {domain}\n"
        f"subdomain: \"{subdomain}\"\n"
        + _source_block +
        f"created_at: {now}\n"
        f"updated_at: {now}\n"
        "importance: medium\n"
        "confidence: 0.8\n"
        "refresh_policy: on_demand\n"
        "raw_ingest: false\n"
        "embedding_scope:\n"
        "  - brain\n"
        "  - knowledge\n"
        f"tags: {json.dumps(tags_base)}\n"
        f"entities: {json.dumps(entities)}\n"
        "related: []\n"
        "drill_down_triggers:\n"
    )
    for trigger in triggers:
        frontmatter += f"  - \"{trigger}\"\n"
    frontmatter += "---\n"

    # Placeholders para LLM (secciones narrativas)
    placeholder_body = """
# 📌 Summary
[LLM: Qué es esto en 5-10 líneas]

# 🧩 Core Knowledge
[LLM: Conceptos clave y su explicación]

# 🧠 Key Insights
[LLM: Interpretaciones y decisiones técnicas relevantes]

# 🔗 Relationships
[LLM: Dependencias y artefactos relacionados]

# ⚙️ Practical Usage
[LLM: Cómo se usa en la práctica]

# ⚠️ Pitfalls / Risks
[LLM: Problemas comunes y errores típicos]

# 📄 Source Extract
[Extracto literal si es necesario]
"""

    partial_passport = frontmatter + placeholder_body

    log.info(
        "[passport] '%s' (tipo='%s') → %d entities, %d tags, %d triggers",
        source, file_type, len(entities), len(tags_base), len(triggers),
    )

    return {
        "passport_partial": partial_passport,
        "entities": entities,
        "tags_base": tags_base,
        "triggers": triggers,
        "entities_for_extraction": "\n".join(
            [f"  - {e}" for e in entities]
        ) if entities else "  [ninguno extraído]",
    }


def _slugify(text: str) -> str:
    """Genera un slug válido."""
    name = os.path.splitext(os.path.basename(text))[0] if not text.startswith("http") else text
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] if slug else "unnamed"
