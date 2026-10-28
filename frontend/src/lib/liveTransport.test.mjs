import assert from "node:assert/strict";
import { test } from "node:test";
import { applyChanges, streamDescriptor, readLiveData, requestLiveData, clearLiveTransport, watchLiveData, invalidateLiveData } from "./liveTransport.js";
import { currentLiveSnapshot, mergeLiveSnapshot } from "./liveFreshness.js";
import { isDataRead } from "./readRequests.js";

test("per-view watchdog repairs stalled updates despite healthy socket heartbeats", () => {
  const now = Date.now, interval = globalThis.setInterval, clear = globalThis.clearInterval;
  let clock = 1_800_000_000_000, sweep, updates = 0;
  Date.now = () => clock;
  globalThis.setInterval = (callback) => { sweep = callback; return 1; };
  globalThis.clearInterval = () => {};
  globalThis.window = new EventTarget();
  globalThis.document = { hidden: false };
  globalThis.localStorage = { getItem: () => '{"id":1}' };
  globalThis.CustomEvent = class extends Event { constructor(name, options) { super(name); this.detail = options.detail; } };
  class Socket {
    static instances = [];
    constructor() { this.readyState = 0; this.sent = []; Socket.instances.push(this); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; }
  }
  globalThis.WebSocket = Socket;
  const options = { method: "POST", body: '{"selected_groups":["fixture"]}' };
  const read = () => readLiveData("/api/risk/live", options, "ws://localhost/api/live/ws");
  try {
    const stop = watchLiveData("/api/risk/live", options, () => updates++, "ws://localhost/api/live/ws");
    const socket = Socket.instances[0];
    socket.readyState = 1; socket.onopen();
    const id = socket.sent[0].id;
    const receive = (message) => socket.onmessage({ data: JSON.stringify({ id, ...message }) });
    const snapshot = () => ({ status: "connected", source_updated_at: clock / 1000 });
    receive({ type: "snapshot", version: 1, data: snapshot() });
    assert.ok(read());
    let cached;
    const stopCached = watchLiveData("/api/risk/live", options, (value) => { cached = value; }, "ws://localhost/api/live/ws");
    assert.deepEqual(cached, snapshot(), "a joining observer immediately receives the verified cached view");
    stopCached();
    clock += 4000;
    receive({ type: "heartbeat" });
    sweep();
    assert.equal(socket.sent.length, 2, "heartbeat does not suppress view repair");
    assert.equal(read(), undefined, "stalled cached values cannot suppress fresh queries");
    receive({ type: "snapshot", version: 1, data: snapshot() });
    assert.ok(read());
    clock += 2000;
    receive({ type: "progress", version: 1 });
    clock += 2000;
    sweep();
    assert.equal(socket.sent.length, 2, "verified unchanged view avoids full retransmission");
    assert.ok(read());
    invalidateLiveData();
    assert.equal(socket.sent.length, 3, "mutation preserves and refreshes active subscription");
    receive({ type: "snapshot", version: 1, data: snapshot() });
    assert.equal(updates, 3);
    receive({ type: "snapshot", version: 2, data: { status: "connected", source_updated_at: (clock - 60000) / 1000 } });
    assert.equal(read(), undefined, "a new frame does not make old source data fresh");
    clock += 4000;
    receive({ type: "heartbeat" }); sweep();
    assert.equal(socket.sent.length, 4);
    // Even progress acknowledgements cannot hide an old source timestamp.
    receive({ type: "snapshot", version: 1, data: snapshot() });
    clock += 11000;
    receive({ type: "progress", version: 1 }); sweep();
    assert.equal(read(), undefined);
    clock += 16000;
    sweep();
    const reconnecting = Socket.instances.at(-1);
    assert.notEqual(reconnecting, socket);
    assert.equal(socket.readyState, 3);
    clock += 11000;
    sweep();
    assert.equal(reconnecting.readyState, 3, "a hanging WebSocket upgrade is closed instead of connecting forever");
    stop();
  } finally {
    clearLiveTransport();
    Date.now = now; globalThis.setInterval = interval; globalThis.clearInterval = clear;
    for (const key of ["window", "document", "localStorage", "CustomEvent", "WebSocket"]) delete globalThis[key];
  }
});

test("out-of-order results cannot replace newer verified values; outages preserve their actual age", () => {
  const now = 1_800_000_000_000;
  const latest = { status: "connected", generated_at: new Date(now).toISOString(), source_updated_at: now / 1000, value: 99 };
  const old = { ...latest, source_updated_at: (now - 60000) / 1000, value: 1 };
  assert.equal(mergeLiveSnapshot(latest, old), latest);
  assert.equal(mergeLiveSnapshot(latest, { ...latest, generated_at: new Date(now - 1000).toISOString(), value: 1 }), latest);
  assert.equal(currentLiveSnapshot(old, now), false);
  const outage = mergeLiveSnapshot(latest, { status: "offline", source_updated_at: (now + 1000) / 1000 });
  assert.equal(outage.value, 99);
  assert.equal(outage.source_updated_at, latest.source_updated_at);
  assert.equal(currentLiveSnapshot(outage, now), false);
});

