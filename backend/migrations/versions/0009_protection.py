"""Versioned display policies and explicit raw evidence access."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    op.create_table("protection_policies",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("definition", sa.JSON(), nullable=False),
        sa.UniqueConstraint("project_id", "revision"))
    op.create_index("ix_protection_policies_project_id", "protection_policies", ["project_id"])
    op.create_table("evidence_grants",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("team_users.id"), nullable=False),
        sa.Column("raw_read", sa.Boolean(), nullable=False), sa.UniqueConstraint("project_id", "user_id"))
    op.create_index("ix_evidence_grants_project_id", "evidence_grants", ["project_id"])


def downgrade():
    op.drop_index("ix_evidence_grants_project_id", table_name="evidence_grants")
    op.drop_table("evidence_grants")
    op.drop_index("ix_protection_policies_project_id", table_name="protection_policies")
    op.drop_table("protection_policies")
