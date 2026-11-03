"""One trusted plugin invocation per subprocess, using bounded JSON IPC."""
import json
import sys

from agenteval.plugins import PLUGINS


def main():
    payload = json.loads(sys.stdin.read(1_048_577))
    if payload.get("adapter_type") == "langgraph":
        from agenteval.langgraph_adapter import invoke_graph
        response = invoke_graph(payload["plugin_id"], payload["request"])
    else:
        response = PLUGINS[payload["plugin_id"]](payload["request"])
    json.dump(response, sys.stdout, ensure_ascii=True, allow_nan=False)


if __name__ == "__main__":
    main()

