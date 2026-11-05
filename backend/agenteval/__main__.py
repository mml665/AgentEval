import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def main():
    parser = argparse.ArgumentParser(prog="python -m agenteval")
    parser.add_argument("command", choices=["init-db", "init-admin", "serve", "worker", "dispatch", "archive-project", "verify-archive", "restore-archive"])
    parser.add_argument("--project-id")
    parser.add_argument("--archive-file")
    parser.add_argument("--reason", default="")
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name", default="Administrator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "init-db":
        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    elif args.command in ("archive-project", "verify-archive", "restore-archive"):
        import json
        from agenteval import archives
        from agenteval.config import Settings
        from agenteval.db import make_database, SecurityAudit
        if not args.archive_file: parser.error("--archive-file is required")
        engine, sessions = make_database(Settings().database_url)
        try:
            if args.command == "archive-project":
                if not args.project_id: parser.error("--project-id is required")
                result = archives.create(engine, args.project_id, args.archive_file, args.reason)
            elif args.command == "verify-archive": result = archives.verify(args.archive_file)["manifest"]
            else: result = archives.restore(engine, args.archive_file, args.confirm_isolated)
            with sessions() as session:
                session.add(SecurityAudit(actor={"kind": "deployment_cli", "id": None, "display_name": "Deployment archive operator"},
                    action=args.command.upper().replace("-", "_"), project_ids=[result["project_id"]],
                    resource_ids={"payload_hash": result["payload_hash"]}, status=200)); session.commit()
            print(json.dumps({"operation": args.command, "project_id": result["project_id"], "payload_hash": result["payload_hash"], "counts": result["counts"]}))
        except Exception as exc:
            # Never print database connection URLs, signing keys or malformed archive content.
            message = str(exc)
            code = message if message.isupper() and len(message) < 100 and " " not in message else "ARCHIVE_OPERATION_FAILED"
            raise SystemExit(code) from None
        finally: engine.dispose()
    elif args.command == "init-admin":
        import getpass
        import os
        from agenteval.config import Settings
        from agenteval.db import make_database
        from agenteval.security import bootstrap_admin
        if not args.username: parser.error("init-admin requires --username")
        password = os.getenv("AGENTEVAL_BOOTSTRAP_PASSWORD")
        if not password:
            password = getpass.getpass("New administrator password: ")
            if password != getpass.getpass("Confirm password: "): parser.error("Passwords do not match")
        engine, sessions = make_database(Settings().database_url)
        try: bootstrap_admin(sessions, args.username, password, args.display_name)
        except ValueError:
            raise SystemExit("Initialization refused. Check username/password constraints and whether an administrator already exists.") from None
        finally: engine.dispose()
        print("Administrator initialized; no password or session token is printed.")
    elif args.command == "worker":
        from agenteval.tasks import app
        app.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING", "--without-gossip", "--without-mingle"])
    elif args.command == "dispatch":
        from agenteval.tasks import dispatch_forever
        dispatch_forever()
    else:
        import uvicorn
        from agenteval.config import Settings
        trusted = Settings().trusted_proxy_ips
        uvicorn.run("agenteval.api:app", host=args.host, port=args.port, workers=1,
            proxy_headers=bool(trusted), forwarded_allow_ips=",".join(trusted), access_log=False)


if __name__ == "__main__":
    main()
