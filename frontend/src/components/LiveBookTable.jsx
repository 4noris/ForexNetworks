import React, { useDeferredValue, useMemo, useState } from "react";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });
const money = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const moneyFields = new Set(["balance", "equity", "floating_pnl", "margin", "margin_free", "profit"]);
const accountColumns = [["login", "Account"], ["name", "Name"], ["group", "Group"], ["currency", "Currency"],
  ["positions", "Positions"], ["gross_lots", "Gross lots"], ["floating_pnl", "Floating P&L"],
  ["equity", "Equity"], ["balance", "Balance"], ["margin", "Margin"], ["margin_free", "Free margin"],
  ["margin_level", "Margin level %"], ["severity", "Risk"], ["signals", "Signals"]];
const positionColumns = [["ticket", "Ticket"], ["login", "Account"], ["name", "Name"], ["group", "Group"],
  ["symbol", "Symbol"], ["action", "Side"], ["volume", "Lots"], ["price_open", "Open price"],
  ["price_current", "Current price"], ["profit", "Floating P&L"], ["currency", "Currency"], ["opened_at", "Opened UTC"]];

function display(value, key) {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.join(" · ") || "—";
  if (key === "ticket") return String(value);
  if (typeof value === "number") return Number.isFinite(value) ? (moneyFields.has(key) ? money : number).format(value) : "—";
  return key === "opened_at" ? String(value).replace("T", " ").replace(/\+00:00$/, " UTC") : String(value);
}

function BookRows({ rows, columns, query, sort }) {
  const [scrollTop, setScrollTop] = useState(0);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const filtered = useMemo(() => {
    const selected = deferredQuery ? rows.filter((row) => columns.some(([key]) =>
      String(row[key] ?? "").toLowerCase().includes(deferredQuery))) : rows;
    return [...selected].sort((a, b) => {
      const left = a[sort.key], right = b[sort.key];
      const result = typeof left === "number" && typeof right === "number" ? left - right
        : String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true });
      return sort.direction === "asc" ? result : -result;
    });
  }, [rows, columns, deferredQuery, sort]);
  const rowHeight = 44;
  const visibleCount = 24;
  const first = Math.max(0, Math.min(Math.floor(Math.max(0, scrollTop - 40) / rowHeight) - 6,
    filtered.length - visibleCount));
  const last = Math.min(filtered.length, first + visibleCount);
  return <>
    <p className="border-b border-[rgb(var(--color-border))] px-4 py-2 text-[11px] text-muted" role="status">
      {filtered.length.toLocaleString()} matching records · {rows.length.toLocaleString()} loaded · Scroll or search to reach every record
    </p>
    <div data-testid="live-book-scroll" className="h-[440px] max-w-full overflow-auto" tabIndex={0} aria-label="Scrollable live records" onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}>
      <table aria-label="Complete live book" aria-rowcount={filtered.length + 1} className="w-full min-w-[1250px] table-fixed text-left text-xs">
        <thead className="sticky top-0 z-10 bg-[rgb(var(--color-surface))] text-muted"><tr style={{ height: 40 }}>
          {columns.map(([key, label]) => <th key={key} className="border-b border-[rgb(var(--color-border))] px-3 font-medium" style={{ width: key === "group" || key === "signals" ? 240 : key === "opened_at" ? 210 : 135 }}>{label}</th>)}
        </tr></thead>
        <tbody>
          {first > 0 && <tr aria-hidden="true"><td colSpan={columns.length} style={{ height: first * rowHeight, padding: 0, border: 0 }} /></tr>}
          {filtered.slice(first, last).map((row, index) => <tr key={String(row.ticket ?? row.login)} data-testid="live-book-row" aria-rowindex={first + index + 2} className="border-b border-[rgb(var(--color-border))]" style={{ height: rowHeight }}>
            {columns.map(([key]) => <td key={key} title={display(row[key], key)} className={`truncate px-3 py-0 tabular-nums ${moneyFields.has(key) && Number(row[key]) < 0 ? "text-danger" : ""}`} style={{ height: rowHeight }}>{display(row[key], key)}</td>)}
          </tr>)}
          {last < filtered.length && <tr aria-hidden="true"><td colSpan={columns.length} style={{ height: (filtered.length - last) * rowHeight, padding: 0, border: 0 }} /></tr>}
          {!filtered.length && <tr><td colSpan={columns.length} className="px-4 py-8 text-center text-muted">No matching records in the current live scope.</td></tr>}
        </tbody>
      </table>
    </div>
  </>;
}

export default function LiveBookTable({ accounts = [], positions = [] }) {
  const [kind, setKind] = useState("accounts");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState("");
  const [direction, setDirection] = useState("asc");
  const currencies = useMemo(() => new Map(accounts.map((row) => [row.login, row.currency])), [accounts]);
  const positionRows = useMemo(() => positions.map((row) => ({ ...row, currency: currencies.get(row.login) || "UNKNOWN" })), [positions, currencies]);
  const columns = kind === "accounts" ? accountColumns : positionColumns;
  const sort = useMemo(() => ({ key: sortKey || (kind === "accounts" ? "login" : "profit"), direction }), [sortKey, kind, direction]);
  return <section className="min-w-0 overflow-hidden rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))]" aria-label="All live accounts and positions">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[rgb(var(--color-border))] p-4">
      <div><h2 className="text-sm font-semibold">Complete live book</h2><p className="mt-1 text-[11px] text-muted">All matching active accounts and open positions. Money stays in each account’s currency.</p></div>
      <div className="flex gap-2">{[["accounts", "Accounts", accounts.length], ["positions", "Positions", positions.length]].map(([value, label, count]) =>
        <button type="button" key={value} aria-pressed={kind === value} className={kind === value ? "btn-primary text-xs" : "btn-secondary text-xs"} onClick={() => { setKind(value); setQuery(""); setSortKey(""); setDirection("asc"); }}>{label} ({count.toLocaleString()})</button>)}</div>
    </div>
    <div className="flex flex-wrap items-end gap-3 p-4">
      <label className="min-w-48 flex-1 text-xs text-muted">Search all live records<input type="search" className="field-input mt-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={kind === "accounts" ? "Account, name, group or risk signal" : "Ticket, account, symbol or side"} /></label>
      <label className="text-xs text-muted">Sort by<select className="field-input mt-1" value={sort.key} onChange={(event) => setSortKey(event.target.value)}>{columns.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <button type="button" className="btn-secondary text-xs" onClick={() => setDirection((value) => value === "asc" ? "desc" : "asc")}>{direction === "asc" ? "Ascending" : "Descending"}</button>
    </div>
    <BookRows key={`${kind}:${query}:${sort.key}:${sort.direction}`} rows={kind === "accounts" ? accounts : positionRows} columns={columns} query={query} sort={sort} />
  </section>;
}
