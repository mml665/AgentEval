"""Loopback-only HTTP authentication fixture for CI, not a real business Agent."""
import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--port", type=int, required=True); args = parser.parse_args()
    counts = {"posts": 0}; lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, status, value):
            data = json.dumps(value).encode(); self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_HEAD(self): self.send_response(405); self.end_headers()
        def do_GET(self): self.reply(200, counts if self.path == "/stats" else {"ready": True})
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 2_097_152: return self.reply(413, {})
            self.rfile.read(size)
            with lock: counts["posts"] += 1
            key = os.environ["AGENTEVAL_AGENT_SECRET_BROWSER_HTTP"]
            if self.headers.get("Authorization") != "Bearer " + key and self.headers.get("X-API-Key") != key:
                return self.reply(401, {"error": "authentication rejected"})
            self.reply(200, {"output": "ok"})
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__": main()
