import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  Check,
  Plus,
  ShieldAlert,
  X,
} from "lucide-react";
import { apiRequest, getStoredUser } from "../lib/api";

const panel =
  "rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))]";
const format = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const metrics = {
  gross_lots: "Gross lots",
  floating_loss: "Floating loss",
  gross_exposure: "Gross notional",
  margin_level_below: "Minimum margin level (%)",
  concentration_pct: "Symbol concentration (%)",
};
const defaults = {
  scope_type: "account",
  scope_value: "",
  metric: "gross_lots",
  threshold: 1,
  warning_pct: 80,
  escalation_level: "warning",
  enabled: true,
};
const workflows = [
  ["Live dealing", "Positions, P&L and exposure", "/dealing-risk"],
  ["Investigations", "Evidence, cases and resolutions", "/operations"],
  ["Automation", "Rules, simulations and approvals", "/risk-automation"],
  ["Analytics", "Historical reports and exports", "/dealing-risk/analytics"],
];

export default function RiskCenter() {
  const client = useQueryClient();
  const admin = Boolean(getStoredUser()?.is_admin);
  const [group, setGroup] = useState("");
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState(defaults);
  const health = useQuery({
    queryKey: ["risk-health"],
    queryFn: ({ signal }) => apiRequest("/api/dealing-risk/health", { signal }),
    enabled: admin,
    refetchInterval: 10000,
  });
  const live = useQuery({
    queryKey: ["risk-center-limits", group],
    queryFn: ({ signal }) =>
      apiRequest("/api/operations/live", {
        method: "POST",
        signal,
        body: JSON.stringify({ selected_groups: group ? [group] : [] }),
      }),
    enabled: admin && Boolean(health.data?.connected),
    refetchInterval: 5000,
  });
  const saved = useQuery({
    queryKey: ["risk-configured-limits"],
    queryFn: ({ signal }) => apiRequest("/api/operations/limits", { signal }),
    enabled: admin,
    refetchInterval: 30000,
  });
  const save = useMutation({
    mutationFn: () =>
      apiRequest(
        typeof editing === "number"
          ? `/api/operations/limits/${editing}`
          : "/api/operations/limits",
        {
          method: typeof editing === "number" ? "PUT" : "POST",
          body: JSON.stringify(draft),
        },
      ),
    onSuccess: () => {
      setEditing(null);
      client.invalidateQueries({ queryKey: ["risk-configured-limits"] });
      client.invalidateQueries({ queryKey: ["risk-center-limits"] });
      client.invalidateQueries({ queryKey: ["risk-health"] });
    },
  });
  const evaluations = new Map(
    (live.data?.limits || []).map((row) => [row.id, row]),
  );
  const sourceStale = health.data?.stale || live.data?.status !== "connected";
  const edit = (row) => {
    setDraft(
      row ? { ...defaults, ...row, enabled: Boolean(row.enabled) } : defaults,
    );
    setEditing(row?.id ?? "new");
    save.reset();
  };
  if (!admin)
    return (
      <div className="p-6 text-sm text-muted">
        Administrator access is required for risk configuration.
      </div>
    );
  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Risk Center</h1>
          <p className="mt-1 text-xs text-muted">
            Live oversight and broker controls
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-2 text-xs ${!health.data?.connected || health.data?.stale ? "text-amber-500" : "text-emerald-500"}`}
        >
          <Activity size={14} />
          {health.data?.connected
            ? health.data?.data_age_seconds != null
              ? `MT5 · ${format.format(health.data.data_age_seconds)}s ago`
              : "MT5 initializing"
            : "Connecting to MT5"}
        </span>
      </header>
      {(health.error || live.error || saved.error) && (
        <p
          role="alert"
          className="rounded-lg border border-amber-500/30 p-3 text-xs text-amber-500"
        >
          {health.error?.message || live.error?.message || saved.error?.message}{" "}
          Previous observations remain visible.
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          [
            "High-risk events",
            health.data?.open_high_risk_events,
            "/operations",
          ],
          [
            "Pending approvals",
            health.data?.pending_approvals,
            "/risk-automation",
          ],
          ["Monitoring limits", health.data?.enabled_limits, "#limits"],
          [
            "Source checks",
            health.data?.checks?.filter((row) => row.state !== "ok").length,
            "#source-health",
          ],
        ].map(([label, value, href]) => (
          <Link key={label} to={href} className={`${panel} p-4`}>
            <p className="text-xs text-muted">{label}</p>
            <p className="mt-2 text-2xl font-semibold tabular-nums">
              {value == null ? "—" : format.format(value)}
            </p>
          </Link>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {workflows.map(([title, description, href]) => (
          <Link
            key={title}
            to={href}
            className={`${panel} flex items-center justify-between gap-3 p-4 transition-colors hover:border-[rgb(var(--color-accent))]`}
          >
            <div>
              <h2 className="text-sm font-medium">{title}</h2>
              <p className="mt-1 text-xs text-muted">{description}</p>
            </div>
            <ArrowUpRight size={16} className="shrink-0 text-muted" />
          </Link>
        ))}
      </div>
      <section id="limits" className={`${panel} overflow-hidden`}>
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[rgb(var(--color-border))] p-4">
          <div>
            <h2 className="text-sm font-semibold">Monitoring limits</h2>
            <p className="mt-1 text-xs text-muted">
              Alerts only. Trading actions require separate automation rules.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Limit evaluation group"
              className="field-input !w-auto max-w-60 text-xs"
              value={group}
              onChange={(event) => setGroup(event.target.value)}
            >
              <option value="">All live groups</option>
              {health.data?.groups?.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
            <button className="btn-primary text-xs" onClick={() => edit(null)}>
              <Plus size={14} />
              Add limit
            </button>
          </div>
        </header>
        {editing !== null && (
          <form
            className="border-b border-[rgb(var(--color-border))] p-4"
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <label className="text-xs text-muted">
                Scope
                <select
                  className="field-input mt-1"
                  value={draft.scope_type}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      scope_type: event.target.value,
                      metric:
                        event.target.value === "symbol"
                          ? "gross_lots"
                          : draft.metric,
                    })
                  }
                >
                  {["account", "group", "symbol"].map((key) => (
                    <option key={key}>{key}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-muted">
                {draft.scope_type === "account"
                  ? "Account login"
                  : draft.scope_type === "group"
                    ? "Exact group"
                    : "Exact symbol"}
                <input
                  required
                  className="field-input mt-1"
                  value={draft.scope_value}
                  onChange={(event) =>
                    setDraft({ ...draft, scope_value: event.target.value })
                  }
                />
              </label>
              <label className="text-xs text-muted">
                Metric
                <select
                  className="field-input mt-1"
                  value={draft.metric}
                  onChange={(event) =>
                    setDraft({ ...draft, metric: event.target.value })
                  }
                >
                  {Object.entries(metrics)
                    .filter(
                      ([key]) =>
                        draft.scope_type !== "symbol" ||
                        ["gross_lots", "gross_exposure"].includes(key),
                    )
                    .map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                </select>
              </label>
              <label className="text-xs text-muted">
                Threshold
                <input
                  required
                  type="number"
                  min="0.000001"
                  step="any"
                  className="field-input mt-1"
                  value={draft.threshold}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      threshold: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label className="text-xs text-muted">
                Warning at (%)
                <input
                  required
                  type="number"
                  min="1"
                  max="100"
                  className="field-input mt-1"
                  value={draft.warning_pct}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      warning_pct: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label className="text-xs text-muted">
                Severity
                <select
                  className="field-input mt-1"
                  value={draft.escalation_level}
                  onChange={(event) =>
                    setDraft({ ...draft, escalation_level: event.target.value })
                  }
                >
                  {["info", "warning", "high", "critical"].map((key) => (
                    <option key={key}>{key}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) =>
                    setDraft({ ...draft, enabled: event.target.checked })
                  }
                />
                Enabled
              </label>
              <div className="flex items-end gap-2">
                <button
                  className="btn-primary text-xs"
                  disabled={save.isPending}
                >
                  <Check size={14} />
                  Save
                </button>
                <button
                  type="button"
                  aria-label="Cancel limit changes"
                  className="btn-secondary"
                  onClick={() => setEditing(null)}
                >
                  <X size={14} />
                </button>
              </div>
            </div>
            {save.error && (
              <p className="mt-3 text-xs text-danger" role="alert">
                {save.error.message}
              </p>
            )}
          </form>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr>
                {["Scope", "Metric", "Current", "Threshold", "Status", ""].map(
                  (label, index) => (
                    <th
                      key={index}
                      className="whitespace-nowrap px-4 py-3 font-medium text-muted"
                    >
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {saved.data?.limits?.map((row) => {
                const evaluation = evaluations.get(row.id);
                const state = !row.enabled
                  ? "disabled"
                  : sourceStale
                    ? "stale"
                    : evaluation?.evaluation_state || "outside scope";
                return (
                  <tr
                    key={row.id}
                    className="border-t border-[rgb(var(--color-border))]"
                  >
                    <td
                      className="max-w-xs truncate px-4 py-3"
                      title={row.scope_value}
                    >
                      {row.scope_value}{" "}
                      <span className="ml-2 text-muted">{row.scope_type}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      {metrics[row.metric]}
                    </td>
                    <td
                      className="px-4 py-3 tabular-nums"
                      title={evaluation?.source}
                    >
                      {evaluation?.current_value == null
                        ? "—"
                        : format.format(evaluation.current_value)}
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {format.format(row.threshold)}
                    </td>
                    <td
                      className={`whitespace-nowrap px-4 py-3 ${state === "breached" ? "text-danger" : state === "within_limit" ? "text-emerald-500" : "text-muted"}`}
                    >
                      {state.replaceAll("_", " ")}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        className="text-xs underline underline-offset-4"
                        onClick={() => edit(row)}
                      >
                        Edit
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {saved.data?.limits?.length === 0 && (
            <p className="p-6 text-sm text-muted">
              No monitoring limits configured. Add an account, group or symbol
              limit.
            </p>
          )}
        </div>
      </section>
      <section id="source-health" className={panel}>
        <header className="flex items-center gap-2 border-b border-[rgb(var(--color-border))] p-4">
          <ShieldAlert size={16} />
          <h2 className="text-sm font-semibold">Data health</h2>
        </header>
        <div className="divide-y divide-[rgb(var(--color-border))]">
          {health.data?.checks?.map((row) => (
            <Link
              key={row.id}
              to={row.href}
              className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-xs"
            >
              <span>{row.label}</span>
              <span
                className={`flex items-center gap-2 ${row.state === "ok" ? "text-emerald-500" : "text-amber-500"}`}
              >
                {row.value}
                <ArrowUpRight size={13} />
              </span>
            </Link>
          ))}
        </div>
        {Boolean(health.data?.group_mismatches?.length) && (
          <details className="border-t border-[rgb(var(--color-border))] p-4 text-xs">
            <summary className="cursor-pointer text-muted">
              Review outdated book mappings
            </summary>
            <div className="mt-3 max-h-48 overflow-auto">
              {health.data.group_mismatches.map((row) => (
                <p key={`${row.book}-${row.group}`} className="py-1">
                  {row.book.replaceAll("_", " ")} · {row.group}
                </p>
              ))}
            </div>
            <Link to="/settings" className="mt-3 inline-block underline">
              Update book groups
            </Link>
          </details>
        )}
      </section>
    </div>
  );
}
