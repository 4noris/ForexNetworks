import React from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import SafeResponsiveContainer from "./SafeResponsiveContainer";

const money = (value) => value == null ? "Unavailable" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
const compact = (value) => value == null ? "Unavailable" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 }).format(value);
const colors = { net: "#7774f6", profit: "#98c96b", commission: "#21c1d3", swap: "#f4a266", fee: "#b784cf" };

function Plot({ title, points, lines, numeric = false }) {
  const available = points.some((point) => lines.some(([key]) => point[key] != null));
  return <section className="min-w-0 rounded-xl border border-[rgb(var(--color-border))] p-4">
    <h3 className="text-sm font-semibold">{title}</h3>
    {available ? <div className="mt-4 h-72"><SafeResponsiveContainer><LineChart data={points} margin={{ top: 8, right: 16, left: 8, bottom: 12 }}>
      <CartesianGrid vertical={false} stroke="rgb(var(--color-border))" />
      <XAxis dataKey={numeric ? "horizon" : "time"} type="number" domain={["dataMin", "dataMax"]} tickFormatter={(value) => numeric ? `${value}s` : new Date(value).toISOString().slice(5, 16).replace("T", " ")} tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }} />
      <YAxis tickFormatter={compact} tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }} width={65} />
      <ReferenceLine y={0} stroke="rgb(var(--color-text-3))" strokeDasharray="7 6" />
      <Tooltip formatter={money} labelFormatter={(value) => numeric ? `${value} seconds after execution` : `${new Date(value).toISOString()} UTC`} contentStyle={{ background: "rgb(var(--color-card))", borderColor: "rgb(var(--color-border))", fontSize: 11 }} />
      <Legend wrapperStyle={{ fontSize: 11 }} />
      {lines.map(([key, label, color]) => <Line key={key} dataKey={key} name={label} type="linear" stroke={color} strokeWidth={2} dot={numeric ? { r: 3 } : false} connectNulls={false} isAnimationActive={false} />)}
    </LineChart></SafeResponsiveContainer></div> : <div className="flex min-h-72 items-center justify-center text-center text-xs leading-6 text-muted">Unavailable: the source records do not support this monetary calculation.</div>}
  </section>;
}

export default function MarkoutDashboard({ data }) {
  const report = data.dashboard;
  if (!report) return null;
  const hedgeHint = report.unavailable_reason || "Connect execution-level LP/bridge hedge allocations; MT5 ticks provide prices only.";
  const cards = [
    ["Total attribution", report.total_attribution_usd == null ? "Hedge feed required" : money(report.total_attribution_usd), hedgeHint],
    ["DPM · 900s", money(report.dpm), "Client execution-to-mid P&L per covered USD million; positive favours the client."],
    ["Execution volume", compact(report.volume_usd), "Gross executed notional in USD. All fills must have verified units, contract size and historical conversion."],
    ["Executed fills", report.trades.toLocaleString(), "Actual buy/sell deal tickets, including partial fills and exits."],
    ["Internalised", report.internalised_pct == null ? "Hedge feed required" : `${report.internalised_pct}%`, hedgeHint],
    ["Undistributed residual", report.residual_usd == null ? "Hedge feed required" : money(report.residual_usd), hedgeHint],
  ];
  return <div className="space-y-5">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">{cards.map(([label, value, hint]) => <div key={label} className="min-w-0 rounded-xl border border-[rgb(var(--color-border))] p-4" title={hint}><p className="text-xs text-muted">{label}</p><p className={`mt-3 font-semibold tabular-nums ${["Unavailable", "Hedge feed required"].includes(value) ? "text-sm" : "break-words text-xl sm:text-2xl"}`}>{value}</p></div>)}</div>
    <p className="rounded-lg border border-[rgb(var(--color-border))] p-3 text-xs leading-5 text-muted">Prices and markout curves use MT5 execution and tick history over WebSocket. Internalised %, total attribution and undistributed residual require LP/bridge hedge-allocation records. MT5 ticks do not contain those allocations.</p>
    <div className="grid gap-4 xl:grid-cols-2">
      <Plot title="Cumulative recorded client P&L (USD)" points={report.cumulative} lines={Object.entries(colors).map(([key, color]) => [key, key === "net" ? "Net realised" : key, color])} />
      <Plot title="Markout curve (USD / million)" points={report.curves} lines={[["dpm", "Client execution-to-mid", colors.net]]} numeric />
    </div>
    <section className="rounded-xl border border-[rgb(var(--color-border))] p-4"><h3 className="text-sm font-semibold">SI attributed vs B-book P&L (USD)</h3><p className="py-10 text-center text-xs leading-6 text-muted">{report.unavailable_reason}</p></section>
    <p className="text-[11px] leading-5 text-muted">{report.monetary_method} The cumulative chart shows recorded client deal profit and costs; it is not an LP attribution or broker hedge-profit estimate. Account currency: {report.currency || "unavailable"}.</p>
  </div>;
}
