"""
vocabulary.py
-------------
ÚNICA FUENTE DE VERDAD para las tags del pasaporte semántico.

Diseño:
  - VOCABULARY es un diccionario {tag_canónica: [alias, ...]}.
      · La clave (izquierda) es la tag OFICIAL que acaba en el frontmatter.
      · La lista (derecha) son las formas/variantes que deben unificarse a ella.
  - De aquí se DERIVAN automáticamente, sin duplicar conocimiento:
      · VOCAB_LIST            → solo las canónicas (lo que ve el LLM).
      · ALIAS                 → mapa {variante → canónica} (normalización).
      · _PATTERNS             → regex precompiladas para detectar términos en texto.

Para ampliar el vocabulario: añade UNA entrada a VOCABULARY. El resto se
actualiza solo (lista para el LLM, normalización y matching).

Funciones públicas:
  match_text_to_vocab(text, extra_candidates=None, cap=20) -> list[str]
      Detecta qué conceptos del vocabulario aparecen en el texto/candidatos.
  normalize_tags(raw, cap=10) -> list[str]
      Normaliza una lista de tags (cualquier variante → canónica), deduplica.
  in_vocab(tag) -> bool
      True si la tag (o su variante) está en el vocabulario.
"""

import re
from collections import Counter

# ---------------------------------------------------------------------------
# VOCABULARIO CONTROLADO  ──  {canónica: [alias...]}
# Convención de canónicas: minúsculas-con-guiones.
# ---------------------------------------------------------------------------

