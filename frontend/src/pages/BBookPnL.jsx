import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Database, RefreshCw, WalletCards } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import SafeResponsiveContainer from "../components/SafeResponsiveContainer";
import AnimatedCard from "../components/AnimatedCard";
import { apiRequest } from "../lib/api";

const books = { a_book: "A Book", b_book: "B Book", cent: "Cent", hybrid: "Hybrid" };
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const emptyReport = { daily: [], intraday: [], totals: {} };
const tooltipStyle = { background: "rgb(var(--color-card))", border: "1px solid rgb(var(--color-border))", borderRadius: 10 };
function utcDay(offset = 0) {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}
function Money({ value, neutral = false }) {
  if (value == null || !Number.isFinite(Number(value))) return <span className="text-primary">Unavailable</span>;
  const amount = Number(value);
  return <span className={neutral ? "text-primary" : amount > 0 ? "text-success" : amount < 0 ? "text-danger" : "text-primary"}>{money.format(amount)}</span>;
}
function Metric({ label, value, neutral = false, note = "" }) {
  return <div className="min-w-0 text-center"><div className="text-[10px] font-semibold uppercase tracking-[0.15em] text-muted">{label}</div><div className="mt-2 text-lg font-semibold tabular-nums"><Money value={value} neutral={neutral} /></div>{note && <div className="mt-1 text-[11px] text-muted">{note}</div>}</div>;
}
export default function BBookPnL() {
  const [book, setBook] = useState("a_book");
  const [dateFrom, setDateFrom] = useState(() => utcDay(-6));
  const [dateTo, setDateTo] = useState(() => utcDay());
  const [report, setReport] = useState(emptyReport);
  const [loading, setLoading] = useState(true);
  const activeRequest = useRef(null);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const load = useCallback(async (background = false) => {
    const current = ++requestId.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    if (!background) { setLoading(true); setError(""); setReport(emptyReport); }
    try {
      const data = await apiRequest(`/api/book-pnl?book=${book}&date_from=${dateFrom}&date_to=${dateTo}`, { cache: "no-store", dedupeMs: 0, signal: controller.signal, timeoutMs: 45_000 });
      if (current === requestId.current) { setReport(data); setError(""); }
    } catch (cause) {
      if (!controller.signal.aborted && current === requestId.current) { setError(cause.message || "Book history is unavailable."); if (!background) setReport(emptyReport); }
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [book, dateFrom, dateTo]);
  useEffect(() => { load(); return () => { requestId.current += 1; activeRequest.current?.abort(); }; }, [load]);
  useEffect(() => {
    const timer = setTimeout(() => load(true), report.backfill?.refreshing ? 5000 : 10000);
    return () => clearTimeout(timer);
  }, [load, report]);
  const totalLabel = book === "a_book" ? "Posted client charge offset" : `${book === "b_book" ? "Broker" : "Client"} ${books[book]} P&L`;
  const totals = report.totals || {};
  const days = useMemo(() => [...(report.daily || [])].reverse(), [report.daily]);
  const intraday = useMemo(() => {
    const opening = new Map((report.daily || []).map((day) => [day.day, Number(day.opening_floating || 0)]));
    return (report.intraday || []).map((point) => {
      const day = String(point.captured_at).slice(0, 10);
      const client = Number(point.closed_swaps_comm || 0) + Number(point.floating || 0) - (opening.get(day) || 0);
      return { time: new Date(point.captured_at).toLocaleTimeString(), value: book === "a_book" ? -Number(point.swaps || 0) - Number(point.commissions || 0) : book === "b_book" ? -client : client };
    });
  }, [report.daily, report.intraday, book]);
  const coverage = report.coverage;
  const dealOnlyDays = (report.daily || []).filter((row) => row.source === "verified_deals_only");
  const snapshotDays = (report.daily || []).filter((row) => row.floating_delta != null);
  const snapshotCount = snapshotDays.length;
  const availableFloating = snapshotCount ? snapshotDays.reduce((sum, row) => sum + Number(row.floating_delta), 0) : null;
  const availableShifts = snapshotCount ? snapshotDays.reduce((sum, row) => sum + Number(row.shift_corrections || 0), 0) : null;
  const postedChargeOffset = report.posted_client_charge_offset;
  const totalValue = book === "a_book" ? postedChargeOffset : (totals.total_book_pnl ?? totals.total_bbook_pnl);
  return <div className="space-y-4 p-4 md:p-6">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="flex items-center gap-2 text-xl font-bold text-primary"><WalletCards size={19} className="text-danger" />Book Performance</h1><p className="mt-1 text-xs text-muted">Broker perspective from stored MT5 snapshots. LP costs and markup are unavailable.</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-1">{Object.entries(books).map(([key, label]) => <button key={key} type="button" onClick={() => setBook(key)} className={`rounded-lg px-3 py-2 text-xs font-medium ${book === key ? "bg-white text-black" : "text-muted hover:text-primary"}`}>{label}</button>)}</div>
        <input aria-label="From date" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] px-3 py-2 text-xs text-primary" />
        <span className="text-xs text-muted">to</span>
        <input aria-label="To date" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] px-3 py-2 text-xs text-primary" />
        <button type="button" onClick={() => load(true)} disabled={loading} className="flex items-center gap-2 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] px-4 py-2 text-xs text-primary disabled:opacity-60"><RefreshCw size={13} className={loading ? "animate-spin" : ""} />Refresh</button>
      </div>
    </div>
    <div className="rounded-xl border border-[rgb(var(--color-border))] px-3 py-2 text-[11px] text-muted"><Database size={12} className="mr-2 inline text-success" />Live balances with stored MT5 history · refreshes every 10s{report.last_updated ? ` · last snapshot ${new Date(report.last_updated).toLocaleString()}` : ""}{report.next_sync_at ? ` · next sync ${new Date(report.next_sync_at).toLocaleString()}` : ""}</div>
    {coverage && <div className="rounded-xl border border-[rgb(var(--color-border))] px-3 py-2 text-xs text-primary">{book === "a_book" ? "Deal or snapshot coverage" : "History coverage"}: {coverage.available_days} of {coverage.expected_days} days. {report.backfill?.refreshing ? "Checking MT5 history now." : ""}{!coverage.complete && <div className="mt-1">Available-day subtotal: <Money value={report.available_days_subtotal?.total_book_pnl} />. Full-range P&L remains unavailable.</div>}</div>}
    {book === "a_book" && <div className="rounded-xl border border-[rgb(var(--color-border))] px-3 py-2 text-xs leading-5 text-muted">
      {dealOnlyDays.length > 0 ? `${dealOnlyDays.length} day${dealOnlyDays.length === 1 ? "" : "s"} (${dealOnlyDays.map((row) => row.day).join(", ")}) have verified deals but no complete daily equity snapshot. Floating and shift figures below cover only ${snapshotCount} of ${report.daily?.length || 0} days.` : snapshotCount ? "Daily equity snapshots are available for the reported days." : "Historical daily equity snapshots are not yet available for this range."} Posted client charges come from MT5 deals. Actual A-Book broker net P&L requires LP execution and cost records.
    </div>}
    {report.backfill?.message && !report.daily?.length && <p role="status" className="text-xs text-muted">{report.backfill.message}</p>}
    {error && <div role="alert" className="rounded-xl border border-red-500/40 px-3 py-2 text-sm text-danger">{error}</div>}
    <AnimatedCard className="grid grid-cols-2 gap-x-5 gap-y-6 p-5 md:grid-cols-4 md:p-7">
      <Metric label="Net in/out" value={totals.net_in_out} /><Metric label={report.live ? "Live balance" : "Balance"} value={report.live?.balance ?? totals.balance} neutral /><Metric label={report.live ? "Live equity" : "Equity"} value={report.live?.equity ?? totals.equity} neutral /><Metric label={report.live ? "Live floating" : "Closing floating"} value={report.live?.floating ?? totals.closing_floating} />
      <Metric label="Client closed + posted charges" value={totals.closed_swaps_comm} /><Metric label={book === "a_book" && dealOnlyDays.length ? "Observed floating delta" : "Client floating delta"} value={book === "a_book" ? availableFloating : totals.floating_delta} note={book === "a_book" && dealOnlyDays.length ? `${snapshotCount} of ${report.daily.length} days` : ""} /><Metric label={book === "a_book" && dealOnlyDays.length ? "Recorded shift corrections" : "Shift corrections"} value={book === "a_book" ? availableShifts : totals.shift_corrections} note={book === "a_book" && dealOnlyDays.length ? `${snapshotCount} of ${report.daily.length} days` : ""} /><Metric label={totalLabel} value={totalValue} note={book === "a_book" ? "Client charges only · excludes LP costs" : ""} />
    </AnimatedCard>
    <AnimatedCard className="overflow-hidden"><div className="border-b border-[rgb(var(--color-border))] px-4 py-3 text-sm font-semibold text-primary">Daily {totalLabel}</div>
      <div className="overflow-x-auto"><table className="w-full min-w-[1450px] text-right text-xs"><thead className="text-muted"><tr>{["Day", "Deposits", "Withdrawals", "Net In/Out", "Balance", "Equity", "Closed P&L", "Swaps", "Commissions", "Opening Floating", "Closing Floating", "Floating Delta", "Shift Corrections", totalLabel].map((label) => <th key={label} className="whitespace-nowrap px-3 py-3 first:text-left">{label}</th>)}</tr></thead>
      <tbody>{(report.daily || []).map((row) => <tr key={row.day} className="border-t border-[rgb(var(--color-border))]"><td className="whitespace-nowrap px-3 py-3 text-left font-semibold text-primary">{row.day}{row.source === "verified_deals_only" && <span className="ml-1 text-[10px] font-normal text-muted">deals only</span>}</td>{["deposits", "withdrawals", "net_in_out", "balance", "equity", "closed_profit", "swaps", "commissions", "opening_floating", "closing_floating", "floating_delta", "shift_corrections"].map((key) => <td key={key} className="whitespace-nowrap px-3 py-3">{row[key] == null && row.source === "verified_deals_only" ? <span className="text-muted" title="No complete MT5 daily equity snapshot">No snapshot</span> : <Money value={row[key]} />}</td>)}<td className="whitespace-nowrap px-3 py-3"><Money value={book === "a_book" ? row.posted_client_charge_offset : (row.total_book_pnl ?? row.total_bbook_pnl)} /></td></tr>)}</tbody></table>{!report.daily?.length && <div className="p-8 text-center text-sm text-muted">{loading ? "Loading book history…" : "No book history for this range."}</div>}</div>
    </AnimatedCard>
    <div className="grid gap-4 xl:grid-cols-2">
      <AnimatedCard className="min-w-0 p-4"><h2 className="mb-3 text-sm font-semibold text-primary">Client P&L components</h2><div className="h-64"><SafeResponsiveContainer><BarChart data={days}><CartesianGrid stroke="rgb(var(--color-border))" vertical={false} /><XAxis dataKey="day" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} /><Tooltip contentStyle={tooltipStyle} /><Bar dataKey="closed_swaps_comm" name="Closed + charges" fill="#4c85d8" /><Bar dataKey="floating_delta" name="Floating delta" fill="#ad74cb" /><Bar dataKey="shift_corrections" name="Shift corrections" fill="#e69b32" /></BarChart></SafeResponsiveContainer></div></AnimatedCard>
      <AnimatedCard className="min-w-0 p-4"><h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-primary"><Activity size={15} />Intraday {totalLabel} movement</h2><div className="h-64"><SafeResponsiveContainer><AreaChart data={intraday}><CartesianGrid stroke="rgb(var(--color-border))" vertical={false} /><XAxis dataKey="time" tick={{ fontSize: 10 }} minTickGap={24} /><YAxis tick={{ fontSize: 10 }} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="value" stroke="#e3942b" fill="#e3942b" fillOpacity={0.2} isAnimationActive={false} /></AreaChart></SafeResponsiveContainer></div></AnimatedCard>
    </div>
  </div>;
}
