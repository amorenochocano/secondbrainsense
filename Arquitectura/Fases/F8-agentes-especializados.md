# F8 — Agentes Especializados: Synthesizer, Project Intelligence, Meeting Prep, Code Explainer
**Duración:** 3 semanas  
**Equipo:** Backend Senior (1) + IA Engineer (1)  
**Dependencias:** F3 completada (pasaportes), F4 completada (router multinivel), F5 completada (pipeline unificado)  
**Entregable:** 4 subagentes builtin operativos, integrados en el main_agent, invocables vía chat, optimizados para modelos Ollama (7B–14B)

---

## Objetivo

Implementar 4 agentes especializados que extienden SecondBrainSense con capacidades de alto nivel. Cada agente sigue la definición: **LLM + herramientas + capacidad de decisión + ciclo acción/observación**.

Los agentes reutilizan toda la infraestructura existente:
- **Subagent framework** — `SurfSenseSubagentSpec` + `pack_subagent()` + `registry.py`
- **Router multinivel F4** — BrainRouter L1→L2→BM25→Web
- **Pasaportes semánticos F3** — documentos enriquecidos por `DocumentSynthesizer`
- **Pipeline unificado F5** — chunks de alta calidad (UniversalCleaner + nomic-embed-text 768d)
- **Conectores MCP** — Jira, Slack, Gmail, Calendar, GitHub, Confluence, Linear
- **BM25** — PostgreSQL `tsvector` via `ChucksHybridSearchRetriever.full_text_search()`

**Árbol de agentes tras F8:**

```
Usuario → Chat → main_agent router
                    ├── research (web_search + scrape)          [existente]
                    ├── knowledge_base (filesystem)              [existente]
                    ├── memory (personal notes)                  [existente]
                    ├── deliverables (reports, podcasts)         [existente]
                    ├── connectors (jira, slack, gmail...)       [existente]
                    ├── [NEW] knowledge_synthesizer              [F8.2]
                    ├── [NEW] project_intelligence               [F8.3]
                    ├── [NEW] meeting_prep                       [F8.4]
                    └── [NEW] code_explainer                     [F8.5]
```

---

## Impacto de F5 en F8

Tras F5 (pipeline unificado), los agentes de F8 se benefician automáticamente sin cambios de código:

- **Mejor calidad de chunks** — UniversalCleaner filtra ruido, `_filter_low_quality()` descarta bloques < 0.30
- **Mismo modelo de embedding en ingesta y búsqueda** — nomic-embed-text 768d
- **`search_knowledge_base()` ya usa Qdrant** — adaptado en F5.5 con `unified_embedder`
- **Metadata enriquecida** — cada chunk tiene `category` (Extracción especializada/Conector inteligente/Procesamiento estándar), `quality_score`, `language`

Los agentes F8.3 (Project Intelligence) y F8.4 (Meeting Prep) usan conectores MCP que ya están en SurfSense — no dependen del pipeline de ingesta.

---

## Restricciones Ollama — Impacto directo en el diseño

| Modelo | Context window | Calidad JSON | Uso en F8 |
|--------|---------------|--------------|-----------|
| qwen2.5-coder:3b | 6K tokens | Baja | NO usar — insuficiente para agentes |
| qwen2.5-coder:7b | 28K tokens | Media | Fallback mínimo, prompts cortos |
| llama3.1:8b | 30K tokens | Media | Project Intelligence, Meeting Prep |
| deepseek-r1:14b | 64K tokens | Alta | Knowledge Synthesizer, Code Explainer |

**Reglas de diseño:**
1. Prompts directivos y cortos — máximo 400 tokens de system_prompt
2. Templates fijos para outputs — el LLM rellena secciones
3. Multi-call > single-call — varias llamadas cortas mejor que una larga
4. Pasaportes > chunks raw — un pasaporte de 500 tokens contiene más info que 5 chunks
5. Máximo 3 rondas de búsqueda por conversación
6. Fallback explícito si modelo no disponible → degradar a qwen2.5-coder:7b

---

## F8.0 — Estado del codebase (Día 0)

### Lo que ya existe y se REUTILIZA

