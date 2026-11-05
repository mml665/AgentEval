"""Explicit evaluation baselines and private member drafts."""
from alembic import op
import sqlalchemy as sa
revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None


def upgrade():
    op.create_table("baseline_settings",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("benchmark_version_id", sa.String(36), sa.ForeignKey("benchmark_versions.id"), nullable=False),
        sa.Column("grading_batch_id", sa.String(36), sa.ForeignKey("grading_batches.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("actor", sa.JSON(), nullable=False),
        sa.UniqueConstraint("project_id", "agent_id", "benchmark_version_id"))
    op.create_index("ix_baseline_settings_project_id", "baseline_settings", ["project_id"])
    op.create_table("evaluation_drafts",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("team_users.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id"))
    op.create_index("ix_evaluation_drafts_project_id", "evaluation_drafts", ["project_id"])


def downgrade():
    op.drop_index("ix_evaluation_drafts_project_id", table_name="evaluation_drafts"); op.drop_table("evaluation_drafts")
    op.drop_index("ix_baseline_settings_project_id", table_name="baseline_settings"); op.drop_table("baseline_settings")
