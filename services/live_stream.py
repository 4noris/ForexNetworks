"""Authenticated read-only streams over existing RMS views; no SDK event callbacks.

Every connection starts with a replacement snapshot. Subsequent frames carry
versioned JSON changes; arrays with structural changes are replaced atomically.
"""
import asyncio
import json
import math
import orjson
import time
import threading

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from starlette.requests import Request
from .websocket_reads import dispatch_read


class SharedLiveViews:
    """Share work for an identical authorized view without blocking worker threads."""
    def __init__(self, build, workers=4, pending=128):
        self.build = build
        self.slots = asyncio.Semaphore(workers)
        self.pending = pending
        self.tasks = {}

    async def read(self, key, *args):
        task = self.tasks.get(key)
        if task is None:
            if len(self.tasks) >= self.pending:
                raise RuntimeError("Live views are busy; retrying shortly")

            async def produce():
                async with self.slots:
                    return await asyncio.to_thread(self.build, *args)

            task = asyncio.create_task(produce())
            self.tasks[key] = task

            def finished(completed):
                if self.tasks.get(key) is completed:
                    self.tasks.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        # Closing one tab must not cancel a build used by other subscribers.
        return await asyncio.shield(task)


def view_key(topic, params, user):
    return (user.get("id"), user.get("username"), topic, json.dumps(params, sort_keys=True))


def finite_float(value):
    if not math.isfinite(value):
        raise ValueError("Non-finite financial data is unavailable")
    return float(value)


def prepare_live_json(data):
    """Validate plain live data without recursively copying every financial row."""
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, float):
            finite_float(value)
        elif value is None or isinstance(value, (str, int, bool)):
            continue
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
            pending.extend(value.values())
        else:
            # Reports with date/Decimal/model values keep the existing encoder.
            return jsonable_encoder(data, custom_encoder={float: finite_float})
    return data


def changes(before, after, path=()):
    if type(before) is not type(after):
        return [{"path": list(path), "value": after}]
    if isinstance(after, dict) and before.keys() == after.keys():
        return [item for key in after for item in changes(before[key], after[key], (*path, key))]
    if isinstance(after, list) and len(before) == len(after):
        return [item for index, value in enumerate(after)
                for item in changes(before[index], value, (*path, index))]
    return [] if before == after else [{"path": list(path), "value": after}]


def frame(previous, current, version, *, reset=False):
    """Prefer a replacement when the delta is larger than the full snapshot."""
    full = {"type": "snapshot", "version": version + 1, "data": current}
    if previous is None or reset:
        return full
    delta = changes(previous, current)
    if not delta:
        return None
    patch = {"type": "delta", "base": version, "version": version + 1, "changes": delta}
    return patch if len(orjson.dumps(patch)) < len(orjson.dumps(full)) else full


def subscription(message):
    topic = message.get("topic")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("Invalid stream parameters")
    if topic not in {"dashboard", "accounts", "market-watch", "live-risk", "exposure", "performance", "antifraud", "client-live", "markout"}:
        raise ValueError("Unsupported stream topic")
    if topic in {"accounts", "market-watch"}:
        q = params.get("q", "")
        if not isinstance(q, str) or len(q) > 120:
            raise ValueError("Invalid search")
        params = {"q": q, **({"limit": params.get("limit", 250)} if topic == "market-watch"
                              else {"enabled_only": params.get("enabled_only", False)})}
        if topic == "market-watch" and (type(params["limit"]) is not int or not 1 <= params["limit"] <= 1000):
            raise ValueError("Invalid limit")
        if topic == "accounts" and type(params["enabled_only"]) is not bool:
            raise ValueError("Invalid account filter")
    elif topic == "dashboard":
        params = {}
    elif topic == "live-risk":
        # Manager secrets never need to travel over the stream or enter its keys.
        params = {key: value for key, value in params.items() if key in {
            "selected_groups", "excluded_logins", "group_filter_rules",
            "account_login", "symbol", "risk_category"}}
    else:
        params = {key: value for key, value in params.items() if key not in {"server", "login", "password", "refresh_token"}}
    return topic, params


