"""
brain/prompts/planner.py
=========================
Planificador de síntesis: decide cuántas llamadas LLM hacer y cómo
dimensionar cada una según el modelo activo y el documento a procesar.

PROPÓSITO
---------
Traduce las capacidades del modelo (ModelProfile) y las características
del documento (tamaño, número de entidades, tipo de contenido) en un
SynthesisPlan concreto: cuántas llamadas, qué secciones genera cada una,
y cuántos tokens de salida reservar para cada llamada.

Desacopla el synthesizer de las decisiones de planificación:
el synthesizer simplemente ejecuta el plan que el planner devuelve.

PLANES DISPONIBLES
------------------
  single-call
      1 llamada con las 7 secciones del pasaporte.
      Usado para: docs pequeños con cualquier modelo, o modelos small.

  2-calls
      Call A: Summary + Core Knowledge  (lectura profunda del documento)
      Call B: Key Insights + Relationships + Usage + Pitfalls + Extract
              (razonamiento sobre lo ya sintetizado en Call A)
      Usado para: docs medianos/grandes con modelos medium.
      Ventaja: Call A puede focalizarse en leer sin generar todo el pasaporte.

  chunked
      Call overview: todas las secciones EXCEPTO Core Knowledge.
      Calls CK 1..N: cada chunk del documento genera subsecciones ###.
      Usado para: docs muy grandes que no caben en el contexto del modelo.
      El número de chunks lo calcula el synthesizer en runtime.

DIMENSIONADO DE max_tokens_out
--------------------------------
El tamaño del pasaporte generado depende de tres variables:

  1. context_chars del modelo  → techo físico (40% del contexto disponible)
  2. n_entities                → cuántas entidades documentar en Core Knowledge
                                 (~150 tokens por entidad)
  3. doc_chars                 → densidad del documento
                                 (bonus de 250-1000 tokens para docs grandes)

Esto resuelve el problema de 3000 tokens fijos para cualquier documento:
un RFP con 40 secciones necesita ~3550 tokens; un script Python con
5 funciones solo necesita ~2000.

El suelo se adapta al modelo: si el modelo es pequeño (techo < suelo),
el suelo baja para no pedir más de lo que el modelo puede generar.

CONFIGURACIÓN POR PERFIL (_PROFILE_CONFIG)
-------------------------------------------
Cada perfil define valores de fallback usados cuando no hay información
del modelo activo (context_chars=0). En producción con model_profiles.py
activo, los valores críticos (chunked_trigger, max_tokens_out) se calculan
dinámicamente y estos son solo el respaldo.

  small:
    - Sin 2-calls (instruction following insuficiente)
    - chunked_trigger bajo: fragmenta antes para no saturar el modelo
    - Prompts cortos: menos tokens de sistema

  medium:
    - 2-calls para docs heterogéneos medianos/grandes
    - chunked_trigger alto como fallback (se sobrescribe por context_chars)
    - chunk_size=3000: equilibrio entre contexto y coherencia de fragmentos

  large/claude:
    - Sin 2-calls: contexto suficiente para todo en 1 llamada
    - chunked_trigger muy alto: chunked solo para documentos enormes
    - max_tokens_per_call=4096: aprovecha la capacidad del modelo

AÑADIR UN NUEVO PERFIL
-----------------------
Si añades un tier nuevo (ej. "xlarge"), añade su entrada en _PROFILE_CONFIG
y actualiza el match en plan_synthesis(). El resto del sistema se adapta
automáticamente.
"""

from dataclasses import dataclass


# ===========================================================================
# Estructuras de datos
# ===========================================================================

@dataclass(frozen=True)
class CallSpec:
    """
    Describe una llamada individual al LLM dentro del plan.

    Atributos:
        sections:          secciones del pasaporte que esta llamada genera.
                           Ej: ("summary", "core_knowledge")
        purpose:           descripción legible del objetivo de esta llamada.
                           Solo para logs y debug.
        needs_prev_result: True si esta llamada necesita el resultado de la
                           anterior como contexto (encadenamiento de calls).
                           El synthesizer sustituye {call1_result} en el prompt.
        max_tokens_out:    tokens máximos de salida para esta llamada.
                           Calculado dinámicamente por _build_two_call_plan()
                           según modelo y documento. Ver docstring del módulo.
        temperature:       temperatura del LLM para esta llamada.
                           0.2-0.3 para síntesis factual; 0.3 para razonamiento.
    """
    sections: tuple
    purpose: str
    needs_prev_result: bool = False
    max_tokens_out: int = 3000
    temperature: float = 0.3