| Componente | Fichero | Rol en F8 |
|-----------|---------|-----------|
| `SurfSenseSubagentSpec` | `subagents/shared/spec.py` | Contrato de retorno |
| `pack_subagent()` | `subagents/shared/subagent_builder.py` | Builder de agentes |
| `read_md_file()` | `subagents/shared/md_file_reader.py` | Lee description/system_prompt |
| `registry.py` | `subagents/registry.py` | Registro central |
| `create_web_search_tool` | `subagents/builtins/research/tools/web_search.py` | Reutilizar en KS |
| `ChucksHybridSearchRetriever.full_text_search()` | `app/retriever/chunks_hybrid_search.py` | BM25 — sin cambios |
| `BrainRouter.route()` | `app/brain/router.py` (F4) | Router L1→L2 Qdrant |
| MCP tools | `subagents/connectors/*/tools/` | Jira, Slack, Gmail, Calendar |

### Lo que F8 debe CREAR

| Componente | Ruta |
|-----------|------|
| Shared brain tools | `subagents/builtins/_shared_brain_tools/` |
| Knowledge Synthesizer | `subagents/builtins/knowledge_synthesizer/` |
| Project Intelligence | `subagents/builtins/project_intelligence/` |
| Meeting Prep | `subagents/builtins/meeting_prep/` |
| Code Explainer | `subagents/builtins/code_explainer/` |

### Lo que F8 debe MODIFICAR

| Fichero | Cambio |
|---------|--------|
| `subagents/registry.py` | +4 imports + 4 entries en `_BUILTIN_BUILDERS` |

---

## F8.1 — Shared brain tools (Día 1)

**Ruta:** `subagents/builtins/_shared_brain_tools/`

### `brain_router_tool.py` — Wrapper del BrainRouter F4

```python
"""
brain_router_tool.py
--------------------
Wrapper del BrainRouter (F4) para uso en agentes F8.

Expone la cascada L1→L2→BM25→Web→L0 como una tool LangChain invocable.
Los agentes la usan igual que web_search — no conocen los detalles del router.

F5: los vectores consultados son de Qdrant (nomic-embed-text 768d) con
chunks de alta calidad (UniversalCleaner + quality_score filtrado).
"""
from langchain_core.tools import tool
from app.brain.router import BrainRouter

@tool
async def search_brain(
    question: str,
    search_space_id: int,
    force_level: int | None = None,
) -> dict:
    """
    Consulta el Brain mediante el router multinivel (F4).
    Busca primero en pasaportes (L1), luego en knowledge (L2),
    luego BM25, luego web, y como último recurso LLM libre (L0).

    Args:
        question: Pregunta en lenguaje natural.
        search_space_id: ID del espacio de búsqueda.
        force_level: Forzar nivel específico (1=brain, 2=knowledge, 3=bm25, 4=web, 0=llm).

    Returns:
        dict con answer, sources, level_used, level_label.
    """
    router = BrainRouter()
    return await router.route(
        question=question,
        search_space_id=str(search_space_id),
        force_level=force_level,
    )
```

### `bm25_search_tool.py` — BM25 PostgreSQL

```python
"""
bm25_search_tool.py
-------------------
Wrapper de búsqueda keyword BM25 en PostgreSQL.

Usa ChucksHybridSearchRetriever.full_text_search() — intacto tras F5.
Se mantiene separado de brain_router_tool para que los agentes puedan
usar BM25 independientemente de la cascada completa.
"""
from langchain_core.tools import tool
from app.retriever.chunks_hybrid_search import ChucksHybridSearchRetriever

@tool
async def search_bm25(
    query: str,
    search_space_id: int,
    top_k: int = 5,
) -> list[dict]:
    """
    Búsqueda keyword BM25 en PostgreSQL (to_tsvector).
    Complementa la búsqueda semántica de search_brain.

    Args:
        query: Términos de búsqueda keyword.
        search_space_id: ID del espacio de búsqueda.
        top_k: Número de resultados.

    Returns:
        Lista de chunks con content, document title, score BM25.
    """
    # BM25 sigue en PostgreSQL — no afectado por F5
    retriever = ChucksHybridSearchRetriever(db_session=None)  # sesión por contexto
    return await retriever.full_text_search(query, top_k, search_space_id)
```

---

## F8.2 — Agente 1: Knowledge Synthesizer (Días 2-5)

### Propósito

Responde preguntas complejas que requieren sintetizar información de múltiples fuentes del Brain. A diferencia del chat normal (una respuesta directa), este agente produce síntesis estructuradas con citaciones verificables.

**Cuándo se activa:** "Explícame todo lo que sabe el Brain sobre X", "Sintetiza las decisiones de arquitectura sobre Y", "¿Qué dicen los documentos sobre la estrategia de Z?"

### Herramientas

