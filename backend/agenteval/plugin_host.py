"""One trusted plugin invocation per subprocess, using bounded JSON IPC."""
import json
import sys

from agenteval.plugins import PLUGINS


def main():
    payload = json.loads(sys.stdin.read(1_048_577))
    plugin = PLUGINS[payload["plugin_id"]]
    json.dump(plugin(payload["request"]), sys.stdout, ensure_ascii=True, allow_nan=False)


if __name__ == "__main__":
    main()

