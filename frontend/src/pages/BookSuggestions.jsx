import React, { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Search,
  Split,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import AnimatedCard from "../components/AnimatedCard";
import { apiRequest } from "../lib/api";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const PERIODS = [
  ["daily", "Daily"],
  ["weekly", "Weekly"],
  ["monthly", "Monthly"],
  ["overall", "Overall (365d)"],
];

const BOOKS = {
  "A-Book": {
    badge: "border-blue-500/30 bg-blue-500/10 text-blue-400",
    icon: TrendingUp,
    description: "Consider externalizing more of this client’s market risk.",
  },
  "B-Book": {
    badge: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    icon: TrendingDown,
    description: "Internalization may fit the observed risk profile.",
  },
  Hybrid: {
    badge: "border-amber-500/30 bg-amber-500/10 text-amber-400",
    icon: Split,
    description: "Use partial hedging or continue monitoring mixed evidence.",
  },
};

function classify(account, realized) {
  const historyAvailable = Number.isFinite(realized);
  const realizedValue = historyAvailable ? realized : null;
  const floating = Number(account.floating_pnl || 0);
  const equity = Math.abs(Number(account.equity || 0));
  const lots = Number(account.gross_lots || 0);
  const positions = Number(account.positions || 0);
  const concentration = Number(account.concentration_pct || 0);
  const riskScore = Number(account.risk_score || 0);
  const netPerformance = (realizedValue ?? 0) + floating;
  const pnlRatio = equity ? netPerformance / equity : 0;
  let aScore = 0;
  let bScore = 0;
  const aReasons = [];
  const bReasons = [];

  if (netPerformance > 0) {
    aScore += 30;
    aReasons.push(
      historyAvailable
        ? "positive realized + floating P&L"
        : "positive live floating P&L",
    );
  } else if (netPerformance < 0) {
    bScore += 30;
    bReasons.push(
      historyAvailable
        ? "negative realized + floating P&L"
        : "negative live floating P&L",
    );
  }
  if (historyAvailable && realizedValue > 0) {
    aScore += 20;
    aReasons.push("positive realized P&L in the selected period");
  } else if (historyAvailable && realizedValue < 0) {
    bScore += 20;
    bReasons.push("negative realized P&L in the selected period");
  }
  if (pnlRatio >= 0.1) {
    aScore += 15;
    aReasons.push("profit is material relative to equity");
  } else if (pnlRatio <= -0.1) {
    bScore += 15;
    bReasons.push("loss is material relative to equity");
  }
  if (lots >= 1 || positions >= 10) {
    aScore += 15;
    aReasons.push("meaningful live volume");
  } else {
    bScore += 10;
    bReasons.push("limited live volume");
  }
  if (concentration >= 70) {
    aScore += 10;
    aReasons.push("high symbol concentration");
  } else {
    bScore += 5;
  }
  if (riskScore >= 50) {
    aScore += 10;
    aReasons.push("elevated live risk score");
  } else if (riskScore < 25) {
    bScore += 10;
    bReasons.push("low current risk score");
  }

  const difference = Math.abs(aScore - bScore);
  const incompleteHistory = !historyAvailable;
  const book = incompleteHistory
    ? "Hybrid"
    : difference < 20
      ? "Hybrid"
      : aScore > bScore
        ? "A-Book"
        : "B-Book";
  const confidence = incompleteHistory
    ? 40
    : book === "Hybrid"
      ? Math.max(45, Math.min(69, 70 - difference))
      : Math.min(95, 55 + difference);
  let reasons;
  if (book === "A-Book") reasons = aReasons;
  else if (book === "B-Book") reasons = bReasons;
  else reasons = [...aReasons.slice(0, 2), ...bReasons.slice(0, 2)];
  if (incompleteHistory) reasons.push("limited cached realized-P&L history");

  return {
    ...account,
    suggestion: book,
    confidence,
    realized: realizedValue,
    netPerformance,
    reasons: [...new Set(reasons)].slice(0, 4),
  };
}

function SummaryCard({ label, value, book }) {
  const Icon = BOOKS[book]?.icon;
  return (
    <AnimatedCard className="p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{label}</span>
        {Icon && (
          <Icon size={16} className={BOOKS[book].badge.split(" ").at(-1)} />
        )}
      </div>
      <div className="mt-3 text-2xl font-semibold tabular-nums">{value}</div>
    </AnimatedCard>
  );
}

export default function BookSuggestions({
  profile,
  selectedGroups,
  accounts,
  totalActiveAccounts = 0,
  active,
}) {
  const [performanceState, setPerformanceState] = useState(null);
  const [period, setPeriod] = useState("overall");
  const [filter, setFilter] = useState("All");
  const [query, setQuery] = useState("");

  const scopeKey = useMemo(
    () => `${profile.id}:${[...selectedGroups].sort().join("|")}`,
    [profile.id, selectedGroups],
  );
  const performance = performanceState?.scopeKey === scopeKey
    ? performanceState.result
    : null;

  useEffect(() => {
    if (!active || !selectedGroups.length) return undefined;
    let cancelled = false;
    let timer;
    const load = async () => {
      try {
        const result = await apiRequest("/api/risk/performance", {
          method: "POST",
          timeoutMs: 60_000,
          body: JSON.stringify({
            server: profile.server,
            login: profile.mt5_login,
            password: profile.mt5_password,
            selected_groups: selectedGroups,
            lookback_days: 365,
          }),
        });
        if (!cancelled && result?.status !== "warming")
          setPerformanceState({ scopeKey, result });
      } catch {
        // The live-only classifier remains available during history refresh.
      }
      if (!cancelled) timer = window.setTimeout(load, 60_000);
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, profile, selectedGroups, scopeKey]);

  const suggestions = useMemo(() => {
    const selectedPeriod = performance?.periods?.[period] || {};
    const historyComplete = period === "daily" || performance?.data_quality?.history_complete;
    const periodAccounts = historyComplete && selectedPeriod.accounts?.length
      ? selectedPeriod.accounts
      : historyComplete
        ? [...(selectedPeriod.winners || []), ...(selectedPeriod.losers || [])]
        : [];
    const realizedByLogin = new Map(
      periodAccounts.map((row) => [Number(row.login), Number(row.pnl || 0)]),
    );
    return accounts
      .filter((row) => Number(row.positions || 0) > 0)
      .map((row) => classify(row, realizedByLogin.get(Number(row.login))))
      .sort((a, b) => b.confidence - a.confidence || a.login - b.login);
  }, [accounts, performance, period]);

  const visible = useMemo(() => {
    const search = query.trim().toLowerCase();
    return suggestions.filter(
      (row) =>
        (filter === "All" || row.suggestion === filter) &&
        (!search ||
          String(row.login).includes(search) ||
          String(row.name || "")
            .toLowerCase()
            .includes(search) ||
          String(row.group || "")
            .toLowerCase()
            .includes(search)),
    );
  }, [filter, query, suggestions]);

  const counts = Object.fromEntries(
    ["A-Book", "B-Book", "Hybrid"].map((book) => [
      book,
      suggestions.filter((row) => row.suggestion === book).length,
    ]),
  );

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs text-secondary">
        <span className="flex items-start gap-2">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-400" />
          Suggestions are decision support based only on trading activity.
          Review liquidity, compliance, client agreements, and current market
          conditions before changing any book assignment.
        </span>
      </div>
      {totalActiveAccounts > accounts.length && (
        <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-400">
          Suggestions cover {accounts.length} of {totalActiveAccounts} active accounts returned by the live monitor. Review the remaining accounts before applying any group assignment.
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard label="Clients analysed" value={suggestions.length} />
        <SummaryCard
          label="Suggested A-Book"
          value={counts["A-Book"]}
          book="A-Book"
        />
        <SummaryCard
          label="Suggested B-Book"
          value={counts["B-Book"]}
          book="B-Book"
        />
        <SummaryCard
          label="Suggested Hybrid"
          value={counts.Hybrid}
          book="Hybrid"
        />
      </div>

      <AnimatedCard className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-[rgb(var(--color-border))] p-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold">Client book suggestions</h2>
            <p className="mt-1 text-xs text-muted">
              Live exposure plus cached{" "}
              {PERIODS.find(([key]) => key === period)?.[1]} realized
              performance
            </p>
            {performance && period !== "daily" && !performance.data_quality?.history_complete && (
              <p className="mt-1 text-xs text-amber-400">Full realized history is loading. Suggestions remain provisional.</p>
            )}
          </div>
          <div className="flex flex-col gap-2 xl:flex-row">
            <div className="flex overflow-x-auto rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-1 scrollbar-hidden">
              {PERIODS.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPeriod(key)}
                  className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs ${period === key ? "bg-white text-black" : "text-muted"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="relative">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search client or group"
                className="field-input py-2 pl-9 text-xs"
              />
            </div>
            <div className="flex rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-1">
              {["All", "A-Book", "B-Book", "Hybrid"].map((book) => (
                <button
                  key={book}
                  type="button"
                  onClick={() => setFilter(book)}
                  className={`rounded-md px-3 py-1.5 text-xs ${filter === book ? "bg-white text-black" : "text-muted"}`}
                >
                  {book}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="max-h-[620px] overflow-auto scrollbar-hidden">
          <table className="w-full min-w-[1100px] text-xs">
            <thead className="sticky top-0 z-10 bg-[rgb(var(--color-surface))]">
              <tr>
                {[
                  "Client",
                  "Group",
                  "Suggestion",
                  "Confidence",
                  `${PERIODS.find(([key]) => key === period)?.[1]} Realized`,
                  "Live Floating",
                  "Lots",
                  "Positions",
                  "Concentration",
                  "Reasoning",
                ].map((head) => (
                  <th
                    key={head}
                    className="px-3 py-3 text-left font-medium text-muted"
                  >
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const config = BOOKS[row.suggestion];
                return (
                  <tr
                    key={row.login}
                    className="border-t border-[rgb(var(--color-border))] align-top"
                  >
                    <td className="px-3 py-3">
                      <div className="font-medium">
                        {row.name || `Login ${row.login}`}
                      </div>
                      <div className="text-muted">#{row.login}</div>
                    </td>
                    <td
                      className="max-w-[190px] truncate px-3 py-3"
                      title={row.group}
                    >
                      {row.group}
                    </td>
                    <td className="px-3 py-3">
                      <span
                        className={`inline-flex rounded-full border px-2 py-1 font-semibold ${config.badge}`}
                      >
                        {row.suggestion}
                      </span>
                      <div className="mt-1 max-w-[200px] text-[10px] text-muted">
                        {config.description}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="font-semibold">{row.confidence}%</div>
                      <div className="mt-1 h-1.5 w-20 overflow-hidden rounded-full bg-white/5">
                        <div
                          className="h-full bg-blue-500"
                          style={{ width: `${row.confidence}%` }}
                        />
                      </div>
                    </td>
                    <td
                      className={`px-3 py-3 ${row.realized == null ? "text-muted" : row.realized < 0 ? "text-danger" : "text-success"}`}
                    >
                      {row.realized == null
                        ? "Unavailable"
                        : money.format(row.realized)}
                    </td>
                    <td
                      className={`px-3 py-3 ${row.floating_pnl < 0 ? "text-danger" : "text-success"}`}
                    >
                      {money.format(row.floating_pnl || 0)}
                    </td>
                    <td className="px-3 py-3">
                      {number.format(row.gross_lots || 0)}
                    </td>
                    <td className="px-3 py-3">{row.positions}</td>
                    <td className="px-3 py-3">
                      {number.format(row.concentration_pct || 0)}%
                    </td>
                    <td className="max-w-[280px] px-3 py-3 text-secondary">
                      {row.reasons.join(" · ")}
                    </td>
                  </tr>
                );
              })}
              {!visible.length && (
                <tr>
                  <td colSpan={10} className="p-12 text-center text-muted">
                    No active clients match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </AnimatedCard>
    </div>
  );
}