1. `search_brain` — consulta el router L1→L2→BM25→Web
2. `search_bm25` — búsqueda keyword complementaria
3. `web_search` — si el Brain no tiene suficiente información

### Estructura de ficheros

```
subagents/builtins/knowledge_synthesizer/
├── __init__.py
├── agent.py                    ← Builder: pack_subagent() con las tools
├── description.md              ← Texto que usa el router para elegir este agente
├── system_prompt.md            ← Instrucciones del agente
└── tools/
    ├── __init__.py
    ├── index.py                ← NAME + RULESET + load_tools()
    ├── evaluate_coverage.py    ← ¿Tengo suficiente información para sintetizar?
    └── synthesize_with_citations.py  ← Genera la síntesis final con [source:X]
```

### `description.md`

```markdown
Use this agent when the user needs to synthesize knowledge from the Brain about a topic.
Triggers: "explain everything about", "synthesize", "what do the documents say about",
"summarize the Brain's knowledge on", "create a knowledge summary".
Does NOT trigger for: simple factual questions, code tasks, meeting prep, project status.
```

### `system_prompt.md`

```markdown
You are a knowledge synthesizer. Your task is to produce structured, cited answers
using information from the Brain (internal knowledge base).

PROCESS:
1. Search the Brain with search_brain (up to 3 searches with different angles)
2. Use search_bm25 for specific terms not found semantically
3. Use evaluate_coverage to decide if you have enough information
4. Use synthesize_with_citations to produce the final answer

OUTPUT FORMAT (always use this template):
## {Topic}
### {Sub-question 1}
{answer with [source:slug] citations}
### {Sub-question 2}
{answer with citations}
### Sources consulted
- [{title}]({source}): {one-line summary}

RULES:
- Maximum 3 search rounds — do not loop
- If Brain has no information, say so explicitly
- Always cite sources — never synthesize without evidence
- Keep each section under 200 words for Ollama context limits
```

### `agent.py`

```python
"""
knowledge_synthesizer/agent.py
-------------------------------
Builder del agente Knowledge Synthesizer.

Reutiliza pack_subagent() del framework SurfSense.
Las tools brain_router_tool y bm25_search_tool son compartidas con F8.5 (Code Explainer).
"""
from app.agents.chat.multi_agent_chat.subagents.shared.subagent_builder import pack_subagent
from app.agents.chat.multi_agent_chat.subagents.shared.md_file_reader import read_md_file
from app.agents.chat.multi_agent_chat.subagents.builtins._shared_brain_tools import (
    search_brain, search_bm25,
)
from .tools.index import load_tools

def build_knowledge_synthesizer(context) -> object:
    """
    Ensambla el Knowledge Synthesizer.
    Requiere F3 (pasaportes) y F4 (router) operativos.
    """
    return pack_subagent(
        spec_name="knowledge_synthesizer",
        description=read_md_file(__file__, "description.md"),
        system_prompt=read_md_file(__file__, "system_prompt.md"),
        tools=[search_brain, search_bm25, *load_tools(context)],
        context=context,
    )
```

---

## F8.3 — Agente 2: Project Intelligence (Días 5-8)

### Propósito

Genera informes de estado de proyectos cruzando múltiples conectores: Jira (tareas), GitHub (PRs), Slack (conversaciones), Confluence (documentación). No requiere F3/F4 — trabaja directamente con conectores MCP.

**Cuándo se activa:** "Estado del proyecto X", "¿Qué se ha hecho esta semana en Y?", "Informe del sprint", "¿Qué bloqueantes hay en Z?"

### Herramientas

1. MCP Jira — tareas abiertas, sprint activo, bloqueantes
2. MCP GitHub — PRs pendientes, commits recientes
3. MCP Slack — mensajes del canal del proyecto
4. `search_brain` — contexto arquitectónico del pasaporte del proyecto
5. `search_bm25` — búsqueda de documentos relacionados

### Estructura de ficheros

```
subagents/builtins/project_intelligence/
├── __init__.py
├── agent.py
├── description.md
├── system_prompt.md
└── tools/
    ├── __init__.py
    ├── index.py
    └── generate_project_report.py   ← Template del informe
```

### `description.md`

```markdown
Use this agent for project status reports, sprint summaries, and cross-connector
project intelligence. Triggers: "project status", "sprint report", "what happened
with project X", "weekly summary", "blockers in Y".
```

### `system_prompt.md`