test("read-only allowlist and scope keys exclude manager credentials", () => {
  assert.equal(streamDescriptor("/api/manager/connect", { method: "POST" }), null);
  const descriptor = streamDescriptor("/api/risk/live", { method: "POST", body: JSON.stringify({
    selected_groups: ["live"], account_login: 1, password: "secret", server: "private",
  }) });
  assert.equal(descriptor.key.includes("secret"), false);
  assert.equal(descriptor.key.includes("private"), false);
  assert.notEqual(descriptor.key, streamDescriptor("/api/risk/live", {
    method: "POST", body: JSON.stringify({ selected_groups: ["other"], account_login: 1 }),
  }).key);
});

test("all UI data areas use RPC while mutations, auth and downloads remain HTTP", () => {
  for (const path of ["/api/calendar", "/api/dealing-risk/analytics/accounts?login=7",
    "/api/settings/books", "/api/operations/cases", "/api/risk-automation/overview"])
    assert.equal(isDataRead(path), true, path);
  for (const path of ["/api/analytics", "/api/reports/deal", "/api/risk/analyse", "/api/operations/live"])
    assert.equal(isDataRead(path, { method: "POST" }), true, path);
  for (const [path, method] of [["/api/settings/books", "PUT"], ["/api/operations/cases", "POST"],
    ["/api/auth/session", "GET"], ["/api/reports/deal/export", "POST"],
    ["/api/dealing-risk/analytics/exports/id/download", "GET"], ["//other.invalid/api/calendar", "GET"]])
    assert.equal(isDataRead(path, { method }), false, path);
});

test("Exposure and P&L consumers share one subscription regardless of filter ordering", () => {
  const target = new EventTarget();
  globalThis.window = target;
  globalThis.document = { hidden: false };
  globalThis.localStorage = { getItem: () => '{"id":1}' };
  globalThis.CustomEvent = class extends Event { constructor(name, options) { super(name); this.detail = options.detail; } };
  class Socket {
    static instances = [];
    constructor() { this.readyState = 0; this.sent = []; Socket.instances.push(this); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; }
  }
  globalThis.WebSocket = Socket;
  let count = 0;
  try {
    const stop1 = watchLiveData("/api/risk/performance", { method: "POST", body: JSON.stringify({ selected_groups: ["b", "a"], lookback_days: 31, password: "secret" }) }, () => count++, "ws://localhost/api/live/ws");
    const stop2 = watchLiveData("/api/risk/performance", { method: "POST", body: JSON.stringify({ lookback_days: 31, selected_groups: ["a", "b"] }) }, () => count++, "ws://localhost/api/live/ws");
    const socket = Socket.instances[0];
    socket.readyState = 1;
    socket.onopen();
    assert.equal(socket.sent.filter((item) => item.type === "subscribe").length, 1);
    assert.equal(JSON.stringify(socket.sent).includes("secret"), false);
    socket.onmessage({ data: JSON.stringify({ type: "snapshot", id: socket.sent[0].id, version: 1, data: { periods: {} } }) });
    assert.equal(count, 2);
    stop1(); stop2();
  } finally {
    clearLiveTransport();
    for (const key of ["window", "document", "localStorage", "CustomEvent", "WebSocket"]) delete globalThis[key];
  }
});

test("patches preserve original snapshots and remove closed positions", () => {
  const initial = { positions: [{ id: 1, profit: 10 }, { id: 2, profit: 20 }] };
  const updated = applyChanges(initial, [{ path: ["positions", 0, "profit"], value: 11 }]);
  assert.equal(initial.positions[0].profit, 10);
  assert.equal(updated.positions[0].profit, 11);
  assert.deepEqual(applyChanges(updated, [{ path: ["positions"], value: [] }]), { positions: [] });
  assert.throws(() => applyChanges(initial, [{ path: ["__proto__", "bad"], value: true }]));
  assert.throws(() => applyChanges(initial, [{ path: ["missing", 0], value: 1 }]));
});

