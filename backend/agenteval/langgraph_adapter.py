"""Deployment-owned graph factories mapped through actual LangGraph stream events."""
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from agenteval.plugins import GRAPH_PLUGINS


class OrderState(TypedDict, total=False):
    input: dict
    output: dict
    tool_calls: list
    retrieval_results: list


def order_graph():
    def lookup(state):
        order_id = state["input"].get("order_id", "1001")
        return {"output": {"order_id": order_id, "status": "shipped" if order_id == "1001" else "processing"},
                "tool_calls": [{"name": "get_order", "arguments": {"order_id": order_id}, "status": "ok"}],
                "retrieval_results": ["policy-shipped", "policy-refund"]}

    def respond(state):
        return {"output": state["output"]}

    return (StateGraph(OrderState).add_node("lookup", lookup).add_node("respond", respond)
            .add_edge(START, "lookup").add_edge("lookup", "respond").add_edge("respond", END).compile())


GRAPHS = {key: globals()[name] for key, name in GRAPH_PLUGINS.items()}


def invoke_graph(plugin_id, request):
    graph = GRAPHS[plugin_id]()
    steps, state = [], {}
    for part in graph.stream({"input": request["input"]}, stream_mode=["updates", "values"], version="v2"):
        if part["type"] == "updates":
            steps.extend(part["data"].keys())
        elif part["type"] == "values":
            state = part["data"]
    return {"output": state["output"], "observations": {
        "workflow_steps": steps, "tool_calls": state.get("tool_calls", []),
        "retrieval_results": state.get("retrieval_results", [])},
        "observations_complete": ["workflow_steps", "tool_calls", "retrieval_results"],
        "adapter_metadata": {"framework": "langgraph", "stream_schema": "v2", "graph_id": plugin_id}}
