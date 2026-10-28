// One multiplexed, session-private socket for live updates and JSON data queries.
import { isDataRead } from "./readRequests.js";
import { sourceTimeMs } from "./liveFreshness.js";
const entries = new Map();
const watchers = new Map();
const requests = new Map();
let socket;
let retryTimer;
let sweepTimer;
let identity;
let lastFrame = 0;
let connectionStartedAt = 0;
let retryMs = 1000;
let streamUrl;
let sequence = 0;
let lastUsed = 0;
let sessionChanged = false;

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}

export function refreshLiveSession() {
  // The next idle read reconnects with the browser's newly renewed cookie.
  sessionChanged = true;
}

export function streamDescriptor(path, options = {}) {
  const url = new URL(path, "http://rms.local");
  const method = (options.method || "GET").toUpperCase();
  let topic;
  let params = {};
  if (method === "GET") {
    topic = { "/api/manager/dashboard": "dashboard", "/api/manager/accounts": "accounts",
      "/api/manager/market-watch": "market-watch" }[url.pathname];
    if (topic === "accounts") params = { q: url.searchParams.get("q") || "", enabled_only: url.searchParams.get("enabled_only") === "true" };
    if (topic === "market-watch") params = { q: url.searchParams.get("q") || "", limit: Number(url.searchParams.get("limit") || 250) };
  } else if (method === "POST" && ["/api/risk/live", "/api/risk/exposure", "/api/risk/performance", "/api/risk/antifraud", "/api/risk/client-live", "/api/risk/markout"].includes(url.pathname)) {
    topic = { "/api/risk/live": "live-risk", "/api/risk/exposure": "exposure", "/api/risk/performance": "performance", "/api/risk/antifraud": "antifraud", "/api/risk/client-live": "client-live", "/api/risk/markout": "markout" }[url.pathname];
    let body;
    try { body = JSON.parse(options.body || "{}"); } catch { return null; }
    if (body.refresh_token) return null;
    if (topic === "live-risk") {
      for (const key of ["selected_groups", "excluded_logins", "group_filter_rules", "account_login", "symbol", "risk_category"])
        if (body[key] !== undefined) params[key] = body[key];
    } else {
      params = Object.fromEntries(Object.entries(body).filter(([key]) => !["server", "login", "password", "refresh_token"].includes(key)));
    }
    if (params.selected_groups) params.selected_groups = [...params.selected_groups].sort();
    if (params.excluded_logins) params.excluded_logins = [...params.excluded_logins].sort((a, b) => a - b);
  }
  return topic ? { topic, params, key: JSON.stringify(canonical([topic, params])) } : null;
}

export function watchLiveData(path, options, listener, url) {
  const descriptor = streamDescriptor(path, options);
  if (!descriptor || typeof window === "undefined") return () => {};
  const cached = readLiveData(path, options, url);
  const entry = entries.get(descriptor.key);
  if (!entry) return () => {};
  watchers.set(descriptor.key, (watchers.get(descriptor.key) || 0) + 1);
  const update = (event) => { if (event.detail?.key === descriptor.key) listener(event.detail.data); };
  window.addEventListener("rms:live-data", update);
  if (cached !== undefined) listener(cached);
  return () => {
    window.removeEventListener("rms:live-data", update);
    const count = Math.max(0, (watchers.get(descriptor.key) || 0) - 1);
    if (count) watchers.set(descriptor.key, count);
    else watchers.delete(descriptor.key);
    entry.used = Date.now();
    if (!count && entries.get(descriptor.key) === entry) {
      entry.active = false;
      if (socket?.readyState === 1) socket.send(JSON.stringify({ type: "unsubscribe", id: entry.id }));
    }
  };
}

export function applyChanges(data, changes) {
  let result = data;
  const owned = new WeakSet();
  for (const change of changes) {
    const path = change.path;
    if (!Array.isArray(path) || path.some((key) => ["__proto__", "constructor", "prototype"].includes(key)))
      throw new Error("Invalid stream patch");
    const replace = (node, index) => {
      if (index === path.length) return change.value;
      const key = path[index];
      if (node === null || typeof node !== "object" || !Object.hasOwn(node, key))
        throw new Error("Stream patch does not match snapshot");
      const copy = owned.has(node) ? node : Array.isArray(node) ? [...node] : { ...node };
      owned.add(copy);
      copy[key] = replace(node[key], index + 1);
      return copy;
    };
    result = replace(result, 0);
  }
  return result;
}

