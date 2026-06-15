"""
masters.py
----------
Gestión de maestros (domain, subdomain, doc_type) con SQLite.

Filosofía idéntica a vocabulary.py: la clasificación se basa en el CONTENIDO
(tags ya extraídas + keyphrases + título), no en la extensión ni en el nombre
del fichero.

Cada maestro tiene "signal_tags" y "signal_kw" (señales). Se puntúa cuántas
señales coinciden con el documento → el que más puntos saca gana.

Almacenamiento:
  · SQLite en {BRAIN_DIR}/masters.db (volumen persistente).
  · Al arrancar se carga todo en memoria → clasificación instantánea sin I/O.
  · Para ampliar: INSERT en la DB y llamar reload(), o exponer un CRUD REST.

Funciones públicas:
  classify_document(tags, keyphrases, title, file_type) -> dict
  reload()        → recarga la caché desde SQLite
  get_domains()   → lista de domains
  get_subdomains(domain_id=None) → lista de subdomains
  get_doc_types() → lista de doc_types
"""

import json
import os
import re
import sqlite3
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

_BRAIN_DIR = os.environ.get("BRAIN_DIR", os.path.join(os.path.dirname(__file__), ".."))
_DB_PATH = os.path.join(_BRAIN_DIR, "masters.db")

# Umbrales mínimos de score para asignar (por debajo → "other"/"general")
_MIN_SCORE_DOMAIN = 2
_MIN_SCORE_SUBDOMAIN = 2
_MIN_SCORE_DOCTYPE = 1.5

# ---------------------------------------------------------------------------
# Caché en memoria (se carga desde SQLite una vez)
# ---------------------------------------------------------------------------

_domains: dict[str, dict] = {}      # id → {label, description, signal_tags, signal_kw}
_subdomains: dict[str, dict] = {}   # id → {domain_id, label, signal_tags, signal_kw}
_doc_types: dict[str, dict] = {}    # id → {label, signal_tags, signal_kw, signal_formats}
_entity_hints: list[dict] = []      # lista de hints con domain_id/doc_type_id opcionales
_loaded: bool = False

# ---------------------------------------------------------------------------
# Schema + seed
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT DEFAULT '',
    signal_tags TEXT DEFAULT '[]',    -- JSON array
    signal_kw   TEXT DEFAULT '[]'     -- JSON array
);

