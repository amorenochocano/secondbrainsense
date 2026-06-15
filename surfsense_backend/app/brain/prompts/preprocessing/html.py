"""
brain/prompts/preprocessing/html.py
------------------------------------
Preprocesado de páginas HTML para síntesis del pasaporte semántico.

v3 — build_focused usa los bloques del WebExtractor; preprocess es fallback.

ARQUITECTURA DE LA CADENA (html)
----------------------------------
  WebExtractor.extract(html_file)
    └─ BeautifulSoup: elimina script/style/nav/header/footer/form/button
    └─ Filtra NODOS HOJA (_is_leaf_container): evita duplicación
    └─ Deduplica por fingerprint
    └─ _build_structured_text: intenta P1/P2 (secciones "// 00 — Título")
    └─ Produce bloques: {content_type:"text"} principal + {content_type:"code"}

  build_doc_focused_text(blocks, full_text, "html", max_chars)
    └─ Llama a html.build_focused(blocks, full_text, max_chars)  ← AQUÍ

  html.preprocess(full_text, max_chars)   ← FALLBACK si no hay bloques

INPUT REAL QUE RECIBE build_focused
-------------------------------------
El bloque principal {content_type:"text"}.content contiene líneas LIMPIAS
separadas por newlines, ya deduplicadas. Según el tipo de HTML:

  TIPO A — Curso/guía con secciones (curso-arquitectura-dato-ia.html):
    Línea de título del módulo: "Fundamentos Matemáticos para IA"
    Línea de keywords:          "ÁLGEBRA LINEAL · SIMILITUD · ESPACIOS VECTORIALES"
    Línea de badge:             "FUNDAMENTO"
    Descripción larga:          "¿Por qué? Todo en IA de datos —embeddings..."
    Subtemas intercalados:      "Vectores y espacios de alta dimensión"
    Desc. subtema:              "Qué significa representar texto como un punto..."
    Keywords del subtema:       "dot product normas ortogonalidad"
    Keywords duplicadas cortas: "normas", "ortogonalidad"  ← ruido

  TIPO B — Ya estructurado (P1/P2 detectado por extractor):
    "## Módulo 01: Título"
    "contenido..."

  TIPO C — Dashboard/tabla (model_token_budget.html):
    Líneas de cabecera/filas de tabla como texto plano

  TIPO D — UI/Mockup:
    CSS, JS, nombres de clase: poco contenido útil

ESTRATEGIA build_focused
--------------------------
  1. Detectar el tipo por señales del contenido
  2. Para TIPO A (curso): detectar secciones por patrón
     TÍTULO_MIXTO → KEYWORDS_MAYÚSCULAS → badge → descripción
  3. Para TIPO B (ya estructurado): pasar casi tal cual
  4. Para TIPO C (tabla): agrupar por títulos de sección
  5. Para TIPO D (UI): nota breve
  6. Para TIPO genérico: párrafos largos como secciones ##
"""

from __future__ import annotations
import re as _re
from collections import Counter


# ---------------------------------------------------------------------------
# Constantes y patrones
# ---------------------------------------------------------------------------

_BLOCK_TAGS = frozenset({
    "div", "section", "article", "ul", "ol", "table", "tr", "blockquote",
    "header", "footer", "aside", "nav", "figure", "form", "main", "tbody",
    "thead", "dl", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "dt", "dd", "td", "th",
})

# Línea de keywords: MAYÚSCULAS separadas por · o · · o & o /
_KEYWORDS_LINE = _re.compile(
    r"^[A-ZÁÉÍÓÚÑ&/\s·•\-,().]{8,}$"
)

# Badge de tipo de módulo (una sola palabra en mayúsculas)
_BADGE_LINE = _re.compile(
    r"^(FUNDAMENTO|CORE|INFRAESTRUCTURA|ARQUITECTURA|DATOS|INGENIERÍA|"
    r"AVANZADO|PROYECTO|PRODUCCIÓN|PRODUCCION|MEDIO|BÁSICO|BASICO|"
    r"AVANZADO|EXPERTO|INTRO|INTRODUCTORIO)$"
)

# Separadores visuales (decoración)
_SEPARATOR_LINE = _re.compile(r"^[▶◀►▷●○◦→←↑↓]{1,3}$")

# Línea de número de módulo estricto: "01", "02", ..., "10"
_MODULE_NUM_RE = _re.compile(r"^(0[1-9]|10)$")

