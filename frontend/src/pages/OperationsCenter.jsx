import React, {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Activity,
  AlertTriangle,
  BellRing,
  BriefcaseBusiness,
  Check,
  GitBranch,
  History,
  Radio,
  RefreshCw,
  Save,
  Settings2,
  ShieldAlert,
  Trash2,
  Users,
} from "lucide-react";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import RiskControls from "./RiskControls";
import {
  apiRequest,
  fetchCompleteMt5Groups,
  getStoredUser,
  operationsWebSocketUrl,
  useStoredProfile,
} from "../lib/api";

const tabs = [
  ["alerts", "Alert Center", BellRing],
  ["cases", "Cases", BriefcaseBusiness],
  ["related", "Related Accounts", GitBranch],
  ["limits", "Exposure Limits", ShieldAlert],
  ["intelligence", "Intelligence", Activity],
  ["reconciliation", "Reconciliation", RefreshCw],
  ["channels", "Channels", Radio],
  ["audit", "Audit Log", History],
];
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const actor = () => getStoredUser()?.username || "system";
const severityClass = (value) =>
  value === "critical" || value === "high"
    ? "badge badge-danger"
    : value === "warning"
      ? "badge badge-amber"
      : "badge";

function Empty({ children }) {
  return <div className="p-10 text-center text-xs text-muted">{children}</div>;
}
function Panel({ title, count, children }) {
  return (
    <AnimatedCard className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-[rgb(var(--color-border))] px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-[.14em]">
          {title}
        </h2>
        {count !== undefined && <span className="badge">{count}</span>}
      </div>
      {children}
    </AnimatedCard>
  );
}

