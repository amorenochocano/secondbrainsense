# F1 — Pipeline de Tres Fases en SurfSense
**Duración:** 2 semanas  
**Equipo:** Backend Senior (1) + Backend Mid (1)  
**Dependencias:** F0 completada  
**Entregable:** Cualquier fichero subido a SurfSense pasa por las 3 fases de Second Brain antes de indexarse. Schema PostgreSQL para metadata Brain (maestros + vocabulario). Pipeline robusto con propagación de `search_space_id` en todos los bloques.

---

## Objetivo

Reemplazar el pipeline genérico de `document_converters.py` de SurfSense por el pipeline de tres fases de Second Brain. Este es el cambio de mayor impacto y el que habilita todas las fases posteriores.

**Antes (SurfSense):**
```
Fichero → ETL Service (Docling) → Markdown genérico → SUMMARY_PROMPT_TEMPLATE → chunker global
```

**Después (SecondBrainSense F1):**
```
Fichero → Fase 1 (Extractor específico) → Fase 2 (UniversalCleaner) → Fase 3 (Preprocesador semántico) → processed_text con ##/###/####
```

---

## F1.0 — Schema PostgreSQL: Brain Metadata (Día 0)

`masters.db` (SQLite) y `vocabulary.py` (hardcodeado en Python) se sustituyen por **cinco tablas PostgreSQL** gestionadas con Alembic. Esto convierte los maestros de clasificación y el vocabulario controlado en datos de primera clase: versionables, multi-tenant por `search_space_id`, y preparados para el módulo de administración (F6/F7).

### Justificación de la decisión

| Criterio | SQLite `masters.db` | PostgreSQL (F1.0) |
|---|---|---|
| Multi-tenant | ❌ fichero global único | ✅ `search_space_id` por fila |
| Admin UI | ❌ requiere acceso SSH/código | ✅ CRUD REST en F6/F7 |
| Migraciones | ❌ DDL manual | ✅ Alembic con seed incluido |
| Concurrencia | ❌ write-lock global | ✅ MVCC PostgreSQL |
| Vocabulario editable en caliente | ❌ redeploy completo | ✅ `PUT /brain/admin/vocabulary/{id}` |

**Regla de resolución multi-tenant:** `search_space_id = NULL` → registro global (seed por defecto, aplica a todos los spaces). Un registro con `search_space_id = <id>` sobreescribe el global para ese space específico. El `BrainMetadataService` (F1.2) aplica esta resolución con caché en memoria.

### Tablas

| Tabla | Sustituye a | Propósito |
|---|---|---|
| `brain_domains` | `_SEED_DOMAINS` en `masters.py` | Dominios funcionales (data-engineering, cloud-azure…) |
| `brain_subdomains` | `_SEED_SUBDOMAINS` en `masters.py` | Subdominios dentro de cada dominio |
| `brain_doc_types` | `_SEED_DOC_TYPES` en `masters.py` | Tipos documentales (notebook, pipeline, procedure…) |
| `brain_entity_hints` | `_entity_hints` en `masters.py` | Patrones regex para extracción de entities por dominio |
| `brain_vocabulary` | `VOCABULARY` dict en `vocabulary.py` | Vocabulario controlado: canónica → aliases |

### Migración Alembic

**Fichero:** `surfsense_backend/alembic/versions/160_brain_metadata_tables.py`

