"""Add passport fields to documents table (F3.4)

Revision ID: 161
Revises: 160
Create Date: 2026-06

Añade los campos del pasaporte semántico al modelo Document.
Estos campos son rellenados por el pipeline Brain (F3) tras la síntesis
y por el task Celery que llama a process_document_content().

Campos añadidos:
  passport_path         — ruta en disco del .md generado por BrainWriter
  passport_generated_at — timestamp de la última síntesis exitosa
  avg_quality_score     — promedio de quality_score de los bloques (0.0–1.0)
  has_pii               — True si UniversalCleaner detectó PII en el documento
  detected_languages    — lista de idiomas detectados (ej: ["es", "en"])
  embedding_scope       — colecciones Qdrant donde se vectorizó
                          (ej: ["brain", "knowledge"] o ["brain", "knowledge", "code"])

Decisión de diseño:
  - Todos los campos son nullable para compatibilidad total con documentos
    existentes que no han pasado por el pipeline Brain.
  - embedding_scope usa server_default '{knowledge,code}' para documentos
    pre-F3 que no tienen pasaporte: indica que se vectorizaron solo en
    knowledge/code (el comportamiento legacy de SurfSense).
  - upgrade() es idempotente: usa _column_exists() antes de cada add_column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "161"
down_revision: str | None = "160"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tabla real del modelo Document (plural — verificado en db.py __tablename__)
_TABLE = "documents"

# Campos que esta migración gestiona — usados en upgrade y downgrade
_PASSPORT_COLUMNS = [
    "passport_path",
    "passport_generated_at",
    "avg_quality_score",
    "has_pii",
    "detected_languages",
    "embedding_scope",
]


def _column_exists(conn, table: str, column: str) -> bool:
    """Comprueba si una columna existe en la tabla dada (idempotencia)."""
    return (
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).fetchone()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, _TABLE, "passport_path"):
        op.add_column(
            _TABLE,
            sa.Column("passport_path", sa.String(), nullable=True,
                      comment="Ruta en disco del .md generado por BrainWriter"),
        )

    if not _column_exists(conn, _TABLE, "passport_generated_at"):
        op.add_column(
            _TABLE,
            sa.Column("passport_generated_at", sa.DateTime(timezone=True), nullable=True,
                      comment="Timestamp de la última síntesis exitosa del pasaporte"),
        )

    if not _column_exists(conn, _TABLE, "avg_quality_score"):
        op.add_column(
            _TABLE,
            sa.Column("avg_quality_score", sa.Float(), nullable=True,
                      comment="Promedio de quality_score de los bloques (0.0–1.0)"),
        )

    if not _column_exists(conn, _TABLE, "has_pii"):
        op.add_column(
            _TABLE,
            sa.Column("has_pii", sa.Boolean(), nullable=True,
                      server_default=sa.text("false"),
                      comment="True si UniversalCleaner detectó PII en el documento"),
        )

    if not _column_exists(conn, _TABLE, "detected_languages"):
        op.add_column(
            _TABLE,
            sa.Column("detected_languages", postgresql.ARRAY(sa.String()), nullable=True,
                      comment="Idiomas detectados en el documento (ej: ['es', 'en'])"),
        )

    if not _column_exists(conn, _TABLE, "embedding_scope"):
        op.add_column(
            _TABLE,
            sa.Column(
                "embedding_scope",
                postgresql.ARRAY(sa.String()),
                nullable=True,
                server_default=sa.text("'{knowledge,code}'"),
                comment=(
                    "Colecciones Qdrant donde se vectorizó el documento. "
                    "Documentos pre-F3 usan el default legacy '{knowledge,code}'."
                ),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    for col in _PASSPORT_COLUMNS:
        if _column_exists(conn, _TABLE, col):
            op.drop_column(_TABLE, col)
