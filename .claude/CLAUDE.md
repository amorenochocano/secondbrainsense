# CLAUDE.md — secondbrainsense
Comportamiento global en CLAUDE.md global. Aquí solo contexto específico del proyecto.

## Contexto
Second brain on-premise para Enagás, fork de SurfSense. Ingesta documentos (ficheros, Confluence, Jira, GitHub, OneDrive), indexa en Qdrant para RAG, expone interfaz de chat.
Usuarios: equipos internos Enagás. Prioridad: integridad de datos → mantenibilidad → rendimiento.

## Stack
- Backend: Python 3.12, FastAPI, LangGraph+LangChain, LiteLLM, Celery+Redis, SQLAlchemy+Alembic
- LLM local (Ollama): deepseek-r1:14b (default), qwen2.5-coder:7b (síntesis), qwen2.5-coder:3b (chat)
- Embeddings: nomic-embed-text (brain/knowledge), qwen3-embedding:4b (código)
- Vector store: Qdrant | BD: PostgreSQL+pgvector | ETL: Docling/Unstructured/LlamaCloud (ETL_SERVICE)
- Storage: local FS o Azure Blob | Observabilidad: OpenTelemetry+LangSmith
- Frontend: Next.js 16, React 19, TypeScript 5 | Desktop: Electron | Ext: browser extension, Obsidian plugin
- Tooling: uv, ruff (line-length 88, py312), Docker Compose

## Arquitectura del pipeline
```
Fuente → FormatDetector+SubtypeDetector [rag_lib/detector]
       → Extractor [extractors/]
       → UniversalCleaner [rag_lib/layer1_universal]
       → ChunkAssembler [rag_lib/layer4_assembler]
       → Writer → pasaporte .md [writer.py]
       → BrainGraph [graph.py]
```
`app/brain/` es nuestro módulo. Todo lo externo es upstream SurfSense — no modificar sin justificación explícita.

## Estructura de directorios
```
surfsense_backend/app/
  brain/                    ← NUESTRO MÓDULO
    brain_ingest.py         entrada de ingesta
    brain_watcher.py        watcher re-indexar .md editados
    ingest_router.py        enruta CleanChunks a Qdrant
    synthesizer.py          síntesis LLM pasaporte (Planner v5)
    llm_client.py           cliente unificado Ollama/Claude/hybrid
    qdrant_manager.py       gestión colecciones Qdrant
    vocabulary.py           vocabulario controlado (única fuente de verdad)
    writer.py               lectura/escritura pasaportes .md
    graph.py                grafo relaciones D3/vis.js
    passport_builder.py     construcción determinista pasaporte
    model_profiles.py       perfiles modelo (capacidad, límites)
    masters.py              maestros dominio/subtipo
    metadata_service.py     enriquecimiento metadatos
    chunking.py             utilidades chunking
    collections.py          definición colecciones Qdrant
    router.py               rutas FastAPI brain
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
      orchestrator.py       entrada única pipeline RAG
      detector/             format_detector.py, subtype_detector.py, processor_registry.py
      layer1_universal/     universal_cleaner.py (limpieza+PII+quality_score)
      layer4_assembler/     chunk_assembler.py → CleanChunk
      config/               registry_config.yaml
    prompts/
      builder.py, blocks.py, planner.py, subtypes.py, type_specs.py
      preprocessing/        un fichero por tipo (pdf, docx, xlsx…)
  agents/         upstream: agentes LangGraph
  automations/    upstream: automatizaciones
  connectors/     upstream: OAuth (Drive, OneDrive, Dropbox…)
  etl_pipeline/   upstream: parseo ETL
  gateway/        upstream: mensajería (Telegram, Slack, Discord, WhatsApp)
  indexing_pipeline/ upstream: adaptadores indexación vectorial
  routes/         upstream: rutas FastAPI principales
  tasks/          upstream: tareas Celery
  services/       upstream: servicios externos

surfsense_web/    frontend Next.js (upstream) — app/, components/, atoms/, lib/, contracts/, hooks/, contexts/, features/, zero/, tests/
surfsense_desktop/ surfsense_browser_extension/ surfsense_obsidian/ — upstream
surfsense_evals/  evaluación RAG
docker/           Docker Compose, OTel, SearXNG
Arquitectura/Surfsense/ decisiones M1–M7
```

