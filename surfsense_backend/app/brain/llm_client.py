"""
llm_client.py  (v3.0 — Model-Profile Aware)
--------------------------------------------
Cliente LLM unificado que abstrae Ollama y Claude API (Anthropic directo o Azure AI Foundry).

CAMBIOS v3.0:
  - SYNTHESIS_MODEL ahora se lee también como nombre canónico para get_profile()
  - active_synthesis_profile property: expone el ModelProfile activo
  - Sin cambios en la interfaz pública (generate/chat/providers)

El provider se elige por llamada (no por configuración global), lo que permite
que distintos endpoints usen distintos modelos en el mismo arranque.

Providers disponibles:
  - "ollama"  : modelo local vía Ollama API
  - "claude"  : Claude vía Anthropic directo O Azure AI Foundry (según CLAUDE_BACKEND)
  - "hybrid"  : síntesis/prompts largos (> HYBRID_THRESHOLD tokens) → Claude;
                queries de chat cortas → Ollama

La KEY nunca se gestiona desde la UI: sólo desde el .env.
Si no hay credenciales de Claude configuradas y se solicita Claude o Hybrid,
se hace fallback automático a Ollama con un log de aviso.

Backends soportados para Claude (variable CLAUDE_BACKEND):
  - "anthropic" (defecto): api.anthropic.com + ANTHROPIC_API_KEY
  - "azure"               : Azure AI Foundry MaaS + AZURE_CLAUDE_ENDPOINT + AZURE_CLAUDE_API_KEY

Embeddings (nomic-embed-text, nomic-embed-code) NO pasan por aquí.
"""
import os
import re
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Configuración Ollama ──────────────────────────────────────────────────────
OLLAMA_HOST        = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL", "deepseek-r1:14b")
OLLAMA_CHAT_MODEL  = os.getenv("OLLAMA_CHAT_MODEL", OLLAMA_MODEL)
SYNTHESIS_MODEL    = os.getenv("SYNTHESIS_MODEL", OLLAMA_MODEL)
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))

# ── Configuración Claude ──────────────────────────────────────────────────────
CLAUDE_BACKEND    = os.getenv("CLAUDE_BACKEND", "anthropic")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
AZURE_CLAUDE_ENDPOINT  = os.getenv("AZURE_CLAUDE_ENDPOINT", "")
AZURE_CLAUDE_API_KEY   = os.getenv("AZURE_CLAUDE_API_KEY", "")
AZURE_CLAUDE_MODEL     = os.getenv("AZURE_CLAUDE_MODEL", CLAUDE_MODEL)

# ── Modo híbrido ──────────────────────────────────────────────────────────────
HYBRID_THRESHOLD = int(os.getenv("HYBRID_THRESHOLD", "8000"))

# Provider por defecto al arrancar (sobreescribible por request desde la UI)
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# Pattern para limpiar bloque <think> de deepseek-r1
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _claude_available() -> bool:
    if CLAUDE_BACKEND == "azure":
        return bool(AZURE_CLAUDE_ENDPOINT and AZURE_CLAUDE_API_KEY)
    return bool(ANTHROPIC_API_KEY)


def _claude_backend_label() -> str:
    if CLAUDE_BACKEND == "azure":
        return f"Azure AI Foundry ({AZURE_CLAUDE_MODEL})"
    return f"Anthropic API ({CLAUDE_MODEL})"


# ── Model profile para síntesis ───────────────────────────────────────────────

def _get_synthesis_profile(model_name: str, provider: str):
    """
    Retorna el ModelProfile para el modelo de síntesis activo.
    Para provider=claude, siempre retorna el perfil "claude".
    Para provider=ollama, hace lookup por nombre de modelo.
    """
    try:
        from brain.model_profiles import get_profile, ModelProfile
        if provider in ("claude", "azure"):
            # Para Claude siempre usamos el perfil claude independiente del modelo
            return get_profile("claude-sonnet")
        return get_profile(model_name)
    except ImportError:
        # model_profiles.py no instalado aún — fallback seguro a comportamiento v2.5
        log.debug("model_profiles no disponible; usando perfil 'large' por defecto.")
        return None


# ── Cliente unificado ─────────────────────────────────────────────────────────

