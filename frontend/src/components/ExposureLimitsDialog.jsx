import React, { useEffect, useRef, useState } from "react";
import { apiRequest } from "../lib/api";

const fresh = { scope_type: "group", scope_value: "", metric: "net_lots", threshold: "", warning_pct: 80,
  escalation_level: "critical", enabled: true };

export default function ExposureLimitsDialog({ limits, groups, symbols, onClose, onSaved }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = dialogRef.current;
    dialog?.querySelector("button")?.focus();
    const trap = (event) => {
      if (event.key !== "Tab") return;
      const elements = [...dialog.querySelectorAll("button,input,select")].filter((element) => !element.disabled);
      const first = elements[0]; const last = elements.at(-1);
      if (!dialog.contains(document.activeElement)) { event.preventDefault(); first?.focus(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", trap); previous?.focus(); };
  }, []);
  const [form, setForm] = useState(fresh);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const closeOnEscape = (event) => { if (event.key === "Escape" && !busy) onClose(); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const save = async (event) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await apiRequest(`/api/operations/limits${editing == null ? "" : `/${editing}`}`, {
        method: editing == null ? "POST" : "PUT", body: JSON.stringify({ ...form,
          threshold: Number(form.threshold), warning_pct: Number(form.warning_pct) }) });
      setEditing(null); setForm(fresh); await onSaved();
    } catch (failure) { setError(failure.message || "Could not save risk limit"); }
    finally { setBusy(false); }
  };
  const remove = async (id) => {
    setBusy(true); setError("");
    try { await apiRequest(`/api/operations/limits/${id}`, { method: "DELETE" }); await onSaved(); }
    catch (failure) { setError(failure.message || "Could not delete risk limit"); }
    finally { setBusy(false); }
  };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3">
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="exposure-limits-title" className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-5 shadow-xl">
      <div className="flex items-center justify-between"><h2 id="exposure-limits-title" className="font-semibold">Exposure risk limits</h2><button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>Close</button></div>
      <p className="my-3 text-xs text-muted">Limits apply to the complete selected book. Critical is 100% of the configured lot threshold. Use * for a default per client, group or symbol; an exact rule overrides that metric’s default.</p>
      {error && <p role="alert" className="mb-3 text-sm text-danger">{error}</p>}
      <form onSubmit={save} className="grid gap-3 rounded-xl border border-[rgb(var(--color-border))] p-3 sm:grid-cols-3">
        <label className="text-xs">Scope<select aria-label="Limit scope" className="field-input mt-1" value={form.scope_type} onChange={(event) => update("scope_type", event.target.value)}>{["group", "symbol", "account"].map((kind) => <option key={kind}>{kind}</option>)}</select></label>
        <label className="text-xs">Group / symbol / login<input aria-label="Limit scope value" className="field-input mt-1" required maxLength={160} list="exposure-limit-values" value={form.scope_value} onChange={(event) => update("scope_value", event.target.value)} /></label>
        <datalist id="exposure-limit-values"><option value="*" />{(form.scope_type === "group" ? groups : form.scope_type === "symbol" ? symbols : []).map((value) => <option key={value} value={value} />)}</datalist>
        <label className="text-xs">Metric<select aria-label="Limit metric" className="field-input mt-1" value={form.metric} onChange={(event) => update("metric", event.target.value)}><option value="net_lots">Absolute net lots</option><option value="gross_lots">Gross lots</option></select></label>
        <label className="text-xs">Critical lot threshold<input aria-label="Critical lot threshold" className="field-input mt-1" type="number" min="0.00000001" max="1000000000000" step="any" required value={form.threshold} onChange={(event) => update("threshold", event.target.value)} /></label>
        <label className="text-xs">Warning utilization %<input aria-label="Warning utilization" className="field-input mt-1" type="number" min={1} max={100} required value={form.warning_pct} onChange={(event) => update("warning_pct", event.target.value)} /></label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={form.enabled} onChange={(event) => update("enabled", event.target.checked)} />Enabled</label>
        <button className="btn-primary text-xs" disabled={busy}>{busy ? "Saving…" : editing == null ? "Add limit" : "Update limit"}</button>
        {editing != null && <button type="button" className="btn-secondary text-xs" disabled={busy} onClick={() => { setEditing(null); setForm(fresh); }}>Cancel edit</button>}
      </form>
      <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[580px] text-xs"><thead><tr>{["Scope", "Value", "Metric", "Threshold", "Warning", "State", "Actions"].map((name) => <th className="px-2 py-2 text-left text-muted" key={name}>{name}</th>)}</tr></thead><tbody>
        {limits.map((rule) => <tr key={rule.id} className="border-t border-[rgb(var(--color-border))]"><td className="p-2">{rule.scope_type}</td><td className="p-2">{rule.scope_value}</td><td className="p-2">{rule.metric === "net_lots" ? "Net lots" : "Gross lots"}</td><td className="p-2">{rule.threshold}</td><td className="p-2">{rule.warning_pct}%</td><td className="p-2">{rule.enabled ? "Enabled" : "Disabled"}</td><td className="space-x-2 p-2"><button type="button" className="underline" disabled={busy} onClick={() => { setEditing(rule.id); setForm({ ...fresh, ...rule }); }}>Edit</button><button type="button" className="text-danger underline" disabled={busy} onClick={() => remove(rule.id)}>Delete</button></td></tr>)}
        {!limits.length && <tr><td colSpan={7} className="p-5 text-center text-muted">No lot limits configured. Enter your actual risk policy above.</td></tr>}
      </tbody></table></div>
    </section>
  </div>;
}