```markdown
You are a project intelligence agent. You produce structured project status reports
by querying available connectors (Jira, GitHub, Slack) and the Brain.

PROCESS:
1. Query Jira for open tasks and sprint status (if available)
2. Query GitHub for recent PRs and commits (if available)
3. Query Slack for relevant discussions (if available)
4. Search Brain for architectural context (optional)
5. Use generate_project_report to produce the final report

DEGRADATION: If a connector is not available, skip it and note it in the report.
Do not fail — always produce a partial report with what is available.

OUTPUT: Use generate_project_report template — never free-form output.
```

---

## F8.4 — Agente 3: Meeting Prep (Días 6-9)

### Propósito

Genera briefings de preparación para reuniones: lee el calendario, identifica el contexto de los participantes y temas, y busca en el Brain información relevante.

**Cuándo se activa:** "Prepárame para la reunión de las 15h", "Briefing para la reunión de arquitectura", "¿De qué trata la reunión con X?"

### Herramientas

1. MCP Google Calendar — evento, participantes, descripción
2. MCP Slack — conversaciones recientes con participantes
3. `search_brain` — contexto sobre los temas de la reunión
4. `search_bm25` — documentos específicos mencionados

### Estructura de ficheros

```
subagents/builtins/meeting_prep/
├── __init__.py
├── agent.py
├── description.md
├── system_prompt.md
└── tools/
    ├── __init__.py
    ├── index.py
    └── generate_briefing.py   ← Template del briefing
```

### `description.md`

```markdown
Use this agent to prepare for meetings. Triggers: "prepare for meeting",
"briefing for", "meeting prep", "what's the meeting about", "pre-meeting context".
Requires Calendar connector. Works with partial data if other connectors unavailable.
```

---

## F8.5 — Agente 4: Code Explainer (Días 9-13)

### Propósito

Explica código usando la colección `code` de Qdrant y los pasaportes de documentos técnicos. Produce explicaciones en 4 niveles: qué hace, cómo funciona, por qué existe, cómo usarlo.

**Cuándo se activa:** "Explícame `brain_ingestion_adapter.py`", "¿Qué hace la función `_categorize()`?", "Cómo funciona el pipeline de ingesta?", "Explain the BrainRouter"

### Herramientas

1. `search_brain` — busca en pasaportes del código
2. `search_bm25` — busca términos específicos en chunks de código
3. MCP GitHub — código fuente real (si conector disponible)
4. `search_code_passport` — busca específicamente en colección `code` de Qdrant

### Estructura de ficheros

```
subagents/builtins/code_explainer/
├── __init__.py
├── agent.py
├── description.md
├── system_prompt.md
└── tools/
    ├── __init__.py
    ├── index.py
    ├── search_code_passport.py   ← Busca en colección code de Qdrant
    └── explain_code.py           ← Template de explicación 4 niveles
```

### `search_code_passport.py`

```python
"""
search_code_passport.py
-----------------------
Busca en la colección 'code' de Qdrant — específica para explicar código.

La colección code contiene chunks de código Python/SQL/etc procesados por
los extractores especializados de Second Brain (python_file.py, sql_file.py).
Complementa search_brain que busca en pasaportes y knowledge.

F5: los vectores son de nomic-embed-text 768d (mismo que knowledge y brain).
"""
from langchain_core.tools import tool
from app.brain.qdrant_manager import QdrantManager
from app.brain.collections import CODE
from app.indexing_pipeline.unified_embedder import embed_query

@tool
async def search_code_passport(
    query: str,
    search_space_id: int,
    top_k: int = 5,
) -> list[dict]:
    """
    Busca en la colección 'code' de Qdrant.
    Para encontrar implementaciones específicas de funciones, clases, módulos.
    """
    import asyncio
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    embedding = await asyncio.to_thread(embed_query, query)
    qdrant = QdrantManager.get_instance().client
    results = qdrant.search(
        collection_name=CODE,
        query_vector=embedding,
        query_filter=Filter(must=[
            FieldCondition(key="search_space_id",
                          match=MatchValue(value=str(search_space_id)))
        ]),
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "content":  r.payload.get("text", ""),
            "source":   r.payload.get("source", ""),
            "score":    r.score,
            "language": r.payload.get("language", ""),
        }
        for r in results
    ]
```

---

## F8.6 — Registry: registrar los 4 agentes (Día 14)

**Fichero:** `subagents/registry.py` ← MODIFICAR

