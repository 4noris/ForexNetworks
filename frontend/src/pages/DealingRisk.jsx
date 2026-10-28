import React, {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation } from "react-router-dom";
import WorldNetworkMap from "../components/WorldNetworkMap";
import { subscribeLiveData } from "../lib/api";
import {
  Activity,
  AlertTriangle,
  DollarSign,
  MapPin,
  Server,
  ShieldAlert,
  Table2,
  TimerReset,
  TrendingDown,
  Users,
  CheckCheck,
  X,
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
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import LiveBookTable from "../components/LiveBookTable";
import { currentLiveSnapshot, mergeLiveSnapshot } from "../lib/liveFreshness";
import AppErrorBoundary from "../components/AppErrorBoundary";
import AnimatedNumber from "../components/AnimatedNumber";
const ClientAnalyser = lazy(() => import("./ClientAnalyser"));
const MarkoutWorkspace = lazy(() => import("./MarkoutWorkspace"));
const AntiFraud = lazy(() => import("./AntiFraud"));
const PerformanceLeaders = lazy(() => import("./PerformanceLeaders"));
const ExposureDashboard = lazy(() => import("./ExposureDashboard"));
const BookSuggestions = lazy(() => import("./BookSuggestions"));
const preloadRiskTab = {
  performance: () => import("./PerformanceLeaders"),
  analyser: () => import("./ClientAnalyser"),
  markout: () => import("./MarkoutWorkspace"),
  exposure: () => import("./ExposureDashboard"),
  suggestions: () => import("./BookSuggestions"),
  antifraud: () => import("./AntiFraud"),
};
const warmRiskTab = (tab) => {
  preloadRiskTab[tab]?.().catch(() => {});
};
import GroupTreeSelect from "../components/GroupTreeSelect";
import { initialBookGroups } from "../lib/bookDefaults";
import { appendLiveMoneySample, currencyMovement, liveMoneyView } from "../lib/liveMoney";
import {
  apiRequest,
  fetchCompleteMt5Groups,
  getBookGroupSettings,
  getCachedGroupsForProfile,
  getDefaultLiveGroupForProfile,
  getStoredUser,
  storeCachedGroupsForProfile,
  syncGoogleSheets,
  useStoredProfile,
} from "../lib/api";

const EMPTY_SNAPSHOT = {
  summary: {},
  accounts: [],
  symbols: [],
  positions: [],
  network: {},
  warnings: [],
};

const money = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const lotsNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });

function moneyText(value) {
  const parsed = Number(value || 0);
  return money.format(Number.isFinite(parsed) ? parsed : 0);
}

function LineLoader({ active }) {
  if (!active) return null;
  return (
    <div className="line-loader" aria-label="Loading live MT5 data">
      <span />
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
  tone = "",
  formatter = number,
  prefix = "",
  suffix = "",
  emptyLabel = "Unavailable",
  currency = "",
}) {
  const CardIcon = icon;
  return (
    <AnimatedCard className="risk-stat-card min-w-0 overflow-hidden p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 text-[10px] uppercase tracking-[0.16em] text-muted">
          {label}
        </p>
        <div className="risk-stat-icon shrink-0 rounded-lg border border-[rgb(var(--color-border))] p-2 text-muted">
          <CardIcon size={16} />
        </div>
      </div>
      <p
        className={`mt-2 whitespace-nowrap text-lg font-semibold tracking-[-0.035em] tabular-nums 2xl:text-xl ${tone}`}
      >
        {value == null ? <span className="text-muted">{emptyLabel}</span> : <AnimatedNumber
          key={currency}
          value={value}
          formatter={(next) => formatter.format(next)}
          prefix={prefix}
          suffix={suffix}
        />}
      </p>
    </AnimatedCard>
  );
}

function severityClass(severity) {
  if (severity === "critical" || severity === "high")
    return "badge badge-danger";
  if (severity === "elevated") return "badge badge-amber";
  return "badge badge-success";
}

function NetworkCell({ row }) {
  const statusClass =
    row.vps_status === "detected"
      ? "badge badge-danger"
      : row.vps_status === "not_detected"
        ? "badge badge-success"
        : "badge";
  const statusText =
    row.vps_status === "detected"
      ? `VPS ${row.vps_confidence}%`
      : String(row.vps_status || "unknown").replace("_", " ");
  return (
    <td className="min-w-[240px] px-4 py-3">
      <div className="flex items-center gap-2">
        <span>{row.last_ip || "Unavailable"}</span>
        <span className={statusClass}>{statusText}</span>
      </div>
      <div className="mt-1 text-[10px] text-muted">
        {row.location || "Location unavailable"} ·{" "}
        {row.provider || "Provider unavailable"} {row.asn || ""}
      </div>
      <div className="mt-1 text-[10px] text-muted">
        {row.shared_ip_accounts > 1
          ? `${row.shared_ip_accounts} accounts share IP · `
          : ""}
        {(row.vps_evidence || []).join(" · ")}
      </div>
    </td>
  );
}

