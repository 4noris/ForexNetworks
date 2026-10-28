import { initialBookGroups } from "../lib/bookDefaults";
import React, { useEffect, useMemo, useRef, useState } from "react";
import GroupTreeSelect from "../components/GroupTreeSelect";
import AuditedOverview from "./AuditedOverview";
import { apiDownload, apiRequest, fetchCompleteMt5Groups, getBookGroupSettings, getCachedGroupsForProfile, getDefaultLiveGroupForProfile, useStoredProfile } from "../lib/api";

const utcDay = (offset = 0) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const money = (value) => value == null ? "Unavailable" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
const visible = (value) => value == null || value === "" ? "Unavailable" : String(value);
const ACCOUNT_COLUMNS = ["Login", "Name", "Group", "Currency", "Opening Equity", "Closing Equity", "Deposit", "Credit In", "Credit Out", "Additional", "Withdrawal", "Net PnL", "Client PnL", "Broker PnL", "Client Commission", "Client Swap", "Data Quality"];
const AUDIT_COLUMNS = ["Time UTC", "Login", "Deal", "Action", "Raw Amount", "Category", "Reported Amount", "Client Commission", "Client Swap", "Review", "Comment"];

function DataTable({ rows, columns, searchable = false }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const filtered = useMemo(() => !search.trim() ? rows : rows.filter((row) => columns.some((column) => String(row[column] ?? "").toLowerCase().includes(search.trim().toLowerCase()))), [rows, columns, search]);
  const pageSize = 50;
  const maxPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
  const currentPage = Math.min(page, maxPage);
  return <div className="space-y-2">
    {searchable && <label className="block text-xs text-muted">Search these rows<input className="field-input mt-1 w-full max-w-sm" value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="Login, group, category, or amount" /></label>}
    <div className="scrollbar-hidden max-h-[460px] overflow-auto rounded-lg border border-[rgb(var(--color-border))]">
      <table className="w-full min-w-[900px] text-left text-xs"><thead className="sticky top-0 bg-[rgb(var(--color-card))]"><tr>{columns.map((column) => <th className="px-3 py-2" key={column}>{column}</th>)}</tr></thead><tbody>{filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map((row, index) => <tr key={`${currentPage}-${index}`} className="border-t border-[rgb(var(--color-border))]">{columns.map((column) => <td className="whitespace-nowrap px-3 py-2" key={column}>{typeof row[column] === "number" && column !== "Login" && column !== "Deal" ? money(row[column]) : visible(row[column])}</td>)}</tr>)}</tbody></table>
      {!filtered.length && <p className="p-4 text-xs text-muted">No rows in this view.</p>}
    </div>
    <div className="flex items-center justify-between text-xs text-muted"><span>{filtered.length.toLocaleString()} rows{rows.length !== filtered.length ? ` of ${rows.length.toLocaleString()}` : ""}</span><div className="flex items-center gap-2"><button className="btn-secondary px-2 py-1" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous</button><span>{currentPage + 1} / {maxPage + 1}</span><button className="btn-secondary px-2 py-1" disabled={currentPage >= maxPage} onClick={() => setPage(currentPage + 1)}>Next</button></div></div>
  </div>;
}

