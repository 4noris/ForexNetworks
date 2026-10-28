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
  DollarSign,
  Gauge,
  ShieldAlert,
  SlidersHorizontal,
} from "lucide-react";
import {
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
import { apiRequest } from "../lib/api";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

function moneyText(value) {
  return money.format(Number(value || 0));
}

function Metric({ label, value, tone = "", icon: Icon = Activity }) {
  return (
    <AnimatedCard className="p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[9px] uppercase tracking-[.17em] text-muted">
            {label}
          </p>
          <p className={`mt-2 text-xl font-semibold ${tone}`}>{value}</p>
        </div>
        {React.createElement(Icon, { size: 15, className: "text-muted" })}
      </div>
    </AnimatedCard>
  );
}

export default function RiskControls({
  profile,
  selectedGroups,
  active = true,
}) {
  const [scenario, setScenario] = useState({
    shock_percent: -5,
    margin_level_floor: 120,
    concentration_limit: 70,
    symbol_gross_limit: 1000000,
  });
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);
  const activeRequest = useRef(false);
  const requestController = useRef(null);

  useEffect(() => {
    setSnapshot(null);
  }, [profile.id]);

  const load = useCallback(async () => {
    if (!profile || !selectedGroups.length || activeRequest.current)
      return false;
    activeRequest.current = true;
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    try {
      const data = await apiRequest("/api/risk/stress", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          selected_groups: selectedGroups,
          excluded_logins: [],
          group_filter_rules: "",
          ...scenario,
        }),
      });
      if (controller.signal.aborted || requestController.current !== controller) return false;
      if (data.status === "warming") return false;
      setSnapshot(data);
      return true;
    } catch (error) {
      if (!controller.signal.aborted && error.message !== "Request was cancelled")
        toast.error(error.message || "Risk stress snapshot failed");
      return false;
    } finally {
      if (requestController.current === controller) {
        activeRequest.current = false;
        requestController.current = null;
        setLoading(false);
      }
    }
  }, [profile, selectedGroups, scenario]);

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    const run = async () => {
      while (!cancelled) {
        const ready = await load();
        if (!cancelled)
          await new Promise((resolve) =>
            window.setTimeout(resolve, ready ? 15_000 : 2_000),
          );
      }
    };
    run();
    return () => {
      cancelled = true;
      requestController.current?.abort();
      requestController.current = null;
      activeRequest.current = false;
    };
  }, [active, load]);

  const summary = snapshot?.summary || {};
  const chartRows = useMemo(
    () =>
      (snapshot?.symbols || [])
        .slice(0, 12)
        .map((row) => ({ ...row, exposure_m: row.gross_exposure / 1000000 })),
    [snapshot],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] px-4 py-3">
        <div className="flex items-center gap-2 text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full bg-success ${loading ? "animate-pulse" : ""}`}
          />
          {loading
            ? "Recalculating live exposure and stress…"
            : "Continuous portfolio risk controls"}
          {snapshot?.generated_at && (
            <span>· {new Date(snapshot.generated_at).toLocaleString()}</span>
          )}
        </div>
        <span className="badge">Read-only controls</span>
      </div>

      <AnimatedCard className="p-4">
        <div className="mb-3 flex items-center gap-2">
          <SlidersHorizontal size={15} />
          <h2 className="text-sm font-semibold">Stress scenario and limits</h2>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-[11px] text-muted">
            Directional market shock, %
            <input
              type="number"
              min="-50"
              max="50"
              step="0.5"
              value={scenario.shock_percent}
              onChange={(event) =>
                setScenario((row) => ({
                  ...row,
                  shock_percent: Number(event.target.value),
                }))
              }
              className="input mt-2 w-full"
            />
          </label>
          <label className="text-[11px] text-muted">
            Margin level floor, %
            <input
              type="number"
              min="0"
              value={scenario.margin_level_floor}
              onChange={(event) =>
                setScenario((row) => ({
                  ...row,
                  margin_level_floor: Number(event.target.value),
                }))
              }
              className="input mt-2 w-full"
            />
          </label>
          <label className="text-[11px] text-muted">
            Concentration limit, %
            <input
              type="number"
              min="0"
              max="100"
              value={scenario.concentration_limit}
              onChange={(event) =>
                setScenario((row) => ({
                  ...row,
                  concentration_limit: Number(event.target.value),
                }))
              }
              className="input mt-2 w-full"
            />
          </label>
          <label className="text-[11px] text-muted">
            Symbol gross limit, USD
            <input
              type="number"
              min="0"
              step="10000"
              value={scenario.symbol_gross_limit}
              onChange={(event) =>
                setScenario((row) => ({
                  ...row,
                  symbol_gross_limit: Number(event.target.value),
                }))
              }
              className="input mt-2 w-full"
            />
          </label>
        </div>
        <p className="mt-3 text-[10px] text-muted">
          Shock P&L uses real lots × contract size × current price. A negative
          shock hurts BUY exposure and benefits SELL exposure.
        </p>
      </AnimatedCard>

      {(snapshot?.warnings || []).length > 0 && (
        <AnimatedCard className="p-3 text-xs text-amber-400">
          {snapshot.warnings.slice(0, 8).join(" · ")}
        </AnimatedCard>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
        <Metric
          label="Total Equity"
          value={moneyText(summary.equity)}
          icon={DollarSign}
        />
        <Metric
          label="Gross Exposure"
          value={moneyText(summary.gross_exposure)}
          icon={Activity}
        />
        <Metric
          label="Net Exposure"
          value={moneyText(summary.net_exposure)}
          icon={Activity}
        />
        <Metric
          label="Floating P&L"
          value={moneyText(summary.floating_pnl)}
          tone={summary.floating_pnl < 0 ? "text-danger" : "text-success"}
          icon={DollarSign}
        />
        <Metric
          label="Scenario P&L"
          value={moneyText(summary.shock_pnl)}
          tone={summary.shock_pnl < 0 ? "text-danger" : "text-success"}
          icon={Gauge}
        />
        <Metric
          label="Projected Equity"
          value={moneyText(summary.projected_equity)}
          icon={DollarSign}
        />
        <Metric
          label="Account Breaches"
          value={summary.account_breaches || 0}
          tone="text-amber-400"
          icon={AlertTriangle}
        />
        <Metric
          label="Symbol Breaches"
          value={summary.symbol_breaches || 0}
          tone="text-danger"
          icon={ShieldAlert}
        />
      </div>

      {!snapshot && (
        <AnimatedCard className="p-12 text-center text-muted">
          <Gauge size={28} className="mx-auto" />
          <p className="mt-3 text-sm">
            Building the first portfolio stress snapshot…
          </p>
        </AnimatedCard>
      )}

      {snapshot && (
        <div className="grid gap-4 xl:grid-cols-[1.15fr_.85fr]">
          <AnimatedCard className="p-5">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-[.14em]">
              Gross Symbol Exposure
            </h2>
            <div className="h-80">
              <SafeResponsiveContainer>
                <BarChart data={chartRows} layout="vertical">
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgb(var(--color-border))"
                    horizontal={false}
                  />
                  <XAxis
                    type="number"
                    tick={{ fontSize: 9, fill: "rgb(var(--color-text-3))" }}
                    tickFormatter={(value) => `$${value}m`}
                  />
                  <YAxis
                    type="category"
                    dataKey="symbol"
                    width={90}
                    tick={{ fontSize: 10, fill: "rgb(var(--color-text-2))" }}
                  />
                  <Tooltip
                    formatter={(value) => `$${Number(value).toFixed(3)}m`}
                  />
                  <Bar
                    dataKey="exposure_m"
                    fill="#f59e0b"
                    radius={[0, 4, 4, 0]}
                  />
                </BarChart>
              </SafeResponsiveContainer>
            </div>
          </AnimatedCard>
          <AnimatedCard className="overflow-hidden">
            <div className="border-b border-[rgb(var(--color-border))] p-4">
              <h2 className="text-sm font-semibold uppercase tracking-[.14em]">
                Symbol Limit Monitor
              </h2>
            </div>
            <div className="max-h-80 overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    {["Symbol", "Gross", "Net", "Lots", "Shock", "Status"].map(
                      (head) => (
                        <th
                          className="px-3 py-3 text-left font-medium text-muted"
                          key={head}
                        >
                          {head}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {(snapshot.symbols || []).map((row) => (
                    <tr
                      className="border-t border-[rgb(var(--color-border))]"
                      key={row.symbol}
                    >
                      <td className="px-3 py-3 font-medium">{row.symbol}</td>
                      <td className="px-3 py-3">
                        ${compact.format(row.gross_exposure)}
                      </td>
                      <td className="px-3 py-3">
                        ${compact.format(row.net_exposure)}
                      </td>
                      <td className="px-3 py-3">{row.gross_lots}</td>
                      <td
                        className={
                          row.shock_pnl < 0
                            ? "px-3 py-3 text-danger"
                            : "px-3 py-3 text-success"
                        }
                      >
                        {moneyText(row.shock_pnl)}
                      </td>
                      <td className="px-3 py-3">
                        <span
                          className={
                            row.limit_breach
                              ? "badge badge-danger"
                              : "badge badge-success"
                          }
                        >
                          {row.limit_breach ? "Breach" : "Within limit"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </AnimatedCard>
        </div>
      )}

      {snapshot && (
        <AnimatedCard className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-[.14em]">
                Account Risk Watchlist
              </h2>
              <p className="mt-1 text-[10px] text-muted">
                Live breaches with projected scenario impact and evidence.
              </p>
            </div>
            <span className="badge badge-amber">
              {snapshot.watchlist.length} accounts
            </span>
          </div>
          <div className="max-h-[500px] overflow-auto">
            <table className="w-full min-w-[1250px] text-xs">
              <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
                <tr>
                  {[
                    "Account",
                    "Group",
                    "Equity",
                    "Margin Level",
                    "Gross Exposure",
                    "Concentration",
                    "Shock P&L",
                    "Projected Equity",
                    "Projected Margin",
                    "Reasons",
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
                {snapshot.watchlist.map((row) => (
                  <tr
                    className="border-t border-[rgb(var(--color-border))]"
                    key={row.login}
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium">
                        {row.name || `Login ${row.login}`}
                      </div>
                      <div className="text-muted">#{row.login}</div>
                    </td>
                    <td className="px-4 py-3 text-muted">{row.group}</td>
                    <td className="px-4 py-3">{moneyText(row.equity)}</td>
                    <td className="px-4 py-3">{row.margin_level}%</td>
                    <td className="px-4 py-3">
                      {moneyText(row.gross_exposure)}
                    </td>
                    <td className="px-4 py-3">{row.concentration_pct}%</td>
                    <td
                      className={
                        row.shock_pnl < 0
                          ? "px-4 py-3 text-danger"
                          : "px-4 py-3 text-success"
                      }
                    >
                      {moneyText(row.shock_pnl)}
                    </td>
                    <td className="px-4 py-3">
                      {moneyText(row.projected_equity)}
                    </td>
                    <td className="px-4 py-3">
                      {row.margin > 0
                        ? `${row.projected_margin_level}%`
                        : "No margin"}
                    </td>
                    <td className="max-w-sm px-4 py-3 text-muted">
                      {row.reasons.join(" · ")}
                    </td>
                  </tr>
                ))}
                {!snapshot.watchlist.length && (
                  <tr>
                    <td colSpan={10} className="p-10 text-center text-muted">
                      No accounts breach the current limits.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </AnimatedCard>
      )}

      {snapshot && (
        <AnimatedCard className="overflow-hidden">
          <div className="border-b border-[rgb(var(--color-border))] p-4">
            <h2 className="text-sm font-semibold uppercase tracking-[.14em]">
              MT5 Group Risk Policies
            </h2>
          </div>
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[850px] text-xs">
              <thead>
                <tr>
                  {[
                    "Group",
                    "Currency",
                    "Margin Call",
                    "Stop Out",
                    "Margin Mode",
                    "Trade Mode",
                    "Default Leverage",
                    "Source",
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
                {snapshot.group_policies.map((row) => (
                  <tr
                    className="border-t border-[rgb(var(--color-border))]"
                    key={row.group}
                  >
                    <td className="px-4 py-3 font-medium">{row.group}</td>
                    <td className="px-4 py-3">{row.currency || "—"}</td>
                    <td className="px-4 py-3">{row.margin_call || "—"}</td>
                    <td className="px-4 py-3">{row.margin_stopout || "—"}</td>
                    <td className="px-4 py-3">{row.margin_mode || "—"}</td>
                    <td className="px-4 py-3">{row.trade_mode || "—"}</td>
                    <td className="px-4 py-3">
                      {row.leverage_default ? `1:${row.leverage_default}` : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          row.available
                            ? "badge badge-success"
                            : "badge badge-amber"
                        }
                      >
                        {row.available ? "GroupGet" : "Unavailable"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AnimatedCard>
      )}
    </div>
  );
}
