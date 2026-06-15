"""
blocks.py
---------
Bloques componibles para construir system prompts y user messages.

Filosofía:
  - Un prompt = base_invariante + bloques_por_sección + bloque_de_tipo + restricciones
  - Cada bloque es una función pura que toma parámetros y devuelve string
  - El builder.py compone los bloques según el plan, profile y type_spec

Esto evita el prompt monolítico de 800 tokens y permite generar exactamente
las instrucciones necesarias para cada llamada concreta.
"""

from .type_specs import TypeSpec


# ===========================================================================
# BLOQUES BASE (invariantes — siempre presentes)
# ===========================================================================

def block_identity(profile: str) -> str:
    """Identidad del asistente. Más larga para large/claude, mínima para small."""
    if profile == "small":
        return "Completa secciones del pasaporte semántico dado."
    return "Eres un experto en gestión del conocimiento técnico."


def block_constraints(profile: str) -> str:
    """Restricciones siempre presentes."""
    if profile == "small":
        return (
            "Reglas:\n"
            "- No modifiques frontmatter, tags ni entities\n"
            "- Basa todo en el documento dado, no inventes\n"
            "- Responde solo Markdown"
        )
    return (
        "RESTRICCIONES:\n"
        "- Frontmatter EXACTAMENTE como está (no modificar tags ni entities)\n"
        "- Responde SOLO en Markdown, sin meta-comentarios\n"
        "- No inventes información que no esté en el documento dado"
    )


# ===========================================================================
# BLOQUES POR SECCIÓN
# Cada función genera la instrucción para una sección concreta del pasaporte.
# Las instrucciones se enriquecen con datos del TypeSpec.
# ===========================================================================

def block_summary(spec: TypeSpec, profile: str) -> str:
    if profile == "small":
        return "# 📌 Summary\n- 6-10 líneas: qué es, para qué sirve, contexto"
    return (
        "# 📌 Summary\n"
        f"- 6-10 líneas: {spec.summary_hint}\n"
        "- Usa información LITERAL del documento. No inventes."
    )


def block_core_knowledge(spec: TypeSpec, profile: str, n_entities: int) -> str:
    """Bloque crítico: el LLM debe documentar cada entity con una subsección ###."""
    if profile == "small":
        return (
            "# 🧩 Core Knowledge\n"
            f"- Una subsección ### por cada {spec.unidad}\n"
            f"- Formato: {_first_line(spec.ck_format)}"
        )

    entity_clause = (
        f"- N entities del frontmatter → N subsecciones ###. Cubre cada una.\n"
        if n_entities > 0
        else ""
    )

    return (
        "# 🧩 Core Knowledge\n"
        f"- Unidad: {spec.unidad}\n"
        f"{entity_clause}"
        f"- Fuente primaria: {spec.source_primary}\n"
        f"- Formato de cada subsección:\n{_indent(spec.ck_format, '  ')}"
    )


def block_key_insights(spec: TypeSpec, profile: str) -> str:
    if profile == "small":
        return (
            "# 🧠 Key Insights\n"
            "- Mínimo 3 inferencias específicas, no triviales\n"
            "- NO repitas Summary ni Core Knowledge"
        )
    return (
        "# 🧠 Key Insights\n"
        f"- Mínimo 4 puntos: {spec.insights_hint}\n"
        "- Lo NO explícito pero inferible del diseño o estructura\n"
        "- NO repitas Summary ni Core Knowledge"
    )


def block_relationships(spec: TypeSpec, profile: str) -> str:
    return (
        "# 🔗 Relationships\n"
        "- **Depends on**: artefactos/sistemas necesarios\n"
        "- **Related to**: otros documentos o componentes\n"
        "- **Enables**: qué hace posible este artefacto"
    )


def block_practical_usage(spec: TypeSpec, profile: str) -> str:
    """Adapta el estilo de Usage según spec.usage_style."""
    style_intro = {
        "código":      "Pasos concretos + ejemplo de código REAL basado en el documento",
        "consulta":    "Cuándo y cómo consultar; filtros o búsquedas típicas",
        "proceso":     "Cuándo consultarlo, quién, para qué decisión. NO incluir código",
        "navegación":  "Cómo navegar el artefacto, qué decisiones soporta",
    }.get(spec.usage_style, "Pasos concretos de uso real")

    return (
        "# ⚙️ Practical Usage\n"
        f"- {style_intro}\n"
        f"- {spec.usage_hint}"
    )