test("the final observer pauses its stream immediately and returning observers reuse verified data", () => {
  globalThis.window = new EventTarget();
  globalThis.document = { hidden: false };
  globalThis.localStorage = { getItem: () => '{"id":1}' };
  globalThis.CustomEvent = class extends Event { constructor(name, options) { super(name); this.detail = options.detail; } };
  class Socket {
    static instances = [];
    constructor() { this.readyState=0; this.sent=[]; Socket.instances.push(this); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.readyState=3; }
  }
  globalThis.WebSocket = Socket;
  try {
    const url="ws://localhost/api/live/ws";
    const stop1=watchLiveData("/api/manager/dashboard", {}, () => {}, url);
    const stop2=watchLiveData("/api/manager/dashboard", {}, () => {}, url);
    const socket=Socket.instances[0]; socket.readyState=1; socket.onopen();
    const id=socket.sent[0].id;
    socket.onmessage({ data:JSON.stringify({ id,type:"snapshot",version:1,data:{equity:123.45} }) });
    stop1();
    assert.equal(socket.sent.filter((frame) => frame.type==="unsubscribe").length,0);
    stop2();
    assert.deepEqual(socket.sent.at(-1),{type:"unsubscribe",id});
    let cached;
    const stop3=watchLiveData("/api/manager/dashboard", {}, (value) => { cached=value; }, url);
    assert.deepEqual(cached,{equity:123.45});
    assert.equal(socket.sent.filter((frame) => frame.type==="subscribe").length,2);
    stop3();
  } finally {
    clearLiveTransport();
    for(const key of ["window","document","localStorage","CustomEvent","WebSocket"]) delete globalThis[key];
  }
});

test("socket cache falls back before snapshot, rejects gaps and clears on logout", () => {
  const received = [];
  globalThis.window = { dispatchEvent: (event) => received.push(event.detail) };
  globalThis.document = { hidden: false };
  globalThis.localStorage = { getItem: () => '{"id":1}' };
  globalThis.CustomEvent = class { constructor(name, options) { this.detail = options.detail; } };
  class Socket {
    static instances = [];
    constructor() { this.readyState = 0; Socket.instances.push(this); }
    send(value) { this.sent = JSON.parse(value); }
    close() { this.readyState = 3; }
  }
  globalThis.WebSocket = Socket;
  try {
    const read = () => readLiveData("/api/manager/dashboard", {}, "ws://localhost/api/live/ws");
    assert.equal(read(), undefined);
    const socket = Socket.instances[0];
    socket.readyState = 1;
    socket.onopen();
    const id = socket.sent.id;
    socket.onmessage({ data: JSON.stringify({ id, type: "snapshot", version: 1, data: { count: 5 } }) });
    assert.deepEqual(read(), { count: 5 });
    socket.onmessage({ data: JSON.stringify({ id, type: "delta", base: 1, version: 2, changes: [{ path: ["count"], value: 6 }] }) });
    assert.deepEqual(read(), { count: 6 });
    assert.equal(received.length, 2);
    socket.onmessage({ data: JSON.stringify({ id, type: "delta", base: 8, version: 9, changes: [] }) });
    assert.equal(read(), undefined);
    clearLiveTransport();
    assert.equal(socket.readyState, 3);
  } finally {
    clearLiveTransport();
    for (const key of ["window", "document", "localStorage", "CustomEvent", "WebSocket"]) delete globalThis[key];
  }
});

test("RPC correlates out-of-order replies, preserves errors and cancels independently", async () => {
  globalThis.window = { dispatchEvent() {} };
  globalThis.document = { hidden: false };
  globalThis.localStorage = { getItem: () => '{"id":1}' };
  class Socket {
    static instances = [];
    constructor() { this.readyState = 0; this.sent = []; Socket.instances.push(this); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; }
  }
  globalThis.WebSocket = Socket;
  try {
    const first = requestLiveData("/api/calendar", {}, "ws://localhost/api/live/ws");
    const socket = Socket.instances[0];
    socket.readyState = 1;
    socket.onopen();
    const controller = new AbortController();
    const cancelled = requestLiveData("/api/settings/books", { signal: controller.signal }, "ws://localhost/api/live/ws");
    const second = requestLiveData("/api/operations/cases", {}, "ws://localhost/api/live/ws");
    const requests = socket.sent.filter((message) => message.type === "request");
    socket.onmessage({ data: JSON.stringify({ type: "response", id: requests[2].id, status: 403, data: { detail: "Administrator required" } }) });
    assert.equal((await second).status, 403);
    controller.abort();
    await assert.rejects(cancelled, { name: "AbortError" });
    assert.equal(socket.sent.at(-1).type, "cancel");
    socket.onmessage({ data: JSON.stringify({ type: "response", id: requests[0].id, status: 200, data: { events: [] } }) });
    assert.deepEqual((await first).data, { events: [] });
    const interrupted = requestLiveData("/api/reports/brokerage/audited", { method: "POST", body: "{}" }, "ws://localhost/api/live/ws");
    socket.onclose();
    await assert.rejects(interrupted, /Connection closed/);
    const unavailable = requestLiveData("/api/calendar", {}, "ws://localhost/api/live/ws");
    Socket.instances.at(-1).onclose();
    assert.equal(await unavailable, undefined); // Not sent: normal HTTP fallback.
  } finally {
    clearLiveTransport();
    for (const key of ["window", "document", "localStorage", "WebSocket"]) delete globalThis[key];
  }
});
