import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def main():
    parser = argparse.ArgumentParser(prog="python -m agenteval")
    parser.add_argument("command", choices=["init-db", "serve", "worker", "dispatch"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "init-db":
        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    elif args.command == "worker":
        from agenteval.tasks import app
        app.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING", "--without-gossip", "--without-mingle"])
    elif args.command == "dispatch":
        from agenteval.tasks import dispatch_forever
        dispatch_forever()
    else:
        import uvicorn
        uvicorn.run("agenteval.api:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
