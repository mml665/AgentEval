"""Read-only HTTP fixture. Run only on loopback for local evaluation."""
from fastapi import FastAPI
from agenteval.plugins import order_agent

app = FastAPI()


@app.post("/invoke")
def invoke(request: dict):
    from agenteval.tracing import Recorder, event
    recorder = Recorder()
    with recorder.span("http.order", "agent", request["input"], request.get("traceparent")):
        response = order_agent(request)
        response["observations"]["tool_calls"] = recorder.calls
        event("final.output", response["output"])
    response["trace_spans"] = recorder.records()
    response["trace_completeness"] = "instrumented"
    return response

