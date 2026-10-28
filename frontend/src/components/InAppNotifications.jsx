import React, { useCallback, useEffect, useRef, useState } from "react";
import { BellRing } from "lucide-react";
import toast from "react-hot-toast";
import { apiRequest, getStoredUser, operationsWebSocketUrl } from "../lib/api";

const readKey = () => `rms-alerts-read:${getStoredUser()?.id || "admin"}`;
const readIds = () => {
  try { return new Set(JSON.parse(localStorage.getItem(readKey()) || "[]")); }
  catch { return new Set(); }
};

export default function InAppNotifications() {
  const [events, setEvents] = useState([]);
  const [read, setRead] = useState(readIds);
  const [open, setOpen] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const initial = useRef(false);
  const known = useRef(new Set());
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const payload = await apiRequest("/api/notifications/in-app?limit=50", { dedupeMs: 0 });
      const incoming = payload.events || [];
      if (initial.current) {
        const fresh = incoming.filter((event) => !known.current.has(event.event_id));
        const balanceAlerts = fresh.filter((event) => event.detector === "balance_below_credit");
        balanceAlerts.slice(0, 3).forEach((event) => {
          toast.error(`Balance below credit · Account #${event.login}\n${event.evidence}`, {
            id: `balance-credit-${event.event_id}`,
            duration: 10_000,
          });
        });
        if (balanceAlerts.length > 3) toast.error(`${balanceAlerts.length - 3} more accounts have balances below credit. Open notifications for details.`, { duration: 10_000 });
        const otherCount = fresh.length - balanceAlerts.length;
        if (otherCount) toast(`New RMS alert${otherCount === 1 ? "" : "s"}: ${otherCount}`, { icon: "🔔" });
      }
      incoming.forEach((event) => known.current.add(event.event_id));
      initial.current = true;
      setEvents(incoming);
      setLastUpdated(Date.now());
      setRefreshFailed(false);
    } catch {
      // Retain the last verified events, but label their freshness accurately.
      setRefreshFailed(true);
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(refresh, 0);
    let socket;
    let retry;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(operationsWebSocketUrl());
      socket.onmessage = (message) => {
        try { if (JSON.parse(message.data).type === "risk_events_updated") refresh(); }
        catch { /* Ignore unrelated or malformed updates. */ }
      };
      socket.onclose = () => { if (!stopped) retry = window.setTimeout(connect, 5000); };
      socket.onerror = () => socket.close();
    };
    connect();
    const fallback = window.setInterval(refresh, 60_000);
    return () => {
      stopped = true;
      window.clearInterval(fallback);
      window.clearTimeout(initialTimer);
      window.clearTimeout(retry);
      socket?.close();
    };
  }, [refresh]);

  const unread = events.filter((event) => !read.has(event.event_id)).length;
  const markAllRead = () => {
    const next = new Set([...read, ...events.map((event) => event.event_id)]);
    localStorage.setItem(readKey(), JSON.stringify([...next].slice(-500)));
    setRead(next);
  };

  return <div className="fixed right-3 top-2.5 z-50 md:right-4">
    {open && <button type="button" className="fixed inset-0 z-0 cursor-default bg-white/10 backdrop-blur-[2px] dark:bg-black/25" onClick={() => setOpen(false)} aria-label="Close notifications" />}
    <button type="button" className="relative z-10 flex h-9 w-9 items-center justify-center rounded-full border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] shadow-sm" onClick={() => setOpen((value) => !value)} aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} aria-expanded={open}>
      <BellRing size={16} />
      {unread > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold text-white">{unread > 99 ? "99+" : unread}</span>}
    </button>
    {open && <section className="absolute right-0 top-11 z-10 w-[min(92vw,390px)] rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-3 shadow-xl" aria-label="Recent RMS notifications">
      <div className="flex items-center justify-between gap-2 border-b border-[rgb(var(--color-border))] pb-2"><h2 className="text-sm font-semibold">Notifications</h2><button type="button" className="text-xs text-muted hover:text-primary" onClick={markAllRead}>Mark all read</button></div>
      <p className={`py-2 text-[11px] ${refreshFailed ? "text-amber-400" : "text-muted"}`} role={refreshFailed ? "status" : undefined}>
        {refreshFailed ? "Updates unavailable; showing the last verified alerts." : lastUpdated ? `Updated ${new Date(lastUpdated).toLocaleTimeString()}` : "Loading verified alerts…"}
      </p>
      <div className="scrollbar-hidden max-h-[420px] overflow-y-auto">
        {events.map((event) => <div key={event.event_id} className={`border-b border-[rgb(var(--color-border))] py-2 last:border-0 ${read.has(event.event_id) ? "opacity-70" : ""}`}>
          <div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold">{event.detector === "deposit_alert" ? "Deposit Alert" : event.detector.replaceAll("_", " ")} · #{event.login}</p><span className="text-[10px] uppercase text-muted">{event.severity}</span></div>
          <p className="mt-1 text-[11px] leading-4 text-muted">{event.evidence || "Evidence available in Operations"}</p>
          <p className="mt-1 text-[10px] text-muted">{event.group || "Unknown group"} · {event.data_quality} · {event.first_seen_at}</p>
        </div>)}
        {!events.length && <p className="py-5 text-center text-xs text-muted">No Anti-Fraud alerts yet.</p>}
      </div>
    </section>}
  </div>;
}
