"""Request body size limit, enforced before the body is parsed."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import ErrorBody, ErrorResponse


class BodySizeLimitMiddleware:
    """Reject request bodies larger than `max_bytes` with 413, without reading more than that.

    Checks `Content-Length` first. For bodies without one (chunked), it buffers at most `max_bytes` and stops
    as soon as the limit is passed, so an oversized upload is never parsed or held in memory in full.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await self._reject(send)
            return

        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await self._reject(send)
                return
            chunks.append(chunk)
            more = message.get("more_body", False)

        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, send: Send) -> None:
        limit_mb = self.max_bytes / (1024 * 1024)
        payload = ErrorResponse(error=ErrorBody(
            code="payload_too_large", message=f"Request body is larger than the {limit_mb:g} MiB limit",
        )).model_dump_json(exclude_none=True).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": payload})


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