def block_pitfalls(spec: TypeSpec, profile: str) -> str:
    if profile == "small":
        return (
            "# ⚠️ Pitfalls / Risks\n"
            "- Mínimo 3 puntos específicos al documento, no genéricos"
        )
    return (
        "# ⚠️ Pitfalls / Risks\n"
        f"- Mínimo 3 puntos: {spec.pitfalls_hint}\n"
        "- Menciona nombres reales del documento, no genéricos"
    )


def block_source_extract(spec: TypeSpec, profile: str) -> str:
    return (
        "# 📄 Source Extract\n"
        "- 5-10 líneas literales del fragmento más representativo\n"
        "- Formato: cada línea precedida de `>` (blockquote Markdown)\n"
        "- Texto literal del documento, sin parafrasear, sin triple-backtick"
    )


# ===========================================================================
# BLOQUE DE PROHIBICIONES (específico del tipo)
# ===========================================================================

def block_prohibitions(spec: TypeSpec) -> str:
    if not spec.prohibitions:
        return ""
    items = "\n".join(f"- NO incluyas: {p}" for p in spec.prohibitions)
    return f"PROHIBICIONES ESPECÍFICAS PARA {spec.extension.upper()}:\n{items}"


# ===========================================================================
# BLOQUE DE CONTEXTO (qué hay en el processed_text)
# ===========================================================================

def block_context_guide(profile: str, has_toc: bool, has_subtype: bool,
                        subtype: str | None = None) -> str:
    """Guía al LLM sobre qué bloques esperar en el documento preprocesado."""
    if profile == "small":
        parts = ["NOTA: usa el documento dado como fuente."]
        if has_toc:
            parts.append("'## Índice del documento' es el esqueleto de Core Knowledge.")
        return " ".join(parts)

    lines = ["NOTA SOBRE EL CONTEXTO DEL DOCUMENTO:"]
    if has_toc:
        lines.append(
            "- '## Índice del documento': estructura real del doc — "
            "ÚSALA como esqueleto OBLIGATORIO de Core Knowledge"
        )
    lines.append("- '## Documentación': fuente primaria — cita literalmente")
    lines.append("- '## Firmas de funciones': para identificar entidades")
    lines.append("- '## Estructura': referencia de campos/elementos")
    if has_subtype and subtype:
        lines.append(f"- Subtipo detectado: {subtype}")

    return "\n".join(lines)


# ===========================================================================
# BLOQUE DE ENTITIES (cobertura obligatoria)
# ===========================================================================

def block_entity_coverage(entities: list[str], spec: TypeSpec) -> str:
    """
    Instrucción explícita para cubrir cada entity en Core Knowledge.
    Se inyecta en el user_msg, justo antes del documento.
    """
    if not entities:
        return ""
    listing = "\n".join(f"  - {e}" for e in entities[:25])
    return (
        f"ENTITIES A CUBRIR EN CORE KNOWLEDGE ({len(entities)} elementos — "
        f"una subsección ### por cada una):\n{listing}\n"
        "INSTRUCCIÓN: si el documento tiene info de la entity, documéntala. "
        "Si no, omítela. NO inventes contenido sin base en el documento."
    )


# ===========================================================================
# BLOQUE DE FORMATO DE RESPUESTA
# Crítico para instruction following: dice exactamente qué headers esperar
# ===========================================================================

def block_response_format(section_ids: tuple, profile: str) -> str:
    """Template del output esperado: solo los headers de las secciones del plan."""
    from .planner import section_display_name

    headers = "\n".join(f"# {section_display_name(s)}\n[contenido]\n"
                        for s in section_ids)
    if profile == "small":
        return f"FORMATO DE RESPUESTA:\n{headers}"
    return (
        f"FORMATO DE RESPUESTA (exacto, sin variación):\n{headers}\n"
        "Responde SOLO con estas secciones en Markdown. Nada más."
    )


# ===========================================================================
# HELPERS
# ===========================================================================

def _first_line(text: str) -> str:
    """Devuelve la primera línea no vacía de un texto multilínea."""
    for line in text.split("\n"):
        if line.strip():
            return line.strip()
    return text


def _indent(text: str, indent: str = "  ") -> str:
    """Indenta cada línea de un texto."""
    return "\n".join(indent + line for line in text.split("\n"))