function frameDeadline(entry) {
  if (["performance", "antifraud", "markout"].includes(entry.topic)) return 180000;
  return 3000;
}

function sourceExpired(entry, now) {
  if (!["live-risk", "exposure", "client-live"].includes(entry.topic)) return false;
  const stamp = sourceTimeMs(entry.data);
  return stamp !== null && now - stamp > 10000;
}

function freshEntry(entry, now) {
  return entry.data !== undefined && entry.progressAt && now - entry.progressAt <= frameDeadline(entry) && !sourceExpired(entry, now);
}

function repairEntry(entry, now) {
  if (!entry.active || socket?.readyState !== 1 || document.hidden) return;
  if (now - (entry.sentAt || 0) < frameDeadline(entry)) return;
  if (freshEntry(entry, now)) return;
  entry.data = undefined;
  entry.version = 0;
  send(entry);
}

export function invalidateLiveData() {
  // Writes invalidate values, not the long-lived subscriptions watching them.
  for (const entry of entries.values()) {
    entry.data = undefined;
    entry.version = 0;
    if (watchers.get(entry.key)) send(entry);
  }
}

function send(entry) {
  if (entry.active && socket?.readyState === 1) {
    entry.sentAt = Date.now();
    socket.send(JSON.stringify({ type: "subscribe", id: entry.id, topic: entry.topic, params: entry.params }));
  }
}

function disconnect() {
  const previous = socket;
  socket = undefined;
  lastFrame = 0;
  connectionStartedAt = 0;
  for (const entry of entries.values()) { entry.data = undefined; entry.version = 0; entry.progressAt = 0; }
  for (const request of [...requests.values()]) {
    const error = new Error("Connection closed while fetching data. Retry the query.");
    error.wsRequest = true;
    request.finish(request.sent ? error : null);
  }
  if (previous) { previous.onclose = null; previous.close(); }
}

export function clearLiveTransport() {
  clearTimeout(retryTimer);
  clearInterval(sweepTimer);
  sweepTimer = undefined;
  disconnect();
  entries.clear();
  identity = undefined;
}

function connect() {
  if (socket || document.hidden || (![...entries.values()].some((entry) => entry.active) && !requests.size)) return;
  clearTimeout(retryTimer);
  let current;
  try { current = new WebSocket(streamUrl); } catch { scheduleRetry(); return; }
  socket = current;
  connectionStartedAt = Date.now();
  current.onopen = () => {
    if (socket !== current) return;
    lastFrame = Date.now();
    retryMs = 1000;
    for (const entry of entries.values()) send(entry);
    for (const request of requests.values()) request.send();
  };
  current.onmessage = (event) => {
    if (socket !== current) return;
    let entry;
    try {
      const message = JSON.parse(event.data);
      lastFrame = Date.now();
      if (message.type === "heartbeat") return;
      if (message.type === "response") {
        const request = requests.get(message.id);
        if (request) request.finish(null, { status: message.status,
          ok: message.status >= 200 && message.status < 300, data: message.data });
        return;
      }
      entry = [...entries.values()].find((item) => item.id === message.id);
      if (!entry) return;
      if (message.type === "progress") {
        if (entry.data !== undefined && entry.version === message.version) entry.progressAt = Date.now();
        return;
      }
      if (message.type === "error") { entry.data = undefined; entry.version = 0; return; }
      if (message.type === "snapshot") entry.data = message.data;
      else if (message.type === "delta") {
        if (entry.data === undefined || entry.version !== message.base) throw new Error("Stream sequence gap");
        entry.data = applyChanges(entry.data, message.changes);
      } else return;
      entry.version = message.version;
      entry.progressAt = Date.now();
      window.dispatchEvent(new CustomEvent("rms:live-data", { detail: { key: entry.key, topic: entry.topic, data: entry.data } }));
    } catch {
      // Resnapshot only the affected view. Other streams and pending queries
      // can continue over the existing authenticated connection.
      if (entry && socket === current && current.readyState === 1) {
        entry.data = undefined;
        entry.version = 0;
        send(entry);
      } else {
        disconnect();
        scheduleRetry();
      }
    }
  };
  current.onclose = () => {
    if (socket !== current) return;
    disconnect();
    scheduleRetry();
  };
  current.onerror = () => current.close();
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, retryMs);
  retryMs = Math.min(30_000, retryMs * 2);
}

