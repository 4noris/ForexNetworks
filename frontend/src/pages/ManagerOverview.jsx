import React, { useEffect, useState, useMemo } from "react";
import { useLocation } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Award,
  BarChart3,
  CheckCircle2,
  Database,
  DollarSign,
  TrendingDown,
  TrendingUp,
  Users,
} from "lucide-react";
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
} from "recharts";
import SafeResponsiveContainer from "../components/SafeResponsiveContainer";
import AnimatedCard from "../components/AnimatedCard";
import AnimatedNumber from "../components/AnimatedNumber";
import KPICard from "../components/KPICard";
import { liveMoneyView } from "../lib/liveMoney";
import { useStoredProfile } from "../lib/api";
import { useDashboard } from "../hooks/useManagerQueries";
import {
  buildSummaryRows,
  buildExposureRows,
  buildMarginCallAlerts,
  buildAccountExposureRows,
} from "../lib/dataTransforms";
import { formatCurrency } from "../lib/formatters";

const COLORS = [
  "#10b981",
  "#3b82f6",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#ec4899",
];
const currencyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const percentFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export default function ManagerOverview() {
  const profile = useStoredProfile();
  const isProfileSelected = Boolean(profile?.id);

  const pageActive = useLocation().pathname === "/manager";
  const { data, isLoading, refetch } = useDashboard(isProfileSelected && pageActive);
  const [moneyCurrency, setMoneyCurrency] = useState("USD");
  const moneyView = useMemo(() => liveMoneyView(data?.totals || {}, moneyCurrency), [data?.totals, moneyCurrency]);
  const currency = moneyView.currency;
  const moneyTotals = moneyView.valid ? moneyView.totals : {};
  const [activeTab, setActiveTab] = useState("summary"); // 'summary' | 'exposure'
  const [now, setNow] = useState(0);

  useEffect(() => {
    if (!pageActive) return undefined;
    const updateClock = () => setNow(Date.now());
    updateClock();
    const timer = window.setInterval(updateClock, 1000);
    return () => window.clearInterval(timer);
  }, [pageActive]);

  const sampledAt = Date.parse(data?.server_time_checked_at || "");
  const elapsedSinceSample = Number.isFinite(sampledAt) && now
    ? Math.max(0, now - sampledAt)
    : 0;
  const managerTime = data?.server_time_msc
    ? new Date(
        Number(data.server_time_msc) + elapsedSinceSample,
      ).toLocaleTimeString()
    : "Unavailable";
  const sourceAgeSeconds = data?.last_update && now ? Math.max(0, now / 1000 - data.last_update) : null;

  // Build rows & metrics
  const summaryBook = useMemo(
    () => buildSummaryRows(data?.summary_rows || []),
    [data?.summary_rows],
  );
  const exposureBook = useMemo(
    () => buildExposureRows(data?.exposure_rows || []),
    [data?.exposure_rows],
  );
  const accountMoney = useMemo(() => (value) => typeof value === "number" && Number.isFinite(value) && currency
    ? `${currencyFormatter.format(value)} ${currency}` : "Unavailable", [currency]);
  const currencyUsers = useMemo(() => (data?.users || []).filter((row) => row.currency === currency), [data?.users, currency]);
  const marginCallAlerts = useMemo(
    () => buildMarginCallAlerts(currencyUsers, accountMoney),
    [currencyUsers, accountMoney],
  );
  const accountExposure = useMemo(
    () => buildAccountExposureRows(currencyUsers, accountMoney),
    [currencyUsers, accountMoney],
  );

  // PIE CHARTS FOR EXPONSURE LONGS & SHORTS
  const netLongs = useMemo(() => {
    return exposureBook.rows
      .filter((row) => (row.NetUSDValue ?? 0) > 0)
      .sort((a, b) => b.NetUSDValue - a.NetUSDValue)
      .slice(0, 6)
      .map((row) => ({
        name: row.Asset,
        value: Math.abs(row.NetUSDValue),
      }));
  }, [exposureBook.rows]);

  const netShorts = useMemo(() => {
    return exposureBook.rows
      .filter((row) => (row.NetUSDValue ?? 0) < 0)
      .sort((a, b) => Math.abs(b.NetUSDValue) - Math.abs(a.NetUSDValue))
      .slice(0, 6)
      .map((row) => ({
        name: row.Asset,
        value: Math.abs(row.NetUSDValue),
      }));
  }, [exposureBook.rows]);

  if (!isProfileSelected) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <AnimatedCard className="p-8 max-w-md text-center flex flex-col items-center gap-4">
          <Database size={48} className="text-muted" />
          <h2 className="text-xl font-bold">No MT5 Profile Selected</h2>
          <p className="text-secondary text-sm">
            Please configure and select an active MT5 profile in Settings to
            enable real-time dashboard analytics.
          </p>
        </AnimatedCard>
      </div>
    );
  }

  const totals = data?.totals || {};
  const status = data?.status || "disconnected";
  const errors = data?.errors || [];

  return (
    <div className="space-y-6">
      {/* Header Row */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted">
            Real-Time Sync
          </p>
          <h1 className="font-display text-3xl font-bold tracking-tight">
            Manager Overview
          </h1>
        </div>

        {/* Sync Status Badge & Manual Controls */}
        <div className="flex flex-wrap items-center gap-3 self-start md:self-auto">
          {moneyView.available.length > 0 && <label className="inline-flex items-center gap-2 text-xs text-muted">Money currency
            <select aria-label="Manager money currency" className="field-input !w-auto text-xs" value={currency} onChange={(event) => setMoneyCurrency(event.target.value)}>
              {moneyView.available.map((code) => <option key={code}>{code}</option>)}
            </select>
          </label>}
          {status === "connected" && (
            <span className="badge badge-success flex items-center gap-1.5 py-1">
              <CheckCircle2 size={13} />
              Verified MT5 snapshot
            </span>
          )}
          {status === "error" && (
            <span className="badge badge-danger flex items-center gap-1.5 py-1">
              <AlertTriangle size={13} />
              Connection Error
            </span>
          )}
          {status === "disconnected" && (
            <span className="badge flex items-center gap-1.5 py-1">
              <Activity size={13} />
              Idle
            </span>
          )}
          {status === "warming" && (
            <span className="badge badge-amber flex items-center gap-1.5 py-1">
              <Activity size={13} />
              Loading verified MT5 snapshot
            </span>
          )}

          <button
            onClick={() => refetch()}
            className="btn-ghost py-2 px-3 text-xs"
            disabled={isLoading}
          >
            Force Refresh
          </button>
        </div>
      </div>

      {errors.length > 0 && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-xs text-red-400">
          <p className="font-semibold">Backend Connection Errors:</p>
          <ul className="list-disc pl-4 mt-1 space-y-1">
            {errors.map((err, idx) => (
              <li key={idx}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {status === "connected" && sourceAgeSeconds > 15 && (
        <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-400">
          MT5 account snapshot is {Math.round(sourceAgeSeconds)} seconds old. Values may be stale while the Manager refreshes.
        </div>
      )}

      {/* KPI Cards Row */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KPICard
          title="Active Accounts"
          value={totals.visible_accounts}
          icon={Users}
          suffix={totals.enabled_accounts == null ? "" : ` / ${totals.enabled_accounts}`}
        />
        <KPICard
          title="Open Positions"
          value={totals.open_positions}
          icon={Activity}
        />
        <KPICard
          title="Total Balance"
          value={moneyTotals.balance}
          suffix={currency ? ` ${currency}` : ""}
          formatter={currencyFormatter}
          icon={DollarSign}
        />
        <KPICard
          title="Total Equity"
          value={moneyTotals.equity}
          suffix={currency ? ` ${currency}` : ""}
          formatter={currencyFormatter}
          icon={Award}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Margin Level & Free Margin */}
        <AnimatedCard className="p-5 flex flex-col justify-between">
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted">
              Aggregate Margin Level
            </h3>
            <div className="mt-4 flex items-baseline gap-2">
              <span className="text-4xl font-bold tracking-tight">
                <AnimatedNumber
                  value={moneyTotals.margin > 0 && moneyTotals.equity != null ? moneyTotals.equity / moneyTotals.margin * 100 : null}
                  formatter={percentFormatter}
                  suffix="%"
                />
              </span>
            </div>
            <p className="mt-2 text-xs text-secondary">
              {currency || "Selected currency"} deposit accounts only. Account and position counts cover all currencies.
            </p>
          </div>
          <div className="mt-4 pt-4 border-t border-[rgb(var(--color-border))] flex justify-between text-xs">
            <div>
              <p className="text-muted">Total Margin</p>
              <p className="font-semibold text-primary mt-1">
                <AnimatedNumber
                  value={moneyTotals.margin}
                  formatter={currencyFormatter}
                  suffix={currency ? ` ${currency}` : ""}
                />
              </p>
            </div>
            <div>
              <p className="text-muted">Free Margin</p>
              <p className="font-semibold text-primary mt-1">
                <AnimatedNumber
                  value={moneyTotals.margin_free}
                  formatter={currencyFormatter}
                  suffix={currency ? ` ${currency}` : ""}
                />
              </p>
            </div>
          </div>
        </AnimatedCard>

        {/* Longs Pie Chart */}
        <AnimatedCard className="p-5">
          <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted mb-4">
            Top Net Long Exposure (USD)
          </h3>
          <div className="h-44">
            {netLongs.length > 0 ? (
              <SafeResponsiveContainer>
                <PieChart>
                  <Pie
                    data={netLongs}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={70}
                    paddingAngle={3}
                    dataKey="value"
                  >
                    {netLongs.map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={COLORS[index % COLORS.length]}
                      />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => formatCurrency(value)} />
                  <Legend
                    iconSize={8}
                    iconType="circle"
                    wrapperStyle={{ fontSize: 10 }}
                  />
                </PieChart>
              </SafeResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted">
                No long exposure assets.
              </div>
            )}
          </div>
        </AnimatedCard>

        {/* Shorts Pie Chart */}
        <AnimatedCard className="p-5">
          <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted mb-4">
            Top Net Short Exposure (USD)
          </h3>
          <div className="h-44">
            {netShorts.length > 0 ? (
              <SafeResponsiveContainer>
                <PieChart>
                  <Pie
                    data={netShorts}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={70}
                    paddingAngle={3}
                    dataKey="value"
                  >
                    {netShorts.map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={COLORS[index % COLORS.length]}
                      />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => formatCurrency(value)} />
                  <Legend
                    iconSize={8}
                    iconType="circle"
                    wrapperStyle={{ fontSize: 10 }}
                  />
                </PieChart>
              </SafeResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted">
                No short exposure assets.
              </div>
            )}
          </div>
        </AnimatedCard>
      </div>

      {/* Live Book Panel */}
      <AnimatedCard className="p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-[rgb(var(--color-border))] pb-4 mb-6">
          <div className="flex gap-2">
            <button
              onClick={() => setActiveTab("summary")}
              className={`px-4 py-2 text-sm font-medium rounded-lg border transition-all ${
                activeTab === "summary"
                  ? "bg-primary text-reverse border-primary"
                  : "bg-transparent text-secondary border-[rgb(var(--color-border))] hover:bg-[var(--btn-ghost-bg)]"
              }`}
            >
              Summary Book
            </button>
            <button
              onClick={() => setActiveTab("exposure")}
              className={`px-4 py-2 text-sm font-medium rounded-lg border transition-all ${
                activeTab === "exposure"
                  ? "bg-primary text-reverse border-primary"
                  : "bg-transparent text-secondary border-[rgb(var(--color-border))] hover:bg-[var(--btn-ghost-bg)]"
              }`}
            >
              Asset Exposure
            </button>
          </div>

          <div className="text-right text-xs text-muted">
            <p>Manager time: {managerTime}</p>
            <p>
              Snapshot: {data?.last_update ? new Date(data.last_update * 1000).toLocaleTimeString() : "Never"}
            </p>
          </div>
        </div>

        {/* Tab Content */}
        {activeTab === "summary" ? (
          <div className="max-h-[420px] overflow-auto scrollbar-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="sticky top-0 z-10 bg-[rgb(var(--color-card))]">
                <tr className="border-b border-[rgb(var(--color-border))] text-muted">
                  <th className="py-3 px-4 font-semibold">Symbol</th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Positions (C/Cov)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Buy Vol (C/Cov)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Buy Price (C/Cov)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Sell Vol (C/Cov)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Sell Price (C/Cov)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Net Volume
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Profit (C/Cov)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Uncovered (USD)
                  </th>
                </tr>
              </thead>
              <tbody>
                {summaryBook.rows.length > 0 ? (
                  summaryBook.rows.map((row, idx) => {
                    const isTotalRow =
                      row.Symbol.toLowerCase() === "summary" ||
                      row.Symbol.toLowerCase() === "total";
                    return (
                      <tr
                        key={idx}
                        className={`border-b border-[rgb(var(--color-border-soft))] hover:bg-[var(--btn-ghost-bg)] ${
                          isTotalRow
                            ? "font-bold bg-[var(--btn-ghost-bg)] sep border-t border-[rgb(var(--color-border))]"
                            : ""
                        }`}
                      >
                        <td className="py-3 px-4 font-medium">{row.Symbol}</td>
                        <td className="py-3 px-4 text-right tabular-nums">
                          {row.Positions}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums">
                          {row["Buy Volume"]}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums text-muted">
                          {row["Buy Price"]}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums">
                          {row["Sell Volume"]}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums text-muted">
                          {row["Sell Price"]}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums">
                          {row["Net Volume"]}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums">
                          {row["Profit (USD)"]}
                        </td>
                        <td className="py-3 px-4 text-right tabular-nums font-semibold">
                          {row["Uncovered (USD)"]}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={9} className="py-8 text-center text-muted">
                      No summary book records found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="max-h-[420px] overflow-auto scrollbar-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="sticky top-0 z-10 bg-[rgb(var(--color-card))]">
                <tr className="border-b border-[rgb(var(--color-border))] text-muted">
                  <th className="py-3 px-4 font-semibold">Asset</th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Clients Volume
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Coverage Volume
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Net Total Units
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">Rate</th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Net Total (USD)
                  </th>
                  <th className="py-3 px-4 font-semibold text-right">
                    Positive (USD)
                  </th>
                </tr>
              </thead>
              <tbody>
                {exposureBook.rows.length > 0 ? (
                  exposureBook.rows.map((row, idx) => (
                    <tr
                      key={idx}
                      className="border-b border-[rgb(var(--color-border-soft))] hover:bg-[var(--btn-ghost-bg)]"
                    >
                      <td className="py-3 px-4 font-medium">{row.Asset}</td>
                      <td className="py-3 px-4 text-right tabular-nums">
                        {row.Clients}
                      </td>
                      <td className="py-3 px-4 text-right tabular-nums">
                        {row.Coverage}
                      </td>
                      <td className="py-3 px-4 text-right tabular-nums">
                        {row["Net Total"]}
                      </td>
                      <td className="py-3 px-4 text-right tabular-nums text-muted">
                        {row.Rate}
                      </td>
                      <td
                        className={`py-3 px-4 text-right tabular-nums font-semibold ${
                          row.NetUSDValue > 0
                            ? "text-success"
                            : row.NetUSDValue < 0
                              ? "text-danger"
                              : ""
                        }`}
                      >
                        {row["Net Total (USD)"]}
                      </td>
                      <td className="py-3 px-4 text-right tabular-nums text-muted">
                        {row["Positive (USD)"]}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-muted">
                      No exposure book records found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </AnimatedCard>

      {/* Bottom Grid: Margin Alerts & Top Account Exposure */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Margin Alerts (<120%) */}
        <AnimatedCard className="p-6">
          <div className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] pb-4 mb-4">
            <AlertTriangle size={18} className="text-danger" />
            <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted">
              Margin Call Watchlist (&lt;120%) · {currency || "Currency required"}
            </h3>
          </div>

          <div className="max-h-[320px] overflow-auto scrollbar-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-[rgb(var(--color-border))] text-muted">
                  <th className="py-2.5 px-3 font-semibold">Account</th>
                  <th className="py-2.5 px-3 font-semibold">Name</th>
                  <th className="py-2.5 px-3 font-semibold text-right">
                    Margin Level
                  </th>
                  <th className="py-2.5 px-3 font-semibold text-right">
                    Equity
                  </th>
                  <th className="py-2.5 px-3 font-semibold">Group</th>
                  <th className="py-2.5 px-3 font-semibold text-center">
                    Risk
                  </th>
                </tr>
              </thead>
              <tbody>
                {marginCallAlerts.length > 0 ? (
                  marginCallAlerts.map((row, idx) => {
                    const alertBadgeClass =
                      row.Alert === "Critical"
                        ? "badge-danger"
                        : row.Alert === "High"
                          ? "badge-amber"
                          : "";
                    return (
                      <tr
                        key={idx}
                        className="border-b border-[rgb(var(--color-border-soft))] hover:bg-[var(--btn-ghost-bg)]"
                      >
                        <td className="py-2.5 px-3 tabular-nums font-semibold">
                          {row.Login}
                        </td>
                        <td className="py-2.5 px-3 truncate max-w-[120px]">
                          {row.Name}
                        </td>
                        <td className="py-2.5 px-3 text-right font-bold text-danger tabular-nums">
                          {row["Margin Level"]}
                        </td>
                        <td className="py-2.5 px-3 text-right tabular-nums text-muted">
                          {row.Equity}
                        </td>
                        <td className="py-2.5 px-3 truncate max-w-[100px] text-muted">
                          {row.Group}
                        </td>
                        <td className="py-2.5 px-3 text-center">
                          <span
                            className={`badge ${alertBadgeClass} py-0.5 px-2 text-[9px]`}
                          >
                            {row.Alert}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-muted">
                      No accounts currently under margin threshold.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </AnimatedCard>

        {/* Top Account Exposure */}
        <AnimatedCard className="p-6">
          <div className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] pb-4 mb-4">
            <TrendingUp size={18} className="text-success" />
            <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted">
              Top 5 Account Exposure · {currency || "Currency required"}
            </h3>
          </div>

          <div className="max-h-[320px] overflow-auto scrollbar-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-[rgb(var(--color-border))] text-muted">
                  <th className="py-2.5 px-3 font-semibold">Account</th>
                  <th className="py-2.5 px-3 font-semibold">Name</th>
                  <th className="py-2.5 px-3 font-semibold text-right">
                    Margin
                  </th>
                  <th className="py-2.5 px-3 font-semibold text-right">
                    Margin Level
                  </th>
                  <th className="py-2.5 px-3 font-semibold text-right">
                    Floating P/L
                  </th>
                  <th className="py-2.5 px-3 font-semibold">Group</th>
                </tr>
              </thead>
              <tbody>
                {accountExposure.length > 0 ? (
                  accountExposure.map((row, idx) => (
                    <tr
                      key={idx}
                      className="border-b border-[rgb(var(--color-border-soft))] hover:bg-[var(--btn-ghost-bg)]"
                    >
                      <td className="py-2.5 px-3 tabular-nums font-semibold">
                        {row.Login}
                      </td>
                      <td className="py-2.5 px-3 truncate max-w-[120px]">
                        {row.Name}
                      </td>
                      <td className="py-2.5 px-3 text-right font-semibold tabular-nums">
                        {row.Margin}
                      </td>
                      <td className="py-2.5 px-3 text-right tabular-nums text-muted">
                        {row["Margin Level"]}
                      </td>
                      <td
                        className={`py-2.5 px-3 text-right tabular-nums ${
                          parseFloat(
                            row["Floating P/L"].replace(/[^0-9.-]+/g, ""),
                          ) > 0
                            ? "text-success"
                            : parseFloat(
                                  row["Floating P/L"].replace(/[^0-9.-]+/g, ""),
                                ) < 0
                              ? "text-danger"
                              : ""
                        }`}
                      >
                        {row["Floating P/L"]}
                      </td>
                      <td className="py-2.5 px-3 truncate max-w-[100px] text-muted">
                        {row.Group}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-muted">
                      No accounts currently active.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </AnimatedCard>
      </div>
    </div>
  );
}
