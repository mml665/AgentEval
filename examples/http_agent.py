"""Read-only HTTP fixture. Run only on loopback for local evaluation."""
from fastapi import FastAPI
from agenteval.plugins import order_agent

app = FastAPI()


@app.post("/invoke")
def invoke(request: dict):
    return order_agent(request)

