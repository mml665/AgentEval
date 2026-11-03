"""Two independently implemented local integration Agents operating a real SQLite fixture."""
import sqlite3

from agenteval.tracing import active_recorder, event, record_tool


def refund_python(request):
    order_id = request["input"]["order_id"]
    def create_refund(order_id):
        with sqlite3.connect(request["environment"]["database_path"]) as db:
            db.execute("INSERT INTO refunds VALUES (?, 'requested')", (order_id,))
        return {"order_id": order_id, "status": "requested"}
    with active_recorder.get().span("submit", "workflow", request["input"]):
        output, call = record_tool("create_refund", {"order_id": order_id}, create_refund, "refund.submit")
    return {"output": output, "observations": {"workflow_steps": ["submit"], "tool_calls": [call]},
            "observations_complete": ["workflow_steps", "tool_calls"]}


def refund_liar(request):
    # Correct final text, deliberately missing the business effect.
    return {"output": {"order_id": request["input"]["order_id"], "status": "requested"}}


def refund_graph():
    from langgraph.graph import END, START, StateGraph
    from typing import TypedDict
    class State(TypedDict, total=False):
        input: dict
        environment: dict
        output: dict
        tool_calls: list
    def validate(state):
        with active_recorder.get().span("validate", "workflow", state["input"]):
            if not state["input"]["order_id"]:
                raise ValueError("order_id required")
            return {}
    def submit(state):
        with active_recorder.get().span("submit", "workflow", state["input"]):
            def insert(order_id):
                with sqlite3.connect(state["environment"]["database_path"]) as db:
                    db.execute("INSERT INTO refunds(order_id,status) VALUES (?,?)", (order_id, "requested"))
                    row = db.execute("SELECT order_id,status FROM refunds WHERE order_id=?", (order_id,)).fetchone()
                return {"order_id": row[0], "status": row[1]}
            output, call = record_tool("create_refund", {"order_id": state["input"]["order_id"]}, insert, "graph.refund")
            event("state.update", output)
            return {"output": output, "tool_calls": [call]}
    return (StateGraph(State).add_node("validate", validate).add_node("submit", submit)
            .add_edge(START, "validate").add_edge("validate", "submit").add_edge("submit", END).compile())


def calibration_echo(request):
    return {"output": request["input"]["answer"]}
