"""Remove pgvector embedding columns and HNSW indexes (F5.6)

Revision ID: 162
Revises: 161
Create Date: 2026-06

PROPÓSITO
---------
Tras F5, toda la búsqueda vectorial se hace en Qdrant (nomic-embed-text 768d).
Las columnas pgvector de PostgreSQL ya no se usan para búsqueda semántica.
Esta migración elimina:
  1. Índice HNSW de chunks (chucks_vector_index)        ← ~12KB por fila
  2. Índice HNSW de documents (document_vector_index)    ← ~12KB por fila
  3. Columna chunks.embedding (Vector 384d)
  4. Columna documents.embedding (Vector 384d)

NO ELIMINA (se mantienen en PostgreSQL):
  - chucks_search_index (GIN tsvector) → BM25 keyword search
  - document_search_index (GIN tsvector) → BM25 keyword search
  - chunks.content (Text) → fuente de verdad del texto para BM25
  - documents.content (Text) → fuente de verdad del texto

CUÁNDO EJECUTAR
---------------
SOLO después de verificar que:
  1. BRAIN_INGESTION_ENABLED=true funciona correctamente
  2. Todos los documentos existentes se han re-indexado en Qdrant
     (scripts/migrate_pgvector_to_qdrant.py)
  3. El chat de SurfSense devuelve resultados correctos desde Qdrant
  4. El pipeline Brain (F5) pasa todos los tests

VERIFICACIÓN PREVIA (ejecutar ANTES de la migración):
  docker compose exec backend python -c "
  from app.brain.qdrant_manager import QdrantManager
  mgr = QdrantManager.get_instance()
  print(f'knowledge: {mgr.client.count(\"knowledge\").count} vectores')
  print(f'brain: {mgr.client.count(\"brain\").count} vectores')
  "
  # Verificar que el count de knowledge ≈ count de chunks en PostgreSQL

ROLLBACK
--------
  downgrade() recrea las columnas vacías (sin datos) y los índices HNSW.
  Los vectores NO se restauran — se necesita re-indexar con el pipeline
  SurfSense original (BRAIN_INGESTION_ENABLED=false + embed_texts).

IMPACTO EN DISCO
----------------
  Ahorro estimado: ~12KB por chunk + ~12KB por documento.
  Con 10.000 chunks: ~120MB de espacio liberado en PostgreSQL.

IMPORTANTE
----------
  Tras ejecutar esta migración, BRAIN_INGESTION_ENABLED=false (fallback
  SurfSense) ya NO funcionará para nuevos documentos porque las columnas
  embedding no existen. El pipeline Brain es obligatorio.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "162"
down_revision: str | None = "161"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_exists(conn, index_name: str) -> bool:
    """Comprueba si un índice existe en la base de datos (idempotencia)."""
    return (
        conn.execute(
            sa.text(
                "SELECT 1 FROM pg_indexes WHERE indexname = :name"
            ),
            {"name": index_name},
        ).fetchone()
        is not None
    )


def _column_exists(conn, table: str, column: str) -> bool:
    """Comprueba si una columna existe en la tabla (idempotencia)."""
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

    # ── Paso 1: Eliminar índices HNSW pgvector ────────────────────────────
    # Los índices HNSW son pesados (~12KB por fila) y ya no se usan
    # porque la búsqueda vectorial se hace en Qdrant.

    if _index_exists(conn, "chucks_vector_index"):
        op.drop_index("chucks_vector_index", table_name="chunks")

    if _index_exists(conn, "document_vector_index"):
        op.drop_index("document_vector_index", table_name="documents")

    # ── Paso 2: Eliminar columnas embedding ───────────────────────────────
    # Las columnas Vector(384) almacenaban los embeddings de all-MiniLM-L6-v2.
    # Ahora los embeddings están en Qdrant con nomic-embed-text (768d).

    if _column_exists(conn, "chunks", "embedding"):
        op.drop_column("chunks", "embedding")

    if _column_exists(conn, "documents", "embedding"):
        op.drop_column("documents", "embedding")

    # ── Verificación: BM25 indexes NO se tocan ────────────────────────────
    # chucks_search_index y document_search_index permanecen intactos.
    # Son GIN indexes sobre to_tsvector('english', content) para BM25.


def downgrade() -> None:
    """
    Recrea las columnas embedding vacías y los índices HNSW.

    NOTA: Las columnas se crean con tipo genérico (no Vector) porque
    la extensión pgvector podría no estar disponible al hacer downgrade.
    Para restaurar completamente:
      1. Ejecutar este downgrade
      2. Cambiar el tipo de columna a Vector(384) manualmente
      3. Re-indexar todos los documentos con BRAIN_INGESTION_ENABLED=false
    """
    conn = op.get_bind()

    # Recrear columnas embedding (vacías, nullable)
    # Usar bytea como tipo genérico si pgvector no está disponible
    if not _column_exists(conn, "chunks", "embedding"):
        # Intentar crear con Vector si pgvector está disponible
        try:
            op.execute(
                "ALTER TABLE chunks ADD COLUMN embedding vector"
            )
        except Exception:
            # pgvector no disponible — crear como bytea placeholder
            op.add_column(
                "chunks",
                sa.Column("embedding", sa.LargeBinary(), nullable=True),
            )

    if not _column_exists(conn, "documents", "embedding"):
        try:
            op.execute(
                "ALTER TABLE documents ADD COLUMN embedding vector"
            )
        except Exception:
            op.add_column(
                "documents",
                sa.Column("embedding", sa.LargeBinary(), nullable=True),
            )

    # Recrear índices HNSW (solo si las columnas son tipo vector)
    if not _index_exists(conn, "chucks_vector_index"):
        try:
            op.execute(
                "CREATE INDEX IF NOT EXISTS chucks_vector_index "
                "ON chunks USING hnsw (embedding public.vector_cosine_ops)"
            )
        except Exception:
            pass  # No se puede crear HNSW sobre bytea — skip

    if not _index_exists(conn, "document_vector_index"):
        try:
            op.execute(
                "CREATE INDEX IF NOT EXISTS document_vector_index "
                "ON documents USING hnsw (embedding public.vector_cosine_ops)"
            )
        except Exception:
            pass