export default function OperationsCenter() {
  const profile = useStoredProfile();
  const [activeTab, setActiveTab] = useState("alerts");
  const renderedTab = useDeferredValue(activeTab);
  const [groups, setGroups] = useState([]);
  const [live, setLive] = useState({
    alerts: [],
    related_accounts: [],
    coordinated_positions: [],
    limits: [],
    summary: {},
  });
  const [cases, setCases] = useState([]);
  const [audit, setAudit] = useState([]);
  const [channels, setChannels] = useState([]);
  const [reconciliations, setReconciliations] = useState([]);
  const [intelligence, setIntelligence] = useState(null);
  const [cidAudit, setCidAudit] = useState(null);
  const [cidAuditRange, setCidAuditRange] = useState("24");
  const [cidAuditHours, setCidAuditHours] = useState(24);
  const [cidAuditFrom, setCidAuditFrom] = useState(
    new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10),
  );
  const [cidAuditTo, setCidAuditTo] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [cidAuditLoading, setCidAuditLoading] = useState(false);
  const [cidFilters, setCidFilters] = useState({
    login: "",
    cid: "",
    ip: "",
    device: "all",
    country: "all",
    build: "all",
    flagged: false,
  });
  const [loading, setLoading] = useState(true);
  const [socketLive, setSocketLive] = useState(false);
  const [caseForm, setCaseForm] = useState({
    client_login: "",
    title: "",
    assigned_to: "",
    priority: "medium",
    summary: "",
  });
  const [limitForm, setLimitForm] = useState({
    scope_type: "account",
    scope_value: "",
    metric: "gross_lots",
    threshold: 1,
    warning_pct: 80,
    escalation_level: "warning",
  });
  const [channelForm, setChannelForm] = useState({
    name: "",
    channel_type: "email",
    destination: "",
    min_severity: "warning",
  });
  const refreshTimer = useRef(0);
  const liveRequestRunning = useRef(false);
  const lastLiveError = useRef(0);
  const tabLoadedAt = useRef({});
  const activeTabRef = useRef(activeTab);
  const loadLiveRef = useRef(null);

  useEffect(() => {
    activeTabRef.current = activeTab;
  }, [activeTab]);

  const requestBody = useMemo(
    () => ({
      selected_groups: groups,
      excluded_logins: [],
      group_filter_rules: "",
      actor: actor(),
    }),
    [groups],
  );
  const cidFilterOptions = useMemo(() => {
    const events = cidAudit?.events || [];
    const unique = (key) =>
      [
        ...new Set(events.map((row) => String(row[key] || "")).filter(Boolean)),
      ].sort();
    return {
      devices: unique("device_type"),
      countries: unique("country"),
      builds: unique("build"),
    };
  }, [cidAudit]);
  const filteredCidEvents = useMemo(() => {
    const login = cidFilters.login.trim().toLowerCase();
    const cid = cidFilters.cid.trim().toLowerCase();
    const ip = cidFilters.ip.trim().toLowerCase();
    return (cidAudit?.events || []).filter((row) => {
      if (login && !String(row.login).toLowerCase().includes(login))
        return false;
      if (cid && !String(row.computer_id).toLowerCase().includes(cid))
        return false;
      if (
        ip &&
        !String(row.ip_address || "")
          .toLowerCase()
          .includes(ip)
      )
        return false;
      if (
        cidFilters.device !== "all" &&
        String(row.device_type) !== cidFilters.device
      )
        return false;
      if (
        cidFilters.country !== "all" &&
        String(row.country) !== cidFilters.country
      )
        return false;
      if (cidFilters.build !== "all" && String(row.build) !== cidFilters.build)
        return false;
      return !cidFilters.flagged || row.flagged;
    });
  }, [cidAudit, cidFilters]);
  const cidRiskSummary = useMemo(
    () =>
      [
        ["Shared CID (2+ accounts)", "shared_cid"],
        ["Shared IP (3+ CIDs)", "shared_ip"],
        ["Account Uses Multiple CIDs", "multiple_cids"],
        ["CID Seen From Multiple Countries", "multiple_countries"],
      ].map(([label, key]) => ({
        label,
        count: filteredCidEvents.filter((row) => row.flags?.[key]).length,
      })),
    [filteredCidEvents],
  );
  const filteredCidGroups = useMemo(() => {
    const groups = new Map();
    for (const row of filteredCidEvents) {
      if (!groups.has(row.computer_id)) groups.set(row.computer_id, new Set());
      groups.get(row.computer_id).add(Number(row.login));
    }
    return [...groups]
      .map(([computer_id, logins]) => ({
        computer_id,
        logins: [...logins].sort((a, b) => a - b),
        login_count: logins.size,
      }))
      .sort((a, b) => b.login_count - a.login_count);
  }, [filteredCidEvents]);
  const filteredIpGroups = useMemo(() => {
    const groups = new Map();
    for (const row of filteredCidEvents) {
      if (!row.ip_address) continue;
      if (!groups.has(row.ip_address))
        groups.set(row.ip_address, {
          computerIds: new Set(),
          logins: new Set(),
        });
      groups.get(row.ip_address).computerIds.add(row.computer_id);
      groups.get(row.ip_address).logins.add(Number(row.login));
    }
    return [...groups]
      .map(([ip_address, value]) => ({
        ip_address,
        computer_ids: [...value.computerIds].sort(),
        logins: [...value.logins].sort((a, b) => a - b),
        cid_count: value.computerIds.size,
        login_count: value.logins.size,
      }))
      .sort((a, b) => b.cid_count - a.cid_count);
  }, [filteredCidEvents]);
  const loadLive = useCallback(async () => {
    if (liveRequestRunning.current) return;
    liveRequestRunning.current = true;
    try {
      setLive(
        await apiRequest("/api/operations/live", {
          method: "POST",
          body: JSON.stringify(requestBody),
          timeoutMs: 60_000,
        }),
      );
    } catch (error) {
      if (Date.now() - lastLiveError.current > 15000) {
        lastLiveError.current = Date.now();
        toast.error(error.message || "Operations feed unavailable");
      }
    } finally {
      liveRequestRunning.current = false;
      setLoading(false);
    }
  }, [requestBody]);
  loadLiveRef.current = loadLive;
  const loadCases = useCallback(
    async () =>
      setCases((await apiRequest("/api/operations/cases")).cases || []),
    [],
  );
  const loadAudit = useCallback(
    async () =>
      setAudit(
        (await apiRequest("/api/operations/audit?limit=500")).events || [],
      ),
    [],
  );
  const loadChannels = useCallback(
    async () =>
      setChannels(
        (await apiRequest("/api/operations/channels")).channels || [],
      ),
    [],
  );
  const loadReconciliations = useCallback(
    async () =>
      setReconciliations(
        (await apiRequest("/api/operations/reconciliation")).rows || [],
      ),
    [],
  );

  useEffect(() => {
    let alive = true;
    const refreshGroups = () =>
      fetchCompleteMt5Groups(profile)
        .then((rows) => {
          if (alive) setGroups(rows);
        })
        .catch(() => {});
    refreshGroups();
    const timer = window.setInterval(refreshGroups, 15000);
    window.addEventListener("focus", refreshGroups);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshGroups);
    };
  }, [profile]);
  useEffect(() => {
    const now = Date.now();
    const fresh = now - (tabLoadedAt.current[activeTab] || 0) < 15000;
    if (["alerts", "related", "limits"].includes(activeTab)) loadLive();
    if (fresh) return;
    tabLoadedAt.current[activeTab] = now;
    if (activeTab === "cases") loadCases();
    if (activeTab === "audit") loadAudit();
    if (activeTab === "channels") loadChannels();
    if (activeTab === "reconciliation") loadReconciliations();
  }, [
    activeTab,
    loadLive,
    loadCases,
    loadAudit,
    loadChannels,
    loadReconciliations,
  ]);
  useEffect(() => {
    let socket;
    let retry;
    let connectTimer;
    let disposed = false;
    let retryDelay = 1000;
    let lastSourceState = null;
    const connect = () => {
      if (disposed || document.hidden) return;
      const socketUrl = operationsWebSocketUrl();
      if (!socketUrl) {
        setSocketLive(false);
        return;
      }
      socket = new WebSocket(socketUrl);
      socket.onopen = () => {
        if (disposed) {
          socket.close(1000, "Page closed");
          return;
        }
        retryDelay = 1000;
        setSocketLive(true);
      };
      socket.onmessage = (event) => {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.type !== "manager_tick") return;
        const sourceState = `${message.connected}:${message.source_updated_at}`;
        if (sourceState === lastSourceState) return;
        lastSourceState = sourceState;
        const now = Date.now();
        if (
          !document.hidden &&
          ["alerts", "related", "limits"].includes(activeTabRef.current) &&
          now - refreshTimer.current > 1500
        ) {
          refreshTimer.current = now;
          loadLiveRef.current?.();
        }
      };
      socket.onclose = (event) => {
        if (disposed || document.hidden) return;
        setSocketLive(false);
        // Authentication failures are terminal for this page; retrying them
        // only floods the browser console and the server with rejected sockets.
        if (event.code === 1008) return;
        retry = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 30000);
      };
      socket.onerror = () => {
        if (socket.readyState === WebSocket.OPEN) socket.close();
      };
    };
    const handleVisibility = () => {
      if (document.hidden) {
        window.clearTimeout(retry);
        if (socket?.readyState === WebSocket.OPEN) socket.close(1000, "Hidden");
      } else if (!socket || socket.readyState === WebSocket.CLOSED) {
        connect();
      }
    };
    connectTimer = window.setTimeout(connect, 100);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      disposed = true;
      window.clearTimeout(connectTimer);
      window.clearTimeout(retry);
      document.removeEventListener("visibilitychange", handleVisibility);
      if (socket?.readyState === WebSocket.OPEN)
        socket.close(1000, "Unmounted");
      else if (socket?.readyState === WebSocket.CONNECTING) {
        socket.onopen = () => socket.close(1000, "Unmounted");
        socket.onerror = null;
        socket.onclose = null;
      }
    };
  }, []);

  async function runCidAudit() {
    if (!profile) return toast.error("Select an MT5 profile first");
    setCidAuditLoading(true);
    try {
      const result = await apiRequest("/api/operations/cid-audit", {
        method: "POST",
        timeoutMs: 300_000,
        body: JSON.stringify({
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          lookback_hours: Number(cidAuditHours),
          all_history: cidAuditRange === "all",
          from_date: cidAuditRange === "custom" ? cidAuditFrom : null,
          to_date: cidAuditRange === "custom" ? cidAuditTo : null,
        }),
      });
      setCidAudit(result);
      if (result.warning) toast(result.warning);
      else
        toast.success(`CID audit loaded ${result.summary.events} login events`);
    } catch (error) {
      setCidAudit(null);
      toast.error(error.message || "CID journal audit failed");
    } finally {
      setCidAuditLoading(false);
    }
  }

  async function acknowledge(key) {
    await apiRequest("/api/operations/alerts/acknowledge", {
      method: "POST",
      body: JSON.stringify({ alert_key: key, actor: actor() }),
    });
    await loadLive();
  }
  async function caseFromAlert(alert) {
    const payload = {
      client_login: Number(alert.login),
      title: alert.title,
      assigned_to: actor(),
      priority: alert.severity === "critical" ? "critical" : "high",
      status: "open",
      summary: alert.detail,
      evidence: [alert],
      actor: actor(),
    };
    if (!payload.client_login)
      return toast.error("This platform alert is not tied to a client account");
    await apiRequest("/api/operations/cases", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    toast.success("Investigation case created");
    loadCases();
  }
  async function createCase(event) {
    event.preventDefault();
    await apiRequest("/api/operations/cases", {
      method: "POST",
      body: JSON.stringify({
        ...caseForm,
        client_login: Number(caseForm.client_login),
        status: "open",
        evidence: [],
        actor: actor(),
      }),
    });
    setCaseForm({
      client_login: "",
      title: "",
      assigned_to: "",
      priority: "medium",
      summary: "",
    });
    await loadCases();
    toast.success("Case created");
  }
  async function updateCase(item, changes) {
    await apiRequest(`/api/operations/cases/${item.id}`, {
      method: "PUT",
      body: JSON.stringify({ ...changes, actor: actor() }),
    });
    await loadCases();
  }
  async function deleteCase(item) {
    if (
      !window.confirm(`Delete case “${item.title}” for #${item.client_login}?`)
    )
      return;
    const previous = cases;
    setCases((rows) => rows.filter((row) => row.id !== item.id));
    try {
      await apiRequest(`/api/operations/cases/${item.id}`, {
        method: "DELETE",
      });
      toast.success("Case deleted");
    } catch (error) {
      setCases(previous);
      toast.error(error.message);
    }
  }
  async function createLimit(event) {
    event.preventDefault();
    await apiRequest("/api/operations/limits", {
      method: "POST",
      body: JSON.stringify({
        ...limitForm,
        threshold: Number(limitForm.threshold),
        warning_pct: Number(limitForm.warning_pct),
        enabled: true,
        actor: actor(),
      }),
    });
    await loadLive();
    toast.success("Exposure limit active");
  }
  async function removeLimit(id) {
    await apiRequest(
      `/api/operations/limits/${id}?actor=${encodeURIComponent(actor())}`,
      { method: "DELETE" },
    );
    await loadLive();
  }
  async function runIntelligence() {
    setLoading(true);
    try {
      let result = await apiRequest("/api/operations/intelligence", {
        method: "POST",
        body: JSON.stringify({ ...requestBody, lookback_days: 90 }),
      });
      while (result.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        result = await apiRequest(`/api/operations/intelligence/${result.job_id}`);
      }
      if (result.status === "error") throw new Error(result.detail);
      setIntelligence(result);
    } catch (error) {
      toast.error(error.message || "Intelligence scan failed");
    } finally {
      setLoading(false);
    }
  }
  async function runReconciliation() {
    await apiRequest(
      `/api/operations/reconciliation/run?actor=${encodeURIComponent(actor())}`,
      { method: "POST" },
    );
    await loadReconciliations();
    toast.success("Reconciliation stored");
  }
  async function createChannel(event) {
    event.preventDefault();
    await apiRequest("/api/operations/channels", {
      method: "POST",
      body: JSON.stringify({ ...channelForm, enabled: true, actor: actor() }),
    });
    await loadChannels();
    toast.success("Channel added");
  }
  async function removeChannel(id) {
    await apiRequest(
      `/api/operations/channels/${id}?actor=${encodeURIComponent(actor())}`,
      { method: "DELETE" },
    );
    await loadChannels();
  }

  const summary = live.summary || {};
  const hasVerifiedLiveData =
    Boolean(live.generated_at) && ["connected", "stale"].includes(live.status);
  return (
    <div className="mx-auto max-w-[1700px] space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[.24em] text-muted">
            Broker Operations
          </p>
          <h1 className="mt-1 text-xl font-semibold">Operations Center</h1>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full ${socketLive ? "bg-success" : "bg-danger"}`}
          />
          {socketLive ? "Live channel connected" : "Reconnecting"}
          {live.generated_at && (
            <span>· {new Date(live.generated_at).toLocaleTimeString()}</span>
          )}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
        {[
          ["Live Alerts", hasVerifiedLiveData ? (summary.alerts ?? 0) : "—"],
          ["Critical", hasVerifiedLiveData ? (summary.critical ?? 0) : "—"],
          ["Open Cases", hasVerifiedLiveData ? (summary.open_cases ?? 0) : "—"],
          [
            "Related Clusters",
            hasVerifiedLiveData ? (summary.related_clusters ?? 0) : "—",
          ],
          [
            "Coordinated",
            hasVerifiedLiveData ? (summary.coordinated_signals ?? 0) : "—",
          ],
          [
            "Limit Breaches",
            hasVerifiedLiveData ? (summary.limit_breaches ?? 0) : "—",
          ],
        ].map(([label, value]) => (
          <AnimatedCard className="p-3" key={label}>
            <p className="text-[9px] uppercase tracking-[.15em] text-muted">
              {label}
            </p>
            <p className="mt-1 text-lg font-semibold">{value}</p>
          </AnimatedCard>
        ))}
      </div>
      <div className="flex gap-1 overflow-x-auto rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-1 scrollbar-hidden">
        {tabs.map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={`flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-xs ${activeTab === key ? "bg-white text-black" : "text-muted hover:text-primary"}`}
          >
            {React.createElement(Icon, { size: 14 })}
            {label}
          </button>
        ))}
      </div>
      {loading && (
        <div className="line-loader">
          <span />
        </div>
      )}
      {live.status === "stale" && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-300">
          Live MT5 cache is stale. Showing the most recent verified snapshot
          while reconnection continues.
        </div>
      )}

      {renderedTab === "alerts" && (
        <Panel
          title="Verified Live Alert Feed"
          count={live.alerts?.length || 0}
        >
          <div className="max-h-[650px] overflow-y-auto scrollbar-hidden">
            {(live.alerts || []).map((alert) => (
              <div
                key={alert.key}
                className="flex flex-col gap-3 border-b border-[rgb(var(--color-border))] p-4 lg:flex-row lg:items-center lg:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={severityClass(alert.severity)}>
                      {alert.severity}
                    </span>
                    <span className="text-sm font-semibold">{alert.title}</span>
                    {alert.acknowledged && (
                      <span className="badge badge-success">Acknowledged</span>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-secondary">{alert.detail}</p>
                  <p className="mt-1 text-[10px] text-muted">
                    Source: {alert.source}
                    {alert.login ? ` · Login ${alert.login}` : ""}
                    {alert.symbol ? ` · ${alert.symbol}` : ""}
                  </p>
                </div>
                <div className="flex gap-2">
                  {!alert.acknowledged && (
                    <button
                      className="btn-secondary"
                      onClick={() => acknowledge(alert.key)}
                    >
                      <Check size={13} />
                      Acknowledge
                    </button>
                  )}
                  <button
                    className="btn-secondary"
                    onClick={() => caseFromAlert(alert)}
                  >
                    <BriefcaseBusiness size={13} />
                    Create case
                  </button>
                </div>
              </div>
            ))}
            {!live.alerts?.length && <Empty>No current MT5 alerts.</Empty>}
          </div>
        </Panel>
      )}

      {renderedTab === "cases" && (
        <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
          <AnimatedCard className="p-4">
            <h2 className="text-sm font-semibold">New investigation</h2>
            <form onSubmit={createCase} className="mt-4 space-y-3">
              <input
                required
                type="number"
                className="input w-full"
                placeholder="Client login"
                value={caseForm.client_login}
                onChange={(e) =>
                  setCaseForm({ ...caseForm, client_login: e.target.value })
                }
              />
              <input
                required
                className="input w-full"
                placeholder="Case title"
                value={caseForm.title}
                onChange={(e) =>
                  setCaseForm({ ...caseForm, title: e.target.value })
                }
              />
              <input
                className="input w-full"
                placeholder="Assigned dealer"
                value={caseForm.assigned_to}
                onChange={(e) =>
                  setCaseForm({ ...caseForm, assigned_to: e.target.value })
                }
              />
              <select
                className="input w-full"
                value={caseForm.priority}
                onChange={(e) =>
                  setCaseForm({ ...caseForm, priority: e.target.value })
                }
              >
                {["low", "medium", "high", "critical"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
              <textarea
                className="input min-h-28 w-full"
                placeholder="Evidence summary"
                value={caseForm.summary}
                onChange={(e) =>
                  setCaseForm({ ...caseForm, summary: e.target.value })
                }
              />
              <button className="btn-primary">
                <Save size={14} />
                Create case
              </button>
            </form>
          </AnimatedCard>
          <Panel title="Investigation Queue" count={cases.length}>
            <div className="max-h-[680px] overflow-y-auto p-3 scrollbar-hidden">
              {cases.map((item) => (
                <div
                  key={item.id}
                  className="mb-2 rounded-lg border border-[rgb(var(--color-border))] p-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold">
                        {item.title}{" "}
                        <span className="text-muted">#{item.client_login}</span>
                      </div>
                      <p className="mt-1 text-xs text-secondary">
                        {item.summary || "No summary"}
                      </p>
                      <p className="mt-1 text-[10px] text-muted">
                        Created by {item.created_by} ·{" "}
                        {new Date(item.created_at + "Z").toLocaleString()}
                      </p>
                    </div>
                    <span className={severityClass(item.priority)}>
                      {item.priority}
                    </span>
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto_auto]">
                    <input
                      className="input"
                      value={item.assigned_to || ""}
                      placeholder="Assignee"
                      onChange={(e) =>
                        setCases((rows) =>
                          rows.map((r) =>
                            r.id === item.id
                              ? { ...r, assigned_to: e.target.value }
                              : r,
                          ),
                        )
                      }
                    />
                    <select
                      className="input"
                      value={item.status}
                      onChange={(e) =>
                        updateCase(item, { status: e.target.value })
                      }
                    >
                      {[
                        "open",
                        "investigating",
                        "monitoring",
                        "resolved",
                        "closed",
                      ].map((v) => (
                        <option key={v}>{v}</option>
                      ))}
                    </select>
                    <button
                      className="btn-secondary"
                      onClick={() =>
                        updateCase(item, {
                          assigned_to: item.assigned_to,
                          summary: item.summary,
                        })
                      }
                    >
                      <Save size={13} />
                      Save
                    </button>
                    <button
                      type="button"
                      className="btn-secondary !text-red-500"
                      title="Delete case"
                      aria-label={`Delete case ${item.title}`}
                      onClick={() => deleteCase(item)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              ))}
              {!cases.length && <Empty>No investigation cases.</Empty>}
            </div>
          </Panel>
        </div>
      )}

      {renderedTab === "related" && (
        <div className="grid gap-4 xl:grid-cols-2">
          <AnimatedCard className="overflow-hidden xl:col-span-2">
            <div className="flex flex-col gap-3 border-b border-[rgb(var(--color-border))] p-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 className="text-sm font-semibold">
                  CID login journal audit
                </h2>
                <p className="mt-1 text-xs text-muted">
                  Parse historical MT5 client logins and flag shared CIDs,
                  shared IPs, accounts using multiple CIDs, and CIDs seen across
                  countries.
                </p>
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                  Date range
                  <select
                    className="input mt-1 block"
                    value={cidAuditRange}
                    onChange={(event) => {
                      const value = event.target.value;
                      setCidAuditRange(value);
                      if (!["custom", "all"].includes(value))
                        setCidAuditHours(Number(value));
                    }}
                  >
                    <option value={6}>6 hours</option>
                    <option value={24}>24 hours</option>
                    <option value={72}>3 days</option>
                    <option value={168}>7 days</option>
                    <option value="custom">Custom dates</option>
                    <option value="all">All history</option>
                  </select>
                </label>
                {cidAuditRange === "custom" && (
                  <>
                    <label className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                      From
                      <input
                        type="date"
                        className="input mt-1 block"
                        value={cidAuditFrom}
                        max={cidAuditTo}
                        onChange={(event) =>
                          setCidAuditFrom(event.target.value)
                        }
                      />
                    </label>
                    <label className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                      To
                      <input
                        type="date"
                        className="input mt-1 block"
                        value={cidAuditTo}
                        min={cidAuditFrom}
                        max={new Date().toISOString().slice(0, 10)}
                        onChange={(event) => setCidAuditTo(event.target.value)}
                      />
                    </label>
                  </>
                )}
                <button
                  type="button"
                  className="btn-primary"
                  disabled={cidAuditLoading}
                  onClick={runCidAudit}
                >
                  <RefreshCw
                    size={13}
                    className={cidAuditLoading ? "animate-spin" : ""}
                  />
                  {cidAuditLoading ? "Reading journal" : "Run CID audit"}
                </button>
              </div>
            </div>
            {cidAudit ? (
              <div className="p-4">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {[
                    ["Login ID contains", "login", "text", []],
                    ["CID contains", "cid", "text", []],
                    ["IP Address contains", "ip", "text", []],
                  ].map(([label, key, type]) => (
                    <label
                      key={key}
                      className="text-[10px] font-semibold uppercase tracking-wider text-muted"
                    >
                      {label}
                      <input
                        type={type}
                        className="input mt-1 block w-full"
                        value={cidFilters[key]}
                        onChange={(event) =>
                          setCidFilters((current) => ({
                            ...current,
                            [key]: event.target.value,
                          }))
                        }
                      />
                    </label>
                  ))}
                  {[
                    ["Device Type", "device", cidFilterOptions.devices],
                    ["Country", "country", cidFilterOptions.countries],
                    ["Build Version", "build", cidFilterOptions.builds],
                  ].map(([label, key, options]) => (
                    <label
                      key={key}
                      className="text-[10px] font-semibold uppercase tracking-wider text-muted"
                    >
                      {label}
                      <select
                        className="input mt-1 block w-full"
                        value={cidFilters[key]}
                        onChange={(event) =>
                          setCidFilters((current) => ({
                            ...current,
                            [key]: event.target.value,
                          }))
                        }
                      >
                        <option value="all">All</option>
                        {options.map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label className="flex items-center gap-2 self-end py-3 text-xs text-muted">
                    <input
                      type="checkbox"
                      checked={cidFilters.flagged}
                      onChange={(event) =>
                        setCidFilters((current) => ({
                          ...current,
                          flagged: event.target.checked,
                        }))
                      }
                    />
                    Flagged only
                  </label>
                </div>
                <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
                  {[
                    ["Login events", filteredCidEvents.length],
                    [
                      "Accounts",
                      new Set(filteredCidEvents.map((row) => row.login)).size,
                    ],
                    [
                      "Unique CIDs",
                      new Set(filteredCidEvents.map((row) => row.computer_id))
                        .size,
                    ],
                    ["IP addresses", cidAudit.summary.ip_addresses],
                    ["Shared CIDs", cidAudit.summary.shared_computer_ids],
                    [
                      "Flagged events",
                      filteredCidEvents.filter((row) => row.flagged).length,
                    ],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-lg border border-[rgb(var(--color-border))] p-3"
                    >
                      <p className="text-[10px] uppercase tracking-wider text-muted">
                        {label}
                      </p>
                      <p className="mt-1 text-lg font-semibold">{value}</p>
                    </div>
                  ))}
                </div>
                {cidAudit.warning && (
                  <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-500">
                    {cidAudit.warning}
                  </p>
                )}
                <div className="mt-4 overflow-hidden rounded-lg border border-[rgb(var(--color-border))]">
                  <div className="border-b border-[rgb(var(--color-border))] px-3 py-2 text-xs font-semibold">
                    Risk summary
                  </div>
                  {cidRiskSummary.map((rule) => (
                    <div
                      key={rule.label}
                      className="flex items-center justify-between border-t border-[rgb(var(--color-border-soft))] px-3 py-2 text-xs first:border-t-0"
                    >
                      <span>{rule.label}</span>
                      <span
                        className={rule.count ? "badge badge-danger" : "badge"}
                      >
                        {rule.count}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="mt-4 overflow-x-auto rounded-lg border border-[rgb(var(--color-border))]">
                  <table className="w-full min-w-[1050px] text-left text-xs">
                    <thead className="bg-[var(--inp-bg)] text-[10px] uppercase tracking-wider text-muted">
                      <tr>
                        <th className="p-3">Time</th>
                        <th className="p-3">Login</th>
                        <th className="p-3">CID</th>
                        <th className="p-3">Device</th>
                        <th className="p-3">IP / Geo</th>
                        <th className="p-3">Access / Ping</th>
                        <th className="p-3">Risk flags</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredCidEvents.slice(0, 500).map((row) => (
                        <tr
                          key={row.event_key}
                          className="border-t border-[rgb(var(--color-border))]"
                        >
                          <td className="p-3 whitespace-nowrap">
                            {row.occurred_at || "Unknown"}
                          </td>
                          <td className="p-3 font-semibold">#{row.login}</td>
                          <td className="p-3 font-mono text-[10px]">
                            {row.computer_id}
                          </td>
                          <td className="p-3">
                            {row.device_type} · {row.build || "—"}
                          </td>
                          <td className="p-3">
                            {row.ip_address || "Unavailable"}
                            <div className="text-[10px] text-muted">
                              {[row.country, row.city, row.isp]
                                .filter(Boolean)
                                .join(", ") || "No geo"}
                            </div>
                          </td>
                          <td className="p-3">
                            {row.access_server || "Unknown"}
                            <div className="text-[10px] text-muted">
                              {row.ping_ms ?? "—"} ms
                            </div>
                          </td>
                          <td className="p-3">
                            <div className="flex flex-wrap gap-1">
                              {row.flags.shared_cid && (
                                <span className="badge badge-danger">
                                  Shared CID
                                </span>
                              )}
                              {row.flags.shared_ip && (
                                <span className="badge badge-amber">
                                  Shared IP
                                </span>
                              )}
                              {row.flags.multiple_cids && (
                                <span className="badge badge-amber">
                                  Multi-CID
                                </span>
                              )}
                              {row.flags.multiple_countries && (
                                <span className="badge badge-danger">
                                  Multi-country
                                </span>
                              )}
                              {!row.flagged && (
                                <span className="badge badge-success">
                                  Clear
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!filteredCidEvents.length && (
                  <Empty>
                    No CID login events were available for this range.
                  </Empty>
                )}
                <div className="mt-5 grid gap-4 xl:grid-cols-2">
                  <div className="overflow-hidden rounded-lg border border-[rgb(var(--color-border))]">
                    <div className="border-b border-[rgb(var(--color-border))] p-3">
                      <h3 className="text-xs font-semibold">
                        Grouped by CID (shared device)
                      </h3>
                      <p className="mt-1 text-[10px] text-muted">
                        Two or more accounts observed on one Computer ID are
                        flagged.
                      </p>
                    </div>
                    <div className="max-h-80 overflow-auto">
                      {filteredCidGroups.map((row) => (
                        <div
                          key={row.computer_id}
                          className="border-t border-[rgb(var(--color-border-soft))] p-3 text-xs first:border-t-0"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <code
                              className="truncate text-[10px]"
                              title={row.computer_id}
                            >
                              {row.computer_id}
                            </code>
                            <span
                              className={
                                row.login_count >= 2
                                  ? "badge badge-danger"
                                  : "badge"
                              }
                            >
                              {row.login_count} account(s)
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {row.logins.map((login) => (
                              <span key={login} className="badge">
                                #{login}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                      {!filteredCidGroups.length && (
                        <Empty>No CID groups match.</Empty>
                      )}
                    </div>
                  </div>
                  <div className="overflow-hidden rounded-lg border border-[rgb(var(--color-border))]">
                    <div className="border-b border-[rgb(var(--color-border))] p-3">
                      <h3 className="text-xs font-semibold">
                        Grouped by IP (shared network)
                      </h3>
                      <p className="mt-1 text-[10px] text-muted">
                        Three or more distinct CIDs on one IP are flagged.
                      </p>
                    </div>
                    <div className="max-h-80 overflow-auto">
                      {filteredIpGroups.map((row) => (
                        <div
                          key={row.ip_address}
                          className="border-t border-[rgb(var(--color-border-soft))] p-3 text-xs first:border-t-0"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <code>{row.ip_address}</code>
                            <span
                              className={
                                row.cid_count >= 3
                                  ? "badge badge-danger"
                                  : "badge"
                              }
                            >
                              {row.cid_count} CID(s) · {row.login_count}{" "}
                              account(s)
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {row.logins.slice(0, 20).map((login) => (
                              <span key={login} className="badge">
                                #{login}
                              </span>
                            ))}
                            {row.logins.length > 20 && (
                              <span className="badge">
                                +{row.logins.length - 20}
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                      {!filteredIpGroups.length && (
                        <Empty>No IP groups match.</Empty>
                      )}
                    </div>
                  </div>
                </div>
                <p className="mt-3 text-[10px] text-muted">
                  Source: {cidAudit.source} · fresh events:{" "}
                  {cidAudit.fresh_events}
                </p>
              </div>
            ) : (
              <Empty>
                Run the audit to read retained MT5 client-login journal
                evidence.
              </Empty>
            )}
          </AnimatedCard>
          <Panel
            title="Verified Shared Attributes"
            count={live.related_accounts?.length || 0}
          >
            <div className="max-h-[650px] overflow-y-auto p-3 scrollbar-hidden">
              {(live.related_accounts || []).map((cluster, index) => (
                <div
                  key={`${cluster.type}-${cluster.value}-${index}`}
                  className="mb-2 rounded-lg border border-[rgb(var(--color-border))] p-3"
                >
                  <div className="flex justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold">
                        {cluster.type.replaceAll("_", " ")}
                      </p>
                      <p className="mt-1 font-mono text-xs text-secondary">
                        {cluster.value}
                      </p>
                    </div>
                    <span className="badge">{cluster.count} accounts</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {cluster.accounts.map((row) => (
                      <span className="badge" key={row.login}>
                        #{row.login} {row.name}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 text-[10px] text-muted">
                    {cluster.source} · {cluster.confidence}
                  </p>
                </div>
              ))}
              {!live.related_accounts?.length && (
                <Empty>No verified shared account attributes.</Empty>
              )}
            </div>
          </Panel>
          <Panel
            title="Coordinated Position Timing"
            count={live.coordinated_positions?.length || 0}
          >
            <div className="max-h-[650px] overflow-y-auto p-3 scrollbar-hidden">
              {(live.coordinated_positions || []).map((row, index) => (
                <div
                  className="mb-2 rounded-lg border border-[rgb(var(--color-border))] p-3"
                  key={index}
                >
                  <div className="flex justify-between">
                    <b className="text-xs">
                      {row.symbol} {row.side}
                    </b>
                    <span className="badge badge-amber">
                      {row.time_gap_seconds}s
                    </span>
                  </div>
                  <p className="mt-2 text-xs">
                    Accounts {row.accounts.map((v) => `#${v}`).join(" · ")}
                  </p>
                  <p className="mt-1 text-[10px] text-muted">
                    {row.source} · timing similarity is not proof
                  </p>
                </div>
              ))}
              {!live.coordinated_positions?.length && (
                <Empty>No live timing correlations.</Empty>
              )}
            </div>
          </Panel>
        </div>
      )}

      {renderedTab === "limits" && (
        <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
          <AnimatedCard className="p-4">
            <h2 className="text-sm font-semibold">New exposure limit</h2>
            <form onSubmit={createLimit} className="mt-4 space-y-3">
              {[
                ["scope_type", ["account", "group", "symbol"]],
                [
                  "metric",
                  [
                    "gross_lots",
                    "net_lots",
                    "floating_loss",
                    "gross_exposure",
                    "margin_level_below",
                    "concentration_pct",
                  ],
                ],
                ["escalation_level", ["warning", "high", "critical"]],
              ].map(([key, values]) => (
                <select
                  key={key}
                  className="input w-full"
                  value={limitForm[key]}
                  onChange={(e) =>
                    setLimitForm({ ...limitForm, [key]: e.target.value })
                  }
                >
                  {values.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              ))}
              <input
                required
                className="input w-full"
                placeholder="Login, group or symbol"
                value={limitForm.scope_value}
                onChange={(e) =>
                  setLimitForm({ ...limitForm, scope_value: e.target.value })
                }
              />
              <input
                required
                type="number"
                step="any"
                min="0.0001"
                className="input w-full"
                placeholder="Threshold"
                value={limitForm.threshold}
                onChange={(e) =>
                  setLimitForm({ ...limitForm, threshold: e.target.value })
                }
              />
              <label className="text-xs text-muted">
                Warning at %
                <input
                  type="number"
                  min="1"
                  max="100"
                  className="input mt-2 w-full"
                  value={limitForm.warning_pct}
                  onChange={(e) =>
                    setLimitForm({ ...limitForm, warning_pct: e.target.value })
                  }
                />
              </label>
              <button className="btn-primary">
                <Save size={14} />
                Activate limit
              </button>
            </form>
          </AnimatedCard>
          <Panel title="Live Limit Monitor" count={live.limits?.length || 0}>
            <div className="max-h-[680px] overflow-y-auto scrollbar-hidden">
              {(live.limits || []).map((row) => (
                <div
                  key={row.id}
                  className="flex items-center justify-between gap-3 border-b border-[rgb(var(--color-border))] p-4"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <b className="text-xs">
                        {row.scope_type} · {row.scope_value}
                      </b>
                      <span
                        className={
                          row.breached
                            ? "badge badge-danger"
                            : row.warning
                              ? "badge badge-amber"
                              : "badge badge-success"
                        }
                      >
                        {row.breached
                          ? "Breach"
                          : row.warning
                            ? "Warning"
                            : "Normal"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-secondary">
                      {row.metric}: {row.current_value ?? "Unavailable"} /{" "}
                      {row.threshold}
                    </p>
                    <p className="mt-1 text-[10px] text-muted">{row.source}</p>
                  </div>
                  <button
                    className="btn-ghost"
                    onClick={() => removeLimit(row.id)}
                    aria-label="Delete limit"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              {!live.limits?.length && (
                <Empty>No exposure limits configured.</Empty>
              )}
            </div>
          </Panel>
        </div>
      )}

      {renderedTab === "intelligence" && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button
              className="btn-primary"
              disabled={loading}
              onClick={runIntelligence}
            >
              <RefreshCw size={14} />
              {loading ? "Scanning 90-day MT5 history…" : "Run 90-day verified scan"}
            </button>
          </div>
          {intelligence && (
            <>
              <Panel
                title="Client Profitability & Observed Value"
                count={intelligence.clients?.length || 0}
              >
                <div className="max-h-[520px] overflow-auto scrollbar-hidden">
                  <table className="w-full min-w-[1100px] text-xs">
                    <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
                      <tr>
                        {[
                          "Client",
                          "Group",
                          "Trades",
                          "Volume",
                          "Realized P&L",
                          "Win Rate",
                          "Value Score",
                          "P&L Change",
                          "Concentration",
                          "Deposit Anomaly",
                        ].map((v) => (
                          <th
                            className="px-3 py-3 text-left text-muted"
                            key={v}
                          >
                            {v}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {intelligence.clients.map((row) => (
                        <tr
                          className="border-t border-[rgb(var(--color-border))]"
                          key={row.login}
                        >
                          <td className="px-3 py-3">
                            {row.name || `#${row.login}`}
                            <div className="text-muted">#{row.login}</div>
                          </td>
                          <td className="px-3 py-3">{row.group}</td>
                          <td className="px-3 py-3">{row.trades}</td>
                          <td className="px-3 py-3">{row.volume}</td>
                          <td
                            className={`px-3 py-3 ${row.realized_pnl >= 0 ? "text-success" : "text-danger"}`}
                          >
                            {money.format(row.realized_pnl)}
                          </td>
                          <td className="px-3 py-3">{row.win_rate}%</td>
                          <td className="px-3 py-3">
                            {row.observed_value_score}
                          </td>
                          <td className="px-3 py-3">
                            {money.format(row.pnl_change)}
                          </td>
                          <td className="px-3 py-3">
                            {row.symbol_concentration_pct}%
                          </td>
                          <td className="px-3 py-3">
                            {row.deposit_profit_anomaly ? (
                              <span className="badge badge-danger">Review</span>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
              <div className="grid gap-4 xl:grid-cols-3">
                <Panel
                  title="Coordinated Trading Candidates"
                  count={intelligence.copy_trading_signals?.length || 0}
                >
                  <div className="max-h-80 overflow-y-auto p-3 scrollbar-hidden">
                    {intelligence.copy_trading_signals?.map((row, i) => (
                      <p
                        className="mb-2 rounded-lg bg-[var(--inp-bg)] p-2 text-xs"
                        key={i}
                      >
                        {row.symbol} {row.side} ·{" "}
                        {row.accounts.map((v) => `#${v}`).join(", ")}
                      </p>
                    ))}
                  </div>
                </Panel>
                <Panel
                  title="Latency Timeline Candidates"
                  count={intelligence.latency_candidates?.length || 0}
                >
                  <div className="max-h-80 overflow-y-auto p-3 scrollbar-hidden">
                    {intelligence.latency_candidates?.map((row, i) => (
                      <p
                        className="mb-2 rounded-lg bg-[var(--inp-bg)] p-2 text-xs"
                        key={i}
                      >
                        #{row.login} · {row.symbol} · {row.hold_seconds}s ·{" "}
                        {money.format(row.pnl)}
                      </p>
                    ))}
                  </div>
                </Panel>
                <Panel
                  title="Deposit / Profit Anomalies"
                  count={intelligence.deposit_profit_anomalies?.length || 0}
                >
                  <div className="max-h-80 overflow-y-auto p-3 scrollbar-hidden">
                    {intelligence.deposit_profit_anomalies?.map((row) => (
                      <p
                        className="mb-2 rounded-lg bg-[var(--inp-bg)] p-2 text-xs"
                        key={row.login}
                      >
                        #{row.login} · deposits {money.format(row.deposits)} ·
                        P&L {money.format(row.realized_pnl)}
                      </p>
                    ))}
                  </div>
                </Panel>
              </div>
            </>
          )}
          <RiskControls profile={profile} selectedGroups={groups} active />
        </div>
      )}

      {renderedTab === "reconciliation" && (
        <Panel
          title="Automated Daily Reconciliation"
          count={reconciliations.length}
        >
          <div className="p-4">
            <button className="btn-primary" onClick={runReconciliation}>
              <RefreshCw size={14} />
              Run now
            </button>
            <div className="mt-4 max-h-[520px] overflow-y-auto scrollbar-hidden">
              {reconciliations.map((row) => (
                <div
                  key={row.report_date}
                  className="grid gap-2 border-t border-[rgb(var(--color-border))] py-3 text-xs sm:grid-cols-6"
                >
                  <b>{row.report_date}</b>
                  <span>{row.status}</span>
                  <span>Accounts {row.metrics.accounts || 0}</span>
                  <span>{money.format(row.metrics.balance || 0)} balance</span>
                  <span>{money.format(row.metrics.equity || 0)} equity</span>
                  <span>{row.metrics.open_positions || 0} positions</span>
                </div>
              ))}
              {!reconciliations.length && (
                <Empty>No reconciliation snapshots.</Empty>
              )}
            </div>
          </div>
        </Panel>
      )}

      {renderedTab === "channels" && (
        <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
          <AnimatedCard className="p-4">
            <h2 className="text-sm font-semibold">Notification channel</h2>
            <form onSubmit={createChannel} className="mt-4 space-y-3">
              <input
                required
                className="input w-full"
                placeholder="Channel name"
                value={channelForm.name}
                onChange={(e) =>
                  setChannelForm({ ...channelForm, name: e.target.value })
                }
              />
              <select
                className="input w-full"
                value={channelForm.channel_type}
                onChange={(e) =>
                  setChannelForm({
                    ...channelForm,
                    channel_type: e.target.value,
                  })
                }
              >
                {["telegram", "email", "webhook"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
              <input
                className="input w-full"
                placeholder="Email or webhook URL"
                value={channelForm.destination}
                onChange={(e) =>
                  setChannelForm({
                    ...channelForm,
                    destination: e.target.value,
                  })
                }
              />
              <select
                className="input w-full"
                value={channelForm.min_severity}
                onChange={(e) =>
                  setChannelForm({
                    ...channelForm,
                    min_severity: e.target.value,
                  })
                }
              >
                {["info", "warning", "high", "critical"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
              <button className="btn-primary">
                <Save size={14} />
                Add channel
              </button>
            </form>
          </AnimatedCard>
          <Panel title="Escalation Channels" count={channels.length}>
            <div>
              {channels.map((row) => (
                <div
                  key={row.id}
                  className="flex items-center justify-between border-b border-[rgb(var(--color-border))] p-4"
                >
                  <div>
                    <b className="text-xs">{row.name}</b>
                    <p className="mt-1 text-[10px] text-muted">
                      {row.channel_type} · minimum {row.min_severity} ·{" "}
                      {row.destination}
                    </p>
                  </div>
                  <button
                    className="btn-ghost"
                    onClick={() => removeChannel(row.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      )}

      {renderedTab === "audit" && (
        <Panel title="Dealer Action Audit" count={audit.length}>
          <div className="max-h-[700px] overflow-y-auto scrollbar-hidden">
            {audit.map((row) => (
              <div
                key={row.id}
                className="grid gap-2 border-b border-[rgb(var(--color-border))] px-4 py-3 text-xs lg:grid-cols-[170px_180px_1fr_180px]"
              >
                <span>{new Date(row.created_at + "Z").toLocaleString()}</span>
                <b>{row.actor}</b>
                <span>
                  {row.action} · {row.entity_type} {row.entity_id}
                </span>
                <span className="truncate text-muted">
                  {JSON.stringify(row.details)}
                </span>
              </div>
            ))}
            {!audit.length && <Empty>No audit events.</Empty>}
          </div>
        </Panel>
      )}
    </div>
  );
}
