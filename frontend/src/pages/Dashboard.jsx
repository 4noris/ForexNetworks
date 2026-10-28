import { initialBookGroups } from "../lib/bookDefaults";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import gsap from "gsap";
import KPICard from "../components/KPICard";
import AnimatedCard from "../components/AnimatedCard";
import GroupTreeSelect from "../components/GroupTreeSelect";
import {
  Users,
  DollarSign,
  Activity,
  Briefcase,
  RefreshCw,
  Download,
} from "lucide-react";
import toast from "react-hot-toast";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import SafeResponsiveContainer from "../components/SafeResponsiveContainer";
import { formatTradeCount, getTradeBreakdown } from "../lib/utils";
import {
  apiRequest,
  downloadDealReport,
  fetchCompleteMt5Groups,
  getBookGroupSettings,
  getCachedGroupsForProfile,
  storeCachedGroupsForProfile,
  useStoredProfile,
} from "../lib/api";

const USD_NUMBER_FORMAT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatCurrency(value) {
  const numeric = Number(value || 0);
  const safeNumeric = Number.isFinite(numeric) ? numeric : 0;
  return `${safeNumeric < 0 ? "-" : ""}$${USD_NUMBER_FORMAT.format(Math.abs(safeNumeric))}`;
}

function AccountListCard({ title, accounts, delay = 0.3, emptyMessage }) {
  return (
    <AnimatedCard className="p-5" delay={delay}>
      <h3
        className="mb-4 text-sm font-semibold uppercase tracking-[0.12em]"
        style={{ color: "rgb(var(--color-text))" }}
      >
        {title}
      </h3>
      <div className="space-y-2">
        {accounts.map((account, index) => {
          const pnl = Number(account["NET PNL USD"] || 0);
          const login = account.Login ?? "-";
          const { winningTrades, losingTrades, totalTrades } =
            getTradeBreakdown(account);
          return (
            <div
              key={`${title}-${login}`}
              className="flex items-center justify-between gap-3 rounded-xl p-3"
              style={{
                background: "var(--inp-bg)",
                border: "1px solid var(--inp-border)",
              }}
            >
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <div
                  className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
                  style={{
                    background: "var(--btn-ghost-hover)",
                    color: "rgb(var(--color-text-2))",
                  }}
                >
                  {index + 1}
                </div>
                <div className="min-w-0 flex-1">
                  <div
                    className="truncate text-sm font-medium"
                    style={{ color: "rgb(var(--color-text))" }}
                  >
                    {account.Name || `Login ${login}`}
                  </div>
                  <div
                    className="mt-0.5 flex flex-wrap items-center gap-2 text-xs"
                    style={{ color: "rgb(var(--color-text-3))" }}
                  >
                    <span>#{login}</span>
                    <span className="opacity-60">|</span>
                    <span>{account.Group || "No group"}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="badge badge-success text-[9px] px-1.5 py-0.5">
                      W {formatTradeCount(winningTrades)}
                    </span>
                    <span className="badge badge-danger text-[9px] px-1.5 py-0.5">
                      L {formatTradeCount(losingTrades)}
                    </span>
                    <span className="badge text-[9px] px-1.5 py-0.5">
                      {formatTradeCount(totalTrades)} trades
                    </span>
                  </div>
                </div>
              </div>
              <div
                className={`shrink-0 text-sm font-semibold tabular-nums ${pnl >= 0 ? "text-success" : "text-danger"}`}
              >
                {formatCurrency(pnl)}
              </div>
            </div>
          );
        })}
        {accounts.length === 0 && (
          <p className="text-sm" style={{ color: "rgb(var(--color-text-3))" }}>
            {emptyMessage}
          </p>
        )}
      </div>
    </AnimatedCard>
  );
}