class LLMClient:
    """
    Interfaz única para generación de texto.

    Uso:
        client = LLMClient()
        answer = client.generate(prompt, provider="claude", system="...")
        answer = client.chat(messages, provider="hybrid")
    """

    # ── generate ─────────────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        provider: str = DEFAULT_PROVIDER,
        system: Optional[str] = None,
        temperature: float = OLLAMA_TEMPERATURE,
        max_tokens: int = CLAUDE_MAX_TOKENS,
        model: Optional[str] = None,
        num_ctx: Optional[int] = None,
    ) -> str:
        resolved = self._resolve_provider(provider, prompt)
        if resolved == "ollama":
            effective_model = model or OLLAMA_MODEL
        else:
            effective_model = AZURE_CLAUDE_MODEL if CLAUDE_BACKEND == "azure" else CLAUDE_MODEL

        log.info(
            "generate() → provider=%s (solicitado=%s, model=%s)",
            resolved, provider, effective_model,
        )

        if resolved == "claude":
            return self._claude_generate(prompt, system, max_tokens)
        return self._ollama_generate(
            prompt, system, temperature,
            model=model, max_tokens=max_tokens, num_ctx=num_ctx,
        )

    # ── chat ─────────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        provider: str = DEFAULT_PROVIDER,
        temperature: float = OLLAMA_TEMPERATURE,
        max_tokens: int = CLAUDE_MAX_TOKENS,
        model: Optional[str] = None,
    ) -> str:
        total_chars = sum(len(m.get("content", "")) for m in messages)
        resolved = self._resolve_provider(provider, " " * total_chars)
        log.info(
            "chat() → provider=%s (solicitado=%s, chars=%d, model_override=%s)",
            resolved, provider, total_chars, model,
        )
        if resolved == "claude":
            return self._claude_chat(messages, max_tokens)
        return self._ollama_chat(messages, temperature, model=model)

    # ── provider resolution ───────────────────────────────────────────────────

    def _resolve_provider(self, provider: str, prompt_content: str) -> str:
        if provider not in ("ollama", "claude", "hybrid"):
            log.warning("Provider desconocido '%s'; usando ollama como fallback.", provider)
            return "ollama"
        if provider == "ollama":
            return "ollama"
        if not _claude_available():
            log.warning(
                "Credenciales Claude no configuradas (backend=%s). "
                "Provider '%s' → fallback a ollama.",
                CLAUDE_BACKEND, provider,
            )
            return "ollama"
        if provider == "claude":
            return "claude"
        # hybrid
        estimated_tokens = len(prompt_content) // 4
        if estimated_tokens >= HYBRID_THRESHOLD:
            log.info("Hybrid: prompt largo (%d tokens est.) → claude.", estimated_tokens)
            return "claude"
        log.info("Hybrid: prompt corto (%d tokens est.) → ollama.", estimated_tokens)
        return "ollama"

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _ollama_generate(
        self, prompt: str, system: Optional[str],
        temperature: float, model: Optional[str] = None,
        max_tokens: int = 4096, num_ctx: Optional[int] = None,
    ) -> str:
        import ollama as _ollama
        client = _ollama.Client(host=OLLAMA_HOST, timeout=1200)
        effective_model = model or OLLAMA_MODEL

        # num_ctx: ventana de contexto de Ollama (input + output).
        # CRÍTICO: si no se especifica, Ollama usa 2048 por defecto
        # y trunca silenciosamente prompts largos → el modelo ve solo
        # el principio y responde con "Lo siento, no puedo generar...".
        # Usamos el context_tokens del ModelProfile si está disponible,
        # con un mínimo de 4096 para evitar truncados en prompts normales.
        if num_ctx is None:
            try:
                from brain.model_profiles import get_profile
                p = get_profile(effective_model)
                num_ctx = p.context_tokens
            except Exception:
                num_ctx = 8192  # fallback seguro

        options = {
            "temperature": temperature,
            "num_predict": max_tokens,   # tokens máximos de salida
            "num_ctx":     num_ctx,       # ventana de contexto total
        }

        log.info(
            "Ollama generate() → model=%s host=%s temp=%.2f num_ctx=%d num_predict=%d",
            effective_model, OLLAMA_HOST, temperature, num_ctx, max_tokens,
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = client.chat(
                model=effective_model,
                messages=messages,
                options=options,
            )
            return _strip_think(resp["message"]["content"])
        except Exception as exc:
            log.error("Error en Ollama generate: %s", exc, exc_info=True)
            raise

    def _ollama_chat(
        self, messages: list[dict], temperature: float, model: Optional[str] = None,
    ) -> str:
        import ollama as _ollama
        client = _ollama.Client(host=OLLAMA_HOST, timeout=1200)
        effective_model = model or OLLAMA_CHAT_MODEL
        log.info(
            "Ollama chat() → model=%s host=%s temp=%.2f messages=%d",
            effective_model, OLLAMA_HOST, temperature, len(messages),
        )
        try:
            resp = client.chat(
                model=effective_model,
                messages=messages,
                options={"temperature": temperature},
            )
            return _strip_think(resp["message"]["content"])
        except Exception as exc:
            log.error("Error en Ollama chat (model=%s): %s", effective_model, exc, exc_info=True)
            raise

    # ── Claude ────────────────────────────────────────────────────────────────

    def _build_anthropic_client(self):
        import anthropic
        if CLAUDE_BACKEND == "azure":
            if not AZURE_CLAUDE_ENDPOINT or not AZURE_CLAUDE_API_KEY:
                raise EnvironmentError(
                    "CLAUDE_BACKEND=azure pero AZURE_CLAUDE_ENDPOINT o "
                    "AZURE_CLAUDE_API_KEY no están configuradas en el .env"
                )
            log.debug("Claude vía Azure AI Foundry: %s", AZURE_CLAUDE_ENDPOINT)
            return anthropic.Anthropic(
                base_url=AZURE_CLAUDE_ENDPOINT,
                api_key=AZURE_CLAUDE_API_KEY,
                default_headers={"api-key": AZURE_CLAUDE_API_KEY},
            )
        if not ANTHROPIC_API_KEY:
            raise EnvironmentError(
                "CLAUDE_BACKEND=anthropic pero ANTHROPIC_API_KEY no está configurada en el .env"
            )
        log.debug("Claude vía Anthropic API directa")
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def _effective_claude_model(self) -> str:
        return AZURE_CLAUDE_MODEL if CLAUDE_BACKEND == "azure" else CLAUDE_MODEL

    def _claude_generate(self, prompt: str, system: Optional[str], max_tokens: int) -> str:
        client = self._build_anthropic_client()
        model  = self._effective_claude_model()
        kwargs = {
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        try:
            resp = client.messages.create(**kwargs)
            return resp.content[0].text
        except Exception as exc:
            log.error("Error en Claude generate (backend=%s): %s", CLAUDE_BACKEND, exc, exc_info=True)
            raise

    def _claude_chat(self, messages: list[dict], max_tokens: int) -> str:
        client = self._build_anthropic_client()
        model  = self._effective_claude_model()
        system_content = None
        user_messages  = []
        for m in messages:
            role    = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_content = (system_content + "\n\n" + content) if system_content else content
            elif role in ("user", "assistant"):
                user_messages.append({"role": role, "content": content})
        if not user_messages:
            log.warning("Claude chat sin mensajes de usuario; retornando vacío.")
            return ""
        kwargs = {
            "model": model, "max_tokens": max_tokens,
            "messages": user_messages,
        }
        if system_content:
            kwargs["system"] = system_content
        try:
            resp = client.messages.create(**kwargs)
            return resp.content[0].text
        except Exception as exc:
            log.error("Error en Claude chat (backend=%s): %s", CLAUDE_BACKEND, exc, exc_info=True)
            raise

    # ── Utilidades públicas ───────────────────────────────────────────────────

    @property
    def active_model_label(self) -> str:
        if DEFAULT_PROVIDER == "claude" and _claude_available():
            return _claude_backend_label()
        if DEFAULT_PROVIDER == "hybrid":
            return f"Hybrid ({'Claude+Ollama' if _claude_available() else 'Ollama fallback'})"
        return f"Ollama ({OLLAMA_MODEL})"

    @property
    def active_synthesis_profile(self):
        """
        ModelProfile activo para síntesis de pasaportes.
        Útil para la UI: muestra tier, context_chars, chunk_trigger.
        Retorna None si model_profiles.py no está instalado.
        """
        return _get_synthesis_profile(SYNTHESIS_MODEL, DEFAULT_PROVIDER)

    @staticmethod
    def claude_available() -> bool:
        return _claude_available()

    @staticmethod
    def claude_backend() -> str:
        return CLAUDE_BACKEND

    @staticmethod
    def claude_backend_label() -> str:
        return _claude_backend_label()


# Singleton global
llm_client = LLMClient()
