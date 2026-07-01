# CLAUDE.md — secondbrainsense

> Solo contexto específico del proyecto.
> El comportamiento global, estilo y principios de ingeniería están en el `CLAUDE.md` global.

---

# 1. Contexto del proyecto

Second brain on-premise para Enagás, fork de SurfSense.

Ingesta documentos de múltiples fuentes (ficheros, Confluence, Jira, GitHub, OneDrive, etc.),
extrae contenido estructurado, lo indexa en Qdrant para búsqueda  hibrida semántica y RAG,
y lo expone mediante una interfaz de chat.

Usuarios: equipos internos de Enagás.

Orden de prioridad: integridad de datos → mantenibilidad → rendimiento.
---
# 2. Stack tecnológico

```
Backend
- Python 3.12
- FastAPI
- LangGraph + LangChain
- LiteLLM (abstracción LLM)
- Celery + Redis (cola de tareas async)
- SQLAlchemy + Alembic

LLM (local)
- Ollama en la máquina host
- Modelos: deepseek-r1:14b (por defecto), qwen2.5-coder:7b (síntesis), qwen2.5-coder:3b (chat)
- Embeddings: nomic-embed-text (brain/knowledge), qwen3-embedding:4b (código)

Vector store
- Qdrant

Base de datos
- PostgreSQL + pgvector

Parseo de documentos (ETL)
- Docling (por defecto), Unstructured o LlamaCloud (variable ETL_SERVICE)

Almacenamiento de ficheros
- Sistema de ficheros local (por defecto) o Azure Blob Storage

Observabilidad
- OpenTelemetry + LangSmith

Frontend
- Next.js 16, React 19, TypeScript 5

App de escritorio
- Electron (surfsense_desktop)

Extensión de navegador
- surfsense_browser_extension

Plugin de Obsidian
- surfsense_obsidian

Gestor de paquetes (backend)
- uv

Linter / formateador
- ruff (line-length 88, py312)

Infraestructura
- Docker Compose
```
---
# 3. Arquitectura

```
Fuente (fichero / conector)
↓
FormatDetector + SubtypeDetector   [brain/rag_lib/detector]
↓
Extractor                           [brain/extractors/]
↓
UniversalCleaner                   [brain/rag_lib/layer1_universal]
↓
ChunkAssembler                     [brain/rag_lib/layer4_assembler]
↓
Writer → pasaporte semántico (.md) [brain/writer.py]
↓
BrainGraph (relaciones)            [brain/graph.py]
```

`app/brain/` es nuestro módulo. Todo lo que está fuera es upstream SurfSense — no modificar sin justificación explícita.

---

# 4. Estructura de directorios

```
surfsense_backend/app/
  brain/                    ← NUESTRO MÓDULO
    brain_ingest.py         punto de entrada de ingesta
    brain_watcher.py        watcher asyncio para re-indexar .md editados
    ingest_router.py        enruta CleanChunks a Qdrant
    synthesizer.py          síntesis LLM del pasaporte (Planner v5)
    llm_client.py           cliente unificado Ollama/Claude/hybrid
    qdrant_manager.py       gestión de colecciones Qdrant
    vocabulary.py           vocabulario controlado (única fuente de verdad)
    writer.py               lectura/escritura pasaportes .md
    graph.py                grafo de relaciones D3/vis.js
    passport_builder.py     construcción determinista del pasaporte
    model_profiles.py       perfiles de modelo (capacidad, límites)
    masters.py              maestros de dominio/subtipo
    metadata_service.py     enriquecimiento de metadatos
    chunking.py             utilidades de chunking
    collections.py          definición de colecciones Qdrant
    router.py               rutas FastAPI del brain
    extractors/
      base.py               BaseExtractor (heredar aquí)
      factory.py            ExtractorFactory._MAP (registrar aquí)
      pdf.py, docx.py, xlsx.py, pptx.py, csv.py, txt.py
      ipynb.py, markdown.py, drawio.py, xml_ext.py
      json_fabric.py, python_file.py, sql_file.py, web.py
      confluence.py, jira_ticket.py, github_file.py
    connectors/
      factory.py            ConnectorFactory
      confluence_connector.py, jira_connector.py, github_connector.py
    rag_lib/
      orchestrator.py       entrada única del pipeline RAG
      detector/             format_detector.py, subtype_detector.py, processor_registry.py
      layer1_universal/     universal_cleaner.py (limpieza + PII + quality_score)
      layer4_assembler/     chunk_assembler.py → CleanChunk
      config/               registry_config.yaml
    prompts/
      builder.py, blocks.py, planner.py, subtypes.py, type_specs.py
      preprocessing/        un fichero por tipo (pdf, docx, xlsx…)

  agents/                   upstream: agentes LangGraph (chat, vídeo)
  automations/              upstream: automatizaciones programadas
  connectors/               upstream: OAuth (Drive, OneDrive, Dropbox…)
  etl_pipeline/             upstream: parseo ETL (Docling/Unstructured/LlamaCloud)
  gateway/                  upstream: mensajería (Telegram, Slack, Discord, WhatsApp)
  indexing_pipeline/        upstream: adaptadores indexación vectorial
  routes/                   upstream: rutas FastAPI principales
  tasks/                    upstream: tareas Celery
  services/                 upstream: servicios de integración externos

surfsense_web/              frontend Next.js 16 + React 19 + TypeScript 5 (upstream)
  app/                    rutas Next.js App Router
    (home)/               landing page
    auth/                 login/registro
    dashboard/            panel principal
    api/                  API routes Next.js
  components/             componentes React por dominio
    brain/                UI del brain (pasaportes, grafo)
    new-chat/             interfaz de chat principal
    connectors/           gestión de conectores
    documents/            gestión de documentos
    settings/             configuración de usuario/espacio
    ui/                   componentes base (shadcn/ui)
    shared/               componentes reutilizables
  atoms/                  estado global Jotai por dominio
  lib/                    utilidades y clientes API
    apis/                 llamadas al backend
    brain/                utilidades del brain
    chat/                 lógica de chat
  contracts/              tipos e interfaces TypeScript compartidos
    types/                tipos de dominio
    enums/                enumeraciones
  hooks/                  custom hooks React
  contexts/               React contexts
  features/               lógica de negocio por feature
  zero/                   ZeroSync (queries/schema)
  tests/                  Playwright e2e + smoke tests
  Herramientas: pnpm, Biome (linter/formatter), Tailwind, Drizzle, Playwright

surfsense_desktop/          Electron (upstream)
surfsense_browser_extension/ extensión navegador (upstream)
surfsense_obsidian/         plugin Obsidian (upstream)
surfsense_evals/            evaluación RAG
docker/                     Docker Compose, OTel collector, SearXNG
Arquitectura/Surfsense/     decisiones de arquitectura M1–M7
```