export default function Dashboard() {
  const containerRef = useRef(null);
  const [availableGroups, setAvailableGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [fromDate, setFromDate] = useState(
    new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString().split("T")[0],
  );
  const [toDate, setToDate] = useState(new Date().toISOString().split("T")[0]);
  const [matchEquityPeriod, setMatchEquityPeriod] = useState(true);
  const [report, setReport] = useState({
    kpis: {},
    client_report: [],
    groups: [],
    warnings: [],
  });
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const profile = useStoredProfile();

  const loadGroups = useCallback(
    async ({ refresh = false } = {}) => {
      if (!profile) return [];
      const cachedGroups = getCachedGroupsForProfile(profile.id);
      if (!refresh && cachedGroups.length > 0) setAvailableGroups(cachedGroups);

      setLoadingGroups(true);
      try {
        const nextGroups = await fetchCompleteMt5Groups(profile);
        setAvailableGroups(nextGroups);
        storeCachedGroupsForProfile(profile.id, nextGroups);
        return nextGroups;
      } catch (error) {
        if (!refresh && cachedGroups.length > 0) {
          setAvailableGroups(cachedGroups);
          return cachedGroups;
        }
        throw error;
      } finally {
        setLoadingGroups(false);
      }
    },
    [profile],
  );

  const loadData = async (groupsOverride = []) => {
    if (!profile) return;
    if (fromDate > toDate || (matchEquityPeriod && fromDate === toDate)) {
      toast.error("Choose an opening date before the closing date.");
      return;
    }
    const effectiveFrom = matchEquityPeriod
      ? new Date(Date.parse(`${fromDate}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
      : fromDate;
    setLoading(true);
    try {
      const resolvedGroups =
        groupsOverride.length > 0
          ? groupsOverride
          : selectedGroups.length > 0
            ? selectedGroups
            : availableGroups;
      const data = await apiRequest("/api/reports/deal", {
        method: "POST",
        body: JSON.stringify({
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          from_date: effectiveFrom,
          to_date: toDate,
          selected_groups: resolvedGroups,
        }),
      });
      setReport(data);
    } catch (error) {
      toast.error(error.message || "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  };

  const downloadPnlReport = async () => {
    if (!profile) return;
    if (fromDate > toDate || (matchEquityPeriod && fromDate === toDate)) {
      toast.error("Choose an opening date before the closing date.");
      return;
    }
    const resolvedGroups =
      selectedGroups.length > 0 ? selectedGroups : availableGroups;
    setExporting(true);
    try {
      const effectiveFrom = matchEquityPeriod
        ? new Date(Date.parse(`${fromDate}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
        : fromDate;
      const { blob, filename } = await downloadDealReport({
        server: profile.server,
        login: profile.mt5_login,
        password: profile.mt5_password,
        from_date: effectiveFrom,
        to_date: toDate,
        selected_groups: resolvedGroups,
        group_filter_rules: "",
        cached_report: report,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || `PnL_${fromDate}_to_${toDate}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error.message || "Failed to download PnL report");
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    gsap.fromTo(
      containerRef.current,
      { opacity: 0, y: 16 },
      { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" },
    );
  }, []);

  useEffect(() => {
    if (!profile) {
      setAvailableGroups([]);
      setSelectedGroups([]);
      setReport({ kpis: {}, client_report: [], groups: [], warnings: [] });
      setLoadingGroups(false);
      setLoading(false);
      setExporting(false);
      return;
    }
    let alive = true;
    (async () => {
      try {
        setAvailableGroups([]);
        setSelectedGroups([]);
        setReport({ kpis: {}, client_report: [], groups: [], warnings: [] });
        const [nextGroups, defaultBooks] = await Promise.all([loadGroups({ refresh: true }), getBookGroupSettings()]);
        if (!alive) return;
        setSelectedGroups(initialBookGroups(nextGroups, defaultBooks));
        // Data is now loaded manually by user clicking Run Report
      } catch (error) {
        if (!alive) return;
        toast.error(error.message || "Failed to load dashboard");
      }
    })();
    return () => {
      alive = false;
    };
  }, [profile, loadGroups]);

  useEffect(() => {
    if (!profile) return undefined;
    let active = true;
    const refreshCatalog = async () => {
      try {
        const groups = await fetchCompleteMt5Groups(profile);
        if (!active) return;
        setAvailableGroups(groups);
        storeCachedGroupsForProfile(profile.id, groups);
      } catch {
        // Preserve the last catalog while the Manager is temporarily unavailable.
      }
    };
    const timer = window.setInterval(refreshCatalog, 15000);
    window.addEventListener("focus", refreshCatalog);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshCatalog);
    };
  }, [profile]);

  const topGroups = (report.groups || []).slice(0, 8).map((g) => ({
    name: g.Group || "N/A",
    pnl: Number(g["NET PNL USD"] || 0),
  }));
  const sortedAccounts = useMemo(
    () =>
      [...(report.client_report || [])].sort(
        (l, r) => Number(r["NET PNL USD"] || 0) - Number(l["NET PNL USD"] || 0),
      ),
    [report.client_report],
  );
  const topAccounts = sortedAccounts.slice(0, 5);
  const loserAccounts = [...sortedAccounts].reverse().slice(0, 5);

  if (!profile) {
    return (
      <div ref={containerRef} className="max-w-5xl mx-auto">
        <AnimatedCard className="p-8">
          <h1
            className="text-xl font-semibold"
            style={{ color: "rgb(var(--color-text))" }}
          >
            Overview Dashboard
          </h1>
          <p
            className="mt-1 text-sm"
            style={{ color: "rgb(var(--color-text-3))" }}
          >
            Select or create an MT5 profile in Settings to start loading live
            data.
          </p>
        </AnimatedCard>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="space-y-6 max-w-7xl mx-auto">
      {/* Header row */}
      <div className="flex flex-col xl:flex-row xl:items-end xl:justify-between gap-4">
        <div>
          <h1
            className="text-xl font-semibold"
            style={{ color: "rgb(var(--color-text))" }}
          >
            Overview Dashboard
          </h1>
          <p
            className="mt-0.5 text-sm"
            style={{ color: "rgb(var(--color-text-3))" }}
          >
            Live MT5 deal analytics · {profile.name}
          </p>
        </div>

        <div className="flex flex-col lg:flex-row gap-2">
          {/* Date range */}
          <div
            className="flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm"
            style={{
              background: "var(--inp-bg)",
              border: "1px solid var(--inp-border)",
              color: "rgb(var(--color-text))",
            }}
          >
            <input
              type="date"
              value={fromDate}
              onChange={(e) => { setFromDate(e.target.value); setReport({ kpis: {}, client_report: [], groups: [], warnings: [] }); }}
              className="bg-transparent outline-none"
              style={{ color: "rgb(var(--color-text))" }}
            />
            <span style={{ color: "rgb(var(--color-text-3))" }}>→</span>
            <input
              type="date"
              value={toDate}
              onChange={(e) => { setToDate(e.target.value); setReport({ kpis: {}, client_report: [], groups: [], warnings: [] }); }}
              className="bg-transparent outline-none"
              style={{ color: "rgb(var(--color-text))" }}
            />
          </div>

          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={matchEquityPeriod} onChange={(event) => { setMatchEquityPeriod(event.target.checked); setReport({ kpis: {}, client_report: [], groups: [], warnings: [] }); }} />
            Match Brokerage equity interval (deals after opening date)
          </label>

          <GroupTreeSelect
            groups={availableGroups}
            value={selectedGroups}
            onChange={(groups) => { setSelectedGroups(groups); setReport({ kpis: {}, client_report: [], groups: [], warnings: [] }); }}
            className="w-full lg:w-[340px]"
          />

          <button
            onClick={() => loadData()}
            disabled={loading || loadingGroups}
            className="btn-primary"
          >
            <RefreshCw
              size={15}
              className={loading || loadingGroups ? "animate-spin" : ""}
            />
            {loading || loadingGroups ? "Loading…" : "Run Report"}
          </button>

          <button
            onClick={downloadPnlReport}
            disabled={loading || loadingGroups || exporting}
            className="btn-ghost"
          >
            <Download size={15} className={exporting ? "animate-bounce" : ""} />
            {exporting ? "Exporting…" : "Export"}
          </button>
        </div>
      </div>

      <p className="text-xs text-muted">
        {matchEquityPeriod ? "Deals start the day after the opening equity date." : "Deals include the first selected date."}
        {" "}MT5 trade profit excludes posted commission, fee, and swap; net trading P&amp;L includes them. Equity P&amp;L also includes floating changes and funding adjustments.
      </p>

      {/* Loading banner */}
      {loadingGroups && (
        <AnimatedCard className="px-4 py-3">
          <p className="text-sm" style={{ color: "rgb(var(--color-text-2))" }}>
            Loading group hierarchy from MT5 Manager…
          </p>
        </AnimatedCard>
      )}

      {/* Warnings */}
      {report.warnings?.length > 0 && (
        <AnimatedCard className="px-4 py-3">
          <p className="text-sm text-amber-400">
            {report.warnings.join(" | ")}
          </p>
        </AnimatedCard>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
        <KPICard
          title="Total Clients"
          value={String(report.kpis?.total_clients || 0)}
          icon={Users}
          delay={0.05}
        />
        <KPICard
          title="MT5 Trade Profit"
          value={formatCurrency(report.kpis?.trade_profit)}
          icon={DollarSign}
          delay={0.1}
        />
        <KPICard
          title="Net Trading P&L"
          value={formatCurrency(report.kpis?.total_pnl)}
          icon={DollarSign}
          delay={0.1}
        />
        <KPICard
          title="Closed Lots"
          value={String((report.kpis?.total_lots || 0).toFixed(2))}
          icon={Activity}
          delay={0.15}
        />
        <KPICard
          title="Total Trades"
          value={String(report.kpis?.total_trades || 0)}
          icon={Briefcase}
          delay={0.2}
        />
      </div>

      {/* Chart + account lists */}
      <div className="grid grid-cols-1 xl:grid-cols-2 items-start gap-4">
        <AnimatedCard className="p-5 xl:col-span-2" delay={0.25}>
          <h3
            className="mb-5 text-sm font-semibold uppercase tracking-[0.12em]"
            style={{ color: "rgb(var(--color-text))" }}
          >
            Group P&L
          </h3>
          <div className="h-72 w-full">
            <SafeResponsiveContainer>
              <BarChart data={topGroups}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="rgb(var(--color-border))"
                  vertical={false}
                />
                <XAxis
                  dataKey="name"
                  stroke="rgb(var(--color-text-3))"
                  tick={{ fill: "rgb(var(--color-text-3))", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  stroke="rgb(var(--color-text-3))"
                  tick={{ fill: "rgb(var(--color-text-3))", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "rgb(var(--color-card))",
                    borderColor: "rgb(var(--color-border))",
                    borderRadius: "10px",
                    color: "rgb(var(--color-text))",
                    boxShadow: "var(--card-shadow)",
                  }}
                  formatter={(v) => formatCurrency(v)}
                />
                <Bar
                  dataKey="pnl"
                  fill="rgb(var(--chart-accent))"
                  radius={[6, 6, 0, 0]}
                />
              </BarChart>
            </SafeResponsiveContainer>
          </div>
        </AnimatedCard>

        <AccountListCard
          title="Top Accounts"
          accounts={topAccounts}
          delay={0.3}
          emptyMessage="No live accounts found for the current filters."
        />
        <AccountListCard
          title="Losers Accounts"
          accounts={loserAccounts}
          delay={0.35}
          emptyMessage="No losing accounts found for the current filters."
        />
      </div>
    </div>
  );
}
