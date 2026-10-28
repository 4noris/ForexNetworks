/* eslint-disable no-unused-vars -- dynamic icon components are referenced from JSX */
import React, { useCallback, useDeferredValue, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bell,
  Check,
  ChevronRight,
  Clock3,
  Edit3,
  Eye,
  FileClock,
  Filter,
  GitBranch,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  UserCog,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import {
  applyRiskManualOverride,
  createRiskAutomationRule,
  decideRiskApproval,
  deleteRiskAutomationRule,
  getStoredUser,
  runRiskAutomation,
  simulateRiskAutomationRule,
  updateRiskAutomationRule,
} from "../lib/api";
import { useRiskAutomationOverview } from "../hooks/useManagerQueries";

const CONDITION_TYPES = [
  ["toxic_behavior", "Toxic behavior", "toxicity_score"],
  ["vps_usage", "VPS usage", "vps_usage"],
  ["scalping_activity", "Scalping activity", "scalping_rate"],
  ["high_win_rate", "High win rate", "win_rate"],
  ["abnormal_profitability", "Abnormal profitability", "profit_pct"],
  ["excessive_trading_frequency", "Trading frequency", "trades_per_day"],
  ["hedging_activity", "Hedging activity", "hedged_symbols"],
  ["suspicious_patterns", "Suspicious patterns", "risk_score"],
  ["custom", "Custom data field", ""],
];
const OPERATORS = [
  ["gt", ">"],
  ["gte", "≥"],
  ["lt", "<"],
  ["lte", "≤"],
  ["equals", "="],
  ["not_equals", "≠"],
  ["contains", "contains"],
  ["in", "in list"],
];
const ACTION_TYPES = [
  ["move_group", "Move to target group"],
  ["disable_trading", "Disable trading"],
  ["disable_account", "Disable account"],
  ["read_only", "Set read-only"],
  ["restrict_symbols", "Restrict symbols"],
  ["restrict_lot_size", "Restrict lot size"],
  ["set_leverage", "Set leverage"],
  ["add_label", "Add risk label"],
  ["monitoring_list", "Add to monitoring list"],
  ["manual_review", "Require manual review"],
];
const ALERT_EVENTS = [
  ["triggered", "Condition triggered"],
  ["executed", "Action completed"],
  ["failed", "Execution failed"],
  ["approval_required", "Approval required"],
  ["reverted", "Client reverted"],
];
const TABS = [
  ["rules", SlidersHorizontal, "Rules"],
  ["monitor", Activity, "Live monitor"],
  ["approvals", ShieldCheck, "Approvals"],
  ["history", FileClock, "Execution history"],
  ["audit", Eye, "Audit log"],
];
const EMPTY_OVERVIEW = {
  rules: [],
  history: [],
  approvals: [],
  clients: [],
  groups: [],
  audit: [],
  controls: [],
  summary: {},
};

const emptyRule = () => ({
  name: "",
  description: "",
  enabled: false,
  priority: 100,
  source_group: "*",
  target_group: "",
  condition_logic: "AND",
  delay_minutes: 0,
  require_approval: false,
  revert_when_clear: false,
  simulation_mode: true,
  cooldown_minutes: 60,
  excluded_logins: [],
  conditions: [
    {
      type: "high_win_rate",
      field: "win_rate",
      operator: "gt",
      value: 80,
      window_days: 30,
    },
  ],
  actions: [
    { type: "move_group", value: null, target_group: "", symbols: [] },
    { type: "monitoring_list", value: null, target_group: "", symbols: [] },
  ],
  alert_events: [
    "triggered",
    "executed",
    "failed",
    "approval_required",
    "reverted",
  ],
});

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(
    String(value).endsWith("Z") || String(value).includes("+")
      ? value
      : `${value}Z`,
  );
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatEquity(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

function Status({ value }) {
  const danger = ["failed", "rejected", "offline"].includes(value);
  const good = [
    "success",
    "executed",
    "approved",
    "connected",
    "applied",
  ].includes(value);
  return (
    <span
      className={`badge ${danger ? "badge-danger" : good ? "badge-success" : "badge-amber"}`}
    >
      {String(value || "unknown").replaceAll("_", " ")}
    </span>
  );
}

function Metric({ label, value, icon: Icon, tone = "" }) {
  return (
    <AnimatedCard className="p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[.2em] text-muted">
            {label}
          </p>
          <p className={`mt-2 text-2xl font-semibold ${tone}`}>{value}</p>
        </div>
        <div className="rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-2.5">
          <Icon size={17} />
        </div>
      </div>
    </AnimatedCard>
  );
}

function Toggle({ checked, onChange, label }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 text-xs">
      <span>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition-colors ${checked ? "bg-emerald-500" : "bg-[rgb(var(--color-border))]"}`}
      >
        <span
          className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${checked ? "left-6" : "left-1"}`}
        />
      </button>
    </label>
  );
}

