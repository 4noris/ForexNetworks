import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, subscribeLiveData } from "./api";
import { currentLiveSnapshot, mergeLiveSnapshot } from "./liveFreshness";

const interrupted = new Set(["warming", "offline", "disconnected", "error"]);

export function useExposureData(params, active) {
  const [state, setState] = useState(null);
  const [transportError, setTransportError] = useState("");
  const refreshAction = useRef(null);
  const requestKey = JSON.stringify(params);
  const scopeKey = JSON.stringify([params.selected_groups, params.group, params.symbol, params.query,
    params.direction, params.risk_status, params.money_currency]);
  useEffect(() => {
    if (!active || !JSON.parse(requestKey).selected_groups?.length) return undefined;
    let cancelled = false;
    let controller = null;
    let lastPushedAt = 0;
    const receive = (incoming) => {
      if (cancelled) return;
      setTransportError("");
      setState((current) => {
        const previous = current?.scopeKey === scopeKey ? current.data : null;
        const data = mergeLiveSnapshot(previous, incoming);
        return { scopeKey, data: previous?.generated_at && interrupted.has(incoming?.status)
          ? { ...data, status: "delayed" } : data };
      });
    };
    const query = async (force = false) => {
      if (cancelled || document.hidden || controller || (!force && Date.now() - lastPushedAt < 3000)) return;
      const pending = new AbortController();
      controller = pending;
      try {
        const incoming = await apiRequest("/api/risk/exposure", {
          method: "POST", body: requestKey, signal: pending.signal, timeoutMs: 8000,
        });
        if (!pending.signal.aborted) receive(incoming);
      } catch (error) {
        if (!cancelled && !pending.signal.aborted) setTransportError(error.message || "Live exposure is reconnecting");
      } finally {
        if (controller === pending) controller = null;
      }
    };
    const stop = subscribeLiveData("/api/risk/exposure", JSON.parse(requestKey), (incoming) => {
      if (cancelled || document.hidden) return;
      if (currentLiveSnapshot(incoming)) {
        lastPushedAt = Date.now();
        controller?.abort();
        controller = null;
      }
      receive(incoming);
    });
    const manual = () => query(true);
    refreshAction.current = manual;
    const visible = () => { if (!document.hidden) void query(); };
    document.addEventListener("visibilitychange", visible);
    const initial = window.setTimeout(() => void query(), import.meta.env.VITE_LIVE_STREAM === "false" ? 0 : 1500);
    const timer = window.setInterval(() => void query(), 3000);
    return () => {
      cancelled = true;
      controller?.abort();
      stop();
      window.clearTimeout(initial);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      if (refreshAction.current === manual) refreshAction.current = null;
    };
  }, [active, requestKey, scopeKey]);
  const data = state?.scopeKey === scopeKey ? state.data : null;
  const refresh = useCallback(() => refreshAction.current?.(), []);
  return { data, refresh, transportError };
}
