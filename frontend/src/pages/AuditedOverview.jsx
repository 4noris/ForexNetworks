import React, { useMemo, useState } from "react";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, Users } from "lucide-react";

const number = (value) => value === null || value === undefined || value === "" ? null : Number(value);
const finite = (value) => {
  const parsed = number(value);
  return parsed !== null && Number.isFinite(parsed) ? parsed : null;
};
const currencyScope = (row) => row.Currency === "Unknown"
  ? `Unknown (${row.Group})` : String(row.Currency || "Unknown");
const sourceColumns = ["Opening Equity", "Closing Equity", "Deposit", "Credit In", "Credit Out", "Additional", "Withdrawal"];
const amount = (value, signed = false) => {
  const parsed = finite(value);
  if (parsed === null) return "Unavailable";
  return `${signed && parsed > 0 ? "+" : ""}${parsed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

function Figure({ label, value, currency, tone = "neutral", caption }) {
  const toneClass = tone === "positive" ? "text-emerald-400" : tone === "negative" ? "text-rose-400" : "text-[rgb(var(--color-text))]";
  return <div className="min-w-0 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-4">
    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[rgb(var(--color-text-3))]">{label}</p>
    <p className={`mt-2 break-words text-xl font-semibold tabular-nums sm:text-2xl ${toneClass}`}>{amount(value)}</p>
    <p className="mt-1 text-xs text-[rgb(var(--color-text-2))]">{caption || currency}</p>
  </div>;
}

function Leaderboard({ title, rows, winning, limit, currency }) {
  const ranked = useMemo(() => rows
    .filter((row) => {
      const pnl = finite(row["Client PnL"]);
      return pnl !== null && (winning ? pnl > 0 : pnl < 0);
    })
    .sort((a, b) => winning
      ? finite(b["Client PnL"]) - finite(a["Client PnL"]) || Number(a.Login) - Number(b.Login)
      : finite(a["Client PnL"]) - finite(b["Client PnL"]) || Number(a.Login) - Number(b.Login))
    .slice(0, limit), [rows, winning, limit]);
  const max = Math.max(0, ...ranked.map((row) => Math.abs(finite(row["Client PnL"]))));
  const Icon = winning ? ArrowUpRight : ArrowDownRight;
  return <section className="min-w-0 rounded-2xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 shadow-sm">
    <div className="mb-3 flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Icon size={16} className={winning ? "text-emerald-400" : "text-rose-400"} />{title}</h3>
      <span className="rounded-full border border-[rgb(var(--color-border))] px-2 py-1 text-[10px] text-[rgb(var(--color-text-2))]">{ranked.length} accounts</span>
    </div>
    {ranked.length ? <ol className="space-y-2.5">{ranked.map((row, index) => <li key={`${row.Login}-${index}`} className="rounded-xl bg-[rgb(var(--color-surface))] px-3 py-2.5">
      <div className="flex items-center gap-3">
        <span className="w-5 shrink-0 text-xs font-semibold text-[rgb(var(--color-text-3))]">{index + 1}</span>
        <div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold text-[rgb(var(--color-text))]">{row.Name || `Account ${row.Login}`}</p><p className="truncate text-[11px] text-[rgb(var(--color-text-3))]">{row.Login} · {row.Group}</p></div>
        <div className="text-right"><p className={`whitespace-nowrap text-sm font-semibold tabular-nums ${winning ? "text-emerald-400" : "text-rose-400"}`}>{amount(row["Client PnL"], true)}</p><p className="text-[10px] text-[rgb(var(--color-text-3))]">{currency}</p></div>
      </div>
      <div className="ml-8 mt-2 h-1.5 overflow-hidden rounded-full bg-[rgb(var(--color-border-soft))]"><div className={`h-full rounded-full ${winning ? "bg-emerald-400" : "bg-rose-400"}`} style={{ width: `${Math.max(4, Math.abs(finite(row["Client PnL"])) / max * 100)}%` }} /></div>
    </li>)}</ol> : <p className="rounded-xl bg-[rgb(var(--color-surface))] p-4 text-xs text-[rgb(var(--color-text-2))]">No {winning ? "winning" : "losing"} accounts with complete P&amp;L in {currency}.</p>}
  </section>;
}

export default function AuditedOverview({ report }) {
  const currencies = useMemo(() => [...new Set((report.all_clients || []).map((row) => String(row.Currency || "Unknown")))], [report.all_clients]);
  const [selectedCurrency, setSelectedCurrency] = useState("");
  const [limit, setLimit] = useState(5);
  const currency = currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0];
  const full = (report.all_clients || []).find((row) => String(row.Currency || "Unknown") === currency);
  const complete = (report.complete_accounts || []).find((row) => String(row.Currency || "Unknown") === currency);
  const rows = (report.accounts || []).filter((row) => currencyScope(row) === currency && finite(row["Client PnL"]) !== null);
  const winners = report.leaderboards?.[currency]?.winners || rows;
  const losers = report.leaderboards?.[currency]?.losers || rows;
  const fullPnl = finite(full?.["Client PnL"]);
  const brokerPnl = finite(full?.["Broker PnL"]);
  const completeCount = Number(complete?.Accounts || 0);
  const totalCount = Number(full?.Accounts || 0);
  const fullAvailable = fullPnl !== null && brokerPnl !== null;
  const blockers = (report.blocking_accounts?.length ? report.blocking_accounts : (report.accounts || []).flatMap((row) => {
    const missing = sourceColumns.filter((key) => finite(row[key]) === null);
    return missing.length || String(row["Data Quality"] || "").includes("Currency changed")
      ? [{ ...row, "Missing fields": missing, Reason: row["Data Quality"] }] : [];
  })).filter((row) => currencyScope(row) === currency);

  return <div className="space-y-4">
    <section className="rounded-2xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 shadow-sm sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[rgb(var(--color-text-3))]">Audited overview</p><h2 className="mt-1 text-lg font-semibold">Client and broker P&amp;L</h2><p className="mt-1 text-xs text-[rgb(var(--color-text-2))]">{report.opening_equity_date} to {report.closing_equity_date} UTC · {report.book_mode}</p></div>
        <label className="text-xs font-medium text-[rgb(var(--color-text-2))]">Currency
          <select className="field-input mt-1 block min-w-36" value={currency || ""} onChange={(event) => setSelectedCurrency(event.target.value)}>{currencies.map((item) => <option key={item}>{item}</option>)}</select>
        </label>
      </div>
      {full ? <>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Figure label="Opening equity" value={full["Opening Equity"]} currency={currency} />
          <Figure label="Closing equity" value={full["Closing Equity"]} currency={currency} />
          <Figure label="Client P&L" value={full["Client PnL"]} currency={currency} tone={fullPnl === null ? "neutral" : fullPnl >= 0 ? "positive" : "negative"} />
          <Figure label={`Broker P&L · ${report.book_mode}`} value={full["Broker PnL"]} currency={currency} tone={brokerPnl === null ? "neutral" : brokerPnl >= 0 ? "positive" : "negative"} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex items-center gap-1 rounded-full border border-[rgb(var(--color-border))] px-2.5 py-1"><Users size={13} />{totalCount.toLocaleString()} accounts in scope</span>
          <span className="inline-flex items-center gap-1 rounded-full border border-[rgb(var(--color-border))] px-2.5 py-1"><CheckCircle2 size={13} />{completeCount.toLocaleString()} with complete inputs</span>
          {!fullAvailable && <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-amber-400"><AlertTriangle size={13} />Full scope P&amp;L unavailable</span>}
        </div>
        {!fullAvailable && <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-[rgb(var(--color-text-2))]">Some account source values are missing. The complete input cohort below excludes {Math.max(0, totalCount - completeCount).toLocaleString()} accounts and must not be read as the full scope result.</div>}
        {blockers.length > 0 && <details className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/[.04] p-3 text-xs">
          <summary className="cursor-pointer font-semibold text-amber-500">Review {blockers.length.toLocaleString()} account{blockers.length === 1 ? "" : "s"} blocking full-scope P&amp;L</summary>
          <div className="mt-3 max-h-64 space-y-2 overflow-auto">
            {blockers.map((row) => <div key={`${row.Group}-${row.Login}`} className="rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3">
              <p className="font-semibold">#{row.Login} {row.Name ? `· ${row.Name}` : ""} <span className="font-normal text-muted">· {row.Group}</span></p>
              <p className="mt-1 text-secondary">{row["Missing fields"]?.length ? `Missing: ${row["Missing fields"].join(", ")}` : row.Reason}</p>
              <p className="mt-1 text-muted">Opening: {row["Opening Source"] || "Unavailable"} · Closing: {row["Closing Source"] || "Unavailable"}</p>
            </div>)}
          </div>
        </details>}
        {complete && <div className="mt-3 grid gap-2 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3 text-xs sm:grid-cols-3"><div><p className="text-[rgb(var(--color-text-3))]">Complete input cohort</p><p className="mt-1 font-semibold">{completeCount.toLocaleString()} of {totalCount.toLocaleString()} accounts</p></div><div><p className="text-[rgb(var(--color-text-3))]">Client P&amp;L · {currency}</p><p className="mt-1 font-semibold tabular-nums">{amount(complete["Client PnL"])}</p></div><div><p className="text-[rgb(var(--color-text-3))]">Broker P&amp;L · {currency}</p><p className="mt-1 font-semibold tabular-nums">{amount(complete["Broker PnL"])}</p></div></div>}
      </> : <p className="mt-4 text-sm text-[rgb(var(--color-text-2))]">No accounts in this report.</p>}
    </section>

    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-base font-semibold">Top client winners and losers</h2><p className="mt-1 text-xs text-[rgb(var(--color-text-2))]">Ranked by client P&amp;L for {currency || "the selected currency"}; accounts with missing P&amp;L are excluded.</p></div><label className="text-xs text-[rgb(var(--color-text-2))]">Show top <select className="field-input ml-2" value={limit} onChange={(event) => setLimit(Number(event.target.value))}><option value={5}>5</option><option value={10}>10</option></select></label></div>
      <div className="grid gap-3 lg:grid-cols-2"><Leaderboard title="Top winners" rows={winners} winning limit={limit} currency={currency} /><Leaderboard title="Top losers" rows={losers} winning={false} limit={limit} currency={currency} /></div>
    </section>
  </div>;
}
