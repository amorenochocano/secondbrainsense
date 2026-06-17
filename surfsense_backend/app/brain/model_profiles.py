"""
brain/model_profiles.py
========================
Perfiles de modelos LLM para el motor de síntesis del Second Brain.

PROPÓSITO
---------
Conecta las capacidades reales de cada modelo con las decisiones del
synthesizer y el planner:

  - ¿Cuánto texto del documento puede ver el modelo en una llamada?
  - ¿A partir de qué tamaño conviene fragmentar la síntesis?
  - ¿Qué nivel de prompt usar (small/medium/claude)?
  - ¿Tiene contexto suficiente para el modo mejora (re-ingesta de .md)?

CÓMO AÑADIR UN MODELO NUEVO
----------------------------
Añade una entrada en _REGISTRY con:

  name:           nombre canónico de Ollama (ej. "qwen2.5-coder:7b")
                  El matching es por substring, no exacto.
  prompt_tier:    "small" | "medium" | "claude"
                    small  → modelos ≤4B; prompts cortos; sin modo mejora
                    medium → modelos 7-14B; prompts completos; modo mejora
                    claude → modelos API (Claude, GPT-4); prompts extensos
  context_tokens: ventana de contexto CONSERVADORA en tokens.
                  Regla práctica para modelos Ollama en CPU:
                    - ≤4B:  usar 30-50% del máximo oficial
                    - 7-8B: usar 25-30% del máximo oficial
                    - 14B+: usar 30-40% del máximo oficial
                  Motivo: KV cache, overhead de cuantización, latencia.
                  El máximo oficial siempre supera lo práctico en CPU sin GPU.
  backend:        "ollama" | "api"

PRESUPUESTOS CALCULADOS (propiedades derivadas)
-----------------------------------------------
Todos se calculan automáticamente desde context_tokens. No hay que ajustarlos.

  context_chars   → chars disponibles para (documento + pasaporte parcial)
                    en el mensaje de usuario. Fórmula:
                    (context_tokens - system_tokens - passport_tokens - output_reserve) × 4
                    Representa el presupuesto real para el texto del documento.

  chunk_trigger   → umbral a partir del cual el planner activa síntesis
                    fragmentada (chunked). = context_chars × 80%.
                    Si el processed_text supera este valor, el documento
                    no cabe en una sola llamada al LLM.

  chunk_size      → tamaño de cada fragmento en síntesis chunked.
                    = min(3000, context_chars ÷ 3)

  max_current_md  → máximo de chars del .md existente en modo mejora.
                    0 si el modelo no soporta modo mejora (small).

  supports_improve_mode → True si el modelo tiene contexto para re-leer
                    el pasaporte actual y mejorarlo en lugar de regenerarlo.

PROPIEDADES DE RETRIEVAL — F4 Router Multinivel
------------------------------------------------
Propiedades adicionales específicas para la cascada de consulta F4.
Son DISTINTAS de las de síntesis porque el caso de uso es diferente:

  Síntesis (F3): el modelo lee UN documento grande partido en trozos.
  Retrieval (F4): el modelo lee N chunks DISTINTOS del corpus indexado.

  retrieval_chunk_budget → número máximo de chunks a incluir en el
                    prompt de consulta. Derivado de context_chars con
                    caps por tier (small: 1-2, medium: 3-8, claude: 8-20).
                    Usado en brain_routes.py como parámetro 'budget' en
                    _extract_chunks(), _extract_chunks_from_orm() y
                    _extract_chunks_from_web().

  retrieval_chunk_max_chars → longitud máxima de texto por chunk en el
                    prompt de consulta. Derivado de context_chars con caps
                    por tier (small: 500, medium: 1200, claude: 3000).
                    Usado para truncar ScoredPoints y Chunks ORM antes
                    de incluirlos en el contexto del LLM.

FLUJO DE DECISIÓN EN EL SYNTHESIZER
-------------------------------------
  1. get_profile(model_name) → ModelProfile
  2. max_chars = min(profile.context_chars, _MAX_CHARS[file_type])
     → _MAX_CHARS es el techo por tipo; context_chars es el techo del modelo
     → se usa el menor para no sobrepasar ninguno de los dos límites
  3. processed_text = build_doc_focused_text(blocks, full_text, ft, max_chars)
  4. plan = plan_synthesis(..., context_chars=profile.context_chars)
     → el planner usa context_chars para calcular chunked_trigger y max_tokens_out
     → max_tokens_out se ajusta dinámicamente según tamaño del doc y n_entities

CALIBRACIÓN DE context_tokens POR MODELO
-----------------------------------------
Valores basados en pruebas empíricas en CPU (sin GPU):

  llama3.2:3b        4K    (128K real; en CPU 4K práctico sin timeout)
  qwen2.5-coder:3b   6K    (32K real; en CPU 6K práctico)
  phi3:mini          3.5K  (4K real; sin margen)
  phi3.5:mini        6K    (128K real; en CPU 6K práctico)
  qwen2.5-coder:7b   28K   (32K real; 28K conservador; modelo recomendado)
  llama3.1:8b        30K   (128K real; 30K conservador en CPU)
  deepseek-r1:7b     20K   (128K real; reducido porque <think> consume tokens)
  llama3.2-vision:11b 16K  (128K real; limitado por pesos multimodales)
  llama3.1:70b       100K  (128K real; 100K conservador)
  qwen2.5:14b        40K   (128K real; 40K conservador en CPU)
  mistral:7b         28K   (32K real; 28K conservador)
  mistral-nemo       80K   (128K real; modelo eficiente)
  claude-*/gpt-4*    180K  (200K real; 180K conservador)

NOTA SOBRE deepseek-r1
-----------------------
Este modelo genera un bloque <think>...</think> antes de la respuesta.
Los tokens de razonamiento consumen contexto de salida sin aportar al
pasaporte. Por eso context_tokens es más bajo (20K en lugar de 128K real):
el razonamiento interno puede consumir miles de tokens extra.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    """
    Perfil de un modelo LLM con sus capacidades y presupuestos calculados.

    Inmutable (frozen=True) para que sea seguro usarlo como singleton
    cacheado y pasarlo entre funciones sin riesgo de modificación.
    """

    name: str
    """Nombre canónico del modelo. El matching en get_profile() es por substring."""

    prompt_tier: str
    """
    Nivel de prompt: "small" | "medium" | "claude"
    Determina qué system prompt y qué instrucciones por tipo se usan.
      small  → prompts cortos y directivos; sin modo mejora
      medium → prompts completos con instrucciones detalladas por tipo
      claude → prompts extensos aprovechando la mayor capacidad del modelo
    """

    context_tokens: int
    """
    Ventana de contexto CONSERVADORA en tokens.
    Ver sección CALIBRACIÓN en el docstring del módulo.
    """

    backend: str
    """Backend del modelo: "ollama" | "api" """

    # ------------------------------------------------------------------
    # Propiedades derivadas — calculadas automáticamente
    # ------------------------------------------------------------------

    @property
    def context_chars(self) -> int:
        """
        Chars disponibles para (documento + pasaporte parcial) en el user_msg.

        Fórmula:
          (context_tokens − system_tokens − passport_tokens − output_reserve) × 4

          system_tokens:   tokens del system prompt (~85 small, ~252 medium, ~180 claude)
          passport_tokens: pasaporte parcial fijo ~350 tokens
          output_reserve:  tokens reservados para la salida del LLM
                           (~300 small, ~700 medium, ~900 claude)

        Mínimo garantizado: 1000 chars (para que siempre haya algo que procesar).
        """
        system_t   = {"small": 85,  "medium": 252, "claude": 180}.get(self.prompt_tier, 252)
        passport_t = 350
        output_t   = {"small": 300, "medium": 700, "claude": 900}.get(self.prompt_tier, 700)
        available  = self.context_tokens - system_t - passport_t - output_t
        return max(1000, available * 4)

    @property
    def chunk_trigger(self) -> int:
        """
        Umbral de chars a partir del cual el planner activa síntesis fragmentada.

        = context_chars × 80%

        Si processed_text > chunk_trigger, el documento no cabe completo en
        una sola llamada. El planner activará chunked (overview + N fragmentos
        de Core Knowledge). En la práctica, con modelos de 28K+ tokens el
        chunk_trigger supera los 90K chars, suficiente para cualquier RFP.
        """
        return int(self.context_chars * 0.80)

    @property
    def chunk_size(self) -> int:
        """
        Tamaño de cada fragmento en síntesis chunked (chars).

        = min(3000, context_chars ÷ 3)

        Limita a 3000 para que el modelo tenga contexto suficiente para
        generar subsecciones ### completas por cada fragmento.
        """
        return min(3000, self.context_chars // 3)

    @property
    def max_current_md(self) -> int:
        """
        Máximo de chars del .md existente que se incluye en modo mejora.

        0 → modelo small: sin contexto para el .md previo; siempre regenera.
        >0 → modelo medium/claude: re-lee el pasaporte actual y lo mejora
             en lugar de generar desde cero. Mejor calidad en re-ingestas.
        """
        if self.prompt_tier == "small":
            return 0
        if self.prompt_tier == "medium":
            return min(2000, self.context_chars // 6)
        return 3000  # claude

    @property
    def supports_improve_mode(self) -> bool:
        """True si el modelo tiene contexto para modo mejora (re-ingesta)."""
        return self.max_current_md > 0

    # ------------------------------------------------------------------
    # Propiedades de retrieval — F4 Router Multinivel
    # ------------------------------------------------------------------
    # Estas propiedades son distintas de las de síntesis (chunk_size,
    # chunk_trigger) porque el caso de uso es diferente:
    #
    # Síntesis (F3):                    Retrieval (F4):
    #   Doc completo → LLM              Chunks recuperados → LLM
    #   1 doc grande fragmentado        N chunks pequeños del corpus
    #   Objetivo: generar pasaporte     Objetivo: responder una pregunta
    #   Budget: processar el texto      Budget: caber en el prompt del chat
    #
    # En retrieval el modelo NO lee el documento completo — lee los K
    # chunks más relevantes del corpus. El presupuesto es distinto:
    # - No hay pasaporte parcial que ocupe tokens
    # - Sí hay historial de conversación (~4-8 turnos)
    # - Los chunks deben ser cortos para que quepan varios a la vez
    # ------------------------------------------------------------------

    @property
    def retrieval_chunk_max_chars(self) -> int:
        """
        Longitud máxima de texto de cada chunk individual en el prompt de
        consulta del Router Multinivel (F4 brain_routes.py).

        USO:
          Se aplica en _extract_chunks() y _extract_chunks_from_orm() para
          truncar el texto de cada ScoredPoint/Chunk antes de incluirlo
          en el contexto que se envía al LLM.

        POR QUÉ DISTINTO A chunk_size (síntesis):
          chunk_size (F3) controla el tamaño de fragmentos del DOCUMENTO
          durante síntesis multi-call. En retrieval el chunk ya está en
          Qdrant/PostgreSQL — lo truncamos para que quepan varios a la vez.

        FÓRMULA:
          context_chars ÷ (retrieval_chunk_budget × 3)
          Cap al máximo coherente por tier:
            small:  500 chars  — ventana 6K, 2 chunks, poco margen
            medium: 1200 chars — ventana 28K, 6 chunks, contexto cómodo
            claude: 3000 chars — ventana 180K, sin restricción práctica

        EJEMPLOS CON MODELOS REALES:
          qwen2.5-coder:3b  (small,  6K ctx)  →  500 chars/chunk
          qwen2.5-coder:7b  (medium, 28K ctx) → 1200 chars/chunk
          llama3.1:8b       (medium, 30K ctx) → 1200 chars/chunk
          claude-sonnet     (claude, 180K ctx) → 3000 chars/chunk

        RELACIÓN CON CERO HARDCODE:
          Este valor se deriva automáticamente del context_tokens del modelo
          registrado en _REGISTRY. Al cambiar SYNTHESIS_MODEL en .env, el
          presupuesto de retrieval se adapta sin tocar brain_routes.py.
        """
        caps = {"small": 500, "medium": 1200, "claude": 3000}
        budget = self.retrieval_chunk_budget
        # Calcular cuántos chars por chunk caben en el contexto disponible
        # reservando 1/3 para el system prompt + pregunta + historial
        available_for_chunks = self.context_chars * 2 // 3
        computed = available_for_chunks // max(1, budget)
        return min(computed, caps.get(self.prompt_tier, 1200))

    @property
    def retrieval_chunk_budget(self) -> int:
        """
        Número máximo de chunks a incluir en el prompt de consulta del
        Router Multinivel (F4 brain_routes.py).

        USO:
          Se usa en brain_query() para limitar cuántos ScoredPoints/Chunks
          ORM se extraen y envían al LLM. Es el parámetro 'budget' en
          _extract_chunks(), _extract_chunks_from_orm() y _extract_chunks_from_web().

        POR QUÉ DISTINTO A chunk_trigger / chunk_size (síntesis):
          En síntesis (F3) el modelo lee 1 documento grande partido en
          trozos. En retrieval (F4) el modelo lee N chunks DISTINTOS del
          corpus indexado. El budget controla cuántas ‘fuentes diferentes’
          puede leer a la vez antes de saturar la ventana de contexto.

        FÓRMULA:
          context_chars ÷ retrieval_chunk_max_chars (con mínimos y máximos
          por tier para evitar extremos por modelos atípicos):
            small:  mín=1, máx=2   — ventana pequeña, mejor 1-2 chunks
            medium: mín=3, máx=8   — ventana 28K, 4-6 chunks óptimos
            claude: mín=8, máx=20  — ventana enorme, hasta 15-20 chunks

        EJEMPLOS CON MODELOS REALES:
          qwen2.5-coder:3b  (small,  6K ctx)  →  2 chunks máx
          qwen2.5-coder:7b  (medium, 28K ctx) →  6 chunks máx
          llama3.1:8b       (medium, 30K ctx) →  6 chunks máx
          claude-sonnet     (claude, 180K ctx) → 15 chunks máx

        IMPACTO EN CALIDAD vs LATENCIA:
          Más chunks = más contexto = mejor respuesta, pero:
            - Modelos small: saturan con >2 chunks, respuesta degradada
            - Modelos medium: punto óptimo 4-6 chunks
            - Modelos claude: escalan bien hasta 15-20 chunks
          El cap por tier previene que un modelo small reciba 10 chunks
          aunque matemáticamente ‘quepan’ en sus tokens.

        RELACIÓN CON BM25 (L2.b):
          En BM25 se recuperan (budget × 2) candidatos para compensar
          la menor precisión semántica del keyword search respecto a
          la búsqueda vectorial de Qdrant.
        """
        floors = {"small": 1, "medium": 3, "claude": 8}
        caps   = {"small": 2, "medium": 8, "claude": 20}
        # Máximo teórico: cuántos chunks de tamaño 'retrieval_chunk_max_chars'
        # caben en 2/3 del contexto disponible (reservamos 1/3 para meta)
        # Usamos 500 como tamaño de chunk estimado para evitar recursividad
        estimated_chunk = {"small": 500, "medium": 1200, "claude": 3000}.get(self.prompt_tier, 1200)
        theoretical = (self.context_chars * 2 // 3) // max(1, estimated_chunk)
        floor = floors.get(self.prompt_tier, 3)
        cap   = caps.get(self.prompt_tier, 8)
        return max(floor, min(theoretical, cap))

    def __str__(self) -> str:
        return (
            f"ModelProfile({self.name} | tier={self.prompt_tier} | "
            f"ctx={self.context_tokens // 1000}K tokens | "
            f"doc_budget={self.context_chars // 1000}K chars | "
            f"chunked_at={self.chunk_trigger // 1000}K chars | "
            f"improve={'✓' if self.supports_improve_mode else '✗'})"
        )


# ===========================================================================
# REGISTRO DE MODELOS
# ===========================================================================
# El matching en get_profile() es por substring (case-insensitive):
#   "qwen2.5-coder:7b" matchea "qwen2.5-coder:7b-instruct-q4_K_M"
#   "llama3.1" matchea "llama3.1:8b" y "llama3.1:70b" (usar el más específico)

_REGISTRY: list[ModelProfile] = [

    # ── SMALL (≤4B) ────────────────────────────────────────────────────────
    # Instruction following limitado. Single-call o chunked automático.
    # Sin modo mejora (contexto insuficiente para releer el .md anterior).
    # Adecuados para pruebas rápidas o documentos muy pequeños.

    ModelProfile(
        name="llama3.2:3b",
        prompt_tier="small",
        context_tokens=4000,    # 128K real; en CPU 4K práctico sin OOM/timeout
        backend="ollama",
    ),
    ModelProfile(
        name="qwen2.5-coder:3b",
        prompt_tier="small",
        context_tokens=6000,    # 32K real; en CPU ~6K práctico
        backend="ollama",
    ),
    ModelProfile(
        name="phi3:mini",
        prompt_tier="small",
        context_tokens=3500,    # 4K real; modelo antiguo, sin margen
        backend="ollama",
    ),
    ModelProfile(
        name="phi3.5:mini",
        prompt_tier="small",
        context_tokens=6000,    # 128K real; en CPU ~6K práctico
        backend="ollama",
    ),

    # ── MEDIUM (7-14B) ─────────────────────────────────────────────────────
    # Buen instruction following. 2-calls para docs medianos, chunked para
    # docs grandes. Modo mejora activo. Adecuados para producción en CPU.

    ModelProfile(
        name="qwen2.5-coder:7b",
        prompt_tier="medium",
        context_tokens=28000,   # 32K real; 28K conservador por KV cache en CPU
        backend="ollama",       # RECOMENDADO: mejor balance calidad/velocidad
    ),
    ModelProfile(
        name="llama3.1:8b",
        prompt_tier="medium",
        context_tokens=30000,   # 128K real; 30K conservador en CPU
        backend="ollama",       # context_chars ~114K → 2-calls para docs <90K chars
    ),
    ModelProfile(
        name="deepseek-r1:7b",
        prompt_tier="medium",
        context_tokens=20000,   # 128K real; reducido porque genera <think> verbose
        backend="ollama",       # los tokens de razonamiento consumen contexto extra
    ),
    ModelProfile(
        name="llama3.2-vision:11b",
        prompt_tier="medium",
        context_tokens=16000,   # 128K real; en CPU limitado por pesos multimodales
        backend="ollama",
    ),
    ModelProfile(
        name="llama3.1:70b",
        prompt_tier="medium",   # instruction following casi como claude
        context_tokens=100000,  # 128K real; 100K conservador
        backend="ollama",
    ),
    ModelProfile(
        name="qwen2.5:14b",
        prompt_tier="medium",
        context_tokens=40000,   # 128K real; 40K conservador en CPU
        backend="ollama",
    ),
    ModelProfile(
        name="mistral:7b",
        prompt_tier="medium",
        context_tokens=28000,   # 32K real; 28K conservador
        backend="ollama",
    ),
    ModelProfile(
        name="mistral-nemo",
        prompt_tier="medium",
        context_tokens=80000,   # 128K real; modelo eficiente, 80K conservador
        backend="ollama",
    ),

    # ── CLAUDE / GPT API ───────────────────────────────────────────────────
    # Máxima calidad. Sin limitaciones prácticas de contexto para documentos
    # normales. Coste ~0.003€/pasaporte (Haiku) o ~0.02€/pasaporte (Sonnet).
    # Recomendado para documentos críticos o con muchas secciones técnicas.

    ModelProfile(
        name="claude-haiku",    # claude-haiku-3-5, claude-haiku-4, etc.
        prompt_tier="claude",
        context_tokens=180000,  # 200K real; 180K conservador
        backend="api",
    ),
    ModelProfile(
        name="claude-sonnet",   # claude-sonnet-4-5, claude-sonnet-4, etc.
        prompt_tier="claude",
        context_tokens=180000,
        backend="api",
    ),
    ModelProfile(
        name="claude-opus",
        prompt_tier="claude",
        context_tokens=180000,
        backend="api",
    ),
    ModelProfile(
        name="claude-3",        # catch-all para claude-3-* sin variante específica
        prompt_tier="claude",
        context_tokens=180000,
        backend="api",
    ),
    ModelProfile(
        name="gpt-4",           # GPT-4, GPT-4o, GPT-4-turbo
        prompt_tier="claude",
        context_tokens=100000,  # 128K real (gpt-4-turbo); 100K conservador
        backend="api",
    ),
    ModelProfile(
        name="gpt-3.5",
        prompt_tier="medium",
        context_tokens=14000,   # 16K real; 14K conservador
        backend="api",
    ),
]

# Perfil por defecto si el modelo no está en el registro.
# Usa valores conservadores para evitar OOM inesperados.
_DEFAULT_PROFILE = ModelProfile(
    name="default",
    prompt_tier="medium",
    context_tokens=7000,
    backend="ollama",
)


# ===========================================================================
# LOOKUP — get_profile(model_name) -> ModelProfile
# ===========================================================================

def get_profile(model_name: str) -> ModelProfile:
    """
    Retorna el ModelProfile para el modelo dado.

    Estrategia de matching (en orden de prioridad):
      1. Exacto (ignorando mayúsculas/minúsculas)
      2. El nombre del registro es substring del model_name dado
         → "qwen2.5-coder:7b" matchea "qwen2.5-coder:7b-instruct-q4_K_M"
      3. El model_name dado es substring del nombre en el registro
         → "qwen2.5" matchea "qwen2.5-coder:7b" (fallback amplio)
      4. _DEFAULT_PROFILE (7K tokens, medium, ollama)

    Args:
        model_name: nombre del modelo tal como viene de Ollama o la config
                    (ej. "qwen2.5-coder:7b", "claude-haiku-3-5-20251022")

    Returns:
        ModelProfile correspondiente, o _DEFAULT_PROFILE si no hay match.
    """
    if not model_name:
        return _DEFAULT_PROFILE

    mn = model_name.lower().strip()

    for p in _REGISTRY:
        if p.name.lower() == mn:
            return p

    for p in _REGISTRY:
        if p.name.lower() in mn:
            return p

    for p in _REGISTRY:
        if mn in p.name.lower():
            return p

    return _DEFAULT_PROFILE


def list_profiles() -> None:
    """Imprime todos los perfiles registrados. Útil para debug y calibración."""
    print(f"\n{'Modelo':<30} {'Tier':<8} {'Ctx':>6} {'Doc budget':>12} {'Chunked at':>12} {'Mejora'}")
    print("─" * 78)
    for p in _REGISTRY:
        print(
            f"{p.name:<30} {p.prompt_tier:<8} "
            f"{p.context_tokens // 1000:>4}K "
            f"{p.context_chars // 1000:>10}K chars "
            f"{p.chunk_trigger // 1000:>10}K chars "
            f"{'✓' if p.supports_improve_mode else '✗'}"
        )
    print()


if __name__ == "__main__":
    list_profiles()
    print("Ejemplos de get_profile():")
    for model in [
        "qwen2.5-coder:7b", "llama3.2:3b", "llama3.1:8b",
        "deepseek-r1:7b", "claude-haiku-3-5", "gpt-4o", "unknown-model",
    ]:
        p = get_profile(model)
        print(f"  {model:<35} → {p}")
