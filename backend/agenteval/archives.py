"""Signed, non-destructive project snapshots; restoration is restricted to an empty database."""
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path

from sqlalchemy import DateTime, select
from agenteval import __version__
from agenteval.contracts import canonical, digest
from agenteval.db import Base

# Identity and live infrastructure are deliberately excluded. Restored projects have no grants/sessions.
EXCLUDED = {"team_users", "project_memberships", "machine_tokens", "user_sessions", "login_throttles",
    "security_locks", "service_heartbeats", "evidence_grants"}
TABLES = [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED]


def key():
    value = os.getenv("AGENTEVAL_ARCHIVE_SIGNING_KEY", "")
    if len(value) < 32: raise ValueError("ARCHIVE_SIGNING_KEY_REQUIRED")
    return value.encode()


def encoded(row):
    return {k: v.isoformat() if isinstance(v, datetime) else v for k,v in row.items()}


def snapshot(engine, project_id):
    # PostgreSQL snapshot isolation and SQLite's explicit read transaction freeze the graph together.
    with engine.connect() as conn:
        if engine.dialect.name == "postgresql": conn = conn.execution_options(isolation_level="REPEATABLE READ")
        if engine.dialect.name == "sqlite": conn.exec_driver_sql("BEGIN")
        data, ids = {}, {}
        projects = Base.metadata.tables["projects"]
        if not conn.execute(select(projects.c.id).where(projects.c.id == project_id)).first(): raise ValueError("PROJECT_NOT_FOUND")
        # A DAG ordered by actual FK dependencies, plus Comparison's explicit JSON relationship.
        for table in TABLES:
            if table.name == "projects": condition = table.c.id == project_id
            elif table.name == "security_audits":
                data[table.name] = [encoded(dict(r)) for r in conn.execute(select(table).order_by(table.c.id)).mappings() if r["project_ids"] == [project_id]]
                ids[table.name] = [r["id"] for r in data[table.name]]
                continue
            elif table.name == "regression_reports":
                condition = table.c.report["baseline"]["run_id"].as_string().in_(ids.get("evaluation_runs", []))
                # This table has no FK and may sort before evaluation_runs; handle it below.
                continue
            elif table.name == "release_decisions": continue
            else:
                parents = [fk for fk in table.foreign_keys if fk.column.table.name in ids]
                if not parents: raise ValueError("UNCLASSIFIED_ARCHIVE_TABLE:" + table.name)
                # Pick the direct project/parent link; other FKs are verified during restore.
                preferred = next((fk for fk in parents if fk.parent.name == "project_id"), parents[0])
                condition = preferred.parent.in_(ids[preferred.column.table.name])
            rows = [encoded(dict(row)) for row in conn.execute(select(table).where(condition).order_by(table.c.id)).mappings()]
            data[table.name] = rows; ids[table.name] = [row["id"] for row in rows]
        for name in ("regression_reports", "release_decisions"):
            table = Base.metadata.tables[name]
            condition = table.c.project_id == project_id if name == "release_decisions" else table.c.report["baseline"]["run_id"].as_string().in_(ids["evaluation_runs"])
            data[name] = [encoded(dict(row)) for row in conn.execute(select(table).where(condition).order_by(table.c.id)).mappings()]
        if any(r["status"] not in ("completed", "cancelled", "failed") for r in data["evaluation_runs"]): raise ValueError("ARCHIVE_PROJECT_NOT_SETTLED")
        if any(r["status"] not in ("completed", "failed") for r in data["grading_batches"]): raise ValueError("ARCHIVE_GRADING_NOT_SETTLED")
        if any(r["status"] not in ("done", "blocked") for r in data["work_items"]): raise ValueError("ARCHIVE_WORK_PENDING")
        if any(r["report"]["candidate"]["run_id"] not in ids["evaluation_runs"] for r in data["regression_reports"]): raise ValueError("ARCHIVE_CROSS_PROJECT_COMPARISON")
    return data


