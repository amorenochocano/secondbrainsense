# CLAUDE.md — secondbrainsense

> Solo contexto específico del proyecto.
> El comportamiento global, estilo y principios de ingeniería están en el `CLAUDE.md` global.

---

# 1. Contexto del proyecto

Second brain on-premise para Enagás, fork de SurfSense.

Ingesta documentos de múltiples fuentes (ficheros, Confluence, Jira, GitHub, OneDrive, etc.),
extrae contenido estructurado, lo indexa en Qdrant para búsqueda semántica y RAG,
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
surfsense_backend/app/
  brain/             ← NUESTRO MÓDULO: pipeline RAG, extractores, conectores
  agents/            Upstream: agentes de chat y presentaciones de vídeo (LangGraph)
  automations/       Upstream: automatizaciones programadas
  connectors/        Upstream: conectores OAuth (Drive, OneDrive, Dropbox…)
  etl_pipeline/      Upstream: parseo de documentos (Docling/Unstructured/LlamaCloud)
  gateway/           Upstream: gateway de mensajería (Telegram, Slack, Discord, WhatsApp)
  indexing_pipeline/ Upstream: adaptadores de indexación vectorial
  routes/            Upstream: rutas FastAPI
  tasks/             Upstream: tareas Celery
  services/          Upstream: servicios de integración
```

Pipeline Brain (nuestro módulo):

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
surfsense_backend/
  app/brain/
    extractors/         Un extractor por tipo de fichero (pdf, docx, xlsx, pptx, ipynb, csv, …)
    rag_lib/
      detector/         Detección de formato y subtipo + registro de procesadores
      layer1_universal/ Limpieza de texto, quality_score (detección PII con Presidio)
      layer4_assembler/ Ensamblado de chunks (CleanChunk)
      config/           registry_config.yaml
      orchestrator.py   Punto de entrada único del pipeline RAG
    connectors/         Conectores Confluence, Jira, GitHub (módulo brain)
    prompts/            Bloques de prompt, builder, specs de subtipo, preprocesado por tipo
    graph.py            Grafo de relaciones entre documentos (compatible D3 / vis.js)
    writer.py           Lectura/escritura de pasaportes semánticos (.md con frontmatter YAML)
    vocabulary.py       Vocabulario controlado — única fuente de verdad para tags canónicas
  alembic/              Migraciones de BD
  tests/                Tests pytest unitarios e integración

surfsense_web/               Frontend Next.js (upstream)
surfsense_desktop/           App de escritorio Electron (upstream)
surfsense_browser_extension/ Extensión de navegador (upstream)
surfsense_obsidian/          Plugin de Obsidian (upstream)
surfsense_evals/             Scripts de evaluación RAG

docker/                 Configs Docker Compose, colector OTel, config SearXNG

Arquitectura/
  Surfsense/            Decisiones de arquitectura por módulo (M1–M7)
  Fases/                Documentos de planificación por fases
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

## Ubicación

```
Todo el código nuevo va en app/brain/ o sus submódulos.
Nunca modificar código upstream SurfSense sin justificación explícita.
Antes de añadir una dependencia, verificar que no exista ya en pyproject.toml.
```

## Estilo y formato

```
Formateador: ruff format (línea máx. 88, comillas dobles, indentación 4 espacios)
Linter:      ruff check (ver pyproject.toml para reglas activas)
Siempre correr ambos antes de hacer commit.
```

## Convenciones Python

```
Type hints obligatorios en todas las firmas de funciones y métodos públicos.
Clases de datos: usar @dataclass en lugar de dicts cuando el shape sea fijo.
Logging: log = logging.getLogger(__name__) al nivel de módulo, nunca print().
  Formato de mensaje: "[módulo] 'fuente' → descripción: %s", valor
  Nivel debug para flujo normal, warning para estados anómalos no críticos.
