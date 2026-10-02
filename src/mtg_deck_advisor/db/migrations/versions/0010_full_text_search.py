"""Full-text search on cards and rules, for keyword and hybrid search.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-02
"""

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None

# Generated columns, so they can never fall out of step with the text they
# index. Weights rank a match by where it is: a card's name (A) above its type
# line (B) above its rules text (C); a rule's number and section (A) above its
# text (B). The 'english' configuration stems words ("regenerated" and
# "regenerating" both match "regener") and drops stop words.
COLUMNS = {
    "cards": """
        setweight(to_tsvector('english', name), 'A')
        || setweight(to_tsvector('english', type_line), 'B')
        || setweight(to_tsvector('english', oracle_text), 'C')
    """,
    "rules": """
        setweight(to_tsvector('english', number || ' ' || section), 'A')
        || setweight(to_tsvector('english', text), 'B')
    """,
}


def upgrade() -> None:
    for table, expression in COLUMNS.items():
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN search_vector tsvector "
            f"GENERATED ALWAYS AS ({expression}) STORED"
        )
        op.execute(f"CREATE INDEX {table}_search_vector ON {table} USING gin (search_vector)")


def downgrade() -> None:
    for table in COLUMNS:
        op.execute(f"ALTER TABLE {table} DROP COLUMN search_vector")
