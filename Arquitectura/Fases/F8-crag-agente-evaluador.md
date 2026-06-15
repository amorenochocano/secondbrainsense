# F8 — CRAG: Agente Evaluador y Contingencia Web
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + IA Engineer (1) + Backend Junior (1)  
**Dependencias:** F4 completada (BrainRouter L1→L2→L0)  
**Entregable:** El router de F4 gana un agente evaluador explícito y un módulo de búsqueda web en tiempo real como contingencia inteligente

---

## Contexto y motivación

El router multinivel de F4 decide relevancia por score threshold (`>0.45`) y heurísticas de keywords. Funciona, pero tiene un problema fundamental: **no razona, mide**. Un chunk con score 0.44 se descarta aunque sea perfectamente relevante. Un chunk con score 0.8 pasa aunque responda una pregunta completamente diferente.

F8 añade un agente evaluador LLM entre la recuperación Qdrant y la decisión de routing. El agente justifica su decisión en texto auditado y la emite como JSON determinista. Cuando decide que Qdrant no tiene la respuesta, activa búsqueda web en tiempo real con query rewriting — no la indexación periódica de los conectores de SurfSense, sino búsqueda instantánea.

**Flujo F4 (antes):**
```
query → Qdrant → score > threshold? → SÍ: responder | NO: L0 LLM libre
```

**Flujo F8 (después):**
```
query → Qdrant → Agente Evaluador (LLM, temperature=0, JSON)
                      ├── es_relevante: true  → responder desde Qdrant
                      └── es_relevante: false → Query Rewriting
                                                    → DuckDuckGo/SearXNG
                                                    → responder con advertencia "fuente externa"
```

---

## F8.1 — agent.py: Agente Evaluador (Día 1-2)

**Fichero:** `surfsense_backend/app/brain/crag/agent.py` ← NUEVO