```python
# Añadir junto al resto de imports de builtins:
from .builtins.knowledge_synthesizer.agent import build_knowledge_synthesizer
from .builtins.project_intelligence.agent import build_project_intelligence
from .builtins.meeting_prep.agent import build_meeting_prep
from .builtins.code_explainer.agent import build_code_explainer

# Añadir en _BUILTIN_BUILDERS:
_BUILTIN_BUILDERS = {
    # ... existentes ...
    "knowledge_synthesizer": build_knowledge_synthesizer,
    "project_intelligence":  build_project_intelligence,
    "meeting_prep":          build_meeting_prep,
    "code_explainer":        build_code_explainer,
}
```

---

## F8.7 — Tests (Días 14-17)

**Fichero:** `tests/agents/test_f8_agents.py`

| Clase | Tests | Qué cubre |
|-------|-------|-----------|
| `TestSharedBrainTools` | 4 | brain_router_tool existe, bm25_tool existe, search_brain llama BrainRouter, search_bm25 llama full_text_search |
| `TestKnowledgeSynthesizer` | 5 | builder registrado, description.md existe, system_prompt.md existe, tool evaluate_coverage, tool synthesize_with_citations |
| `TestProjectIntelligence` | 4 | builder registrado, description.md, system_prompt.md, generate_project_report tool |
| `TestMeetingPrep` | 4 | builder registrado, description.md, system_prompt.md, generate_briefing tool |
| `TestCodeExplainer` | 5 | builder registrado, description.md, system_prompt.md, search_code_passport usa colección CODE, explain_code tool |
| `TestRegistry` | 3 | 4 agentes en _BUILTIN_BUILDERS, imports sin error, nombres correctos |
| `TestF5Integration` | 3 | search_knowledge_base usa nomic 768d, chunks tienen quality_score, colección knowledge existe |
| **Total** | **28** | |

```python
class TestRegistry:
    def test_cuatro_agentes_registrados(self):
        from app.agents.chat.multi_agent_chat.subagents.registry import _BUILTIN_BUILDERS
        for name in ["knowledge_synthesizer", "project_intelligence",
                     "meeting_prep", "code_explainer"]:
            assert name in _BUILTIN_BUILDERS, f"{name} no está en el registry"

    def test_imports_sin_error(self):
        from app.agents.chat.multi_agent_chat.subagents.builtins.knowledge_synthesizer.agent import build_knowledge_synthesizer
        from app.agents.chat.multi_agent_chat.subagents.builtins.project_intelligence.agent import build_project_intelligence
        from app.agents.chat.multi_agent_chat.subagents.builtins.meeting_prep.agent import build_meeting_prep
        from app.agents.chat.multi_agent_chat.subagents.builtins.code_explainer.agent import build_code_explainer

class TestF5Integration:
    def test_knowledge_search_usa_unified_embedder(self):
        src = open("app/agents/chat/multi_agent_chat/shared/middleware/knowledge_search.py").read()
        assert "unified_embedder import embed_query" in src

    def test_search_code_passport_usa_coleccion_code(self):
        src = open("app/agents/chat/multi_agent_chat/subagents/builtins/code_explainer/tools/search_code_passport.py").read()
        assert "from app.brain.collections import CODE" in src
        assert "collection_name=CODE" in src

    def test_coleccion_code_qdrant_existe(self):
        from app.brain.qdrant_manager import QdrantManager
        from app.brain.collections import CODE
        mgr = QdrantManager.get_instance()
        info = mgr.client.get_collection(CODE)
        assert info is not None
```

---

## F8.8 — Cronograma

| Semana | Días | Entregable |
|--------|------|------------|
| 1 (D1-D5) | 5 | F8.1 shared tools + F8.2 Knowledge Synthesizer completo |
| 2 (D6-D10) | 5 | F8.3 Project Intelligence + F8.4 Meeting Prep |
| 3 (D11-D17) | 7 | F8.5 Code Explainer + F8.6 Registry + F8.7 Tests |

---

## F8.9 — Dependencias entre fases

```
F3 (Pasaportes) ──────────────────────────────► F8.2 Knowledge Synthesizer
F4 (Router multinivel) ─── F8.1 Shared tools ──► F8.2 Knowledge Synthesizer
F5 (Pipeline unificado) ──────────────────────► F8.5 Code Explainer
                                                 (colección code con chunks de calidad)
Conectores MCP (SurfSense) ───────────────────► F8.3 Project Intelligence
                                                ► F8.4 Meeting Prep
```

