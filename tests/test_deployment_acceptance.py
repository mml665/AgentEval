"""Safety boundaries for a tool that intentionally stops/kills containers."""
import argparse
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest
from scripts.deployment_acceptance import (AcceptanceError, Runner, Stack, clean_environment,
    file_manifest, project_name, unpack_source, write_private)


@pytest.mark.parametrize("name", ["agenteval", "agenteval-prod", "../ae-acceptance", "AE-acceptance", "-acceptance"])
def test_deployment_refuses_non_dedicated_project_names(name):
    with pytest.raises(argparse.ArgumentTypeError): project_name(name)


def test_deployment_environment_excludes_local_secrets_and_compose_overrides():
    result = clean_environment({"PATH": "tools", "HOME": "user", "DOCKER_HOST": "npipe://docker",
        "AGENTEVAL_API_TOKEN": "private", "agenteval_judge_api_key": "private",
        "POSTGRES_PASSWORD": "private", "COMPOSE_FILE": "production.yml", "COMPOSE_PROFILES": "production"})
    assert result == {"PATH": "tools", "HOME": "user", "DOCKER_HOST": "npipe://docker"}


@pytest.mark.parametrize("member_name,kind", [("../escape", "file"), ("/escape", "file"),
    ("C:/escape", "file"), ("dir\\escape", "file"), ("link", "symlink")])
def test_deployment_rejects_unsafe_source_before_any_file_is_written(tmp_path, member_name, kind):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        first = tarfile.TarInfo("safe.txt"); first.size = 2
        archive.addfile(first, io.BytesIO(b"ok"))
        item = tarfile.TarInfo(member_name)
        if kind == "symlink": item.type, item.linkname = tarfile.SYMTYPE, "../outside"
        archive.addfile(item, io.BytesIO(b""))
    with pytest.raises(AcceptanceError, match="UNSAFE_SOURCE_ARCHIVE"):
        unpack_source(raw.getvalue(), tmp_path / "source")
    assert not (tmp_path / "source" / "safe.txt").exists()


@pytest.mark.parametrize("occupied", ["container", "volume"])
def test_deployment_never_owns_or_stops_an_existing_project(tmp_path, occupied):
    commands = []
    def command(argv):
        commands.append(argv)
        return b"existing" if ("volume" in argv) == (occupied == "volume") else b""
    runner = SimpleNamespace(folder=tmp_path, command=command, source=tmp_path, commit="fixture")
    stack = Stack(runner, "ae-acceptance", 8139, "fixture-image")
    with pytest.raises(AcceptanceError, match="EXISTING_PROJECT"):
        stack.reserve()
    assert not stack.owned and not stack.override.exists()
    assert all("stop" not in c and "kill" not in c for c in commands)


def test_deployment_error_report_stops_only_owned_stacks_and_retains_backups(tmp_path):
    calls = []
    runner = Runner.__new__(Runner)
    runner.folder, runner.started, runner.report = tmp_path, __import__("time").monotonic(), {"checks": []}
    runner.stacks = [SimpleNamespace(owned=False, dc=lambda *a, **k: pytest.fail("Foreign stack touched")),
        SimpleNamespace(owned=True, dc=lambda *a, **k: calls.append(a))]
    write_private(tmp_path / "database.dump", b"fixture backup")
    assert not runner.finish(AcceptanceError("failure"))
    assert calls == [("stop",)] and (tmp_path / "database.dump").read_bytes() == b"fixture backup"
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["volumes_preserved"] and report["containers_stopped"]


def test_deployment_exclusive_backup_and_nested_environment_integrity(tmp_path):
    source = tmp_path / "environment"; (source / "nested").mkdir(parents=True)
    (source / "nested" / "state.sqlite3").write_bytes(b"fixture state")
    before = file_manifest(source)
    assert list(before) == ["nested/state.sqlite3"]
    (source / "nested" / "state.sqlite3").write_bytes(b"changed state")
    assert file_manifest(source) != before
    target = tmp_path / "backup"
    write_private(target, b"first")
    with pytest.raises(FileExistsError): write_private(target, b"replacement")
    assert target.read_bytes() == b"first"
