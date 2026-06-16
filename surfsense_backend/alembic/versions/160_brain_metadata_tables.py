"""Brain metadata tables: domains, subdomains, doc_types, entity_hints, vocabulary

Revision ID: 160
Revises: 159
Create Date: 2026-06

Convierte los maestros de clasificación (masters.py) y el vocabulario controlado
(vocabulary.py) del Second Brain en tablas PostgreSQL de primera clase.

Decisiones de diseño:
  - search_space_id es Integer (FK a searchspaces.id), NO UUID — igual que el
    resto del schema de SurfSense.
  - search_space_id = NULL → registro global (seed por defecto, aplica a todos).
  - Un registro con search_space_id = <id> sobreescribe el global para ese space.
  - PK autoincrement Integer, igual que el resto de modelos SurfSense.
"""

from collections.abc import Sequence

import json
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "160"
down_revision: str | None = "159"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── brain_domains ──────────────────────────────────────────────────────────
    op.create_table(
        "brain_domains",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain_key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("signal_tags", JSONB, server_default="[]"),
        sa.Column("signal_kw", JSONB, server_default="[]"),
        sa.Column(
            "search_space_id",
            sa.Integer,
            sa.ForeignKey("searchspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("domain_key", "search_space_id", name="uq_brain_domain_key_space"),
    )
    op.create_index("ix_brain_domains_space", "brain_domains", ["search_space_id"])

    # ── brain_subdomains ───────────────────────────────────────────────────────
    op.create_table(
        "brain_subdomains",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("subdomain_key", sa.String(80), nullable=False),
        sa.Column("domain_key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("signal_tags", JSONB, server_default="[]"),
        sa.Column("signal_kw", JSONB, server_default="[]"),
        sa.Column(
            "search_space_id",
            sa.Integer,
            sa.ForeignKey("searchspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("subdomain_key", "search_space_id", name="uq_brain_subdomain_key_space"),
    )
    op.create_index("ix_brain_subdomains_domain", "brain_subdomains", ["domain_key"])

    # ── brain_doc_types ────────────────────────────────────────────────────────
    op.create_table(
        "brain_doc_types",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("type_key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("signal_tags", JSONB, server_default="[]"),
        sa.Column("signal_kw", JSONB, server_default="[]"),
        sa.Column("signal_formats", JSONB, server_default="[]"),
        sa.Column(
            "search_space_id",
            sa.Integer,
            sa.ForeignKey("searchspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("type_key", "search_space_id", name="uq_brain_doc_type_key_space"),
    )
    op.create_index("ix_brain_doc_types_space", "brain_doc_types", ["search_space_id"])

    # ── brain_entity_hints ─────────────────────────────────────────────────────
    op.create_table(
        "brain_entity_hints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("hint_key", sa.String(150), nullable=False),
        sa.Column("domain_key", sa.String(80), nullable=True),
        sa.Column("doc_type_key", sa.String(80), nullable=True),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("patterns", JSONB, server_default="[]"),
        sa.Column("examples", JSONB, server_default="[]"),
        sa.Column(
            "search_space_id",
            sa.Integer,
            sa.ForeignKey("searchspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("hint_key", "search_space_id", name="uq_brain_entity_hint_key_space"),
    )

    # ── brain_vocabulary ───────────────────────────────────────────────────────
    op.create_table(
        "brain_vocabulary",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("canonical_tag", sa.String(150), nullable=False),
        sa.Column("aliases", JSONB, server_default="[]"),
        sa.Column(
            "search_space_id",
            sa.Integer,
            sa.ForeignKey("searchspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_tag", "search_space_id", name="uq_brain_vocab_tag_space"),
    )
    op.create_index("ix_brain_vocab_space", "brain_vocabulary", ["search_space_id"])

    # ── Seed global (search_space_id = NULL) ───────────────────────────────────
    bind = op.get_bind()
    _seed_brain_metadata(bind)


def _seed_brain_metadata(bind) -> None:
    """
    Inserta datos globales extraídos de masters.py + vocabulary.py del Second Brain.

    Nota: asyncpg no admite la sintaxis ':param::jsonb' (parámetro nombrado + cast
    PostgreSQL en la misma expresión). La solución es embeber el JSON directamente
    en la query como literal usando text() con el valor ya interpolado de forma
    segura (json.dumps garantiza el escaping correcto).
    """

    def _insert_domain(dk, lb, st, sk):
        bind.execute(sa.text(
            f"INSERT INTO brain_domains(domain_key,label,signal_tags,signal_kw,"
            f"search_space_id,is_active,created_at,updated_at) "
            f"VALUES('{dk}','{lb}','{json.dumps(st)}'::jsonb,'{json.dumps(sk)}'::jsonb,"
            f"NULL,true,now(),now()) "
            f"ON CONFLICT(domain_key,search_space_id) DO NOTHING"
        ))

    def _insert_doc_type(tk, lb, st, sk, sf):
        bind.execute(sa.text(
            f"INSERT INTO brain_doc_types(type_key,label,signal_tags,signal_kw,"
            f"signal_formats,search_space_id,is_active,created_at,updated_at) "
            f"VALUES('{tk}','{lb}','{json.dumps(st)}'::jsonb,'{json.dumps(sk)}'::jsonb,"
            f"'{json.dumps(sf)}'::jsonb,NULL,true,now(),now()) "
            f"ON CONFLICT(type_key,search_space_id) DO NOTHING"
        ))

    def _insert_vocab(ct, al):
        bind.execute(sa.text(
            f"INSERT INTO brain_vocabulary(canonical_tag,aliases,search_space_id,"
            f"is_active,created_at,updated_at) "
            f"VALUES('{ct}','{json.dumps(al)}'::jsonb,NULL,true,now(),now()) "
            f"ON CONFLICT(canonical_tag,search_space_id) DO NOTHING"
        ))

    # ── Dominios ───────────────────────────────────────────────────────────────
    _insert_domain("data-engineering", "Data Engineering",
        ["pyspark","etl","data-pipeline","medallion","delta-lake","airflow","kafka","dbt","data-lake","data-warehouse"],
        ["pipeline","ingesta","etl","medallion","bronze","silver","gold","lakehouse"])
    _insert_domain("cloud-azure", "Cloud Azure",
        ["azure","microsoft-fabric","azure-data-factory","databricks","power-bi","azure-synapse","azure-functions"],
        ["azure","fabric","synapse","databricks","power bi","adf","data factory"])
    _insert_domain("cloud-aws", "Cloud AWS",
        ["aws","s3","aws-glue","redshift","sagemaker","dynamodb","cloudformation","ecs","eks"],
        ["amazon","aws","s3","lambda","redshift","sagemaker"])
    _insert_domain("development", "Software Development",
        ["python","fastapi","rest-api","typescript","react","nodejs","pytest","pydantic","asyncio"],
        ["api","endpoint","frontend","backend","desarrollo","modulo","libreria","framework"])
    _insert_domain("data-science-ml", "Data Science and ML",
        ["machine-learning","llm","rag","embeddings","vector-database","ollama","huggingface","langchain"],
        ["modelo","embedding","clasificacion","neural","inteligencia artificial","prediccion"])
    _insert_domain("databases", "Databases",
        ["sql","postgresql","mongodb","redis","sqlserver","oracle-db","sqlite","qdrant"],
        ["base de datos","tabla","query","indice","database","schema","stored procedure"])
    _insert_domain("architecture", "Architecture and Design",
        ["architecture","solution-architecture","design-pattern","data-model","api-design","diagram","event-driven"],
        ["arquitectura","diseno","patron","modelo de datos","diagrama","flujo","componente"])
    _insert_domain("devops-infra", "DevOps and Infrastructure",
        ["docker","kubernetes","terraform","ansible","ci-cd","github-actions","monitoring","prometheus","grafana"],
        ["deploy","despliegue","contenedor","infra","pipeline ci","kubernetes"])
    _insert_domain("testing", "Testing and QA",
        ["testing","unit-testing","e2e-testing","selenium","cypress","bdd","tdd","xray","pytest"],
        ["pruebas","test","caso de prueba","plan de pruebas","qa","automatizacion"])
    _insert_domain("project-management", "Project Management",
        ["jira","confluence","xray","test-management","kanban","scrum"],
        ["proyecto","sprint","backlog","incidencia","ticket","epic","story","kanban","scrum"])
    _insert_domain("business-intelligence", "Business Intelligence",
        ["power-bi","reporting","kpi","dashboard","olap","powerbi"],
        ["reporte","dashboard","kpi","analisis de negocio","indicador"])

    # ── Tipos documentales ─────────────────────────────────────────────────────
    _insert_doc_type("notebook", "Notebook / Analisis",
        ["jupyter","notebook","analisis","exploracion","visualizacion"],
        ["notebook","ipynb","analisis exploratorio","jupyter"], [".ipynb"])
    _insert_doc_type("pipeline", "Pipeline / ETL",
        ["pipeline","etl","orchestration","data-factory","airflow"],
        ["pipeline","etl","ingesta","transformacion","orquestacion"], [".py",".json",".drawio",".xml"])
    _insert_doc_type("procedure", "Stored Procedure / SQL",
        ["sql","stored-procedure","query","ddl","dml"],
        ["procedure","stored procedure","sp_","usp_","query","select","insert"], [".sql"])
    _insert_doc_type("documentation", "Documentacion tecnica",
        ["documentation","readme","wiki","guide","tutorial"],
        ["documentacion","guia","manual","tutorial","readme","wiki"], [".md",".docx",".pdf",".html"])
    _insert_doc_type("configuration", "Configuracion / Schema",
        ["configuration","config","schema","settings","parameters"],
        ["configuracion","config","parametros","settings","schema"], [".json",".xml",".yaml",".yml"])
    _insert_doc_type("diagram", "Diagrama / Arquitectura",
        ["diagram","architecture","flow","drawio","visio"],
        ["diagrama","arquitectura","flujo","diseno","draw.io"], [".drawio",".xml"])
    _insert_doc_type("presentation", "Presentacion",
        ["presentation","slides","deck","powerpoint"],
        ["presentacion","diapositivas","slides","deck"], [".pptx",".ppt"])
    _insert_doc_type("spreadsheet", "Hoja de calculo",
        ["spreadsheet","excel","table","data"],
        ["hoja de calculo","excel","tabla","datos","xlsx"], [".xlsx",".xls",".csv"])

    # ── Vocabulario controlado ─────────────────────────────────────────────────
    _insert_vocab("azure",               ["microsoft-azure"])
    _insert_vocab("azure-data-factory",  ["adf","data-factory"])
    _insert_vocab("microsoft-fabric",    ["fabric","ms-fabric","msfabric"])
    _insert_vocab("power-bi",            ["powerbi","pbi"])
    _insert_vocab("etl",                 ["elt"])
    _insert_vocab("data-lake",           ["datalake","lakehouse"])
    _insert_vocab("data-warehouse",      ["datawarehouse","dwh","warehouse"])
    _insert_vocab("medallion",           ["medallion-architecture","bronze-silver-gold"])
    _insert_vocab("bronze-layer",        ["capa-bronze"])
    _insert_vocab("silver-layer",        ["capa-silver"])
    _insert_vocab("gold-layer",          ["capa-gold"])
    _insert_vocab("pyspark",             ["spark","apache-spark","py-spark"])
    _insert_vocab("delta-lake",          ["delta","deltalake","delta-table"])
    _insert_vocab("docker",              ["dockerfile","container","contenedor"])
    _insert_vocab("kubernetes",          ["k8s","kube"])
    _insert_vocab("python",              ["py","python3"])
    _insert_vocab("fastapi",             ["fast-api"])
    _insert_vocab("postgresql",          ["postgres","pg","postgresql-db"])
    _insert_vocab("machine-learning",    ["ml","aprendizaje-automatico"])
    _insert_vocab("llm",                 ["large-language-model","modelo-lenguaje"])
    _insert_vocab("rag",                 ["retrieval-augmented-generation"])
    _insert_vocab("embeddings",          ["embedding","vector-embedding"])
    _insert_vocab("sql",                 ["structured-query-language"])
    _insert_vocab("rest-api",            ["rest","restful","api-rest"])
    _insert_vocab("github-actions",      ["gha","github-ci"])
    _insert_vocab("databricks",          ["azure-databricks"])
    _insert_vocab("data-engineering",    ["data-eng","ingenieria-datos"])
    _insert_vocab("data-pipeline",       ["pipeline","etl-pipeline"])
    _insert_vocab("azure-synapse",       ["synapse","synapse-analytics"])
    _insert_vocab("airflow",             ["apache-airflow"])
    _insert_vocab("dbt",                 ["data-build-tool"])
    _insert_vocab("qdrant",              ["qdrant-db","vector-db"])
    _insert_vocab("pytest",              ["py-test"])
    _insert_vocab("terraform",           ["tf","hashicorp-terraform"])
    _insert_vocab("redis",               ["redis-cache"])
    _insert_vocab("mongodb",             ["mongo"])


def downgrade() -> None:
    op.drop_index("ix_brain_vocab_space",       table_name="brain_vocabulary")
    op.drop_index("ix_brain_doc_types_space",   table_name="brain_doc_types")
    op.drop_index("ix_brain_subdomains_domain", table_name="brain_subdomains")
    op.drop_index("ix_brain_domains_space",     table_name="brain_domains")
    op.drop_table("brain_vocabulary")
    op.drop_table("brain_entity_hints")
    op.drop_table("brain_doc_types")
    op.drop_table("brain_subdomains")
    op.drop_table("brain_domains")
