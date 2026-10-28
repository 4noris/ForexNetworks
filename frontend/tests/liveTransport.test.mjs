import assert from "node:assert/strict";
import { test } from "node:test";

test("a stream sequence gap resnapshots only that view and preserves pending reads", async () => {
  globalThis.window = new EventTarget();
  globalThis.document = Object.assign(new EventTarget(), { hidden: false });
  globalThis.localStorage = { getItem: () => JSON.stringify({ id: 1 }) };
  class Socket {
    static instances = [];
    readyState = 0;
    messages = [];
    closed = 0;
    constructor() { Socket.instances.push(this); }
    send(raw) { this.messages.push(JSON.parse(raw)); }
    open() { this.readyState = 1; this.onopen?.(); }
    deliver(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
    close() { this.closed++; this.readyState = 3; this.onclose?.(); }
  }
  globalThis.WebSocket = Socket;
  const transport = await import("../src/lib/liveTransport.js");
  const url = "ws://test-only/api/live/ws";
  try {
    transport.readLiveData("/api/manager/dashboard", {}, url);
    transport.readLiveData("/api/manager/market-watch", {}, url);
    const socket = Socket.instances[0];
    socket.open();
    const dashboard = socket.messages.find((m) => m.topic === "dashboard");
    const quotes = socket.messages.find((m) => m.topic === "market-watch");
    socket.deliver({ id: dashboard.id, type: "snapshot", version: 1, data: { equity: 100 } });
    socket.deliver({ id: quotes.id, type: "snapshot", version: 1, data: { bid: 1.1 } });
    const pending = transport.requestLiveData("/api/manager/status", {}, url);
    const request = socket.messages.find((m) => m.type === "request");
    assert.ok(request);
    socket.deliver({ id: dashboard.id, type: "delta", base: 99, version: 100, changes: [] });
    assert.equal(socket.closed, 0);
    assert.equal(Socket.instances.length, 1);
    assert.equal(socket.messages.filter((m) => m.type === "subscribe" && m.id === dashboard.id).length, 2);
    assert.equal(socket.messages.filter((m) => m.type === "subscribe" && m.id === quotes.id).length, 1);
    socket.deliver({ id: request.id, type: "response", status: 200, data: { status: "connected" } });
    assert.equal((await pending).data.status, "connected");
    assert.deepEqual(transport.readLiveData("/api/manager/market-watch", {}, url), { bid: 1.1 });
    socket.deliver({ id: dashboard.id, type: "snapshot", version: 1, data: { equity: 101 } });
    assert.deepEqual(transport.readLiveData("/api/manager/dashboard", {}, url), { equity: 101 });
  } finally {
    transport.clearLiveTransport();
  }
});