function sweep() {
  if (sweepTimer) return;
  sweepTimer = setInterval(() => {
    if (!document.hidden && socket?.readyState === 0 && Date.now() - connectionStartedAt >= 10000) {
      disconnect();
      scheduleRetry();
    }
    if (!document.hidden && socket?.readyState === 1 && lastFrame && Date.now() - lastFrame > 15000) {
      disconnect();
      connect();
    }
    for (const [key, value] of entries) {
      if (watchers.get(key)) repairEntry(value, Date.now());
      if (!watchers.get(key) && Date.now() - value.used > 45_000) {
        if (value.active && socket?.readyState === 1) socket.send(JSON.stringify({ type: "unsubscribe", id: value.id }));
        entries.delete(key);
      }
    }
    if (!entries.size && !requests.size && Date.now() - lastUsed > 45_000) clearLiveTransport();
  }, 1000);
}

function prepare(url) {
  const user = localStorage.getItem("user");
  if (!user) return false;
  if (identity !== user) { clearLiveTransport(); identity = user; }
  if (sessionChanged && !requests.size) { disconnect(); sessionChanged = false; }
  streamUrl = url;
  lastUsed = Date.now();
  if (socket && lastFrame && Date.now() - lastFrame > 15_000) disconnect();
  sweep();
  return true;
}

export function requestLiveData(path, options, url) {
  if (!isDataRead(path, options) || typeof window === "undefined" || document.hidden ||
      options?.headers || requests.size >= 32) return Promise.resolve(undefined);
  if (options?.signal?.aborted) {
    const error = new Error("Request was cancelled");
    error.name = "AbortError";
    return Promise.reject(error);
  }
  if (!prepare(url)) return Promise.resolve(undefined);
  let body;
  try { body = options?.body ? JSON.parse(options.body) : undefined; }
  catch { return Promise.resolve(undefined); }
  return new Promise((resolve, reject) => {
    const id = `read-${++sequence}`;
    let readyTimer;
    const abort = () => {
      if (socket?.readyState === 1 && entry.sent) socket.send(JSON.stringify({ type: "cancel", id }));
      const error = new Error("Request was cancelled");
      error.name = "AbortError";
      entry.finish(error);
    };
    const entry = {
      sent: false,
      finish(error, result) {
        if (!requests.delete(id)) return;
        clearTimeout(readyTimer);
        options?.signal?.removeEventListener("abort", abort);
        if (error) reject(error);
        else resolve(result);
      },
      send() {
        if (entry.sent || socket?.readyState !== 1) return;
        clearTimeout(readyTimer);
        try {
          socket.send(JSON.stringify({ type: "request", id, path, method: (options?.method || "GET").toUpperCase(), body }));
          entry.sent = true;
        } catch { entry.finish(null); }
      },
    };
    requests.set(id, entry);
    options?.signal?.addEventListener("abort", abort, { once: true });
    // If upgrades are unavailable, HTTP can start promptly. Sent queries are
    // never blindly repeated after a drop; report generation can create jobs.
    readyTimer = setTimeout(() => entry.finish(null), 1500);
    connect();
    entry.send();
  });
}

export function readLiveData(path, options, url) {
  const descriptor = streamDescriptor(path, options);
  if (!descriptor || typeof window === "undefined" || options?.signal?.aborted) return undefined;
  if (!prepare(url)) return undefined;
  let entry = entries.get(descriptor.key);
  if (!entry) {
    if (entries.size >= 8) {
      const oldest = [...entries.values()].filter((item) => !watchers.get(item.key)).sort((a, b) => a.used - b.used)[0];
      if (!oldest) return undefined;
      if (socket?.readyState === 1) socket.send(JSON.stringify({ type: "unsubscribe", id: oldest.id }));
      entries.delete(oldest.key);
    }
    entry = { ...descriptor, id: String(++sequence), data: undefined, version: 0, active: true };
    entries.set(entry.key, entry);
    send(entry);
  } else if (!entry.active) {
    entry.active = true;
    entry.version = 0;
    send(entry);
  }
  entry.used = Date.now();
  connect();
  repairEntry(entry, Date.now());
  return socket?.readyState === 1 && Date.now() - lastFrame < 15_000 && freshEntry(entry, Date.now()) ? entry.data : undefined;
}

if (typeof window !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && !requests.size) disconnect();
    else connect();
  });
  window.addEventListener("storage", () => {
    if (identity !== localStorage.getItem("user")) clearLiveTransport();
  });
}
