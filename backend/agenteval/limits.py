"""Bound request buffering before JSON parsing, including chunked requests."""
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 2 * 1024 * 1024


class RequestLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                return await JSONResponse({"code": "REQUEST_TOO_LARGE"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)

