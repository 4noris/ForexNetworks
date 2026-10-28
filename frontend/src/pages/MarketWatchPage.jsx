import React, { useState, useMemo, useEffect } from "react";
import { Search, TrendingUp } from "lucide-react";
import AnimatedCard from "../components/AnimatedCard";
import { Link } from "react-router-dom";
import { getStoredUser, useStoredProfile } from "../lib/api";
import { useMarketWatch } from "../hooks/useManagerQueries";
import { buildDisplayRows } from "../lib/dataTransforms";
import { formatCurrency, formatPercent } from "../lib/formatters";

export default function MarketWatchPage() {
  const profile = useStoredProfile();
  const isProfileSelected = Boolean(profile?.id);

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [limit, setLimit] = useState(100);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Live queries
  const { data: watchData, isLoading: watchLoading, isError: watchError } = useMarketWatch(
    debouncedSearch,
    isProfileSelected,
    limit,
  );

  // Transforms
  const displayRows = useMemo(
    () => buildDisplayRows(watchData?.rows || []),
    [watchData?.rows],
  );
  const accountExposure = useMemo(
    () =>
      (watchData?.top_margin_accounts || []).map((row) => ({
        Login: row.login,
        Name: row.name || "-",
        Group: row.group || "-",
        Margin: formatCurrency(row.margin),
        "Margin Level": formatPercent(row.margin_level),
        "Floating P/L": formatCurrency(row.floating_pnl),
      })),
    [watchData?.top_margin_accounts],
  );

  if (!isProfileSelected) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <AnimatedCard className="p-8 max-w-md text-center flex flex-col items-center gap-4">
          <Search size={48} className="text-muted" />
          <h2 className="text-xl font-bold">No MT5 Profile Selected</h2>
          <p className="text-secondary text-sm">
            Please configure and select an active MT5 profile in Settings to
            view the Live Market Watch ticks.
          </p>
        </AnimatedCard>
      </div>
    );
  }

  const summary = watchData?.summary || {};

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <p className="text-xs uppercase tracking-widest text-muted">
          Real-Time Quotes
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">
          Market Watch
        </h1>
        {getStoredUser()?.is_admin && <Link to="/settings#spread-alerts" className="mt-2 inline-block text-xs text-accent underline">Configure spread alerts</Link>}
        <p className="mt-2 text-xs text-muted">
          {watchError
            ? "Quote service is unavailable; the last displayed values may be old."
            : `${summary.live_quotes ?? 0} live quotes · ${summary.stale_quotes ?? 0} stale · last MT5 tick ${summary.last_tick ? `${String(summary.last_tick).slice(11, 19)} MT5` : "unavailable"}`}
        </p>
      </div>

      {/* Summary statistics bar */}
      <div className="grid gap-4 grid-cols-2 md:grid-cols-4 lg:grid-cols-5">
        <AnimatedCard className="p-4 flex flex-col justify-between">
          <p className="text-[10px] uppercase tracking-wider text-muted">
            Total Symbols
          </p>
          <p className="text-xl font-bold tracking-tight mt-1 tabular-nums">
            {summary.total_symbols ?? 0}
          </p>
        </AnimatedCard>
        <AnimatedCard className="p-4 flex flex-col justify-between">
          <p className="text-[10px] uppercase tracking-wider text-muted">
            Filtered Symbols
          </p>
          <p className="text-xl font-bold tracking-tight mt-1 tabular-nums">
            {summary.filtered_symbols ?? 0}
          </p>
        </AnimatedCard>
        <AnimatedCard className="p-4 flex flex-col justify-between">
          <p className="text-[10px] uppercase tracking-wider text-muted">
            Active Ticks
          </p>
          <p className="text-xl font-bold tracking-tight mt-1 tabular-nums text-success">
            {summary.live_quotes ?? 0}
          </p>
        </AnimatedCard>
        <AnimatedCard className="p-4 flex flex-col justify-between">
          <p className="text-[10px] uppercase tracking-wider text-muted">
            Avg Spread (Pts)
          </p>
          <p className="text-xl font-bold tracking-tight mt-1 tabular-nums">
            {summary.average_spread_points ?? "0.00"}
          </p>
        </AnimatedCard>
        <AnimatedCard className="p-4 flex flex-col justify-between col-span-2 md:col-span-1">
          <p className="text-[10px] uppercase tracking-wider text-muted">
            Max Spread (Pts)
          </p>
          <p className="text-xs mt-1 truncate font-semibold text-danger">
            {summary.max_spread_points
              ? `${summary.max_spread_points} (${summary.max_spread_symbol})`
              : "0 (None)"}
          </p>
        </AnimatedCard>
      </div>

      {/* Main content grid: Symbols Table (left) + Account Exposure Sidebar (right) */}
      <div className="grid gap-6 lg:grid-cols-4">
        {/* Symbols Table */}
        <div className="lg:col-span-3 space-y-4">
          <AnimatedCard className="p-5">
            {/* Search and Limit bar */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
              <div className="relative flex-1 max-w-sm">
                <Search
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                />
                <input
                  type="text"
                  placeholder="Search symbols..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="field-input pl-9 py-2 text-xs"
                />
              </div>

              <div className="flex items-center gap-2 text-xs">
                <span className="text-muted">Display Limit:</span>
                <select
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                  className="field-input py-1.5 px-3 text-xs w-28 bg-[var(--inp-bg)] border-[var(--inp-border)]"
                >
                  <option value={50}>50 rows</option>
                  <option value={100}>100 rows</option>
                  <option value={150}>150 rows</option>
                  <option value={250}>250 rows</option>
                  <option value={500}>500 rows</option>
                </select>
              </div>
            </div>

            {/* Table */}
            <div className="max-h-[520px] overflow-auto scrollbar-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead className="sticky top-0 z-10 bg-[rgb(var(--color-card))]">
                  <tr className="border-b border-[rgb(var(--color-border))] text-muted">
                    <th className="py-2.5 px-3 font-semibold">Symbol</th>
                    <th className="py-2.5 px-3 font-semibold">Path</th>
                    <th className="py-2.5 px-3 font-semibold">Quote</th>
                    <th className="py-2.5 px-3 font-semibold">Last Tick</th>
                    <th className="py-2.5 px-3 font-semibold text-right">
                      Spread (Pts)
                    </th>
                    <th className="py-2.5 px-3 font-semibold text-right">
                      Bid
                    </th>
                    <th className="py-2.5 px-3 font-semibold text-right">
                      Ask
                    </th>
                    <th className="py-2.5 px-3 font-semibold text-right">
                      Contract Size
                    </th>
                    <th className="py-2.5 px-3 font-semibold text-right">
                      Tick Value
                    </th>
                    <th className="py-2.5 px-3 font-semibold text-right">
                      Margin Initial
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {displayRows.length > 0 ? (
                    displayRows.map((row, idx) => (
                      <tr
                        key={idx}
                        className="border-b border-[rgb(var(--color-border-soft))] hover:bg-[var(--btn-ghost-bg)]"
                      >
                        <td className="py-2.5 px-3 font-semibold text-primary">
                          {row.Symbol}
                        </td>
                        <td className="py-2.5 px-3 text-muted truncate max-w-[120px]">
                          {row.Path}
                        </td>
                        <td className="py-2.5 px-3">
                          <span className={row["Quote Status"] === "live" ? "badge badge-success" : "badge badge-amber"}>
                            {row["Quote Status"] === "live" ? "Live" : row["Quote Status"] === "stale" ? "Stale" : "Unavailable"}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-muted tabular-nums" title={row["Tick Age"] == null ? "Tick time unavailable" : `${Math.round(row["Tick Age"])} seconds old`}>
                          {row["Last Tick"]}
                        </td>
                        <td className="py-2.5 px-3 text-right font-semibold tabular-nums text-danger">
                          {row.Spread}
                        </td>
                        <td className="py-2.5 px-3 text-right tabular-nums text-primary font-medium">
                          {row.Bid}
                        </td>
                        <td className="py-2.5 px-3 text-right tabular-nums text-primary font-medium">
                          {row.Ask}
                        </td>
                        <td className="py-2.5 px-3 text-right tabular-nums text-muted">
                          {row["Contract Size"]}
                        </td>
                        <td className="py-2.5 px-3 text-right tabular-nums text-muted">
                          {row["Tick Value"]}
                        </td>
                        <td className="py-2.5 px-3 text-right tabular-nums text-muted font-semibold">
                          {row["Margin Initial"]}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={10} className="py-8 text-center text-muted">
                        {watchLoading
                          ? "Loading quotes..."
                          : "No live quotes found matching the search criteria."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </AnimatedCard>
        </div>

        {/* Account Exposure Sidebar */}
        <div className="lg:col-span-1 space-y-4">
          <AnimatedCard className="p-5">
            <div className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] pb-3 mb-4">
              <TrendingUp size={16} className="text-success" />
              <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                Peak Margin Exposure
              </h3>
            </div>

            <div className="max-h-[520px] space-y-3 overflow-y-auto scrollbar-hidden">
              {accountExposure.length > 0 ? (
                accountExposure.map((acc, idx) => (
                  <div
                    key={idx}
                    className="p-3 rounded-lg border border-[rgb(var(--color-border-soft))] bg-[var(--inp-bg)] space-y-1 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-primary">
                        {acc.Login}
                      </span>
                      <span className="font-bold text-danger text-[10px] px-1.5 py-0.5 rounded bg-danger/5 border border-danger/10">
                        {acc["Margin Level"]}
                      </span>
                    </div>
                    <div className="flex justify-between text-muted text-[10px]">
                      <span>{acc.Name}</span>
                      <span className="truncate max-w-[80px]">{acc.Group}</span>
                    </div>
                    <div className="flex justify-between pt-1 border-t border-[rgb(var(--color-border-soft))] mt-1 font-medium">
                      <span>Margin: {acc.Margin}</span>
                      <span
                        className={
                          parseFloat(
                            acc["Floating P/L"].replace(/[^0-9.-]+/g, ""),
                          ) > 0
                            ? "text-success"
                            : parseFloat(
                                  acc["Floating P/L"].replace(/[^0-9.-]+/g, ""),
                                ) < 0
                              ? "text-danger"
                              : ""
                        }
                      >
                        PnL: {acc["Floating P/L"]}
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-center text-xs text-muted py-6">
                  No active accounts exposed.
                </p>
              )}
            </div>
          </AnimatedCard>
        </div>
      </div>
    </div>
  );
}