# Señales de código CSS/JS
_CSS_SIGNALS = frozenset({"{", "}", ";", "px", "em", "rem", "var(", "display:",
                           "background:", "font-", "border:", "margin:", "padding:",
                           "color:", "width:", "height:", "flex", "grid"})


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _clean_html(raw: str) -> str:
    """Limpia HTML crudo a texto plano."""
    text = _re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=_re.DOTALL)
    text = _re.sub(r"<script[^>]*>.*?</script>", "", text, flags=_re.DOTALL)
    text = _re.sub(r"<[^>]+>", " ", text)
    for ent, rep in [
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&#xa;", "\n"), ("&#x27;", "'"),
    ]:
        text = text.replace(ent, rep)
    text = _re.sub(r"[ \t]+", " ", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_noise(line: str) -> bool:
    """True si la línea es ruido de navegación o boilerplate."""
    if not line or len(line.strip()) < 3:
        return True
    if _re.match(r"^(©|®|™|\d{1,4}$|inicio|home|menú|buscar|login|cerrar|ver más|"
                 r"read more|compartir|share)\s*$", line.strip(), _re.IGNORECASE):
        return True
    return False


def _remove_boilerplate(lines: list[str]) -> list[str]:
    """Elimina líneas repetidas ≥3 veces (nav, footer duplicado)."""
    freq = Counter(l for l in lines if 3 < len(l) < 80)
    boilerplate = {t for t, c in freq.items() if c >= 3}
    return [l for l in lines if l not in boilerplate]


def _is_keywords_line(line: str) -> bool:
    """True si la línea parece una línea de keywords técnicas (MAYÚSCULAS · separadas)."""
    s = line.strip()
    if len(s) < 8 or len(s) > 150:
        return False
    return bool(_KEYWORDS_LINE.match(s)) and ("·" in s or "•" in s or "&" in s or "/" in s)


def _is_section_title(line: str) -> bool:
    """
    True si la línea parece un título de sección de módulo.
    Características: mixto de mayúsculas/minúsculas, longitud razonable,
    sin puntuación de oración, empieza con mayúscula.
    """
    s = line.strip()
    if len(s) < 8 or len(s) > 100:
        return False
    if s[0].islower():
        return False
    # No debe ser una descripción larga (con punto final o coma abundante)
    if s.count(".") > 1 or s.count(",") > 2:
        return False
    # No debe ser solo mayúsculas (eso es keyword line o badge)
    if s.isupper():
        return False
    # No debe tener ¿ al inicio (es una pregunta retórica)
    if s.startswith("¿"):
        return False
    return True


# ---------------------------------------------------------------------------
# Detección del tipo de contenido
# ---------------------------------------------------------------------------

def _detect_content_type(lines: list[str]) -> str:
    """
    Detecta el patrón de contenido para elegir la estrategia de estructuración.

    Returns:
        "already_structured" — ya tiene ## headers (P1/P2 detectado)
        "sectioned_guide"    — guía/curso con secciones título+keywords+desc
        "numbered_modules"   — patrón "01", "Título", descripción (menos común)
        "table_data"         — dashboard/tabla de datos
        "ui_mockup"          — principalmente CSS/JS, sin contenido semántico
        "generic"            — texto genérico
    """
    if not lines:
        return "ui_mockup"

    # ¿Ya estructurado?
    headers = [l for l in lines if l.startswith("## ")]
    if len(headers) >= 3:
        return "already_structured"

    # ¿UI/mockup? (señales CSS/JS dominantes o título de UI)
    css_count = sum(1 for l in lines[:30]
                    if any(kw in l for kw in _CSS_SIGNALS))
    text_count = sum(1 for l in lines if len(l) > 20 and not _is_noise(l))
    first_lines = " ".join(lines[:5]).lower()
    is_ui_title = any(kw in first_lines for kw in ("mockup", "ui mock", "component", "prototype"))
    if (css_count > text_count * 0.4 and text_count < 15) or is_ui_title:
        return "ui_mockup"

    # ¿Módulos numerados? (patrón "01")
    num_matches = sum(1 for l in lines if _MODULE_NUM_RE.match(l.strip()))
    if num_matches >= 3:
        return "numbered_modules"

    # ¿Guía seccionada? (patrón título + keywords en MAYÚSCULAS)
    # Detectar líneas de keywords (ej: "ÁLGEBRA LINEAL · SIMILITUD · ESPACIOS VECTORIALES")
    kw_lines = sum(1 for l in lines if _is_keywords_line(l))
    if kw_lines >= 2:
        return "sectioned_guide"

    # ¿Tabla de datos? (muchas líneas cortas uniformes)
    short_lines = [l for l in lines if 5 < len(l) < 60]
    if len(short_lines) > len(lines) * 0.65 and len(lines) > 15:
        return "table_data"

    return "generic"


# ---------------------------------------------------------------------------
# Estrategia: ya estructurado (el extractor ya aplicó P1/P2)
# ---------------------------------------------------------------------------

def _structure_already(lines: list[str], code_blocks: list[str], max_chars: int) -> str:
    """El texto ya tiene ## headers — limpiar y añadir code blocks si existen."""
    parts = ["\n".join(lines)]
    if code_blocks:
        cb_parts = ["## Código y diagramas"]
        for i, cb in enumerate(code_blocks[:6]):
            cb_parts.append(f"```\n{cb[:800]}\n```")
        parts.append("\n".join(cb_parts))
    result = "\n\n".join(parts)
    return result[:max_chars] if len(result) > max_chars else result


# ---------------------------------------------------------------------------
# Estrategia: guía seccionada (cursos, documentación técnica con secciones)
# ---------------------------------------------------------------------------

def _structure_sectioned_guide(lines: list[str], code_blocks: list[str], max_chars: int) -> str:
    """
    Reconstruye la estructura semántica de un HTML con secciones tipo guía/curso.

    Patrón real detectado en curso-arquitectura-dato-ia.html:
      "Fundamentos Matemáticos para IA"      ← TÍTULO (mixto, sin puntuación)
      "ÁLGEBRA LINEAL · SIMILITUD · ..."     ← KEYWORDS (solo mayúsculas, con ·)
      "FUNDAMENTO"                           ← BADGE (tipo del módulo)
      "¿Por qué? Todo en IA de datos..."    ← DESCRIPCIÓN INTRO (larga)
      "Vectores y espacios de alta dimens."  ← SUBTEMA (mixto, sin desc.)
      "Qué significa representar texto..."   ← DESC. SUBTEMA (larga)
      "dot product normas ortogonalidad"     ← KW del subtema (sin ·, corta)
      ...
      "Modelos de Embedding"                 ← SIGUIENTE SECCIÓN
      "LLMs DE EMBEDDING · FINE-TUNING · ..." ← KEYWORDS
      ...

    El truco es detectar cuándo empieza una nueva sección: una línea
    que parece título de sección (mixto, corta, empieza con mayúscula)
    seguida de una línea de keywords.

    También manejamos tablas embebidas detectando grupos de líneas cortas
    uniformes que siguen a un título.
    """
    sections: list[str] = []

    # ── Pre-paso: identificar cabecera global (antes de las secciones) ──
    # Buscar el primer par (título + keywords) para marcar el inicio
    first_kw_idx = next((i for i, l in enumerate(lines) if _is_keywords_line(l)), None)

    if first_kw_idx and first_kw_idx > 0:
        pre = lines[:first_kw_idx - 1]  # -1 para no incluir el título antes de las kw
        pre_text = " ".join(l for l in pre if len(l) > 10 and not _is_noise(l))
        if pre_text:
            sections.append(f"## Documento\n{pre_text[:400]}")

    # ── Segmentar en secciones usando el patrón título+keywords ──
    # Un punto de corte es cuando encontramos (título_de_sección + keywords_line)
    # o solo título_de_sección después de un keywords_line

    # Construir una lista de (índice_inicio, título) para cada sección
    section_starts: list[tuple[int, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Detectar patrón: línea de título seguida de keywords
        if _is_section_title(line) and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if _is_keywords_line(next_line):
                section_starts.append((i, line))
        i += 1

    if not section_starts:
        # No detectamos el patrón exacto → intentar solo por títulos mixtos
        for i, line in enumerate(lines):
            s = line.strip()
            if _is_section_title(s) and len(s) > 15:
                section_starts.append((i, s))

    if not section_starts:
        return _structure_generic(lines, code_blocks, max_chars)

    # ── Generar sección por cada bloque ──
    for sec_idx, (start_i, title) in enumerate(section_starts):
        end_i = (section_starts[sec_idx + 1][0]
                 if sec_idx + 1 < len(section_starts)
                 else len(lines))

        block_lines = lines[start_i:end_i]
        if not block_lines:
            continue

        block_parts = [f"## {title}"]

        # Clasificar las líneas del bloque
        keywords_found = False
        badge_found = False
        intro_lines: list[str] = []
        topic_lines: list[str] = []
        kw_collected: list[str] = []

        for bl in block_lines[1:]:  # skip el título
            bl_s = bl.strip()
            if not bl_s or _is_noise(bl_s) or _SEPARATOR_LINE.match(bl_s):
                continue

            if _is_keywords_line(bl_s) and not keywords_found:
                kw_collected.append(bl_s)
                keywords_found = True
            elif _BADGE_LINE.match(bl_s) and not badge_found:
                badge_found = True
                # El badge lo añadimos como metadata
                block_parts.append(f"*Nivel: {bl_s}*")
            elif len(bl_s) > 80:
                # Descripción larga (intro o desc de subtema)
                intro_lines.append(bl_s)
            elif len(bl_s) > 15 and _is_section_title(bl_s):
                # Subtema (título mixto corto)
                topic_lines.append(bl_s)
            # Ignorar: keywords cortas duplicadas (normas, ortogonalidad, etc.)
            # que son sub-elementos de keywords ya capturadas

        if kw_collected:
            block_parts.append(f"**Área:** {kw_collected[0]}")

        if intro_lines:
            block_parts.append(intro_lines[0][:250])  # primera descripción

        if topic_lines:
            block_parts.append("**Subtemas:**")
            # Para cada subtema, añadir la descripción larga siguiente si existe
            seen_topics: set[str] = set()
            topic_descs: list[str] = [l for l in block_lines if len(l.strip()) > 80]
            for ti, topic in enumerate(topic_lines[:10]):
                if topic in seen_topics:
                    continue
                seen_topics.add(topic)
                # Desc asociada: la descripción larga que sigue al subtema en block_lines
                topic_pos = next((j for j, bl in enumerate(block_lines)
                                  if bl.strip() == topic), -1)
                desc = ""
                if topic_pos >= 0:
                    for bl in block_lines[topic_pos + 1:topic_pos + 3]:
                        if len(bl.strip()) > 50:
                            desc = bl.strip()[:120]
                            break
                if desc:
                    block_parts.append(f"- **{topic}**: {desc}")
                else:
                    block_parts.append(f"- {topic}")

        sections.append("\n".join(block_parts))

    # ── Tablas del HTML (si existen como code_blocks) ──
    if code_blocks:
        cb_parts = ["## Código y diagramas"]
        for cb in code_blocks[:6]:
            cb_parts.append(f"```\n{cb[:800]}\n```")
        sections.append("\n".join(cb_parts))

    result = "\n\n".join(sections)
    return result[:max_chars] if len(result) > max_chars else result


# ---------------------------------------------------------------------------
# Estrategia: módulos numerados (01, 02, ...)
# ---------------------------------------------------------------------------

def _structure_numbered_modules(lines: list[str], code_blocks: list[str], max_chars: int) -> str:
    """Para HTMLs con números de módulo aislados como "01", "02"."""
    module_starts: list[tuple[int, str]] = [
        (i, l.strip()) for i, l in enumerate(lines)
        if _MODULE_NUM_RE.match(l.strip())
    ]
    if not module_starts:
        return _structure_generic(lines, code_blocks, max_chars)

    sections: list[str] = []
    pre_lines = lines[:module_starts[0][0]]
    pre_text = " ".join(l for l in pre_lines if len(l) > 10)
    if pre_text:
        sections.append(f"## Documento\n{pre_text[:300]}")

    for idx, (start_i, num) in enumerate(module_starts):
        end_i = module_starts[idx + 1][0] if idx + 1 < len(module_starts) else len(lines)
        block = lines[start_i + 1:end_i]

        title = next((l.strip() for l in block if _is_section_title(l.strip())), f"Módulo {num}")
        kw = next((l.strip() for l in block if _is_keywords_line(l.strip())), "")
        descs = [l.strip() for l in block if len(l.strip()) > 60]
        topics = [l.strip() for l in block
                  if 15 < len(l.strip()) < 60 and _is_section_title(l.strip())]

        parts = [f"## Módulo {num}: {title}"]
        if kw:
            parts.append(f"**Área:** {kw}")
        if descs:
            parts.append(descs[0][:200])
        if topics:
            parts.append("**Subtemas:** " + ", ".join(topics[:8]))

        sections.append("\n".join(parts))

    if code_blocks:
        cb_parts = ["## Código"]
        for cb in code_blocks[:4]:
            cb_parts.append(f"```\n{cb[:600]}\n```")
        sections.append("\n".join(cb_parts))

    result = "\n\n".join(sections)
    return result[:max_chars] if len(result) > max_chars else result


# ---------------------------------------------------------------------------
# Estrategia: tabla de datos (dashboards, comparativas, presupuestos)
# ---------------------------------------------------------------------------

def _structure_table_data(lines: list[str], code_blocks: list[str], max_chars: int) -> str:
    """
    Para dashboards y comparativas cuyos datos llegan como líneas planas.

    model_token_budget.html produce líneas como:
      "Presupuesto de tokens por modelo — Síntesis de pasaporte semántico"
      "Modelo", "Tier", "Ctx oficial", "Overhead", ...
      "llama3.2:3b", "SMALL", "4,096", "435", "1,613", ...
    """
    # Buscar títulos de sección (líneas largas con guión — o con texto descriptivo)
    sections: list[str] = []
    current_section_title = ""
    current_items: list[str] = []

    for line in lines:
        s = line.strip()
        if not s or _is_noise(s):
            continue

        # Línea larga que actúa como título de sección
        is_title = (
            len(s) > 25 and s[0].isupper()
            and not s.endswith(",")
            and ("—" in s or ":" in s or len(s) > 40)
        )

        if is_title and current_items:
            # Volcar la sección anterior
            if current_section_title:
                sections.append(f"## {current_section_title}\n" +
                                 "\n".join(f"- {it}" for it in current_items[:30]))
            else:
                sections.append("\n".join(f"- {it}" for it in current_items[:30]))
            current_items = []
            current_section_title = s
        elif is_title:
            current_section_title = s
        else:
            current_items.append(s)

    if current_items:
        sections.append(f"## {current_section_title}\n" +
                         "\n".join(f"- {it}" for it in current_items[:30]))

    if code_blocks:
        cb_parts = ["## Código"]
        for cb in code_blocks[:4]:
            cb_parts.append(f"```\n{cb[:600]}\n```")
        sections.append("\n".join(cb_parts))

    if not sections:
        return "\n".join(f"- {l}" for l in lines[:60])

    result = "\n\n".join(sections)
    return result[:max_chars] if len(result) > max_chars else result


# ---------------------------------------------------------------------------
# Estrategia: UI/mockup
# ---------------------------------------------------------------------------

def _structure_ui(lines: list[str], max_chars: int) -> str:
    """Para HTMLs que son componentes de UI/mockup sin contenido documental."""
    title_el = next((l for l in lines if len(l) > 5 and not _is_noise(l) and
                     not any(kw in l for kw in _CSS_SIGNALS)), "")
    return (
        f"## Nota: Contenido UI\n"
        f"Este fichero HTML es un componente de interfaz de usuario o mockup. "
        f"El contenido semántico extraíble es mínimo.\n"
        + (f"Título detectado: {title_el}" if title_el else "")
    )[:max_chars]


# ---------------------------------------------------------------------------
# Estrategia: texto genérico
# ---------------------------------------------------------------------------

def _structure_generic(lines: list[str], code_blocks: list[str], max_chars: int) -> str:
    """Para HTMLs sin patrón estructural reconocible."""
    paragraphs: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if not line or _is_noise(line):
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(current)

    sections: list[str] = []
    for i, para in enumerate(paragraphs):
        text = " ".join(para)
        if len(text) < 30:
            continue
        first = text.split(".")[0].strip()[:60]
        sections.append(f"## Sección {i+1}: {first}\n{text[:600]}")

    if code_blocks:
        cb_parts = ["## Código"]
        for cb in code_blocks[:4]:
            cb_parts.append(f"```\n{cb[:600]}\n```")
        sections.append("\n".join(cb_parts))

    if not sections:
        return ("\n".join(lines[:80]))[:max_chars]

    result = "\n\n".join(sections)
    return result[:max_chars] if len(result) > max_chars else result


# ---------------------------------------------------------------------------
# build_focused — RUTA PRINCIPAL
# ---------------------------------------------------------------------------

def build_focused(blocks: list[dict], full_text: str, max_chars: int) -> str:
    """
    Construye el texto enriquecido desde los bloques del WebExtractor.

    Args:
        blocks:    bloques del WebExtractor [{content, content_type, metadata,...}]
        full_text: HTML crudo original (fallback si no hay bloques útiles)
        max_chars: límite de caracteres

    Returns:
        Texto estructurado con ## por sección, listo para el LLM.
    """
    if not blocks:
        return preprocess(full_text, max_chars)

    # Separar el bloque principal del texto y los code blocks
    main_text = ""
    code_blocks: list[str] = []

    for block in blocks:
        ct = block.get("content_type", "text")
        content = (block.get("content") or "").strip()
        if not content:
            continue
        if ct == "code":
            code_blocks.append(content)
        elif ct == "text" and len(content) > len(main_text):
            main_text = content

    if not main_text:
        return preprocess(full_text, max_chars)

    # Limpiar y deduplicar las líneas del bloque principal
    lines = [l.strip() for l in main_text.split("\n") if l.strip()]
    lines = _remove_boilerplate(lines)
    lines = [l for l in lines if not _is_noise(l) and not _SEPARATOR_LINE.match(l)]

    if not lines:
        return preprocess(full_text, max_chars)

    # Detectar tipo y aplicar estrategia
    ct = _detect_content_type(lines)

    if ct == "already_structured":
        return _structure_already(lines, code_blocks, max_chars)
    elif ct == "sectioned_guide":
        return _structure_sectioned_guide(lines, code_blocks, max_chars)
    elif ct == "numbered_modules":
        return _structure_numbered_modules(lines, code_blocks, max_chars)
    elif ct == "table_data":
        return _structure_table_data(lines, code_blocks, max_chars)
    elif ct == "ui_mockup":
        return _structure_ui(lines, max_chars)
    else:
        return _structure_generic(lines, code_blocks, max_chars)


# ---------------------------------------------------------------------------
# preprocess — FALLBACK (recibe HTML crudo)
# ---------------------------------------------------------------------------

def preprocess(full_text: str, max_chars: int) -> str:
    """
    Fallback: preprocesa HTML crudo sin bloques del extractor.

    Limpia el HTML y aplica las mismas estrategias de estructuración
    que build_focused sobre el texto extraído.
    """
    text = _clean_html(full_text)
    lines_raw = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 3]

    seen: set[str] = set()
    lines: list[str] = []
    for l in lines_raw:
        fp = l[:80].lower()
        if fp not in seen and not _is_noise(l):
            seen.add(fp)
            lines.append(l)

    if not lines:
        return text[:max_chars]

    full = "\n".join(lines)

    # Intentar P1: "// 00 — Título" o "## 00 — Título"
    P1 = _re.compile(
        r"^(?://\s*|#+\s*)?(0[0-9]|1[0-9]|2[0-9])\s*[—–-]\s*(.+)",
        _re.MULTILINE,
    )
    matches_p1 = [(m.group(1), m.group(2).strip(), m.start(), m.end())
                  for m in P1.finditer(full)]
    if len(matches_p1) >= 3:
        out = []
        pre = full[:matches_p1[0][2]]
        pre_lines = [l for l in pre.split("\n") if l.strip() and len(l) > 20]
        if pre_lines:
            out.append("## Documento\n" + "\n".join(pre_lines[:6]))
        out.append(f"## Índice ({len(matches_p1)} secciones)")
        out.append("\n".join(f"- Sección {n}: {t}" for n, t, _, __ in matches_p1))
        for i, (num, title, start, end) in enumerate(matches_p1):
            nxt = matches_p1[i + 1][2] if i + 1 < len(matches_p1) else len(full)
            body = [l.strip() for l in full[end:nxt].split("\n")
                    if l.strip() and len(l.strip()) > 20]
            out.append(f"## Sección {num}: {title}\n" + "\n".join(body[:10]))
        result = "\n\n".join(out)
        return result[:max_chars] if len(result) > max_chars else result

    # Intentar P2
    P2 = _re.compile(
        r"(?m)^(0[1-9]|10)\s*\n(.+?)(?=\n(?:0[1-9]|10)\s*\n|\Z)",
        _re.DOTALL,
    )
    matches_p2 = list(P2.finditer(full))
    if len(matches_p2) >= 3:
        out = []
        st = [(m.group(1), m.group(2).strip()) for m in matches_p2]
        out.append(f"## Índice ({len(st)} módulos)")
        out.append("\n".join(f"- Módulo {n}: {t[:60]}" for n, t in st))
        for m in matches_p2:
            bl = [l.strip() for l in m.group(2).split("\n")
                  if l.strip() and len(l.strip()) > 20]
            out.append(f"## Módulo {m.group(1)}: {bl[0][:60] if bl else ''}\n"
                       + "\n".join(bl[:10]))
        result = "\n\n".join(out)
        return result[:max_chars] if len(result) > max_chars else result

    # Delegar en build_focused (sin bloques → crea uno sintético)
    synthetic_block = [{"content": "\n".join(lines), "content_type": "text"}]
    return build_focused(synthetic_block, full_text, max_chars)