---

# 5. Vocabulario del proyecto

El vocabulario canónico está definido en `vocabulary.py` — única fuente de verdad.
No inventar nombres de tags; siempre usar o extender `VOCABULARY`.

```
Pasaporte semántico  = fichero .md generado por documento ingestado, almacenado en BRAIN_DIR
Brain               = la base de conocimiento indexada (BRAIN_DIR, por defecto /data/brain)
Tag canónica        = forma oficial de una tag (clave izquierda en VOCABULARY)
Alias               = variante que se normaliza a una tag canónica
CleanChunk          = salida del ChunkAssembler, lista para indexación vectorial
quality_score       = puntuación numérica asignada por UniversalCleaner a cada bloque
Search Space        = concepto upstream: espacio de conocimiento aislado por usuario/equipo
```

---

# 6. Estándares de código

```
Todo el código nuevo va en app/brain/ o sus submódulos.
Nunca modificar código upstream SurfSense sin justificación explícita.
Antes de añadir una dependencia, verificar que no exista ya en pyproject.toml.
Tests: @pytest.mark.unit o @pytest.mark.integration.
Mensajes de commit: cortos, en español o inglés, sin emojis.
Timestamps: UTC.
Vocabulario: editar solo el dict VOCABULARY en vocabulary.py — las estructuras derivadas se actualizan solas.
```

---

# 7. Reglas de negocio

```
Todo documento ingestado debe producir un pasaporte semántico (.md con frontmatter).
Las tags deben normalizarse a forma canónica antes de almacenarse.
BRAIN_DIR nunca debe estar hardcodeado — siempre leer de la variable de entorno.
El grafo (_graph.json) se deriva del frontmatter; nunca escribirlo manualmente.
El PII debe detectarse y anonimizarse (Presidio) antes de indexar.
Nunca registrar en logs el contenido de documentos ni PII.
```
---
# 8. Integraciones externas

```
Ollama
  Propósito: inferencia LLM local (síntesis + chat + embeddings)
  Conexión: OLLAMA_HOST (por defecto http://host.docker.internal:11434)
  Restricción: corre en el host, no dentro del contenedor

Qdrant
  Propósito: almacén vectorial para embeddings de chunks
  Restricción: requiere instancia en ejecución (Docker Compose)

Confluence / Jira
  Propósito: ingesta de documentos y tickets
  Auth: Atlassian OAuth (ATLASSIAN_CLIENT_ID / ATLASSIAN_CLIENT_SECRET)

GitHub
  Propósito: ingesta de ficheros de repositorios
  Auth: personal access token (variables de entorno)

Redis
  Propósito: broker Celery + result backend + caché de app
  Conexión: REDIS_URL

SearXNG
  Propósito: búsqueda web integrada
  Conexión: SEARXNG_DEFAULT_HOST (Docker Compose lo configura automáticamente)

LangSmith (opcional)
  Propósito: observabilidad y trazado de llamadas LLM
  Config: LANGSMITH_API_KEY, LANGSMITH_PROJECT

OpenTelemetry (opcional)
  Propósito: trazado distribuido y métricas
  Config: OTEL_EXPORTER_OTLP_ENDPOINT
```
---
# 9. Base de datos
```
Base de datos: PostgreSQL + pgvector
ORM:           SQLAlchemy (async)
Migraciones:   Alembic
PKs:           UUID
Regla:         Nunca modificar migraciones ya aplicadas.
```
---

# 10. Testing

```
Framework:   pytest (asyncio_mode = auto)
Tests unit:  @pytest.mark.unit — lógica pura, sin BD, sin Qdrant, sin Ollama
Integración: @pytest.mark.integration — requieren PostgreSQL + Qdrant reales
Mocking:     mockear servicios externos en tests unitarios
Config:      surfsense_backend/pyproject.toml [tool.pytest.ini_options]
Evals:       surfsense_evals/ (scripts de evaluación RAG independientes)
```


# 11. Restricciones

```
No modificar código upstream SurfSense sin justificación explícita.
No renombrar las variables de entorno BRAIN_DIR ni OLLAMA_HOST.
No añadir dependencias sin verificar primero pyproject.toml.
No hardcodear rutas, secretos ni nombres de modelos.
No editar ALIAS ni _PATTERNS en vocabulary.py directamente — son derivados.
No modificar migraciones Alembic ya aplicadas.
No cambiar el esquema de frontmatter del pasaporte semántico sin actualizar writer.py y graph.py.
No tocar surfsense_web/, surfsense_desktop/, surfsense_browser_extension/, surfsense_obsidian/ salvo petición explícita.
```