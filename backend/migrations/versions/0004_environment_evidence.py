"""Persist independently observed environment state, without backfilling fake evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("case_executions", sa.Column("environment_evidence", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("case_executions", "environment_evidence")