```python
# agent.py — Agente Evaluador CRAG
"""
Evalúa si un fragmento de Qdrant es suficientemente relevante
para responder la pregunta del usuario.

Contrato:
- temperature=0.0 → 100% determinista
- format="json"   → parseo garantizado
- XML wrapping    → protección contra prompt injection
- Fallback False  → si el LLM falla, ir a búsqueda web (safe default)
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.brain.llm_client import BrainLLMClient
from app.brain.crag.config import CRAGConfig

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    es_relevante: bool
    razonamiento: str
    source: str = ""   # slug del chunk evaluado


EVALUATOR_SYSTEM_PROMPT = """Eres un evaluador experto de relevancia documental.
Tu única función es determinar si el fragmento proporcionado contiene información
suficiente para responder la pregunta del usuario.

Responde ÚNICAMENTE con un objeto JSON con exactamente estas dos claves:
{
  "razonamiento": "string con tu análisis en 1-2 frases",
  "es_relevante": true|false
}

Criterios de evaluación:
- true: el fragmento contiene la respuesta directa o contexto necesario
- false: el fragmento es tangencial, genérico, o no responde la pregunta
- En caso de duda: false (es más seguro buscar en la web que dar información incorrecta)
"""

EVALUATOR_USER_TEMPLATE = """
<pregunta_usuario>
{pregunta}
</pregunta_usuario>

<documento_recuperado>
{fragmento}
</documento_recuperado>

Evalúa si el documento recuperado responde la pregunta del usuario.
"""


async def evaluar_relevancia(
    pregunta: str,
    fragmento: str,
    llm_client: BrainLLMClient,
    source: str = "",
) -> EvaluationResult:
    """
    Evalúa si un fragmento de Qdrant es relevante para la pregunta.

    Args:
        pregunta: Pregunta original del usuario
        fragmento: Texto del chunk recuperado de Qdrant
        llm_client: Cliente LLM compartido (usa modelo evaluador rápido)
        source: Slug del documento origen (para logging)

    Returns:
        EvaluationResult con es_relevante y razonamiento auditado
    """
    # Encapsular en XML para evitar prompt injection
    prompt = EVALUATOR_USER_TEMPLATE.format(
        pregunta=pregunta.strip(),
        fragmento=fragmento.strip()[:3000],  # limitar para no exceder context window
    )

    try:
        raw_response = await llm_client.generate(
            prompt=prompt,
            system=EVALUATOR_SYSTEM_PROMPT,
            model=CRAGConfig.EVALUATOR_MODEL,
            temperature=0.0,
            format="json",
            max_tokens=256,  # la respuesta es corta — un JSON pequeño
        )

        # Parseo estricto — solo json nativo, sin eval() ni ast
        parsed = json.loads(raw_response)

        # Validar que las claves requeridas existen
        if "es_relevante" not in parsed or "razonamiento" not in parsed:
            raise KeyError(f"JSON sin claves requeridas: {list(parsed.keys())}")

        result = EvaluationResult(
            es_relevante=bool(parsed["es_relevante"]),
            razonamiento=str(parsed["razonamiento"]),
            source=source,
        )

        # LOG AUDITADO — permite revisar en producción por qué el agente decidió
        logger.info(
            f"[CRAG Evaluador] source={source!r} | "
            f"es_relevante={result.es_relevante} | "
            f"razonamiento={result.razonamiento!r}"
        )

        return result

    except json.JSONDecodeError as e:
        # JSON corrupto → fallback seguro: ir a web
        logger.warning(
            f"[CRAG Evaluador] JSONDecodeError para source={source!r}: {e}. "
            f"Fallback: es_relevante=False"
        )
        return EvaluationResult(
            es_relevante=False,
            razonamiento=f"Fallback por JSON inválido: {str(e)}",
            source=source,
        )

    except KeyError as e:
        logger.warning(f"[CRAG Evaluador] Claves JSON incorrectas: {e}. Fallback: False")
        return EvaluationResult(
            es_relevante=False,
            razonamiento=f"Fallback por claves inválidas: {str(e)}",
            source=source,
        )

    except Exception as e:
        # Cualquier otro error (timeout Ollama, red...) → fallback seguro
        logger.error(f"[CRAG Evaluador] Error inesperado: {e}. Fallback: False")
        return EvaluationResult(
            es_relevante=False,
            razonamiento=f"Error inesperado: {type(e).__name__}",
            source=source,
        )


async def evaluar_mejores_chunks(
    pregunta: str,
    chunks: list[dict],
    llm_client: BrainLLMClient,
    max_evaluaciones: int = 3,
) -> tuple[bool, list[EvaluationResult]]:
    """
    Evalúa los top-N chunks y decide si alguno es suficientemente relevante.
    Evalúa en orden de score — si el primero es relevante, no evalúa el resto.

    Returns:
        (hay_relevante: bool, evaluaciones: list[EvaluationResult])
    """
    evaluaciones = []

    for chunk in chunks[:max_evaluaciones]:
        result = await evaluar_relevancia(
            pregunta=pregunta,
            fragmento=chunk.get("text", ""),
            llm_client=llm_client,
            source=chunk.get("source", ""),
        )
        evaluaciones.append(result)

        # Early exit: si encontramos un chunk relevante, no evaluamos el resto
        if result.es_relevante:
            return True, evaluaciones

    return False, evaluaciones
```

---

## F8.2 — config.py: configuración sin hardcoding (Día 1)

**Fichero:** `surfsense_backend/app/brain/crag/config.py` ← NUEVO