Constantes de módulo: MAYÚSCULAS_CON_GUIONES_BAJOS, definidas al inicio del fichero.
Métodos privados: prefijo _ (ej. _compute_heading_threshold).
Patrones de regexp: precompilar con re.compile() como constante de módulo (prefijo _).
```

## Estructura de extractores

```
Cada extractor hereda de BaseExtractor e implementa extract(source: str) -> list[dict].
Un extractor por tipo de fichero — no mezclar formatos en la misma clase.
Añadir soporte para un formato nuevo = una línea en ExtractorFactory._MAP.
Los bloques devueltos siempre incluyen: content, content_type, metadata (dict).
```

## Docstrings

```
Módulo: bloque inicial con nombre, responsabilidad, estrategia y uso de ejemplo.
Clase pública: una línea descriptiva + párrafo si la responsabilidad no es obvia.
Método público: docstring si el comportamiento no es evidente por nombre y tipos.
Métodos privados y código obvio: sin docstring.
```

## Tests y commits

```
Tests: @pytest.mark.unit (sin BD/Qdrant/Ollama) o @pytest.mark.integration.
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

# 10. Seguridad

```
Secretos solo mediante variables de entorno — nunca hardcodeados.
BRAIN_DIR solo mediante variable de entorno.
Presidio (presidio-analyzer / presidio-anonymizer) para detección de PII en UniversalCleaner.
Nunca registrar en logs contenido de documentos ni PII.
AUTH_TYPE: LOCAL (email/contraseña) o GOOGLE (OAuth2).
```

---

# 11. Rendimiento

```
SYNTHESIS_ENABLED: activa síntesis LLM (true) o pasaporte determinista sin LLM (false).
SYNTHESIS_V5_ENABLED: activa Planner v5 (single-call / 2-calls / chunked según perfil del modelo).
LLM_PROVIDER: "ollama" | "claude" | "hybrid".
HYBRID_THRESHOLD: número de tokens a partir del cual el modo hybrid enruta a Claude en lugar de Ollama.
Rerankers: controlados por RERANKERS_ENABLED / RERANKERS_MODEL_NAME.
Caché de agentes: los agentes LangGraph compilados se cachean en memoria (SURFSENSE_ENABLE_AGENT_CACHE).
```

---

# 12. Testing

```
Framework:   pytest (asyncio_mode = auto)
Tests unit:  @pytest.mark.unit — lógica pura, sin BD, sin Qdrant, sin Ollama
Integración: @pytest.mark.integration — requieren PostgreSQL + Qdrant reales
Mocking:     mockear servicios externos en tests unitarios
Config:      surfsense_backend/pyproject.toml [tool.pytest.ini_options]
Evals:       surfsense_evals/ (scripts de evaluación RAG independientes)
```

---

# 13. Comandos

```bash
# Lint
uv run ruff check .
uv run ruff format .

# Tests unitarios
uv run pytest -m unit

# Tests de integración (requieren BD + Qdrant en ejecución)
uv run pytest -m integration

# Arrancar el stack completo
docker compose up
```

---

# 14. Registro de decisiones

```
Orchestrator (orchestrator.py) coexiste con ExtractorFactory durante la migración.
  process()                  → interfaz antigua, compatible con el IngestRouter actual.
  process_to_clean_chunks()  → interfaz nueva para el IngestRouter actualizado.
  No unificar hasta que todos los extractores tengan interfaz .process() (Fase 5+).

La cadena genérica de procesadores (config YAML) está reservada para post-migración.
  No activar hasta que todos los procesadores expongan una interfaz .process() unificada.

vocabulary.py es la única fuente de verdad para las tags.
  ALIAS y _PATTERNS se derivan automáticamente — nunca editarlos directamente.

LLM_PROVIDER=hybrid: enruta por número de tokens (HYBRID_THRESHOLD).
  Ollama para contextos cortos, Claude para contextos largos.
  No cambiar la lógica de enrutamiento sin actualizar la documentación de HYBRID_THRESHOLD.
```

---

# 15. Deuda técnica

```
ExtractorFactory y Orchestrator coexisten temporalmente durante la migración.
No eliminar ExtractorFactory hasta que IngestRouter esté completamente migrado a CleanChunk.
```

---

# 16. Restricciones

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

---

# 17. Documentación

```
Arquitectura/Surfsense/00-indice.md      Índice de módulos (M1–M7)
Arquitectura/Surfsense/M4-tuning-rag.md  Decisiones de tuning RAG
surfsense_backend/pyproject.toml         Dependencias y configuración de herramientas
surfsense_backend/.env.example           Todas las variables de entorno con descripciones
docker/.env.example                      Configuración Docker Compose
```
