"""Enable the pgvector extension.

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

from alembic import op

# Sequential, readable revision IDs rather than Alembic's random hex, so the
# migration order is obvious from the file names.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The `vector` column type and similarity operators used for embeddings.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
