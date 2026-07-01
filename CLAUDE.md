# CLAUDE.md — secondbrainsense

> Solo contexto específico del proyecto. Comportamiento global en el `CLAUDE.md` global.

---

## Proyecto

Fork on-premise de SurfSense para Enagás. Ingesta documentos, genera pasaportes semánticos (.md), indexa en Qdrant para RAG.

Prioridad: integridad de datos → mantenibilidad → rendimiento.

---

## Límite upstream / nuestro módulo

`app/brain/` es nuestro módulo. **Todo lo demás es upstream SurfSense — no tocar sin justificación explícita.**

Incluye: `surfsense_web/`, `surfsense_desktop/`, `surfsense_browser_extension/`, `surfsense_obsidian/`.

---

## Stack resumido

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, LangGraph, LiteLLM, Celery+Redis |
| LLM local | Ollama en host (`OLLAMA_HOST`) |
| Vector store | Qdrant |
| BD | PostgreSQL + pgvector, SQLAlchemy async, Alembic |
| ETL | Docling / Unstructured / LlamaCloud (`ETL_SERVICE`) |
| Frontend | Next.js 16, React 19, TypeScript 5 |
| Infra | Docker Compose, uv, ruff |

---

## Estructura de directorios

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

## Pipeline Brain (`app/brain/`)

```
Fuente → FormatDetector+SubtypeDetector → Extractor → UniversalCleaner → ChunkAssembler → Writer(.md) → BrainGraph
```

Ref: `rag_lib/detector`, `extractors/`, `rag_lib/layer1_universal`, `rag_lib/layer4_assembler`, `writer.py`, `graph.py`.

---

## Reglas no negociables

```
BRAIN_DIR y OLLAMA_HOST: solo desde env var, nunca hardcodeados.
Tags: normalizar siempre a canónica antes de guardar (vocabulary.py).
ALIAS y _PATTERNS en vocabulary.py: son derivados — nunca editar directamente.
Pasaporte semántico: obligatorio por documento ingestado.
_graph.json: derivado del frontmatter — nunca escribir manualmente.
PII: detectar y anonimizar (Presidio) antes de indexar.
Migraciones Alembic: nunca modificar las ya aplicadas.
Frontmatter del pasaporte: cambiar schema → actualizar writer.py y graph.py.
Dependencias: verificar pyproject.toml antes de añadir cualquiera nueva.
```

---

## Decisiones de arquitectura (no "mejorar")

```
Orchestrator + ExtractorFactory coexisten durante migración (Fase 5+ para unificar).
  process()                 → interfaz antigua (IngestRouter actual)
  process_to_clean_chunks() → interfaz nueva (IngestRouter nuevo)

Pipeline YAML genérico: reservado post-migración, no activar aún.

LLM_PROVIDER=hybrid: Ollama para contextos cortos, Claude por encima de HYBRID_THRESHOLD tokens.
```

---

## Vocabulario clave

```
Pasaporte semántico = .md con frontmatter YAML por cada documento ingestado
Brain               = base de conocimiento en BRAIN_DIR (/data/brain por defecto)
CleanChunk          = salida del ChunkAssembler, lista para indexación vectorial
quality_score       = puntuación del UniversalCleaner por bloque
Search Space        = concepto upstream: namespace de conocimiento por usuario/equipo
```

---

## Convenciones de código (solo lo no obvio)

```
Extractores: heredan BaseExtractor, implementan extract(source: str) -> list[dict].
  Bloque mínimo: {content, content_type, metadata}.
  Un extractor por formato. Registrar en ExtractorFactory._MAP.

Logging: log = logging.getLogger(__name__) a nivel módulo.
  Formato: "[módulo] 'fuente' → descripción: %s"
  Nunca print(). Nunca loguear contenido de documentos ni PII.

Tests: @pytest.mark.unit (sin servicios externos) | @pytest.mark.integration.
Commits: cortos, español o inglés, sin emojis.
```

---

## Deuda técnica activa

ExtractorFactory no eliminar hasta que IngestRouter esté 100% migrado a CleanChunk.

---

## Documentación de referencia

```
Arquitectura/Surfsense/00-indice.md   módulos M1–M7
surfsense_backend/.env.example        todas las variables de entorno
surfsense_backend/pyproject.toml      dependencias y config de herramientas
```