```python
# config.py — Configuración CRAG sin hardcoding
import os

class CRAGConfig:
    # Modelos — leídos desde .env, nunca hardcodeados
    EVALUATOR_MODEL: str = os.getenv(
        "CRAG_EVALUATOR_MODEL", "qwen2.5-coder:3b"  # modelo rápido para evaluación
    )
    QUERY_REWRITER_MODEL: str = os.getenv(
        "CRAG_REWRITER_MODEL", "llama3.2:latest"  # modelo ligero para rewriting
    )

    # Qdrant — sin hardcoding
    COLLECTION_BRAIN: str = os.getenv("QDRANT_COLLECTION_BRAIN", "brain")
    COLLECTION_KNOWLEDGE: str = os.getenv("QDRANT_COLLECTION_KNOWLEDGE", "knowledge")
    COLLECTION_CODE: str = os.getenv("QDRANT_COLLECTION_CODE", "code")

    # Búsqueda web
    WEB_SEARCH_PROVIDER: str = os.getenv("CRAG_WEB_PROVIDER", "searxng")  # searxng | ddgs
    WEB_SEARCH_MAX_RESULTS: int = int(os.getenv("CRAG_WEB_MAX_RESULTS", "3"))
    SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://searxng:8080")

    # Evaluador
    MAX_CHUNKS_TO_EVALUATE: int = int(os.getenv("CRAG_MAX_EVAL_CHUNKS", "3"))
    EVALUATOR_FALLBACK: bool = False  # si el LLM falla, asumir no relevante → ir a web

    # Timeouts (segundos)
    WEB_SEARCH_TIMEOUT: int = int(os.getenv("CRAG_WEB_TIMEOUT", "10"))
    EVALUATOR_TIMEOUT: int = int(os.getenv("CRAG_EVAL_TIMEOUT", "15"))
```

**Añadir al `.env`:**
```bash
# ── CRAG ──────────────────────────────────────────────
CRAG_EVALUATOR_MODEL=qwen2.5-coder:3b
CRAG_REWRITER_MODEL=llama3.2:latest
CRAG_WEB_PROVIDER=searxng      # searxng (privado) | ddgs (DuckDuckGo, sin API key)
CRAG_WEB_MAX_RESULTS=3
CRAG_MAX_EVAL_CHUNKS=3
CRAG_WEB_TIMEOUT=10
CRAG_EVAL_TIMEOUT=15
```

---

## F8.3 — web_search.py: contingencia web con query rewriting (Día 2-3)

**Fichero:** `surfsense_backend/app/brain/crag/web_search.py` ← NUEVO