CREATE TABLE IF NOT EXISTS subdomains (
    id          TEXT PRIMARY KEY,
    domain_id   TEXT NOT NULL REFERENCES domains(id),
    label       TEXT NOT NULL,
    signal_tags TEXT DEFAULT '[]',
    signal_kw   TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS doc_types (
    id             TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    signal_tags    TEXT DEFAULT '[]',
    signal_kw      TEXT DEFAULT '[]',
    signal_formats TEXT DEFAULT '[]'  -- JSON array de extensiones
);

CREATE TABLE IF NOT EXISTS entity_hints (
    id           TEXT PRIMARY KEY,
    domain_id    TEXT,               -- NULL = aplica a todos los dominios
    doc_type_id  TEXT,               -- NULL = aplica a todos los tipos
    label        TEXT NOT NULL,      -- descripción del patrón
    patterns     TEXT DEFAULT '[]',  -- JSON array de patrones regex
    examples     TEXT DEFAULT '[]'   -- JSON array de ejemplos para el LLM
);
"""

# Seed: datos iniciales. Ampliar aquí o vía SQL/API.
_SEED_DOMAINS = [
    {
        "id": "data-engineering",
        "label": "Data Engineering",
        "description": "Pipelines, transformaciones, ingestión y almacenamiento de datos",
        "signal_tags": [
            "pyspark", "spark-sql", "delta-lake", "etl", "data-pipeline",
            "medallion", "bronze-layer", "silver-layer", "gold-layer",
            "airflow", "kafka", "dbt", "data-lake", "data-warehouse",
            "parquet", "batch-processing", "streaming", "orchestration",
            "data-quality", "data-governance", "dataframe", "pandas", "numpy",
        ],
        "signal_kw": [
            "transformación", "ingesta", "pipeline", "lakehouse", "capa",
            "etl", "elt", "medallion", "bronze", "silver", "gold",
        ],
    },
    {
        "id": "cloud-azure",
        "label": "Cloud Azure",
        "description": "Servicios y plataforma Microsoft Azure",
        "signal_tags": [
            "azure", "azure-data-factory", "azure-synapse", "microsoft-fabric",
            "azure-devops", "azure-functions", "azure-blob-storage",
            "azure-data-lake", "azure-sql", "power-bi", "event-hubs",
            "key-vault", "databricks",
        ],
        "signal_kw": [
            "azure", "fabric", "synapse", "databricks", "power bi",
            "data factory", "adf",
        ],
    },
    {
        "id": "cloud-aws",
        "label": "Cloud AWS",
        "description": "Servicios y plataforma Amazon Web Services",
        "signal_tags": [
            "aws", "s3", "ec2", "aws-lambda", "aws-glue", "redshift",
            "athena", "dynamodb", "sagemaker", "cloudformation", "ecs", "eks",
        ],
        "signal_kw": ["amazon", "aws", "s3", "lambda", "redshift", "sagemaker"],
    },
    {
        "id": "testing",
        "label": "Testing & QA",
        "description": "Pruebas, calidad de software, automatización de tests",
        "signal_tags": [
            "testing", "test-case", "test-plan", "unit-testing",
            "integration-testing", "e2e-testing", "regression-testing",
            "selenium", "cypress", "postman", "bdd", "tdd",
            "test-automation", "test-management", "xray",
        ],
        "signal_kw": [
            "pruebas", "test", "caso de prueba", "plan de pruebas",
            "automatización", "qa", "quality", "calidad",
        ],
    },
    {
        "id": "devops-infra",
        "label": "DevOps & Infrastructure",
        "description": "CI/CD, contenedores, IaC, observabilidad",
        "signal_tags": [
            "docker", "kubernetes", "helm", "terraform", "ansible",
            "ci-cd", "github-actions", "gitlab-ci", "jenkins",
            "monitoring", "logging", "observability", "prometheus",
            "grafana", "linux", "bash", "nginx", "serverless",
            "microservices", "devops",
        ],
        "signal_kw": [
            "deploy", "despliegue", "contenedor", "container", "infra",
            "pipeline ci", "github actions", "kubernetes",
        ],
    },
    {
        "id": "development",
        "label": "Software Development",
        "description": "Desarrollo de aplicaciones, APIs, frontend, backend",
        "signal_tags": [
            "python", "fastapi", "flask", "django", "pydantic", "pytest",
            "rest-api", "graphql", "javascript", "typescript", "react",
            "angular", "vue", "nodejs", "html", "css", "web", "asyncio",
            "poetry", "conda", "pip",
        ],
        "signal_kw": [
            "api", "endpoint", "frontend", "backend", "desarrollo",
            "aplicación", "módulo", "librería", "framework",
        ],
    },
    {
        "id": "architecture",
        "label": "Architecture & Design",
        "description": "Arquitectura de software, patrones, modelado de datos",
        "signal_tags": [
            "architecture", "solution-architecture", "design-pattern",
            "data-model", "api-design", "event-driven",
            "domain-driven-design", "diagram",
        ],
        "signal_kw": [
            "arquitectura", "diseño", "patrón", "modelo de datos",
            "diagrama", "flujo", "componente",
        ],
    },
    {
        "id": "data-science-ml",
        "label": "Data Science & ML",
        "description": "Machine learning, IA, NLP, modelos",
        "signal_tags": [
            "machine-learning", "deep-learning", "llm", "rag", "embeddings",
            "vector-database", "ollama", "langchain", "huggingface",
            "openai", "prompt-engineering", "nlp",
        ],
        "signal_kw": [
            "modelo", "entrenamiento", "predicción", "embedding",
            "clasificación", "regresión", "neural", "inteligencia artificial",
        ],
    },
    {
        "id": "databases",
        "label": "Databases",
        "description": "Bases de datos relacionales y NoSQL",
        "signal_tags": [
            "sql", "postgresql", "mysql", "mongodb", "redis",
            "sqlserver", "oracle-db", "sqlite",
        ],
        "signal_kw": [
            "base de datos", "tabla", "query", "índice", "database",
            "schema", "stored procedure",
        ],
    },
    {
        "id": "project-management",
        "label": "Project Management",
        "description": "Gestión de proyectos, incidencias, documentación colaborativa",
        "signal_tags": ["jira", "confluence", "xray", "test-management"],
        "signal_kw": [
            "proyecto", "sprint", "backlog", "incidencia", "ticket",
            "epic", "story", "kanban", "scrum", "agile",
        ],
    },
    {
        "id": "business-intelligence",
        "label": "Business Intelligence",
        "description": "Reporting, dashboards, KPIs y análisis de negocio",
        "signal_tags": [
            "power-bi", "kpi", "analytics",
            "data-visualization", "spreadsheet", "estimation",
        ],
        "signal_kw": [
            "informe", "report", "dashboard", "kpi", "indicador",
            "analítica", "visualización", "cuadro de mando", "power bi",
        ],
    },
    {
        "id": "generative-ai",
        "label": "Generative AI & Agents",
        "description": "LLMs, RAG, agentes IA, prompt engineering, MCP, Copilot, Semantic Kernel, AutoGen",
        "signal_tags": [
            "llm", "rag", "ai-agent", "multi-agent", "prompt-engineering",
            "embeddings", "vector-database", "tool-calling", "agent-memory",
            "semantic-kernel", "autogen", "langgraph", "copilot-studio",
            "github-copilot", "mcp", "agent-eval", "fine-tuning",
            "chain-of-thought", "system-prompt", "context-window",
            "langchain", "openai", "azure-openai", "ollama",
        ],
        "signal_kw": [
            "agente ia", "ai agent", "copilot", "semantic kernel", "autogen",
            "langgraph", "langchain", "rag", "retrieval augmented", "llm",
            "prompt", "sistema de prompts", "tool calling", "function calling",
            "mcp", "model context protocol", "ragas", "evaluación llm",
            "fine-tuning", "ajuste fino", "embeddings", "vector store",
            "copilot studio", "github copilot", "agentic", "agent mode",
        ],
    },
    {
        "id": "procurement",
        "label": "Procurement & Contracts",
        "description": "Procesos de adquisición, licitaciones, RFP, RFI, VCT y contratos",
        "signal_tags": [
            "rfp", "rfi", "vct", "sow", "procurement", "contract", "tender",
            "technical-offer", "requirements", "specification",
        ],
        "signal_kw": [
            "solicitud de propuesta", "solicitud de información", "licitación",
            "pliego", "oferta técnica", "contrato", "verificación", "cumplimiento técnico",
            "rfp", "rfi", "vct", "concurso", "adjudicación", "proveedor",
        ],
    },
    {
        "id": "asset-operations",
        "label": "Asset Management & Operations",
        "description": "Gestión de activos industriales, mantenimiento y tecnología operacional",
        "signal_tags": [
            "asset-management", "predictive-maintenance", "preventive-maintenance",
            "asset-health", "operational-technology", "iiot", "scada",
            "historian", "sensor-data", "anomaly-detection", "condition-monitoring",
        ],
        "signal_kw": [
            "activo", "mantenimiento predictivo", "índice de salud", "pozo",
            "sensor", "telemetría", "scada", "historian", "avería",
            "condición", "predictor", "o&m", "operación y mantenimiento",
            "planta", "infraestructura gas",
        ],
    },
]

_SEED_SUBDOMAINS = [
    # ── data-engineering ─────────────────────────────────────────────────
    {"id": "ingestion",       "domain_id": "data-engineering", "label": "Ingestion",
     "signal_tags": ["data-pipeline", "etl", "kafka", "streaming", "batch-processing", "airflow"],
     "signal_kw": ["ingesta", "ingestión", "carga", "source", "origen", "fuente de datos"]},
    {"id": "transformation",  "domain_id": "data-engineering", "label": "Transformation",
     "signal_tags": ["pyspark", "spark-sql", "delta-lake", "dbt", "dataframe", "pandas"],
     "signal_kw": ["transformación", "limpieza", "join", "merge", "enriquecimiento", "deduplicación"]},
    {"id": "storage",         "domain_id": "data-engineering", "label": "Storage & Lakehouse",
     "signal_tags": ["data-lake", "data-warehouse", "parquet", "azure-data-lake", "s3", "delta-lake"],
     "signal_kw": ["almacenamiento", "storage", "lakehouse", "warehouse", "datalake", "onelake"]},
    {"id": "medallion-arch",  "domain_id": "data-engineering", "label": "Medallion Architecture",
     "signal_tags": ["medallion", "bronze-layer", "silver-layer", "gold-layer"],
     "signal_kw": ["medallion", "bronze", "silver", "gold", "capa", "landing", "raw", "curated"]},
    {"id": "data-quality-gov","domain_id": "data-engineering", "label": "Data Quality & Governance",
     "signal_tags": ["data-quality", "data-governance", "metadata", "schema",
                     "data-quality-rule", "data-catalog", "data-lineage"],
     "signal_kw": ["calidad del dato", "gobernanza", "linaje", "lineage", "catálogo", "validación",
                   "regla de calidad", "trazabilidad"]},
    {"id": "orchestration-eng","domain_id": "data-engineering", "label": "Orchestration & DAG",
     "signal_tags": ["orchestration", "airflow", "data-pipeline", "dag", "dependency-graph", "topological-sort"],
     "signal_kw": ["orquestación", "scheduling", "dag", "trigger", "dependencias", "ejecución",
                   "grafo", "topológico", "networkx", "parallel", "foreach"]},

    # ── cloud-azure ───────────────────────────────────────────────────────
    {"id": "fabric-platform", "domain_id": "cloud-azure", "label": "Microsoft Fabric",
     "signal_tags": ["microsoft-fabric", "trident-notebook", "fabric-pipeline", "fabric-lakehouse",
                     "fabric-warehouse", "onelake", "fabric-dataflow", "fabric-eventstream"],
     "signal_kw": ["fabric", "onelake", "lakehouse fabric", "workspace fabric", "capacidad fabric",
                   "tridentnotebook", "fabric pipeline", "fabric warehouse"]},
    {"id": "adf-pipelines",   "domain_id": "cloud-azure", "label": "Azure Data Factory / Pipelines",
     "signal_tags": ["azure-data-factory", "orchestration", "data-pipeline"],
     "signal_kw": ["data factory", "adf", "pipeline adf", "linked service", "dataset", "actividad"]},
    {"id": "synapse-analytics","domain_id": "cloud-azure", "label": "Azure Synapse Analytics",
     "signal_tags": ["azure-synapse", "spark-sql", "sql"],
     "signal_kw": ["synapse", "synapse sql", "dedicated pool", "serverless pool", "spark pool"]},
    {"id": "powerbi-reporting","domain_id": "cloud-azure", "label": "Power BI",
     "signal_tags": ["power-bi"],
     "signal_kw": ["power bi", "report", "pbix", "dataset pbi", "direct query", "dataflow"]},
    {"id": "azure-storage",   "domain_id": "cloud-azure", "label": "Azure Storage",
     "signal_tags": ["azure-blob-storage", "azure-data-lake", "azure-sql"],
     "signal_kw": ["blob", "adls", "storage account", "container azure", "gen2"]},

    # ── cloud-aws ─────────────────────────────────────────────────────────
    {"id": "aws-analytics",   "domain_id": "cloud-aws", "label": "AWS Analytics",
     "signal_tags": ["aws-glue", "athena", "redshift"],
     "signal_kw": ["glue", "athena", "redshift", "data catalog aws", "crawler"]},
    {"id": "aws-ml-platform", "domain_id": "cloud-aws", "label": "AWS ML & AI",
     "signal_tags": ["sagemaker"],
     "signal_kw": ["sagemaker", "endpoint aws", "entrenamiento aws", "inferencia aws"]},
    {"id": "aws-storage",     "domain_id": "cloud-aws", "label": "AWS Storage",
     "signal_tags": ["s3"],
     "signal_kw": ["s3", "bucket", "object storage", "prefijo s3", "lifecycle"]},
    {"id": "aws-compute",     "domain_id": "cloud-aws", "label": "AWS Compute",
     "signal_tags": ["ec2", "aws-lambda", "ecs", "eks"],
     "signal_kw": ["lambda aws", "ec2", "fargate", "ecs", "eks", "función serverless"]},

    # ── testing ───────────────────────────────────────────────────────────
    {"id": "test-planning",   "domain_id": "testing", "label": "Test Planning",
     "signal_tags": ["test-plan", "test-management", "test-case", "xray"],
     "signal_kw": ["plan de pruebas", "caso de prueba", "cobertura", "estrategia de pruebas"]},
    {"id": "test-automation", "domain_id": "testing", "label": "Test Automation",
     "signal_tags": ["test-automation", "selenium", "cypress", "bdd", "tdd"],
     "signal_kw": ["automatización", "framework de pruebas", "robot", "script de pruebas"]},
    {"id": "test-types",      "domain_id": "testing", "label": "Test Types",
     "signal_tags": ["unit-testing", "integration-testing", "e2e-testing", "regression-testing"],
     "signal_kw": ["unitaria", "integración", "e2e", "regresión", "smoke", "sanity"]},
    {"id": "test-reporting",  "domain_id": "testing", "label": "Test Reporting & Results",
     "signal_tags": ["xray", "test-management"],
     "signal_kw": ["resultado de prueba", "ejecución", "defecto", "bug", "tasa de fallo", "evidencia"]},

    # ── devops-infra ──────────────────────────────────────────────────────
    {"id": "containers",      "domain_id": "devops-infra", "label": "Containers & Orchestration",
     "signal_tags": ["docker", "kubernetes", "helm"],
     "signal_kw": ["contenedor", "container", "pod", "cluster", "docker compose", "namespace k8s"]},
    {"id": "ci-cd-pipeline",  "domain_id": "devops-infra", "label": "CI/CD",
     "signal_tags": ["ci-cd", "github-actions", "gitlab-ci", "jenkins", "azure-devops"],
     "signal_kw": ["ci/cd", "pipeline ci", "deploy", "release", "build", "artefacto", "stage"]},
    {"id": "iac",             "domain_id": "devops-infra", "label": "Infrastructure as Code",
     "signal_tags": ["terraform", "ansible"],
     "signal_kw": ["terraform", "ansible", "infraestructura como código", "iac", "bicep", "arm template"]},
    {"id": "observability",   "domain_id": "devops-infra", "label": "Observability",
     "signal_tags": ["monitoring", "logging", "observability", "prometheus", "grafana"],
     "signal_kw": ["monitorización", "logs", "alertas", "métricas", "dashboard", "tracing", "slo"]},

    # ── development ───────────────────────────────────────────────────────
    {"id": "api-dev",         "domain_id": "development", "label": "API Development",
     "signal_tags": ["rest-api", "fastapi", "flask", "graphql", "pydantic"],
     "signal_kw": ["endpoint", "swagger", "openapi", "api rest", "ruta", "handler", "middleware"]},
    {"id": "backend-dev",     "domain_id": "development", "label": "Backend Development",
     "signal_tags": ["python", "django", "nodejs", "asyncio"],
     "signal_kw": ["backend", "servidor", "servicio", "worker", "daemon", "proceso"]},
    {"id": "frontend-dev",    "domain_id": "development", "label": "Frontend Development",
     "signal_tags": ["react", "angular", "vue", "javascript", "typescript", "html", "css"],
     "signal_kw": ["frontend", "componente ui", "interfaz", "spa", "routing", "estado"]},
    {"id": "testing-dev",     "domain_id": "development", "label": "Testing & Quality",
     "signal_tags": ["pytest", "tdd", "unit-testing"],
     "signal_kw": ["test unitario", "mock", "fixture", "cobertura", "pytest"]},

    # ── architecture ──────────────────────────────────────────────────────
    {"id": "data-architecture","domain_id": "architecture", "label": "Data Architecture",
     "signal_tags": ["data-model", "medallion", "data-lake", "data-warehouse", "schema"],
     "signal_kw": ["arquitectura de datos", "modelo conceptual", "entidad", "relación", "normalización"]},
    {"id": "data-modeling",   "domain_id": "architecture", "label": "Data Modeling",
     "signal_tags": ["data-model", "schema"],
     "signal_kw": ["modelo de datos", "entidad relación", "star schema", "dimensión", "hecho"]},
    {"id": "solution-design", "domain_id": "architecture", "label": "Solution Design",
     "signal_tags": ["solution-architecture", "diagram"],
     "signal_kw": ["arquitectura de solución", "diagrama", "diseño técnico", "decisión técnica"]},
    {"id": "patterns",        "domain_id": "architecture", "label": "Patterns",
     "signal_tags": ["design-pattern", "event-driven", "domain-driven-design", "microservices"],
     "signal_kw": ["patrón", "event-driven", "ddd", "cqrs", "saga", "hexagonal"]},

    # ── data-science-ml ───────────────────────────────────────────────────
    {"id": "llm-rag",         "domain_id": "data-science-ml", "label": "LLM & RAG",
     "signal_tags": ["llm", "rag", "embeddings", "vector-database", "prompt-engineering", "langchain"],
     "signal_kw": ["llm", "rag", "embedding", "vector store", "prompt", "retrieval", "generación"]},
    {"id": "ml-modeling",     "domain_id": "data-science-ml", "label": "ML Modeling",
     "signal_tags": ["machine-learning", "deep-learning", "nlp", "sagemaker"],
     "signal_kw": ["entrenamiento", "modelo ml", "hiperparámetro", "overfitting", "evaluación modelo"]},
    {"id": "feature-eng",     "domain_id": "data-science-ml", "label": "Feature Engineering",
     "signal_tags": ["pandas", "numpy", "dataframe"],
     "signal_kw": ["feature", "variable", "preprocesamiento", "normalización", "encoding", "imputación"]},

    # ── databases ─────────────────────────────────────────────────────────
    {"id": "relational-db",   "domain_id": "databases", "label": "Relational Databases",
     "signal_tags": ["sql", "postgresql", "mysql", "sqlserver"],
     "signal_kw": ["tabla", "índice", "foreign key", "join", "transacción", "stored procedure"]},
    {"id": "nosql-db",        "domain_id": "databases", "label": "NoSQL Databases",
     "signal_tags": ["mongodb", "redis", "dynamodb"],
     "signal_kw": ["colección", "documento", "clave-valor", "cache", "ttl", "aggregation mongo"]},
    {"id": "query-tuning",    "domain_id": "databases", "label": "Query Optimization",
     "signal_tags": ["sql", "performance", "schema"],
     "signal_kw": ["optimización query", "plan de ejecución", "explain", "índice", "partición"]},

    # ── project-management ────────────────────────────────────────────────
    {"id": "agile-scrum",     "domain_id": "project-management", "label": "Agile & Scrum",
     "signal_tags": ["jira"],
     "signal_kw": ["sprint", "backlog", "kanban", "scrum", "epic", "story", "velocity", "retro"]},
    {"id": "documentation-mgmt","domain_id": "project-management", "label": "Documentation Management",
     "signal_tags": ["confluence", "documentation"],
     "signal_kw": ["espacio confluence", "página", "wiki", "documentación técnica", "acta"]},

    # ── business-intelligence ─────────────────────────────────────────────
    {"id": "reporting",       "domain_id": "business-intelligence", "label": "Reporting & Dashboards",
     "signal_tags": ["power-bi", "kpi"],
     "signal_kw": ["informe", "dashboard", "kpi", "página pbi", "visual", "filtro", "slicer"]},
    {"id": "data-analysis",   "domain_id": "business-intelligence", "label": "Data Analysis",
     "signal_tags": ["spreadsheet", "pandas", "estimation"],
     "signal_kw": ["análisis", "pivot", "tabla dinámica", "segmentación", "tendencia", "excel", "estimación"]},

    # ── generative-ai ─────────────────────────────────────────────────────
    {"id": "llm-rag",           "domain_id": "generative-ai", "label": "LLM & RAG",
     "signal_tags": ["llm", "rag", "embeddings", "vector-database", "fine-tuning"],
     "signal_kw": ["llm", "rag", "retrieval augmented", "embeddings", "vector store",
                   "fine-tuning", "ajuste fino", "modelo de lenguaje"]},
    {"id": "ai-agents-arch",    "domain_id": "generative-ai", "label": "AI Agents Architecture",
     "signal_tags": ["ai-agent", "multi-agent", "tool-calling", "agent-memory", "agent-planning",
                     "semantic-kernel", "autogen", "langgraph", "mcp"],
     "signal_kw": ["agente ia", "ai agent", "tool calling", "function calling", "multi-agent",
                   "semantic kernel", "autogen", "langgraph", "mcp", "agentic"]},
    {"id": "prompt-design",     "domain_id": "generative-ai", "label": "Prompt Engineering",
     "signal_tags": ["prompt-engineering", "chain-of-thought", "few-shot", "zero-shot",
                     "system-prompt", "context-window"],
     "signal_kw": ["prompt", "instrucción", "system prompt", "chain of thought", "few-shot",
                   "in-context learning", "token limit", "ventana de contexto"]},
    {"id": "copilot-dev",       "domain_id": "generative-ai", "label": "Copilot & IDEs",
     "signal_tags": ["github-copilot", "copilot-studio", "mcp"],
     "signal_kw": ["github copilot", "copilot chat", "copilot studio", "agent mode",
                   "vs code copilot", "custom instructions", "mcp server", "model context protocol"]},
    {"id": "agent-evaluation",  "domain_id": "generative-ai", "label": "Agent Evaluation",
     "signal_tags": ["agent-eval", "rag", "benchmark"],
     "signal_kw": ["ragas", "evaluación llm", "benchmark", "hallucination", "faithfulness",
                   "relevance", "groundedness", "eval"]},

    # ── procurement ───────────────────────────────────────────────────────
    {"id": "rfp-rfi-process", "domain_id": "procurement", "label": "RFP / RFI Process",
     "signal_tags": ["rfp", "rfi", "procurement", "tender"],
     "signal_kw": ["solicitud de propuesta", "solicitud de información", "licitación", "pliego", "concurso"]},
    {"id": "vct-compliance",  "domain_id": "procurement", "label": "VCT / Technical Compliance",
     "signal_tags": ["vct", "specification", "requirements", "technical-offer"],
     "signal_kw": ["verificación cumplimiento", "cumplimiento técnico", "vct", "criterio técnico", "requisito"]},
    {"id": "contracting",     "domain_id": "procurement", "label": "Contracting",
     "signal_tags": ["contract", "sow", "sla"],
     "signal_kw": ["contrato", "cláusula", "alcance", "entregable", "penalización", "adjudicación"]},

    # ── asset-operations ──────────────────────────────────────────────────
    {"id": "predictive-maint","domain_id": "asset-operations", "label": "Predictive Maintenance",
     "signal_tags": ["predictive-maintenance", "anomaly-detection", "condition-monitoring", "sensor-data"],
     "signal_kw": ["mantenimiento predictivo", "predictor", "anomalía", "degradación", "fallo", "vibración"]},
    {"id": "asset-health-idx","domain_id": "asset-operations", "label": "Asset Health & KPIs",
     "signal_tags": ["asset-health", "asset-management", "kpi"],
     "signal_kw": ["índice de salud", "health index", "fiabilidad", "disponibilidad", "mttr", "mtbf"]},
    {"id": "scada-historian",  "domain_id": "asset-operations", "label": "SCADA / Historian",
     "signal_tags": ["scada", "historian", "operational-technology", "iiot"],
     "signal_kw": ["scada", "historian", "pi system", "osisoftpi", "tag sensor", "telemetría"]},
    {"id": "om-operations",   "domain_id": "asset-operations", "label": "O&M Operations",
     "signal_tags": ["operational-technology", "asset-management"],
     "signal_kw": ["operación y mantenimiento", "o&m", "planta", "instalación", "activo crítico", "pozo"]},
]

_SEED_DOC_TYPES = [
    {"id": "notebook",         "label": "Notebook",
     "signal_tags": ["notebook", "jupyter", "pyspark"],
     "signal_kw": ["notebook", "celda", "spark context", "display", "dbutils"],
     "signal_formats": ["ipynb"]},
    {"id": "pipeline-config",  "label": "Pipeline Configuration",
     "signal_tags": ["data-pipeline", "orchestration", "azure-data-factory"],
     "signal_kw": ["pipeline", "actividades", "activities", "linked service", "trigger", "dataset"],
     "signal_formats": ["json"]},
    {"id": "script",           "label": "Script / Module",
     "signal_tags": ["python", "bash"],
     "signal_kw": ["script", "módulo", "cli", "utility", "import", "def ", "class "],
     "signal_formats": ["py", "sh"]},
    {"id": "sql-query",        "label": "SQL Query / Procedure",
     "signal_tags": ["sql", "spark-sql"],
     "signal_kw": ["select", "from", "where", "join", "procedure", "vista", "view", "cte", "with "],
     "signal_formats": ["sql"]},
    {"id": "test-plan",        "label": "Test Plan",
     "signal_tags": ["test-plan", "test-case", "test-management", "xray"],
     "signal_kw": ["plan de pruebas", "caso de prueba", "test plan", "cobertura", "scope"],
     "signal_formats": []},
    {"id": "test-results",     "label": "Test Results / Execution",
     "signal_tags": ["xray", "testing", "test-management"],
     "signal_kw": ["resultado", "ejecución", "passed", "failed", "evidencia", "defecto", "bug report"],
     "signal_formats": []},
    {"id": "runbook",          "label": "Runbook / Procedure",
     "signal_tags": ["runbook", "troubleshooting"],
     "signal_kw": ["runbook", "procedimiento", "paso a paso", "incidencia", "operación", "instrucción"],
     "signal_formats": []},
    {"id": "manual",           "label": "Manual / Guide",
     "signal_tags": ["manual", "tutorial"],
     "signal_kw": ["guía", "manual", "cómo", "how to", "instrucciones", "tutorial"],
     "signal_formats": []},
    {"id": "architecture-doc", "label": "Architecture Document",
     "signal_tags": ["architecture", "diagram", "solution-architecture"],
     "signal_kw": ["arquitectura", "diagrama", "diseño", "componentes", "flujo de datos", "topología"],
     "signal_formats": ["drawio"]},
    {"id": "adr",              "label": "Architecture Decision Record",
     "signal_tags": ["architecture", "design-pattern"],
     "signal_kw": ["decisión", "decision", "adr", "alternativas", "justificación", "trade-off"],
     "signal_formats": []},
    {"id": "technical-spec",   "label": "Technical Specification",
     "signal_tags": ["specification", "requirements"],
     "signal_kw": ["especificación", "requisito", "requirement", "spec", "funcional", "no funcional"],
     "signal_formats": []},
    {"id": "api-spec",         "label": "API Specification",
     "signal_tags": ["rest-api", "graphql", "api-design"],
     "signal_kw": ["endpoint", "swagger", "openapi", "api", "método http", "request", "response"],
     "signal_formats": []},
    {"id": "report",           "label": "Report / Analysis",
     "signal_tags": ["power-bi"],
     "signal_kw": ["informe", "report", "análisis", "dashboard", "kpi", "métrica", "indicador"],
     "signal_formats": []},
    {"id": "config-file",      "label": "Configuration File",
     "signal_tags": ["configuration"],
     "signal_kw": ["configuración", "config", "settings", "env", "parámetro", "variable de entorno"],
     "signal_formats": ["json", "xml", "yaml", "yml", "toml", "ini"]},
    {"id": "presentation",     "label": "Presentation",
     "signal_tags": ["presentation"],
     "signal_kw": ["presentación", "slides", "diapositivas", "agenda", "objetivo"],
     "signal_formats": ["pptx"]},
    {"id": "spreadsheet",      "label": "Spreadsheet / Data File",
     "signal_tags": ["spreadsheet"],
     "signal_kw": ["hoja de cálculo", "datos", "excel", "columnas", "filas", "tabla de datos"],
     "signal_formats": ["xlsx", "csv", "tsv"]},
    {"id": "reference-doc",    "label": "Reference Documentation",
     "signal_tags": ["reference", "documentation", "best-practices"],
     "signal_kw": ["referencia", "documentación", "buenas prácticas", "estándar", "convención"],
     "signal_formats": []},
    {"id": "web-article",      "label": "Web Article / Online Doc",
     "signal_tags": ["web", "html"],
     "signal_kw": ["artículo", "documentación online", "blog", "post", "publicación"],
     "signal_formats": ["web", "html", "htm"]},
    {"id": "meeting-notes",    "label": "Meeting Notes / Acta",
     "signal_tags": ["documentation"],
     "signal_kw": ["acta", "reunión", "meeting", "asistentes", "acuerdos", "puntos tratados", "follow-up"],
     "signal_formats": []},
    {"id": "data-contract",    "label": "Data Contract",
     "signal_tags": ["data-governance", "schema", "data-quality"],
     "signal_kw": ["contrato de datos", "data contract", "propietario", "sla datos", "acuerdo de datos"],
     "signal_formats": []},
    {"id": "data-catalog",     "label": "Data Catalog / Glossary",
     "signal_tags": ["metadata", "data-governance", "schema"],
     "signal_kw": ["catálogo", "glosario", "definición", "término de negocio", "dominio de datos"],
     "signal_formats": []},
    {"id": "migration-doc",    "label": "Migration Document",
     "signal_tags": ["migration"],
     "signal_kw": ["migración", "migration", "rollback", "cutover", "plan de migración", "as-is", "to-be"],
     "signal_formats": []},
    {"id": "onboarding",       "label": "Onboarding / Getting Started",
     "signal_tags": ["documentation", "tutorial"],
     "signal_kw": ["onboarding", "inicio rápido", "primeros pasos", "configuración inicial", "setup"],
     "signal_formats": []},
    {"id": "training-material", "label": "Training Material",
     "signal_tags": ["tutorial", "documentation", "course", "lesson"],
     "signal_kw": ["formación", "training", "curso", "módulo formativo", "ejercicio", "laboratorio",
                   "teoría", "práctica", "aprendizaje"],
     "signal_formats": []},
    {"id": "rfp-doc",          "label": "RFP - Request for Proposal",
     "signal_tags": ["rfp", "procurement", "requirements", "tender"],
     "signal_kw": ["solicitud de propuesta", "request for proposal", "rfp", "pliego de condiciones",
                   "requisitos técnicos", "alcance", "criterios de evaluación", "licitación"],
     "signal_formats": ["docx", "pdf", "doc"]},
    {"id": "rfi-doc",          "label": "RFI - Request for Information",
     "signal_tags": ["rfi", "procurement"],
     "signal_kw": ["solicitud de información", "request for information", "rfi",
                   "información técnica", "capacidades", "proveedores"],
     "signal_formats": ["docx", "pdf", "doc"]},
    {"id": "vct-doc",          "label": "VCT - Verificación Cumplimiento Técnico",
     "signal_tags": ["vct", "technical-offer", "specification", "requirements"],
     "signal_kw": ["verificación cumplimiento técnico", "vct", "cumplimiento técnico",
                   "verificación", "criterio técnico", "requisito técnico", "informe vct",
                   "certificación técnica"],
     "signal_formats": ["docx", "pdf", "doc"]},
    {"id": "technical-offer",  "label": "Technical Offer / Proposal",
     "signal_tags": ["technical-offer", "rfp", "sow"],
     "signal_kw": ["oferta técnica", "propuesta técnica", "solución propuesta", "alcance técnico",
                   "plan de trabajo", "entregables", "metodología"],
     "signal_formats": ["docx", "pdf", "pptx"]},
    {"id": "estimation-doc",   "label": "Estimation / Sizing",
     "signal_tags": ["estimation", "kpi", "spreadsheet"],
     "signal_kw": ["estimación", "sizing", "esfuerzo", "jornadas", "horas", "coste",
                   "presupuesto", "planificación", "plan de proyecto"],
     "signal_formats": ["xlsx", "csv"]},
    {"id": "asset-report",     "label": "Asset Health Report",
     "signal_tags": ["asset-health", "asset-management", "predictive-maintenance"],
     "signal_kw": ["índice de salud", "estado del activo", "informe de activo",
                   "mantenimiento", "pozo", "disponibilidad", "fiabilidad"],
     "signal_formats": ["docx", "pdf", "pptx"]},
    {"id": "course-module",    "label": "Course Module",
     "signal_tags": ["course", "lesson", "training-material"],
     "signal_kw": ["módulo", "teoría", "práctica", "objetivo de aprendizaje",
                   "arquitectura de datos", "fundamentos", "ejercicio práctico"],
     "signal_formats": ["md", "ipynb", "pdf", "pptx"]},
    {"id": "agent-definition", "label": "AI Agent Definition",
     "signal_tags": ["ai-agent", "system-prompt", "tool-calling", "mcp"],
     "signal_kw": ["agente", "agent", "instrucciones agente", "system prompt",
                   "herramientas agente", "mcp server", "copilot agent",
                   "custom instructions", "agent mode", ".agent.md"],
     "signal_formats": ["md", "yaml", "json"]},
    {"id": "prompt-template",  "label": "Prompt Template",
     "signal_tags": ["prompt-engineering", "system-prompt", "llm"],
     "signal_kw": ["plantilla prompt", "prompt template", "instrucciones sistema",
                   "prompt.md", ".prompt.md", "few-shot", "system message"],
     "signal_formats": ["md", "txt", "yaml"]},
    {"id": "bi-report",        "label": "Power BI Report / Dashboard",
     "signal_tags": ["power-bi", "dax", "semantic-model", "kpi"],
     "signal_kw": ["power bi", "dashboard", "informe pbi", "pbix", "medida dax",
                   "modelo semántico", "dataset pbi", "página de informe"],
     "signal_formats": ["pbix", "pbit", "json", "tmdl"]},
    {"id": "security-doc",     "label": "Security Design / Policy",
     "signal_tags": ["owasp", "zero-trust", "iam", "devsecops", "compliance"],
     "signal_kw": ["seguridad", "threat model", "política de seguridad",
                   "zero trust", "iam", "owasp", "control de acceso",
                   "cifrado", "pentest", "vulnerability"],
     "signal_formats": ["md", "docx", "pdf"]},
]

# ---------------------------------------------------------------------------
# Entity hints: patrones por domain/doc_type para guiar la extracción de entidades.
# El LLM (y el extractor programático) pueden usar estos hints como contexto.
# domain_id / doc_type_id = None → aplica globalmente.
# ---------------------------------------------------------------------------

_SEED_ENTITY_HINTS = [
    # ── Notebooks PySpark (data-engineering) ─────────────────────────────
    {
        "id": "ipynb-de-entities",
        "domain_id": "data-engineering", "doc_type_id": "notebook",
        "label": "PySpark Notebook entities",
        "patterns": [
            r"\bdf_\w+",                         # dataframes: df_silver, df_cleaned
            r"\b(spark\.read|spark\.write)\b",   # operaciones spark
            r"\b\w+_table\b",                    # nombres de tabla: fact_orders_table
            r"\b(display|dbutils)\b",            # fabric/databricks specifics
            r"def\s+(\w+)\s*\(",                 # funciones definidas
        ],
        "examples": [
            "df_bronze", "df_silver_clean", "fact_orders_table", "dim_customer",
            "load_delta_table", "write_to_gold", "spark.read.format('delta')",
        ],
    },
    # ── Pipelines ADF/Fabric (data-engineering) ───────────────────────────
    {
        "id": "json-pipeline-entities",
        "domain_id": "data-engineering", "doc_type_id": "pipeline-config",
        "label": "ADF/Fabric Pipeline entities",
        "patterns": [
            r'"name"\s*:\s*"([^"]+)"',           # nombre de actividad/pipeline
            r'"type"\s*:\s*"([A-Z][A-Za-z]+)"',  # tipo de actividad: CopyData, Notebook
            r'"linkedServiceName"\s*:\s*\{[^}]*"referenceName"\s*:\s*"([^"]+)"',
        ],
        "examples": [
            "CopyFromSQL", "RunNotebook", "ExecuteDataflow", "LinkedService_ADLS",
            "Dataset_Bronze", "Pipeline_Main", "Trigger_Daily",
        ],
    },
    # ── SQL Scripts (databases / data-engineering) ────────────────────────
    {
        "id": "sql-entities",
        "domain_id": None, "doc_type_id": "sql-query",
        "label": "SQL tables, views, procedures",
        "patterns": [
            r"\bFROM\s+(\w+\.?\w*)\b",           # tablas en FROM
            r"\bJOIN\s+(\w+\.?\w*)\b",            # tablas en JOIN
            r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:VIEW|TABLE|PROCEDURE)\s+(\w+\.?\w*)\b",
            r"\bINSERT\s+INTO\s+(\w+\.?\w*)\b",
            r"\bWITH\s+(\w+)\s+AS\b",            # CTEs
        ],
        "examples": [
            "fact_sales", "dim_date", "vw_daily_summary", "sp_load_bronze",
            "stg_orders", "bronze.raw_events",
        ],
    },
    # ── Test Plans / Cases (testing) ──────────────────────────────────────
    {
        "id": "test-entities",
        "domain_id": "testing", "doc_type_id": None,
        "label": "Test cases, suites, steps",
        "patterns": [
            r"\b(?:TC|TEST|CASO|CP)[_\-]?\d+\b",  # IDs de caso de prueba
            r"\b(?:TS|SUITE)[_\-]?\d+\b",          # test suites
            r"\bEpic\s*:\s*(.+)",
            r"\bFeature\s*:\s*(.+)",
        ],
        "examples": [
            "TC-001", "TC-042", "TS-SMOKE", "Epic: Gestión de usuarios",
            "Feature: Carga de datos Bronze",
        ],
    },
    # ── Architecture Diagrams (architecture) ──────────────────────────────
    {
        "id": "arch-diagram-entities",
        "domain_id": "architecture", "doc_type_id": "architecture-doc",
        "label": "Architecture components",
        "patterns": [
            r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b",  # CamelCase: DataLake, EventHub
            r"\b\w+\s+(?:Service|Layer|Component|Module|Gateway|Bus)\b",
        ],
        "examples": [
            "DataLake", "EventHub", "APIGateway", "Bronze Layer", "Ingestion Service",
            "Orchestration Component",
        ],
    },
    # ── Python Scripts (development) ──────────────────────────────────────
    {
        "id": "python-script-entities",
        "domain_id": "development", "doc_type_id": "script",
        "label": "Python functions, classes, modules",
        "patterns": [
            r"^def\s+(\w+)\s*\(",
            r"^class\s+(\w+)\s*[\(:]",
            r"^from\s+(\S+)\s+import",
            r"^import\s+(\S+)",
        ],
        "examples": [
            "ingest_data", "transform_records", "DataProcessor", "RAGPipeline",
            "load_config", "from brain.vocabulary import",
        ],
    },
    # ── AI Agent definitions (.agent.md / .prompt.md) ────────────────────
    {
        "id": "agent-def-entities",
        "domain_id": "generative-ai", "doc_type_id": "agent-definition",
        "label": "AI Agent: name, tools, model, instructions sections",
        "patterns": [
            r"^#+\s+(?:ROL|OBJETIVO|DOMINAS|PRINCIPIOS|HERRAMIENTAS|STACK)\b",
            r"\b(?:model|tools|instructions|description)\s*:\s*.+",
            r"\bname\s*:\s*([^\n]+)",
            r"\b(?:Eres un|Eres una)\s+(.{5,60})",  # "Eres un experto en..."
        ],
        "examples": [
            "agent_data", "agent_BI", "agent_ciber", "agent_test",
            "agent_data_architecture", "ROL", "OBJETIVO", "DOMINAS",
            "model: gpt-4o", "tools: [read_file, run_terminal]",
        ],
    },
    # ── Prompt templates (.prompt.md) ────────────────────────────────────
    {
        "id": "prompt-template-entities",
        "domain_id": "generative-ai", "doc_type_id": "prompt-template",
        "label": "Prompt template variables and sections",
        "patterns": [
            r"\{\{(\w+)\}\}",            # {{variable}}
            r"\{(\w+)\}",               # {variable}
            r"^#+\s+(?:System|User|Assistant|Instruction|Context|Examples?)",
            r"\b(?:few-shot|chain-of-thought|zero-shot|CoT)\b",
        ],
        "examples": [
            "{{context}}", "{{question}}", "System:", "User:",
            "few-shot example", "chain-of-thought", "CoT",
        ],
    },
    # ── Power BI / Semantic model ─────────────────────────────────────────
    {
        "id": "pbi-semantic-entities",
        "domain_id": "business-intelligence", "doc_type_id": "bi-report",
        "label": "Power BI: measures, tables, relationships, KPIs",
        "patterns": [
            r"\bMEASURE\s+\[([^\]]+)\]",                # DAX MEASURE [name]
            r"\bCALCULATE\s*\(",                         # DAX CALCULATE
            r"\bRELATED\s*\(",                           # DAX RELATED
            r"\btable\s+\w+\b|\bcolumn\s+\w+\b",        # TMDL table/column
            r"\bSource\s*=\s*.+",                        # Power Query Source
            r"\bKPI\s*[\:\-]\s*\w+",
        ],
        "examples": [
            "MEASURE [Total Ventas]", "CALCULATE(SUM", "RELATED(",
            "table FactVentas", "Source = Lakehouse.Tables",
            "KPI: Disponibilidad",
        ],
    },
    # ── Security documents ────────────────────────────────────────────────
    {
        "id": "security-doc-entities",
        "domain_id": None, "doc_type_id": "security-doc",
        "label": "Security artifacts: vulnerabilities, controls, threats",
        "patterns": [
            r"\bOWASP\s+[A-Z]\d+",                       # OWASP A01
            r"\bCVE-\d{4}-\d+\b",                        # CVE-2024-12345
            r"\b(?:threat|control|mitigación|riesgo)\s*:\s*.+",
            r"\b(?:IAM|RBAC|ABAC|MFA|SSO|SAML|OIDC)\b",
            r"\b(?:Zero Trust|Defense in Depth|Least Privilege)\b",
        ],
        "examples": [
            "OWASP A01:2021", "CVE-2024-1234", "Threat: SQL Injection",
            "IAM Policy", "RBAC Role", "Zero Trust", "MFA",
        ],
    },
    # ── Confluence / Documentation (project-management) ───────────────────
    {
        "id": "docs-entities",
        "domain_id": "project-management", "doc_type_id": None,
        "label": "Project artifacts and roles",
        "patterns": [
            r"\b(?:PROJ|EPIC|STORY|BUG)[_\-]?\d+\b",  # IDs Jira
            r"\b[A-Z]{2,}-\d+\b",                       # formato Jira key: AMCH-123
        ],
        "examples": [
            "AMCH-123", "EPIC-42", "Sprint 5", "Release 2.0",
        ],
    },
    # ── Fabric Pipeline JSON (data-engineering) ───────────────────────────
    {
        "id": "fabric-pipeline-entities",
        "domain_id": "data-engineering", "doc_type_id": "pipeline-config",
        "label": "Microsoft Fabric Pipeline entities (TridentNotebook / ForEach / SetVariable)",
        "patterns": [
            r'"name"\s*:\s*"([^"]+)"',                   # nombre de actividad
            r'"type"\s*:\s*"(TridentNotebook|ForEach|IfCondition|SetVariable|'
            r'Office365Outlook|ExecutePipeline|Wait|Copy)[^"]*"',
            r'"workspaceId"\s*:\s*"([0-9a-f\-]{36})"',   # workspace UUID
            r'"notebookId"\s*:\s*"([0-9a-f\-]{36})"',    # notebook UUID
        ],
        "examples": [
            "TridentNotebook", "ForEach", "SetVariable", "IfCondition",
            "com_nb_tte_get_github_files_control", "sv_sessionTag",
            "Office365Outlook", "ExecutePipeline",
        ],
    },
    # ── RFP / RFI (procurement) ───────────────────────────────────────────
    {
        "id": "rfp-rfi-entities",
        "domain_id": "procurement", "doc_type_id": None,
        "label": "RFP / RFI entities: requirements, vendors, evaluation criteria",
        "patterns": [
            r"\b(?:REQ|RF)[_\-]?\d+\b",                  # IDs de requisito
            r"\b(?:Requisito|Criterio)\s+\d+[\.\:]",      # "Requisito 1:"
            r"\b(?:Proveedor|Ofertante|Licitador)\s*:\s*(.+)",
            r"^\d+\.\d+\s+.{5,60}$",                      # secciones numeradas
        ],
        "examples": [
            "REQ-001", "Requisito 3.2", "Criterio Técnico 1",
            "Fase 1 - Análisis", "Entregable D1",
        ],
    },
    # ── VCT (procurement compliance) ──────────────────────────────────────
    {
        "id": "vct-entities",
        "domain_id": "procurement", "doc_type_id": "vct-doc",
        "label": "VCT: compliance items, versions, technical criteria",
        "patterns": [
            r"\bVCT[_\-]?\w*\b",                          # VCT references
            r"\b[Vv]ersión\s+[\d\.]+\b",                  # Version X.X
            r"\b(?:Conforme|No conforme|N/A)\b",           # compliance status
            r"\b(?:criterio|artículo|apartado)\s+\d+",    # criteria references
        ],
        "examples": [
            "VCT-001", "Versión 1.2", "Conforme", "No conforme",
            "Criterio 3.1", "Artículo 5",
        ],
    },
    # ── Asset Health / OT (asset-operations) ─────────────────────────────
    {
        "id": "asset-health-entities",
        "domain_id": "asset-operations", "doc_type_id": None,
        "label": "Asset entities: equipment IDs, sensors, KPIs",
        "patterns": [
            r"\b[A-Z]{2,4}-\d{3,6}\b",                   # equipment IDs: PZ-00123
            r"\b(?:TAG|KPI|IHS|ISA)\s*[\:\-]\s*\w+",     # TAG/KPI references
            r"\b(?:pozo|compresor|válvula|turbina|bomba)\s+\w+",
            r"\b\w+_sensor\b|\b\w+_tag\b",                # sensor/tag names
        ],
        "examples": [
            "PZ-00123", "TAG: FLOW_001", "KPI: Disponibilidad",
            "IHS: Pozo Norte", "compresor K-101",
        ],
    },
    # ── Drawio Architecture (global) ──────────────────────────────────────
    {
        "id": "arch-diagram-entities",
        "domain_id": "architecture", "doc_type_id": "architecture-doc",
        "label": "Architecture components",
        "patterns": [
            r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b",           # CamelCase: DataLake
            r"\b\w+\s+(?:Service|Layer|Component|Module|Gateway|Bus|Pipeline|Lakehouse|Warehouse)\b",
        ],
        "examples": [
            "DataLake", "EventHub", "APIGateway", "Bronze Layer",
            "Ingestion Service", "Fabric Lakehouse",
        ],
    },
    # ── Course Modules (data-science-ml / architecture) ───────────────────
    {
        "id": "course-module-entities",
        "domain_id": None, "doc_type_id": "course-module",
        "label": "Course module topics and concepts",
        "patterns": [
            r"^#{1,3}\s+(?:Módulo|Módulo\s+\d+|Capítulo|Tema)\s+.+$",
            r"\b(?:objetivo|competencia|resultado de aprendizaje)\s*:\s*.+",
        ],
        "examples": [
            "Módulo 01 - Fundamentos", "Teoría: Arquitectura de datos",
            "Práctica: PySpark en Fabric",
        ],
    },
]



# ---------------------------------------------------------------------------
# Inicialización SQLite
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_conn()
    conn.executescript(_SCHEMA)
    conn.close()


def _seed_if_empty():
    """
    UPSERT de todos los seeds: INSERT OR REPLACE garantiza que los cambios en
    el código se propaguen a la DB existente sin borrarla manualmente.
    """
    conn = _get_conn()
    log.info("Aplicando seed (UPSERT) en masters.db...")
    for d in _SEED_DOMAINS:
        conn.execute(
            "INSERT OR REPLACE INTO domains (id, label, description, signal_tags, signal_kw) VALUES (?,?,?,?,?)",
            (d["id"], d["label"], d.get("description", ""),
             json.dumps(d["signal_tags"]), json.dumps(d.get("signal_kw", []))),
        )
    for s in _SEED_SUBDOMAINS:
        conn.execute(
            "INSERT OR REPLACE INTO subdomains (id, domain_id, label, signal_tags, signal_kw) VALUES (?,?,?,?,?)",
            (s["id"], s["domain_id"], s["label"],
             json.dumps(s["signal_tags"]), json.dumps(s.get("signal_kw", []))),
        )
    for dt in _SEED_DOC_TYPES:
        conn.execute(
            "INSERT OR REPLACE INTO doc_types (id, label, signal_tags, signal_kw, signal_formats) VALUES (?,?,?,?,?)",
            (dt["id"], dt["label"],
             json.dumps(dt["signal_tags"]), json.dumps(dt.get("signal_kw", [])),
             json.dumps(dt.get("signal_formats", []))),
        )
    for eh in _SEED_ENTITY_HINTS:
        conn.execute(
            "INSERT OR REPLACE INTO entity_hints "
            "(id, domain_id, doc_type_id, label, patterns, examples) VALUES (?,?,?,?,?,?)",
            (eh["id"], eh.get("domain_id"), eh.get("doc_type_id"), eh["label"],
             json.dumps(eh.get("patterns", [])), json.dumps(eh.get("examples", []))),
        )
    conn.commit()
    conn.close()
    log.info(
        "Seed UPSERT completado: %d domains, %d subdomains, %d doc_types, %d entity_hints.",
        len(_SEED_DOMAINS), len(_SEED_SUBDOMAINS), len(_SEED_DOC_TYPES), len(_SEED_ENTITY_HINTS),
    )


def _load_into_memory():
    global _domains, _subdomains, _doc_types, _entity_hints, _loaded
    conn = _get_conn()

    _domains = {}
    for r in conn.execute("SELECT * FROM domains"):
        _domains[r["id"]] = {
            "label": r["label"],
            "description": r["description"],
            "signal_tags": set(json.loads(r["signal_tags"])),
            "signal_kw": json.loads(r["signal_kw"]),
        }

    _subdomains = {}
    for r in conn.execute("SELECT * FROM subdomains"):
        _subdomains[r["id"]] = {
            "domain_id": r["domain_id"],
            "label": r["label"],
            "signal_tags": set(json.loads(r["signal_tags"])),
            "signal_kw": json.loads(r["signal_kw"]),
        }

    _doc_types = {}
    for r in conn.execute("SELECT * FROM doc_types"):
        _doc_types[r["id"]] = {
            "label": r["label"],
            "signal_tags": set(json.loads(r["signal_tags"])),
            "signal_kw": json.loads(r["signal_kw"]),
            "signal_formats": set(json.loads(r["signal_formats"])),
        }

    _entity_hints = []
    for r in conn.execute("SELECT * FROM entity_hints"):
        _entity_hints.append({
            "id": r["id"],
            "domain_id": r["domain_id"],
            "doc_type_id": r["doc_type_id"],
            "label": r["label"],
            "patterns": json.loads(r["patterns"]),
            "examples": json.loads(r["examples"]),
        })

    conn.close()
    _loaded = True
    log.info(
        "Masters cargados en memoria: %d domains, %d subdomains, %d doc_types, %d entity_hints.",
        len(_domains), len(_subdomains), len(_doc_types), len(_entity_hints),
    )


def _ensure_loaded():
    if not _loaded:
        _init_db()
        _seed_if_empty()
        _load_into_memory()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _kw_in_text(keywords: list[str], text: str) -> int:
    """Cuenta cuántas keywords aparecen (como subcadena) en el texto."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def _score_entry(
    entry: dict,
    tags_set: set[str],
    searchable_text: str,
    file_type: str | None = None,
) -> float:
    """
    Puntúa un maestro contra las señales del documento.
    Pesos: tag match = 2, keyword match = 1, format match = 1.5.
    """
    score = 0.0
    # Tags coincidentes (peso alto: la señal más fiable)
    tag_hits = entry["signal_tags"] & tags_set
    score += len(tag_hits) * 2.0
    # Keywords en texto (título + keyphrases)
    score += _kw_in_text(entry.get("signal_kw", []), searchable_text) * 1.0
    # Formato (solo doc_types)
    if file_type and "signal_formats" in entry:
        if file_type.lower().lstrip(".") in entry["signal_formats"]:
            score += 1.5
    return score


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def classify_document(
    tags: list[str],
    keyphrases: list[str] | None = None,
    title: str = "",
    file_type: str = "",
) -> dict:
    """
    Clasifica un documento en domain, subdomain y doc_type basándose en su
    contenido (tags, keyphrases, título, tipo de fichero).

    Returns:
        {
            "domain": "data-engineering" | "other",
            "domain_label": "Data Engineering" | "General",
            "subdomain": "transformation" | "",
            "subdomain_label": "Transformation" | "",
            "doc_type": "notebook" | "technical_doc",
            "doc_type_label": "Notebook" | "Technical Document",
        }
    """
    _ensure_loaded()

    tags_set = set(tags or [])
    searchable = " ".join([
        title or "",
        " ".join(keyphrases or []),
        " ".join(tags or []),
    ])
    ft = file_type.lower().lstrip(".") if file_type else ""

    # 1) Domain
    domain_scores = {
        did: _score_entry(d, tags_set, searchable)
        for did, d in _domains.items()
    }
    best_domain = max(domain_scores, key=domain_scores.get) if domain_scores else "other"
    best_domain_score = domain_scores.get(best_domain, 0)
    if best_domain_score < _MIN_SCORE_DOMAIN:
        best_domain = "other"

    # 2) Subdomain (solo dentro del domain ganador)
    subdomain_candidates = {
        sid: s for sid, s in _subdomains.items()
        if s["domain_id"] == best_domain
    }
    if subdomain_candidates:
        sub_scores = {
            sid: _score_entry(s, tags_set, searchable)
            for sid, s in subdomain_candidates.items()
        }
        best_sub = max(sub_scores, key=sub_scores.get)
        best_sub_score = sub_scores.get(best_sub, 0)
        if best_sub_score < _MIN_SCORE_SUBDOMAIN:
            best_sub = ""
    else:
        best_sub = ""

    # 3) Doc type (independiente del domain)
    dt_scores = {
        dtid: _score_entry(dt, tags_set, searchable, ft)
        for dtid, dt in _doc_types.items()
    }
    best_dt = max(dt_scores, key=dt_scores.get) if dt_scores else "technical_doc"
    best_dt_score = dt_scores.get(best_dt, 0)
    if best_dt_score < _MIN_SCORE_DOCTYPE:
        best_dt = "technical_doc"

    return {
        "domain": best_domain,
        "domain_label": _domains[best_domain]["label"] if best_domain in _domains else "General",
        "subdomain": best_sub,
        "subdomain_label": (_subdomains[best_sub]["label"]
                            if best_sub and best_sub in _subdomains else ""),
        "doc_type": best_dt,
        "doc_type_label": _doc_types[best_dt]["label"] if best_dt in _doc_types else "Technical Document",
    }


