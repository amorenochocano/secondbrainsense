"""
builder.py
----------
Constructor de prompts: dado un plan, una ficha de tipo y un contexto,
genera las tuplas (system_prompt, user_msg) para cada call del plan.

Esto es el orquestador final que ata todo:
  type_specs.TypeSpec  +  planner.SynthesisPlan  +  blocks.*  →  [(sys, usr), ...]
"""

from typing import Optional

from .type_specs import TypeSpec
from .planner   import SynthesisPlan, CallSpec, section_display_name
from .subtypes  import detect_subtype
from . import blocks as B


# ===========================================================================
# CONSTRUCTOR PRINCIPAL
# ===========================================================================

def build_calls(
    partial_passport: str,
    source: str,
    file_type: str,
    processed_text: str,
    spec: TypeSpec,
    plan: SynthesisPlan,
    profile: str,
    entities: list[str],
    current_md: Optional[str] = None,
) -> list[tuple[str, str]]:
    """
    Construye la lista de (system_prompt, user_msg) según el plan.

    Para planes de 1 call: devuelve 1 tupla.
    Para planes de 2 calls: devuelve 2 tuplas. La segunda contiene el placeholder
                            "{call1_result}" que el synthesizer sustituye en runtime.
    Para planes chunked: devuelve 1 tupla (overview). Los chunks de CK se generan
                         con build_chunk_call() en runtime.

    Args:
        partial_passport: passport pre-rellenado (frontmatter + entities)
        source:           nombre del fichero
        file_type:        extensión
        processed_text:   texto preprocesado del documento
        spec:             TypeSpec del tipo de fichero
        plan:             SynthesisPlan del planner
        profile:          "small" | "medium" | "large" | "claude"
        entities:         lista de entities del frontmatter
        current_md:       .md existente para modo mejora (opcional)

    Returns:
        Lista de tuplas (system, user_msg).
    """
    # Detectar subtipo si el tipo lo soporta
    subtype = None
    if spec.has_subtypes:
        subtype = detect_subtype(file_type, processed_text)

    # Detectar señales en el processed_text
    has_toc = "## Índice del documento" in processed_text

    # Construir una tupla por cada CallSpec del plan
    result = []
    for i, call_spec in enumerate(plan.calls):
        system = _build_system_prompt(
            call_spec=call_spec,
            spec=spec,
            profile=profile,
            n_entities=len(entities),
            subtype=subtype,
        )
        user = _build_user_message(
            call_spec=call_spec,
            partial_passport=partial_passport,
            source=source,
            file_type=file_type,
            processed_text=processed_text,
            spec=spec,
            entities=entities,
            profile=profile,
            current_md=current_md if i == 0 else None,  # modo mejora solo en call 1
            has_toc=has_toc,
            subtype=subtype,
            is_followup=call_spec.needs_prev_result,
        )
        result.append((system, user))

    return result


# ===========================================================================
# SYSTEM PROMPT BUILDER
# ===========================================================================

def _build_system_prompt(
    call_spec: CallSpec,
    spec: TypeSpec,
    profile: str,
    n_entities: int,
    subtype: Optional[str],
) -> str:
    """Compone el system prompt para una call del plan."""
    parts = []

    # 1. Identidad
    parts.append(B.block_identity(profile))

    # 2. Propósito de esta llamada (solo si es multi-call)
    if call_spec.needs_prev_result:
        parts.append(
            f"\nTarea: {call_spec.purpose}.\n"
            "Tienes el resultado de la llamada anterior — úsalo como contexto."
        )
    elif call_spec.purpose and profile not in ("small",):
        parts.append(f"\nTarea: {call_spec.purpose}.")

    # 3. Bloques por sección que ESTA call debe generar
    section_blocks = []
    for section_id in call_spec.sections:
        block = _section_block(section_id, spec, profile, n_entities)
        if block:
            section_blocks.append(block)

    if section_blocks:
        parts.append("\nSECCIONES A GENERAR:\n\n" + "\n\n".join(section_blocks))

    # 4. Subtipo (si detectado)
    if subtype and profile != "small":
        parts.append(f"\nSUBTIPO DETECTADO: {subtype}")

    # 5. Prohibiciones específicas del tipo
    prohib = B.block_prohibitions(spec)
    if prohib and profile != "small":
        parts.append("\n" + prohib)

    # 6. Formato de respuesta exacto
    parts.append("\n" + B.block_response_format(call_spec.sections, profile))

    # 7. Restricciones invariantes (solo en call 1 o single-call para no repetir)
    if not call_spec.needs_prev_result:
        parts.append("\n" + B.block_constraints(profile))

    return "\n".join(parts).strip()


