"""One trusted plugin invocation per subprocess, using bounded JSON IPC."""
import json
import sys

from agenteval.plugins import PLUGINS


def main():
    payload = json.loads(sys.stdin.read(1_048_577))
    from agenteval.tracing import Recorder, event
    recorder = Recorder()
    kind = payload.get("adapter_type", "python")
    try:
        with recorder.span(payload["plugin_id"], "component" if kind == "component" else "agent", payload["request"]["input"], payload["request"].get("traceparent")):
            if kind == "langgraph":
                from agenteval.langgraph_adapter import invoke_graph
                response = invoke_graph(payload["plugin_id"], payload["request"])
            elif kind == "component":
                from agenteval.components import COMPONENTS
                response = COMPONENTS[payload["plugin_id"]]["invoke"](payload["request"])
            else:
                response = PLUGINS[payload["plugin_id"]](payload["request"])
            event("final.output", response["output"])
    except Exception as exc:
        response = {"output": None, "status": "agent_failed", "error": {"code": "PLUGIN_ERROR", "type": type(exc).__name__},
                    "observations": {}, "observations_complete": []}
    if recorder.calls:
        response.setdefault("observations", {})["tool_calls"] = recorder.calls
    response["trace_spans"] = recorder.records()
    response["trace_completeness"] = "instrumented"
    json.dump(response, sys.stdout, ensure_ascii=True, allow_nan=False)


if __name__ == "__main__":
    main()