def reload():
    """Recarga la caché en memoria desde SQLite (tras INSERT/UPDATE)."""
    global _loaded
    _loaded = False
    _ensure_loaded()


def get_domains() -> list[dict]:
    _ensure_loaded()
    return [{"id": k, **{kk: (list(vv) if isinstance(vv, set) else vv)
             for kk, vv in v.items()}} for k, v in _domains.items()]


def get_subdomains(domain_id: str | None = None) -> list[dict]:
    _ensure_loaded()
    items = _subdomains.items()
    if domain_id:
        items = [(k, v) for k, v in items if v["domain_id"] == domain_id]
    return [{"id": k, **{kk: (list(vv) if isinstance(vv, set) else vv)
             for kk, vv in v.items()}} for k, v in items]


def get_doc_types() -> list[dict]:
    _ensure_loaded()
    return [{"id": k, **{kk: (list(vv) if isinstance(vv, set) else vv)
             for kk, vv in v.items()}} for k, v in _doc_types.items()]


def get_entity_hints(
    domain_id: str | None = None,
    doc_type_id: str | None = None,
) -> list[dict]:
    """
    Devuelve los entity_hints aplicables para el dominio y tipo de documento.
    Incluye hints globales (domain_id=None) y los específicos que coincidan.
    """
    _ensure_loaded()
    result = []
    for h in _entity_hints:
        domain_match = h["domain_id"] is None or h["domain_id"] == domain_id
        dtype_match = h["doc_type_id"] is None or h["doc_type_id"] == doc_type_id
        if domain_match and dtype_match:
            result.append(h)
    return result


def get_db_path() -> str:
    """Devuelve la ruta de la DB (útil para debug/admin)."""
    return _DB_PATH