export default function DealingRisk() {
  const pageActive = useLocation().pathname === "/dealing-risk";
  const profile = useStoredProfile();
  const [availableGroups, setAvailableGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [liveFilters, setLiveFilters] = useState({ account_login: "", symbol: "", risk_category: "" });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filterCount = Object.values(liveFilters).filter(Boolean).length;
  const validLiveLogin = !liveFilters.account_login || (Number.isSafeInteger(Number(liveFilters.account_login)) && Number(liveFilters.account_login) > 0);
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);
  const [movement, setMovement] = useState([]);
  const [financialCurrency, setFinancialCurrency] = useState("USD");
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [clockMs, setClockMs] = useState(Date.now());
  const [activeTab, setActiveTab] = useState(() => {
    const tab = new URLSearchParams(window.location.search).get("tab");
    return ["analyser", "markout", "exposure"].includes(tab) ? tab : "monitor";
  });
  const [visitedTabs, setVisitedTabs] = useState(() => new Set(["monitor", activeTab]));
  const initialClientLogin = new URLSearchParams(window.location.search).get("login") || "";
  const initialClientGroup = new URLSearchParams(window.location.search).get("group") || "";
  const [vpsModalOpen, setVpsModalOpen] = useState(false);
  const [syncingSheet, setSyncingSheet] = useState(false);
  const selectTab = (tab) => {
    setVisitedTabs((current) => current.has(tab) ? current : new Set([...current, tab]));
    setActiveTab(tab);
  };
  const activeRequest = useRef(false);
  const liveController = useRef(null);
  const liveGeneration = useRef(0);
  const lastCacheSource = useRef("");
  const snapshotScope = useRef("");
  const refreshIntervals = useRef({ live: 5000, operational: 30000 });
  const refreshConfig = useQuery({
    queryKey: ["dealing-risk-config"],
    queryFn: ({ signal }) => apiRequest("/api/dealing-risk/config", { signal }),
    staleTime: 60000,
    refetchInterval: 60000,
  });
  useEffect(() => {
    refreshIntervals.current = {
      live: Math.max(5000, Number(refreshConfig.data?.live_refresh_seconds || 5) * 1000),
      operational: Math.max(15000, Number(refreshConfig.data?.operational_refresh_seconds || 30) * 1000),
    };
  }, [refreshConfig.data]);

  useEffect(() => {
    const timer = window.setInterval(() => setClockMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const sourceTime = snapshot.source_updated_at;
  const sourceMs = typeof sourceTime === "number" ? sourceTime * 1_000 : Date.parse(sourceTime || "");
  const sourceAge = Number.isFinite(sourceMs) ? Math.max(0, Math.floor((clockMs - sourceMs) / 1_000)) : null;
  const sourceInterrupted = ["warming", "offline", "disconnected", "error"].includes(snapshot.status);

  const loadGroups = useCallback(async () => {
    if (!profile) return [];
    const cached = getCachedGroupsForProfile(profile.id);
    if (cached.length) setAvailableGroups(cached);
    // A cached catalog is immediately interactive while its refresh happens.
    setLoadingGroups(cached.length === 0);
    try {
      const [groups, bookSettings] = await Promise.all([
        fetchCompleteMt5Groups(profile),
        getBookGroupSettings(),
      ]);
      setAvailableGroups(groups);
      storeCachedGroupsForProfile(profile.id, groups);
      setSelectedGroups((current) => {
        const retained = current.filter((group) => groups.includes(group));
        if (retained.length) return retained;
        return initialBookGroups(groups, bookSettings, initialClientGroup,
          getDefaultLiveGroupForProfile(profile.id));
      });
      return groups;
    } finally {
      setLoadingGroups(false);
    }
  }, [profile, initialClientGroup]);

  const requestSnapshot = useCallback(
    async (groups) => {
      if (!profile || activeRequest.current || !groups.length || !validLiveLogin) return false;
      activeRequest.current = true;
      const generation = ++liveGeneration.current;
      const controller = new AbortController();
      liveController.current = controller;
      setRefreshing(true);
      try {
        const data = await apiRequest("/api/risk/live", {
          method: "POST",
          signal: controller.signal,
          timeoutMs: 8000,
          body: JSON.stringify({
            server: profile.server,
            login: profile.mt5_login,
            password: profile.mt5_password,
            selected_groups: groups,
            excluded_logins: [],
            group_filter_rules: "",
            account_login: liveFilters.account_login ? Number(liveFilters.account_login) : null,
            symbol: liveFilters.symbol,
            risk_category: liveFilters.risk_category,
          }),
        });
        if (generation !== liveGeneration.current) return false;
        lastCacheSource.current = data?.cache?.source || "";
        const interrupted = ["warming", "offline", "disconnected", "error"].includes(data?.status);
        setSnapshot((current) => mergeLiveSnapshot(current, data || EMPTY_SNAPSHOT));
        if (interrupted) return false;
        setMovement((current) => appendLiveMoneySample(current, data));
        return true;
      } catch (error) {
        const cancelled =
          error?.name === "AbortError" ||
          /(?:abort|cancel)/i.test(String(error?.message || ""));
        if (!cancelled)
          toast.error(error.message || "Live risk refresh failed");
        return false;
      } finally {
        if (liveController.current === controller) {
          activeRequest.current = false;
          liveController.current = null;
          setRefreshing(false);
        }
      }
    },
    [profile, liveFilters, validLiveLogin],
  );

  const refresh = useCallback(
    async (groupsOverride) => {
      const groups =
        Array.isArray(groupsOverride) && groupsOverride.length
          ? groupsOverride
          : selectedGroups;
      return requestSnapshot(groups);
    },
    [requestSnapshot, selectedGroups],
  );

  const syncSheet = useCallback(async () => {
    if (!snapshot.generated_at || syncingSheet) return;
    setSyncingSheet(true);
    try {
      const result = await syncGoogleSheets(snapshot);
      toast.success(
        `Google Sheet updated: ${result.tabs.accounts} accounts, ${result.tabs.open_positions} positions, ${result.tabs.frequently_toxic} frequent toxic`,
      );
    } catch (error) {
      toast.error(error.message || "Google Sheets sync failed");
    } finally {
      setSyncingSheet(false);
    }
  }, [snapshot, syncingSheet]);

  useEffect(() => {
    if (!profile) {
      setAvailableGroups([]);
      setSelectedGroups([]);
      setSnapshot(EMPTY_SNAPSHOT);
      setMovement([]);
      return;
    }
    setSelectedGroups([]);
    setSnapshot(EMPTY_SNAPSHOT);
    setMovement([]);
    let active = true;
    const cachedGroups = getCachedGroupsForProfile(profile.id);
    if (cachedGroups.length) {
      setAvailableGroups(cachedGroups);
    }
    loadGroups().catch(
        (error) =>
          active &&
          toast.error(error.message || "Unable to initialize live risk"),
      );
    return () => {
      active = false;
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
        // Keep the last catalog during transient Manager failures.
      }
    };
    const timer = window.setInterval(refreshCatalog, refreshIntervals.current.operational);
    window.addEventListener("focus", refreshCatalog);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshCatalog);
    };
  }, [profile]);

  useEffect(() => {
    if (!profile || !selectedGroups.length || !pageActive || activeTab === "exposure") return undefined;
    let cancelled = false;
    let refreshTimer;
    let lastPushedAt = 0;
    const scope = JSON.stringify([profile.id, profile.server, profile.mt5_login, [...selectedGroups].sort(), liveFilters]);
    if (snapshotScope.current !== scope) {
      snapshotScope.current = scope;
      setSnapshot(EMPTY_SNAPSHOT);
      setMovement([]);
    }
    const schedule = (delay) => {
      window.clearTimeout(refreshTimer);
      if (!cancelled) refreshTimer = window.setTimeout(run, delay);
    };
    const run = async () => {
      if (cancelled) return;
      if (Date.now() - lastPushedAt < 3000) { schedule(5000); return; }
      if (document.hidden) {
        schedule(15_000);
        return;
      }
      if (activeRequest.current) {
        schedule(1_000);
        return;
      }
      const ready = await refresh();
      if (cancelled) return;
      const count = selectedGroups.length;
      const focusedDelay = count >= 200 ? 20_000 : count >= 50 ? 10_000 : count >= 10 ? 5_000 : 2_000;
      schedule(ready && lastCacheSource.current !== "manager" ? Math.max(refreshIntervals.current.live, focusedDelay) : refreshIntervals.current.live);
    };
    const onVisible = () => {
      if (!document.hidden) schedule(0);
    };
    const onSectionRefresh = (event) => {
      if (event.detail?.path === "/dealing-risk") schedule(0);
    };
    document.addEventListener("visibilitychange", onVisible);
    const stopLive = subscribeLiveData("/api/risk/live", {
      selected_groups: selectedGroups, excluded_logins: [], group_filter_rules: "",
      account_login: liveFilters.account_login ? Number(liveFilters.account_login) : null,
      symbol: liveFilters.symbol, risk_category: liveFilters.risk_category,
    }, (data) => {
      if (cancelled || document.hidden) return;
      if (currentLiveSnapshot(data)) {
        lastPushedAt = Date.now();
        if (liveController.current) {
          liveGeneration.current += 1;
          liveController.current.abort();
          liveController.current = null;
          activeRequest.current = false;
          setRefreshing(false);
        }
      }
      const interrupted = ["warming", "offline", "disconnected", "error"].includes(data?.status);
      setSnapshot((current) => mergeLiveSnapshot(current, data || EMPTY_SNAPSHOT));
      if (interrupted) return;
      setMovement((current) => appendLiveMoneySample(current, data));
    });
    window.addEventListener("rms:refresh-section", onSectionRefresh);
    // Give the initial stream snapshot time to arrive before a fallback RPC.
    if (import.meta.env.VITE_LIVE_STREAM === "false") run();
    else schedule(1500);
    return () => {
      cancelled = true;
      stopLive();
      window.clearTimeout(refreshTimer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("rms:refresh-section", onSectionRefresh);
      liveGeneration.current += 1;
      liveController.current?.abort();
      liveController.current = null;
      activeRequest.current = false;
    };
  }, [profile, selectedGroups, refresh, pageActive, liveFilters, activeTab]);

  const changeSelectedGroups = useCallback((groups) => {
    setSelectedGroups(groups);
  }, []);

  const topSymbols = useMemo(
    () => (snapshot.symbols || []).slice(0, 12),
    [snapshot.symbols],
  );
  const alerts = useMemo(
    () =>
      (snapshot.accounts || [])
        .filter((row) => row.severity !== "normal" && !row.noticed)
        .sort((a, b) => b.risk_score - a.risk_score || a.floating_pnl - b.floating_pnl)
        .slice(0, 100),
    [snapshot.accounts],
  );
  const summary = snapshot.summary || {};
  const moneyView = liveMoneyView(summary, financialCurrency);
  const currency = moneyView.currency;
  const financialValue = (key) => moneyView.valid ? moneyView.totals[key] : null;
  const displayedMovement = currencyMovement(movement, currency);
  const highLotPositions = useMemo(
    () => [...(snapshot.large_positions || [])].sort((a, b) => b.volume - a.volume || a.profit - b.profit),
    [snapshot.large_positions],
  );
  const network = snapshot.network || {};
  const vpsAccounts = useMemo(
    () =>
      (snapshot.accounts || []).filter((row) => row.vps_status === "detected"),
    [snapshot.accounts],
  );

  const markNoticed = useCallback(async (row) => {
    if (!row.alert_key) return;
    const setNotice = (noticed) =>
      setSnapshot((current) => ({
        ...current,
        accounts: (current.accounts || []).map((account) =>
          account.alert_key === row.alert_key
            ? { ...account, noticed }
            : account,
        ),
      }));
    setNotice(true);
    try {
      await apiRequest("/api/risk/alerts/notice", {
        method: "POST",
        body: JSON.stringify({ alert_key: row.alert_key }),
      });
      toast.success("Activity marked noticed for 48 hours");
    } catch (error) {
      setNotice(false);
      toast.error(error.message || "Unable to mark activity as noticed");
    }
  }, []);

  if (!profile) {
    return (
      <AnimatedCard className="mx-auto max-w-4xl p-8">
        <h1 className="text-xl font-semibold">Dealing and Risk</h1>
        <p className="mt-2 text-secondary">Select an MT5 profile first.</p>
      </AnimatedCard>
    );
  }

  return (
    <div className="risk-scrollbars-hidden risk-monitor-compact mx-auto max-w-[1600px] space-y-4">
      <div className="risk-page-header">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="risk-page-icon"><ShieldAlert size={18} /></span>
            <div>
              <h1 className="text-xl font-semibold tracking-tight">Dealing and Risk</h1>
            </div>
          </div>
        </div>
      </div>

      <div className="risk-toolbar flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="risk-tabs inline-flex min-w-0 max-w-full flex-1 overflow-x-auto rounded-lg bg-[var(--inp-bg)] p-1">
          <Link to="/risk-center" className="risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium text-muted hover:text-primary">Risk Center</Link>
          <button
            type="button"
            onClick={() => selectTab("monitor")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "monitor" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            Live Monitor
          </button>
          <button
            type="button"
            onClick={() => selectTab("performance")}
            onMouseEnter={() => warmRiskTab("performance")}
            onFocus={() => warmRiskTab("performance")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "performance" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            P&L Leaders
          </button>
          <button
            type="button"
            onClick={() => selectTab("analyser")}
            onMouseEnter={() => warmRiskTab("analyser")}
            onFocus={() => warmRiskTab("analyser")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "analyser" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            Analyser
          </button>
          <button
            type="button"
            onClick={() => selectTab("markout")}
            onMouseEnter={() => warmRiskTab("markout")}
            onFocus={() => warmRiskTab("markout")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "markout" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            Markout
          </button>
          <button
            type="button"
            onClick={() => selectTab("exposure")}
            onMouseEnter={() => warmRiskTab("exposure")}
            onFocus={() => warmRiskTab("exposure")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "exposure" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            Exposure
          </button>
          <button
            type="button"
            onClick={() => selectTab("suggestions")}
            onMouseEnter={() => warmRiskTab("suggestions")}
            onFocus={() => warmRiskTab("suggestions")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "suggestions" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            Book Suggestions
          </button>
          <button
            type="button"
            onClick={() => selectTab("antifraud")}
            onMouseEnter={() => warmRiskTab("antifraud")}
            onFocus={() => warmRiskTab("antifraud")}
            className={`risk-tab whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition ${activeTab === "antifraud" ? "is-active" : "text-muted hover:text-primary"}`}
          >
            Anti-Fraud
          </button>
        </div>
        <div className="flex w-full min-w-0 items-center gap-2 lg:w-auto lg:shrink-0">
          {activeTab === "monitor" && <button type="button" className="btn-secondary shrink-0 px-3 py-2 text-xs" aria-expanded={filtersOpen} aria-controls="live-risk-filters" onClick={() => setFiltersOpen((open) => !open)}>Filters{filterCount ? ` · ${filterCount}` : ""}</button>}
          {activeTab !== "exposure" && getStoredUser()?.is_admin && (
            <button
              type="button"
              onClick={syncSheet}
              disabled={!snapshot.generated_at || syncingSheet}
              className="btn-ghost shrink-0 px-3 py-2 text-xs"
              title="Update the configured Google Sheet with this live snapshot"
            >
              <Table2 size={14} className={syncingSheet ? "animate-pulse" : ""} />
              {syncingSheet ? "Syncing…" : "Sync Sheet"}
            </button>
          )}
          <GroupTreeSelect
            groups={availableGroups}
            value={selectedGroups}
            onChange={changeSelectedGroups}
            className="min-w-0 flex-1 lg:w-[240px] lg:flex-none"
            label="Groups"
            emptyMeansAll={false}
            replaceOnSelect
          />
        </div>
      </div>

      {activeTab === "monitor" && <div id="live-risk-filters" hidden={!filtersOpen} className="risk-filter-panel rounded-xl border border-[rgb(var(--color-border))] p-4">
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
        <label className="text-xs text-muted">Account login<input className="field-input mt-1 !w-36" type="number" min={1} step={1} value={liveFilters.account_login} aria-label="Live account login" onChange={(event) => setLiveFilters((current) => ({ ...current, account_login: event.target.value }))} /></label>
        <label className="text-xs text-muted">Symbol<input className="field-input mt-1 !w-36" list="live-risk-symbols" value={liveFilters.symbol} aria-label="Live symbol" onChange={(event) => setLiveFilters((current) => ({ ...current, symbol: event.target.value.trim() }))} /></label>
        <datalist id="live-risk-symbols">{(snapshot.symbols || []).map((row) => <option key={row.symbol} value={row.symbol} />)}</datalist>
        <label className="text-xs text-muted">Risk category<select className="field-input mt-1" aria-label="Live risk category" value={liveFilters.risk_category} onChange={(event) => setLiveFilters((current) => ({ ...current, risk_category: event.target.value }))}>{[["", "All accounts"],["critical", "Critical"],["high", "High"],["elevated", "Elevated"],["normal", "Normal (active)"]].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        {(liveFilters.account_login || liveFilters.symbol || liveFilters.risk_category) && <button type="button" className="btn-secondary text-xs" onClick={() => setLiveFilters({ account_login: "", symbol: "", risk_category: "" })}>Clear live filters</button>}
        {!validLiveLogin && <p role="alert" className="text-xs text-danger">Choose a positive whole-number account login.</p>}
        </div>
      </div>}

      {visitedTabs.has("performance") && <div hidden={activeTab !== "performance"}>
      <AppErrorBoundary><Suspense fallback={null}>
        <PerformanceLeaders
          profile={profile}
          selectedGroups={selectedGroups}
          liveAccounts={snapshot.accounts || []}
          active={pageActive && activeTab === "performance"}
        />
      </Suspense></AppErrorBoundary></div>}
      {visitedTabs.has("analyser") && <div hidden={activeTab !== "analyser"}>
      <AppErrorBoundary><Suspense fallback={null}>
        <ClientAnalyser
          profile={profile}
          selectedGroups={selectedGroups}
          candidates={snapshot.accounts || []}
          initialLogin={initialClientLogin}
          active={pageActive && activeTab === "analyser"}
        />
      </Suspense></AppErrorBoundary></div>}
      {visitedTabs.has("markout") && <div hidden={activeTab !== "markout"}>
      <AppErrorBoundary><Suspense fallback={null}>
        <MarkoutWorkspace active={pageActive && activeTab === "markout"} selectedGroups={selectedGroups} candidates={snapshot.accounts || []} />
      </Suspense></AppErrorBoundary></div>}
      {visitedTabs.has("antifraud") && <div hidden={activeTab !== "antifraud"}>
      <AppErrorBoundary><Suspense fallback={null}>
        <AntiFraud profile={profile} selectedGroups={selectedGroups} active={pageActive && activeTab === "antifraud"} />
      </Suspense></AppErrorBoundary></div>}
      {visitedTabs.has("exposure") && <div hidden={activeTab !== "exposure"}>
      <AppErrorBoundary><Suspense fallback={null}>
        <ExposureDashboard
          profile={profile}
          selectedGroups={selectedGroups}
          snapshot={snapshot}
          movement={movement}
          active={pageActive && activeTab === "exposure"}
        />
      </Suspense></AppErrorBoundary></div>}
      {visitedTabs.has("suggestions") && <div hidden={activeTab !== "suggestions"}>
      <AppErrorBoundary><Suspense fallback={null}>
        <BookSuggestions
          profile={profile}
          selectedGroups={selectedGroups}
          accounts={snapshot.accounts || []}
          totalActiveAccounts={snapshot.summary?.active_accounts || 0}
          active={pageActive && activeTab === "suggestions"}
        />
      </Suspense></AppErrorBoundary></div>}
      <div hidden={activeTab !== "monitor"}>
        <div className="space-y-4">
          <p className={`text-xs ${sourceInterrupted || sourceAge === null || sourceAge > 10 ? "text-amber-400" : "text-muted"}`} role="status">
            <span title={sourceAge === null ? undefined : new Date(sourceMs).toISOString()}>{sourceAge === null ? "Connecting…" : sourceInterrupted ? `Updates delayed · last verified ${sourceAge}s ago` : sourceAge > 10 ? `Updates delayed · last verified ${sourceAge}s ago` : `Live · ${sourceAge}s ago`}</span>
            {currency ? ` · Money totals: ${currency}` : ""}
            {moneyView.available.length > 1 && <label className="ml-3 inline-flex items-center gap-2">Money currency
              <select aria-label="Money currency" className="rounded-md border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] px-2 py-1 text-primary" value={currency} onChange={(event) => setFinancialCurrency(event.target.value)}>
                {moneyView.available.map((code) => <option key={code} value={code}>{code}</option>)}
              </select>
            </label>}
            {moneyView.totals.accounts != null && <span className="ml-2">{number.format(moneyView.totals.accounts)} {currency} accounts · lots and positions cover all selected groups</span>}
            {summary.currency_totals?.UNKNOWN?.accounts > 0 && <span className="ml-2 text-amber-400">{number.format(summary.currency_totals.UNKNOWN.accounts)} accounts awaiting currency metadata</span>}
            {filterCount > 0 && <button type="button" className="ml-3 underline underline-offset-4" onClick={() => setFiltersOpen(true)}>{[liveFilters.account_login && `#${liveFilters.account_login}`, liveFilters.symbol, liveFilters.risk_category].filter(Boolean).join(" · ")}</button>}
          </p>
          <LineLoader
            active={loadingGroups || (refreshing && !snapshot.generated_at)}
          />

          {Number(snapshot.summary?.estimated_accounts || 0) > 0 && (
            <div role="status" className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-400">
              Authoritative MT5 account metrics are unavailable for {snapshot.summary.estimated_accounts} selected accounts. Incomplete money totals are withheld until verified account data arrives.
            </div>
          )}

          {snapshot.generated_at && <p data-testid="live-scope-counts" className="text-xs text-muted">{(snapshot.accounts || []).length.toLocaleString()} accounts · {(snapshot.positions || []).length.toLocaleString()} open positions · Complete matching scope</p>}

          {(snapshot.warnings || []).length > 0 && (
            <AnimatedCard className="p-3 text-sm text-amber-400">
              {snapshot.warnings.slice(0, 8).join(" · ")}
            </AnimatedCard>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            <StatCard
              label="Floating P&L"
              value={financialValue("floating_pnl")}
              currency={currency}
              formatter={money}
              icon={DollarSign}
              tone={
                Number(financialValue("floating_pnl")) < 0
                  ? "text-danger"
                  : "text-success"
              }
            />
            <StatCard
              label="Total Equity"
              value={financialValue("equity")}
              currency={currency}
              formatter={money}
              icon={Activity}
            />
            <StatCard
              label="Margin Used"
              value={financialValue("margin")}
              currency={currency}
              formatter={money}
              icon={TrendingDown}
            />
            <StatCard
              label="Free Margin"
              value={financialValue("margin_free")}
              currency={currency}
              formatter={money}
              icon={DollarSign}
            />
            <StatCard
              label="Open Positions"
              value={summary.open_positions}
              icon={TimerReset}
            />
            <StatCard
              label="Gross Lots"
              value={summary.gross_lots}
              formatter={lotsNumber}
              icon={Activity}
            />
            <StatCard label="Net Lots" value={summary.net_lots} icon={Activity} formatter={lotsNumber} />
            <StatCard
              label={`Big Lots ≥ ${number.format(summary.high_lot_threshold || 0)}`}
              value={summary.large_positions}
              icon={AlertTriangle}
              tone="text-amber-400"
            />
            <StatCard
              label="Flagged"
              value={summary.flagged_accounts}
              icon={AlertTriangle}
              tone="text-amber-400"
            />
            <StatCard
              label="Critical"
              value={summary.critical_accounts}
              icon={ShieldAlert}
              tone="text-danger"
            />
            <StatCard label="Total Balance" currency={currency} value={financialValue("balance")} formatter={money} icon={DollarSign} />
            <StatCard label="Margin Level %" currency={currency} value={financialValue("margin") > 0 && financialValue("equity") != null ? Number(financialValue("equity")) / Number(financialValue("margin")) * 100 : null} emptyLabel={financialValue("margin") === 0 ? "—" : "Unavailable"} icon={Activity} />
            <StatCard label="Buy Lots" value={summary.buy_lots} formatter={lotsNumber} icon={Activity} />
            <StatCard label="Sell Lots" value={summary.sell_lots} formatter={lotsNumber} icon={Activity} />
          </div>
          <div className="grid min-w-0 gap-4 xl:grid-cols-2">
            <AnimatedCard className="min-w-0 p-5">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.14em]">
                Live P&L Movement
              </h2>
              <p className="mb-2 text-xs text-muted">
                {currency ? `${currency} accounts in selected groups` : "Selected groups"}
              </p>
              <div className="h-72">
                {displayedMovement.length === 0 ? (
                  <div className="flex h-full items-center justify-center text-xs text-muted">
                    Waiting for a fresh MT5 P&L snapshot.
                  </div>
                ) : (
                <SafeResponsiveContainer
                  width="100%"
                  height="100%"
                  minWidth={0}
                  minHeight={0}
                >
                  <AreaChart data={displayedMovement}>
                    <defs>
                      <linearGradient id="riskPnl" x1="0" y1="0" x2="0" y2="1">
                        <stop
                          offset="5%"
                          stopColor="#ef4444"
                          stopOpacity={0.35}
                        />
                        <stop
                          offset="95%"
                          stopColor="#ef4444"
                          stopOpacity={0}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgb(var(--color-border))"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="time"
                      tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }}
                      axisLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }}
                      axisLine={false}
                    />
                    <Tooltip formatter={(value) => moneyText(value)} />
                    <Area
                      type="stepAfter"
                      dataKey="pnl"
                      isAnimationActive={false}
                      stroke="#ef4444"
                      fill="url(#riskPnl)"
                      strokeWidth={2}
                    />
                  </AreaChart>
                </SafeResponsiveContainer>
                )}
              </div>
            </AnimatedCard>
            <AnimatedCard className="min-w-0 p-5">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.14em]">
                Symbol Exposure
              </h2>
              <div className="h-72">
                <SafeResponsiveContainer
                  width="100%"
                  height="100%"
                  minWidth={0}
                  minHeight={0}
                >
                  <BarChart data={topSymbols} layout="vertical">
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgb(var(--color-border))"
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      tick={{ fontSize: 10, fill: "rgb(var(--color-text-3))" }}
                    />
                    <YAxis
                      type="category"
                      dataKey="symbol"
                      width={82}
                      tick={{ fontSize: 10, fill: "rgb(var(--color-text-2))" }}
                    />
                    <Tooltip
                      formatter={(value) => `${number.format(value)} lots`}
                    />
                    <Bar
                      dataKey="gross_lots"
                      isAnimationActive={false}
                      fill="#f59e0b"
                      radius={[0, 4, 4, 0]}
                    />
                  </BarChart>
                </SafeResponsiveContainer>
              </div>
            </AnimatedCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.45fr_.55fr]">
            <AnimatedCard className="network-map-card self-start overflow-hidden p-5">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold uppercase tracking-[0.14em]">
                    Client Network Map
                  </h2>
                  <p className="mt-1 text-[11px] text-muted">
                    Last-login IP locations · green: no hosting evidence · red: hosting/proxy · amber: unclassified
                  </p>
                </div>
                <div className="flex gap-2">
                  <span className="badge">
                    {summary.located_ips || 0} IPs located
                  </span>
                  <button
                    type="button"
                    onClick={() => setVpsModalOpen(true)}
                    className="badge badge-danger cursor-pointer transition hover:brightness-125"
                    aria-label="Show VPS accounts"
                  >
                    {summary.vps_accounts || 0} VPS accounts
                  </button>
                </div>
              </div>
              <WorldNetworkMap points={network.map_points} />
            </AnimatedCard>
            <div className="grid gap-4">
              <AnimatedCard className="overflow-hidden">
                <div className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] p-4">
                  <Server size={15} className="text-muted" />
                  <h2 className="text-xs font-semibold uppercase tracking-[0.14em]">
                    Top Providers
                  </h2>
                </div>
                <div className="max-h-[185px] overflow-auto p-2">
                  {(network.providers || []).slice(0, 8).map((row) => (
                    <div
                      key={row.provider}
                      className="flex items-center justify-between rounded-lg px-2 py-2 text-xs"
                    >
                      <span className="truncate pr-3 text-secondary">
                        {row.provider}
                      </span>
                      <span className="font-semibold">{row.ips}</span>
                    </div>
                  ))}
                  {!(network.providers || []).length && (
                    <p className="p-3 text-xs text-muted">
                      Provider intelligence loading.
                    </p>
                  )}
                </div>
              </AnimatedCard>
              <AnimatedCard className="overflow-hidden">
                <div className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] p-4">
                  <MapPin size={15} className="text-muted" />
                  <h2 className="text-xs font-semibold uppercase tracking-[0.14em]">
                    Countries & Shared IPs
                  </h2>
                </div>
                <div className="max-h-[185px] overflow-auto p-3">
                  <div className="flex flex-wrap gap-2">
                    {(network.countries || []).slice(0, 10).map((row) => (
                      <span className="badge" key={row.country}>
                        {row.country} {row.ips}
                      </span>
                    ))}
                  </div>
                  <div className="mt-3 space-y-2">
                    {(network.shared_clusters || []).slice(0, 4).map((row) => (
                      <div
                        className="rounded-lg bg-[var(--inp-bg)] p-2 text-[11px]"
                        key={row.ip}
                      >
                        <div className="font-medium">
                          {row.ip} · {row.accounts} accounts
                        </div>
                        <div className="mt-1 text-muted">
                          {row.location || "Unknown location"} ·{" "}
                          {row.provider || "Unknown provider"}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </AnimatedCard>
              <AnimatedCard className="min-w-0 overflow-hidden border border-amber-400/25" >
                <div className="flex items-center justify-between gap-2 border-b border-[rgb(var(--color-border))] p-3">
                  <h2 className="text-xs font-semibold uppercase tracking-[0.14em]">Big Lot Alerts</h2>
                  <span className="badge badge-amber">{summary.large_positions || 0} active</span>
                </div>
                <p className="px-3 pt-2 text-[11px] text-muted">Open positions at or above {number.format(summary.high_lot_threshold || 0)} lots.</p>
                <div className="max-h-48 space-y-2 overflow-auto p-3">
                  {highLotPositions.map((row) => <div key={row.ticket} className="rounded-lg bg-[var(--inp-bg)] p-2 text-xs">
                    <div className="flex justify-between gap-2"><span className="truncate font-medium">#{row.login} · {row.symbol}</span><span className="shrink-0 font-semibold text-amber-400">{row.action} {number.format(row.volume)} lots</span></div>
                    <div className="mt-1 truncate text-muted">{row.name || `Login ${row.login}`} · {row.group}</div>
                    <div className={`mt-1 tabular-nums ${row.profit < 0 ? "text-danger" : "text-success"}`}>{moneyText(row.profit)}</div>
                  </div>)}
                  {!highLotPositions.length && <p className="text-xs text-muted">No open position meets the big-lot threshold.</p>}
                </div>
              </AnimatedCard>
            </div>
          </div>


          <AnimatedCard className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-[0.14em]">
                  Top Risk & Abuse Alerts
                </h2>
                <p className="mt-1 text-xs text-muted">
                  {summary.flagged_accounts || 0} flagged in selected groups · {alerts.length} ranked here ? all accounts in Complete live book
                  {sourceAge === null ? " · waiting for MT5" : ` · MT5 data ${sourceAge}s old`}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="badge badge-amber">
                  {sourceInterrupted || sourceAge === null || sourceAge > 30 ? "Reconnecting · previous data" : "Live · background updates"}
                </span>
              </div>
            </div>
            <div className="max-h-[480px] overflow-auto scrollbar-hidden">
              <table className="w-full min-w-[1340px] text-left text-xs">
                <thead className="sticky top-0 bg-[rgb(var(--color-surface))] text-muted">
                  <tr>
                    {[
                      "Severity",
                      "Score",
                      "Account",
                      "Network / VPS",
                      "Group",
                      "Floating P&L",
                      "Equity",
                      "Margin Level",
                      "Positions",
                      "Concentration",
                      "Signals",
                      "Noticed",
                    ].map((head) => (
                      <th
                        key={head}
                        className="border-b border-[rgb(var(--color-border))] px-4 py-3 font-medium"
                      >
                        {head}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((row) => (
                    <tr
                      key={row.login}
                      className="border-b border-[rgb(var(--color-border))] last:border-0"
                    >
                      <td className="px-4 py-3">
                        <span className={severityClass(row.severity)}>
                          {row.severity}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-semibold">
                        {row.risk_score}
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium">
                          {row.name || `Login ${row.login}`}
                        </div>
                        <div className="text-muted">#{row.login}</div>
                      </td>
                      <NetworkCell row={row} />
                      <td className="px-4 py-3 text-muted">{row.group}</td>
                      <td
                        className={`px-4 py-3 font-medium ${row.floating_pnl < 0 ? "text-danger" : "text-success"}`}
                      >
                        {moneyText(row.floating_pnl)}
                      </td>
                      <td className="px-4 py-3">{moneyText(row.equity)}</td>
                      <td className="px-4 py-3">
                        {number.format(row.margin_level)}%
                      </td>
                      <td className="px-4 py-3">{row.positions}</td>
                      <td className="px-4 py-3">
                        {number.format(row.concentration_pct)}%
                      </td>
                      <td className="max-w-md px-4 py-3 text-muted">
                        {row.signals.join(" · ")}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          onClick={() => markNoticed(row)}
                          className="btn-ghost !h-9 !w-9 !p-0"
                          title="Mark noticed for 48 hours"
                          aria-label={`Mark alert for account ${row.login} noticed`}
                        >
                          <CheckCheck size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {alerts.length === 0 && (
                    <tr>
                      <td
                        colSpan={12}
                        className="px-4 py-8 text-center text-muted"
                      >
                        No elevated live-risk signals in this snapshot.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </AnimatedCard>

          {vpsModalOpen && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-white/10 p-4 backdrop-blur-[2px] dark:bg-black/25"
              role="dialog"
              aria-modal="true"
              aria-label="VPS accounts"
            >
              <button
                type="button"
                className="absolute inset-0"
                onClick={() => setVpsModalOpen(false)}
                aria-label="Close VPS accounts"
              />
              <AnimatedCard className="relative z-10 max-h-[80vh] w-full max-w-3xl overflow-hidden">
                <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4">
                  <div>
                    <h2 className="font-semibold">VPS accounts</h2>
                    <p className="mt-1 text-xs text-muted">
                      {vpsAccounts.length} accounts currently detected on
                      hosting or proxy networks
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn-ghost !h-9 !w-9 !p-0"
                    onClick={() => setVpsModalOpen(false)}
                    aria-label="Close"
                  >
                    <X size={17} />
                  </button>
                </div>
                <div className="max-h-[65vh] overflow-auto p-3">
                  {vpsAccounts.map((row) => (
                    <div
                      key={row.login}
                      className="mb-2 grid gap-2 rounded-xl border border-[rgb(var(--color-border))] p-3 text-xs sm:grid-cols-[1fr_1fr_1.2fr]"
                    >
                      <div>
                        <p className="font-semibold">
                          {row.name || `Login ${row.login}`}
                        </p>
                        <p className="text-muted">
                          #{row.login} · {row.group}
                        </p>
                      </div>
                      <div>
                        <p>{row.last_ip || "IP unavailable"}</p>
                        <p className="text-muted">
                          {row.location || "Location unavailable"}
                        </p>
                      </div>
                      <div>
                        <p>{row.provider || "Provider unavailable"}</p>
                        <p className="text-danger">
                          Confidence {row.vps_confidence || 0}% ·{" "}
                          {(row.vps_evidence || []).join(" · ")}
                        </p>
                      </div>
                    </div>
                  ))}
                  {!vpsAccounts.length && (
                    <p className="p-8 text-center text-sm text-muted">
                      No VPS accounts in the current live snapshot.
                    </p>
                  )}
                </div>
              </AnimatedCard>
            </div>
          )}

          <div className="grid min-w-0 gap-4">
            <LiveBookTable key={selectedGroups.join("\u0001")} accounts={snapshot.accounts || []} positions={snapshot.positions || []} />
            <AnimatedCard className="overflow-hidden">
              <div className="border-b border-[rgb(var(--color-border))] p-4">
                <h2 className="text-sm font-semibold uppercase tracking-[0.14em]">
                  Live Symbol Book
                </h2>
              </div>
              <div className="max-h-[430px] overflow-auto">
                <table className="w-full min-w-[650px] text-xs">
                  <thead>
                    <tr className="text-muted">
                      {[
                        "Symbol",
                        "Buy Lots",
                        "Sell Lots",
                        "Net Lots",
                        "Positions",
                        "Floating P&L",
                      ].map((head) => (
                        <th
                          className="px-3 py-3 text-left font-medium"
                          key={head}
                        >
                          {head}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(snapshot.symbols || []).map((row) => (
                      <tr
                        key={row.symbol}
                        className="border-t border-[rgb(var(--color-border))]"
                      >
                        <td className="px-3 py-3 font-medium">{row.symbol}</td>
                        <td className="px-3 py-3 text-success">
                          {row.buy_lots}
                        </td>
                        <td className="px-3 py-3 text-danger">
                          {row.sell_lots}
                        </td>
                        <td className="px-3 py-3">{row.net_lots}</td>
                        <td className="px-3 py-3">{row.positions}</td>
                        <td
                          className={`px-3 py-3 font-semibold ${row.floating_pnl < 0 ? "text-danger" : "text-success"}`}
                        >
                          {moneyText(row.floating_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </AnimatedCard>
          </div>

          <p className="pb-2 text-xs text-muted">
            <Users size={12} className="mr-1 inline" />
            Risk scores are triage signals. They do not prove abuse and do not
            execute trading actions.
          </p>
        </div>
      </div>
    </div>
  );
}
