import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Copy,
  Settings2,
  ShieldAlert,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import { apiRequest, subscribeLiveData } from "../lib/api";

const DEFAULT_SETTINGS = {
  lookback_hours: 24,
  gap_seconds: 3,
  profit_threshold: 10,
  check_total_realized_pnl: false,
  minimum_total_realized_pnl: 0,
  maximum_duration_minutes: 5,
  scalp_seconds: 180,
  scalp_sequence_min: 3,
  sharp_profit_threshold: 100,
  concentration_min_trades: 3,
  deposit_alert_threshold: 1000,
  large_order_lots: 1,
  large_account_lots: 5,
  large_order_exposure_ratio: 1,
  large_account_exposure_ratio: 5,
  mini_equity_threshold: 100,
  stopout_margin_level: 100,
  news_window_minutes: 5,
  news_impacts: ["high", "medium", "low"],
  excluded_symbols: [],
};

function severityClass(severity) {
  if (severity === "critical" || severity === "high")
    return "badge badge-danger";
  if (severity === "warning") return "badge badge-amber";
  return "badge badge-success";
}

function DetectorCard({ detector, selected, onSelect, index = 0 }) {
  const blocked = detector.status === "data_required";
  const noData = detector.status === "no_data";
  const hasEvents = detector.events > 0;
  return (
    <button
      type="button"
      onClick={() => onSelect(detector.key)}
      style={{ animationDelay: `${Math.min(index, 12) * 35}ms` }}
      className={`fraud-reveal group min-w-0 rounded-lg border px-3 py-3 text-left transition hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-500 ${selected ? "border-amber-500 bg-amber-500/10" : hasEvents ? "border-red-500/50 bg-red-500/[.04]" : noData ? "border-amber-500/50 bg-amber-500/[.04]" : "border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] hover:border-[rgb(var(--color-text-3))]"}`}
    >
      <div className="flex items-center gap-3">
        <span
          className={`h-8 w-1 shrink-0 rounded-full ${hasEvents ? "bg-red-500" : noData ? "bg-amber-500" : blocked ? "bg-[rgb(var(--color-text-3))]" : "bg-emerald-500"}`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className={`text-xs font-semibold leading-5 ${hasEvents ? "text-orange-500" : ""}`}>
              {detector.name}
            </h3>
            {blocked ? (
              <span className="badge shrink-0">Source unavailable</span>
            ) : noData ? (
              <span className="badge badge-amber shrink-0">No data</span>
            ) : hasEvents ? (
              <span className="badge badge-danger shrink-0">
                {detector.events}
              </span>
            ) : (
              <CheckCircle2 size={15} className="shrink-0 text-success" />
            )}
          </div>
          <p className="mt-1 text-[11px] leading-4 text-muted">
            {detector.description}
          </p>
          {blocked && <p className="mt-1 text-[10px] font-medium text-secondary">Needed: {detector.source}</p>}
        </div>
      </div>
    </button>
  );
}

function NumberField({ label, value, onChange, min = 0, step = 1 }) {
  return (
    <label className="text-[11px] text-muted">
      {label}
      <input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="input mt-2 w-full"
      />
    </label>
  );
}

export default function AntiFraud({ profile, selectedGroups, active = true }) {
  const [settings, setSettings] = useState(() => {
    try {
      return {
        ...DEFAULT_SETTINGS,
        ...JSON.parse(localStorage.getItem(`antifraud:${profile.id}`) || "{}"),
      };
    } catch {
      return DEFAULT_SETTINGS;
    }
  });
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [selectedDetector, setSelectedDetector] = useState("all");
  const [showToxicClients, setShowToxicClients] = useState(false);
  const [selectedClient, setSelectedClient] = useState(null);
  const [clock, setClock] = useState(Date.now);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const activeRequest = useRef(false);
  const requestController = useRef(null);
  const requestGeneration = useRef(0);

  useEffect(() => {
    let stored = {};
    try {
      stored = JSON.parse(
        localStorage.getItem(`antifraud:${profile.id}`) || "{}",
      );
    } catch {
      stored = {};
    }
    setSettings({ ...DEFAULT_SETTINGS, ...stored });
    setSnapshot(null);
    setSelectedDetector("all");
  }, [profile.id]);

  const requestSnapshot = useCallback(
    async (background = false, replace = false, fresh = false) => {
      if (!profile || !selectedGroups.length) return false;
      if (activeRequest.current && !replace) return false;
      if (replace) requestController.current?.abort();
      const generation = ++requestGeneration.current;
      const controller = new AbortController();
      requestController.current = controller;
      activeRequest.current = true;
      if (!background) setLoading(true);
      try {
        const data = await apiRequest("/api/risk/antifraud", {
          method: "POST",
          timeoutMs: 300_000,
          signal: controller.signal,
          body: JSON.stringify({
            server: profile.server,
            login: profile.mt5_login,
            password: profile.mt5_password,
            selected_groups: selectedGroups,
            excluded_logins: [],
            group_filter_rules: "",
            refresh_token: fresh ? Date.now() : 0,
            ...settings,
            news_impacts: settings.news_impacts?.length ? settings.news_impacts : DEFAULT_SETTINGS.news_impacts,
            calendar_events: [],
          }),
        });
        if (controller.signal.aborted || generation !== requestGeneration.current) return false;
        if (data.status === "warming") {
          return false;
        }
        setSnapshot(data);
        return true;
      } catch (error) {
        if (!controller.signal.aborted && generation === requestGeneration.current && error.message !== "Request was cancelled")
          toast.error(error.message || "Anti-fraud scan failed");
        return false;
      } finally {
        if (generation === requestGeneration.current) {
          activeRequest.current = false;
          requestController.current = null;
          if (!background) setLoading(false);
        }
      }
    },
    [profile, selectedGroups, settings],
  );

  useEffect(() => {
    if (selectedGroups.length) return;
    requestGeneration.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    activeRequest.current = false;
    setLoading(false);
    setSnapshot(null);
  }, [selectedGroups.length]);

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    let firstRequest = true;
    const stopLive = subscribeLiveData("/api/risk/antifraud", {
      selected_groups: selectedGroups, excluded_logins: [], group_filter_rules: "",
      ...settings, news_impacts: settings.news_impacts?.length ? settings.news_impacts : DEFAULT_SETTINGS.news_impacts,
      calendar_events: [],
    }, (data) => {
      if (cancelled || document.hidden || data.status === "warming") return;
      setSnapshot(data);
    });
    const run = async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 350));
      if (cancelled) return;
      while (!cancelled) {
        const ready = await requestSnapshot(!firstRequest, firstRequest, false);
        firstRequest = false;
        if (!cancelled)
          await new Promise((resolve) =>
            window.setTimeout(resolve, ready ? 5_000 : 2_000),
          );
      }
    };
    run();
    return () => {
      cancelled = true;
      stopLive();
      requestGeneration.current += 1;
      requestController.current?.abort();
      requestController.current = null;
      activeRequest.current = false;
    };
  }, [active, requestSnapshot, selectedGroups, settings]);

  function updateSetting(key, value) {
    setSettings((current) => {
      const next = { ...current, [key]: value };
      localStorage.setItem(`antifraud:${profile.id}`, JSON.stringify(next));
      return next;
    });
  }

  const grouped = useMemo(() => {
    const result = {};
    for (const detector of snapshot?.detectors || []) {
      (result[detector.category] ||= []).push(detector);
    }
    return result;
  }, [snapshot]);
  const events = useMemo(() => {
    const seen = new Set();
    return (snapshot?.events || []).filter((event) => {
      if (selectedDetector !== "all" && event.detector !== selectedDetector) return false;
      if (!event.event_id) return true;
      if (seen.has(event.event_id)) return false;
      seen.add(event.event_id);
      return true;
    });
  }, [snapshot, selectedDetector]);
  const summary = snapshot?.summary || {};
  const scanAge = snapshot?.generated_at ? Math.max(0, Math.floor((clock - Date.parse(snapshot.generated_at)) / 1000)) : null;
  const toxicClients = useMemo(() => {
    const clients = new Map();
    for (const event of snapshot?.events || []) {
      if (!event.login) continue;
      const client = clients.get(event.login) || {
        login: event.login,
        name: event.name,
        group: event.group,
        events: [],
        score: 0,
      };
      client.events.push(event);
      client.score +=
        event.severity === "critical"
          ? 40
          : event.severity === "high"
            ? 25
            : 10;
      clients.set(event.login, client);
    }
    return [...clients.values()].sort((a, b) => b.score - a.score);
  }, [snapshot]);
  const groupRiskRanking = useMemo(() => {
    const groups = new Map();
    for (const event of snapshot?.events || []) {
      const group = event.group || "Unassigned";
      const row = groups.get(group) || {
        group,
        events: 0,
        critical: 0,
        high: 0,
        accounts: new Set(),
      };
      row.events += 1;
      if (event.severity === "critical") row.critical += 1;
      if (event.severity === "high") row.high += 1;
      if (event.login) row.accounts.add(event.login);
      groups.set(group, row);
    }
    return [...groups.values()]
      .map((row) => ({ ...row, accounts: row.accounts.size }))
      .sort(
        (left, right) =>
          right.critical * 1000 +
          right.high * 100 +
          right.events -
          (left.critical * 1000 + left.high * 100 + left.events),
      )
      .slice(0, 6);
  }, [snapshot]);
  const waitingForMt5 = loading && !snapshot;

  const openClient = (login) => {
    const client = toxicClients.find(
      (item) => Number(item.login) === Number(login),
    );
    if (client) setSelectedClient(client);
  };

  const copyClientReport = async (client) => {
    const lines = [
      "MT5 TOXIC CLIENT REVIEW",
      `Login: ${client.login}`,
      `Name: ${client.name || "Unknown"}`,
      `Group: ${client.group || "Unknown"}`,
      `Risk score: ${client.score}`,
      `Signals: ${client.events.length}`,
      "",
    ];
    client.events.forEach((event, index) =>
      lines.push(
        `${index + 1}. [${event.severity.toUpperCase()}] ${event.detector}: ${event.evidence}`,
      ),
    );
    await navigator.clipboard.writeText(lines.join("\n"));
    toast.success(`Report for #${client.login} copied`);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] px-4 py-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full ${loading ? "animate-pulse bg-amber-400" : "animate-pulse bg-success"}`}
          />
          {loading
            ? "Scanning live MT5 deals, positions and ticks…"
            : "Continuous anti-fraud monitoring"}
          {scanAge != null && <span className={scanAge > 15 ? "text-amber-500" : "text-success"}> · Last completed scan {scanAge}s ago{snapshot?.cache?.refreshing ? " · checking new activity" : ""}</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={`btn-secondary !p-2.5 ${showSettings ? "bg-[var(--inp-bg)]" : ""}`}
            onClick={() => setShowSettings((value) => !value)}
            title={
              showSettings
                ? "Close anti-fraud configuration"
                : "Configure anti-fraud analysis"
            }
            aria-label={
              showSettings
                ? "Close anti-fraud configuration"
                : "Configure anti-fraud analysis"
            }
            aria-expanded={showSettings}
          >
            <Settings2 size={14} />
          </button>
        </div>
      </div>

      {waitingForMt5 && (
        <div className="line-loader" aria-label="Loading anti-fraud data">
          <span />
        </div>
      )}

      {showSettings && (
        <AnimatedCard className="p-4">
          <div className="mb-4">
            <h2 className="text-sm font-semibold">Configure anti-fraud analysis</h2>
            <p className="mt-1 text-[11px] text-muted">
              Changes are used by the next continuous scan and stored only in
              this browser.
            </p>
          </div>
          {groupRiskRanking.length > 0 && (
            <section className="mb-5 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3 sm:p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-[.12em]">Group risk ranking</h3>
                  <p className="mt-1 text-[11px] text-muted">Selected groups ranked by critical, high, then total events.</p>
                </div>
                <span className="badge">Selected groups only</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {groupRiskRanking.map((row, index) => (
                  <div key={row.group} className="min-w-0 rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-3">
                    <div className="flex items-start justify-between gap-2">
                      <span className="min-w-0 break-all text-xs font-semibold leading-5">#{index + 1} {row.group}</span>
                      <span className="badge badge-danger shrink-0">{row.events}</span>
                    </div>
                    <p className="mt-2 text-[11px] text-muted">{row.accounts} accounts / {row.critical} critical / {row.high} high</p>
                  </div>
                ))}
              </div>
            </section>
          )}
          {(snapshot?.warnings || []).length > 0 && (
            <details className="mb-5 rounded-xl border border-amber-500/30 bg-amber-500/[.05] p-3 text-xs">
              <summary className="cursor-pointer font-semibold text-amber-600 dark:text-amber-300">Data coverage and scan notes ({snapshot.warnings.length})</summary>
              <ul className="mt-3 list-disc space-y-2 pl-5 leading-5 text-secondary">
                {snapshot.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
              </ul>
            </details>
          )}
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-[.12em]">Evidence thresholds</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            <NumberField label="Lookback, hours" value={settings.lookback_hours} onChange={(value) => updateSetting("lookback_hours", value)} min={1} />
            <NumberField label="Tick gap, seconds" value={settings.gap_seconds} onChange={(value) => updateSetting("gap_seconds", value)} min={0.1} step={0.1} />
            <NumberField label="Latency profit, USD" value={settings.profit_threshold} onChange={(value) => updateSetting("profit_threshold", value)} />
            <NumberField label="Maximum duration, minutes" value={settings.maximum_duration_minutes} onChange={(value) => updateSetting("maximum_duration_minutes", value)} min={0.1} step={0.1} />
            <NumberField label="News window, minutes" value={settings.news_window_minutes} onChange={(value) => updateSetting("news_window_minutes", value)} min={0.1} step={0.1} />
            <fieldset className="rounded-lg border border-[rgb(var(--color-border))] px-3 py-2 sm:col-span-2 xl:col-span-2">
              <legend className="px-1 text-xs font-semibold">News impact</legend>
              <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs">
                {["high", "medium", "low"].map((level) => {
                  const selected = settings.news_impacts?.length ? settings.news_impacts : DEFAULT_SETTINGS.news_impacts;
                  return <label key={level} className="flex items-center gap-1.5 capitalize">
                    <input type="checkbox" checked={selected.includes(level)} disabled={selected.length === 1 && selected.includes(level)}
                      onChange={(event) => updateSetting("news_impacts", event.target.checked
                        ? [...selected, level]
                        : selected.filter((item) => item !== level))} />
                    {level}
                  </label>;
                })}
              </div>
            </fieldset>
            <NumberField label="Scalp lifetime, seconds" value={settings.scalp_seconds} onChange={(value) => updateSetting("scalp_seconds", value)} min={1} />
            <NumberField label="Scalp sequence" value={settings.scalp_sequence_min} onChange={(value) => updateSetting("scalp_sequence_min", value)} min={1} />
            <NumberField label="Sharp deal profit, USD" value={settings.sharp_profit_threshold} onChange={(value) => updateSetting("sharp_profit_threshold", value)} />
            <NumberField label="Large order, lots" value={settings.large_order_lots} onChange={(value) => updateSetting("large_order_lots", value)} step={0.01} />
            <NumberField label="Large account, lots" value={settings.large_account_lots} onChange={(value) => updateSetting("large_account_lots", value)} step={0.01} />
            <NumberField label="Large order exposure / capital" value={settings.large_order_exposure_ratio} onChange={(value) => updateSetting("large_order_exposure_ratio", value)} step={0.1} />
            <NumberField label="Account exposure / capital" value={settings.large_account_exposure_ratio} onChange={(value) => updateSetting("large_account_exposure_ratio", value)} step={0.1} />
            <NumberField label="Concentration min. trades" value={settings.concentration_min_trades} onChange={(value) => updateSetting("concentration_min_trades", value)} min={2} />
            <NumberField label="Deposit alert amount (account currency)" value={settings.deposit_alert_threshold} onChange={(value) => updateSetting("deposit_alert_threshold", value)} min={0.01} step={0.01} />
            <NumberField label="Mini equity, USD" value={settings.mini_equity_threshold} onChange={(value) => updateSetting("mini_equity_threshold", value)} />
            <NumberField label="Critical margin level, %" value={settings.stopout_margin_level} onChange={(value) => updateSetting("stopout_margin_level", value)} />
            <label className="flex items-center gap-2 self-end pb-3 text-xs">
              <input type="checkbox" checked={settings.check_total_realized_pnl}
                onChange={(event) => updateSetting("check_total_realized_pnl", event.target.checked)} />
              Check total realized P&amp;L
            </label>
          </div>
          <div className="mt-4 rounded-lg border border-[rgb(var(--color-border))] p-3">
            <label className="block text-xs font-semibold">Economic calendar</label>
            <p className="mt-1 text-[11px] text-muted">News Trading loads scheduled releases from the calendar API automatically. The impact choices determine which releases count; detection uses UTC release times and matching trade currencies.</p>
          </div>
        </AnimatedCard>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
        {[
          ["Events", summary.events || 0, Activity, ""],
          ["Critical", summary.critical || 0, ShieldAlert, "text-danger"],
          ["Accounts", summary.accounts || 0, AlertTriangle, ""],
          [
            "Active Detectors",
            summary.active_detectors || 0,
            CheckCircle2,
            "text-success",
          ],
          ["Deals Scanned", summary.deals_scanned || 0, Activity, ""],
          ["Positions", summary.positions_scanned || 0, Activity, ""],
          [
            "Latency Checked",
            `${summary.latency_checked || 0}/${summary.latency_candidates || 0}`,
            Activity,
            "",
          ],
          [
            "Data Quality",
            summary.deals_scanned > 0
              ? summary.no_data_detectors > 0 || summary.data_required_detectors > 0 || (snapshot?.warnings || []).length > 0
                ? "Partial"
                : "Ready"
              : "No data",
            CheckCircle2,
            summary.deals_scanned > 0 && summary.no_data_detectors === 0 && summary.data_required_detectors === 0 && !(snapshot?.warnings || []).length ? "text-success" : "text-amber-400",
          ],
          [
            "Scan Duration",
            summary.scan_duration_ms
              ? `${(summary.scan_duration_ms / 1000).toFixed(1)}s`
              : "Cached",
            Activity,
            "",
          ],
        ].map(([label, value, Icon, tone]) => (
          <AnimatedCard className="min-w-0 p-3" key={label}>
            <button
              type="button"
              disabled={label !== "Accounts" || !toxicClients.length}
              onClick={() => label === "Accounts" && setShowToxicClients(true)}
              className="flex w-full items-center justify-between gap-3 text-left disabled:cursor-default"
            >
              <div>
                <p className="text-[9px] uppercase tracking-[.16em] text-muted">
                  {label}
                </p>
                <p className={`mt-1 text-lg font-semibold ${tone}`}>{value}</p>
              </div>
              {React.createElement(Icon, { size: 15, className: "text-muted" })}
            </button>
          </AnimatedCard>
        ))}
      </div>

      {!snapshot && (
        <AnimatedCard className="p-12 text-center text-muted">
          <ShieldAlert size={28} className="mx-auto" />
          <p className="mt-3 text-sm">
            Building the first evidence snapshot from MT5…
          </p>
        </AnimatedCard>
      )}

      {snapshot && (
        <div className="flex justify-end">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowToxicClients(true)}
          >
            <ShieldAlert size={14} />
            Toxic clients ({toxicClients.length})
          </button>
        </div>
      )}

      {Object.entries(grouped).filter(([category]) => category !== "Data required").map(([category, detectors]) => (
        <section key={category}>
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-[.12em]">
              {category}
            </h2>
            <span className="h-px flex-1 bg-[rgb(var(--color-border))]" />
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {detectors.map((detector, index) => (
              <DetectorCard
                key={detector.key}
                detector={detector}
                index={index}
                selected={selectedDetector === detector.key}
                onSelect={(key) =>
                  setSelectedDetector((current) =>
                    current === key ? "all" : key,
                  )
                }
              />
            ))}
          </div>
        </section>
      ))}

      {!!grouped["Data required"]?.length && (
        <details className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-3 sm:p-4">
          <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-xs font-semibold uppercase tracking-[.12em]">
            <span>Unavailable data sources</span>
            <span className="badge">{grouped["Data required"].length} checks need source access</span>
          </summary>
          <p className="mt-3 text-xs leading-5 text-muted">These checks stay unavailable until MT5 supplies their required logs or metadata. No result is inferred from missing data.</p>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {grouped["Data required"].map((detector, index) => (
              <DetectorCard key={detector.key} detector={detector} index={index}
                selected={selectedDetector === detector.key}
                onSelect={(key) => setSelectedDetector((current) => current === key ? "all" : key)} />
            ))}
          </div>
        </details>
      )}

      {snapshot && (
        <AnimatedCard className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-[.14em]">
                Evidence Report
              </h2>
              <p className="mt-1 text-[10px] text-muted">
                Every row links to a real MT5 deal, position, account or tick
                sequence.
              </p>
            </div>
            <span className="badge">{events.length} events</span>
          </div>
          <div className="max-h-[520px] overflow-auto scrollbar-hidden">
            <table className="w-full min-w-[1100px] text-xs">
              <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
                <tr>
                  {[
                    "Severity",
                    "Detector",
                    "Account",
                    "Group",
                    "Symbol",
                    "Time",
                    "Value",
                    "Evidence",
                  ].map((head) => (
                    <th
                      className="px-4 py-3 text-left font-medium text-muted"
                      key={head}
                    >
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {events.map((event, index) => (
                  <tr
                    className="border-t border-[rgb(var(--color-border))]"
                    key={`${event.detector}-${event.login}-${event.position_id || index}`}
                  >
                    <td className="px-4 py-3">
                      <span className={severityClass(event.severity)}>
                        {event.severity}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-medium">
                      {(snapshot.detectors || []).find(
                        (item) => item.key === event.detector,
                      )?.name || event.detector}
                      {event.news_flag && <span className="ml-2 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-600 dark:text-amber-300">{event.news_flag}</span>}
                    </td>
                    <td className="px-4 py-3">
                      {event.login > 0 ? (
                        <button
                          type="button"
                          onClick={() => openClient(event.login)}
                          className="text-left hover:text-orange-400"
                        >
                          <div>{event.name || `Login ${event.login}`}</div>
                          <div className="text-muted">#{event.login}</div>
                        </button>
                      ) : (
                        "Platform"
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted">
                      {event.group || "—"}
                    </td>
                    <td className="px-4 py-3">{event.symbol || "—"}</td>
                    <td className="px-4 py-3 text-muted">
                      {event.occurred_at
                        ? new Date(event.occurred_at).toLocaleString()
                        : "Live snapshot"}
                    </td>
                    <td className="px-4 py-3 font-medium">
                      {event.value ?? "—"}
                    </td>
                    <td className="max-w-lg px-4 py-3 text-secondary">
                      {event.evidence}
                    </td>
                  </tr>
                ))}
                {events.length === 0 && (
                  <tr>
                    <td colSpan={8} className="p-10 text-center text-muted">
                      No evidence events for this detector under the current
                      thresholds.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </AnimatedCard>
      )}
      {selectedClient && (
        <div
          className="fixed inset-0 z-[110] flex items-center justify-center bg-white/10 p-4 backdrop-blur-[2px] dark:bg-black/25"
          onMouseDown={(event) =>
            event.target === event.currentTarget && setSelectedClient(null)
          }
        >
          <div className="max-h-[85vh] w-full max-w-3xl overflow-hidden rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] shadow-2xl">
            <div className="flex items-start justify-between border-b border-[rgb(var(--color-border))] p-4">
              <div>
                <h2 className="font-semibold">
                  {selectedClient.name || `Login ${selectedClient.login}`}{" "}
                  <span className="text-muted">#{selectedClient.login}</span>
                </h2>
                <p className="mt-1 text-xs text-muted">
                  {selectedClient.group || "Unknown group"} · risk score{" "}
                  {selectedClient.score} · {selectedClient.events.length}{" "}
                  verified signals
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setSelectedClient(null)}
                aria-label="Close client details"
              >
                <X size={16} />
              </button>
            </div>
            <div className="max-h-[65vh] space-y-2 overflow-y-auto p-4 scrollbar-hidden">
              {selectedClient.events.map((event, index) => (
                <div
                  key={`${event.detector}-${event.position_id || index}`}
                  className="rounded-lg border border-[rgb(var(--color-border))] p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <span className={severityClass(event.severity)}>
                        {event.severity}
                      </span>
                      <span className="ml-2 text-xs font-semibold">
                        {(snapshot.detectors || []).find(
                          (item) => item.key === event.detector,
                        )?.name || event.detector}
                      {event.news_flag && <span className="ml-2 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-600 dark:text-amber-300">{event.news_flag}</span>}
                      </span>
                    </div>
                    <span className="text-[10px] text-muted">
                      {event.occurred_at
                        ? new Date(event.occurred_at).toLocaleString()
                        : "Live snapshot"}
                    </span>
                  </div>
                  <div className="mt-2 grid gap-2 text-[11px] sm:grid-cols-3">
                    <span>
                      Symbol: <b>{event.symbol || "N/A"}</b>
                    </span>
                    <span>
                      Value: <b>{event.value ?? "N/A"}</b>
                    </span>
                    <span>
                      Position: <b>{event.position_id || "N/A"}</b>
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-secondary">
                    {event.evidence}
                  </p>
                </div>
              ))}
            </div>
            <div className="flex justify-end border-t border-[rgb(var(--color-border))] p-3">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => copyClientReport(selectedClient)}
              >
                <Copy size={13} />
                Copy report
              </button>
            </div>
          </div>
        </div>
      )}
      {showToxicClients && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-white/10 p-4 backdrop-blur-[2px] dark:bg-black/25"
          onMouseDown={(event) =>
            event.target === event.currentTarget && setShowToxicClients(false)
          }
        >
          <div className="max-h-[85vh] w-full max-w-5xl overflow-hidden rounded-2xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] shadow-2xl">
            <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
              <div>
                <h2 className="font-semibold">Toxic Client Review</h2>
                <p className="mt-1 text-xs text-muted">
                  Evidence watchlist with copyable investigation reports.
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setShowToxicClients(false)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="max-h-[70vh] space-y-3 overflow-auto p-4">
              {toxicClients.map((client) => (
                <div
                  key={client.login}
                  className="rounded-xl border border-[rgb(var(--color-border))] p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold">
                        {client.name || `Login ${client.login}`}{" "}
                        <span className="text-muted">#{client.login}</span>
                      </div>
                      <div className="mt-1 text-xs text-muted">
                        {client.group} · score {client.score} ·{" "}
                        {client.events.length} signals
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => copyClientReport(client)}
                    >
                      <Copy size={13} />
                      Copy report
                    </button>
                  </div>
                  <div className="mt-3 space-y-2">
                    {client.events.slice(0, 12).map((event, index) => (
                      <div
                        key={`${event.detector}-${index}`}
                        className="rounded-lg bg-[var(--inp-bg)] p-2 text-xs"
                      >
                        <span className={severityClass(event.severity)}>
                          {event.severity}
                        </span>
                        <span className="ml-2 font-medium">
                          {event.detector}
                        </span>
                        <span className="ml-2 text-muted">
                          {event.evidence}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {!toxicClients.length && (
                <p className="p-8 text-center text-muted">
                  No toxic-client signals under the current thresholds.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
