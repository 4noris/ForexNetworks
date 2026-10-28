import React, { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { apiRequest } from "../lib/api";

export default function SpreadAlertSettings({ telegramReady }) {
  const [loaded, setLoaded] = useState(null);
  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState("");
  const [cooldown, setCooldown] = useState(300);
  const [symbols, setSymbols] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [symbolSearch, setSymbolSearch] = useState("");
  const [symbolResults, setSymbolResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setSymbolResults([]);
    setSearchError("");
    if (!symbolSearch.trim()) { setSearching(false); return undefined; }
    setSearching(true);
    const timer = window.setTimeout(() => {
      apiRequest(`/api/notifications/spread/symbols?q=${encodeURIComponent(symbolSearch.trim())}&limit=20`, { signal: controller.signal })
        .then((result) => { if (!controller.signal.aborted) setSymbolResults(result.symbols || []); })
        .catch((cause) => { if (!controller.signal.aborted) setSearchError(cause.message || "MT5 symbol catalog is unavailable"); })
        .finally(() => { if (!controller.signal.aborted) setSearching(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [symbolSearch]);
  useEffect(() => {
    let active = true;
    apiRequest("/api/notifications/spread/settings").then((result) => {
      if (!active) return;
      setLoaded(result);
      setEnabled(result.settings.enabled);
      setThreshold(result.settings.default_threshold_points ?? "");
      setCooldown(result.settings.cooldown_seconds);
      setSymbols(Object.entries(result.settings.symbol_thresholds).map(([symbol, value]) => ({ symbol, value })));
    }).catch((cause) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (loaded && window.location.hash === "#spread-alerts") document.getElementById("spread-alerts")?.scrollIntoView({ block: "start" });
  }, [loaded]);

  const save = async (event) => {
    event.preventDefault();
    const overrides = {};
    for (const row of symbols) {
      const symbol = row.symbol.trim();
      if (!symbol || Object.hasOwn(overrides, symbol) || !Number.isFinite(Number(row.value)) || Number(row.value) <= 0) {
        setError("Use a unique exact symbol and a threshold greater than zero for every row.");
        return;
      }
      overrides[symbol] = Number(row.value);
    }
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest("/api/notifications/spread/settings", { method: "PUT", body: JSON.stringify({
        enabled, default_threshold_points: threshold === "" ? null : Number(threshold),
        symbol_thresholds: overrides, cooldown_seconds: Number(cooldown),
      }) });
      setLoaded(result);
      toast.success("Spread alert settings saved");
    } catch (cause) { setError(cause.message || "Unable to save spread alerts"); }
    finally { setBusy(false); }
  };
  const changeSymbol = (index, key, value) => setSymbols((rows) => rows.map((row, i) => i === index ? { ...row, [key]: value } : row));
  return <section id="spread-alerts" className="mt-7 scroll-mt-6 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-5">
    <h2 className="text-sm font-semibold">Spread threshold alerts</h2>
    <p className="mt-2 text-xs leading-5 text-muted">Monitor fresh MT5 quotes on the server, even when Market Watch is closed. Send a Telegram alert when spread exceeds the limit. Values are points, using each symbol’s digits.</p>
    {error && <p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
    {!loaded ? <p className="mt-3 text-xs text-muted">Loading spread controls…</p> : <form onSubmit={save} className="mt-4 space-y-4">
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Enable automatic spread alerts</label>
      {!(telegramReady ?? loaded.telegram_enabled) && <p className="text-xs text-amber-400">Delivery is paused. Enable Telegram alerts and configure the bot and a default or spread channel below.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-muted">Default threshold (points)<input className="field-input mt-1" type="number" step="any" min="0.000001" max="1000000000" placeholder="Blank: monitor only symbols below" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
        <label className="text-xs text-muted">Alert cooldown per symbol (seconds)<input className="field-input mt-1" type="number" min="30" max="86400" step="1" required value={cooldown} onChange={(event) => setCooldown(event.target.value)} /></label>
      </div>
      <p className="text-[11px] leading-5 text-muted">Exact symbol thresholds override the default. Suffixes are separate (XAUUSD and XAUUSD.x). A continuing breach repeats at most once per cooldown; quotes older than five seconds never trigger alerts.</p>
      <div className="rounded-lg border border-[rgb(var(--color-border))] p-3">
        <label className="block text-xs text-muted">Search MT5 symbols<input className="field-input mt-1" type="search" value={symbolSearch} maxLength={120} placeholder="Search symbol or description, e.g. XAUUSD" onChange={(event) => setSymbolSearch(event.target.value)} /></label>
        {symbolSearch.trim() && <div className="mt-2 max-h-52 overflow-auto" aria-live="polite">
          {searching ? <p className="py-2 text-xs text-muted">Searching MT5 symbols…</p> : searchError ? <p role="alert" className="py-2 text-xs text-danger">{searchError}</p> : !symbolResults.length ? <p className="py-2 text-xs text-muted">No matching MT5 symbols.</p> : symbolResults.map((item) => {
            const added = symbols.some((row) => row.symbol.trim() === item.symbol);
            return <div key={item.symbol} className="flex items-center justify-between gap-3 border-b border-[rgb(var(--color-border))] py-2 last:border-0">
              <div className="min-w-0"><p className="text-xs font-semibold">{item.symbol}</p><p className="truncate text-[10px] text-muted">{item.description}</p></div>
              <button type="button" className="btn-secondary shrink-0 text-xs" disabled={added} aria-label={`Add ${item.symbol}`} onClick={() => setSymbols((rows) => rows.some((row) => row.symbol.trim() === item.symbol) ? rows : [...rows, { symbol: item.symbol, value: threshold }])}>{added ? "Added" : "Add"}</button>
            </div>;
          })}
        </div>}
      </div>
      <div className="space-y-2">{symbols.map((row, index) => <div key={index} className="flex flex-wrap items-end gap-2">
        <label className="min-w-32 flex-1 text-xs text-muted">MT5 symbol<input aria-label={`Spread symbol ${index + 1}`} className="field-input mt-1" value={row.symbol} required maxLength={128} onChange={(event) => changeSymbol(index, "symbol", event.target.value)} /></label>
        <label className="min-w-28 flex-1 text-xs text-muted">Threshold (points)<input aria-label={`Spread threshold ${index + 1}`} className="field-input mt-1" type="number" step="any" min="0.000001" max="1000000000" required value={row.value} onChange={(event) => changeSymbol(index, "value", event.target.value)} /></label>
        <button type="button" className="btn-secondary text-xs" aria-label={`Remove spread symbol ${index + 1}`} onClick={() => setSymbols((rows) => rows.filter((_, i) => i !== index))}>Remove</button>
      </div>)}</div>
      <div className="flex flex-wrap gap-2"><button type="button" className="btn-secondary text-xs" onClick={() => setSymbols((rows) => [...rows, { symbol: "", value: "" }])}>Add manually</button><button type="submit" disabled={busy} className="btn-primary text-xs">{busy ? "Saving…" : "Save spread alerts"}</button></div>
      {loaded.last_alert && <p className="text-[11px] text-muted">Last alert: {loaded.last_alert.symbol} · {loaded.last_alert.spread} points / {loaded.last_alert.threshold} limit · {loaded.last_alert.status}. {loaded.last_alert.detail}</p>}
    </form>}
  </section>;
}
