import React, { useState } from "react";
import { apiRequest } from "../lib/api";

const STATUSES = ["NEW", "ACKNOWLEDGED", "UNDER_REVIEW", "ESCALATED", "RESOLVED", "CLOSED"];

const labelFor = (key) => ({
  last_ip: "Last login IP",
  scoped_logins: "Accounts sharing this IP",
  minimum_accounts: "Alert when shared by",
  source: "MT5 field",
  agent_login: "Agent login",
  client_login: "Client login",
}[key] || key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()));

function EvidenceFields({ title, values }) {
  const entries = Object.entries(values || {});
  return <div className="min-w-0 rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3">
    <h4 className="text-xs font-semibold">{title}</h4>
    {entries.length ? <dl className="mt-2 space-y-2">
      {entries.map(([key, value]) => <div key={key} className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-[rgb(var(--color-border))] pb-2 last:border-0 last:pb-0">
        <dt className="text-muted">{labelFor(key)}</dt>
        <dd className="max-w-full break-words text-right font-medium text-[rgb(var(--color-text))]">
          {Array.isArray(value) ? value.length ? value.map((item, index) => <span key={`${index}-${item}`} className="ml-1 inline-flex rounded-md border border-[rgb(var(--color-border))] px-1.5 py-0.5">{key === "scoped_logins" ? "#" : ""}{String(item)}</span>) : "None" :
            value && typeof value === "object" ? Object.entries(value).map(([child, item]) => `${labelFor(child)}: ${item}`).join(" · ") : String(value ?? "Unavailable")}
          {key === "minimum_accounts" ? " accounts" : ""}
        </dd>
      </div>)}
    </dl> : <p className="mt-2 text-muted">No additional values recorded.</p>}
  </div>;
}

function displayTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : `${date.toLocaleString()} (${date.toLocaleTimeString("en-GB", { timeZone: "UTC", hour: "2-digit", minute: "2-digit" })} UTC)`;
}

export default function RiskEventPanel({ events = [], onUpdated }) {
  const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState("ACKNOWLEDGED");
  const [assignee, setAssignee] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const open = (event) => {
    setSelected(event);
    setStatus(event.status === "NEW" ? "ACKNOWLEDGED" : event.status);
    setAssignee(event.assigned_to || "");
    setNote("");
    setError("");
  };

  const save = async () => {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/api/operations/risk-events/${encodeURIComponent(selected.event_id)}/status`, {
        method: "POST",
        body: JSON.stringify({ status, assigned_to: assignee, note }),
      });
      setSelected(null);
      await onUpdated?.();
    } catch (cause) {
      setError(cause.message || "Unable to update risk event");
    } finally {
      setBusy(false);
    }
  };

  return <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4">
    <h2 className="text-sm font-semibold">Persistent risk events</h2>
    <p className="mt-1 text-xs text-muted">Recorded Anti-Fraud evidence remains available after a server restart. Dealer decisions are audited.</p>
    <div className="scrollbar-hidden mt-3 grid max-h-64 content-start gap-2 overflow-y-auto overscroll-contain pr-1 lg:grid-cols-2" tabIndex={0} role="region" aria-label="Persistent risk event list">
      {events.map((event) => <button key={event.event_id} type="button" onClick={() => open(event)} className="rounded-lg border border-[rgb(var(--color-border))] p-3 text-left text-xs hover:border-amber-500">
        <div className="flex flex-wrap justify-between gap-2"><span className="font-semibold">{event.detector.replaceAll("_", " ")} · #{event.login}</span><span>{event.severity.toUpperCase()} · {event.status}</span></div>
        <p className="mt-1 line-clamp-2 text-muted">{event.evidence?.evidence || "Evidence unavailable"}</p>
        {event.detector === "ip_control" && event.actual_values?.scoped_logins?.length > 0 && (
          <p className="mt-2 font-medium text-secondary">Affected accounts: {event.actual_values.scoped_logins.map((login) => `#${login}`).join(", ")}</p>
        )}
        <p className="mt-2 text-muted">{displayTime(event.last_seen_at)} · {event.data_quality}</p>
      </button>)}
      {events.length === 0 && <p className="text-xs text-muted">No new events.</p>}
    </div>
    {selected && <div className="mt-4 rounded-lg border border-amber-500/50 p-4 text-xs">
      <div className="flex justify-between gap-3"><h3 className="font-semibold">Why this alert? · {selected.detector.replaceAll("_", " ")}</h3><button type="button" onClick={() => setSelected(null)} aria-label="Close event detail">Close</button></div>
      <p className="mt-2">{selected.evidence?.evidence || "Evidence unavailable"}</p>
      {selected.detector === "ip_control" && selected.actual_values?.scoped_logins?.length > 0 && (
        <p className="mt-2 font-semibold text-secondary">Affected accounts: {selected.actual_values.scoped_logins.map((login) => `#${login}`).join(", ")}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-muted"><span>Source: <b className="font-medium text-secondary">{selected.source?.replaceAll("_", " ") || "Unavailable"}</b></span><span>Evidence quality: <b className="font-medium text-secondary">{selected.data_quality}</b></span></div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-muted"><span>First seen: {displayTime(selected.first_seen_at)}</span><span>Last seen: {displayTime(selected.last_seen_at)}</span></div>
      <div className="mt-3 grid gap-3 md:grid-cols-2"><EvidenceFields title="Observed values" values={selected.actual_values} /><EvidenceFields title="Alert criteria" values={selected.thresholds} /></div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <label>Status<select className="input mt-1 w-full" value={status} onChange={(event) => setStatus(event.target.value)}>{STATUSES.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
        <label>Assign to<input className="input mt-1 w-full" value={assignee} onChange={(event) => setAssignee(event.target.value)} maxLength={120} /></label>
        <label>Factual note<input className="input mt-1 w-full" value={note} onChange={(event) => setNote(event.target.value)} maxLength={4000} /></label>
      </div>
      {error && <p className="mt-2 text-red-400">{error}</p>}
      <button type="button" className="btn-secondary mt-3 px-3 py-2" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save dealer action"}</button>
    </div>}
  </section>;
}