## Vocabulario
Fuente de verdad: `vocabulary.py`. No inventar tags; usar o extender `VOCABULARY`.
- Pasaporte semántico: .md con frontmatter por documento ingestado, en BRAIN_DIR
- Brain: base de conocimiento indexada (BRAIN_DIR, default /data/brain)
- Tag canónica: forma oficial (clave izquierda en VOCABULARY)
- Alias: variante normalizada a tag canónica
- CleanChunk: salida ChunkAssembler, lista para indexación vectorial
- quality_score: puntuación UniversalCleaner por bloque
- Search Space: namespace de conocimiento por usuario/equipo (concepto upstream)

## Reglas de código y negocio
- Todo código nuevo en app/brain/ o submódulos.
- No modificar upstream SurfSense sin justificación explícita.
- Verificar pyproject.toml antes de añadir dependencias.
- Tests: @pytest.mark.unit (sin servicios externos) | @pytest.mark.integration (PostgreSQL+Qdrant reales).
- Commits: cortos, español o inglés, sin emojis. Timestamps: UTC.
- Editar solo VOCABULARY en vocabulary.py — ALIAS y _PATTERNS son derivados, no editar directamente.
- Todo documento ingestado → pasaporte semántico (.md con frontmatter).
- Tags: normalizar a canónica antes de guardar.
- BRAIN_DIR y OLLAMA_HOST: solo desde env var, nunca hardcodeados.
- _graph.json: derivado del frontmatter, nunca escribir manualmente.
- PII: detectar y anonimizar (Presidio) antes de indexar. Nunca loguear contenido ni PII.
- No modificar migraciones Alembic ya aplicadas.
- Cambio en schema de frontmatter → actualizar writer.py y graph.py.
- No tocar surfsense_web/, surfsense_desktop/, surfsense_browser_extension/, surfsense_obsidian/ salvo petición explícita.

## Integraciones externas
- Ollama: inferencia LLM local. OLLAMA_HOST (default http://host.docker.internal:11434). Corre en host, no en contenedor.
- Qdrant: almacén vectorial. Requiere instancia activa (Docker Compose).
- Confluence/Jira: ATLASSIAN_CLIENT_ID / ATLASSIAN_CLIENT_SECRET (OAuth).
- GitHub: personal access token (env vars).
- Redis: broker Celery + cache. REDIS_URL.
- SearXNG: búsqueda web. SEARXNG_DEFAULT_HOST (auto Docker Compose).
- LangSmith (opcional): LANGSMITH_API_KEY, LANGSMITH_PROJECT.
- OpenTelemetry (opcional): OTEL_EXPORTER_OTLP_ENDPOINT.

## Base de datos
PostgreSQL+pgvector | SQLAlchemy async | Alembic | PKs: UUID | No modificar migraciones aplicadas.

## Testing
pytest (asyncio_mode=auto) | Config: surfsense_backend/pyproject.toml [tool.pytest.ini_options] | Evals: surfsense_evals/

## Decisiones arquitectónicas (no "mejorar")
- ExtractorFactory coexiste con Orchestrator durante migración (Fase 5+ para unificar).
  - process() → interfaz antigua (IngestRouter actual)
  - process_to_clean_chunks() → interfaz nueva (IngestRouter nuevo)
- Pipeline YAML genérico: reservado post-migración, no activar aún.
- LLM_PROVIDER=hybrid: Ollama para contextos cortos, Claude por encima de HYBRID_THRESHOLD tokens.

## Referencias
- Arquitectura/Surfsense/00-indice.md — módulos M1–M7
- surfsense_backend/.env.example — variables de entorno
- surfsense_backend/pyproject.toml — dependencias y config
