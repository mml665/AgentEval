from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from agenteval.db import make_database


def test_initial_migration_is_repeatable(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine, _ = make_database(url)
    tables = inspect(engine).get_table_names()
    assert {"trial_assessments", "grading_batches", "request_dedup", "evaluation_runs"} <= set(tables)
    assert "environment_evidence" in {c["name"] for c in inspect(engine).get_columns("case_executions")}
    engine.dispose()


def test_upgrade_preserves_unknown_legacy_invocation_without_redispatch(tmp_path):
    from datetime import datetime, timezone
    from uuid import uuid4
    import sqlalchemy as sa
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    config = Config("alembic.ini"); config.attributes["database_url"] = url
    command.upgrade(config, "0001")
    engine, _ = make_database(url)
    metadata = sa.MetaData(); metadata.reflect(engine)
    with engine.begin() as connection:
        def insert(table, **values):
            values.setdefault("id", str(uuid4())); values["created_at"] = datetime.now(timezone.utc)
            connection.execute(metadata.tables[table].insert().values(**values))
            return values["id"]
        project = insert("projects", name="legacy", description="")
        agent = insert("agents", project_id=project, name="legacy", description="", adapter_type="python")
        av = insert("agent_versions", agent_id=agent, version="1", configuration={}, capabilities=[], content_hash="h")
        bm = insert("benchmarks", project_id=project, name="legacy", description="")
        bv = insert("benchmark_versions", benchmark_id=bm, version="1", content_hash="h", case_count=1)
        case = insert("test_cases", benchmark_version_id=bv, case_key="legacy", definition={})
        profile = insert("profile_versions", project_id=project, name="legacy", version="1", definition={}, content_hash="h")
        run = insert("evaluation_runs", project_id=project, agent_version_id=av, benchmark_version_id=bv,
                     profile_version_id=profile, status="running", manifest={}, manifest_hash="h", cancel_requested=False)
        trial = insert("trials", run_id=run, case_id=case, repetition_index=0)
        execution = insert("case_executions", trial_id=trial, attempt_index=0, status="invoking", trace_id=str(uuid4()))
        insert("grading_batches", run_id=run, revision=0, status="pending", grading_manifest={})
    engine.dispose()
    command.upgrade(config, "head")
    engine, _ = make_database(url)
    metadata = sa.MetaData(); metadata.reflect(engine)
    with engine.connect() as connection:
        assert connection.execute(sa.select(metadata.tables["case_executions"].c.status)).scalar() == "outcome_unknown"
        items = list(connection.execute(sa.select(metadata.tables["work_items"])).mappings())
        invocation = next(i for i in items if i["target_id"] == execution)
        assert invocation["status"] == "blocked"
        assert any(i["kind"] == "grade" and i["status"] == "pending" for i in items)
    engine.dispose()


def test_team_migration_preserves_legacy_owners_actors_and_fixed_reports(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from agenteval.api import create_app
    from agenteval.config import Settings
    from agenteval.db import QualityIssue, ReleaseDecision, Run, IssueEvent
    from examples.product_demo import create_product_demo
    url = "sqlite:///" + (tmp_path / "legacy-team.db").as_posix()
    config = Config("alembic.ini"); config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=url, api_token="migration-fixture-token", environment_root=str(tmp_path / "env")))
    with TestClient(app, headers={"Authorization": "Bearer migration-fixture-token"}) as client:
        demo = create_product_demo(client)
        with app.state.sessions() as session:
            issue = session.get(QualityIssue, demo["issue_id"])
            issue.owner = "Historical free-text owner"
            frozen = {row.id: row.snapshot for row in session.scalars(select(ReleaseDecision))}
            manifests = {row.id: row.manifest_hash for row in session.scalars(select(Run))}
            session.commit()
    # Remove only the new fields/tables, producing a populated 0007 database, then upgrade it.
    command.downgrade(config, "0007")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine, sessions = make_database(url)
    with sessions() as session:
        issue = session.get(QualityIssue, demo["issue_id"])
        assert issue.owner == issue.legacy_owner == "Historical free-text owner"
        assert issue.assignee_user_id is None
        assert all(row.initiator is None and manifests[row.id] == row.manifest_hash for row in session.scalars(select(Run)))
        assert all(row.actor is None and frozen[row.id] == row.snapshot for row in session.scalars(select(ReleaseDecision)))
        assert all(row.actor is None for row in session.scalars(select(IssueEvent)))
    engine.dispose()

