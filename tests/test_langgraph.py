from agenteval.adapters import invoke
from agenteval.config import Settings


async def test_real_compiled_graph_emits_actual_workflow_steps():
    response = await invoke("langgraph", {"plugin_id": "example.order.graph"},
        {"input": {"order_id": "1001"}}, 15, Settings())
    assert response["output"] == {"order_id": "1001", "status": "shipped"}
    assert response["observations"]["workflow_steps"] == ["lookup", "respond"]
    assert response["observations"]["tool_calls"][0]["name"] == "get_order"
    assert response["adapter_metadata"]["framework"] == "langgraph"