VOCABULARY: dict[str, list[str]] = {
    # ---- Cloud · Azure ---------------------------------------------------
    "azure": ["microsoft-azure"],
    "azure-data-factory": ["adf", "data-factory"],
    "azure-synapse": ["synapse", "synapse-analytics"],
    "microsoft-fabric": ["fabric", "ms-fabric", "msfabric"],
    "azure-devops": ["azure-pipelines"],
    "azure-functions": ["azure-function"],
    "azure-blob-storage": ["blob-storage", "azure-blob"],
    "azure-data-lake": ["adls", "data-lake-storage"],
    "azure-sql": ["azure-sql-database"],
    "power-bi": ["powerbi", "pbi"],
    "event-hubs": ["event-hub", "eventhub"],
    "key-vault": ["keyvault", "azure-key-vault"],
    "databricks": ["azure-databricks"],

    # ---- Cloud · AWS -----------------------------------------------------
    "aws": ["amazon-web-services"],
    "s3": ["amazon-s3", "aws-s3"],
    "ec2": ["amazon-ec2"],
    "aws-lambda": ["lambda-function"],
    "aws-glue": ["glue", "aws-glue-etl", "glue-job", "glue-crawler"],
    "redshift": ["amazon-redshift"],
    "athena": ["aws-athena"],
    "dynamodb": ["dynamo-db"],
    "sagemaker": ["aws-sagemaker"],
    "cloudformation": ["cloud-formation"],
    "ecs": ["amazon-ecs"],
    "eks": ["amazon-eks"],

    # ---- Datos / Data engineering ---------------------------------------
    "data-engineering": ["data-eng", "ingenieria-de-datos", "ingenieria-datos"],
    "data-pipeline": ["pipeline", "etl-pipeline"],
    "etl": ["elt"],
    "data-lake": ["datalake", "lakehouse"],
    "data-warehouse": ["datawarehouse", "dwh", "warehouse"],
    "medallion": ["medallion-architecture", "bronze-silver-gold"],
    "bronze-layer": ["capa-bronze", "bronze-layer"],
    "silver-layer": ["capa-silver", "silver-layer"],
    "gold-layer": ["capa-gold", "gold-layer"],
    "pyspark": ["spark", "apache-spark", "py-spark"],
    "spark-sql": ["sparksql"],
    "delta-lake": ["delta", "deltalake", "delta-table"],
    "parquet": ["apache-parquet", "parquet-format", "fichero-parquet", ".parquet"],
    "dataframe": ["data-frame"],
    "pandas": ["pd", "pandas-dataframe", "pandas-python", "libreria-pandas"],
    "numpy": ["np", "numpy-array", "libreria-numpy", "numerical-python"],
    "airflow": ["apache-airflow"],
    "kafka": ["apache-kafka"],
    "dbt": ["data-build-tool"],
    "snowflake": ["snowflake-dw", "snowflake-cloud", "snowflake-data-cloud"],
    "bigquery": ["big-query"],
    "batch-processing": ["procesamiento-batch"],
    "streaming": ["stream-processing", "procesamiento-en-streaming"],
    "orchestration": ["orquestacion", "scheduling"],
    "data-quality": ["calidad-de-datos"],
    "data-governance": ["gobierno-del-dato", "gobernanza-del-dato", "governance"],

    # ---- Python / dev ----------------------------------------------------
    "python": ["py", "python3"],
    "jupyter": ["jupyter-notebook"],
    "fastapi": ["fast-api", "fastapi-framework", "starlette"],
    "flask": ["flask-python", "flask-framework", "flask-app"],
    "django": ["django-framework", "django-rest-framework", "drf"],
    "pydantic": ["pydantic-model", "pydantic-validation", "data-validation-python"],
    "pytest": ["py-test", "pytest-framework", "pytest-fixture", "pytest-plugin"],
    "poetry": ["poetry-python", "pyproject-toml", "gestor-dependencias-python"],
    "conda": ["anaconda", "miniconda"],
    "pip": ["pip-install", "python-pip", "requirements-txt", "package-manager-python"],
    "asyncio": ["async-python"],
    "rest-api": ["rest", "api-rest", "restful"],
    "graphql": ["graph-ql", "graphql-api", "graphql-schema", "apollo-graphql"],
    "yaml": ["yml", "yaml-file", "yaml-config"],

    # ---- Web / frontend --------------------------------------------------
    "web": ["website", "pagina-web"],
    "html": ["html5", "htm", "hypertext-markup-language", "html-file", "fichero-html"],
    "css": ["css3"],
    "javascript": ["js"],
    "typescript": ["ts", "typescript-lang", "typed-javascript"],
    "react": ["reactjs"],
    "angular": ["angular-framework", "angularjs", "ng", "angular-cli"],
    "vue": ["vuejs"],
    "nodejs": ["node-js"],

    # ---- Contenedores / Infra / DevOps ----------------------------------
    "docker": ["dockerfile", "docker-compose", "containerization", "contenedores"],
    "kubernetes": ["k8s", "kube"],
    "helm": ["helm-chart", "kubernetes-helm", "helm-release", "helm-values"],
    "terraform": ["terraform-iac", "hcl", "hashicorp-terraform", "tf-plan", "tf-apply"],
    "ansible": ["ansible-playbook", "ansible-role", "ansible-automation", "ansible-tower"],
    "ci-cd": ["cicd", "ci/cd"],
    "github-actions": ["gh-actions"],
    "gitlab-ci": ["gitlab-pipeline", "gitlab-ci-cd", "gitlab-runner", ".gitlab-ci-yml"],
    "jenkins": ["jenkins-pipeline", "jenkins-ci", "jenkinsfile", "jenkins-job"],
    "nginx": ["nginx-proxy", "nginx-server", "nginx-reverse-proxy", "nginx-config"],
    "linux": ["ubuntu", "debian", "centos", "rhel", "sistema-operativo-linux"],
    "bash": ["shell-script", "shell"],
    "microservices": ["microservicios", "microservice"],
    "serverless": ["sin-servidor", "faas", "functions-as-a-service", "serverless-framework"],
    "devops": ["dev-ops", "desarrollo-operaciones", "cultura-devops", "agile-ops"],
    "observability": ["observabilidad"],
    "prometheus": ["prometheus-metrics", "prometheus-monitoring", "promql", "alertmanager"],
    "grafana": ["grafana-dashboard", "grafana-panels", "grafana-alerting", "loki"],
    "monitoring": ["monitorizacion", "monitoreo"],
    "logging": ["logs"],

    # ---- Atlassian / ALM / Testing --------------------------------------
    "jira": ["jira-software", "jira-issue", "jira-ticket", "jira-board", "atlassian-jira"],
    "confluence": ["confluence-wiki", "confluence-page", "confluence-space", "atlassian-confluence"],
    "xray": ["xray-test"],
    "test-management": ["gestion-de-pruebas", "gestion-pruebas"],
    "testing": ["pruebas", "qa", "quality-assurance"],
    "test-case": ["caso-de-prueba", "casos-de-prueba", "test-cases"],
    "test-plan": ["plan-de-pruebas", "plan-pruebas"],
    "unit-testing": ["test-unitario", "pruebas-unitarias", "unit-test"],
    "integration-testing": ["pruebas-de-integracion", "integration-test"],
    "e2e-testing": ["end-to-end", "e2e", "pruebas-e2e"],
    "regression-testing": ["pruebas-de-regresion", "regression"],
    "selenium": ["selenium-webdriver", "selenium-grid", "selenium-ide", "webdriver"],
    "cypress": ["cypress-testing", "cypress-e2e", "cypress-component"],
    "postman": ["postman-api", "newman", "postman-collection", "postman-test"],
    "bdd": ["behavior-driven", "cucumber", "gherkin"],
    "tdd": ["test-driven"],
    "test-automation": ["automatizacion-de-pruebas", "automatizacion-pruebas"],

    # ---- Arquitectura / patrones / documentación ------------------------
    "architecture": ["arquitectura", "arquitectura-software"],
    "solution-architecture": ["arquitectura-de-solucion"],
    "diagram": ["diagrama"],
    "design-pattern": ["patron-de-diseno", "patrones-de-diseno", "design-patterns"],
    "data-model": ["modelo-de-datos", "modelado-de-datos", "data-modeling"],
    "api-design": ["diseno-de-api"],
    "event-driven": ["arquitectura-orientada-a-eventos", "event-driven-architecture"],
    "domain-driven-design": ["ddd"],
    "documentation": ["documentacion"],
    "manual": ["manuales", "guia", "guide", "guia-de-usuario", "user-guide"],
    "runbook": ["run-book"],
    "specification": ["especificacion", "spec"],
    "requirements": ["requisitos", "requerimientos"],
    "tutorial": ["how-to", "paso-a-paso", "guia-practica", "walkthrough", "tutorial-tecnico"],
    "reference": ["referencia"],
    "best-practices": ["buenas-practicas", "mejores-practicas"],
    "troubleshooting": ["resolucion-de-problemas"],
    "configuration": ["configuracion", "config"],
    "security": ["seguridad"],
    "authentication": ["autenticacion", "auth", "oauth", "oauth2"],
    "authorization": ["autorizacion"],
    "performance": ["rendimiento", "optimizacion", "optimization"],
    "migration": ["migracion"],
    "schema": ["esquema"],
    "metadata": ["metadatos"],

    # ---- IA / ML ---------------------------------------------------------
    "machine-learning": ["ml", "aprendizaje-automatico"],
    "deep-learning": ["deep-learning"],
    "llm": ["large-language-model", "modelo-de-lenguaje"],
    "rag": ["retrieval-augmented-generation"],
    "embeddings": ["embedding", "vector-embeddings"],
    "vector-database": ["vector-db", "vectordb", "base-de-datos-vectorial"],
    "ollama": ["ollama-llm", "ollama-local", "ollama-serve", "llama-local"],
    "langchain": ["langchain-python", "langchain-framework", "lcel", "langchain-agent"],
    "huggingface": ["hugging-face"],
    "openai": ["openai-api", "gpt-4", "gpt-4o", "gpt-turbo", "chatgpt", "openai-python"],
    "prompt-engineering": [
        "ingenieria-de-prompts", "prompt-design", "prompt-template",
        "role-prompt", "prompt-pattern",
    ],
    "nlp": ["procesamiento-de-lenguaje-natural"],
    "fine-tuning": ["finetune", "fine-tune", "ajuste-fino", "sft", "rlhf"],
    "chain-of-thought": ["cot", "cadena-de-pensamiento", "chain-of-thought-prompting"],
    "few-shot": ["few-shot-learning", "pocos-ejemplos", "in-context-learning"],
    "zero-shot": ["zero-shot-prompting", "zero-shot-inference"],

    # ---- Agentes IA / Agentic AI ----------------------------------------
    # Cubre los agentes definidos: agent_data, agent_BI, agent_data_architecture,
    # agent_ciber, agent_test y frameworks subyacentes.
    "ai-agent": [
        "agente-ia", "agente-inteligente", "ai-assistant",
        "copilot-agent", "agent-mode", "agentic",
        "agent-data", "agent-bi", "agent-ciber", "agent-test",
        "agent-data-architecture", "agent-md", "agent-markdown",
        "agent-instructions",
    ],
    "multi-agent": [
        "multi-agent-system", "sistema-multiagente", "agentes-colaborativos",
        "agent-orchestration",
    ],
    "tool-calling": ["function-calling", "tool-use", "llamada-a-herramienta"],
    "agent-memory": [
        "memoria-agente", "agent-context", "conversational-memory",
        "long-term-memory",
    ],
    "agent-planning": [
        "planificacion-agente", "react", "reasoning-acting",
        "plan-and-execute",
    ],
    "semantic-kernel": ["sk", "microsoft-semantic-kernel"],
    "autogen": ["microsoft-autogen", "ag2"],
    "langgraph": ["lang-graph", "langchain-graph"],
    "copilot-studio": ["microsoft-copilot-studio", "power-virtual-agents", "pva"],
    "github-copilot": ["copilot", "gh-copilot", "copilot-chat"],
    "mcp": ["model-context-protocol", "mcp-server", "mcp-tool"],
    "agent-eval": [
        "agent-evaluation", "evaluacion-agente", "ragas",
        "llm-eval", "benchmark-llm",
    ],
    "system-prompt": [
        "prompt-sistema", "instrucciones-sistema", "system-message",
        "system-instruction", "system-instructions", "developer-instructions",
        "assistant-instructions", "copilot-instructions", "instruction-file",
        "instructions-md", "prompt-md",
    ],
    "context-window": ["ventana-de-contexto", "token-limit", "context-length"],

    # ---- Power BI / Modelado semántico (agent_BI) ------------------------
    "dax": ["data-analysis-expressions", "medida-dax", "calculated-column"],
    "tmdl": ["tabular-model-definition-language", "tabular-model"],
    "power-query-m": ["power-query", "lenguaje-m", "m-language", "pq-m"],
    "semantic-model": ["modelo-semantico", "power-bi-dataset", "dataset-pbi"],
    "star-schema": ["esquema-estrella", "dimensional-model", "modelo-dimensional"],
    "direct-query": ["directquery", "live-connection", "conexion-directa"],
    "import-mode": ["modo-importacion", "scheduled-refresh"],
    "row-level-security": ["rls", "seguridad-nivel-fila"],

    # ---- Ciberseguridad (agent_ciber) -----------------------------------
    "zero-trust": ["modelo-zero-trust", "never-trust-always-verify"],
    "iam": ["identity-access-management", "gestion-identidades", "idm"],
    "owasp": ["owasp-top-10", "owasp-vulnerabilidades"],
    "devsecops": ["dev-sec-ops", "seguridad-en-ciclo-desarrollo", "sdlc-security"],
    "waf": ["web-application-firewall", "cortafuegos-aplicacion"],
    "siem": ["security-information-event-management", "azure-sentinel", "splunk"],
    "penetration-testing": ["pentest", "prueba-de-penetracion", "ethical-hacking"],
    "vulnerability-scanning": ["escaneo-vulnerabilidades", "cve", "cvss"],
    "secret-management": ["gestion-secretos", "azure-key-vault", "hashicorp-vault"],
    "mtls": ["mutual-tls", "certificado-cliente", "client-certificate"],

    # ---- Bases de datos --------------------------------------------------
    "sql": ["t-sql", "tsql", "structured-query-language"],
    "postgresql": ["postgres", "psql"],
    "mysql": ["mysql-database", "mysql-server", "mariadb", "mysql-workbench"],
    "mongodb": ["mongo"],
    "redis": ["redis-cache", "redis-server", "redis-cluster", "in-memory-db"],
    "sqlserver": ["sql-server", "mssql"],
    "oracle-db": ["oracle-database"],
    "sqlite": ["sqlite-db", "sqlite3", "sqlite-database", "embedded-db"],

    # ---- Control de versiones -------------------------------------------
    "git": ["git-repo", "git-commit", "git-branch", "version-control", "control-de-versiones"],
    "github": ["github-repo", "github-platform", "gh", "github-issues", "github-pr"],
    "gitlab": ["gitlab-repo", "gitlab-platform", "gitlab-merge-request", "gitlab-pages"],
    "bitbucket": ["bitbucket-repo", "atlassian-bitbucket", "bitbucket-pipelines", "bitbucket-pr"],

    # ---- Formatos / artefactos ------------------------------------------
    # Formatos de datos estructurados
    "json": ["json-format", "json-file", "application-json", "json-schema"],
    "xml": ["xml-format", "xml-file", "extensible-markup-language", "xml-schema", "xsd"],
    "csv": ["csv-file", "csv-dataset", "comma-separated", "comma-separated-values", "fichero-csv", "dataset-csv"],
    # Formatos de documentos ofimáticos
    "pdf": ["pdf-document", "pdf-file", "portable-document-format", "adobe-pdf"],
    "spreadsheet": ["excel", "xlsx", "xls", "microsoft-excel", "hoja-de-calculo", "hoja-calculo", "libro-excel", "excel-file", "fichero-excel", "openpyxl", "xlrd", "xlwt"],
    "word-document": ["word", "docx", "doc", "microsoft-word", "documento-word", "word-file", "fichero-word", "python-docx"],
    "presentation": ["powerpoint", "pptx", "ppt", "microsoft-powerpoint", "diapositivas", "presentacion", "presentacion-powerpoint", "python-pptx"],
    # Formatos de texto y código
    "markdown": ["md"],
    "toml": ["toml-file", "pyproject-toml"],
    "ini": ["ini-file", "config-file", "cfg"],
    "txt": ["text-file", "plain-text", "fichero-texto", "archivo-texto"],
    # Formatos de notebooks y código
    "notebook": ["ipynb", "jupyter-notebook", "notebook-file", "interactive-notebook"],
    "python-script": ["py-file", "script-python", "fichero-py", ".py"],
    "sql-script": ["sql-file", "script-sql", "fichero-sql", ".sql", "query-file"],
    # Formatos de diagramas y diseño
    "drawio": ["draw-io", "diagrams-net", "diagrama-drawio", ".drawio", "drawio-file", "fichero-drawio"],
    "svg": ["svg-file", "scalable-vector-graphics"],
    # Formatos de archivos comprimidos / contenedores
    "zip": ["zip-file", "fichero-zip", "archivo-comprimido"],
    "tar": ["tar-file", "tar-gz", "tgz"],

    # ---- Microsoft Fabric (artefactos específicos) ----------------------
    # Distingue Fabric de ADF: Fabric usa "Trident" internamente y tiene
    # artefactos propios (Lakehouse, Warehouse, Eventstream, Mirroring).
    "trident-notebook": ["fabric-notebook", "notebook-fabric", "tridentnotebook"],
    "fabric-pipeline": ["fabric-data-pipeline", "pipeline-fabric"],
    "fabric-lakehouse": ["lakehouse-fabric", "fabric-lh"],
    "fabric-warehouse": ["fabric-dw", "synapse-data-warehouse-fabric"],
    "fabric-workspace": ["workspace-fabric", "capacidad-fabric"],
    "onelake": ["one-lake", "fabric-onelake"],
    "fabric-dataflow": ["dataflow-gen2", "gen2-dataflow", "fabric-dataflow-gen2"],
    "fabric-eventstream": ["eventstream", "fabric-event-stream"],
    "fabric-mirroring": ["fabric-mirror", "mirroring-fabric"],
    "fabric-sql-endpoint": ["sql-analytics-endpoint", "lakehouse-sql-endpoint"],
    "fabric-shortcut": ["shortcut-fabric", "acceso-directo-fabric"],
    "fabric-semantic-model": ["direct-lake", "semantic-model-fabric"],

    # ---- Azure (servicios adicionales) ----------------------------------
    "azure-openai": ["azure-openai-service", "aoai", "azure-gpt"],
    "azure-purview": ["microsoft-purview", "purview", "data-catalog-azure"],
    "azure-monitor": ["log-analytics", "azure-application-insights", "azure-insights"],
    "azure-cognitive-services": ["cognitive-services", "azure-ai-services"],
    "azure-cost-management": ["cost-management", "azure-billing"],
    "azure-rbac": ["role-based-access-control", "rbac"],
    "azure-logic-apps": ["logic-apps"],
    "azure-service-bus": ["service-bus"],

    # ---- Ingeniería de datos: patrones y técnicas -----------------------
    "dag": ["directed-acyclic-graph", "grafo-dependencias", "grafo-dag"],
    "topological-sort": ["ordenamiento-topologico", "topological-ordering"],
    "column-mapping": ["mapeo-columnas", "column-map", "schema-mapping"],
    "data-quality-rule": ["regla-calidad", "validation-rule", "dq-rule", "regla-validacion"],
    "delta-merge": ["merge-into", "upsert-delta", "delta-upsert"],
    "incremental-load": ["carga-incremental", "incremental-processing", "watermark"],
    "full-load": ["carga-completa", "full-refresh"],
    "schema-evolution": ["schema-drift", "evolucion-esquema"],
    "lakehouse-schema": ["schema-lakehouse", "capa-lakehouse"],
    "data-lineage": ["linaje-de-datos", "lineage", "trazabilidad-datos"],
    "data-catalog": ["catalogo-de-datos", "data-catalogue"],
    "networkx": ["network-x", "nx-python"],
    "dependency-graph": ["grafo-dependencias-tablas"],
    "checkpoint": ["delta-checkpoint", "spark-checkpoint"],
    "broadcast-join": ["join-broadcast", "broadcast-hint"],
    "partition-pruning": ["poda-particiones"],

    # ---- Documentos de adquisición / licitación ------------------------
    "rfp": ["request-for-proposal", "solicitud-de-propuesta", "rfp-document"],
    "rfi": ["request-for-information", "solicitud-de-informacion", "rfi-document"],
    "vct": [
        "verificacion-cumplimiento-tecnico",
        "technical-compliance-verification",
        "verification-compliance",
        "informe-vct",
    ],
    "sow": ["statement-of-work", "enunciado-de-trabajo", "alcance-del-trabajo"],
    "procurement": ["adquisicion", "licitacion", "contratacion", "concurso"],
    "technical-offer": ["oferta-tecnica", "propuesta-tecnica"],
    "contract": ["contrato", "acuerdo-marco", "convenio"],
    "tender": ["pliego", "pliego-de-condiciones", "convocatoria"],

    # ---- Gestión de activos / Tecnología Operacional (OT) ---------------
    "asset-management": ["gestion-activos", "gestion-de-activos", "asset-lifecycle"],
    "predictive-maintenance": [
        "mantenimiento-predictivo", "maintenance-prediction", "mantenimiento-basado-condicion",
    ],
    "preventive-maintenance": ["mantenimiento-preventivo", "pm-schedule", "mantenimiento-programado", "preventive-pm"],
    "corrective-maintenance": ["mantenimiento-correctivo", "cm-work-order", "reparacion", "averia"],
    "asset-health": ["indice-salud-activo", "health-index", "salud-activo"],
    "operational-technology": ["tecnologia-operacional", "ot", "ot-systems"],
    "iiot": ["industrial-iot", "iot-industrial", "industry-4", "industria-4"],
    "scada": ["sistema-scada", "supervisory-control"],
    "historian": ["pi-historian", "sistema-historian", "osisoftpi"],
    "sensor-data": ["datos-sensor", "telemetria", "telemetry"],
    "anomaly-detection": ["deteccion-anomalias", "deteccion-anomalia"],
    "condition-monitoring": ["monitorizacion-condicion", "cbm"],

    # ---- Gestión de proyectos: artefactos adicionales -------------------
    "estimation": ["estimacion", "estimacion-esfuerzo", "effort-estimation", "sizing"],
    "roadmap": ["hoja-de-ruta", "plan-estrategico"],
    "kpi": ["indicador-clave", "key-performance-indicator"],
    "sla": ["service-level-agreement", "acuerdo-nivel-servicio"],
    "raci": ["matriz-raci", "responsability-matrix"],

    # ---- Formación / Cursos ---------------------------------------------
    "course": ["curso", "modulo", "modulo-formativo", "modulo-de-formacion"],
    "lesson": ["leccion", "teoria", "practica"],
    "exercise": ["ejercicio", "lab", "laboratorio"],
    "certification": ["certificacion", "certificado"],

    # ---- Seguridad y cumplimiento ---------------------------------------
    "data-privacy": ["privacidad-datos", "gdpr", "rgpd", "proteccion-datos"],
    "data-masking": ["enmascaramiento-datos", "anonimizacion"],
    "encryption": ["cifrado", "encriptacion"],
    "audit-log": ["registro-auditoria", "audit-trail"],
    "compliance": ["cumplimiento", "normativa"],
}