```python
# web_search.py — Módulo de contingencia web
"""
Búsqueda web en tiempo real como fallback cuando Qdrant no tiene la respuesta.
Dos pasos:
1. Query rewriting: la pregunta del usuario → keywords optimizadas (via LLM rápido)
2. Búsqueda: keywords → resultados web limpios y estandarizados
"""

import re
import logging
from typing import Optional
from app.brain.llm_client import BrainLLMClient
from app.brain.crag.config import CRAGConfig

logger = logging.getLogger(__name__)


# ── Query Rewriting ────────────────────────────────────────────

REWRITER_PROMPT = """Convierte la siguiente pregunta de usuario en 3-4 palabras clave
optimizadas para buscar en Google. Devuelve SOLO las palabras clave, sin explicación,
sin puntuación, sin introducción.

Ejemplos:
- "¿Cómo soluciono el error 500 que me da Docker al levantar el backend?" → "Docker backend error 500 localhost"
- "Oye, ¿tienes idea de cómo conectar Qdrant con Python?" → "Qdrant Python client connection"
- "Me está fallando el login, creo que es el JWT" → "JWT authentication error Python"

Pregunta del usuario: {pregunta}
"""


async def optimizar_query(
    pregunta_original: str,
    llm_client: BrainLLMClient,
) -> str:
    """
    Convierte la pregunta del usuario en keywords optimizadas para búsqueda web.

    Args:
        pregunta_original: Pregunta en lenguaje natural del usuario
        llm_client: Cliente LLM (usa modelo rápido/ligero)

    Returns:
        String de keywords para pasar al motor de búsqueda
    """
    try:
        raw = await llm_client.generate(
            prompt=REWRITER_PROMPT.format(pregunta=pregunta_original.strip()),
            system="Eres un optimizador de queries de búsqueda. Responde solo con las palabras clave.",
            model=CRAGConfig.QUERY_REWRITER_MODEL,
            temperature=0.0,
            max_tokens=50,
        )
        # Limpiar respuesta del LLM
        query_optimizado = _limpiar_texto(raw.strip().replace("\n", " "))
        logger.info(f"[CRAG QueryRewriter] '{pregunta_original[:50]}...' → '{query_optimizado}'")
        return query_optimizado

    except Exception as e:
        # Fallback: usar los primeros 6 tokens de la pregunta
        logger.warning(f"[CRAG QueryRewriter] Error: {e}. Fallback a pregunta original truncada.")
        fallback = " ".join(pregunta_original.split()[:6])
        return fallback


# ── Búsqueda Web ───────────────────────────────────────────────

@dataclass
class WebResult:
    title: str
    body: str
    url: str


async def buscar_en_web(
    query_optimizado: str,
    max_results: int = None,
    provider: str = None,
) -> list[WebResult]:
    """
    Busca en la web usando el provider configurado.

    Args:
        query_optimizado: Keywords ya optimizadas por optimizar_query()
        max_results: Número máximo de resultados (default: CRAGConfig.WEB_SEARCH_MAX_RESULTS)
        provider: 'searxng' | 'ddgs' (default: CRAGConfig.WEB_SEARCH_PROVIDER)

    Returns:
        Lista de WebResult con title, body y url estandarizados
    """
    max_results = max_results or CRAGConfig.WEB_SEARCH_MAX_RESULTS
    provider = provider or CRAGConfig.WEB_SEARCH_PROVIDER

    try:
        if provider == "searxng":
            return await _buscar_searxng(query_optimizado, max_results)
        elif provider == "ddgs":
            return _buscar_ddgs(query_optimizado, max_results)
        else:
            raise ValueError(f"Provider desconocido: {provider}")

    except Exception as e:
        logger.error(f"[CRAG WebSearch] Error buscando '{query_optimizado}': {e}")
        return []  # lista vacía → el orquestador decidirá qué hacer


async def _buscar_searxng(query: str, max_results: int) -> list[WebResult]:
    """Búsqueda via SearXNG self-hosted (privado, sin API key)."""
    import httpx
    async with httpx.AsyncClient(timeout=CRAGConfig.WEB_SEARCH_TIMEOUT) as client:
        r = await client.get(
            f"{CRAGConfig.SEARXNG_URL}/search",
            params={"q": query, "format": "json", "categories": "general"}
        )
        r.raise_for_status()
        results = r.json().get("results", [])

    return [
        WebResult(
            title=_limpiar_texto(item.get("title", "")),
            body=_limpiar_texto(item.get("content", "")),
            url=item.get("url", ""),
        )
        for item in results[:max_results]
        if item.get("content")
    ]


def _buscar_ddgs(query: str, max_results: int) -> list[WebResult]:
    """Búsqueda via DuckDuckGo (sin API key, requiere duckduckgo-search)."""
    from duckduckgo_search import DDGS
    results = []
    # Uso de context manager para evitar fugas de memoria/sockets
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=max_results):
            results.append(WebResult(
                title=_limpiar_texto(item.get("title", "")),
                body=_limpiar_texto(item.get("body", "")),
                url=item.get("href", ""),
            ))
    return results


# ── Formateo estandarizado para el LLM ────────────────────────

def formatear_resultados_web(results: list[WebResult]) -> str:
    """
    Formatea los resultados web de manera idéntica y consistente
    para que el LLM final los asimile fácilmente.
    """
    if not results:
        return "[Sin resultados web disponibles]"

    blocks = []
    for i, r in enumerate(results, 1):
        block = f"[Fuente {i}]\nTítulo: {r.title}\nURL: {r.url}\nContenido: {r.body}"
        blocks.append(block)

    return "\n\n---\n\n".join(blocks)


# ── Limpieza de texto ──────────────────────────────────────────

def _limpiar_texto(texto: str) -> str:
    """
    Limpieza básica de texto de internet:
    - Elimina emojis y caracteres no ASCII extraños
    - Normaliza espacios en blanco dobles
    - Elimina saltos de línea basura
    """
    # Eliminar caracteres de control y no imprimibles (excepto \n\t)
    texto = re.sub(r'[^\x20-\x7E\n\tÁáÉéÍíÓóÚúÑñüÜ]', ' ', texto)
    # Normalizar espacios dobles
    texto = re.sub(r' {2,}', ' ', texto)
    # Normalizar múltiples saltos de línea
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    return texto.strip()
```