def install(app):
    # Reuse encoded read views across tabs/users, matching the HTTP endpoints'
    # single configured Manager. Include the authenticated user in every key.
    views = {}
    views_lock = threading.Lock()

    def build(topic, params, user):
        if topic == "live-risk":
            import mt5_connection
            manager = mt5_connection.get_manager()
            metadata = manager.live_risk_cache_view(groups=[]) if manager and manager.is_connected else None
            if not (mt5_connection.FULL_SNAPSHOT_ENABLED and metadata
                    and metadata["last_update"] > 0 and not metadata["errors"]
                    and time.time() - metadata["last_update"] <= 30):
                raise RuntimeError("Live cache unavailable; use the HTTP scoped fallback")
        request = Request({"type": "http", "method": "GET", "path": "/api/live/ws",
                           "headers": [], "state": {"user": user}})
        if topic == "dashboard":
            return app.manager_dashboard(request)
        if topic == "accounts":
            return app.manager_accounts(request, **params)
        if topic == "market-watch":
            return app.manager_market_watch(request, **params)
        if topic == "exposure":
            return app.get_exposure(app.ExposureRequest(**params),request)
        if topic == "markout":
            return app.client_markout(app.MarkoutRequest(**params))
        if topic == "performance":
            return app.get_risk_performance(app.PerformanceRequest(**params))
        if topic == "antifraud":
            return app.get_antifraud_snapshot(app.AntiFraudRequest(**params))
        if topic == "client-live":
            req = app.ClientLiveRequest(**params)
            if app._cached_client_read(req.client_login) is None:
                raise RuntimeError("Live client cache is unavailable; use the scoped query fallback")
            return app.get_client_live(req)
        req = app.LiveRiskRequest(server="", login=0, password="", **params)
        return app.get_live_risk(req)

    def cached_build(topic, params, user):
        key = view_key(topic, params, user)
        with views_lock:
            now = time.monotonic()
            # No entry survives indefinitely after its subscribers leave.
            for old_key, entry in list(views.items()):
                if now - entry["used"] > 45:
                    del views[old_key]
            if key not in views:
                if len(views) >= 64:
                    del views[min(views, key=lambda item: views[item]["used"])]
                views[key] = {"lock": threading.Lock(), "used": now, "at": 0, "data": None}
            entry = views[key]
            entry["used"] = now
        with entry["lock"]:
            generation = None
            if topic in {"dashboard", "accounts", "live-risk", "exposure", "client-live"}:
                try:
                    import mt5_connection
                    manager = mt5_connection.get_manager()
                    if manager and manager.is_connected:
                        generation = manager.live_view_marker() if hasattr(manager, "live_view_marker") else manager.update_marker()
                except (ImportError, AttributeError):
                    pass
            age = time.monotonic() - entry["at"]
            unchanged = generation is not None and generation == entry.get("generation") and age < 2
            native_delivery = bool(app.os.getenv("RMS_NATIVE_URL"))
            minimum_age = 30 if topic == "markout" else 1.5 if topic == "antifraud" else (0.1 if native_delivery else 0.25) if topic in {"exposure", "live-risk"} else 0.1 if native_delivery else 0.4
            if not unchanged and age >= minimum_age:
                data = build(topic, params, user)
                entry["data"] = prepare_live_json(data) if topic == "live-risk" else jsonable_encoder(data, custom_encoder={float:finite_float})
                entry["at"] = time.monotonic()
                entry["generation"] = generation
            return entry["data"]

    shared_views = SharedLiveViews(cached_build)
    shared_reports = SharedLiveViews(cached_build, workers=2, pending=32)

    @app.api.websocket("/api/live/ws")
    async def live_websocket(websocket: WebSocket):
        token = websocket.cookies.get(app.SESSION_COOKIE_NAME, "")
        user = await asyncio.to_thread(app._verify_session_token, token)
        if not user or user.get("must_change_pw"):
            await websocket.close(code=1008)
            return
        # Mirror the HTTP origin policy for cookie-authenticated upgrades.
        origin = websocket.headers.get("origin")
        configured = [v.strip().rstrip("/") for v in app.os.getenv("ALLOWED_ORIGINS", "").split(",") if v.strip()]
        same_origin = str(websocket.base_url).replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
        if origin and origin.rstrip("/") != same_origin and origin.rstrip("/") not in configured:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        streams = {}
        requests = {}
        send_lock = asyncio.Lock()
        query_slots = asyncio.Semaphore(4)
        fast_slots = asyncio.Semaphore(4)

        async def send_message(payload):
            # Heartbeats must not queue behind a large JSON report encoding.
            encoded = ('{"type":"heartbeat"}' if payload.get("type") == "heartbeat" else
                       (await asyncio.to_thread(orjson.dumps, payload)).decode("utf-8"))
            async with send_lock:
                await websocket.send_text(encoded)

        async def answer(identifier, message):
            try:
                fast_path = str(message.get("path", "")).split("?", 1)[0] in {"/api/realtime/status", "/api/manager/status", "/api/book-pnl", "/api/settings/books", "/api/dealer-command-center", "/api/operations/live"}
                async with fast_slots if fast_path else query_slots:
                    result = await dispatch_read(app.api, websocket.scope, message)
                await send_message({"type": "response", "id": identifier, **result})
            except asyncio.CancelledError:
                raise
            except (WebSocketDisconnect, OSError):
                return
            except Exception:
                # The existing HTTP error handler records failures; do not send
                # traceback details or credentials to the browser.
                try:
                    await send_message({"type": "response", "id": identifier, "status": 500,
                                        "data": {"detail": "Data query failed; check the backend logs"}})
                except (WebSocketDisconnect, OSError, RuntimeError):
                    pass
            finally:
                requests.pop(identifier, None)

        async def publish(identifier, state):
            interval = 30 if state["topic"] == "markout" else (0.1 if app.os.getenv("RMS_NATIVE_URL") else 0.25) if state["topic"] in {"exposure", "live-risk"} else (0.1 if app.os.getenv("RMS_NATIVE_URL") else 0.5) if state["topic"] in {"dashboard", "market-watch", "accounts", "client-live"} else 1
            try:
                producer = shared_reports if state["topic"] in {"performance", "antifraud", "markout"} else shared_views
                current = await producer.read(view_key(state["topic"], state["params"], user),
                                              state["topic"], state["params"], user)
                if streams.get(identifier) is not state:
                    return
                if current is state["previous"] and time.monotonic() - state["snapshot_at"] < 30:
                    # A healthy socket alone cannot prove that this view is
                    # advancing. Acknowledge completed reads without inventing
                    # a source observation time or retransmitting a full book.
                    if time.monotonic() - state.get("progress_at", 0) >= 1:
                        await send_message({"id": identifier, "type": "progress", "version": state["version"]})
                        state["progress_at"] = time.monotonic()
                    return
                output = await asyncio.to_thread(frame, state["previous"], current, state["version"], reset=time.monotonic() - state["snapshot_at"] >= 30)
                if streams.get(identifier) is not state:
                    return
                if output:
                    await send_message({"id": identifier, **output})
                    state["progress_at"] = time.monotonic()
                    state["previous"] = current
                    state["version"] = output["version"]
                    if output["type"] == "snapshot":
                        state["snapshot_at"] = time.monotonic()
                elif time.monotonic() - state.get("progress_at", 0) >= 1:
                    await send_message({"id": identifier, "type": "progress", "version": state["version"]})
                    state["progress_at"] = time.monotonic()
            except (HTTPException, ValueError, RuntimeError) as exc:
                await send_message({"id": identifier, "type": "error", "detail": str(exc)})
                state["previous"] = None
                interval = 5
            except (WebSocketDisconnect, OSError):
                return
            except Exception:
                interval = 5
                try:
                    await send_message({"id": identifier, "type": "error", "detail": "Live view unavailable; using scoped queries"})
                except (WebSocketDisconnect, OSError, RuntimeError):
                    pass
            finally:
                state["next"] = time.monotonic() + interval
                state["task"] = None

        checked = time.monotonic()
        heartbeat = checked
        receiver = asyncio.create_task(websocket.receive_text())
        try:
            while True:
                try:
                    # Poll publication deadlines without cancelling the ASGI
                    # receiver. A timeout racing a completed receive can consume
                    # and lose a subscribe/query frame on a healthy socket.
                    received, _ = await asyncio.wait({receiver}, timeout=0.01 if app.os.getenv("RMS_NATIVE_URL") else 0.1)
                    if not received:
                        raise asyncio.TimeoutError
                    raw = receiver.result()
                    receiver = asyncio.create_task(websocket.receive_text())
                    if len(raw) > 2 * 1024 * 1024:
                        await websocket.close(code=1009)
                        return
                    message = json.loads(raw)
                    identifier = message.get("id")
                    if not isinstance(identifier, str) or len(identifier) > 128:
                        raise ValueError("Invalid stream identifier")
                    if message.get("type") == "request":
                        if identifier in requests or identifier in streams:
                            await send_message({"type": "response", "id": identifier, "status": 409,
                                                "data": {"detail": "Duplicate request identifier"}})
                        elif len(requests) >= 32:
                            await send_message({"type": "response", "id": identifier, "status": 429,
                                                "data": {"detail": "Too many pending data queries"}})
                        else:
                            requests[identifier] = asyncio.create_task(answer(identifier, message))
                    elif message.get("type") == "cancel":
                        task = requests.get(identifier)
                        if task:
                            task.cancel()
                    elif message.get("type") == "unsubscribe":
                        previous = streams.pop(identifier, None)
                        if previous and previous.get("task"):
                            previous["task"].cancel()
                    elif message.get("type") == "subscribe":
                        if identifier not in streams and len(streams) >= 8:
                            raise ValueError("Too many subscriptions")
                        topic, params = subscription(message)
                        previous = streams.get(identifier)
                        if previous and previous.get("task"):
                            previous["task"].cancel()
                        streams[identifier] = {"topic": topic, "params": params, "previous": None,
                                               "version": 0, "next": 0, "snapshot_at": 0}
                    else:
                        raise ValueError("Unsupported stream message")
                except asyncio.TimeoutError:
                    pass
                except (ValueError, TypeError, AttributeError):
                    await websocket.close(code=1008)
                    return
                now = time.monotonic()
                if now - checked >= 15:
                    user = await asyncio.to_thread(app._verify_session_token, token)
                    if not user or user.get("must_change_pw"):
                        await websocket.close(code=1008)
                        return
                    checked = now
                for identifier, state in list(streams.items()):
                    if now < state["next"] or state.get("task"):
                        continue
                    state["task"] = asyncio.create_task(publish(identifier, state))
                if now - heartbeat >= 5:
                    await send_message({"type": "heartbeat"})
                    heartbeat = now
        except (WebSocketDisconnect, OSError):
            return
        finally:
            pending = [receiver, *requests.values(), *(state["task"] for state in streams.values() if state.get("task"))]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
