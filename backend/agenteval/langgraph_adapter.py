"""Deployment-owned graph factories mapped through actual LangGraph stream events."""
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from agenteval.plugins import GRAPH_PLUGINS
from agenteval.tracing import active_recorder, record_tool, event


class OrderState(TypedDict, total=False):
    input: dict
    output: dict
    tool_calls: list
    retrieval_results: list


def order_graph():
    def lookup(state):
        order_id = state["input"].get("order_id", "1001")
        def get_order(order_id):
            return {"order_id": order_id, "status": "shipped" if order_id == "1001" else "processing"}
        with active_recorder.get().span("lookup", "workflow", state):
            output, call = record_tool("get_order", {"order_id": order_id}, get_order, "order.lookup")
            event("state.update", {"output": output})
        return {"output": output,
                "tool_calls": [call],
                "retrieval_results": ["policy-shipped", "policy-refund"]}

    def respond(state):
        with active_recorder.get().span("respond", "workflow", state):
            event("tool.result_used", {"call_id": "order.lookup", "output": state["output"]})
            return {"output": state["output"]}

    return (StateGraph(OrderState).add_node("lookup", lookup).add_node("respond", respond)
            .add_edge(START, "lookup").add_edge("lookup", "respond").add_edge("respond", END).compile())


from agenteval.refund_agents import refund_graph
GRAPHS = {key: globals()[name] for key, name in GRAPH_PLUGINS.items()}


def invoke_graph(plugin_id, request):
    graph = GRAPHS[plugin_id]()
    steps, state = [], {}
    for part in graph.stream({"input": request["input"], "environment": request.get("environment", {})}, stream_mode=["updates", "values"], version="v2"):
        if part["type"] == "updates":
            steps.extend(part["data"].keys())
        elif part["type"] == "values":
            state = part["data"]
    return {"output": state["output"], "observations": {
        "workflow_steps": steps, "tool_calls": state.get("tool_calls", []),
        "retrieval_results": state.get("retrieval_results", [])},
        "observations_complete": ["workflow_steps", "tool_calls", "retrieval_results"],
        "adapter_metadata": {"framework": "langgraph", "stream_schema": "v2", "graph_id": plugin_id}}
