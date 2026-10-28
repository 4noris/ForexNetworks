"""Read-only JSON RPC adapter to the existing authenticated ASGI HTTP routes.

There is no loopback network connection: validation, dependencies and HTTP
middleware run in-process with the WebSocket's original session cookie.
"""
import asyncio
import json
import orjson
from urllib.parse import unquote, urlsplit


READ_POST_PATHS = frozenset({
    "/api/analytics", "/api/book-performance", "/api/mt5/groups",
    "/api/dealer-command-center", "/api/risk/live", "/api/risk/exposure", "/api/risk/performance",
    "/api/risk/markout", "/api/risk/analyse", "/api/risk/client-live",
    "/api/risk/antifraud", "/api/risk/stress", "/api/reports/deal",
    "/api/reports/brokerage", "/api/reports/brokerage/audited", "/api/reports/diff",
    "/api/operations/live", "/api/operations/cid-audit", "/api/operations/intelligence",
})
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def read_target(method, target):
    if not isinstance(target, str) or len(target) > 16384:
        raise ValueError("Invalid read path")
    url = urlsplit(target)
    path = unquote(url.path)
    if (url.scheme or url.netloc or url.fragment or not path.startswith("/api/")
            or "\\" in path or "//" in path or any(ord(char) < 32 for char in path)
            or any(part in {".", ".."} for part in path.split("/"))
            or path.startswith("/api/auth/") or path.endswith(("/export", "/download", ".mq5", ".ex5"))):
        raise ValueError("Only application JSON data reads are supported")
    if method != "GET" and not (method == "POST" and path in READ_POST_PATHS):
        raise ValueError("This action must use its existing HTTP endpoint")
    return path, url.query


async def dispatch_read(application, websocket_scope, message):
    method = message.get("method", "GET")
    try:
        path, query = read_target(method, message.get("path"))
    except (ValueError, TypeError):
        return {"status": 405, "data": {"detail": "Unsupported WebSocket read"}}
    body = message.get("body")
    if body is not None and not isinstance(body, (dict, list)):
        return {"status": 422, "data": {"detail": "JSON request body must be an object or array"}}
    raw = await asyncio.to_thread(json.dumps, body) if body is not None else ""
    raw = raw.encode("utf-8")
    headers = [(key, value) for key, value in websocket_scope.get("headers", [])
               if key.lower() in {b"cookie", b"host", b"origin"}]
    headers += [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "https" if websocket_scope.get("scheme") == "wss" else "http",
             "path": path, "raw_path": path.encode("utf-8"), "query_string": query.encode("utf-8"),
             "root_path": websocket_scope.get("root_path", ""), "headers": headers,
             "client": websocket_scope.get("client"), "server": websocket_scope.get("server")}
    consumed = False
    stopped = asyncio.Event()
    status = 500
    chunks = []
    size = 0
    too_large = False
    content_type = ""

    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await stopped.wait()
        return {"type": "http.disconnect"}

    async def send(event):
        nonlocal status, size, too_large, content_type
        if event["type"] == "http.response.start":
            status = event["status"]
            content_type = dict(event.get("headers", [])).get(b"content-type", b"").decode("latin-1")
        elif event["type"] == "http.response.body":
            chunk = event.get("body", b"")
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                too_large = True
                chunks.clear()
            elif not too_large:
                chunks.append(chunk)

    try:
        await application(scope, receive, send)
    finally:
        stopped.set()
    if too_large:
        return {"status": 413, "data": {"detail": "Result exceeds 64 MiB; narrow the report filters"}}
    if "json" not in content_type:
        return {"status": status if status >= 400 else 415,
                "data": {"detail": "This response is not JSON data; use the download endpoint"}}
    try:
        data = await asyncio.to_thread(orjson.loads, b"".join(chunks)) if chunks else None
    except (ValueError, UnicodeDecodeError):
        return {"status": 502, "data": {"detail": "Invalid JSON response from data view"}}
    return {"status": status, "data": data}