@dataclass(frozen=True)
class SynthesisPlan:
    """
    Plan completo de síntesis devuelto por plan_synthesis().

    Atributos:
        n_calls:          número de calls planificadas (sin contar chunks CK).
        calls:            tupla de CallSpec en orden de ejecución.
        chunked:          True → Core Knowledge se procesa por fragmentos.
                          El synthesizer añade N calls extra en runtime.
        chunk_size:       chars por fragmento de Core Knowledge (chunked=True).
        overview_excerpt: chars del extracto del doc para la call de overview
                          en modo chunked (solo se ve el inicio del documento).
    """
    n_calls: int
    calls: tuple
    chunked: bool = False
    chunk_size: int = 3000
    overview_excerpt: int = 2000

    @property
    def section_names(self) -> tuple:
        """Todas las secciones que este plan genera, en orden."""
        return tuple(s for call in self.calls for s in call.sections)


# ===========================================================================
# CONFIGURACIÓN POR PERFIL — valores de fallback
# ===========================================================================
# Usados cuando context_chars=0 (model_profiles.py no disponible o modelo
# desconocido). En producción normal, los valores críticos se calculan
# dinámicamente en plan_synthesis() y _build_two_call_plan().

_PROFILE_CONFIG: dict[str, dict] = {
    "small": {
        # Docs hasta este tamaño: 1 call completa
        "max_doc_chars_single":  1500,
        # Más allá: chunked obligatorio (small no tiene contexto para más)
        "chunked_trigger":       4000,
        "chunk_size":            1200,
        "overview_excerpt":       600,
        # small no aprovecha 2 calls: instruction following insuficiente
        # para encadenar bien dos generaciones largas
        "supports_two_calls":    False,
        "max_tokens_per_call":   1500,
    },
    "medium": {
        "max_doc_chars_single":  3000,
        # Fallback alto: en producción se sobrescribe por context_chars del modelo.
        # Con model_profiles activo, llama3.1:8b tiene effective_trigger ~93K.
        "chunked_trigger":      80000,
        "chunk_size":            3000,
        "overview_excerpt":      2000,
        "supports_two_calls":    True,
        "max_tokens_per_call":   3000,
    },
    "large": {
        "max_doc_chars_single": 15000,
        "chunked_trigger":      60000,
        "chunk_size":            5000,
        "overview_excerpt":      3000,
        # large tiene contexto suficiente para todo en 1 call
        "supports_two_calls":    False,
        "max_tokens_per_call":   4096,
    },
    "claude": {
        "max_doc_chars_single": 50000,
        # Claude rara vez llega al chunked; solo para documentos muy grandes
        "chunked_trigger":     200000,
        "chunk_size":            8000,
        "overview_excerpt":      4000,
        "supports_two_calls":    False,
        "max_tokens_per_call":   4096,
    },
}


# Secciones canónicas del pasaporte semántico, en orden de aparición
_ALL_SECTIONS = (
    "summary",
    "core_knowledge",
    "key_insights",
    "relationships",
    "practical_usage",
    "pitfalls",
    "source_extract",
)

# División para estrategia de 2 llamadas:
#   Call A: requiere LECTURA PROFUNDA del documento fuente
#   Call B: requiere RAZONAMIENTO sobre lo ya sintetizado en Call A
_TWO_CALL_SPLIT = {
    "call_a": ("summary", "core_knowledge"),
    "call_b": ("key_insights", "relationships", "practical_usage", "pitfalls", "source_extract"),
}


# ===========================================================================
# DECISIÓN PRINCIPAL
# ===========================================================================