F8.3 y F8.4 se pueden desarrollar en paralelo con F8.2 — no dependen del Brain.

---

## Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| Modelo Ollama lento (>30s) | UX degradada | Reducir top_k a 3, max 2 rondas, acortar prompts |
| Modelo no genera formato correcto | Tool calls fallan | No usar JSON structured — parsear con regex/heurísticas |
| BrainRouter no disponible | KS y CE sin funcionar | Fallback a solo BM25 + web_search |
| Conectores MCP no configurados | PI y MP sin datos | Reportar explícitamente qué falta — degradación graceful |
| Context overflow con pasaportes largos | Truncamiento | Limitar `text[:600]`, max 5 resultados por búsqueda |

---

## Criterios de aceptación

- [ ] Los 4 agentes registrados en `registry.py` sin errores de import
- [ ] Main agent rutea correctamente ≥ 90% de queries de test al agente correcto
- [ ] Knowledge Synthesizer produce respuestas con citaciones `[source:slug]` verificables
- [ ] Project Intelligence reporta conectores no disponibles (degradación graceful)
- [ ] Meeting Prep funciona con solo Calendar conectado
- [ ] Code Explainer usa colección `code` de Qdrant y devuelve explicaciones de 4 niveles
- [ ] Ningún agente falla con error no manejado — todos degradan gracefully
- [ ] Tiempo de respuesta < 30s con qwen2.5-coder:7b en CPU (queries simples)
- [ ] 28 tests unitarios pasan: `pytest tests/agents/test_f8_agents.py -v`

---

## Inventario de ficheros a crear

| # | Fichero | Descripción |
|---|---------|-------------|
| 1 | `_shared_brain_tools/__init__.py` | Package |
| 2 | `_shared_brain_tools/brain_router_tool.py` | Wrapper BrainRouter F4 |
| 3 | `_shared_brain_tools/bm25_search_tool.py` | Wrapper BM25 PostgreSQL |
| 4 | `knowledge_synthesizer/__init__.py` | Package |
| 5 | `knowledge_synthesizer/agent.py` | Builder |
| 6 | `knowledge_synthesizer/description.md` | Router description |
| 7 | `knowledge_synthesizer/system_prompt.md` | System prompt |
| 8 | `knowledge_synthesizer/tools/__init__.py` | Package |
| 9 | `knowledge_synthesizer/tools/index.py` | NAME + load_tools |
| 10 | `knowledge_synthesizer/tools/evaluate_coverage.py` | Tool |
| 11 | `knowledge_synthesizer/tools/synthesize_with_citations.py` | Tool |
| 12 | `project_intelligence/__init__.py` | Package |
| 13 | `project_intelligence/agent.py` | Builder |
| 14 | `project_intelligence/description.md` | Router description |
| 15 | `project_intelligence/system_prompt.md` | System prompt |
| 16 | `project_intelligence/tools/__init__.py` | Package |
| 17 | `project_intelligence/tools/index.py` | NAME + load_tools |
| 18 | `project_intelligence/tools/generate_project_report.py` | Tool |
| 19 | `meeting_prep/__init__.py` | Package |
| 20 | `meeting_prep/agent.py` | Builder |
| 21 | `meeting_prep/description.md` | Router description |
| 22 | `meeting_prep/system_prompt.md` | System prompt |
| 23 | `meeting_prep/tools/__init__.py` | Package |
| 24 | `meeting_prep/tools/index.py` | NAME + load_tools |
| 25 | `meeting_prep/tools/generate_briefing.py` | Tool |
| 26 | `code_explainer/__init__.py` | Package |
| 27 | `code_explainer/agent.py` | Builder |
| 28 | `code_explainer/description.md` | Router description |
| 29 | `code_explainer/system_prompt.md` | System prompt |
| 30 | `code_explainer/tools/__init__.py` | Package |
| 31 | `code_explainer/tools/index.py` | NAME + load_tools |
| 32 | `code_explainer/tools/search_code_passport.py` | Tool — colección code Qdrant |
| 33 | `code_explainer/tools/explain_code.py` | Tool — template 4 niveles |
| 34 | `registry.py` (MODIFICAR) | +4 imports, +4 builder entries |
| 35 | `tests/agents/test_f8_agents.py` | 28 tests |

**Total: 34 ficheros nuevos + 1 modificación**

---

**Anterior:** [F7 — Hardening](./F7-hardening.md)  
**Siguiente:** [F9 — MCP Server](./F9-mcp-server.md)
