import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import AnimatedCard from "../components/AnimatedCard";
import SafeResponsiveContainer from "../components/SafeResponsiveContainer";
import ExposureLimitsDialog from "../components/ExposureLimitsDialog";
import { getStoredUser, subscribeLiveData } from "../lib/api";
import { useExposureData } from "../lib/useExposureData";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const lots = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });
const price = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });
const text = (value, formatter = number) => value == null || !Number.isFinite(Number(value)) ? "—" : formatter.format(value);
const tone = (value) => value > 0 ? "text-success" : value < 0 ? "text-danger" : "";
const timeLabel = (value) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fields = [["name", "Group / client"], ["buy_lots", "Buy lots"], ["sell_lots", "Sell lots"], ["net_lots", "Net lots"],
  ["gross_lots", "Gross lots"], ["avg_buy", "Average buy"], ["avg_sell", "Average sell"], ["floating_pnl", "Floating"], ["clients", "Clients"], ["positions", "Positions"], ["utilization", "Utilization"], ["margin_level", "Margin level"]];
const initialFilters = { symbol: "", group: "", query: "", direction: "BOTH", risk_status: "ALL", range: "4H", money_currency: "" };

function Panel({ title, children, actions, className = "" }) {
  return <AnimatedCard className={`min-w-0 overflow-hidden ${className}`}><div className="flex min-h-11 items-center justify-between gap-2 border-b border-[rgb(var(--color-border))] px-3 py-2"><h2 className="text-xs font-semibold tracking-wide">{title}</h2>{actions}</div>{children}</AnimatedCard>;
}

function Kpi({ label, value, formatter = lots }) {
  return <AnimatedCard className="min-h-[100px] min-w-0 p-4"><p className="text-xs text-muted">{label}</p><p className={`mt-5 truncate text-center text-2xl font-semibold tabular-nums ${label.startsWith("Floating") ? tone(value) : ""}`}>{text(value, formatter)}</p></AnimatedCard>;
}

function Utilization({ row }) {
  if (row.utilization == null) return <span className="text-muted" title="No lot limit configured">Not set</span>;
  return <span className={`inline-flex items-center gap-1.5 ${row.status === "critical" ? "text-danger" : row.status === "warning" ? "text-amber-400" : "text-success"}`} title="Full selected-book utilization before display filters">
    <span className="h-1 w-9 overflow-hidden rounded bg-[rgb(var(--color-border))]"><span className="block h-full bg-current" style={{ width: `${Math.min(100, Math.max(0, row.utilization))}%` }} /></span>{text(row.utilization)}%
  </span>;
}

function AveragePriceCell({ row, side }) {
  const field = side === "buy" ? "avg_buy" : "avg_sell";
  const values = (row.price_breakdown || []).filter((entry) => entry[`${side}_lots`] > 0);
  return <td data-testid={`average-${side}-price`} title="Opening price weighted by lots, separately for each symbol">
    {values.length > 1 ? <details className="inline-block text-left"><summary className="cursor-pointer text-secondary">By symbol ({values.length})</summary>
      <div className="mt-1 max-h-40 overflow-y-auto">{values.map((entry) => <div className="flex justify-between gap-3 py-1" key={entry.symbol}><span className="text-muted">{entry.symbol || "Unknown symbol"}</span><span>{text(entry[field], price)}</span></div>)}</div>
    </details> : <>{text(values[0]?.[field] ?? row[field], price)}{values[0]?.symbol && <span className="ml-1 text-[10px] text-muted">{values[0].symbol}</span>}</>}
  </td>;
}

function MetricCells({ row, client = false }) {
  return <><td>{text(row.buy_lots, lots)}</td><td>{text(row.sell_lots, lots)}</td><td className={tone(row.net_lots)}>{text(row.net_lots, lots)}</td><td>{text(row.gross_lots, lots)}</td>
    <AveragePriceCell row={row} side="buy" /><AveragePriceCell row={row} side="sell" />
    <td className={tone(row.floating_pnl)}>{text(row.floating_pnl)}</td><td>{client ? "—" : row.clients}</td><td>{row.positions}</td><td><Utilization row={row} /></td><td>{client && row.margin_level != null ? `${text(row.margin_level)}%` : "—"}</td></>;
}

