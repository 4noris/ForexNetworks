import React, { useCallback, useEffect, useRef, useState } from "react";
import { Activity, ChevronDown, Loader2 } from "lucide-react";
import { CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import SafeResponsiveContainer from "./SafeResponsiveContainer";
import { apiRequest, subscribeLiveData } from "../lib/api";
import MarkoutDashboard from "./MarkoutDashboard";
import { boundedUtcRange, utcDaysBefore } from "../lib/utcDateRange";

const HORIZONS = ["1", "5", "10", "30", "60", "120", "300", "600", "900"];
const SERIES = { average_client_mid_bps: "Average", median_client_mid_bps: "Median", volume_weighted_client_mid_bps: "Volume weighted" };
const display = (value) => value == null ? "Unavailable" : `${Number(value).toFixed(2)} bps`;

export default function MarkoutPanel({ login, selectedGroups, initialDate, active = true }) {
  const [fromDate, setFromDate] = useState(initialDate);
  const [toDate, setToDate] = useState(initialDate);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [series, setSeries] = useState("average_client_mid_bps");
  const [loadedScope, setLoadedScope] = useState("");
  const request = useRef(null);
  const panel = useRef(null);
  const today = new Date().toISOString().slice(0, 10);
  const rangeDays = (Date.parse(toDate) - Date.parse(fromDate)) / 86400000 + 1;
  const dateError = !fromDate || !toDate || !Number.isFinite(rangeDays)
    ? "Choose both UTC dates."
    : rangeDays < 1 ? "To UTC must be on or after From UTC."
      : rangeDays > 7 ? "Select at most seven UTC days, including both dates."
        : toDate > today ? "Future UTC dates are unavailable." : "";
  const currentScope = JSON.stringify([login, fromDate, toDate, [...selectedGroups].sort()]);
  const changeDate = (boundary, value) => {
    const [from, to] = boundedUtcRange(fromDate, toDate, boundary, value, today);
    setFromDate(from);
    setToDate(to);
    setError("");
  };
  const stale = data && loadedScope !== currentScope;
  const chart = data ? HORIZONS.map((horizon) => ({
    horizon: `${horizon}s`, value: data.summary[horizon]?.[series] ?? null,
    covered: data.summary[horizon]?.covered ?? 0,
    total: data.summary[horizon]?.total ?? 0,
    coverage: data.summary[horizon]?.coverage_pct ?? 0,
  })) : [];

  const groupScope = JSON.stringify(selectedGroups);
  const load = useCallback(async () => {
    if (request.current) return;
    if (dateError) {
      setError(dateError);
      return;
    }
    setError("");
    setLoading(true);
    const controller = new AbortController();
    request.current = controller;
    try {
      const response = await apiRequest("/api/risk/markout", {
        method: "POST",
        signal: controller.signal,
        timeoutMs: 120_000,
        dedupeMs: 0,
        body: JSON.stringify({ client_login: login, selected_groups: JSON.parse(groupScope),
          from_date: fromDate, to_date: toDate }),
      });
      if (controller.signal.aborted) return;
      setData(response);
      setLoadedScope(currentScope);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || "Quote-backed markout is unavailable.");
    } finally {
      if (request.current === controller || request.current === null) { request.current = null; setLoading(false); }
    }
  }, [dateError, currentScope, groupScope, login, fromDate, toDate]);
  useEffect(() => () => { request.current?.abort(); request.current = null; }, [currentScope]);
  const refreshToday = Boolean(data) && !stale && toDate === today;
  useEffect(() => {
    if (!active || !refreshToday) return undefined;
    if (import.meta.env.VITE_LIVE_STREAM === "false") {
      const timer = window.setInterval(() => {
        if (!document.hidden && !panel.current?.closest("[hidden]")) load();
      }, 60_000);
      return () => window.clearInterval(timer);
    }
    return subscribeLiveData("/api/risk/markout", {
      client_login: login, selected_groups: JSON.parse(groupScope), from_date: fromDate, to_date: toDate,
    }, (incoming) => {
      if (panel.current?.closest("[hidden]")) return;
      setData(incoming);
      setLoadedScope(currentScope);
    });
  }, [active, refreshToday, load, login, groupScope, fromDate, toDate, currentScope]);

  return <section ref={panel} className="min-w-0 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 sm:p-5" aria-label="Execution markout">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        <span className="settings-icon"><Activity size={18} /></span>
        <div><h2 className="text-sm font-semibold">Execution markout</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted">Signed price movement after each executed MT5 deal, measured from the quote at execution. Positive favors the client. This is evidence for review, not a fraud verdict.</p></div>
      </div>
      <div className="flex w-full flex-wrap items-end gap-2 sm:w-auto">
        <label className="min-w-[130px] flex-1 text-[11px] text-muted">From UTC<input className="field-input mt-1 !py-2" type="date" max={today} value={fromDate} onChange={(event) => changeDate("from", event.target.value)} /></label>
        <label className="min-w-[130px] flex-1 text-[11px] text-muted">To UTC<input className="field-input mt-1 !py-2" type="date" max={today} value={toDate} onChange={(event) => changeDate("to", event.target.value)} /></label>
        <button type="button" className="btn-primary min-h-10 text-xs" disabled={loading || !selectedGroups.length || Boolean(dateError)} onClick={load}>{loading ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />} Calculate</button>
      </div>
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-muted"><span>Dates adjust automatically to seven UTC days maximum, including both dates. Today (UTC): {today}.</span><button type="button" className="underline" onClick={() => { setFromDate(utcDaysBefore(today, 6)); setToDate(today); setError(""); }}>Last 7 UTC days</button><button type="button" className="underline" onClick={() => { setFromDate(today); setToDate(today); setError(""); }}>Today (UTC)</button></div>
    {(dateError || error) && <p className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-danger" role="alert">{dateError || error}</p>}
    {stale && <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-400">Scope or dates changed. Calculate again to refresh this report.</p>}
    {data && !stale && <div className="mt-4 space-y-4">
      <p className="text-[11px] text-muted">Fetched from MT5: {data.generated_at} · Historical executions and quotes · UTC</p>
      {!data.executions.length ? <div role="status" className="rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-5">
        <h3 className="text-sm font-semibold">No executed trades for these dates</h3>
        <p className="mt-2 text-xs leading-5 text-muted">MT5 returned no executed buy or sell deals for account #{login} from {fromDate} to {toDate}, including both UTC dates. Markout needs executed deals and their historical quotes.</p>
        {data.diagnostics?.latest_execution_date && <div className="mt-3 flex flex-wrap items-center gap-3">
          <p className="text-xs text-muted">Latest execution in the recent seven-day lookup: {data.diagnostics.latest_execution_date} UTC.</p>
          <button type="button" className="btn-secondary text-xs" onClick={() => { setFromDate(data.diagnostics.latest_execution_date); setToDate(data.diagnostics.latest_execution_date); setError(""); }}>Select latest trading day</button>
        </div>}
        {data.diagnostics?.recent_lookup_status === "complete" && !data.diagnostics.latest_execution_date && <p className="mt-2 text-xs text-muted">No executions were found in the seven UTC days ending {data.diagnostics.recent_lookup_to}. Choose an earlier trading date or another client.</p>}
        {data.diagnostics?.recent_lookup_status === "unavailable" && <p className="mt-2 text-xs text-muted">The recent trading-day lookup could not be completed. You can choose a known trading date above.</p>}
        <p className="mt-3 text-[11px] text-muted">Choose trading dates above, then select Calculate. No prices or P&amp;L values are substituted for missing trades.</p>
      </div> : <>
      <MarkoutDashboard data={data} />
      <p className="text-[11px] text-muted">Source: {data.source}. {data.executions.length} executions. Quoted results require an arrival quote and a quote near each horizon. Missing results are excluded from averages.</p>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3"><p className="text-[10px] uppercase tracking-widest text-muted">Executed fills</p><p className="mt-1 text-xl font-semibold tabular-nums">{data.executions.length.toLocaleString()}</p></div>
        <div className="rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3"><p className="text-[10px] uppercase tracking-widest text-muted">Positive 1s markout</p><p className="mt-1 text-xl font-semibold tabular-nums">{data.summary["1"].positive_share_pct == null ? "Unavailable" : `${data.summary["1"].positive_share_pct.toFixed(1)}%`}</p><p className="text-[11px] text-muted">Among {data.summary["1"].covered} quoted fills</p></div>
        <div className="rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3"><p className="text-[10px] uppercase tracking-widest text-muted">Average 1s markout</p><p className="mt-1 text-xl font-semibold tabular-nums">{display(data.summary["1"].average_client_mid_bps)}</p><p className="text-[11px] text-muted">{data.summary["1"].coverage_pct}% quote coverage</p></div>
      </div>
      <div className="rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3 sm:p-4">
        <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="text-sm font-semibold">Client markout by horizon</h3><p className="mt-1 text-[11px] text-muted">Actual MT5 deal and tick history · basis points · UTC</p></div><label className="text-[11px] text-muted">Statistic<select className="field-input mt-1 !w-auto !py-1.5 text-xs" value={series} onChange={(event) => setSeries(event.target.value)}>{Object.entries(SERIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
        {chart.some((point) => point.value !== null) ? <div className="mt-3 h-[240px] w-full sm:h-[300px]" role="img" aria-label={`${SERIES[series]} client markout at nine horizons`}><SafeResponsiveContainer><LineChart data={chart} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid stroke="rgb(var(--color-border))" strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="horizon" tick={{ fill: "rgb(var(--color-text-3))", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={(value) => Number(value).toFixed(1)} tick={{ fill: "rgb(var(--color-text-3))", fontSize: 11 }} axisLine={false} tickLine={false} width={45} label={{ value: "bps", angle: -90, position: "insideLeft", fill: "rgb(var(--color-text-3))", fontSize: 10 }} />
          <ReferenceLine y={0} stroke="rgb(var(--color-text-3))" strokeOpacity={0.7} />
          <Tooltip content={({ active, payload }) => active && payload?.length ? <div className="rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-3 text-xs shadow-lg"><p className="font-semibold">{payload[0].payload.horizon}</p><p className="mt-1 text-[rgb(var(--color-accent))]">{SERIES[series]}: {display(payload[0].payload.value)}</p><p className="mt-1 text-muted">{payload[0].payload.covered}/{payload[0].payload.total} quoted · {payload[0].payload.coverage}% coverage</p></div> : null} />
          <Line type="linear" dataKey="value" connectNulls={false} stroke="rgb(var(--color-accent))" strokeWidth={2.5} dot={{ r: 4, fill: "rgb(var(--color-accent))" }} activeDot={{ r: 6 }} isAnimationActive={false} />
        </LineChart></SafeResponsiveContainer></div> : <p className="py-12 text-center text-xs text-muted">No verified quote pairs for this statistic. The chart remains unavailable.</p>}
        <p className="mt-2 text-[11px] text-muted">Each point uses only fills with valid quotes at that horizon. Coverage can differ between points. No peer cohort or estimated values are shown.</p>
      </div>
      {(data.warnings || []).map((warning) => <p key={warning} className="rounded-lg border border-amber-500/30 p-2 text-xs text-amber-400">{warning}</p>)}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {HORIZONS.map((horizon) => { const item = data.summary[horizon]; return <div key={horizon} className="rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3">
          <p className="text-[10px] uppercase tracking-widest text-muted">{horizon}s client markout</p>
          <p className="mt-1 text-base font-semibold">{display(item.average_client_mid_bps)}</p>
          <p className="mt-1 text-[11px] text-muted">Median {display(item.median_client_mid_bps)} · Weighted {display(item.volume_weighted_client_mid_bps)}</p>
          <p className="mt-1 text-[11px] text-muted">{item.covered}/{item.total} quoted · {item.coverage_pct}% coverage</p>
        </div>; })}
      </div>
      <details className="group rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)]">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-3 text-xs font-semibold sm:p-4">Executed deal details <span className="flex items-center gap-2 text-muted">{data.executions.length} rows <ChevronDown size={15} className="transition-transform group-open:rotate-180" /></span></summary>
        <div className="max-h-72 overflow-auto border-t border-[rgb(var(--color-border))]">
          <table className="w-full min-w-[860px] text-left text-xs"><thead className="sticky top-0 z-10 bg-[rgb(var(--color-card))] text-muted"><tr><th className="px-3 py-2">Deal</th><th className="px-3 py-2">Time UTC</th><th className="px-3 py-2">Symbol</th><th className="px-3 py-2">Side</th>{HORIZONS.map((horizon) => <th key={horizon} className="px-3 py-2 text-right">{horizon}s</th>)}</tr></thead>
            <tbody>{data.executions.map((row) => <tr key={row.deal} className="border-t border-[rgb(var(--color-border))]"><td className="px-3 py-2">#{row.deal}</td><td className="whitespace-nowrap px-3 py-2">{row.time_utc.replace("T", " ").replace(/\+00:00$/, " UTC")}</td><td className="px-3 py-2">{row.symbol}</td><td className="px-3 py-2">{row.side}</td>{HORIZONS.map((horizon) => <td key={horizon} className="px-3 py-2 text-right" title={row.horizons[horizon].status}>{display(row.horizons[horizon].client_mid_bps)}</td>)}</tr>)}</tbody></table>
          {!data.executions.length && <p className="p-4 text-xs text-muted">No executed buy or sell deals were returned for this client and date range.</p>}
        </div>
      </details>
      </>}
    </div>}
  </section>;
}
