import { useEffect, useState } from "react";
import { apiRequest } from "../lib/api";

type NativeStatus = {
  source: string;
  ready: boolean;
  connected?: boolean;
  native_enabled?: boolean;
  collector_to_gateway_p95_ms?: number | null;
  ages?: Record<string, number | null>;
};

function parseStatus(value: unknown): NativeStatus | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (typeof record.source !== "string" || typeof record.ready !== "boolean") return null;
  return record as NativeStatus;
}

export default function RealtimeStatus() {
  const [status, setStatus] = useState<NativeStatus | null>(null);
  useEffect(() => {
    let stopped = false;
    let pending = false;
    let controller: AbortController | undefined;
    const refresh = async () => {
      if (document.hidden || pending) return;
      pending = true;
      controller = new AbortController();
      try {
        const value: unknown = await apiRequest("/api/realtime/status", { signal: controller.signal, timeoutMs: 4000 });
        if (!stopped) setStatus(parseStatus(value));
      } catch {
        if (!stopped) setStatus(null);
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 3000);
    return () => { stopped = true; controller?.abort(); window.clearInterval(timer); };
  }, []);

  const native = status?.source === "mt5-cpp-native";
  const ready = native && status.ready;
  const latency = status?.collector_to_gateway_p95_ms;
  const age = status?.ages?.position_age_ms;
  const detail = typeof latency === "number" && Number.isFinite(latency)
    ? `Collector to Go post-sync p95: ${latency === 0 ? "<1" : latency} ms` : "Latency unavailable";
  return <div role="status" aria-live="polite" className={`mb-3 h-16 min-h-16 overflow-hidden sm:h-8 sm:min-h-8 flex flex-wrap items-center justify-between gap-2 text-[11px] ${ready ? "text-success" : "text-amber-400"}`}>
    <span>{native ? ready ? "MT5 native feed · C++ → Go" : "MT5 native feed unavailable or synchronizing · live values are not current" : status ? "MT5 polling feed · native bridge is not active" : "MT5 source status unavailable"}</span>
    {native && <span className="text-muted" title="This measures collector-to-gateway transport, not total browser latency. Price, account and position observations have separate source timestamps.">{detail}{typeof age === "number" && Number.isFinite(age) ? ` · Position observation: ${(Math.max(0, age) / 1000).toFixed(1)}s ago` : ""}</span>}
  </div>;
}
