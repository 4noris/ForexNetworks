import React, { useEffect, useMemo, useState } from "react";
import { CalendarDays, RefreshCw } from "lucide-react";
import { apiRequest } from "../lib/api";

const impacts = ["all", "high", "medium", "low"];
const EMPTY_EVENTS = [];

export default function EconomicCalendar() {
  const [feed, setFeed] = useState(null);
  const [error, setError] = useState("");
  const [currency, setCurrency] = useState("all");
  const [impact, setImpact] = useState("all");
  const [selectedDate, setSelectedDate] = useState(null);
  const [search, setSearch] = useState("");

  async function refresh() {
    try {
      const nextFeed = await apiRequest("/api/calendar", { dedupeMs: 0 });
      setFeed(nextFeed);
      setSelectedDate((current) => current === null ? (nextFeed.server_today || "all") : current);
      setError("");
    } catch (cause) {
      setError(cause.message === "Not found." ? "The RMS backend is still running the previous version. Restart it to enable Calendar." : cause.message || "Calendar unavailable");
    }
  }

  useEffect(() => {
    const initial = window.setTimeout(refresh, 0);
    const timer = window.setInterval(refresh, 60_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, []);

  const events = feed?.events || EMPTY_EVENTS;
  const currencies = useMemo(() => [...new Set(events.map((row) => row.currency))].sort(), [events]);
  const dates = useMemo(() => [...new Set(events.map((row) => row.server_time?.slice(0, 10).replaceAll(".", "-")))].filter(Boolean).sort(), [events]);
  const visible = useMemo(() => events.filter((row) =>
    (selectedDate === null || selectedDate === "all" || row.server_time?.startsWith(selectedDate.replaceAll("-", "."))) &&
    (currency === "all" || row.currency === currency) &&
    (impact === "all" || row.impact === impact) &&
    row.title.toLowerCase().includes(search.trim().toLowerCase())
  ).sort((a, b) => new Date(a.time) - new Date(b.time)), [events, selectedDate, currency, impact, search]);
  const serverTime = (row) => {
    const value = row.server_time || "";
    const date = value.slice(0, 10).replaceAll(".", "-");
    if (row.time_mode === "all_day") return `${date} · All day`;
    if (row.time_mode === "tentative") return `${date} · Tentative`;
    if (row.time_mode === "no_time") return `${date} · Time pending`;
    return value.replaceAll(".", "-");
  };

  return <div className="mx-auto max-w-[1500px] space-y-5 p-2 sm:p-4">
    <header className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-muted">Market events</p>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold"><CalendarDays size={21} /> Economic Calendar</h1>
          <p className="mt-2 text-xs text-muted">Releases load automatically from the weekly calendar API. Times are shown in the configured MT5 server zone; News Trading compares UTC release times with trade opening times.</p>
        </div>
        <button type="button" className="btn-secondary flex items-center gap-2 px-3 py-2 text-xs" onClick={refresh}><RefreshCw size={14} /> Refresh</button>
      </div>
      <p className={`mt-3 text-xs ${feed?.status === "connected" ? "text-emerald-500" : "text-amber-500"}`}>
        {feed?.status === "connected" ? `${events.length} calendar events · ${visible.length} shown · ${feed.source} · MT5 server zone: ${feed.server_timezone}` : error || feed?.message || "Loading calendar…"}
      </p>
    </header>

    {!error && feed?.status === "stale" && <section className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs leading-6">The calendar API is temporarily unavailable. The last fetched schedule remains visible; News Trading waits for a fresh schedule.</section>}

    <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4">
      <div className="flex flex-wrap items-center gap-3">
        <select className="input min-w-[150px] text-xs" value={selectedDate || "all"} onChange={(event) => setSelectedDate(event.target.value)} aria-label="Filter MT5 server date"><option value="all">All dates</option>{dates.map((value) => <option key={value} value={value}>{value === feed?.server_today ? `Today · ${value}` : value}</option>)}</select>
        <input className="input min-w-[180px] flex-1 text-xs" placeholder="Search events" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search calendar events" />
        <select className="input min-w-[110px] text-xs" value={currency} onChange={(event) => setCurrency(event.target.value)} aria-label="Filter currency"><option value="all">All currencies</option>{currencies.map((value) => <option key={value}>{value}</option>)}</select>
        <select className="input min-w-[110px] text-xs" value={impact} onChange={(event) => setImpact(event.target.value)} aria-label="Filter impact">{impacts.map((value) => <option key={value} value={value}>{value === "all" ? "All priorities" : `${value[0].toUpperCase()}${value.slice(1)} priority`}</option>)}</select>
      </div>
      <div className="mt-4 max-h-[70vh] overflow-auto">
        <table className="w-full min-w-[780px] text-left text-xs">
          <thead className="sticky top-0 bg-[rgb(var(--color-card))] text-muted"><tr>{["MT5 server time", "Currency", "Event", "Priority", "Period", "Actual", "Forecast", "Previous"].map((label) => <th key={label} className="px-3 py-2 font-semibold">{label}</th>)}</tr></thead>
          <tbody>{visible.map((row, index) => <tr key={`${row.time}-${row.currency}-${row.title}-${index}`} className="border-t border-[rgb(var(--color-border))] hover:bg-[var(--btn-ghost-bg)]">
            <td className="whitespace-nowrap px-3 py-2">{serverTime(row)}</td><td className="px-3 py-2 font-semibold">{row.currency}</td><td className="min-w-[240px] px-3 py-2">{row.title}</td>
            <td className="px-3 py-2"><span className={`badge ${row.impact === "high" ? "badge-danger" : row.impact === "medium" ? "badge-amber" : ""}`}>{row.impact}</span></td>
            <td className="px-3 py-2">{row.period || "—"}</td><td className="px-3 py-2 tabular-nums">{row.actual || "—"}</td><td className="px-3 py-2 tabular-nums">{row.forecast || "—"}</td><td className="px-3 py-2 tabular-nums">{row.previous || "—"}</td>
          </tr>)}</tbody>
        </table>
        {!visible.length && <p className="p-6 text-center text-xs text-muted">{feed?.status === "error" ? "The calendar API is unavailable. Try Refresh shortly." : "No calendar events available for these filters."}</p>}
      </div>
    </section>
  </div>;
}