def create(engine, project_id, destination, reason):
    signing_key = key()
    if len(reason.strip()) < 8: raise ValueError("ARCHIVE_REASON_REQUIRED")
    data = snapshot(engine, project_id)
    signed = {"manifest": {"schema": "project-archive-v1", "database_revision": "0009", "engine_version": __version__,
        "project_id": project_id, "created_at": datetime.now().astimezone().isoformat(), "reason": reason.strip(),
        "counts": {name: len(rows) for name,rows in data.items()}, "payload_hash": digest(data),
        "retention": "Non-destructive; all original rows and fixed-report references remain."}, "tables": data}
    signature = hmac.new(signing_key, canonical(signed).encode(), hashlib.sha256).hexdigest()
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting any prior archive/backup or following an existing symlink.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({**signed, "signature": signature}, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    return signed["manifest"]


def verify(source):
    path = Path(source)
    if path.stat().st_size > 256 * 1024 * 1024: raise ValueError("ARCHIVE_TOO_LARGE")
    value = json.loads(path.read_text(encoding="utf-8"))
    signed = {k:v for k,v in value.items() if k != "signature"}
    expected = hmac.new(key(), canonical(signed).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(value.get("signature", ""))): raise ValueError("ARCHIVE_SIGNATURE_MISMATCH")
    manifest, tables = value["manifest"], value["tables"]
    if manifest["schema"] != "project-archive-v1" or manifest["database_revision"] != "0009": raise ValueError("ARCHIVE_SCHEMA_MISMATCH")
    if digest(tables) != manifest["payload_hash"] or {k:len(v) for k,v in tables.items()} != manifest["counts"]: raise ValueError("ARCHIVE_CONTENT_MISMATCH")
    if set(tables) != {t.name for t in TABLES}: raise ValueError("ARCHIVE_TABLE_MISMATCH")
    return value


def restore(engine, source, confirm_isolated=False):
    if not confirm_isolated: raise ValueError("ISOLATED_RESTORE_CONFIRMATION_REQUIRED")
    archive = verify(source)
    # One transaction: collision/constraint errors roll back the entire import; no merge, delete or overwrite.
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite": conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
        if conn.exec_driver_sql("select version_num from alembic_version").scalar() != "0009": raise ValueError("RESTORE_SCHEMA_MISMATCH")
        for table in TABLES:
            if table.name != "security_audits" and conn.execute(select(table.c.id).limit(1)).first(): raise ValueError("RESTORE_REQUIRES_EMPTY_BUSINESS_DATABASE")
        # Comparisons have a JSON-only dependency and must precede ReleaseDecision.
        ordered = [t for t in TABLES if t.name != "release_decisions"] + [Base.metadata.tables["release_decisions"]]
        for table in ordered:
            rows = archive["tables"][table.name]
            converted = [{k: datetime.fromisoformat(v) if isinstance(table.c[k].type, DateTime) and v is not None else v for k,v in row.items()} for row in rows]
            if converted: conn.execute(table.insert(), converted)
        # Verify actual round-trip JSON/date serialization, including original evidence and release snapshots.
        actual = {t.name: [encoded(dict(r)) for r in conn.execute(select(t).where(t.c.id.in_([r["id"] for r in archive["tables"][t.name]])).order_by(t.c.id)).mappings()] for t in TABLES}
        # SQLite drops timezone suffixes in DateTime columns. Normalize dates for cross-database validation.
        def normalized(data):
            return {name: [{k: datetime.fromisoformat(v).replace(tzinfo=None).isoformat() if isinstance(Base.metadata.tables[name].c[k].type, DateTime) and v else v for k,v in row.items()} for row in rows] for name,rows in data.items()}
        if digest(normalized(actual)) != digest(normalized(archive["tables"])): raise ValueError("RESTORE_ROUNDTRIP_MISMATCH")
    return archive["manifest"]