# ---------------------------------------------------------------------------
# Derivados automáticos  (NO editar a mano)
# ---------------------------------------------------------------------------

def _norm_key(s: str) -> str:
    """Normaliza una cadena a la forma canónica: minúsculas-con-guiones."""
    s = re.sub(r"[\s_/]+", "-", (s or "").strip().lower()).strip("-")
    return re.sub(r"[^a-z0-9\-]", "", s)


# Lista de canónicas (lo que ve el LLM como vocabulario preferido)
VOCAB_LIST: list[str] = sorted(VOCABULARY.keys())

# Mapa de normalización: cada variante (y cada canónica) → canónica
ALIAS: dict[str, str] = {}
for _canonical, _aliases in VOCABULARY.items():
    ALIAS[_norm_key(_canonical)] = _canonical
    for _a in _aliases:
        ALIAS[_norm_key(_a)] = _canonical


def _build_patterns() -> list[tuple[str, "re.Pattern"]]:
    """Precompila una regex por cada superficie (canónica + alias)."""
    patterns: list[tuple[str, re.Pattern]] = []
    for canonical, aliases in VOCABULARY.items():
        for surface in [canonical, *aliases]:
            surface = surface.strip().lower()
            if not surface or len(surface) < 2:
                continue
            parts = [p for p in re.split(r"[\s\-_/]+", surface) if p]
            if not parts:
                continue
            # separador flexible: espacio, guion, guion-bajo o barra
            body = r"[\s\-_/]+".join(re.escape(p) for p in parts)
            # límites tipo "palabra" sin cortar dentro de otra palabra
            pat = re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)
            patterns.append((canonical, pat))
    return patterns