---

## F8.4 — Integrar CRAG en BrainRouter (Día 3-4)

Modificar `router.py` de F4 para añadir la capa de evaluación CRAG entre L2 y L0.

```python
# router.py — BrainRouter actualizado con CRAG

from app.brain.crag.agent import evaluar_mejores_chunks
from app.brain.crag.web_search import optimizar_query, buscar_en_web, formatear_resultados_web

# En BrainRouter.query() — sustituir la caída a L0:

async def query(self, question: str, chat_history: list[dict] = None) -> QueryResult:
    """Router multinivel con agente evaluador CRAG."""

    if self.force_l0:
        return await self._resolve_l0(question, chat_history)

    # ── L1: buscar en pasaportes ───────────────────────
    l1_results = await self._search_l1(question)
    l1_relevant = [r for r in l1_results if r["score"] >= self.l1_threshold]

    if not l1_relevant:
        # Sin candidatos en L1 → CRAG decide
        return await self._crag_fallback(question, chat_history)

    # ── L2: profundizar en chunks de detalle ──────────
    l1_sources = list(dict.fromkeys(r["source"] for r in l1_relevant))
    l2_results = await self._search_l2(question, filter_sources=l1_sources)

    if not l2_results:
        return await self._crag_fallback(question, chat_history)

    # ── CRAG: evaluar con agente LLM ──────────────────
    hay_relevante, evaluaciones = await evaluar_mejores_chunks(
        pregunta=question,
        chunks=l2_results,
        llm_client=self.llm,
        max_evaluaciones=CRAGConfig.MAX_CHUNKS_TO_EVALUATE,
    )

    if hay_relevante:
        # Los chunks pasan el filtro del agente evaluador
        chunks_relevantes = [
            l2_results[i] for i, ev in enumerate(evaluaciones) if ev.es_relevante
        ]
        return await self._resolve_l2(
            question, l1_relevant, chunks_relevantes, chat_history
        )
    else:
        # Agente dice: no es relevante → contingencia web
        logger.info(
            f"[CRAG] Agente decidió ir a web. "
            f"Razonamientos: {[ev.razonamiento for ev in evaluaciones]}"
        )
        return await self._crag_fallback(question, chat_history)


async def _crag_fallback(
    self, question: str, chat_history: list[dict]
) -> QueryResult:
    """
    Contingencia CRAG: query rewriting → búsqueda web → respuesta con advertencia.
    Resiliencia: si la web falla, cae a L0 LLM libre sin romperse.
    """
    try:
        # Query rewriting
        query_optimizado = await optimizar_query(question, self.llm)

        # Búsqueda web (con timeout propio)
        web_results = await asyncio.wait_for(
            buscar_en_web(query_optimizado),
            timeout=CRAGConfig.WEB_SEARCH_TIMEOUT,
        )

        if not web_results:
            logger.warning("[CRAG] Sin resultados web → L0 LLM libre")
            return await self._resolve_l0(question, chat_history)

        # Formatear y responder con advertencia de fuente externa
        contexto_web = formatear_resultados_web(web_results)
        answer = await self.llm.generate(
            prompt=WEB_SYNTHESIS_PROMPT.format(
                question=question,
                context=contexto_web,
            ),
            system=WEB_SYNTHESIS_SYSTEM,
        )

        return QueryResult(
            answer=answer,
            level=3,  # nuevo nivel para CRAG web
            level_label="🌐 Web",
            sources=[r.url for r in web_results],
            chunks_used=[{"text": r.body, "source": r.url} for r in web_results],
        )

    except asyncio.TimeoutError:
        logger.error("[CRAG] Timeout en búsqueda web → L0 LLM libre")
        return await self._resolve_l0(question, chat_history)

    except Exception as e:
        # RESILIENCIA: nunca romper la ejecución
        logger.error(f"[CRAG] Error en contingencia web: {e} → L0 LLM libre")
        return await self._resolve_l0(question, chat_history)


# ── Prompts de síntesis diferenciados ─────────────────────────

WEB_SYNTHESIS_SYSTEM = """Eres un asistente técnico. La información que tienes proviene
de fuentes públicas de internet, NO de la documentación interna del equipo.
Debes advertirlo claramente al inicio de tu respuesta."""

WEB_SYNTHESIS_PROMPT = """
Pregunta del usuario: {question}

Información recuperada de fuentes externas:
{context}

Responde advirtiendo que la información proviene de internet y puede no reflejar
la configuración específica del proyecto.
"""
```