function Bars({ rows, dataKey = "gross_lots", signed = false, group = false }) {
  if (!rows.length) return <div className="flex h-56 items-center justify-center text-xs text-muted">No matching observations</div>;
  return <div className="h-56 p-2"><SafeResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={180}>
    <BarChart data={rows} layout={group ? "horizontal" : "vertical"} margin={{ left: 5, right: 15 }}>
      <CartesianGrid stroke="rgb(var(--color-border))" strokeOpacity={0.4} />
      {group ? <><XAxis dataKey="label" tick={{ fontSize: 9 }} /><YAxis tick={{ fontSize: 9 }} /></> : <><XAxis type="number" tick={{ fontSize: 9 }} /><YAxis type="category" dataKey="label" width={90} tick={{ fontSize: 9 }} /></>}
      <Tooltip formatter={(value) => text(value, lots)} />
      {group ? <><Bar name="Buy lots" dataKey="buy_lots" fill="#2fc58a" /><Bar name="Sell lots" dataKey="sell_lots" fill="#f0605d" /><Bar name="Net lots" dataKey="net_lots" fill="#8a9ba7" /></>
        : <Bar dataKey={dataKey} name={dataKey === "net_abs" ? "Absolute net lots" : "Gross lots"}>{rows.map((row) => <Cell key={row.label} fill={signed ? row.net_lots < 0 ? "#f0605d" : "#2fc58a" : "#6b8e9c"} />)}</Bar>}
    </BarChart>
  </SafeResponsiveContainer></div>;
}

function ObservedLines({ rows, pnl = false, compare = false }) {
  if (rows.length < 2) return <div className="flex h-56 items-center justify-center px-3 text-center text-xs text-muted">Collecting real observations. Earlier history is not reconstructed.</div>;
  return <div className="h-56 p-2"><SafeResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={180}><LineChart data={rows} margin={{ left: 5, right: 12 }}>
    <CartesianGrid stroke="rgb(var(--color-border))" strokeOpacity={0.4} /><XAxis dataKey="source_ms" tickFormatter={timeLabel} minTickGap={45} tick={{ fontSize: 9 }} /><YAxis tick={{ fontSize: 9 }} />
    <Tooltip labelFormatter={timeLabel} formatter={(value) => text(value, pnl ? number : lots)} />
    <Line name={pnl ? "Filtered floating" : "Gross lots"} type="linear" dataKey={pnl ? "floating_pnl" : "gross_lots"} stroke="#2fc58a" dot={false} isAnimationActive={false} connectNulls={false} />
    {(!pnl || compare) && <Line name={pnl ? "All selected groups" : "Net lots"} type="linear" dataKey={pnl ? "comparison_floating" : "net_lots"} stroke="#d4a05b" dot={false} isAnimationActive={false} connectNulls={false} />}
  </LineChart></SafeResponsiveContainer></div>;
}