```python
"""Brain metadata tables: domains, subdomains, doc_types, entity_hints, vocabulary

Revision ID: brain_metadata_001
Revises: <revision_anterior>
Create Date: 2026-06-XX
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid, json
from datetime import datetime, timezone


def upgrade() -> None:
    # ── brain_domains ──────────────────────────────────────────────────────
    op.create_table(
        "brain_domains",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("domain_key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("signal_tags", JSONB, server_default="[]"),
        sa.Column("signal_kw",   JSONB, server_default="[]"),
        sa.Column(
            "search_space_id", UUID(as_uuid=True),
            sa.ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("is_active",  sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("domain_key", "search_space_id", name="uq_brain_domain_key_space"),
    )

    # ── brain_subdomains ───────────────────────────────────────────────────
    op.create_table(
        "brain_subdomains",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("subdomain_key", sa.String(80), nullable=False),
        sa.Column("domain_key",    sa.String(80), nullable=False),   # FK lógica para flexibilidad
        sa.Column("label",         sa.String(200), nullable=False),
        sa.Column("signal_tags",   JSONB, server_default="[]"),
        sa.Column("signal_kw",     JSONB, server_default="[]"),
        sa.Column(
            "search_space_id", UUID(as_uuid=True),
            sa.ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("is_active",  sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("subdomain_key", "search_space_id", name="uq_brain_subdomain_key_space"),
    )

    # ── brain_doc_types ────────────────────────────────────────────────────
    op.create_table(
        "brain_doc_types",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("type_key",       sa.String(80),  nullable=False),
        sa.Column("label",          sa.String(200), nullable=False),
        sa.Column("signal_tags",    JSONB, server_default="[]"),
        sa.Column("signal_kw",      JSONB, server_default="[]"),
        sa.Column("signal_formats", JSONB, server_default="[]"),   # [".py", ".ipynb", ...]
        sa.Column(
            "search_space_id", UUID(as_uuid=True),
            sa.ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("is_active",  sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("type_key", "search_space_id", name="uq_brain_doc_type_key_space"),
    )

    # ── brain_entity_hints ─────────────────────────────────────────────────
    op.create_table(
        "brain_entity_hints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("hint_key",    sa.String(150), nullable=False),
        sa.Column("domain_key",  sa.String(80),  nullable=True),   # NULL = todos los dominios
        sa.Column("doc_type_key",sa.String(80),  nullable=True),   # NULL = todos los tipos
        sa.Column("label",       sa.String(300), nullable=False),
        sa.Column("patterns",    JSONB, server_default="[]"),      # regex patterns
        sa.Column("examples",    JSONB, server_default="[]"),
        sa.Column(
            "search_space_id", UUID(as_uuid=True),
            sa.ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("is_active",  sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("hint_key", "search_space_id", name="uq_brain_entity_hint_key_space"),
    )

    # ── brain_vocabulary ───────────────────────────────────────────────────
    op.create_table(
        "brain_vocabulary",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("canonical_tag", sa.String(150), nullable=False),
        sa.Column("aliases",       JSONB, server_default="[]"),    # ["alias1", "alias2"]
        sa.Column(
            "search_space_id", UUID(as_uuid=True),
            sa.ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("is_active",  sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_tag", "search_space_id", name="uq_brain_vocab_tag_space"),
    )

    # Índices de rendimiento (clasificación ocurre en cada ingesta)
    op.create_index("ix_brain_domains_space",    "brain_domains",    ["search_space_id"])
    op.create_index("ix_brain_subdomains_domain","brain_subdomains", ["domain_key"])
    op.create_index("ix_brain_vocab_space",      "brain_vocabulary", ["search_space_id"])
    op.create_index("ix_brain_doc_types_space",  "brain_doc_types",  ["search_space_id"])

    # Seed global (search_space_id=NULL)
    bind = op.get_bind()
    _seed_brain_metadata(bind)


def _seed_brain_metadata(bind) -> None:
    """Inserta datos globales desde masters.py + vocabulary.py del Second Brain original."""
    now = datetime.now(timezone.utc).isoformat()

    _domains = [
        ("data-engineering",     "Data Engineering",
         ["pyspark","etl","data-pipeline","medallion","delta-lake","airflow","kafka","dbt","data-lake","data-warehouse"],
         ["pipeline","ingesta","etl","medallion","bronze","silver","gold","lakehouse"]),
        ("cloud-azure",          "Cloud Azure",
         ["azure","microsoft-fabric","azure-data-factory","databricks","power-bi","azure-synapse","azure-functions"],
         ["azure","fabric","synapse","databricks","power bi","adf","data factory"]),
        ("cloud-aws",            "Cloud AWS",
         ["aws","s3","aws-glue","redshift","sagemaker","dynamodb","cloudformation","ecs","eks"],
         ["amazon","aws","s3","lambda","redshift","sagemaker"]),
        ("development",          "Software Development",
         ["python","fastapi","rest-api","typescript","react","nodejs","pytest","pydantic","asyncio"],
         ["api","endpoint","frontend","backend","desarrollo","módulo","librería","framework"]),
        ("data-science-ml",      "Data Science & ML",
         ["machine-learning","llm","rag","embeddings","vector-database","ollama","huggingface","langchain"],
         ["modelo","embedding","clasificación","neural","inteligencia artificial","predicción"]),
        ("databases",            "Databases",
         ["sql","postgresql","mongodb","redis","sqlserver","oracle-db","sqlite","qdrant"],
         ["base de datos","tabla","query","índice","database","schema","stored procedure"]),
        ("architecture",         "Architecture & Design",
         ["architecture","solution-architecture","design-pattern","data-model","api-design","diagram","event-driven"],
         ["arquitectura","diseño","patrón","modelo de datos","diagrama","flujo","componente"]),
        ("devops-infra",         "DevOps & Infrastructure",
         ["docker","kubernetes","terraform","ansible","ci-cd","github-actions","monitoring","prometheus","grafana"],
         ["deploy","despliegue","contenedor","infra","pipeline ci","kubernetes"]),
        ("testing",              "Testing & QA",
         ["testing","unit-testing","e2e-testing","selenium","cypress","bdd","tdd","xray","pytest"],
         ["pruebas","test","caso de prueba","plan de pruebas","qa","automatización"]),
        ("project-management",   "Project Management",
         ["jira","confluence","xray","test-management","kanban","scrum"],
         ["proyecto","sprint","backlog","incidencia","ticket","epic","story","kanban","scrum"]),
        ("business-intelligence","Business Intelligence",
         ["power-bi","reporting","kpi","dashboard","olap","powerbi"],
         ["reporte","dashboard","kpi","análisis de negocio","indicador"]),
    ]
    for domain_key, label, signal_tags, signal_kw in _domains:
        bind.execute(sa.text(
            "INSERT INTO brain_domains(id,domain_key,label,signal_tags,signal_kw,"
            "search_space_id,is_active,created_at,updated_at) "
            "VALUES(gen_random_uuid(),:dk,:lb,:st::jsonb,:sk::jsonb,NULL,true,:now,:now) "
            "ON CONFLICT(domain_key,search_space_id) DO NOTHING"
        ), {"dk": domain_key, "lb": label,
            "st": json.dumps(signal_tags), "sk": json.dumps(signal_kw), "now": now})

    _doc_types = [
        ("notebook",   "Notebook / Análisis",
         ["jupyter","notebook","análisis","exploración","visualización"],
         ["notebook","ipynb","análisis exploratorio","jupyter"],
         [".ipynb"]),
        ("pipeline",   "Pipeline / ETL",
         ["pipeline","etl","orchestration","data-factory","airflow"],
         ["pipeline","etl","ingesta","transformación","orquestación"],
         [".py",".json",".drawio",".xml"]),
        ("procedure",  "Stored Procedure / SQL",
         ["sql","stored-procedure","query","ddl","dml"],
         ["procedure","stored procedure","sp_","usp_","query","select","insert"],
         [".sql"]),
        ("documentation","Documentación técnica",
         ["documentation","readme","wiki","guide","tutorial"],
         ["documentación","guía","manual","tutorial","readme","wiki"],
         [".md",".docx",".pdf",".html"]),
        ("configuration","Configuración / Schema",
         ["configuration","config","schema","settings","parameters"],
         ["configuración","config","parámetros","settings","schema"],
         [".json",".xml",".yaml",".yml"]),
        ("diagram",    "Diagrama / Arquitectura",
         ["diagram","architecture","flow","drawio","visio"],
         ["diagrama","arquitectura","flujo","diseño","draw.io"],
         [".drawio",".xml"]),
        ("presentation","Presentación",
         ["presentation","slides","deck","powerpoint"],
         ["presentación","diapositivas","slides","deck"],
         [".pptx",".ppt"]),
        ("spreadsheet","Hoja de cálculo",
         ["spreadsheet","excel","table","data"],
         ["hoja de cálculo","excel","tabla","datos","xlsx"],
         [".xlsx",".xls",".csv"]),
    ]
    for type_key, label, signal_tags, signal_kw, signal_formats in _doc_types:
        bind.execute(sa.text(
            "INSERT INTO brain_doc_types(id,type_key,label,signal_tags,signal_kw,"
            "signal_formats,search_space_id,is_active,created_at,updated_at) "
            "VALUES(gen_random_uuid(),:tk,:lb,:st::jsonb,:sk::jsonb,:sf::jsonb,NULL,true,:now,:now) "
            "ON CONFLICT(type_key,search_space_id) DO NOTHING"
        ), {"tk": type_key, "lb": label,
            "st": json.dumps(signal_tags), "sk": json.dumps(signal_kw),
            "sf": json.dumps(signal_formats), "now": now})

    # Vocabulary seed (extracto — completar con todos los de vocabulary.py al implementar)
    _vocab = [
        ("azure",              ["microsoft-azure"]),
        ("azure-data-factory", ["adf","data-factory"]),
        ("microsoft-fabric",   ["fabric","ms-fabric","msfabric"]),
        ("power-bi",           ["powerbi","pbi"]),
        ("etl",                ["elt"]),
        ("data-lake",          ["datalake","lakehouse"]),
        ("data-warehouse",     ["datawarehouse","dwh","warehouse"]),
        ("medallion",          ["medallion-architecture","bronze-silver-gold"]),
        ("bronze-layer",       ["capa-bronze"]),
        ("silver-layer",       ["capa-silver"]),
        ("gold-layer",         ["capa-gold"]),
        ("pyspark",            ["spark","apache-spark","py-spark"]),
        ("delta-lake",         ["delta","deltalake","delta-table"]),
        ("docker",             ["dockerfile","container","contenedor"]),
        ("kubernetes",         ["k8s","kube"]),
        ("python",             ["py","python3"]),
        ("fastapi",            ["fast-api"]),
        ("postgresql",         ["postgres","pg","postgresql-db"]),
        ("machine-learning",   ["ml","aprendizaje-automatico"]),
        ("llm",                ["large-language-model","modelo-lenguaje"]),
        ("rag",                ["retrieval-augmented-generation"]),
        ("embeddings",         ["embedding","vector-embedding"]),
        ("sql",                ["structured-query-language"]),
        ("rest-api",           ["rest","restful","api-rest"]),
        ("github-actions",     ["gha","github-ci"]),
        ("databricks",         ["azure-databricks"]),
        ("data-engineering",   ["data-eng","ingenieria-datos"]),
        ("data-pipeline",      ["pipeline","etl-pipeline"]),
        # IMPORTANTE: completar con el VOCABULARY completo de vocabulary.py al implementar
    ]
    for canonical, aliases in _vocab:
        bind.execute(sa.text(
            "INSERT INTO brain_vocabulary(id,canonical_tag,aliases,search_space_id,"
            "is_active,created_at,updated_at) "
            "VALUES(gen_random_uuid(),:ct,:al::jsonb,NULL,true,:now,:now) "
            "ON CONFLICT(canonical_tag,search_space_id) DO NOTHING"
        ), {"ct": canonical, "al": json.dumps(aliases), "now": now})


def downgrade() -> None:
    op.drop_index("ix_brain_doc_types_space",   table_name="brain_doc_types")
    op.drop_index("ix_brain_vocab_space",        table_name="brain_vocabulary")
    op.drop_index("ix_brain_subdomains_domain",  table_name="brain_subdomains")
    op.drop_index("ix_brain_domains_space",      table_name="brain_domains")
    op.drop_table("brain_vocabulary")
    op.drop_table("brain_entity_hints")
    op.drop_table("brain_doc_types")
    op.drop_table("brain_subdomains")
    op.drop_table("brain_domains")
```

### Modelos SQLAlchemy