_PATTERNS: list[tuple[str, "re.Pattern"]] = _build_patterns()


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def in_vocab(tag: str) -> bool:
    """True si la tag (o cualquiera de sus variantes) está en el vocabulario."""
    return _norm_key(tag) in ALIAS


def normalize_tags(raw: list[str] | None, cap: int = 10) -> list[str]:
    """
    Convierte cualquier lista de tags a su forma canónica, deduplicando y
    preservando el orden de aparición. Las tags fuera del vocabulario se
    conservan tal cual (normalizadas a minúsculas-con-guiones).
    """
    out: list[str] = []
    seen: set[str] = set()
    for t in raw or []:
        if not isinstance(t, str):
            continue
        key = _norm_key(t)
        if not key or len(key) < 2:
            continue
        canonical = ALIAS.get(key, key)
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
        if len(out) >= cap:
            break
    return out


def match_text_to_vocab(
    text: str,
    extra_candidates: list[str] | None = None,
    cap: int = 20,
) -> list[str]:
    """
    Detecta qué conceptos del vocabulario aparecen en el texto (y en una lista
    opcional de candidatos: entities, keyphrases, tokens del nombre/URL...).

    Devuelve las tags CANÓNICAS ordenadas por número de apariciones
    (descendente) y, a igualdad, alfabéticamente. Limitado a `cap`.
    """
    haystack = text or ""
    if extra_candidates:
        haystack = haystack + "\n" + "\n".join(c for c in extra_candidates if c)
    haystack = haystack.lower()

    counts: dict[str, int] = Counter()
    for canonical, pat in _PATTERNS:
        hits = pat.findall(haystack)
        if hits:
            counts[canonical] += len(hits)

    ordered = sorted(counts.keys(), key=lambda c: (-counts[c], c))
    return ordered[:cap]