function PositionDetails({ detail, symbolMap, onPage, client }) {
  if (!detail) return <p className="p-4 text-xs text-muted">Loading verified positions…</p>;
  return <div className="overflow-x-auto p-2"><table className="exposure-table w-full min-w-[820px] text-[11px]"><thead><tr>{["Ticket", "Symbol", "Side", "Lots", "Open", "Current", "Profit", "Swap", "Commission", "Currency"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>
    {detail.positions.map((row) => <tr key={row.ticket}><td>{row.ticket}</td><td>{row.symbol}{symbolMap.get(row.symbol)?.quote_stale && <span className="ml-1 text-amber-400" title="Latest quote is stale or unavailable">· delayed quote</span>}</td><td className={row.action === "BUY" ? "text-success" : "text-danger"}>{row.action}</td><td>{text(row.volume, lots)}</td><td>{text(row.price_open, price)}</td><td>{text(row.price_current, price)}</td><td className={tone(row.profit)}>{text(row.profit)}</td><td>{text(row.swap)}</td><td>{text(row.commission)}</td><td>{row.currency}</td></tr>)}
  </tbody></table>{detail.total > detail.positions.length && <div className="my-2 flex items-center gap-2 text-xs"><button type="button" className="btn-ghost" aria-label={`Previous positions for ${client}`} disabled={!detail.offset} onClick={() => onPage(Math.max(0, detail.offset - detail.limit))}>Previous</button><span>{detail.offset + 1}–{detail.offset + detail.positions.length} of {detail.total}</span><button type="button" className="btn-ghost" aria-label={`Next positions for ${client}`} disabled={detail.offset + detail.positions.length >= detail.total} onClick={() => onPage(detail.offset + detail.limit)}>Next</button></div>}</div>;
}

export default function ExposureDashboard({ selectedGroups, active }) {
  const [filters, setFilters] = useState(initialFilters);
  const [query, setQuery] = useState("");
  const [expandedGroups, setExpandedGroups] = useState([]);
  const [expandedClients, setExpandedClients] = useState([]);
  const [sort, setSort] = useState({ sort_by: "gross_lots", sort_desc: true });
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [clientMetric, setClientMetric] = useState("net");
  const [detailOffsets, setDetailOffsets] = useState({});
  const [clientOffsets, setClientOffsets] = useState({});
  const [clock, setClock] = useState(() => Date.now());
  const [flashLogin, setFlashLogin] = useState("");
  const [performance, setPerformance] = useState(null);
  const [realizedOpen, setRealizedOpen] = useState(false);
  const flashTimer = useRef(null);
  const pendingLocate = useRef(null);
  const isAdmin = Boolean(getStoredUser()?.is_admin);
  useEffect(() => { const timer = window.setTimeout(() => setQuery(filters.query), 180); return () => window.clearTimeout(timer); }, [filters.query]);
  useEffect(() => { if (!active) return undefined; const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => window.clearInterval(timer); }, [active]);
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);
  const groupFilter = selectedGroups.includes(filters.group) ? filters.group : "";
  const params = useMemo(() => ({ ...filters, group: groupFilter, query, ...sort, selected_groups: selectedGroups,
    expanded_groups: expandedGroups, expanded_clients: expandedClients, client_metric: clientMetric,
    detail_offsets: detailOffsets, client_offsets: clientOffsets }),
  [filters, groupFilter, query, sort, selectedGroups, expandedGroups, expandedClients, clientMetric, detailOffsets, clientOffsets]);
  const { data, refresh, transportError } = useExposureData(params, active);
  const summary = data?.summary || {};
  const groups = data?.groups || [];
  const symbols = useMemo(() => data?.symbols || [], [data?.symbols]);
  const symbolMap = useMemo(() => new Map(symbols.map((row) => [row.symbol, row])), [symbols]);
  const age = data?.source_updated_at ? Math.max(0, Math.floor(clock / 1000 - data.source_updated_at)) : null;
  const delayed = data?.status === "delayed" || age > 10;
  const currency = summary.currency || "";
  const setFilter = (key, value) => setFilters((current) => ({ ...current, [key]: value }));
  const toggle = (setter, value) => setter((current) => current.includes(value) ? current.filter((entry) => entry !== value) : [...current, value]);
  const changeSort = (key) => setSort((current) => ({ sort_by: key, sort_desc: current.sort_by === key ? !current.sort_desc : true }));
  const revealClient = useCallback((found) => {
    setExpandedGroups((current) => current.includes(found.group) ? current : [...current, found.group]);
    setExpandedClients((current) => current.includes(found.login) ? current : [...current.slice(-199), found.login]);
    setFlashLogin(found.login); window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => {
      document.getElementById(`exposure-client-${found.login}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      flashTimer.current = window.setTimeout(() => setFlashLogin(""), 1800);
    }, 400);
  }, []);
  const locate = (event) => {
    if (event.key !== "Enter") return;
    pendingLocate.current = filters.query.trim();
    setQuery(filters.query.trim());
    if (query.trim() === filters.query.trim() && data?.located_account?.visible) {
      pendingLocate.current = null;
      revealClient(data.located_account);
    }
  };
  useEffect(() => {
    if (pendingLocate.current !== query || !data?.located_account?.visible) return undefined;
    const timer = window.setTimeout(() => { pendingLocate.current = null; revealClient(data.located_account); }, 0);
    return () => window.clearTimeout(timer);
  }, [data, query, revealClient]);
  const performanceKey = JSON.stringify(selectedGroups);
  useEffect(() => {
    if (!active || !realizedOpen || !JSON.parse(performanceKey).length) return undefined;
    let cancelled = false;
    const stop = subscribeLiveData("/api/risk/performance", { selected_groups: JSON.parse(performanceKey), lookback_days: 31 }, (value) => {
      if (!cancelled && value?.status !== "warming") setPerformance({ key: performanceKey, value });
    });
    return () => { cancelled = true; stop(); };
  }, [active, realizedOpen, performanceKey]);
  const daily = performance?.key === performanceKey ? performance.value?.periods?.daily : null;
  const realized = [...(daily?.winners || []), ...(daily?.losers || [])].filter((row) =>
    (!groupFilter || row.group === groupFilter) && (!query || String(row.login).includes(query) || String(row.name || "").toLowerCase().includes(query.toLowerCase())));
  const observed = data?.history || [];
  return <div className="space-y-3" data-testid="exposure-workspace">
    <style>{`.exposure-table th,.exposure-table td{padding:9px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid rgb(var(--color-border)/.55)}.exposure-table th:first-child,.exposure-table td:first-child{text-align:left}.exposure-table th{position:sticky;top:0;z-index:1;background:rgb(var(--color-surface));font-weight:500;color:rgb(var(--color-text-3))}.exposure-table tbody tr:hover{background:rgb(var(--color-border)/.15)}`}</style>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span role="status" className={delayed ? "text-amber-400" : "text-muted"}>{age == null ? "Connecting to live exposure…" : `${delayed ? "Updates delayed · last verified" : "Live"} ${age}s ago`}</span>
      <span className="ml-auto text-muted">WebSocket · {data?.complete === false ? "partial coverage" : "full selected scope"}</span>
      <button type="button" className="btn-secondary text-xs" onClick={refresh}>Refresh exposure</button>
      <button type="button" className="btn-secondary text-xs" disabled={!isAdmin} title={isAdmin ? "Configure lot limits" : "Limits are managed by an administrator"} onClick={() => setLimitsOpen(true)}>Limits</button>
    </div>
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[rgb(var(--color-border))] p-3">
      <select aria-label="Exposure symbol" className="field-input !w-auto max-w-[180px] text-xs" value={filters.symbol} onChange={(event) => setFilter("symbol", event.target.value)}><option value="">All symbols</option>{(data?.available_symbols || []).map((symbol) => <option key={symbol}>{symbol}</option>)}</select>
      <select aria-label="Exposure group" className="field-input !w-auto max-w-[220px] text-xs" value={groupFilter} onChange={(event) => setFilter("group", event.target.value)}><option value="">All selected groups</option>{selectedGroups.map((group) => <option key={group}>{group}</option>)}</select>
      <input aria-label="Exposure login or name" placeholder="Login or name · Enter to locate" className="field-input !w-56 text-xs" value={filters.query} onChange={(event) => setFilter("query", event.target.value)} onKeyDown={locate} />
      <select aria-label="Exposure direction" className="field-input !w-auto text-xs" value={filters.direction} onChange={(event) => setFilter("direction", event.target.value)}><option value="BOTH">Both sides</option><option>BUY</option><option>SELL</option></select>
      <select aria-label="Exposure status" className="field-input !w-auto text-xs" value={filters.risk_status} onChange={(event) => setFilter("risk_status", event.target.value)}>{["ALL", "normal", "warning", "critical", "unconfigured"].map((status) => <option key={status} value={status}>{status === "ALL" ? "All status" : status === "unconfigured" ? "No limit set" : status}</option>)}</select>
      <select aria-label="Exposure history range" className="field-input !w-auto text-xs" value={filters.range} onChange={(event) => setFilter("range", event.target.value)}>{[["15M", "15 minutes"], ["1H", "1 hour"], ["4H", "4 hours"], ["24H", "24 hours"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      {!!summary.currencies?.length && <label className="inline-flex items-center gap-1 text-xs text-muted">Money<select aria-label="Exposure money currency" className="field-input !w-auto text-xs" value={currency} onChange={(event) => setFilter("money_currency", event.target.value)}>{summary.currencies.map((code) => <option key={code}>{code}</option>)}</select></label>}
      <button type="button" className="btn-ghost text-xs" onClick={() => { setFilters(initialFilters); setExpandedClients([]); }}>Reset exposure filters</button>
    </div>
    <p className="text-[11px] text-muted">Lots and positions cover every matching open position. Floating totals are {currency || "awaiting a verified account currency"}{currency ? " only" : ""}.{summary.unknown_currency_positions ? ` ${summary.unknown_currency_positions} positions await currency metadata.` : ""} Utilization uses the complete selected book before display filters; warnings and critical levels come from saved limits.</p>
    {data?.located_account && <p className="text-xs text-muted">Client {data.located_account.login} · {data.located_account.group}{data.located_account.visible ? " · press Enter to expand" : " · no positions visible with these filters"}</p>}
    {(transportError || data?.warnings?.length > 0 || data?.stale_symbols?.length > 0) && <div role="note" className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-amber-400">{transportError}{(data?.warnings || []).map((warning) => <p key={warning}>{warning}</p>)}{!!data?.stale_symbols?.length && <p>Delayed or missing quotes: {data.stale_symbols.slice(0, 15).join(", ")}{data.stale_symbols.length > 15 ? ` +${data.stale_symbols.length - 15}` : ""}. P&L remains the broker-reported observation.</p>}</div>}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Kpi label="Buy lots" value={summary.buy_lots} /><Kpi label="Sell lots" value={summary.sell_lots} /><Kpi label="Net lots" value={summary.net_lots} /><Kpi label="Gross lots" value={summary.gross_lots} /><Kpi label={`Floating${currency ? ` (${currency})` : ""}`} value={summary.floating_pnl} formatter={number} /><Kpi label="Positions" value={summary.positions} formatter={number} />
    </div>
    <Panel title="Exposure by symbol"><div className="max-h-[380px] overflow-auto"><table className="exposure-table w-full min-w-[980px] text-xs"><thead><tr>{["Symbol", "Buy lots", "Sell lots", "Net lots", "Average buy", "Average sell", "Bid / ask", `Floating ${currency}`, "Positions", "Utilization"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>
      {symbols.map((row) => <tr key={row.symbol}><td className="font-medium">{row.symbol}</td><td>{text(row.buy_lots, lots)}</td><td>{text(row.sell_lots, lots)}</td><td className={tone(row.net_lots)}>{text(row.net_lots, lots)}</td><td>{text(row.avg_buy, price)}</td><td>{text(row.avg_sell, price)}</td><td className={row.quote_stale ? "text-amber-400" : ""} title={row.quote_time || "Quote unavailable"}>{text(row.bid, price)} / {text(row.ask, price)}{row.quote_stale ? " · delayed" : ""}</td><td className={tone(row.floating_pnl)}>{text(row.floating_pnl)}</td><td>{row.positions}</td><td><Utilization row={row} /></td></tr>)}
      {!symbols.length && <tr><td colSpan={10} className="p-8 text-center text-muted">{data?.source_updated_at ? "No matching open positions" : "Waiting for verified MT5 positions…"}</td></tr>}
    </tbody></table></div></Panel>
    <Panel title="Groups · clients · positions" actions={<div className="flex gap-2"><button className="btn-ghost text-xs" onClick={() => setExpandedGroups(groups.map((row) => row.group))}>Expand all groups</button><button className="btn-ghost text-xs" onClick={() => { setExpandedGroups([]); setExpandedClients([]); }}>Collapse all</button></div>}>
      <div className="max-h-[62vh] overflow-auto"><table className="exposure-table w-full min-w-[1320px] text-xs"><thead><tr>{fields.map(([key, label]) => <th key={key} aria-sort={key.startsWith("avg_") ? undefined : sort.sort_by === key ? sort.sort_desc ? "descending" : "ascending" : "none"}>{key.startsWith("avg_") ? <span title="Opening price weighted by lots for each symbol">{label}</span> : <button type="button" className="whitespace-nowrap" onClick={() => changeSort(key)}>{label}{sort.sort_by === key ? sort.sort_desc ? " ▾" : " ▴" : ""}</button>}</th>)}</tr></thead><tbody>
        {groups.map((group) => <React.Fragment key={group.group}><tr className="bg-[rgb(var(--color-border)/.18)] font-medium"><td><button type="button" className="text-left" aria-label={`${expandedGroups.includes(group.group) ? "Collapse" : "Expand"} group ${group.group}`} aria-expanded={expandedGroups.includes(group.group)} onClick={() => toggle(setExpandedGroups, group.group)}>{expandedGroups.includes(group.group) ? "▾" : "▸"} {group.group}</button></td><MetricCells row={group} /></tr>
          {expandedGroups.includes(group.group) && <>{!group.accounts && <tr><td colSpan={12} className="text-center text-muted">Loading client exposure…</td></tr>}{(group.accounts || []).map((client) => <React.Fragment key={client.login}><tr id={`exposure-client-${client.login}`} className={flashLogin === client.login ? "bg-amber-500/15" : ""}><td><button type="button" className="pl-5 text-left" aria-label={`${expandedClients.includes(client.login) ? "Collapse" : "Expand"} client ${client.login}`} aria-expanded={expandedClients.includes(client.login)} onClick={() => setExpandedClients((current) => current.includes(client.login) ? current.filter((id) => id !== client.login) : [...current.slice(-199), client.login])}>{expandedClients.includes(client.login) ? "▾" : "▸"} {client.login} <span className="ml-1 text-muted">{client.name} · {client.currency}</span></button></td><MetricCells row={client} client /></tr>
            {expandedClients.includes(client.login) && <tr><td colSpan={12} className="!p-0"><PositionDetails detail={data?.details?.[client.login]} client={client.login} symbolMap={symbolMap} onPage={(offset) => setDetailOffsets((current) => ({ ...current, [client.login]: offset }))} /></td></tr>}
          </React.Fragment>)}{group.accounts_total > (group.accounts?.length || 0) && <tr><td colSpan={12}>{group.detail_budget_reached ? <span className="text-muted">Select this group or narrow the login filter to load additional clients.</span> : <div className="flex items-center gap-2"><button type="button" className="btn-ghost text-xs" disabled={!group.accounts_offset} onClick={() => setClientOffsets((current) => ({ ...current, [group.group]: Math.max(0, group.accounts_offset - 100) }))}>Previous clients</button><span>{group.accounts_offset + 1}–{group.accounts_offset + (group.accounts?.length || 0)} of {group.accounts_total}</span><button type="button" className="btn-ghost text-xs" disabled={group.accounts_offset + (group.accounts?.length || 0) >= group.accounts_total} onClick={() => setClientOffsets((current) => ({ ...current, [group.group]: group.accounts_offset + 100 }))}>Next clients</button></div>}</td></tr>}</>}
        </React.Fragment>)}
        {!groups.length && <tr><td colSpan={12} className="p-8 text-center text-muted">No matching group exposure</td></tr>}
      </tbody></table></div><p className="border-t border-[rgb(var(--color-border))] px-3 py-2 text-[11px] text-muted">{summary.clients ?? "—"} clients · {summary.positions ?? "—"} positions · totals include the complete matching scope. Position details load when expanded.</p>
    </Panel>
    <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
      <Panel title="Group volume · buy / sell / net"><Bars rows={groups.slice(0, 15).map((row) => ({ ...row, label: row.group }))} group /></Panel>
      <Panel title="Exposure · observed gross / net lots"><ObservedLines rows={observed} /></Panel>
      <Panel title={`Floating P&L${currency ? ` (${currency})` : ""}`}><ObservedLines rows={observed} pnl compare={Boolean(groupFilter)} /></Panel>
      <Panel title="Volume by symbol"><Bars rows={symbols.slice(0, 15).map((row) => ({ ...row, label: row.symbol }))} /></Panel>
      <Panel title="Top clients" actions={<select aria-label="Top clients measure" className="field-input !w-auto text-xs" value={clientMetric} onChange={(event) => setClientMetric(event.target.value)}><option value="net">Absolute net lots</option><option value="gross">Gross lots</option></select>}><Bars rows={(data?.top_clients || []).map((row) => ({ ...row, label: String(row.login), net_abs: Math.abs(row.net_lots) }))} dataKey={clientMetric === "net" ? "net_abs" : "gross_lots"} signed /></Panel>
      <Panel title={`Alerts · ${data?.alerts_total ?? 0}`}><div className="h-56 overflow-auto p-3 text-xs">{(data?.alerts || []).map((alert) => <p key={`${alert.scope_type}-${alert.scope_value}`} className="border-b border-[rgb(var(--color-border))] py-2"><span className={alert.status === "critical" ? "text-danger" : "text-amber-400"}>{alert.status}</span> · {alert.scope_type} {alert.scope_value} · {text(alert.utilization)}%</p>)}{!data?.alerts?.length && <p className="py-6 text-center text-muted">{data?.limits?.some((limit) => limit.enabled) ? "No configured lot limit is breached." : "Configure real lot limits to enable utilization alerts."}</p>}</div></Panel>
    </div>
    <p className="text-[11px] text-muted">{data?.observed_since_ms ? `Observed since ${new Date(data.observed_since_ms).toLocaleString()} · ` : ""}History contains actual source samples collected for these filters. Coverage grows while the view is open; the selected range does not generate earlier data.</p>
    <details className="rounded-xl border border-[rgb(var(--color-border))] p-3" open><summary className="cursor-pointer text-sm font-medium">Account Concentration & Open Exposure per Account / Symbol</summary><div className="mt-3 grid gap-3 xl:grid-cols-[1.4fr_1fr]">
      <Panel title="Open exposure and account concentration"><div className="max-h-[400px] overflow-auto"><table className="exposure-table w-full min-w-[850px] text-xs"><thead><tr>{["Account", "Symbol", "Group", "Average buy", "Average sell", "Net lots", "Positions", "Concentration", `Floating ${currency}`].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>{(data?.concentration || []).map((row) => <tr key={`${row.login}-${row.symbol}`}><td>{row.login}</td><td>{row.symbol}</td><td>{row.group}</td><td>{text(row.avg_buy, price)}</td><td>{text(row.avg_sell, price)}</td><td>{text(row.net_lots, lots)}</td><td>{row.positions}</td><td>{text(row.concentration_pct)}%</td><td className={tone(row.floating_pnl)}>{text(row.floating_pnl)}</td></tr>)}</tbody></table></div><p className="p-2 text-[11px] text-muted">Showing {data?.concentration?.length ?? 0} of {data?.concentration_total ?? 0} account/symbol rows, ranked by concentration. Narrow filters for other accounts.</p></Panel>
      <Panel title={`Floating per account ${currency}`}><div className="h-[300px] p-2">{(data?.top_clients || []).some((row) => row.floating_pnl != null) ? <SafeResponsiveContainer width="100%" height="100%" minHeight={180}><BarChart layout="vertical" data={(data?.top_clients || []).filter((row) => row.floating_pnl != null)}><XAxis type="number" tick={{ fontSize: 9 }} /><YAxis type="category" dataKey="login" tick={{ fontSize: 9 }} /><Tooltip formatter={(value) => text(value)} /><Bar dataKey="floating_pnl" fill="#2fc58a" /></BarChart></SafeResponsiveContainer> : <p className="p-4 text-xs text-muted">No matching currency observations</p>}</div></Panel>
    </div></details>
    <details className="rounded-xl border border-[rgb(var(--color-border))] p-3" open={realizedOpen} onToggle={(event) => setRealizedOpen(event.currentTarget.open)}><summary className="mb-2 cursor-pointer text-sm font-medium">Turnover & Closed P&L · Today (UTC)</summary><Panel title="Turnover & Closed P&L · Today (UTC)"><div className="max-h-[300px] overflow-auto"><table className="exposure-table w-full min-w-[600px] text-xs"><thead><tr>{["Account", "Client", "Group", "Closed trades", "Realized", "Currency"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>{realized.map((row) => <tr key={row.login}><td>{row.login}</td><td>{row.name}</td><td>{row.group}</td><td>{row.trades}</td><td className={tone(row.pnl)}>{text(row.pnl)}</td><td>{row.currency || "—"}</td></tr>)}{!realized.length && <tr><td colSpan={6} className="p-6 text-center text-muted">{daily ? "No reported realized rows match these filters." : "Waiting for verified realized history; live exposure remains active."}</td></tr>}</tbody></table></div><p className="p-2 text-[11px] text-muted">Reported daily ranking rows from MT5 deal history. Direction, symbol and live risk-status filters apply to open exposure; realized rows use selected group and client filters.</p></Panel></details>
    <Panel title="Overnight & Aged Exposure"><div className="grid grid-cols-2 gap-3 p-3 lg:grid-cols-4"><Kpi label="Held at least 24h" value={data?.aged?.overnight_count} formatter={number} /><Kpi label={`Floating 24h+ ${currency}`} value={data?.aged?.overnight_floating} formatter={number} /><Kpi label="Positions 5d+" value={data?.aged?.count} formatter={number} /><Kpi label={`Floating 5d+ ${currency}`} value={data?.aged?.floating_pnl} formatter={number} /></div><div className="max-h-[300px] overflow-auto"><table className="exposure-table w-full min-w-[740px] text-xs"><thead><tr>{["Account", "Ticket", "Symbol", "Side", "Lots", "Opened", "Days", "Floating", "Currency"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>{(data?.aged?.positions || []).map((row) => <tr key={row.ticket}><td>{row.login}</td><td>{row.ticket}</td><td>{row.symbol}</td><td>{row.action}</td><td>{text(row.volume, lots)}</td><td>{row.opened_at ? new Date(row.opened_at).toLocaleString() : "—"}</td><td>{text(row.age_seconds / 86400)}</td><td className={tone(row.floating_pnl)}>{text(row.floating_pnl)}</td><td>{row.currency}</td></tr>)}</tbody></table></div><p className="p-2 text-[11px] text-muted">Oldest {data?.aged?.positions?.length ?? 0} of {data?.aged?.count ?? 0} aged positions shown; totals include every matching aged position.</p></Panel>
    {limitsOpen && isAdmin && <ExposureLimitsDialog limits={data?.limits || []} groups={selectedGroups} symbols={data?.available_symbols || []} onClose={() => setLimitsOpen(false)} onSaved={refresh} />}
  </div>;
}