---

## F8.5 — Actualizar el endpoint y la UI (Día 4)

```python
# brain_routes.py — añadir nivel 3 a QueryResponse

class QueryResponse(BaseModel):
    answer: str
    level: int              # 0=LLM, 1=Brain, 2=Knowledge, 3=Web (NUEVO)
    level_label: str        # "🌐 Web"
    sources: list[str]
    chunks_used: list[dict]
    crag_evaluations: list[dict] = []  # razonamientos del agente (para debug)
```

```tsx
// En Brain Chat (F6) — añadir badge Web y tooltip de razonamiento CRAG

const LEVEL_COLORS = {
  "🧠 Brain":      "bg-purple-100 text-purple-800",
  "📚 Knowledge":  "bg-blue-100 text-blue-800",
  "🤖 LLM":       "bg-gray-100 text-gray-700",
  "🌐 Web":       "bg-green-100 text-green-800",  // NUEVO
};

// Si level === 3, mostrar tooltip con "Qdrant no tenía información suficiente.
// Se buscó en fuentes externas."
```

---

## F8.6 — Tests CRAG (Día 5)

```python
# tests/brain/test_crag_f8.py

import pytest
from unittest.mock import AsyncMock
from app.brain.crag.agent import evaluar_relevancia, EvaluationResult
from app.brain.crag.web_search import _limpiar_texto, optimizar_query

class TestAgenteCRAG:

    @pytest.mark.asyncio
    async def test_json_valido_relevante(self):
        """Cuando el LLM devuelve JSON válido con es_relevante=true."""
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value='{"razonamiento": "El fragmento explica JWT", "es_relevante": true}')
        result = await evaluar_relevancia("¿Qué es JWT?", "JWT es un token...", llm)
        assert result.es_relevante is True
        assert "JWT" in result.razonamiento

    @pytest.mark.asyncio
    async def test_json_corrupto_fallback_false(self):
        """JSON corrupto → fallback False (nunca lanzar excepción)."""
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="esto no es json {{{")
        result = await evaluar_relevancia("pregunta", "fragmento", llm)
        assert result.es_relevante is False
        assert "Fallback" in result.razonamiento

    @pytest.mark.asyncio
    async def test_llm_timeout_fallback_false(self):
        """Si Ollama no responde → False, no se cuelga."""
        import asyncio
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=asyncio.TimeoutError())
        result = await evaluar_relevancia("pregunta", "fragmento", llm)
        assert result.es_relevante is False

    @pytest.mark.asyncio
    async def test_json_sin_claves_requeridas(self):
        """JSON válido pero con claves incorrectas → fallback False."""
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value='{"decision": true, "reason": "ok"}')
        result = await evaluar_relevancia("pregunta", "fragmento", llm)
        assert result.es_relevante is False

class TestQueryRewriting:

    @pytest.mark.asyncio
    async def test_rewriter_devuelve_keywords(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="Docker backend error 500 localhost")
        query = await optimizar_query(
            "¿Cómo soluciono el error 500 de Docker al levantar el backend?", llm
        )
        assert "Docker" in query
        assert len(query.split()) <= 8  # keywords cortas

    @pytest.mark.asyncio
    async def test_rewriter_fallback_si_llm_falla(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=Exception("Ollama caído"))
        query = await optimizar_query("esta es mi pregunta de prueba aquí", llm)
        # Fallback: primeros 6 tokens de la pregunta original
        assert len(query) > 0
        assert "pregunta" in query or "esta" in query

class TestLimpiezaTexto:

    def test_elimina_espacios_dobles(self):
        assert "hola mundo" in _limpiar_texto("hola  mundo")

    def test_elimina_caracteres_extranios(self):
        texto = "resultado 🚀 con emojis \x00 y nulos"
        limpio = _limpiar_texto(texto)
        assert "🚀" not in limpio
        assert "\x00" not in limpio

    def test_normaliza_saltos_linea(self):
        texto = "línea1\n\n\n\n\nlínea2"
        limpio = _limpiar_texto(texto)
        assert "\n\n\n" not in limpio
```