**Fichero:** `surfsense_backend/app/db.py` — añadir al final junto a los modelos existentes. Usar los mismos imports de `Base`, `UUID`, `JSONB`, `func` que ya están en el fichero.

```python
# ── Brain Metadata Models ──────────────────────────────────────────────────

class BrainDomain(Base):
    __tablename__ = "brain_domains"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_key      = Column(String(80), nullable=False)
    label           = Column(String(200), nullable=False)
    description     = Column(Text, default="")
    signal_tags     = Column(JSONB, default=list)
    signal_kw       = Column(JSONB, default=list)
    search_space_id = Column(UUID(as_uuid=True), ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class BrainSubdomain(Base):
    __tablename__ = "brain_subdomains"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subdomain_key   = Column(String(80), nullable=False)
    domain_key      = Column(String(80), nullable=False)
    label           = Column(String(200), nullable=False)
    signal_tags     = Column(JSONB, default=list)
    signal_kw       = Column(JSONB, default=list)
    search_space_id = Column(UUID(as_uuid=True), ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class BrainDocType(Base):
    __tablename__ = "brain_doc_types"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type_key        = Column(String(80), nullable=False)
    label           = Column(String(200), nullable=False)
    signal_tags     = Column(JSONB, default=list)
    signal_kw       = Column(JSONB, default=list)
    signal_formats  = Column(JSONB, default=list)
    search_space_id = Column(UUID(as_uuid=True), ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class BrainEntityHint(Base):
    __tablename__ = "brain_entity_hints"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hint_key        = Column(String(150), nullable=False)
    domain_key      = Column(String(80), nullable=True)
    doc_type_key    = Column(String(80), nullable=True)
    label           = Column(String(300), nullable=False)
    patterns        = Column(JSONB, default=list)
    examples        = Column(JSONB, default=list)
    search_space_id = Column(UUID(as_uuid=True), ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

class BrainVocabulary(Base):
    __tablename__ = "brain_vocabulary"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_tag   = Column(String(150), nullable=False)
    aliases         = Column(JSONB, default=list)
    search_space_id = Column(UUID(as_uuid=True), ForeignKey("search_spaces.id", ondelete="CASCADE"), nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

> **Nota para F6/F7:** Estos modelos son la base del módulo admin Brain. Los endpoints CRUD (`GET/POST/PUT/DELETE /brain/admin/domains`, `/brain/admin/vocabulary`, etc.) se implementan en F6. En F1 solo se crean las tablas y se leen desde `BrainMetadataService`.

---

## F1.1 — DocumentProcessorFactory corregido (Día 1)

Crear el router que mapea extensión → extractor + preprocesador. Corrige los gaps de import path y añade soporte para texto directo (conectores API).

**Fichero:** `surfsense_backend/app/brain/processor_factory.py` ← NUEVO

```python
# processor_factory.py
"""
Router central del pipeline de tres fases.
Mapea extensión de fichero → (extractor, preprocesador, chunker, prompt).

CORRECCIONES respecto al diseño original:
  - Import correcto: app.brain.prompts.preprocessing (no app.brain.preprocessing)
  - extract_from_text(): path alternativo para conectores API (sin fichero en disco)
  - get_metadata_from_blocks(): llama a normalize_tags() del BrainMetadataService
  - search_space_id propagado a todos los bloques como campo de payload
"""
from pathlib import Path
from typing import Optional
from app.brain.extractors import get_extractor_for_extension
# CORRECCIÓN CRÍTICA: el path real en SecondBrainSense es prompts/preprocessing/
from app.brain.prompts.preprocessing import (
    py     as prep_py,
    sql    as prep_sql,
    pdf    as prep_pdf,
    docx   as prep_docx,
    pptx   as prep_pptx,
    xlsx   as prep_xlsx,
    ipynb  as prep_ipynb,
    html   as prep_html,
    md     as prep_md,
    txt    as prep_txt,
    csv    as prep_csv,
    json   as prep_json,
    xml    as prep_xml,
    drawio as prep_drawio,
)
from app.brain.prompts.type_specs import TYPE_SPECS
import logging

logger = logging.getLogger(__name__)


# Mapa extensión → módulo preprocesador
PREPROCESSOR_MAP = {
    ".py":          prep_py,
    ".sql":         prep_sql,
    ".pdf":         prep_pdf,
    ".docx":        prep_docx,
    ".doc":         prep_docx,
    ".pptx":        prep_pptx,
    ".ppt":         prep_pptx,
    ".xlsx":        prep_xlsx,
    ".xls":         prep_xlsx,
    ".ipynb":       prep_ipynb,
    ".html":        prep_html,
    ".htm":         prep_html,
    ".md":          prep_md,
    ".markdown":    prep_md,
    ".txt":         prep_txt,
    ".csv":         prep_csv,
    ".json":        prep_json,
    ".xml":         prep_xml,
    ".drawio":      prep_drawio,
    # Conectores API (tipos virtuales del ExtractorFactory)
    ".confluence":  prep_md,    # páginas Confluence → tratar como markdown estructurado
    ".jira_ticket": prep_txt,   # tickets Jira → texto plano con estructura
    ".github_file": prep_py,    # ficheros GitHub → inferir según contenido en F5
}


class DocumentProcessor:
    """Encapsula el pipeline de tres fases para un tipo de documento."""

    def __init__(self, extension: str):
        self.extension = extension.lower()
        if not self.extension.startswith("."):
            self.extension = f".{self.extension}"
        self.extractor = get_extractor_for_extension(self.extension)
        self.preprocessor_module = PREPROCESSOR_MAP.get(self.extension)
        self.type_spec = TYPE_SPECS.get(self.extension, {})

    def extract(self, file_path: str, search_space_id: str = "") -> list[dict]:
        """
        Fase 1 + Fase 2: extracción desde fichero en disco y limpieza universal.
        - El extractor tiene _CleaningExtractorWrapper aplicado → UniversalCleaner automático.
        - Propaga search_space_id a todos los bloques para multi-tenancy.
        """
        if self.extractor is None:
            raise ValueError(f"No hay extractor para extensión: {self.extension}")
        blocks = self.extractor.extract(file_path)
        blocks = self._inject_search_space(blocks, search_space_id)
        logger.info("[processor] Fase 1+2 completada: %d bloques, space=%s", len(blocks), search_space_id)
        return blocks

    def extract_from_text(
        self,
        text: str,
        search_space_id: str = "",
        metadata: dict | None = None,
    ) -> list[dict]:
        """
        Fase 1 + Fase 2 para conectores API: el texto ya viene procesado.
        Usado por ConnectorBridge (F5) para GitHub, Jira, Confluence, Slack...

        El extractor virtual (ConfluenceExtractor, JiraTicketExtractor, etc.)
        recibe el texto directamente, aplica UniversalCleaner y devuelve bloques.
        Si no hay extractor virtual, construye un bloque mínimo y aplica cleaner.
        """
        from app.brain.rag_lib.layer1_universal import UniversalCleaner
        cleaner = UniversalCleaner()

        if self.extractor is not None and hasattr(self.extractor, "extract_from_text"):
            # Extractor virtual con soporte nativo para texto directo
            blocks = self.extractor.extract_from_text(text, metadata=metadata or {})
        else:
            # Fallback: construir bloque único y aplicar cleaner
            result = cleaner.clean(text, content_type="text")
            blocks = [{
                "content":      result.text,
                "text":         result.text,
                "content_type": "text",
                "page":         1,
                "metadata": {
                    "quality_score":  result.quality_score,
                    "language":       result.language,
                    "sensitive_data": result.sensitive_data_detected,
                    **(metadata or {}),
                },
            }]

        blocks = self._inject_search_space(blocks, search_space_id)
        logger.info("[processor] Fase 1+2 (texto directo): %d bloques, space=%s",
                    len(blocks), search_space_id)
        return blocks

    def preprocess(self, blocks: list[dict]) -> str:
        """Fase 3: preprocesamiento semántico → processed_text con ##/###/####."""
        if self.preprocessor_module is None:
            logger.warning("[processor] Sin preprocesador para %s, usando fallback", self.extension)
            return "\n\n".join(b.get("content", "") for b in blocks if b.get("content"))

        raw_text = "\n\n".join(b.get("content", "") for b in blocks if b.get("content"))
        processed = self.preprocessor_module.preprocess(raw_text)
        logger.info("[processor] Fase 3 completada: %d chars con marcas semánticas", len(processed))
        return processed

    def get_quality_trigger(self) -> Optional[int]:
        """Umbral de chars para activar síntesis chunked (None = tipo homogéneo)."""
        return self.type_spec.get("quality_trigger")

    def get_metadata_from_blocks(
        self,
        blocks: list[dict],
        meta_service=None,          # BrainMetadataService opcional (F1.2)
        search_space_id: str = "",
    ) -> dict:
        """
        Extrae metadata de calidad de los bloques.
        Si se pasa meta_service, normaliza las tags candidatas contra el vocabulario.
        """
        quality_scores = [
            b.get("metadata", {}).get("quality_score", 1.0) for b in blocks
        ]
        has_pii = any(b.get("metadata", {}).get("sensitive_data") for b in blocks)
        languages = list({b.get("metadata", {}).get("language", "unknown") for b in blocks})

        raw_tags = []
        if meta_service is not None:
            # Normalizar tags candidatas contra vocabulario PostgreSQL
            for block in blocks:
                raw_tags.extend(block.get("metadata", {}).get("candidate_tags", []))
            normalized_tags = meta_service.normalize_tags(raw_tags, search_space_id=search_space_id)
        else:
            normalized_tags = []

        return {
            "avg_quality_score": sum(quality_scores) / len(quality_scores) if quality_scores else 1.0,
            "has_pii":           has_pii,
            "languages":         languages,
            "total_blocks":      len(blocks),
            "code_blocks":       sum(1 for b in blocks if b.get("content_type") == "code"),
            "normalized_tags":   normalized_tags,
        }

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _inject_search_space(blocks: list[dict], search_space_id: str) -> list[dict]:
        """
        Añade search_space_id a todos los bloques.
        Garantiza que el campo esté disponible para el payload de Qdrant (F2)
        y el filtrado RBAC en el router (F4).
        """
        if not search_space_id:
            return blocks
        for block in blocks:
            block.setdefault("metadata", {})["search_space_id"] = search_space_id
            block["search_space_id"] = search_space_id  # también en raíz para IngestRouter
        return blocks


