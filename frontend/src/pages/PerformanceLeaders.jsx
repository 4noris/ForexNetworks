import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Radio, RefreshCw, Trophy, TrendingDown } from "lucide-react";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import { apiRequest, subscribeLiveData } from "../lib/api";

const PERIODS = [
  ["daily", "Daily"],
  ["weekly", "Weekly"],
  ["monthly", "Monthly"],
  ["overall", "Overall"],
];
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function Ranking({
  title,
  rows,
  winner,
  valueLabel = "Realized P&L",
  emptyMessage = "No closed-trade P&L in this period.",
}) {
  return (
    <AnimatedCard className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] p-4">
        {winner ? (
          <Trophy size={15} className="text-amber-400" />
        ) : (
          <TrendingDown size={15} className="text-danger" />
        )}
        <h3 className="text-xs font-semibold uppercase tracking-[.14em]">
          {title}
        </h3>
      </div>
      <div className="max-h-[420px] overflow-auto scrollbar-hidden">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
            <tr>
              {["#", "Client", "Group", "Trades", valueLabel].map((head) => (
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
            {(rows || []).map((row, index) => (
              <tr
                key={row.login}
                className="border-t border-[rgb(var(--color-border))]"
              >
                <td className="px-3 py-3 text-muted">{index + 1}</td>
                <td className="px-3 py-3">
                  <div className="font-medium">
                    {row.name || `Login ${row.login}`}
                  </div>
                  <div className="text-muted">#{row.login}</div>
                </td>
                <td
                  className="max-w-[180px] truncate px-3 py-3 text-muted"
                  title={row.group}
                >
                  {row.group}
                </td>
                <td className="px-3 py-3">{row.trades}</td>
                <td
                  className={`px-3 py-3 font-semibold ${row.pnl < 0 ? "text-danger" : "text-success"}`}
                >
                  {money.format(row.pnl)}
                </td>
              </tr>
            ))}
            {!rows?.length && (
              <tr>
                <td colSpan={5} className="p-10 text-center text-muted">
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </AnimatedCard>
  );
}

export default function PerformanceLeaders({
  profile,
  selectedGroups,
  liveAccounts = [],
  active = true,
}) {
  const [period, setPeriod] = useState("daily");
  const [snapshot, setData] = useState(null);
  const [dataScope, setDataScope] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clockMs, setClockMs] = useState(Date.now());
  const activeRequest = useRef(false);
  const requestController = useRef(null);
  // One 365-day scan contains every shorter period. Keeping the request key
  // stable means tab changes are instant and reuse the same verified result.
  const lookbackDays = 31;
  const storageKey = useMemo(
    () =>
      `performanceLeaders:${profile.id}:${[...selectedGroups].sort().join("|")}`,
    [profile.id, selectedGroups],
  );

  useEffect(() => {
    const timer = window.setInterval(() => setClockMs(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  const load = useCallback(async () => {
    if (!selectedGroups.length || activeRequest.current) return false;
    activeRequest.current = true;
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    setError("");
    try {
      const response = await apiRequest("/api/risk/performance", {
        method: "POST",
        signal: controller.signal,
        timeoutMs: 60_000,
        body: JSON.stringify({
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          selected_groups: selectedGroups,
          lookback_days: lookbackDays,
        }),
      });
      if (controller.signal.aborted || requestController.current !== controller) return false;
      if (response.status === "warming" || response.cache?.stale) { setData(null); return false; }
      setData(response);
      setDataScope(storageKey);
      return true;
    } catch (error) {
      if (!controller.signal.aborted && error.message !== "Request was cancelled")
        setError(error.message || "Unable to load P&L rankings");
      if (!controller.signal.aborted && error?.status !== 503 && error.message !== "Request was cancelled")
        toast.error(error.message || "Unable to load P&L rankings");
      return false;
    } finally {
      if (requestController.current === controller) {
        activeRequest.current = false;
        requestController.current = null;
        setLoading(false);
      }
    }
  }, [profile, selectedGroups, lookbackDays, storageKey]);

  const data = dataScope === storageKey ? snapshot : null;

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    let timer;
    const stopLive = subscribeLiveData("/api/risk/performance", {
      selected_groups: selectedGroups, lookback_days: lookbackDays,
    }, (response) => {
      if (cancelled || document.hidden) return;
      if (response.status === "warming" || response.cache?.stale) { setData(null); return; }
      setData(response);
      setDataScope(storageKey);
    });
    const run = async () => {
      const ready = await load();
      if (!cancelled) timer = window.setTimeout(run, ready ? 60_000 : 2_000);
    };
    run();
    return () => {
      cancelled = true;
      stopLive();
      window.clearTimeout(timer);
      requestController.current?.abort();
      requestController.current = null;
      activeRequest.current = false;
    };
  }, [active, profile.id, selectedGroups.join("|"), lookbackDays]); // eslint-disable-line react-hooks/exhaustive-deps

  const live = useMemo(
    () =>
      [...liveAccounts].sort(
        (a, b) => Number(b.floating_pnl || 0) - Number(a.floating_pnl || 0),
      ),
    [liveAccounts],
  );
  const currentData = data?.periods ? data : null;
  const generatedMs = Date.parse(currentData?.generated_at || "");
  const dataAgeSeconds = Number.isFinite(generatedMs)
    ? Math.max(0, Math.floor((clockMs - generatedMs) / 1_000))
    : null;
  const selected = currentData?.periods?.[period] || {};
  const quality = currentData?.data_quality || {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3">
        <div>
          <h2 className="text-sm font-semibold">P&L Winners & Losers</h2>
          <p className="mt-1 text-[11px] text-muted">
            Live floating P&L plus automatic realized rankings. Overall uses a
            rolling 31-day cached window.
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs text-success">
          <Radio size={14} className="animate-pulse" />
          {loading
            ? data
              ? "Refreshing realized rankings..."
              : "Preparing realized rankings..."
            : dataAgeSeconds === null
              ? "Waiting for MT5 realized history"
              : `Realized snapshot ${dataAgeSeconds}s old${currentData?.cache?.stale ? " · refreshing" : ""}`}
        </span>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Ranking
          title="Live floating winners"
          valueLabel="Live P&L"
          winner
          rows={live
            .filter((row) => row.floating_pnl > 0)
            .slice(0, 10)
            .map((row) => ({ ...row, pnl: row.floating_pnl }))}
        />
        <Ranking
          title="Live floating losers"
          valueLabel="Live P&L"
          rows={live
            .filter((row) => row.floating_pnl < 0)
            .slice(-10)
            .reverse()
            .map((row) => ({ ...row, pnl: row.floating_pnl }))}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {PERIODS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setPeriod(key)}
            className={`rounded-lg px-4 py-2 text-xs font-medium ${period === key ? "bg-white text-black" : "bg-[var(--inp-bg)] text-muted"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {!selectedGroups.length && (
        <AnimatedCard className="p-4 text-xs text-amber-400">
          Select at least one group to load realized P&amp;L rankings.
        </AnimatedCard>
      )}
      {error && (
        <AnimatedCard className="flex items-center justify-between gap-3 p-3 text-xs text-danger">
          <span>{error}</span>
          <button
            type="button"
            className="btn-ghost !px-3 !py-2"
            onClick={load}
          >
            <RefreshCw size={13} />
            Retry
          </button>
        </AnimatedCard>
      )}
      <div className="flex flex-wrap gap-2 text-[10px] text-muted">
        <span className="badge">
          Deals returned {currentData ? quality.deals_returned || 0 : "…"}
        </span>
        <span className="badge">
          Trade deals {currentData ? quality.trade_deals || 0 : "…"}
        </span>
        <span className="badge">
          Closing deals {currentData ? quality.closing_deals || 0 : "…"}
        </span>
        <span className="badge">
          Period timezone {quality.timezone || "UTC"}
        </span>
        {currentData && (
          <span
            className={quality.history_complete ? "badge" : "badge badge-amber"}
          >
            History {quality.history_days_loaded || lookbackDays}/{lookbackDays}{" "}
            days
          </span>
        )}
        {quality.closing_deals_missing_timestamp > 0 && (
          <span className="badge badge-amber">
            Missing timestamps {quality.closing_deals_missing_timestamp}
          </span>
        )}
      </div>
      {(currentData?.warnings || []).length > 0 && (
        <AnimatedCard className="p-3 text-xs text-amber-400">
          {currentData.warnings.join(" · ")}
        </AnimatedCard>
      )}
      <AnimatedCard className="p-4">
        <p className="text-[10px] uppercase tracking-[.18em] text-muted">
          {period} net realized P&L
        </p>
        <p
          className={`mt-2 text-2xl font-semibold ${selected.net_pnl < 0 ? "text-danger" : "text-success"}`}
        >
          {currentData
            ? money.format(selected.net_pnl || 0)
            : "Loading realized history…"}
        </p>
      </AnimatedCard>
      <div className="grid gap-4 xl:grid-cols-2">
        <Ranking
          title={`${period} winners`}
          winner
          rows={selected.winners}
          emptyMessage={
            !currentData && loading
              ? "Loading closed-trade history…"
              : undefined
          }
        />
        <Ranking
          title={`${period} losers`}
          rows={selected.losers}
          emptyMessage={
            !currentData && loading
              ? "Loading closed-trade history…"
              : undefined
          }
        />
      </div>
    </div>
  );
}
