import json
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from agenteval.api import create_app
from agenteval.config import Settings
from agenteval.db import Run, ReleaseDecision, make_database
from agenteval import archives
from examples.product_demo import create_product_demo


def migrate(path):
    url = "sqlite:///" + path.as_posix()
    cfg = Config("alembic.ini"); cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    return url


def test_signed_archive_roundtrip_preserves_fixed_releases_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", "fixture-archive-signing-key-only-32-characters")
    app = create_app(Settings(database_url=migrate(tmp_path / "source.db"), api_token="archive-fixture-token", environment_root=str(tmp_path / "env")))
    with TestClient(app, headers={"Authorization": "Bearer archive-fixture-token"}) as client:
        demo = create_product_demo(client)
        with app.state.sessions() as session:
            frozen = {r.id: r.snapshot for r in session.scalars(select(ReleaseDecision))}
        target = tmp_path / "project.json"
        result = archives.create(app.state.engine, demo["project_id"], target, "Isolated restore acceptance")
        assert result["counts"]["release_decisions"] == len(frozen)
        assert all(name not in result["counts"] for name in archives.EXCLUDED)
        with pytest.raises(FileExistsError): archives.create(app.state.engine, demo["project_id"], target, "Do not overwrite existing evidence")
        with pytest.raises(ValueError, match="CONFIRMATION"): archives.restore(app.state.engine, target)
        with pytest.raises(ValueError, match="EMPTY_BUSINESS"): archives.restore(app.state.engine, target, True)
        engine, sessions = make_database(migrate(tmp_path / "restore.db"))
        archives.restore(engine, target, True)
        with sessions() as session:
            assert {r.id:r.snapshot for r in session.scalars(select(ReleaseDecision))} == frozen
            assert len(list(session.scalars(select(Run)))) == result["counts"]["evaluation_runs"]
        with pytest.raises(ValueError, match="EMPTY_BUSINESS"): archives.restore(engine, target, True)
        tampered = json.loads(target.read_text(encoding="utf-8")); tampered["tables"]["release_decisions"][0]["status"] = "tampered"
        bad = tmp_path / "tampered.json"; bad.write_text(json.dumps(tampered),encoding="utf-8")
        with pytest.raises(ValueError, match="SIGNATURE"): archives.verify(bad)
        import os, subprocess, sys
        cli_url = migrate(tmp_path / "cli-restore.db")
        env = {**os.environ, "PYTHONPATH": "backend", "AGENTEVAL_DATABASE_URL": cli_url}
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "agenteval", *args], env=env, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        checked = cli("verify-archive", "--archive-file", str(target))
        assert checked.returncode == 0, checked.stderr
        imported = cli("restore-archive", "--archive-file", str(target), "--confirm-isolated")
        assert imported.returncode == 0, imported.stderr
        assert env["AGENTEVAL_ARCHIVE_SIGNING_KEY"] not in imported.stdout + imported.stderr
        monkeypatch.setenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", "wrong-fixture-archive-signing-key-32")
        with pytest.raises(ValueError, match="SIGNATURE"): archives.verify(target)
        engine.dispose()


def test_archive_refuses_pending_project_and_absent_signing_key(tmp_path, monkeypatch):
    app = create_app(Settings(database_url=migrate(tmp_path / "pending.db"), api_token="archive-fixture-token", environment_root=str(tmp_path / "env")))
    with TestClient(app, headers={"Authorization": "Bearer archive-fixture-token"}) as client:
        demo = create_product_demo(client)
        monkeypatch.delenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", raising=False)
        with pytest.raises(ValueError, match="SIGNING_KEY"): archives.create(app.state.engine, demo["project_id"], tmp_path / "none.json", "Isolated test archive")
        monkeypatch.setenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", "fixture-archive-signing-key-only-32-characters")
        with app.state.sessions() as session:
            session.get(Run, demo["fixed_run_id"]).status = "running"; session.commit()
        with pytest.raises(ValueError, match="NOT_SETTLED"): archives.create(app.state.engine, demo["project_id"], tmp_path / "none.json", "Isolated test archive")
        assert not (tmp_path / "none.json").exists()


def test_old_v1_archive_restores_after_schema_upgrade_with_no_private_drafts(tmp_path, monkeypatch):
    import hashlib, hmac
    from agenteval.contracts import canonical, digest
    monkeypatch.setenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", "fixture-archive-signing-key-only-32-characters")
    app=create_app(Settings(database_url=migrate(tmp_path/'old-source.db'),api_token='archive-fixture-token',environment_root=str(tmp_path/'env')))
    with TestClient(app,headers={'Authorization':'Bearer archive-fixture-token'}) as client:
        demo=create_product_demo(client); path=tmp_path/'v2.json'
        archives.create(app.state.engine,demo['project_id'],path,'Old archive compatibility validation')
        value=json.loads(path.read_text(encoding='utf-8')); value['tables'].pop('baseline_settings')
        value['manifest'].update(schema='project-archive-v1',database_revision='0009',counts={k:len(v) for k,v in value['tables'].items()},payload_hash=digest(value['tables']))
        signed={k:v for k,v in value.items() if k!='signature'}
        value['signature']=hmac.new(archives.key(),canonical(signed).encode(),hashlib.sha256).hexdigest()
        old=tmp_path/'v1.json'; old.write_text(json.dumps(value),encoding='utf-8')
        engine,_=make_database(migrate(tmp_path/'upgrade-restore.db'))
        try:
            archives.restore(engine,old,True)
            restored=archives.snapshot(engine,demo['project_id'])
            assert restored['baseline_settings']==[] and 'evaluation_drafts' not in restored
            assert restored['release_decisions']==value['tables']['release_decisions']
        finally: engine.dispose()
