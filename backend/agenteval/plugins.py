"""Deployment-owned plugin registry. No uploaded code or arbitrary entrypoints."""


def order_agent(request):
    from agenteval.tracing import active_recorder, record_tool, event
    value = request["input"]
    order_id = value.get("order_id", "1001")
    def get_order(order_id):
        return {"order_id": order_id, "status": "shipped" if order_id == "1001" else "processing"}
    recorder = active_recorder.get()
    if recorder:
        with recorder.span("lookup", "workflow", value):
            output, _ = record_tool("get_order", {"order_id": order_id}, get_order, "order.lookup")
            event("state.update", output)
        with recorder.span("respond", "workflow", output):
            event("tool.result_used", {"call_id": "order.lookup", "output": output})
    else:
        output = get_order(order_id)
    return {
        "output": output,
        "observations": {
            "tool_calls": [{"name": "get_order", "arguments": {"order_id": order_id}, "status": "ok"}],
            "workflow_steps": ["lookup", "respond"],
            "retrieval_results": ["policy-shipped", "policy-refund"],
        },
        "observations_complete": ["tool_calls", "workflow_steps", "retrieval_results"],
    }


def order_agent_regressed(request):
    response = order_agent(request)
    response["output"]["status"] = "processing"
    return response


def slow_agent(request):
    import time
    time.sleep(10)
    return order_agent(request)


def failing_agent(request):
    from agenteval.tracing import record_tool
    def broken_tool():
        raise RuntimeError("Fixture tool failed")
    record_tool("broken_tool", {}, broken_tool, "broken.once")


PLUGINS = {"example.order.v1": order_agent, "example.order.v2": order_agent_regressed,
           "example.slow": slow_agent, "example.failing": failing_agent}

# Deployment-owned factory names; requests may select IDs, never module paths.
GRAPH_PLUGINS = {"example.order.graph": "order_graph"}

