from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse


class ApplicationBodyLimit:
    def __init__(self, app, max_bytes: int = 2097152, timeout: float = 30):
        self.app = app
        self.max_bytes = max_bytes
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        paths = ("/api/forms", "/api/artifacts/commands", "/api/control/application", "/api/admin/control/application")
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"} or not scope["path"].startswith(paths):
            return await self.app(scope, receive, send)
        content_type = next((value for key, value in scope.get("headers", ()) if key.lower() == b"content-type"), b"")
        if content_type and not content_type.lower().startswith(b"application/json"):
            return await JSONResponse({"detail": {"code": "json_required", "message": "This endpoint accepts JSON requests."}}, status_code=415)(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_bytes:
                        return await JSONResponse({"detail": {"code": "request_too_large", "message": "The request exceeds its input limit."}}, status_code=413)(scope, receive, send)
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"detail": {"code": "request_timeout", "message": "The request upload timed out. Retry using the same idempotency key."}}, status_code=408)(scope, receive, send)
        sent = False

        async def bounded_receive():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, send)