function RuleEditor({ initial, groups, onClose, onSaved }) {
  const [rule, setRule] = useState(() =>
    initial ? { ...emptyRule(), ...structuredClone(initial) } : emptyRule(),
  );
  const [saving, setSaving] = useState(false);
  const update = (key, value) =>
    setRule((current) => ({ ...current, [key]: value }));
  const updateCondition = (index, patch) =>
    update(
      "conditions",
      rule.conditions.map((item, idx) =>
        idx === index ? { ...item, ...patch } : item,
      ),
    );
  const updateAction = (index, patch) =>
    update(
      "actions",
      rule.actions.map((item, idx) =>
        idx === index ? { ...item, ...patch } : item,
      ),
    );

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      const payload = {
        ...rule,
        priority: Number(rule.priority),
        delay_minutes: Number(rule.delay_minutes),
        cooldown_minutes: Number(rule.cooldown_minutes),
        excluded_logins: [...new Set(rule.excluded_logins || [])]
          .map(Number)
          .filter((login) => Number.isInteger(login) && login > 0),
        conditions: rule.conditions.map((item) => ({
          ...item,
          value: ["vps_usage"].includes(item.type)
            ? String(item.value).toLowerCase() === "true"
            : item.value,
          window_days: Number(item.window_days || 0),
        })),
        actions: rule.actions.map((item) => ({
          ...item,
          value: ["restrict_lot_size", "set_leverage"].includes(item.type)
            ? Number(item.value)
            : item.value,
          target_group: item.target_group || "",
        })),
      };
      if (initial?.id) await updateRiskAutomationRule(initial.id, payload);
      else await createRiskAutomationRule(payload);
      toast.success(initial?.id ? "Rule updated" : "Rule created");
      onSaved();
      onClose();
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/45 backdrop-blur-sm">
      <form
        onSubmit={save}
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-5 shadow-2xl scrollbar-hidden"
      >
        <div className="mb-5 flex items-start justify-between">
          <div>
            <p className="text-[10px] uppercase tracking-[.25em] text-muted">
              Risk automation
            </p>
            <h2 className="mt-1 text-xl font-semibold">
              {initial?.id ? "Edit rule" : "Create rule"}
            </h2>
            <p className="mt-1 text-xs text-secondary">
              Condition → current group → target group → action → alert
            </p>
          </div>
          <button type="button" className="btn-ghost !p-2" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
        <div className="space-y-5">
          <section className="card p-4">
            <h3 className="mb-4 text-sm font-semibold">
              Rule identity and routing
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-muted">
                Rule name
                <input
                  required
                  className="field-input mt-1.5"
                  value={rule.name}
                  onChange={(e) => update("name", e.target.value)}
                  placeholder="High Win Rate Client Monitoring"
                />
              </label>
              <label className="text-xs text-muted">
                Priority
                <input
                  required
                  min="1"
                  type="number"
                  className="field-input mt-1.5"
                  value={rule.priority}
                  onChange={(e) => update("priority", e.target.value)}
                />
              </label>
              <label className="text-xs text-muted">
                From group
                <select
                  className="field-input mt-1.5"
                  value={rule.source_group}
                  onChange={(e) => update("source_group", e.target.value)}
                >
                  <option value="*">All groups (*)</option>
                  {groups.map((group) => (
                    <option key={group}>{group}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-muted">
                To group
                <select
                  className="field-input mt-1.5"
                  value={rule.target_group}
                  onChange={(e) => update("target_group", e.target.value)}
                >
                  <option value="">No group move</option>
                  {groups.map((group) => (
                    <option key={group}>{group}</option>
                  ))}
                </select>
              </label>
            </div>
            <label className="mt-3 block text-xs text-muted">
              Description
              <textarea
                className="field-input mt-1.5 min-h-20"
                value={rule.description}
                onChange={(e) => update("description", e.target.value)}
                placeholder="Why this control exists and how operators should respond."
              />
            </label>
            <label className="mt-3 block text-xs text-muted">
              Exclude accounts
              <input
                className="field-input mt-1.5"
                value={(rule.excluded_logins || []).join(", ")}
                onChange={(event) =>
                  update(
                    "excluded_logins",
                    event.target.value
                      .split(/[\s,]+/)
                      .map((value) => Number(value))
                      .filter((value) => Number.isInteger(value) && value > 0),
                  )
                }
                placeholder="100001, 100002"
              />
              <span className="mt-1 block text-[10px]">
                These logins are skipped before analysis, simulation, approval,
                and execution.
              </span>
            </label>
          </section>

          <section className="card p-4">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold">Condition builder</h3>
                <p className="mt-1 text-[11px] text-muted">
                  Combine verified live fields with {rule.condition_logic}{" "}
                  logic.
                </p>
              </div>
              <select
                className="field-input !w-auto !py-2"
                value={rule.condition_logic}
                onChange={(e) => update("condition_logic", e.target.value)}
              >
                <option>AND</option>
                <option>OR</option>
              </select>
            </div>
            <div className="space-y-2">
              {rule.conditions.map((condition, index) => (
                <div
                  key={index}
                  className="grid gap-2 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3 md:grid-cols-[1.4fr_1.1fr_.7fr_1fr_.65fr_auto]"
                >
                  <select
                    className="field-input !py-2"
                    value={condition.type}
                    onChange={(e) => {
                      const item = CONDITION_TYPES.find(
                        ([type]) => type === e.target.value,
                      );
                      updateCondition(index, {
                        type: e.target.value,
                        field: item?.[2] || "",
                      });
                    }}
                  >
                    {CONDITION_TYPES.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <input
                    className="field-input !py-2"
                    disabled={condition.type !== "custom"}
                    value={condition.field}
                    onChange={(e) =>
                      updateCondition(index, { field: e.target.value })
                    }
                    placeholder="data field"
                  />
                  <select
                    className="field-input !py-2"
                    value={condition.operator}
                    onChange={(e) =>
                      updateCondition(index, { operator: e.target.value })
                    }
                  >
                    {OPERATORS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                  {condition.type === "vps_usage" ? (
                    <select
                      className="field-input !py-2"
                      value={String(condition.value)}
                      onChange={(e) =>
                        updateCondition(index, { value: e.target.value })
                      }
                    >
                      <option value="true">Detected</option>
                      <option value="false">Not detected</option>
                    </select>
                  ) : (
                    <input
                      required
                      className="field-input !py-2"
                      value={condition.value}
                      onChange={(e) =>
                        updateCondition(index, { value: e.target.value })
                      }
                      placeholder="threshold"
                    />
                  )}
                  <input
                    className="field-input !py-2"
                    type="number"
                    min="0"
                    title="Lookback days"
                    value={condition.window_days || 0}
                    onChange={(e) =>
                      updateCondition(index, { window_days: e.target.value })
                    }
                  />
                  <button
                    type="button"
                    className="btn-ghost !p-2"
                    disabled={rule.conditions.length === 1}
                    onClick={() =>
                      update(
                        "conditions",
                        rule.conditions.filter((_, idx) => idx !== index),
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              className="btn-ghost mt-3 !py-2"
              onClick={() =>
                update("conditions", [
                  ...rule.conditions,
                  {
                    type: "custom",
                    field: "",
                    operator: "gte",
                    value: "",
                    window_days: 0,
                  },
                ])
              }
            >
              <Plus size={14} />
              Condition
            </button>
          </section>

          <section className="card p-4">
            <div className="mb-4">
              <h3 className="text-sm font-semibold">Automated actions</h3>
              <p className="mt-1 text-[11px] text-muted">
                Actions run in order; MT5 changes and internal controls share
                one audited result.
              </p>
            </div>
            <div className="space-y-2">
              {rule.actions.map((action, index) => (
                <div
                  key={index}
                  className="flex flex-wrap gap-2 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3"
                >
                  <select
                    className="field-input !w-56 !py-2"
                    value={action.type}
                    onChange={(e) =>
                      updateAction(index, {
                        type: e.target.value,
                        value: null,
                        symbols: [],
                      })
                    }
                  >
                    {ACTION_TYPES.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                  {["restrict_lot_size", "set_leverage", "add_label"].includes(
                    action.type,
                  ) && (
                    <input
                      required
                      className="field-input !w-44 !py-2"
                      type={action.type === "add_label" ? "text" : "number"}
                      min="0.01"
                      step="any"
                      value={action.value ?? ""}
                      onChange={(e) =>
                        updateAction(index, { value: e.target.value })
                      }
                      placeholder={
                        action.type === "add_label" ? "Label" : "Value"
                      }
                    />
                  )}{" "}
                  {action.type === "restrict_symbols" && (
                    <input
                      required
                      className="field-input min-w-52 flex-1 !py-2"
                      value={(action.symbols || []).join(", ")}
                      onChange={(e) =>
                        updateAction(index, {
                          symbols: e.target.value
                            .split(",")
                            .map((v) => v.trim())
                            .filter(Boolean),
                        })
                      }
                      placeholder="EURUSD, XAUUSD"
                    />
                  )}
                  <button
                    type="button"
                    className="btn-ghost ml-auto !p-2"
                    disabled={rule.actions.length === 1}
                    onClick={() =>
                      update(
                        "actions",
                        rule.actions.filter((_, idx) => idx !== index),
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              className="btn-ghost mt-3 !py-2"
              onClick={() =>
                update("actions", [
                  ...rule.actions,
                  {
                    type: "monitoring_list",
                    value: null,
                    target_group: "",
                    symbols: [],
                  },
                ])
              }
            >
              <Plus size={14} />
              Action
            </button>
          </section>

          <div className="grid gap-5 md:grid-cols-2">
            <section className="card space-y-3 p-4">
              <h3 className="text-sm font-semibold">Execution policy</h3>
              <Toggle
                label="Rule enabled"
                checked={rule.enabled}
                onChange={(value) => update("enabled", value)}
              />
              <Toggle
                label="Simulation mode"
                checked={rule.simulation_mode}
                onChange={(value) => update("simulation_mode", value)}
              />
              <Toggle
                label="Require approval"
                checked={rule.require_approval}
                onChange={(value) => update("require_approval", value)}
              />
              <Toggle
                label="Revert group when clear"
                checked={rule.revert_when_clear}
                onChange={(value) => update("revert_when_clear", value)}
              />
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-muted">
                  Delay (minutes)
                  <input
                    type="number"
                    min="0"
                    className="field-input mt-1.5 !py-2"
                    value={rule.delay_minutes}
                    onChange={(e) => update("delay_minutes", e.target.value)}
                  />
                </label>
                <label className="text-xs text-muted">
                  Cooldown (minutes)
                  <input
                    type="number"
                    min="0"
                    className="field-input mt-1.5 !py-2"
                    value={rule.cooldown_minutes}
                    onChange={(e) => update("cooldown_minutes", e.target.value)}
                  />
                </label>
              </div>
            </section>
            <section className="card p-4">
              <div className="flex items-center gap-2">
                <Bell size={15} />
                <h3 className="text-sm font-semibold">Telegram alerts</h3>
              </div>
              <div className="mt-3 space-y-2">
                {ALERT_EVENTS.map(([value, label]) => (
                  <label
                    key={value}
                    className="flex cursor-pointer items-center gap-2 text-xs"
                  >
                    <input
                      type="checkbox"
                      checked={rule.alert_events.includes(value)}
                      onChange={(e) =>
                        update(
                          "alert_events",
                          e.target.checked
                            ? [...rule.alert_events, value]
                            : rule.alert_events.filter(
                                (item) => item !== value,
                              ),
                        )
                      }
                    />
                    {label}
                  </label>
                ))}
              </div>
            </section>
          </div>
          <div className="sticky bottom-0 flex justify-end gap-2 border-t border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))]/95 py-4 backdrop-blur">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button disabled={saving} className="btn-primary">
              <Save size={15} />
              {saving ? "Saving…" : "Save rule"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export default function RiskAutomation() {
  const user = getStoredUser();
  const isAdmin = Boolean(user?.is_admin);
  const [activeTab, setActiveTab] = useState("rules");
  const renderedTab = useDeferredValue(activeTab);
  const [monitorLimit, setMonitorLimit] = useState(200);
  const [editor, setEditor] = useState(null);
  const [query, setQuery] = useState("");
  const [simulation, setSimulation] = useState(null);
  const [overrideClient, setOverrideClient] = useState(null);
  const [override, setOverride] = useState({
    type: "move_group",
    target_group: "",
    value: null,
    reason: "",
  });
  const {
    data: overview,
    isFetching: coreFetching,
    refetch,
  } = useRiskAutomationOverview(isAdmin);
  const needsClients = activeTab === "monitor";
  const needsAudit = activeTab === "audit";
  const {
    data: detailOverview,
    isFetching: detailFetching,
    refetch: refetchDetail,
  } = useRiskAutomationOverview(isAdmin && (needsClients || needsAudit), 250, {
    includeClients: needsClients,
    includeAudit: needsAudit,
  });
  const coreData = overview || EMPTY_OVERVIEW;
  const data = useMemo(
    () => ({
      ...coreData,
      clients: needsClients ? detailOverview?.clients || [] : [],
      audit: needsAudit ? detailOverview?.audit || [] : [],
      groups: detailOverview?.groups?.length
        ? detailOverview.groups
        : coreData.groups || [],
      manager: detailOverview?.manager || coreData.manager,
    }),
    [coreData, detailOverview, needsClients, needsAudit],
  );
  const isFetching = coreFetching || detailFetching;

  const load = useCallback(async () => {
    await Promise.all([
      refetch(),
      needsClients || needsAudit ? refetchDetail() : Promise.resolve(),
    ]);
  }, [refetch, refetchDetail, needsClients, needsAudit]);
  const pending = useMemo(
    () => data.approvals.filter((item) => item.status === "pending"),
    [data.approvals],
  );
  const normalizedQuery = query.toLowerCase().trim();
  const rules = useMemo(
    () =>
      data.rules.filter(
        (rule) =>
          !normalizedQuery ||
          `${rule.name} ${rule.source_group} ${rule.target_group}`
            .toLowerCase()
            .includes(normalizedQuery),
      ),
    [data.rules, normalizedQuery],
  );
  const clients = useMemo(
    () =>
      data.clients.filter(
        (client) =>
          !normalizedQuery ||
          `${client.login} ${client.name} ${client.group}`
            .toLowerCase()
            .includes(normalizedQuery),
      ),
    [data.clients, normalizedQuery],
  );
  const visibleClients = clients.slice(0, monitorLimit);
  const controlsByLogin = useMemo(
    () => new Map(data.controls.map((item) => [item.client_login, item])),
    [data.controls],
  );

  const mutate = async (operation, success) => {
    try {
      const result = await operation();
      toast.success(typeof success === "function" ? success(result) : success);
      await load(true);
      return result;
    } catch (error) {
      toast.error(error.message);
      return null;
    }
  };
  const toggleRule = (rule) =>
    mutate(
      () =>
        updateRiskAutomationRule(rule.id, { ...rule, enabled: !rule.enabled }),
      rule.enabled ? "Rule disabled" : "Rule enabled",
    );
  const simulate = async (rule) => {
    try {
      const result = await simulateRiskAutomationRule(rule.id);
      setSimulation({ rule, result });
    } catch (error) {
      toast.error(error.message);
    }
  };
  const executeOverride = async (event) => {
    event.preventDefault();
    const action = {
      type: override.type,
      value: override.value,
      target_group: override.target_group,
      symbols: [],
    };
    await mutate(
      () =>
        applyRiskManualOverride({
          client_login: overrideClient.login,
          client_name: overrideClient.name,
          current_group: overrideClient.group,
          actions: [action],
          reason: override.reason,
        }),
      "Manual override applied",
    );
    setOverrideClient(null);
  };

  if (!isAdmin)
    return (
      <div className="mx-auto mt-20 max-w-xl card p-8 text-center">
        <ShieldCheck className="mx-auto text-muted" size={32} />
        <h1 className="mt-4 text-xl font-semibold">
          Administrator access required
        </h1>
        <p className="mt-2 text-sm text-secondary">
          Risk rules can move clients and change account rights, so this
          workspace is restricted to administrators.
        </p>
      </div>
    );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${data.manager?.status === "connected" ? "bg-emerald-500" : "bg-red-500"}`}
            />
            <p className="text-[10px] font-semibold uppercase tracking-[.24em] text-muted">
              Advanced client risk management
            </p>
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">
            Automated Group Assignment
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-secondary">
            Monitor client behavior, apply account controls, route approvals,
            and preserve a complete audit trail.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={() => load()}>
            <RefreshCw className={isFetching ? "animate-spin" : ""} size={15} />
            Refresh
          </button>
          <button className="btn-primary" onClick={() => setEditor("new")}>
            <Plus size={15} />
            New rule
          </button>
        </div>
      </header>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          label="Automation rules"
          value={data.summary.rules || 0}
          icon={GitBranch}
        />
        <Metric
          label="Enabled"
          value={data.summary.enabled || 0}
          icon={Play}
          tone="text-success"
        />
        <Metric
          label="Pending approvals"
          value={data.summary.pending_approvals || 0}
          icon={Clock3}
          tone={pending.length ? "text-amber-500" : ""}
        />
        <Metric
          label="Executions today"
          value={data.summary.executions_24h || 0}
          icon={Activity}
        />
        <Metric
          label="Failures"
          value={data.summary.failures || 0}
          icon={AlertTriangle}
          tone={data.summary.failures ? "text-danger" : ""}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[rgb(var(--color-border))]">
        <div className="flex overflow-x-auto">
          {TABS.map(([value, Icon, label]) => (
            <button
              key={value}
              onClick={() => setActiveTab(value)}
              className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${activeTab === value ? "border-current text-primary" : "border-transparent text-muted"}`}
            >
              <Icon size={14} />
              {label}
              {value === "approvals" && pending.length > 0 && (
                <span className="rounded-full bg-amber-500 px-1.5 py-0.5 text-[9px] text-black">
                  {pending.length}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="relative mb-2">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
            size={14}
          />
          <input
            className="field-input !w-64 !py-2 pl-9"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search client, rule or group"
          />
        </div>
      </div>

      {renderedTab === "rules" && (
        <div className="space-y-3">
          {rules.map((rule) => (
            <AnimatedCard key={rule.id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => toggleRule(rule)}
                      className={`relative h-6 w-11 rounded-full ${rule.enabled ? "bg-emerald-500" : "bg-[rgb(var(--color-border))]"}`}
                    >
                      <span
                        className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${rule.enabled ? "left-6" : "left-1"}`}
                      />
                    </button>
                    <h2 className="font-semibold">{rule.name}</h2>
                    <span className="badge">P{rule.priority}</span>
                    {rule.simulation_mode && (
                      <span className="badge badge-amber">Simulation</span>
                    )}
                    {rule.require_approval && (
                      <span className="badge">Approval</span>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-secondary">
                    {rule.description || "No description provided."}
                  </p>
                  <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-lg bg-[var(--inp-bg)] px-3 py-2">
                      {rule.conditions.length} condition
                      {rule.conditions.length !== 1 && "s"} ·{" "}
                      {rule.condition_logic}
                    </span>
                    <ChevronRight className="text-muted" size={14} />
                    <span className="rounded-lg bg-[var(--inp-bg)] px-3 py-2">
                      {rule.source_group}
                    </span>
                    <ChevronRight className="text-muted" size={14} />
                    <span className="rounded-lg bg-[var(--inp-bg)] px-3 py-2">
                      {rule.target_group || "No group move"}
                    </span>
                    <ChevronRight className="text-muted" size={14} />
                    <span className="rounded-lg bg-[var(--inp-bg)] px-3 py-2">
                      {rule.actions
                        .map((action) => action.type.replaceAll("_", " "))
                        .join(", ")}
                    </span>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    className="btn-ghost !p-2"
                    title="Simulate"
                    onClick={() => simulate(rule)}
                  >
                    <Play size={14} />
                  </button>
                  <button
                    className="btn-ghost !p-2"
                    title="Edit"
                    onClick={() => setEditor(rule)}
                  >
                    <Edit3 size={14} />
                  </button>
                  <button
                    className="btn-ghost !p-2 text-danger"
                    title="Delete"
                    onClick={() =>
                      window.confirm(`Delete ${rule.name}?`) &&
                      mutate(
                        () => deleteRiskAutomationRule(rule.id),
                        "Rule deleted",
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </AnimatedCard>
          ))}
          {!rules.length && (
            <div className="card p-10 text-center">
              <GitBranch className="mx-auto text-muted" />
              <p className="mt-3 text-sm text-secondary">
                No rules match the current filter.
              </p>
            </div>
          )}
          <div className="flex justify-end">
            <button
              className="btn-ghost"
              onClick={() =>
                mutate(() => runRiskAutomation(), "Evaluation completed")
              }
            >
              <Play size={14} />
              Run all enabled rules now
            </button>
          </div>
        </div>
      )}

      {renderedTab === "monitor" && (
        <AnimatedCard className="overflow-hidden">
          <div className="border-b border-[rgb(var(--color-border))] p-4">
            <h2 className="text-sm font-semibold">
              Real-time client monitoring
            </h2>
            <p className="mt-1 text-[11px] text-muted">
              Live Manager cache · {clients.length} visible clients · internal
              restrictions are shown beside MT5 data
              {data.client_total > clients.length
                ? ` · first ${clients.length} of ${data.client_total} loaded for speed`
                : ""}
            </p>
          </div>
          <div className="max-h-[650px] overflow-auto scrollbar-hidden">
            <table className="w-full min-w-[1050px] text-xs">
              <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
                <tr>
                  {[
                    "Client",
                    "Group",
                    "Balance",
                    "Equity",
                    "Floating P&L",
                    "Positions",
                    "Gross lots",
                    "Leverage",
                    "Controls",
                    "",
                  ].map((value) => (
                    <th
                      key={value}
                      className="px-4 py-3 text-left font-medium text-muted"
                    >
                      {value}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleClients.map((client) => {
                  const control = controlsByLogin.get(client.login);
                  return (
                    <tr
                      key={client.login}
                      className="border-t border-[rgb(var(--color-border))]"
                    >
                      <td className="px-4 py-3">
                        <b>{client.name || "Unknown"}</b>
                        <div className="text-muted">#{client.login}</div>
                      </td>
                      <td className="px-4 py-3">{client.group}</td>
                      <td className="px-4 py-3">
                        ${client.balance.toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        {client.equity == null
                          ? "Unavailable"
                          : `$${client.equity.toLocaleString()}`}
                      </td>
                      <td
                        className={`px-4 py-3 ${client.floating_pnl < 0 ? "text-danger" : "text-success"}`}
                      >
                        ${client.floating_pnl.toLocaleString()}
                      </td>
                      <td className="px-4 py-3">{client.open_positions}</td>
                      <td className="px-4 py-3">{client.gross_lots}</td>
                      <td className="px-4 py-3">1:{client.leverage}</td>
                      <td className="px-4 py-3">
                        {control ? (
                          <div className="flex flex-wrap gap-1">
                            {control.monitoring ? (
                              <span className="badge">Monitoring</span>
                            ) : null}
                            {control.manual_review ? (
                              <span className="badge badge-amber">Review</span>
                            ) : null}
                            {control.labels?.map((label) => (
                              <span key={label} className="badge">
                                {label}
                              </span>
                            ))}
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          className="btn-ghost !px-3 !py-2"
                          onClick={() => {
                            setOverrideClient(client);
                            setOverride({
                              type: "move_group",
                              target_group: "",
                              value: null,
                              reason: "",
                            });
                          }}
                        >
                          <UserCog size={13} />
                          Override
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {visibleClients.length < clients.length && (
              <div className="sticky bottom-0 flex justify-center border-t border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3">
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  onClick={() => setMonitorLimit((value) => value + 200)}
                >
                  Show 200 more · {clients.length - visibleClients.length}{" "}
                  remaining
                </button>
              </div>
            )}
          </div>
        </AnimatedCard>
      )}

      {renderedTab === "approvals" && (
        <div className="space-y-3">
          {pending.map((item) => {
            const payload = item.payload || {};
            return (
              <AnimatedCard key={item.id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="flex gap-2">
                      <span className="badge badge-amber">Pending</span>
                      <span className="badge">#{payload.client?.login}</span>
                    </div>
                    <h2 className="mt-3 font-semibold">{payload.rule?.name}</h2>
                    <p className="mt-1 text-xs text-secondary">
                      {payload.client?.name || "Unknown client"} ·{" "}
                      {payload.client?.group} →{" "}
                      {payload.rule?.target_group || "account control"}
                    </p>
                    <p className="mt-2 text-[11px] text-muted">
                      Requested {formatDate(item.requested_at)} ·{" "}
                      {payload.rule?.actions
                        ?.map((action) => action.type.replaceAll("_", " "))
                        .join(", ")}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      className="btn-ghost text-danger"
                      onClick={() =>
                        mutate(
                          () =>
                            decideRiskApproval(
                              item.id,
                              "rejected",
                              "Rejected from risk panel",
                            ),
                          "Request rejected",
                        )
                      }
                    >
                      <X size={14} />
                      Reject
                    </button>
                    <button
                      className="btn-primary"
                      onClick={() =>
                        mutate(
                          () =>
                            decideRiskApproval(
                              item.id,
                              "approved",
                              "Approved from risk panel",
                            ),
                          "Request approved",
                        )
                      }
                    >
                      <Check size={14} />
                      Approve
                    </button>
                  </div>
                </div>
              </AnimatedCard>
            );
          })}
          {!pending.length && (
            <div className="card p-10 text-center">
              <ShieldCheck className="mx-auto text-success" />
              <p className="mt-3 text-sm">No approvals are waiting.</p>
            </div>
          )}
        </div>
      )}

      {renderedTab === "history" && (
        <AnimatedCard className="overflow-hidden">
          <div className="max-h-[680px] overflow-auto scrollbar-hidden">
            <table className="w-full min-w-[1100px] text-xs">
              <thead className="sticky top-0 bg-[rgb(var(--color-surface))]">
                <tr>
                  {[
                    "Time",
                    "Rule",
                    "Client",
                    "Equity at event",
                    "Event",
                    "Status",
                    "Group change",
                    "Actions",
                    "Actor",
                  ].map((value) => (
                    <th key={value} className="px-4 py-3 text-left text-muted">
                      {value}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.history.map((row) => (
                  <tr
                    key={row.id}
                    className="border-t border-[rgb(var(--color-border))]"
                  >
                    <td className="px-4 py-3">{formatDate(row.created_at)}</td>
                    <td className="px-4 py-3 font-medium">{row.rule_name}</td>
                    <td className="px-4 py-3">
                      {row.client_name}
                      <div className="text-muted">#{row.client_login}</div>
                    </td>
                    <td className="px-4 py-3 font-medium tabular-nums">
                      {formatEquity(row.account_equity)}
                    </td>
                    <td className="px-4 py-3">
                      {row.event_type.replaceAll("_", " ")}
                    </td>
                    <td className="px-4 py-3">
                      <Status value={row.status} />
                    </td>
                    <td className="px-4 py-3">
                      {row.previous_group || "—"} → {row.new_group || "—"}
                    </td>
                    <td className="px-4 py-3">
                      {row.result?.actions
                        ?.map((item) => `${item.type}: ${item.status}`)
                        .join(", ") || "—"}
                    </td>
                    <td className="px-4 py-3">{row.actor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AnimatedCard>
      )}

      {renderedTab === "audit" && (
        <div className="space-y-2">
          {data.audit.map((row) => (
            <div
              key={row.id}
              className="card grid gap-2 p-4 text-xs lg:grid-cols-[180px_180px_220px_1fr]"
            >
              <span className="text-muted">{formatDate(row.created_at)}</span>
              <b>{row.actor}</b>
              <span>
                {row.action}
                <div className="text-muted">
                  {row.entity_type} {row.entity_id}
                </div>
              </span>
              <code className="overflow-hidden text-ellipsis whitespace-nowrap text-[10px] text-muted">
                {JSON.stringify(row.details)}
              </code>
            </div>
          ))}
        </div>
      )}

      {editor && (
        <RuleEditor
          initial={editor === "new" ? null : editor}
          groups={data.groups}
          onClose={() => setEditor(null)}
          onSaved={() => load(true)}
        />
      )}
      {simulation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
          <div className="card max-h-[80vh] w-full max-w-3xl overflow-auto p-5 scrollbar-hidden">
            <div className="flex justify-between">
              <div>
                <p className="text-[10px] uppercase tracking-[.2em] text-muted">
                  Safe simulation
                </p>
                <h2 className="mt-1 text-lg font-semibold">
                  {simulation.rule.name}
                </h2>
                <p className="mt-1 text-xs text-secondary">
                  Evaluated {simulation.result.evaluated} clients · matched{" "}
                  {simulation.result.matched} · no changes applied
                </p>
              </div>
              <button
                className="btn-ghost !p-2"
                onClick={() => setSimulation(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="mt-4 space-y-2">
              {simulation.result.clients.map((client) => (
                <div
                  key={client.login}
                  className="rounded-xl border border-[rgb(var(--color-border))] p-3 text-xs"
                >
                  <div className="flex justify-between">
                    <b>
                      {client.name || "Unknown"} · #{client.login}
                    </b>
                    <span className="badge badge-amber">Would trigger</span>
                  </div>
                  <p className="mt-1 text-muted">
                    {client.group} ·{" "}
                    {client.detected.conditions
                      .map(
                        (item) =>
                          `${item.field}: ${item.detected_value} ${item.operator} ${item.threshold}`,
                      )
                      .join(" · ")}
                  </p>
                </div>
              ))}
              {!simulation.result.clients.length && (
                <p className="py-10 text-center text-sm text-muted">
                  No clients match this rule.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
      {overrideClient && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
          <form onSubmit={executeOverride} className="card w-full max-w-lg p-5">
            <div className="flex justify-between">
              <div>
                <p className="text-[10px] uppercase tracking-[.2em] text-muted">
                  Manual override
                </p>
                <h2 className="mt-1 font-semibold">
                  {overrideClient.name || "Unknown"} · #{overrideClient.login}
                </h2>
                <p className="mt-1 text-xs text-secondary">
                  Current group: {overrideClient.group}
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost !p-2"
                onClick={() => setOverrideClient(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="mt-5 space-y-3">
              <label className="text-xs text-muted">
                Action
                <select
                  className="field-input mt-1.5"
                  value={override.type}
                  onChange={(e) =>
                    setOverride((current) => ({
                      ...current,
                      type: e.target.value,
                    }))
                  }
                >
                  {ACTION_TYPES.filter(
                    ([type]) => !["restrict_symbols"].includes(type),
                  ).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {override.type === "move_group" && (
                <label className="text-xs text-muted">
                  Target group
                  <select
                    required
                    className="field-input mt-1.5"
                    value={override.target_group}
                    onChange={(e) =>
                      setOverride((current) => ({
                        ...current,
                        target_group: e.target.value,
                      }))
                    }
                  >
                    <option value="">Select group</option>
                    {data.groups.map((group) => (
                      <option key={group}>{group}</option>
                    ))}
                  </select>
                </label>
              )}
              {["restrict_lot_size", "set_leverage", "add_label"].includes(
                override.type,
              ) && (
                <label className="text-xs text-muted">
                  Value
                  <input
                    required
                    className="field-input mt-1.5"
                    value={override.value ?? ""}
                    onChange={(e) =>
                      setOverride((current) => ({
                        ...current,
                        value: e.target.value,
                      }))
                    }
                  />
                </label>
              )}
              <label className="text-xs text-muted">
                Reason
                <textarea
                  required
                  minLength="3"
                  className="field-input mt-1.5 min-h-24"
                  value={override.reason}
                  onChange={(e) =>
                    setOverride((current) => ({
                      ...current,
                      reason: e.target.value,
                    }))
                  }
                />
              </label>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setOverrideClient(null)}
              >
                Cancel
              </button>
              <button className="btn-primary">
                <UserCog size={14} />
                Apply override
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
