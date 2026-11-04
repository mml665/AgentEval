import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def main():
    parser = argparse.ArgumentParser(prog="python -m agenteval")
    parser.add_argument("command", choices=["init-db", "init-admin", "serve", "worker", "dispatch"])
    parser.add_argument("--username")
    parser.add_argument("--display-name", default="Administrator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "init-db":
        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
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
            proxy_headers=bool(trusted), forwarded_allow_ips=",".join(trusted))


if __name__ == "__main__":
    main()
