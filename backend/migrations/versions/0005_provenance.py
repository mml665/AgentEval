from alembic import op
import sqlalchemy as sa
revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None

def upgrade():
    op.add_column("case_executions", sa.Column("evidence_provenance", sa.JSON(), nullable=True))

def downgrade():
    op.drop_column("case_executions", "evidence_provenance")
