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
    engine.dispose()