def plan_synthesis(
    profile: str,
    doc_chars: int,
    n_entities: int,
    naturaleza: str = "heterogéneo",
    context_chars: int = 0,
    quality_trigger: int | None = None,
) -> SynthesisPlan:
    """
    Decide el plan de síntesis óptimo según modelo, documento y tipo de fichero.

    Args:
        profile:         Tier del modelo: "small" | "medium" | "large" | "claude"
        doc_chars:       Tamaño del processed_text en chars.
        n_entities:      Número de entidades a documentar en Core Knowledge.
        naturaleza:      "homogéneo" | "heterogéneo" — viene de TypeSpec.naturaleza.
        context_chars:   Presupuesto real del modelo en chars (ModelProfile.context_chars).
                         Si > 0, calcula el chunked_trigger efectivo desde el modelo.
        quality_trigger: Umbral de chars a partir del cual chunked mejora la CALIDAD
                         aunque el doc quepa en el contexto del modelo.
                         Viene de TypeSpec.quality_trigger.
                         None → no aplica (tipos homogéneos o modelos large/claude).

    Decisión (en orden de prioridad):
      1. doc_chars > chunk_trigger    → chunked OBLIGATORIO (no cabe en contexto)
      2. doc_chars > quality_trigger  → chunked DE CALIDAD (cabe pero degrada)
      3. 2-calls disponible + heterogéneo + doc mediano → 2-calls
      4. default → single-call

    Returns:
        SynthesisPlan con la estrategia óptima.
    """
    cfg = _PROFILE_CONFIG.get(profile, _PROFILE_CONFIG["large"])

    # ── chunk_trigger: si no cabe en el modelo ─────────────────────────────
    if context_chars > 0:
        effective_chunk_trigger = int((context_chars - 4000) * 0.85)
    else:
        effective_chunk_trigger = cfg["chunked_trigger"]

    # ── quality_trigger: si cabe pero degrada la calidad ───────────────────
    # Solo aplica para tipos heterogéneos y modelos small/medium.
    # Large y claude tienen suficiente capacidad para manejar docs densos.
    effective_quality_trigger = None
    if quality_trigger is not None and profile in ("small", "medium"):
        effective_quality_trigger = quality_trigger

    # ── Decisión 1: chunked obligatorio (no cabe) ──────────────────────────
    if doc_chars > effective_chunk_trigger:
        return _build_chunked_plan(cfg, profile, doc_chars)

    # ── Decisión 2: chunked de calidad (cabe pero degrada) ─────────────────
    if effective_quality_trigger is not None and doc_chars > effective_quality_trigger:
        return _build_chunked_plan(cfg, profile, doc_chars)

    # ── Decisión 3: 2-calls para docs medianos heterogéneos ────────────────
    use_two_calls = (
        cfg["supports_two_calls"]
        and naturaleza == "heterogéneo"
        and (doc_chars > 1500 or n_entities > 8)
    )

    if use_two_calls:
        return _build_two_call_plan(
            cfg,
            context_chars=context_chars,
            doc_chars=doc_chars,
            n_entities=n_entities,
        )

    # ── Por defecto: single-call ────────────────────────────────────────────
    return _build_single_call_plan(cfg)


# ===========================================================================
# BUILDERS DE PLANES
# ===========================================================================

def _build_single_call_plan(cfg: dict) -> SynthesisPlan:
    """1 llamada con las 7 secciones del pasaporte."""
    return SynthesisPlan(
        n_calls=1,
        calls=(
            CallSpec(
                sections=_ALL_SECTIONS,
                purpose="generar pasaporte completo en una llamada",
                needs_prev_result=False,
                max_tokens_out=cfg["max_tokens_per_call"],
                temperature=0.3,
            ),
        ),
        chunked=False,
    )


