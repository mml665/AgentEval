"""Deployment-owned plugin registry. No uploaded code or arbitrary entrypoints."""


def order_agent(request):
    value = request["input"]
    order_id = value.get("order_id", "1001")
    state = "shipped" if order_id == "1001" else "processing"
    return {
        "output": {"order_id": order_id, "status": state},
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


PLUGINS = {"example.order.v1": order_agent, "example.order.v2": order_agent_regressed,
           "example.slow": slow_agent}

# Deployment-owned factory names; requests may select IDs, never module paths.
GRAPH_PLUGINS = {"example.order.graph": "order_graph"}