export default function AuditedBrokerage() {
  const profile = useStoredProfile();
  const [groups, setGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [opening, setOpening] = useState(() => utcDay(-1));
  const [closing, setClosing] = useState(() => utcDay());
  const [customRange, setCustomRange] = useState(false);
  const [flowFrom, setFlowFrom] = useState(() => utcDay());
  const [flowTo, setFlowTo] = useState(() => utcDay());
  const [bookMode, setBookMode] = useState("B-Book");
  const [logins, setLogins] = useState("");
  const [allowLive, setAllowLive] = useState(false);
  const [report, setReport] = useState(null);
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activeTable, setActiveTable] = useState("accounts");
  const [downloading, setDownloading] = useState(false);
  const [reviewDrafts, setReviewDrafts] = useState({});
  const [reviewingDeal, setReviewingDeal] = useState(null);
  const pollTimer = useRef(null);

  useEffect(() => {
    if (!profile) return undefined;
    let active = true;
    setGroups(getCachedGroupsForProfile(profile.id));
    Promise.all([fetchCompleteMt5Groups(profile), getBookGroupSettings()]).then(([available, books]) => {
      if (!active) return;
      setGroups(available);
      setSelectedGroups((current) => {
        const retained = current.filter((group) => available.includes(group));
        if (retained.length) return retained;
        const preferred = getDefaultLiveGroupForProfile(profile.id);
        return initialBookGroups(available, books, "", preferred);
      });
    }).catch((cause) => { if (active) setError(cause.message || "Unable to load groups"); });
    return () => { active = false; };
  }, [profile]);

  useEffect(() => () => window.clearTimeout(pollTimer.current), []);

  const run = async (forceRefresh = false) => {
    window.clearTimeout(pollTimer.current);
    setError("");
    const raw = logins.trim();
    if (raw && !/^[\d\s,;]+$/.test(raw)) { setError("Enter positive login numbers separated by commas or spaces."); return; }
    const selectedLogins = raw ? [...new Set(raw.split(/[\s,;]+/).filter(Boolean).map(Number))] : [];
    if (selectedLogins.some((login) => !Number.isSafeInteger(login) || login <= 0)) { setError("Enter valid positive login numbers."); return; }
    if (!selectedGroups.length) { setError("Select at least one group."); return; }
    if (opening > closing || closing > utcDay() || (customRange && (flowFrom > flowTo || flowTo > utcDay()))) { setError("Choose ordered UTC dates with no future date."); return; }
    setReport(null);
    setJobId("");
    setBusy(true);
    try {
      const result = await apiRequest("/api/reports/brokerage/audited", { method: "POST", body: JSON.stringify({ selected_groups: selectedGroups, selected_logins: selectedLogins, opening_equity_date: opening, closing_equity_date: closing, ...(customRange ? { date_from: flowFrom, date_to: flowTo } : {}), book_mode: bookMode, allow_live: allowLive, force_refresh: forceRefresh }) });
      setJobId(result.job_id);
      if (result.status === "ready") { setReport(result); setBusy(false); }
      else if (result.status === "error") { setError(result.error || "Report failed"); setBusy(false); }
      else poll(result.job_id);
    } catch (cause) { setError(cause.message || "Unable to start report"); setBusy(false); }
  };

  const poll = (id) => {
    pollTimer.current = window.setTimeout(async () => {
      try {
        const result = await apiRequest(`/api/reports/brokerage/audited/${id}`, { dedupeMs: 0 });
        if (result.status === "ready") { setReport(result); setBusy(false); }
        else if (result.status === "error") { setError(result.error || "Report failed"); setBusy(false); }
        else poll(id);
      } catch (cause) { setError(cause.message || "Unable to check report"); setBusy(false); }
    }, 1500);
  };

  const download = async () => {
    if (!jobId) return;
    setDownloading(true);
    try {
      const { blob, filename } = await apiDownload(`/api/reports/brokerage/audited/${jobId}/export`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename || `AuditedBrokerage_${opening}_to_${closing}.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) { setError(cause.message || "Export failed"); }
    finally { setDownloading(false); }
  };

  const reviewTransaction = async (row) => {
    const deal = Number(row.Deal);
    const draft = reviewDrafts[deal] || {};
    if (!draft.category || !draft.reason?.trim()) {
      setError("Choose an allocation and enter a factual review reason.");
      return;
    }
    setReviewingDeal(deal);
    setError("");
    try {
      await apiRequest(`/api/reports/brokerage/audited/${jobId}/review`, {
        method: "POST",
        body: JSON.stringify({ login: Number(row.Login), deal,
          action: Number(row.Action), expected_profit: Number(row["Raw Amount"]),
          category: draft.category, reason: draft.reason.trim() }),
      });
      await run(true);
    } catch (cause) {
      setError(cause.message || "Unable to save the reviewed allocation.");
    } finally {
      setReviewingDeal(null);
    }
  };

  const tableViews = {
    accounts: [report?.accounts || [], ACCOUNT_COLUMNS],
    audit: [report?.transaction_audit || [], AUDIT_COLUMNS],
    quality: [report?.diagnostics || [], ["Group", "Users", "Opening Daily Rows", "Closing Daily Rows", "Deals", "Accounts Included", "Created After Closing", "Request Errors"]],
  };
  const [tableRows, tableColumns] = tableViews[activeTable];
  return <div className="space-y-4 pb-8">
    <header><h1 className="text-xl font-semibold">Audited Broker P&amp;L</h1><p className="mt-1 text-xs text-muted">End-of-day UTC equity snapshots and MT5 deal cash flows. Missing values stay unavailable. Uses the configured RMS Manager connection.</p></header>
    <section className="grid gap-3 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 md:grid-cols-2 xl:grid-cols-4">
      <div className="md:col-span-2 xl:col-span-4"><GroupTreeSelect groups={groups} value={selectedGroups} onChange={setSelectedGroups} label="Report Groups" emptyMeansAll={false} className="max-w-lg" /></div>
      <label className="text-xs text-muted">Opening equity date (UTC)<input className="field-input mt-1 w-full" type="date" value={opening} max={utcDay()} onChange={(event) => setOpening(event.target.value)} /></label>
      <label className="text-xs text-muted">Closing equity date (UTC)<input className="field-input mt-1 w-full" type="date" value={closing} min={opening} max={utcDay()} onChange={(event) => setClosing(event.target.value)} /></label>
      <label className="text-xs text-muted">Book type<select className="field-input mt-1 w-full" value={bookMode} onChange={(event) => setBookMode(event.target.value)}><option>B-Book</option><option>A-Book</option></select></label>
      <label className="text-xs text-muted">Login filter (optional)<input className="field-input mt-1 w-full" value={logins} onChange={(event) => setLogins(event.target.value)} placeholder="10001, 10002" /></label>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={customRange} onChange={(event) => setCustomRange(event.target.checked)} />Use separate transaction dates</label>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={allowLive} onChange={(event) => setAllowLive(event.target.checked)} />Allow provisional live equity for today</label>
      {customRange && <><label className="text-xs text-muted">Transaction start (UTC)<input className="field-input mt-1 w-full" type="date" value={flowFrom} max={utcDay()} onChange={(event) => setFlowFrom(event.target.value)} /></label><label className="text-xs text-muted">Transaction end (UTC)<input className="field-input mt-1 w-full" type="date" value={flowTo} min={flowFrom} max={utcDay()} onChange={(event) => setFlowTo(event.target.value)} /></label></>}
      <div className="flex items-center gap-2 md:col-span-2 xl:col-span-4"><button className="btn-primary" disabled={busy} onClick={() => run(true)}>{busy ? "Calculating..." : "Run audited report"}</button>{report && <button className="btn-secondary" disabled={downloading} onClick={download}>{downloading ? "Preparing..." : "Download Excel audit"}</button>}</div>
    </section>
    {error && <p role="alert" className="rounded-lg border border-red-500/40 p-3 text-xs text-red-400">{error}</p>}
    {busy && <p role="status" className="text-xs text-muted">MT5 history is being checked in the background. You can continue using other tabs.</p>}
    {report && <>
      <AuditedOverview report={report} />
      {(report.unresolved_transactions || []).length > 0 && <section className="space-y-3 rounded-xl border border-amber-500/35 bg-amber-500/[.04] p-4">
        <h2 className="text-sm font-semibold text-amber-400">Transactions requiring a reviewed allocation</h2>
        <p className="text-xs text-muted">These postings can block full-scope equity P&amp;L. An administrator can classify each exact MT5 deal after checking its purpose; the report is recalculated after saving.</p>
        {report.unresolved_transactions.map((row) => <div key={`${row.Login}-${row.Deal}`} className="grid gap-2 rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3 text-xs lg:grid-cols-[minmax(0,1fr)_12rem_minmax(12rem,1fr)_auto] lg:items-end">
          <div><strong>Deal #{row.Deal} · Account #{row.Login}</strong><p className="mt-1 text-muted">{row.Group} · Action {row.Action} · Amount {money(row["Raw Amount"])} · {row.Comment || "No comment"}</p></div>
          <label className="text-muted">Allocation<select className="field-input mt-1 w-full" value={reviewDrafts[row.Deal]?.category || ""} onChange={(event) => setReviewDrafts((drafts) => ({ ...drafts, [row.Deal]: { ...drafts[row.Deal], category: event.target.value } }))}><option value="">Choose after review</option>{["Equity performance", "Deposit", "Withdrawal", "Credit In", "Credit Out", "Additional"].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label className="text-muted">Factual reason<input className="field-input mt-1 w-full" value={reviewDrafts[row.Deal]?.reason || ""} onChange={(event) => setReviewDrafts((drafts) => ({ ...drafts, [row.Deal]: { ...drafts[row.Deal], reason: event.target.value } }))} placeholder="Evidence for this exact posting" /></label>
          <button type="button" className="btn-secondary" disabled={reviewingDeal === Number(row.Deal) || busy || !Number.isFinite(Number(row["Raw Amount"]))} onClick={() => reviewTransaction(row)}>{reviewingDeal === Number(row.Deal) ? "Saving..." : "Save review"}</button>
        </div>)}
      </section>}
      {(report.warnings?.length > 0 || report.excluded_new_accounts > 0) && <details className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 text-xs"><summary className="cursor-pointer font-semibold text-amber-400">Data quality and scope · {(report.warnings?.length || 0) + (report.excluded_new_accounts > 0 ? 1 : 0)} notices</summary>{report.excluded_new_accounts > 0 && <p className="mt-3 text-[rgb(var(--color-text-2))]">{report.excluded_new_accounts} accounts created after the closing date were excluded.</p>}<div className="scrollbar-hidden mt-3 max-h-48 space-y-2 overflow-auto text-[rgb(var(--color-text-2))]">{report.warnings?.map((warning, index) => <p key={`${index}-${warning}`} className="border-l-2 border-amber-500/40 pl-2">{warning}</p>)}</div></details>}
      <details className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4"><summary className="cursor-pointer text-sm font-semibold">Calculation and report scope</summary><code className="mt-3 block whitespace-normal rounded-lg bg-[rgb(var(--color-surface))] p-3 text-xs">{report.formula}</code><p className="mt-3 text-xs text-muted">B-Book broker P&amp;L = negative client P&amp;L. A-Book broker P&amp;L = negative posted client commission and swap. LP costs and markup are not supplied by this report.</p><p className="mt-2 text-xs text-muted">{report.accounts_total?.toLocaleString()} accounts · {report.audit_total?.toLocaleString()} audited deals · Generated {report.generated_at} · {report.coverage ? "Group coverage complete" : "Group coverage incomplete"} · {report.aligned ? "Dates aligned" : "Transaction dates differ from equity period"}</p></details>
      <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4"><div className="mb-3 flex flex-wrap gap-2">{[["accounts", "Account details"], ["audit", "Transaction audit"], ["quality", "Retrieval diagnostics"]].map(([key, label]) => <button key={key} className={activeTable === key ? "btn-primary px-2 py-1 text-xs" : "btn-secondary px-2 py-1 text-xs"} onClick={() => setActiveTable(key)}>{label}</button>)}</div><DataTable rows={tableRows} columns={tableColumns} searchable /></section>
      {(report.accounts_total > 5000 || report.audit_total > 2000) && <p className="text-xs text-muted">The on-screen tables show the first 5,000 accounts and 2,000 audit rows. The Excel export includes all rows.</p>}
    </>}
  </div>;
}