---

## Dependencias adicionales

Añadir al `requirements.txt`:
```txt
duckduckgo-search>=6.2.0    # solo si CRAG_WEB_PROVIDER=ddgs
```

> SearXNG ya está desplegado desde F0 — es el provider recomendado para privacidad.

---

## Checklist F8

- [ ] `crag/config.py` con todas las variables desde `.env`, sin hardcoding
- [ ] `crag/agent.py`: temperatura 0.0, format JSON, XML wrapping, try/except JSONDecodeError
- [ ] `crag/agent.py`: log auditado del campo `razonamiento` en cada decisión
- [ ] `crag/web_search.py`: `optimizar_query()` con fallback si LLM falla
- [ ] `crag/web_search.py`: context manager `with DDGS()` o `async httpx` para SearXNG
- [ ] `crag/web_search.py`: `_limpiar_texto()` elimina emojis, espacios dobles, saltos basura
- [ ] `router.py`: CRAG entre L2 y L0, con `asyncio.wait_for` y timeout propio
- [ ] Si web falla → L0 LLM libre (nunca romper ejecución)
- [ ] Badge `🌐 Web` en UI con advertencia de fuente externa
- [ ] Tests: JSON corrupto → False, LLM timeout → False, rewriter fallback
- [ ] Tipado completo: todas las funciones con type hints y return types
- [ ] Sin hardcoding: modelos, colecciones y límites desde `CRAGConfig`

---

**Flujo completo BrainSense tras F8:**
```
Query
  ↓
L1: brain collection (pasaportes)
  ↓ score < threshold
L2: knowledge + code (chunks detalle, filtrados por L1)
  ↓
CRAG Agente Evaluador (temperature=0, JSON, XML wrapping)
  ├── es_relevante: true  → responder desde L2 (📚 Knowledge)
  └── es_relevante: false
        ↓
        Query Rewriting (LLM rápido)
        ↓
        SearXNG / DuckDuckGo (3 resultados, limpios)
        ├── resultados OK → responder con advertencia (🌐 Web)
        └── sin resultados / timeout → L0 LLM libre (🤖 LLM)
```

---

**Anterior:** [F7 — Hardening y Producción](./F7-hardening.md)  
**Volver al plan maestro:** [00 — Plan Maestro](./00-plan-maestro.md)