def _section_block(section_id: str, spec: TypeSpec, profile: str, n_entities: int) -> str:
    """Despacha al builder de bloque correspondiente."""
    if section_id == "summary":
        return B.block_summary(spec, profile)
    if section_id == "core_knowledge":
        return B.block_core_knowledge(spec, profile, n_entities)
    if section_id == "key_insights":
        return B.block_key_insights(spec, profile)
    if section_id == "relationships":
        return B.block_relationships(spec, profile)
    if section_id == "practical_usage":
        return B.block_practical_usage(spec, profile)
    if section_id == "pitfalls":
        return B.block_pitfalls(spec, profile)
    if section_id == "source_extract":
        return B.block_source_extract(spec, profile)
    return ""


# ===========================================================================
# USER MESSAGE BUILDER
# ===========================================================================

def _build_user_message(
    call_spec: CallSpec,
    partial_passport: str,
    source: str,
    file_type: str,
    processed_text: str,
    spec: TypeSpec,
    entities: list[str],
    profile: str,
    current_md: Optional[str],
    has_toc: bool,
    subtype: Optional[str],
    is_followup: bool,
) -> str:
    """Compone el user_msg para una call concreta."""
    parts = [f"Documento: {source} | Tipo: {file_type}"]

    if is_followup:
        # Call de seguimiento: recibe el resultado de la previa como contexto
        parts.append("\nRESULTADO DE LA LLAMADA ANTERIOR (Summary + Core Knowledge):")
        parts.append("{call1_result}")  # placeholder, lo sustituye synthesizer
        # Incluir extracto del documento para Source Extract
        if "source_extract" in call_spec.sections:
            excerpt_size = 2000 if profile != "small" else 600
            excerpt = processed_text[:excerpt_size]
            parts.append("\nDOCUMENTO ORIGINAL (para Source Extract):")
            parts.append(f"---\n{excerpt}\n---")
    else:
        # Call principal: necesita el passport parcial y el documento completo
        parts.append("\nPASAPORTE PARCIAL (frontmatter + entities pre-rellenadas):")
        parts.append(partial_passport)

        # Cobertura obligatoria de entities (solo si CK está en esta call)
        if "core_knowledge" in call_spec.sections:
            entity_block = B.block_entity_coverage(entities, spec)
            if entity_block:
                parts.append("\n" + entity_block)

        # Documento preprocesado
        parts.append("\nDOCUMENTO:")
        parts.append(f"---\n{processed_text}\n---")

        # Modo mejora si hay current_md
        if current_md:
            parts.append("\nPASAPORTE ACTUAL (en disco — usa como base, conserva lo bueno):")
            parts.append(current_md[:3000])

        # Guía de contexto
        parts.append("\n" + B.block_context_guide(profile, has_toc, bool(subtype), subtype))

    # Instrucción final
    if is_followup:
        parts.append("\nGenera ahora las secciones indicadas.")
    elif current_md:
        parts.append("\nMejora y completa el pasaporte. Conserva secciones bien escritas, reescribe las [LLM] o incompletas.")
    else:
        parts.append("\nGenera ahora las secciones indicadas.")

    return "\n".join(parts).strip()


# ===========================================================================
# CHUNK BUILDER (para estrategia chunked en docs muy grandes)
# ===========================================================================

def build_chunk_call(
    file_type: str,
    spec: TypeSpec,
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    profile: str,
) -> tuple[str, str]:
    """
    Construye la tupla (system, user) para una llamada de chunk de Core Knowledge.
    Usado por el synthesizer en runtime para procesar cada fragmento de docs grandes.
    """
    system = (
        f"Eres experto en documentación técnica. Tu única tarea: generar "
        f"subsecciones ### de Core Knowledge para el fragmento dado.\n\n"
        f"Para cada {spec.unidad} del fragmento:\n\n"
        f"{spec.ck_format}\n\n"
        f"REGLAS:\n"
        f"- Solo subsecciones ### (sin # ni ##)\n"
        f"- Sin texto introductorio ni conclusiones\n"
        f"- Información literal del fragmento, sin inventar"
    )

    user = (
        f"Fragmento {chunk_index}/{total_chunks}:\n"
        f"---\n{chunk}\n---\n\n"
        f"Produce las subsecciones ### para cada {spec.unidad} de este fragmento."
    )

    return system, user