class DocumentProcessorFactory:
    """Punto de entrada para obtener el processor correcto por nombre de fichero o extensión."""

    @staticmethod
    def get(filename: str) -> "DocumentProcessor":
        """
        Resuelve extensión a partir del nombre de fichero o extensión directa.
        Ejemplos: "mi_script.py", ".py", "ticket.jira_ticket"
        """
        ext = Path(filename).suffix.lower() if "." in filename else filename.lower()
        return DocumentProcessor(ext)

    @staticmethod
    def supported_extensions() -> list[str]:
        return list(PREPROCESSOR_MAP.keys())

    @staticmethod
    def is_supported(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in PREPROCESSOR_MAP
```

---

## F1.2 — BrainMetadataService (Día 1-2)

Servicio que reemplaza `masters.py` + `vocabulary.py` en tiempo de ejecución. Lee de PostgreSQL, mantiene caché en memoria y expone las operaciones de clasificación y normalización que `passport_builder.py` y `DocumentProcessorFactory` necesitan.

**Fichero:** `surfsense_backend/app/brain/metadata_service.py` ← NUEVO

```python
"""
brain/metadata_service.py
--------------------------
BrainMetadataService — reemplaza masters.py + vocabulary.py.

Lee dominios, subdomains, doc_types, entity_hints y vocabulary de PostgreSQL.
Caché en memoria por search_space_id: recarga automática cada CACHE_TTL_SECONDS.

Resolución multi-tenant:
  - Busca primero registros con search_space_id == space_id (específico del space).
  - Fallback a registros con search_space_id IS NULL (globales, seed por defecto).

Punto de entrada:
  meta_svc = BrainMetadataService(session_factory)
  await meta_svc.load(search_space_id="...")

  classification = meta_svc.classify_document(
      tags=["pyspark","delta"],
      keyphrases=["pipeline bronze"],
      title="pipeline_tte_etl.py",
      file_type=".py",
      search_space_id="...",
  )
  # → {"domain": "data-engineering", "subdomain": "...", "doc_type": "pipeline"}

  normalized = meta_svc.normalize_tags(["spark","datawarehouse"], search_space_id="...")
  # → ["pyspark", "data-warehouse"]
"""
import asyncio
import logging
import re
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import BrainDomain, BrainDocType, BrainVocabulary, BrainEntityHint

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = int(60 * 30)  # 30 min — reconfigurable vía env var
_MIN_SCORE_DOMAIN  = 2
_MIN_SCORE_DOCTYPE = 1.5


class _SpaceCache:
    """Caché en memoria para un search_space_id específico."""
    def __init__(self):
        self.domains:       list[dict] = []
        self.doc_types:     list[dict] = []
        self.vocab_alias:   dict[str, str] = {}   # alias → canonical
        self.vocab_set:     set[str] = set()       # todas las canónicas
        self.entity_hints:  list[dict] = []
        self.loaded_at:     float = 0.0

    def is_stale(self) -> bool:
        return (time.monotonic() - self.loaded_at) > CACHE_TTL_SECONDS


class BrainMetadataService:
    """
    Servicio singleton (por aplicación) para clasificación y vocabulario Brain.
    Instanciar una vez en el lifespan de FastAPI e inyectar vía dependencia.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory
        self._caches: dict[str, _SpaceCache] = {}
        self._global_cache: Optional[_SpaceCache] = None

    # ── Carga / refresco ──────────────────────────────────────────────────

    async def load(self, search_space_id: str = "") -> None:
        """Carga (o refresca) la caché para el search_space_id dado."""
        cache = self._caches.get(search_space_id) or _SpaceCache()
        if not cache.is_stale():
            return

        async with self._session_factory() as session:
            # Globales (NULL) + específicos del space → específico sobreescribe global
            domains   = await self._load_domains(session, search_space_id)
            doc_types = await self._load_doc_types(session, search_space_id)
            vocab     = await self._load_vocab(session, search_space_id)
            hints     = await self._load_hints(session, search_space_id)

        cache.domains       = domains
        cache.doc_types     = doc_types
        cache.vocab_alias   = vocab["alias"]
        cache.vocab_set     = vocab["canonical_set"]
        cache.entity_hints  = hints
        cache.loaded_at     = time.monotonic()

        self._caches[search_space_id] = cache
        log.info("[brain_meta] Caché cargada: space='%s' domains=%d vocab=%d",
                 search_space_id or "global", len(domains), len(vocab["canonical_set"]))

    async def reload(self, search_space_id: str = "") -> None:
        """Fuerza recarga ignorando TTL (útil tras edición desde admin UI)."""
        if search_space_id in self._caches:
            self._caches[search_space_id].loaded_at = 0.0
        await self.load(search_space_id)

    # ── Clasificación de documento ────────────────────────────────────────

    def classify_document(
        self,
        tags: list[str],
        keyphrases: list[str],
        title: str,
        file_type: str,
        search_space_id: str = "",
    ) -> dict:
        """
        Clasifica un documento en domain + doc_type.
        Lógica equivalente a masters.classify_document() pero leyendo de PostgreSQL.
        Retorna: {"domain": str, "doc_type": str}
        """
        cache = self._caches.get(search_space_id) or _SpaceCache()
        text_lower = " ".join([title] + tags + keyphrases).lower()

        # ── Dominio ──────────────────────────────────────────────────────
        best_domain, best_domain_score = "other", 0.0
        for d in cache.domains:
            score = sum(1.0 for t in (d.get("signal_tags") or []) if t in tags)
            score += sum(0.5 for kw in (d.get("signal_kw") or []) if kw in text_lower)
            if score > best_domain_score:
                best_domain_score, best_domain = score, d["domain_key"]

        if best_domain_score < _MIN_SCORE_DOMAIN:
            best_domain = "other"

        # ── Tipo documental ───────────────────────────────────────────────
        best_type, best_type_score = "document", 0.0
        for dt in cache.doc_types:
            score = sum(1.0 for t in (dt.get("signal_tags") or []) if t in tags)
            score += sum(0.5 for kw in (dt.get("signal_kw") or []) if kw in text_lower)
            score += sum(2.0 for fmt in (dt.get("signal_formats") or []) if fmt == file_type)
            if score > best_type_score:
                best_type_score, best_type = score, dt["type_key"]

        if best_type_score < _MIN_SCORE_DOCTYPE:
            best_type = "document"

        return {"domain": best_domain, "doc_type": best_type}

    # ── Vocabulario ───────────────────────────────────────────────────────

    def normalize_tags(
        self,
        raw_tags: list[str],
        cap: int = 10,
        search_space_id: str = "",
    ) -> list[str]:
        """
        Normaliza tags: variante → canónica. Filtra las que no están en vocabulario.
        Equivalente a vocabulary.normalize_tags() pero leyendo de PostgreSQL.
        """
        cache = self._caches.get(search_space_id) or _SpaceCache()
        seen, result = set(), []
        for tag in raw_tags:
            canonical = cache.vocab_alias.get(tag.lower(), tag.lower())
            if canonical in cache.vocab_set and canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
            if len(result) >= cap:
                break
        return result

    def match_text_to_vocab(
        self,
        text: str,
        cap: int = 20,
        search_space_id: str = "",
    ) -> list[str]:
        """
        Detecta qué tags del vocabulario aparecen en el texto.
        Equivalente a vocabulary.match_text_to_vocab().
        """
        cache = self._caches.get(search_space_id) or _SpaceCache()
        text_lower = text.lower()
        found = []
        for canonical in cache.vocab_set:
            if canonical in text_lower:
                found.append(canonical)
            if len(found) >= cap:
                break
        return found

    def get_entity_hints(
        self,
        domain_key: Optional[str] = None,
        doc_type_key: Optional[str] = None,
        search_space_id: str = "",
    ) -> list[dict]:
        """Devuelve los entity hints aplicables al dominio/tipo dado."""
        cache = self._caches.get(search_space_id) or _SpaceCache()
        return [
            h for h in cache.entity_hints
            if (h.get("domain_key") is None or h.get("domain_key") == domain_key)
            and (h.get("doc_type_key") is None or h.get("doc_type_key") == doc_type_key)
        ]

    # ── Loaders internos ──────────────────────────────────────────────────

    async def _load_domains(self, session: AsyncSession, space_id: str) -> list[dict]:
        rows = await session.execute(
            select(BrainDomain).where(
                BrainDomain.is_active == True,
                (BrainDomain.search_space_id == None) |
                (BrainDomain.search_space_id == space_id)
            )
        )
        # Específico sobreescribe global
        merged: dict[str, dict] = {}
        for d in rows.scalars().all():
            entry = {"domain_key": d.domain_key, "label": d.label,
                     "signal_tags": d.signal_tags or [], "signal_kw": d.signal_kw or []}
            # Si ya hay global y llega específico, sobreescribe
            if d.search_space_id is not None or d.domain_key not in merged:
                merged[d.domain_key] = entry
        return list(merged.values())

    async def _load_doc_types(self, session: AsyncSession, space_id: str) -> list[dict]:
        rows = await session.execute(
            select(BrainDocType).where(
                BrainDocType.is_active == True,
                (BrainDocType.search_space_id == None) |
                (BrainDocType.search_space_id == space_id)
            )
        )
        merged: dict[str, dict] = {}
        for dt in rows.scalars().all():
            entry = {"type_key": dt.type_key, "label": dt.label,
                     "signal_tags": dt.signal_tags or [], "signal_kw": dt.signal_kw or [],
                     "signal_formats": dt.signal_formats or []}
            if dt.search_space_id is not None or dt.type_key not in merged:
                merged[dt.type_key] = entry
        return list(merged.values())

    async def _load_vocab(self, session: AsyncSession, space_id: str) -> dict:
        rows = await session.execute(
            select(BrainVocabulary).where(
                BrainVocabulary.is_active == True,
                (BrainVocabulary.search_space_id == None) |
                (BrainVocabulary.search_space_id == space_id)
            )
        )
        alias_map: dict[str, str] = {}
        canonical_set: set[str] = set()
        seen: dict[str, bool] = {}
        for v in rows.scalars().all():
            canonical = v.canonical_tag.lower()
            # Específico sobreescribe global
            if v.search_space_id is not None or canonical not in seen:
                seen[canonical] = True
                canonical_set.add(canonical)
                alias_map[canonical] = canonical
                for alias in (v.aliases or []):
                    alias_map[alias.lower()] = canonical
        return {"alias": alias_map, "canonical_set": canonical_set}

    async def _load_hints(self, session: AsyncSession, space_id: str) -> list[dict]:
        rows = await session.execute(
            select(BrainEntityHint).where(
                BrainEntityHint.is_active == True,
                (BrainEntityHint.search_space_id == None) |
                (BrainEntityHint.search_space_id == space_id)
            )
        )
        return [
            {"hint_key": h.hint_key, "domain_key": h.domain_key,
             "doc_type_key": h.doc_type_key, "label": h.label,
             "patterns": h.patterns or [], "examples": h.examples or []}
            for h in rows.scalars().all()
        ]
```

### Inyección de dependencia FastAPI

**Fichero:** `surfsense_backend/app/brain/__init__.py` o `app/dependencies.py` ← añadir

```python
# Singleton del servicio, inicializado en el lifespan (ver F1.4)
_brain_metadata_service: Optional["BrainMetadataService"] = None

def get_brain_metadata_service() -> "BrainMetadataService":
    if _brain_metadata_service is None:
        raise RuntimeError("BrainMetadataService no inicializado. Verificar lifespan.")
    return _brain_metadata_service
```

---

## F1.3 — Integración en process_document_content (Día 2-3)

Punto de integración quirúrgico en SurfSense. Actualiza `process_document_content` para usar el pipeline de tres fases con `search_space_id` propagado y `BrainMetadataService` para normalización de tags.

**Fichero:** `surfsense_backend/app/utils/document_converters.py`

```python
# document_converters.py — cambios BrainSense F1
# Solo las funciones nuevas/modificadas — el resto del fichero queda intacto

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.brain.processor_factory import DocumentProcessorFactory

logger = logging.getLogger(__name__)


async def process_document_content(
    file_content: bytes,
    filename: str,
    metadata: dict,
    llm_client,
    search_space_id: str,            # REQUERIDO: propaga el space a todos los bloques
    synthesis_enabled: bool = True,
    vision_mode: str = "off",
    meta_service=None,               # BrainMetadataService (inyectado desde F1.2)
) -> dict:
    """
    Pipeline completo de procesamiento de documento.
    Sustituye la lógica ETL/Docling original de SurfSense.

    Retorna dict con: summary, processed_text, chunks, metadata, blocks.
    Los bloques tienen search_space_id en su metadata para garantizar
    que Qdrant (F2) y el router (F4) puedan filtrar por space.
    """
    if not search_space_id:
        raise ValueError("search_space_id es requerido para garantizar aislamiento multi-tenant")

    ext = Path(filename).suffix.lower()
    tmp_path = None

    try:
        # ── Escribir fichero temporal ──────────────────────────────────
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        # ── Verificar soporte ──────────────────────────────────────────
        if not DocumentProcessorFactory.is_supported(filename):
            logger.warning("[doc_conv] Extensión '%s' no soportada, usando fallback genérico", ext)
            return await _process_document_fallback(
                file_content, filename, metadata, llm_client, search_space_id
            )

        processor = DocumentProcessorFactory.get(filename)

        # ── FASE 1 + 2: Extracción y limpieza universal ───────────────
        blocks = processor.extract(tmp_path, search_space_id=search_space_id)

        # ── Metadata enriquecida con tags normalizadas ─────────────────
        block_metadata = processor.get_metadata_from_blocks(
            blocks,
            meta_service=meta_service,
            search_space_id=search_space_id,
        )
        metadata.update(block_metadata)
        metadata["search_space_id"] = search_space_id   # garantizar siempre presente
        metadata["filename"]         = filename
        metadata["extension"]        = ext

        # ── FASE 3: Preprocesamiento semántico ─────────────────────────
        processed_text = processor.preprocess(blocks)

        logger.info(
            "[doc_conv] Pipeline 3 fases: file='%s' space='%s' "
            "blocks=%d chars=%d quality=%.2f",
            filename, search_space_id, len(blocks),
            len(processed_text), metadata.get("avg_quality_score", 1.0),
        )

        # ── Síntesis LLM — placeholder hasta F3 ───────────────────────
        # En F3 se sustituye por: Planner + multi-call + PassportBuilder
        summary = processed_text if synthesis_enabled else processed_text[:2000]

        # ── Chunking semántico provisional — se sustituye en F3 ───────
        chunks = _simple_semantic_chunk(processed_text)

        return {
            "summary":        summary,
            "processed_text": processed_text,
            "chunks":         chunks,
            "metadata":       metadata,
            "blocks":         blocks,
        }

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def process_document_from_text(
    text: str,
    connector_type: str,            # ej: ".confluence", ".jira_ticket", ".github_file"
    title: str,
    search_space_id: str,
    metadata: dict | None = None,
    meta_service=None,
    synthesis_enabled: bool = True,
) -> dict:
    """
    Variante para contenido de conectores API: el texto ya está disponible,
    no hay fichero en disco. Usado por ConnectorBridge (F5).
    """
    if not search_space_id:
        raise ValueError("search_space_id es requerido")

    processor = DocumentProcessorFactory.get(connector_type)
    blocks = processor.extract_from_text(
        text, search_space_id=search_space_id, metadata=metadata or {}
    )
    block_metadata = processor.get_metadata_from_blocks(
        blocks, meta_service=meta_service, search_space_id=search_space_id
    )
    extra_meta = {**(metadata or {}), **block_metadata,
                  "search_space_id": search_space_id, "title": title}

    processed_text = processor.preprocess(blocks)
    summary = processed_text if synthesis_enabled else processed_text[:2000]
    chunks = _simple_semantic_chunk(processed_text)

    return {
        "summary":        summary,
        "processed_text": processed_text,
        "chunks":         chunks,
        "metadata":       extra_meta,
        "blocks":         blocks,
    }


def _simple_semantic_chunk(processed_text: str, max_chars: int = 1500) -> list[str]:
    """
    Chunking provisional que respeta marcas ##/### del preprocesador.
    Se sustituye por split_doc_text_for_chunked() en F3.
    """
    chunks, current, current_len = [], [], 0
    for line in processed_text.split("\n"):
        if (line.startswith("## ") or line.startswith("### ")) and current_len > 0:
            chunks.append("\n".join(current))
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += len(line)
            if current_len > max_chars and line == "":
                chunks.append("\n".join(current))
                current, current_len = [], 0
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


async def _process_document_fallback(
    file_content: bytes, filename: str, metadata: dict,
    llm_client, search_space_id: str,
) -> dict:
    """
    Pipeline genérico de SurfSense para extensiones no soportadas.
    Garantiza que search_space_id siempre está en metadata aunque no
    pase por el pipeline de tres fases.
    """
    # Invocar aquí el pipeline original de SurfSense (Docling/chunking genérico)
    # preservando search_space_id en la respuesta.
    metadata["search_space_id"] = search_space_id
    metadata["brain_pipeline"]  = False
    # TODO: llamar al pipeline genérico original de SurfSense
    return {"summary": "", "processed_text": "", "chunks": [],
            "metadata": metadata, "blocks": []}
```

---

## F1.4 — Infraestructura: lifespan, chunking y variables de entorno (Día 3)

Tres fixes de infraestructura necesarios antes de poder probar el pipeline en el stack real.

### F1.4.1 — BrainMetadataService + BrainWatcher en el lifespan de FastAPI

**Fichero:** `surfsense_backend/app/app.py` — añadir dentro del `lifespan` existente

```python
# En app/app.py — importar al inicio del fichero
from app.brain.metadata_service import BrainMetadataService
import app.brain as brain_module   # para asignar el singleton

# Dentro del @asynccontextmanager async def lifespan(app: FastAPI):
# AÑADIR después de la inicialización existente (init_otel, create_db_and_tables, etc.):

    # ── BrainMetadataService: cargar caché global ──────────────────────
    brain_meta_svc = BrainMetadataService(session_factory=get_async_session_factory())
    try:
        await asyncio.wait_for(brain_meta_svc.load(search_space_id=""), timeout=15.0)
        brain_module._brain_metadata_service = brain_meta_svc
        logger.info("[startup] BrainMetadataService cargado correctamente")
    except Exception:
        logger.warning("[startup] BrainMetadataService falló en carga inicial (non-fatal)", exc_info=True)

    # ── BrainWatcher: re-indexar .md editados manualmente ─────────────
    brain_watcher_task = None
    if os.getenv("PASSPORT_WATCHER_ENABLED", "true").lower() == "true":
        try:
            from app.brain.brain_watcher import BrainWatcher
            watcher = BrainWatcher()
            brain_watcher_task = asyncio.create_task(watcher.watch())
            logger.info("[startup] BrainWatcher iniciado — monitorizando %s",
                        os.getenv("BRAIN_DATA_PATH", "/data/brain"))
        except Exception:
            logger.warning("[startup] BrainWatcher no disponible (non-fatal)", exc_info=True)

    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    if brain_watcher_task and not brain_watcher_task.done():
        brain_watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await brain_watcher_task
```

### F1.4.2 — chunking.py: resolver dependencia flotante

`ingest_router.py` en SecondBrainSense tiene `from chunking import get_chunks` que referencia el `chunking.py` de la raíz del proyecto original de Second Brain — **no existe en SecondBrainSense**.

**Solución:** mover/crear en la ubicación correcta.

**Acción:** Verificar si `chunking.py` existe en:
1. `surfsense_backend/chunking.py` (raíz del backend) — si está, añadir a `PYTHONPATH`
2. `surfsense_backend/app/brain/chunking.py` — si no está, crear como re-export

```python
# surfsense_backend/app/brain/chunking.py ← CREAR si no existe
"""
brain/chunking.py
-----------------
Wrapper sobre el chunking genérico de SurfSense para uso desde el pipeline Brain.
En F3 se implementará split_doc_text_for_chunked() que sustituirá a este módulo.
"""
from app.indexing_pipeline.document_chunker import chunk_text, chunk_text_hybrid


def get_chunks(text: str, strategy: str = "paragraph", max_chars: int = 1500) -> list[str]:
    """
    Punto de entrada compatible con el import original de Second Brain.
    Delega en el chunker de SurfSense hasta que F3 implemente el chunker semántico.
    """
    if strategy == "semantic":
        return chunk_text_hybrid(text, max_chunk_chars=max_chars)
    return chunk_text(text, max_chunk_chars=max_chars)
```

**Actualizar el import en `ingest_router.py`:**
```python
# surfsense_backend/app/brain/ingest_router.py
# CAMBIAR: from chunking import get_chunks
# POR:
from app.brain.chunking import get_chunks
```

### F1.4.3 — Variables de entorno Brain en `.env`

Añadir al `.env` (o `.env.example`) del proyecto:

```bash
# ── Pipeline Brain ─────────────────────────────────────────────────────────
SYNTHESIS_ENABLED=true
SYNTHESIS_HYBRID=true          # true = passport_builder determinista + LLM focalizado
SYNTHESIS_V5_ENABLED=true      # true = planner v5 multi-call activo
QUALITY_TRIGGER_ENABLED=true   # true = chunked mode si doc > quality_trigger chars
VISION_MODE_DEFAULT=off        # off | auto | force (para PDF/PPTX en F3)

# ── Modelos LLM diferenciados ───────────────────────────────────────────────
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5-coder:7b            # síntesis L1 (pasaporte)
OLLAMA_CHAT_MODEL=qwen2.5-coder:3b       # chat L2 (conversacional)
SYNTHESIS_MODEL=qwen2.5-coder:7b

# ── Modelos de embedding diferenciados ─────────────────────────────────────
EMBED_MODEL=nomic-embed-text             # brain + knowledge (768d)
EMBED_MODEL_CODE=qwen3-embedding:4b      # code (2560d)

# ── Qdrant ─────────────────────────────────────────────────────────────────
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# ── Persistencia pasaportes .md ────────────────────────────────────────────
BRAIN_DATA_PATH=/data/brain
PASSPORT_WATCHER_ENABLED=true
PASSPORT_PERSIST=true

# ── Brain Metadata Service ─────────────────────────────────────────────────
BRAIN_METADATA_CACHE_TTL=1800            # segundos (30 min)
```

---

## F1.5 — Adaptar el task de Celery (Día 3-4)

SurfSense despacha la ingesta como tarea Celery. Actualizar el task para que llame al nuevo pipeline y propague `search_space_id`.

**Fichero:** `surfsense_backend/app/tasks/document/process_file_upload_task.py`

```python
# Solo la sección modificada — el resto del task queda intacto

from app.utils.document_converters import process_document_content
from app.brain.processor_factory import DocumentProcessorFactory
import app.brain as brain_module   # acceso al singleton BrainMetadataService

@celery_app.task(name="process_file_upload", bind=True, max_retries=3)
def process_file_upload_task(
    self, document_id: str, file_path: str, filename: str,
    search_space_id: str, metadata: dict, **kwargs
):
    """Task Celery para ingesta de documento — versión BrainSense F1."""
    try:
        ext = Path(filename).suffix.lower()

        # Verificar soporte antes de leer el fichero
        if not DocumentProcessorFactory.is_supported(filename):
            logger.warning("[task] Extensión '%s' sin soporte Brain, pipeline genérico", ext)
            # Invocar pipeline original de SurfSense como fallback
            return _process_with_surfsense_pipeline(document_id, file_path, filename,
                                                    search_space_id, metadata)

        with open(file_path, "rb") as f:
            content = f.read()

        # Obtener BrainMetadataService (puede ser None si no está inicializado en worker)
        meta_svc = brain_module._brain_metadata_service

        result = asyncio.run(process_document_content(
            file_content=content,
            filename=filename,
            metadata=metadata,
            llm_client=get_llm_client(),      # cliente LLM de SurfSense
            search_space_id=search_space_id,  # REQUERIDO
            synthesis_enabled=config.SYNTHESIS_ENABLED,
            meta_service=meta_svc,
        ))

        # El guardado en PostgreSQL + Qdrant se modifica en F2/F3
        # Por ahora: guardar processed_text y chunks en PostgreSQL (sin vectorizar en Qdrant)
        _save_document_result(document_id, result)

        logger.info("[task] Pipeline F1 completado: doc_id=%s file=%s space=%s",
                    document_id, filename, search_space_id)

    except Exception as exc:
        logger.error("[task] Error en pipeline F1: doc_id=%s error=%s",
                     document_id, exc, exc_info=True)
        self.retry(exc=exc, countdown=60 * (self.request.retries + 1))  # backoff lineal
```

---

## F1.6 — Tests de la Fase 1 (Día 4-5)

Cobertura ampliada respecto al diseño original: incluye tests para `BrainMetadataService`, propagación de `search_space_id`, y el path de texto directo para conectores.

**Fichero:** `tests/brain/test_pipeline_f1.py`

```python
# tests/brain/test_pipeline_f1.py

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.brain.processor_factory import DocumentProcessorFactory

FIXTURES_DIR = Path("tests/fixtures")


class TestImportPaths:
    """Verifica que los imports del pipeline son correctos (gap crítico)."""

    def test_processor_factory_imports_without_error(self):
        """DocumentProcessorFactory importa sin errores de módulo."""
        from app.brain.processor_factory import DocumentProcessorFactory, PREPROCESSOR_MAP
        assert len(PREPROCESSOR_MAP) > 15

    def test_preprocessing_modules_importable(self):
        """Los módulos de preprocessing están en el path correcto."""
        from app.brain.prompts.preprocessing import py, sql, pdf, docx, md
        assert all(hasattr(m, "preprocess") for m in [py, sql, pdf, docx, md])

    def test_type_specs_importable(self):
        """TYPE_SPECS se importa sin error."""
        from app.brain.prompts.type_specs import TYPE_SPECS
        assert ".py" in TYPE_SPECS or "py" in TYPE_SPECS


class TestPipelineTresFases:

    def test_python_file_pipeline(self):
        """Un .py pasa las 3 fases y genera processed_text con marcas semánticas."""
        processor = DocumentProcessorFactory.get("ejemplo.py")
        blocks = processor.extract(str(FIXTURES_DIR / "ejemplo.py"), search_space_id="space-test")

        assert len(blocks) > 0
        assert all("quality_score" in b.get("metadata", {}) for b in blocks)
        # search_space_id propagado
        assert all(b.get("search_space_id") == "space-test" for b in blocks)

        processed = processor.preprocess(blocks)
        assert "## " in processed or "### " in processed

    def test_sql_file_pipeline(self):
        """Un .sql con PROCEDURE genera marcas ### PROCEDURE."""
        processor = DocumentProcessorFactory.get("sp_ejemplo.sql")
        blocks = processor.extract(str(FIXTURES_DIR / "sp_ejemplo.sql"), search_space_id="space-test")
        processed = processor.preprocess(blocks)
        assert "### PROCEDURE" in processed or "### " in processed

    def test_pdf_pipeline(self):
        """Un PDF genera processed_text no vacío."""
        processor = DocumentProcessorFactory.get("documento.pdf")
        blocks = processor.extract(str(FIXTURES_DIR / "documento.pdf"), search_space_id="space-test")
        processed = processor.preprocess(blocks)
        assert len(processed) > 100

    def test_universal_cleaner_applied(self):
        """Los bloques tienen quality_score y language en metadata."""
        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(str(FIXTURES_DIR / "nota.md"), search_space_id="space-test")
        for block in blocks:
            meta = block.get("metadata", {})
            assert "quality_score" in meta, "UniversalCleaner no aplicado"
            assert "language" in meta

    def test_search_space_id_propagated(self):
        """search_space_id aparece en bloques y en su metadata."""
        processor = DocumentProcessorFactory.get("nota.md")
        blocks = processor.extract(str(FIXTURES_DIR / "nota.md"), search_space_id="test-space-123")
        for b in blocks:
            assert b.get("search_space_id") == "test-space-123"
            assert b.get("metadata", {}).get("search_space_id") == "test-space-123"

    def test_extract_from_text_connector(self):
        """extract_from_text funciona para conectores API sin fichero."""
        processor = DocumentProcessorFactory.get(".confluence")
        blocks = processor.extract_from_text(
            text="# Página Confluence\n\nContenido de la página de prueba.",
            search_space_id="space-conn",
            metadata={"source_url": "https://confluence.example.com/pages/123"},
        )
        assert len(blocks) > 0
        assert all(b.get("search_space_id") == "space-conn" for b in blocks)

    def test_unsupported_extension_fallback(self):
        """Extensión no soportada devuelve processor sin lanzar excepción."""
        processor = DocumentProcessorFactory.get("fichero.xyz")
        assert processor is not None
        assert not DocumentProcessorFactory.is_supported("fichero.xyz")

    def test_quality_trigger_values(self):
        """Los quality_triggers están bien configurados por tipo."""
        for ext in [".docx", ".pdf", ".md"]:
            p = DocumentProcessorFactory.get(f"doc{ext}")
            assert p.get_quality_trigger() == 32000, f"{ext} debería tener quality_trigger=32000"
        for ext in [".py", ".sql"]:
            p = DocumentProcessorFactory.get(f"doc{ext}")
            assert p.get_quality_trigger() is None, f"{ext} homogéneo no debería tener quality_trigger"


class TestBrainMetadataService:
    """Tests del servicio de metadata (BrainMetadataService)."""

    @pytest.fixture
    def meta_service_with_mock_db(self):
        """Crea un BrainMetadataService con caché pre-cargada sin DB real."""
        from app.brain.metadata_service import BrainMetadataService, _SpaceCache
        import time
        svc = BrainMetadataService(session_factory=MagicMock())
        cache = _SpaceCache()
        cache.domains = [
            {"domain_key": "data-engineering", "label": "Data Engineering",
             "signal_tags": ["pyspark","etl","delta-lake"], "signal_kw": ["pipeline","ingesta"]},
            {"domain_key": "development", "label": "Software Development",
             "signal_tags": ["python","fastapi"], "signal_kw": ["api","backend"]},
        ]
        cache.doc_types = [
            {"type_key": "pipeline", "label": "Pipeline ETL",
             "signal_tags": ["etl","pipeline"], "signal_kw": ["ingesta","transformación"],
             "signal_formats": [".py",".json"]},
            {"type_key": "notebook", "label": "Notebook",
             "signal_tags": ["jupyter","notebook"], "signal_kw": ["análisis"],
             "signal_formats": [".ipynb"]},
        ]
        cache.vocab_alias = {
            "spark": "pyspark", "apache-spark": "pyspark",
            "adf": "azure-data-factory", "delta": "delta-lake",
            "pyspark": "pyspark", "delta-lake": "delta-lake",
        }
        cache.vocab_set = {"pyspark", "delta-lake", "azure-data-factory", "python", "fastapi"}
        cache.entity_hints = []
        cache.loaded_at = time.monotonic()
        svc._caches[""] = cache
        return svc

    def test_classify_document_data_engineering(self, meta_service_with_mock_db):
        """Documento con tags de data engineering se clasifica correctamente."""
        result = meta_service_with_mock_db.classify_document(
            tags=["pyspark", "delta-lake", "etl"],
            keyphrases=["pipeline ingesta"],
            title="pipeline_tte_etl.py",
            file_type=".py",
            search_space_id="",
        )
        assert result["domain"] == "data-engineering"
        assert result["doc_type"] == "pipeline"

    def test_normalize_tags_resolves_aliases(self, meta_service_with_mock_db):
        """normalize_tags convierte aliases a canónicas."""
        result = meta_service_with_mock_db.normalize_tags(
            ["spark", "delta", "adf"], search_space_id=""
        )
        assert "pyspark" in result
        assert "delta-lake" in result
        assert "azure-data-factory" in result

    def test_classify_unknown_returns_other(self, meta_service_with_mock_db):
        """Documento sin señales suficientes → domain=other."""
        result = meta_service_with_mock_db.classify_document(
            tags=[], keyphrases=[], title="random_document.pdf",
            file_type=".pdf", search_space_id="",
        )
        assert result["domain"] == "other"


class TestSearchSpaceIsolation:
    """Verifica que search_space_id se propaga y aísla correctamente."""

    def test_process_document_requires_search_space_id(self):
        """process_document_content falla sin search_space_id."""
        import asyncio
        from app.utils.document_converters import process_document_content
        with pytest.raises((ValueError, TypeError)):
            asyncio.run(process_document_content(
                file_content=b"test", filename="test.md",
                metadata={}, llm_client=None,
                search_space_id="",   # vacío → debe fallar
            ))

    def test_blocks_contain_search_space_id(self):
        """Todos los bloques del pipeline tienen search_space_id en su metadata."""
        processor = DocumentProcessorFactory.get(".md")
        blocks = processor.extract_from_text(
            "# Título\n\nContenido.", search_space_id="abc-123"
        )
        assert all(b.get("search_space_id") == "abc-123" for b in blocks)
```

---

## Checklist F1

### F1.0 — Schema PostgreSQL
- [ ] Migración `160_brain_metadata_tables.py` creada y ejecutada sin errores
- [ ] Seed de dominios (11 dominios), doc_types (8 tipos) y vocabulary (~25 entradas) cargado
- [ ] Modelos `BrainDomain`, `BrainSubdomain`, `BrainDocType`, `BrainEntityHint`, `BrainVocabulary` añadidos a `db.py`
- [ ] `alembic upgrade head` en el contenedor `migrations` — sin errores ni conflictos
- [ ] Verificar en pgAdmin: tablas `brain_*` con datos seed

### F1.1 — DocumentProcessorFactory
- [ ] `processor_factory.py` creado en `surfsense_backend/app/brain/`
- [ ] Import correcto: `from app.brain.prompts.preprocessing import py, sql, pdf...`
- [ ] `extract_from_text()` implementado y funcional
- [ ] `search_space_id` propagado en `_inject_search_space()`
- [ ] `is_supported()` disponible para verificar extensiones

### F1.2 — BrainMetadataService
- [ ] `metadata_service.py` creado en `surfsense_backend/app/brain/`
- [ ] `classify_document()` equivalente a `masters.classify_document()`
- [ ] `normalize_tags()` equivalente a `vocabulary.normalize_tags()`
- [ ] Caché en memoria con TTL configurable via `BRAIN_METADATA_CACHE_TTL`
- [ ] Resolución multi-tenant: específico sobreescribe global (NULL)
- [ ] Singleton expuesto como `get_brain_metadata_service()` para inyección FastAPI

### F1.3 — process_document_content
- [ ] `process_document_content()` actualizado: `search_space_id` requerido, error si vacío
- [ ] `process_document_from_text()` para conectores API añadido
- [ ] `_process_document_fallback()` para extensiones no soportadas
- [ ] `_simple_semantic_chunk()` funcionando — respeta marcas `##/###`
- [ ] Log estructurado con `space`, `blocks`, `chars`, `quality`

### F1.4 — Infraestructura
- [ ] `BrainMetadataService` inicializado en el `lifespan` de `app.py`
- [ ] `BrainWatcher` arrancado como `asyncio.create_task` si `PASSPORT_WATCHER_ENABLED=true`
- [ ] `app/brain/chunking.py` creado con wrapper sobre `document_chunker`
- [ ] Import `from chunking import get_chunks` corregido en `ingest_router.py`
- [ ] Variables de entorno Brain documentadas en `.env.example`

### F1.5 — Task Celery
- [ ] Task de ingesta llama a `process_document_content` con `search_space_id`
- [ ] Fallback al pipeline genérico para extensiones no soportadas
- [ ] Backoff lineal en reintentos (countdown escalado)
- [ ] `BrainMetadataService` inyectado desde módulo singleton

### F1.6 — Tests
- [ ] Tests de import paths pasando (`TestImportPaths`)
- [ ] Tests de pipeline para `.py`, `.sql`, `.pdf`, `.md` pasando
- [ ] `search_space_id` verificado en todos los bloques de todos los tests
- [ ] `BrainMetadataService.classify_document()` verificado con mock de BD
- [ ] `normalize_tags()` verificado con aliases
- [ ] `TestSearchSpaceIsolation` — verificado que `search_space_id` vacío falla
- [ ] `extract_from_text()` funcional para `.confluence`
- [ ] Cobertura > 80% en `processor_factory.py` y `metadata_service.py`

### Criterio de aceptación global F1
- [ ] Un `.py` subido por la UI de SurfSense → logs muestran `Pipeline 3 fases: blocks=N chars=M`
- [ ] Un `.md` subido → `processed_text` contiene marcas `##/###` verificables en logs
- [ ] `search_space_id` presente en todos los bloques y en el `metadata` de la respuesta
- [ ] Extensión `.xyz` no soportada → fallback graceful, sin error 500
- [ ] `docker compose logs backend | grep "BrainMetadataService"` → `cargado correctamente`
- [ ] Rama `integration/f1-pipeline` mergeada a `main`

---

**Anterior:** [F0 — Preparación](./F0-preparacion.md)  
**Siguiente:** [F2 — Qdrant y Tres Colecciones](./F2-qdrant-colecciones.md)
