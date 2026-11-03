import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agenteval.adapters import AgentFailure, invoke
from agenteval.config import Settings
from agenteval.plugins import order_agent


@pytest.fixture
def http_fixture():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/")
                self.end_headers()
                return
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            raw = json.dumps(order_agent(request)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


async def test_http_agent_uses_actual_network_and_matches_python(http_fixture):
    port = http_fixture
    settings = Settings(http_allowlist=(f"127.0.0.1:{port}",))
    request = {"input": {"order_id": "1001"}}
    actual = await invoke("http", {"endpoint": f"http://127.0.0.1:{port}/invoke"}, request, 3, settings)
    assert actual == order_agent(request)
    python = await invoke("python", {"plugin_id": "example.order.v1"}, request, 3, settings)
    assert actual["output"] == python["output"]
    assert actual["observations"]["workflow_steps"] == python["observations"]["workflow_steps"]
    assert python["observations"]["tool_calls"][0]["call_id"] == "order.lookup"
    assert python["trace_spans"]


async def test_redirect_is_not_followed(http_fixture):
    port = http_fixture
    with pytest.raises(AgentFailure):
        await invoke("http", {"endpoint": f"http://127.0.0.1:{port}/redirect"}, {"input": {}}, 3,
                     Settings(http_allowlist=(f"127.0.0.1:{port}",)))