def _build_two_call_plan(
    cfg: dict,
    context_chars: int = 0,
    doc_chars: int = 0,
    n_entities: int = 0,
) -> SynthesisPlan:
    """
    2 llamadas: lectura+estructura → razonamiento.

    Call A (Summary + Core Knowledge):
        La más densa. Necesita más tokens porque Core Knowledge documenta
        todas las entidades del documento individualmente.

    Call B (Key Insights + Relationships + Usage + Pitfalls + Source Extract):
        Razonamiento sobre el resultado de Call A. Necesita menos tokens
        pero recibe el resultado anterior como contexto.

    max_tokens_out se calcula desde tres variables:
      1. context_chars  → techo físico: 40% del contexto disponible, máx 6000t
      2. n_entities     → ~150 tokens por entidad en Core Knowledge (mín 8)
      3. doc_chars      → bonus por densidad: +250/500/1000t para docs grandes

    El suelo se adapta al modelo: si el techo del modelo es menor que el
    suelo habitual (ej. llama3.2:3b con techo=1200), el suelo baja para
    no pedir más de lo que el modelo puede generar.
    """
    # ── Techo desde el modelo ──────────────────────────────────────────────
    if context_chars > 0:
        # Reservar 40% del contexto para la salida; nunca más de 6000 tokens
        model_ceiling = min(6000, int(context_chars * 0.40 / 4))
    else:
        model_ceiling = cfg["max_tokens_per_call"]

    # ── Estimación desde el contenido ─────────────────────────────────────
    # Call A: Summary (~300t) + Core Knowledge (~150t por entidad, mín 8)
    ck_entities    = max(n_entities, 8)
    ck_tokens      = ck_entities * 150
    summary_tokens = 300
    # Bonus por densidad del documento: más secciones → más texto por sección
    if doc_chars > 30000:
        size_bonus = 1000   # RFP grande, manual técnico
    elif doc_chars > 15000:
        size_bonus = 500    # Notebook con muchas funciones
    elif doc_chars > 8000:
        size_bonus = 250    # Documento mediano
    else:
        size_bonus = 0      # Script pequeño, CSV, tabla

    call_a_estimate = summary_tokens + ck_tokens + size_bonus

    # Call B: Key Insights + Relationships + Usage + Pitfalls + Extract
    # Más corto: son secciones de razonamiento, no de transcripción literal
    call_b_estimate = 1500

    # ── Aplicar techo — suelo adaptado al modelo ──────────────────────────
    # El suelo garantiza un mínimo de sustancia; se adapta si el modelo es pequeño.
    floor_a = min(2000, model_ceiling)
    floor_b = min(1500, model_ceiling // 2)
    call_a_tokens = max(floor_a, min(call_a_estimate, model_ceiling))
    call_b_tokens = max(floor_b, min(call_b_estimate, model_ceiling // 2))

    return SynthesisPlan(
        n_calls=2,
        calls=(
            CallSpec(
                sections=_TWO_CALL_SPLIT["call_a"],
                purpose="leer el documento y estructurar contenido principal",
                needs_prev_result=False,
                max_tokens_out=call_a_tokens,
                temperature=0.3,
            ),
            CallSpec(
                sections=_TWO_CALL_SPLIT["call_b"],
                purpose="razonar e inferir secciones contextuales",
                needs_prev_result=True,
                max_tokens_out=call_b_tokens,
                temperature=0.3,
            ),
        ),
        chunked=False,
    )


def _build_chunked_plan(cfg: dict, profile: str, doc_chars: int = 0) -> SynthesisPlan:
    """
    Síntesis fragmentada: overview call + N calls de Core Knowledge.

    chunk_size: tamaño óptimo de cada fragmento de Core Knowledge.
      - Objetivo: ~10K chars por chunk para que el modelo razone en profundidad.
      - Calculado como ceil(doc_chars / n_chunks) donde n_chunks = ceil(doc_chars / 10K).
      - Mín 3K (fragmentos muy cortos no aportan), máx 12K (saturación).
      - Fallback al cfg si no hay doc_chars.

    Ejemplo: DWP (50K chars) → 5 chunks de 10K → 5 calls de CK
             PDF médico (40K chars) → 4 chunks de 10K → 4 calls de CK
             Notebook (20K chars) → 2 chunks de 10K → 2 calls de CK
    """
    _OPTIMAL_CHUNK = 10_000  # chars: sección temática completa
    _MIN_CHUNK     =  3_000
    _MAX_CHUNK     = 12_000

    if doc_chars > 0:
        import math
        n_chunks   = max(1, math.ceil(doc_chars / _OPTIMAL_CHUNK))
        chunk_size = max(_MIN_CHUNK, min(_MAX_CHUNK, math.ceil(doc_chars / n_chunks)))
    else:
        chunk_size = cfg["chunk_size"]

    overview_sections = tuple(s for s in _ALL_SECTIONS if s != "core_knowledge")

    return SynthesisPlan(
        n_calls=1,
        calls=(
            CallSpec(
                sections=overview_sections,
                purpose="generar todo excepto Core Knowledge (chunked por separado)",
                needs_prev_result=False,
                max_tokens_out=cfg["max_tokens_per_call"],
                temperature=0.3,
            ),
        ),
        chunked=True,
        chunk_size=chunk_size,
        overview_excerpt=cfg["overview_excerpt"],
    )


# ===========================================================================
# UTILIDADES
# ===========================================================================

def section_display_name(section: str) -> str:
    """Convierte el ID interno de sección en nombre visible con emoji."""
    return {
        "summary":         "📌 Summary",
        "core_knowledge":  "🧩 Core Knowledge",
        "key_insights":    "🧠 Key Insights",
        "relationships":   "🔗 Relationships",
        "practical_usage": "⚙️ Practical Usage",
        "pitfalls":        "⚠️ Pitfalls / Risks",
        "source_extract":  "📄 Source Extract",
    }.get(section, section)


def explain_plan(plan: SynthesisPlan) -> str:
    """Genera string descriptivo del plan para logs."""
    if plan.chunked:
        return f"chunked: 1 overview + N chunks (chunk_size={plan.chunk_size})"
    if plan.n_calls == 1:
        return "single-call (7 secciones en 1 llamada)"
    calls_desc = " → ".join(
        f"{','.join(c.sections)} [max={c.max_tokens_out}t]"
        for c in plan.calls
    )
    return f"{plan.n_calls}-calls: {calls_desc}"
