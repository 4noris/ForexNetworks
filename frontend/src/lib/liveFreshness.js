const interrupted = new Set(["warming", "offline", "disconnected", "error"]);

export function sourceTimeMs(data) {
  const value = data?.source_updated_at;
  if (typeof value === "number" && Number.isFinite(value)) return value > 1e12 ? value : value * 1000;
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : null;
}

export function currentLiveSnapshot(data, now = Date.now()) {
  const stamp = sourceTimeMs(data);
  return Boolean(data) && !interrupted.has(data.status) && stamp !== null && now - stamp <= 10000;
}

export function mergeLiveSnapshot(current, incoming) {
  if (!incoming) return current;
  if (interrupted.has(incoming.status)) return current?.generated_at
    ? { ...current, status: incoming.status, warnings: incoming.warnings || current.warnings }
    : incoming;
  const previous = sourceTimeMs(current), next = sourceTimeMs(incoming);
  if (previous !== null && next !== null) {
    if (next < previous) return current;
    if (next === previous && Date.parse(incoming.generated_at || "") < Date.parse(current.generated_at || "")) return current;
  }
  return incoming;
}
