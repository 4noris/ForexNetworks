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
  Download,
  Search,
  ServerCog,
  ShieldAlert,
  TimerReset,
  TrendingDown,
  WifiOff,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import SafeResponsiveContainer from "../components/SafeResponsiveContainer";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import { apiRequest, subscribeLiveData } from "../lib/api";
import MarkoutPanel from "../components/MarkoutPanel";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function isoDay(value) {
  return value.toISOString().slice(0, 10);
}

function moneyText(value) {
  return value == null ? "Unavailable" : money.format(Number(value));
}

function durationText(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${(value / 60).toFixed(1)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function Metric({ label, value, tone = "", icon: Icon = Activity }) {
  return (
    <AnimatedCard className="p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.18em] text-muted">
            {label}
          </p>
          <p className={`mt-2 text-xl font-semibold tabular-nums ${tone}`}>
            {value}
          </p>
        </div>
        {React.createElement(Icon, { size: 16, className: "text-muted" })}
      </div>
    </AnimatedCard>
  );
}

function csvValue(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function TradeNetworkCell({ row }) {
  const detected = row.trade_vps_status === "detected";
  const inferred = row.trade_ip_evidence === "login_ip_fallback";
  return (
    <td className="min-w-[280px] px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{row.trade_ip || "Unavailable"}</span>
        <span
          className={
            inferred
              ? "badge badge-amber"
              : row.trade_ip
                ? "badge badge-success"
                : "badge"
          }
        >
          {inferred
            ? "Login IP fallback"
            : row.trade_ip
              ? "Journal proven"
              : "Unavailable"}
        </span>
        {row.trade_ip_changed && (
          <span className="badge badge-amber">IP changed</span>
        )}
        <span
          className={
            detected
              ? "badge badge-danger"
              : row.trade_vps_status === "not_detected"
                ? "badge badge-success"
                : "badge"
          }
        >
          {detected
            ? `VPS ${row.trade_vps_confidence}%`
            : String(row.trade_vps_status || "unknown").replace("_", " ")}
        </span>
      </div>
      <div className="mt-1 text-[10px] text-muted">
        {row.trade_location || "Location unavailable"} ·{" "}
        {row.trade_provider || "Provider unavailable"} {row.trade_asn || ""}
      </div>
      {(row.trade_ips || []).length > 1 && (
        <div className="mt-1 text-[10px] text-amber-400">
          All proven IPs: {row.trade_ips.join(", ")}
        </div>
      )}
      <div className="mt-1 text-[10px] text-muted">
        Source:{" "}
        {inferred
          ? "MT5 account LastIP; not ticket-linked execution evidence"
          : (row.trade_ip_sources || []).join(", ") ||
            "No ticket-linked journal evidence"}
      </div>
      <div className="mt-1 text-[10px] text-muted">
        {(row.trade_vps_evidence || []).join(" · ")}
      </div>
    </td>
  );
}

function TradeIpDiagnostics({ network = {} }) {
  const healthy = network.diagnostic_status === "healthy";
  const partial = network.diagnostic_status === "partial";
  const Icon = healthy ? CheckCircle2 : partial ? AlertTriangle : WifiOff;
  const tone = healthy
    ? "text-success"
    : partial
      ? "text-amber-400"
      : "text-danger";
  return (
    <AnimatedCard className="overflow-hidden">
      <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex gap-3">
          <Icon size={20} className={`mt-0.5 shrink-0 ${tone}`} />
          <div>
            <h2 className="text-sm font-semibold">
              Trade IP attribution health
            </h2>
            <p className="mt-1 text-xs text-muted">
              {network.covered_trades || 0} journal-proven ·{" "}
              {network.inferred_trades || 0} login-IP fallback ·{" "}
              {network.total_trades || 0} total trades
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="badge badge-success">
                Proven {network.coverage_pct || 0}%
              </span>
              <span className="badge badge-amber">
                Attributed {network.attribution_pct || 0}%
              </span>
              <span className="badge">
                Remote records {network.remote_journal_records || 0}
              </span>
              <span className="badge">
                Local records {network.local_journal_records || 0}
              </span>
              <span className="badge">
                Files scanned {network.local_log_files_scanned || 0}
              </span>
              <span className="badge">
                Unique proven IPs {network.unique_ips || 0}
              </span>
              <span className="badge">
                VPS checked {network.vps_checked_ips || 0}/{network.vps_total_ips || 0} IPs
              </span>
            </div>
          </div>
        </div>
        {!healthy && (
          <div className="max-w-xl border-l border-[rgb(var(--color-border))] pl-4">
            <div className="flex items-center gap-2 text-xs font-medium">
              <ServerCog size={14} />
              For proven execution IPs
            </div>
            <ol className="mt-2 space-y-1 text-[11px] text-muted">
              {(network.actions || []).map((action, index) => (
                <li key={action}>
                  {index + 1}. {action}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </AnimatedCard>
  );
}

export default function ClientAnalyser({
  profile,
  selectedGroups,
  candidates = [],
  active = true,
  initialLogin = "",
}) {
  const today = new Date();
  const weekAgo = new Date(today);
  weekAgo.setDate(today.getDate() - 7);
  const [login, setLogin] = useState(initialLogin);
  const [fromDate, setFromDate] = useState(isoDay(weekAgo));
  const [toDate, setToDate] = useState(isoDay(today));
  const [scalpSeconds, setScalpSeconds] = useState(180);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingSeconds, setLoadingSeconds] = useState(0);
  const [decision, setDecision] = useState("Watch");
  const [notes, setNotes] = useState("");
  const [liveSnapshot, setLiveSnapshot] = useState(null);
  const [liveMovement, setLiveMovement] = useState([]);
  const liveRequest = useRef(false);
  const liveController = useRef(null);

  useEffect(() => {
    setLogin(initialLogin);
    setResult(null);
    setDecision("Watch");
    setNotes("");
    setLiveSnapshot(null);
    setLiveMovement([]);
  }, [profile.id, initialLogin]);

  useEffect(() => {
    if (!loading) return undefined;
    setLoadingSeconds(0);
    const timer = window.setInterval(
      () => setLoadingSeconds((value) => value + 1),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [loading]);

  const loadLive = useCallback(async () => {
    if (!result?.client?.login || !selectedGroups.length || liveRequest.current)
      return false;
    liveRequest.current = true;
    const controller = new AbortController();
    liveController.current = controller;
    try {
      const data = await apiRequest("/api/risk/client-live", {
        method: "POST",
        signal: controller.signal,
        timeoutMs: 30_000,
        body: JSON.stringify({
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          selected_groups: selectedGroups,
          excluded_logins: [],
          group_filter_rules: "",
          client_login: result.client.login,
        }),
      });
      if (controller.signal.aborted) return false;
      setLiveSnapshot(data);
      setLiveMovement((rows) =>
        [
          ...rows,
          {
            time: new Date(data.generated_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }),
            pnl: Number(data.summary?.floating_pnl || 0),
            equity: Number(data.summary?.equity || 0),
          },
        ].slice(-60),
      );
      return true;
    } catch (error) {
      if (!controller.signal.aborted) toast.error(error.message || "Live client exposure failed");
      return false;
    } finally {
      if (liveController.current === controller) { liveRequest.current = false; liveController.current = null; }
    }
  }, [profile, selectedGroups, result]);

  useEffect(() => {
    if (!active || !result?.client?.login) return undefined;
    let cancelled = false;
    const stopLive = subscribeLiveData("/api/risk/client-live", {
      selected_groups: selectedGroups, excluded_logins: [], group_filter_rules: "", client_login: result.client.login,
    }, () => { if (!cancelled && !document.hidden) loadLive(); });
    const run = async () => {
      while (!cancelled) {
        await loadLive();
        if (!cancelled)
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
    };
    setLiveSnapshot(null);
    setLiveMovement([]);
    run();
    return () => {
      cancelled = true;
      stopLive();
      liveController.current?.abort();
      liveController.current = null;
      liveRequest.current = false;
    };
  }, [active, result?.client?.login, loadLive, selectedGroups]);

  const candidateOptions = useMemo(
    () =>
      candidates.map((row) => ({
        label: `${row.login} — ${row.name || "Unnamed client"}`,
        login: String(row.login),
      })),
    [candidates],
  );

  async function analyse(event) {
    event.preventDefault();
    const parsedLogin = Number(String(login).split(/\s|—/)[0]);
    if (!Number.isInteger(parsedLogin) || parsedLogin <= 0)
      return toast.error("Enter a valid client login");
    if (!selectedGroups.length) return toast.error("Select at least one group");
    setLoading(true);
    try {
      const data = await apiRequest("/api/risk/analyse", {
        method: "POST",
        body: JSON.stringify({
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          selected_groups: selectedGroups,
          excluded_logins: [],
          group_filter_rules: "",
          client_login: parsedLogin,
          from_date: fromDate,
          to_date: toDate,
          scalping_seconds: Number(scalpSeconds),
        }),
      });
      setResult(data);
      const stored = JSON.parse(
        localStorage.getItem(`clientAnalysis:${profile.id}:${parsedLogin}`) ||
          "{}",
      );
      setDecision(stored.decision || "Watch");
      setNotes(stored.notes || "");
    } catch (error) {
      toast.error(error.message || "Client analysis failed");
    } finally {
      setLoading(false);
    }
  }

  async function saveReview() {
    if (!result?.client?.login) return;
    try {
      await apiRequest("/api/operations/reviews", {
        method: "POST",
        body: JSON.stringify({
          client_login: result.client.login,
          decision,
          notes,
          actor:
            JSON.parse(localStorage.getItem("user") || "{}").username ||
            "system",
        }),
      });
      localStorage.setItem(
        `clientAnalysis:${profile.id}:${result.client.login}`,
        JSON.stringify({ decision, notes, saved_at: new Date().toISOString() }),
      );
      toast.success("Dealer review saved and audited");
    } catch (error) {
      toast.error(error.message || "Unable to save dealer review");
    }
  }

  function exportCsv() {
    const rows = result?.trades || [];
    if (!rows.length) return toast.error("No trades to export");
    const headings = [
      "Position ID",
      "Symbol",
      "Side",
      "Open Time UTC",
      "Close Time UTC",
      "Hold Seconds",
      "Volume",
      "Net P&L",
      "Trade IP",
      "IP Evidence Type",
      "All Trade IPs",
      "IP Changed",
      "IP Evidence Sources",
      "Trade Location",
      "Trade Provider",
      "Trade ASN",
      "Trade VPS",
      "VPS Confidence",
      "VPS Evidence",
      "Flags",
    ];
    const body = rows.map((row) =>
      [
        row.position_id,
        row.symbol,
        row.side,
        row.open_time,
        row.close_time,
        row.hold_seconds,
        row.volume,
        row.pnl,
        row.trade_ip,
        row.trade_ip_evidence,
        (row.trade_ips || []).join(" | "),
        row.trade_ip_changed,
        (row.trade_ip_sources || []).join(" | "),
        row.trade_location,
        row.trade_provider,
        row.trade_asn,
        row.trade_vps_status,
        row.trade_vps_confidence,
        (row.trade_vps_evidence || []).join(" | "),
        row.flags.join(" | "),
      ]
        .map(csvValue)
        .join(","),
    );
    const blob = new Blob(
      [[headings.map(csvValue).join(","), ...body].join("\r\n")],
      { type: "text/csv;charset=utf-8" },
    );
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `client_${result.client.login}_analysis_${fromDate}_${toDate}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function copyCompactReport() {
    if (!result) return;
    const patternLines = Object.entries(result.patterns || {}).map(
      ([name, value]) => `${name}: ${value.pct}% (${value.count})`,
    );
    const text = [
      `MT5 CLIENT RISK REPORT · #${result.client.login}`,
      `${result.client.name || "Unnamed"} · ${result.client.group}`,
      `Toxicity: ${result.metrics.toxicity_score}% · P&L: ${moneyText(result.metrics.total_pnl)} · Trades: ${result.metrics.total_trades}`,
      `Win rate: ${result.metrics.win_rate}% · Avg hold: ${durationText(result.metrics.average_hold_seconds)} · Max drawdown: ${moneyText(result.metrics.max_drawdown)}`,
      `Patterns: ${patternLines.join(" · ") || "none"}`,
      `VPS IPs: ${result.execution_network?.vps_ips || 0} · Trade-IP coverage: ${result.execution_network?.coverage_pct || 0}%`,
      `Dealer status: ${decision}${notes ? ` · ${notes}` : ""}`,
    ].join("\n");
    await navigator.clipboard.writeText(text);
    toast.success("Compact client report copied");
  }

  const metrics = result?.metrics || {};
  const patterns = result?.patterns || {};

  return (
    <div className="space-y-4">
      <AnimatedCard className="p-4">
        <form
          onSubmit={analyse}
          className="grid gap-3 lg:grid-cols-[minmax(220px,1.2fr)_160px_160px_160px_auto] lg:items-end"
        >
          <label className="text-xs text-muted">
            Client login or active client
            <input
              list="risk-client-options"
              value={login}
              onChange={(event) => setLogin(event.target.value)}
              placeholder="e.g. 102001"
              className="input mt-2 w-full"
            />
            <datalist id="risk-client-options">
              {candidateOptions.map((row) => (
                <option value={row.login} key={row.login}>
                  {row.label}
                </option>
              ))}
            </datalist>
          </label>
          <label className="text-xs text-muted">
            From
            <input
              type="date"
              value={fromDate}
              onChange={(event) => setFromDate(event.target.value)}
              className="input mt-2 w-full"
            />
          </label>
          <label className="text-xs text-muted">
            To
            <input
              type="date"
              value={toDate}
              onChange={(event) => setToDate(event.target.value)}
              className="input mt-2 w-full"
            />
          </label>
          <label className="text-xs text-muted">
            Scalp threshold
            <input
              type="number"
              min="1"
              max="3600"
              value={scalpSeconds}
              onChange={(event) => setScalpSeconds(event.target.value)}
              className="input mt-2 w-full"
            />
          </label>
          <button className="btn-primary h-10 px-5" disabled={loading}>
            <Search size={15} />
            {loading ? `Analysing ${loadingSeconds}s` : "Analyse client"}
          </button>
        </form>
        <p className="mt-3 text-[11px] text-muted">
          Threshold is measured in seconds internally. Times and pattern windows
          use MT5 server deal timestamps.
        </p>
      </AnimatedCard>

      {loading && (
        <AnimatedCard className="overflow-hidden p-4">
          <div
            className="line-loader"
            aria-label="Loading verified MT5 client data"
          >
            <span />
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-medium">
              {loadingSeconds < 3
                ? "Requesting account and deal records"
                : loadingSeconds < 8
                  ? "Reconstructing closed trade lifecycles"
                  : loadingSeconds < 15
                    ? "Checking ticket-linked journal evidence"
                    : "Waiting for the verified MT5 response"}
            </span>
            <span className="text-muted">
              No estimated or placeholder values are shown
            </span>
          </div>
        </AnimatedCard>
      )}

      {!result && (
        <AnimatedCard className="p-10 text-center">
          <ShieldAlert size={28} className="mx-auto text-muted" />
          <h2 className="mt-3 font-semibold">Client Pattern Analyser</h2>
          <p className="mt-2 text-sm text-muted">
            Search a login to reconstruct closed trades and investigate
            execution behavior.
          </p>
        </AnimatedCard>
      )}

      {result && (
        <>
          <AnimatedCard className="flex flex-col gap-3 p-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-lg font-semibold">
                {result.client.name || "Unnamed client"}{" "}
                <span className="text-muted">#{result.client.login}</span>
              </div>
              <div className="mt-1 text-xs text-muted">
                {result.client.group} · IP{" "}
                {result.client.last_ip || "unavailable"} ·{" "}
                {result.client.location || "Location unavailable"}
              </div>
              <div className="mt-1 text-[11px] text-muted">
                {result.client.provider || "Provider unavailable"}{" "}
                {result.client.asn || ""} · VPS{" "}
                {String(result.client.vps_status || "unknown").replace(
                  "_",
                  " ",
                )}{" "}
                {result.client.vps_confidence
                  ? `${result.client.vps_confidence}%`
                  : ""}{" "}
                · {(result.client.vps_evidence || []).join(" · ")}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`badge ${metrics.toxicity_score >= 60 ? "badge-danger" : metrics.toxicity_score >= 30 ? "badge-amber" : "badge-success"}`}
              >
                Toxicity {metrics.toxicity_score}%
              </span>
              <span className="badge">
                Trade IP {result.execution_network?.coverage_pct || 0}%
              </span>
              <span className="badge badge-danger">
                VPS {result.execution_network?.vps_checked_ips
                  ? `${result.execution_network.vps_ips || 0} detected / ${result.execution_network.vps_checked_ips} checked`
                  : "lookup unavailable"}
              </span>
              <button
                type="button"
                className="btn-secondary"
                onClick={copyCompactReport}
                title="Copy compact scalping, burst and toxicity report"
              >
                <Copy size={14} />
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={exportCsv}
              >
                <Download size={14} />
                Export CSV
              </button>
            </div>
          </AnimatedCard>

          {(result.warnings || []).length > 0 && (
            <AnimatedCard className="p-3 text-xs text-amber-400">
              {result.warnings.join(" · ")}
            </AnimatedCard>
          )}

          <MarkoutPanel active={active} key={result.client.login} login={result.client.login} selectedGroups={selectedGroups} initialDate={toDate} />

          <TradeIpDiagnostics network={result.execution_network} />

          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-[.16em]">
                Live Account Summary & Exposure
              </h2>
              <p className="mt-1 text-[10px] text-muted">
                Continuously read from MT5 account, position, symbol and
                current-price records.
              </p>
            </div>
            <span className="flex items-center gap-2 text-[10px] text-success">
              <span className="h-2 w-2 animate-pulse rounded-full bg-success" />
              {liveSnapshot
                ? new Date(liveSnapshot.generated_at).toLocaleTimeString()
                : "Connecting"}
            </span>
          </div>
          {liveSnapshot && (
            <>
              {(liveSnapshot.warnings || []).length > 0 && (
                <AnimatedCard className="p-3 text-xs text-amber-400">
                  {liveSnapshot.warnings.join(" · ")}
                </AnimatedCard>
              )}
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
                <Metric
                  label="Live Equity"
                  value={moneyText(liveSnapshot.summary.equity)}
                />
                <Metric
                  label="Floating P&L"
                  value={moneyText(liveSnapshot.summary.floating_pnl)}
                  tone={
                    liveSnapshot.summary.floating_pnl < 0
                      ? "text-danger"
                      : "text-success"
                  }
                />
                <Metric
                  label="Margin Level"
                  value={`${number.format(liveSnapshot.summary.margin_level)}%`}
                />
                <Metric
                  label="Open Positions"
                  value={number.format(liveSnapshot.summary.open_positions)}
                />
                <Metric
                  label="Long Lots"
                  value={number.format(liveSnapshot.summary.long_lots)}
                />
                <Metric
                  label="Short Lots"
                  value={number.format(liveSnapshot.summary.short_lots)}
                />
                <Metric
                  label="Net Lots"
                  value={number.format(liveSnapshot.summary.net_lots)}
                  tone={
                    liveSnapshot.summary.net_lots < 0
                      ? "text-danger"
                      : "text-success"
                  }
                />
                <Metric
                  label="Gross Exposure"
                  value={moneyText(liveSnapshot.summary.gross_exposure)}
                />
              </div>
              <div className="grid gap-4 xl:grid-cols-2">
                <AnimatedCard className="p-5">
                  <h3 className="mb-4 text-xs font-semibold uppercase tracking-[.14em]">
                    Live Floating P&L Movement
                  </h3>
                  <div className="h-64">
                    <SafeResponsiveContainer>
                      <AreaChart data={liveMovement}>
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke="rgb(var(--color-border))"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="time"
                          tick={{
                            fontSize: 9,
                            fill: "rgb(var(--color-text-3))",
                          }}
                        />
                        <YAxis
                          tick={{
                            fontSize: 9,
                            fill: "rgb(var(--color-text-3))",
                          }}
                        />
                        <Tooltip formatter={(value) => moneyText(value)} />
                        <Area
                          type="monotone"
                          dataKey="pnl"
                          stroke="#ef4444"
                          fill="#ef444430"
                          strokeWidth={2}
                        />
                      </AreaChart>
                    </SafeResponsiveContainer>
                  </div>
                </AnimatedCard>
                <AnimatedCard className="overflow-hidden">
                  <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
                    <h3 className="text-xs font-semibold uppercase tracking-[.14em]">
                      Live Symbol Long / Short Book
                    </h3>
                    <span className="badge">
                      Concentration {liveSnapshot.summary.concentration_pct}%
                    </span>
                  </div>
                  <div className="max-h-64 overflow-auto">
                    <table className="w-full min-w-[780px] text-xs">
                      <thead>
                        <tr>
                          {[
                            "Symbol",
                            "Long Lots",
                            "Short Lots",
                            "Net Lots",
                            "Long Exposure",
                            "Short Exposure",
                            "Net Exposure",
                            "Floating P&L",
                          ].map((head) => (
                            <th
                              className="px-3 py-3 text-left font-medium text-muted"
                              key={head}
                            >
                              {head}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {liveSnapshot.symbols.map((row) => (
                          <tr
                            className="border-t border-[rgb(var(--color-border))]"
                            key={row.symbol}
                          >
                            <td className="px-3 py-3 font-medium">
                              {row.symbol}
                            </td>
                            <td className="px-3 py-3 text-success">
                              {row.buy_lots}
                            </td>
                            <td className="px-3 py-3 text-danger">
                              {row.sell_lots}
                            </td>
                            <td className="px-3 py-3">{row.net_lots}</td>
                            <td className="px-3 py-3">
                              {moneyText(row.long_exposure)}
                            </td>
                            <td className="px-3 py-3">
                              {moneyText(row.short_exposure)}
                            </td>
                            <td
                              className={
                                row.net_exposure < 0
                                  ? "px-3 py-3 text-danger"
                                  : "px-3 py-3 text-success"
                              }
                            >
                              {moneyText(row.net_exposure)}
                            </td>
                            <td
                              className={
                                row.floating_pnl < 0
                                  ? "px-3 py-3 text-danger"
                                  : "px-3 py-3 text-success"
                              }
                            >
                              {moneyText(row.floating_pnl)}
                            </td>
                          </tr>
                        ))}
                        {!liveSnapshot.symbols.length && (
                          <tr>
                            <td
                              colSpan={8}
                              className="p-8 text-center text-muted"
                            >
                              No open exposure for this client.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </AnimatedCard>
              </div>
              <AnimatedCard className="overflow-hidden">
                <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
                  <h3 className="text-xs font-semibold uppercase tracking-[.14em]">
                    Live Open Positions
                  </h3>
                  <span className="badge">
                    Long {liveSnapshot.summary.long_positions} · Short{" "}
                    {liveSnapshot.summary.short_positions}
                  </span>
                </div>
                <div className="max-h-72 overflow-auto">
                  <table className="w-full min-w-[900px] text-xs">
                    <thead>
                      <tr>
                        {[
                          "Position",
                          "Symbol",
                          "Side",
                          "Lots",
                          "Open",
                          "Current",
                          "Notional Exposure",
                          "Floating P&L",
                          "Opened",
                        ].map((head) => (
                          <th
                            className="px-3 py-3 text-left font-medium text-muted"
                            key={head}
                          >
                            {head}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {liveSnapshot.positions.map((row) => (
                        <tr
                          className="border-t border-[rgb(var(--color-border))]"
                          key={row.position_id}
                        >
                          <td className="px-3 py-3">#{row.position_id}</td>
                          <td className="px-3 py-3 font-medium">
                            {row.symbol}
                          </td>
                          <td
                            className={
                              row.side === "BUY"
                                ? "px-3 py-3 text-success"
                                : "px-3 py-3 text-danger"
                            }
                          >
                            {row.side}
                          </td>
                          <td className="px-3 py-3">{row.lots}</td>
                          <td className="px-3 py-3">{row.price_open}</td>
                          <td className="px-3 py-3">{row.price_current}</td>
                          <td className="px-3 py-3">
                            {moneyText(row.exposure)}
                          </td>
                          <td
                            className={
                              row.floating_pnl < 0
                                ? "px-3 py-3 text-danger"
                                : "px-3 py-3 text-success"
                            }
                          >
                            {moneyText(row.floating_pnl)}
                          </td>
                          <td className="px-3 py-3 text-muted">
                            {row.opened_at
                              ? new Date(row.opened_at).toLocaleString()
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </AnimatedCard>
            </>
          )}

          <div>
            <h2 className="text-sm font-semibold uppercase tracking-[.16em]">
              Historical Pattern Analysis
            </h2>
            <p className="mt-1 text-[10px] text-muted">
              Closed-position evidence for the selected date range.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <Metric
              label="Net P&L"
              value={moneyText(metrics.total_pnl)}
              tone={metrics.total_pnl < 0 ? "text-danger" : "text-success"}
            />
            <Metric
              label="Trades"
              value={number.format(metrics.total_trades)}
            />
            <Metric
              label="Volume"
              value={`${number.format(metrics.total_volume)} lots`}
            />
            <Metric
              label="Win Rate"
              value={`${number.format(metrics.win_rate)}%`}
            />
            <Metric
              label="Profit Factor"
              value={number.format(metrics.profit_factor)}
            />
            <Metric
              label="Max Drawdown"
              value={moneyText(metrics.max_drawdown)}
              tone="text-danger"
              icon={TrendingDown}
            />
            <Metric
              label="Avg Hold"
              value={durationText(metrics.average_hold_seconds)}
              icon={TimerReset}
            />
            <Metric
              label="Loss Streak"
              value={number.format(metrics.worst_loss_streak)}
              icon={AlertTriangle}
            />
          </div>

          <AnimatedCard className="p-4">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-[0.14em]">
              Detected patterns
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {Object.entries(patterns).map(([name, value]) => (
                <div
                  className="rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3"
                  key={name}
                >
                  <div className="text-xs capitalize text-muted">{name}</div>
                  <div className="mt-1 text-lg font-semibold">{value.pct}%</div>
                  <div className="text-[10px] text-muted">
                    {value.count} flagged trades
                  </div>
                </div>
              ))}
            </div>
          </AnimatedCard>

          <div className="grid gap-4 xl:grid-cols-2">
            <AnimatedCard className="p-5">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.14em]">
                Cumulative P&L & Drawdown
              </h2>
              <div className="h-72">
                <SafeResponsiveContainer>
                  <AreaChart data={result.equity_curve}>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgb(var(--color-border))"
                      vertical={false}
                    />
                    <XAxis dataKey="time" hide />
                    <YAxis
                      tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }}
                    />
                    <Tooltip formatter={(value) => moneyText(value)} />
                    <Area
                      type="monotone"
                      dataKey="pnl"
                      stroke="#22c55e"
                      fill="#22c55e33"
                    />
                    <Area
                      type="monotone"
                      dataKey="drawdown"
                      stroke="#ef4444"
                      fill="#ef444422"
                    />
                  </AreaChart>
                </SafeResponsiveContainer>
              </div>
            </AnimatedCard>
            <AnimatedCard className="p-5">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.14em]">
                Trading Activity by Hour (UTC)
              </h2>
              <div className="h-72">
                <SafeResponsiveContainer>
                  <BarChart data={result.hourly}>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgb(var(--color-border))"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="hour"
                      interval={2}
                      tick={{ fontSize: 9, fill: "rgb(var(--color-text-3))" }}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }}
                    />
                    <Tooltip />
                    <Bar
                      dataKey="trades"
                      fill="#f59e0b"
                      radius={[3, 3, 0, 0]}
                    />
                  </BarChart>
                </SafeResponsiveContainer>
              </div>
            </AnimatedCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.2fr_.8fr]">
            <AnimatedCard className="overflow-hidden">
              <div className="border-b border-[rgb(var(--color-border))] p-4">
                <h2 className="text-sm font-semibold uppercase tracking-[0.14em]">
                  Symbol Deep Dive
                </h2>
              </div>
              <div className="max-h-80 overflow-auto scrollbar-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted">
                      {["Symbol", "Trades", "Volume", "P&L", "Scalps"].map(
                        (head) => (
                          <th
                            className="px-4 py-3 text-left font-medium"
                            key={head}
                          >
                            {head}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {result.symbols.map((row) => (
                      <tr
                        className="border-t border-[rgb(var(--color-border))]"
                        key={row.symbol}
                      >
                        <td className="px-4 py-3 font-medium">{row.symbol}</td>
                        <td className="px-4 py-3">{row.trades}</td>
                        <td className="px-4 py-3">{row.volume}</td>
                        <td
                          className={`px-4 py-3 ${row.pnl < 0 ? "text-danger" : "text-success"}`}
                        >
                          {moneyText(row.pnl)}
                        </td>
                        <td className="px-4 py-3">{row.scalp_pct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </AnimatedCard>
            <AnimatedCard className="p-4">
              <h2 className="text-sm font-semibold uppercase tracking-[0.14em]">
                Dealer Review
              </h2>
              <select
                value={decision}
                onChange={(event) => setDecision(event.target.value)}
                className="input mt-4 w-full"
              >
                <option>Watch</option>
                <option>Clear</option>
                <option>Investigate</option>
                <option>A-Book candidate</option>
                <option>B-Book candidate</option>
              </select>
              <textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={6}
                placeholder="Evidence, decision and follow-up…"
                className="input mt-3 w-full resize-none"
              />
              <button
                type="button"
                className="btn-primary mt-3"
                onClick={saveReview}
              >
                Save review
              </button>
              <p className="mt-3 text-[10px] text-muted">
                Review notes are stored in the investigation case history and
                audit log. Pattern signals require human verification.
              </p>
            </AnimatedCard>
          </div>

          <AnimatedCard className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-[0.14em]">
                  Trade Lifecycle Datagrid
                </h2>
                <p className="mt-1 text-[10px] text-muted">
                  Journal IPs are ticket-linked; missing evidence uses MT5
                  account LastIP and is marked as inferred.
                </p>
              </div>
              <span className="text-xs text-muted">
                {result.trades.length} rows
              </span>
            </div>
            <div className="max-h-[470px] overflow-auto scrollbar-hidden">
              <table className="w-full min-w-[1450px] text-xs">
                <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
                  <tr className="text-muted">
                    {[
                      "Position",
                      "Symbol",
                      "Side",
                      "Opened UTC",
                      "Closed UTC",
                      "Hold",
                      "Lots",
                      "Net P&L",
                      "Trade IP / VPS",
                      "Pattern flags",
                    ].map((head) => (
                      <th
                        className="px-4 py-3 text-left font-medium"
                        key={head}
                      >
                        {head}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((row) => (
                    <tr
                      className="border-t border-[rgb(var(--color-border))]"
                      key={row.position_id}
                    >
                      <td className="px-4 py-3">#{row.position_id}</td>
                      <td className="px-4 py-3 font-medium">{row.symbol}</td>
                      <td className="px-4 py-3">{row.side}</td>
                      <td className="px-4 py-3">
                        {new Date(row.open_time).toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        {new Date(row.close_time).toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        {durationText(row.hold_seconds)}
                      </td>
                      <td className="px-4 py-3">{row.volume}</td>
                      <td
                        className={`px-4 py-3 font-medium ${row.pnl < 0 ? "text-danger" : "text-success"}`}
                      >
                        {moneyText(row.pnl)}
                      </td>
                      <TradeNetworkCell row={row} />
                      <td className="px-4 py-3 text-muted">
                        {row.flags.join(" · ") || "—"}
                      </td>
                    </tr>
                  ))}
                  {result.trades.length === 0 && (
                    <tr>
                      <td colSpan={10} className="p-8 text-center text-muted">
                        No completed positions found in this date range.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </AnimatedCard>
        </>
      )}
    </div>
  );
}
