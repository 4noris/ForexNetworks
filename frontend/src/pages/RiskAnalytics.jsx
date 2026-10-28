import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ArrowLeft, Download, RefreshCw } from "lucide-react";
import { apiRequest, apiDownload, getStoredUser } from "../lib/api";
import SafeResponsiveContainer from "../components/SafeResponsiveContainer";

const SECTIONS = [
  ["overview", "Overview"],
  ["pnl", "P&L Analytics"],
  ["exposure", "Exposure Analytics"],
  ["accounts", "Account Analytics"],
  ["trading-activity", "Trading Activity"],
  ["risk-events", "Risk and Anti-Fraud"],
  ["reports", "Reports and Exports"],
  ["sync", "Data Synchronization Status"],
];
const today = () => new Date().toISOString().slice(0, 10);
const before = (days) =>
  new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
const dateTime = (value) => {
  if (value == null || value === "") return "Unavailable";
  const parsed = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isFinite(parsed.getTime())
    ? parsed
        .toISOString()
        .replace("T", " ")
        .replace(/\.\d+Z$/, " UTC")
    : "Unavailable";
};
const display = (value) =>
  value == null
    ? "Unavailable"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
const moneyFormat = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value) => value == null || !Number.isFinite(Number(value)) ? "Unavailable" : moneyFormat.format(Number(value));
const monetaryColumns = new Set(["profit", "commission", "swap", "fee", "net", "balance", "equity", "margin", "free_margin", "floating", "gross_notional", "net_notional", "minimum_account_net", "maximum_account_net", "average_account_net", "cumulative", "drawdown"]);
const queryString = (filters) =>
  new URLSearchParams(
    Object.entries(filters).filter(
      ([, value]) => value !== "" && value != null,
    ),
  ).toString();
const card =
  "rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4";

function DataTable({ rows = [], columns }) {
  if (!rows.length)
    return (
      <p className="p-4 text-sm text-muted">
        No stored records match this scope. Check historical coverage and
        synchronization status.
      </p>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr>
            {columns.map((key) => (
              <th
                className="whitespace-nowrap border-b border-[rgb(var(--color-border))] p-3 font-medium text-muted"
                key={key}
              >
                {key.replaceAll("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={
                row.source_id ||
                row.event_id ||
                row.id ||
                `${row.label || row.source_ts}-${row.currency}-${row.symbol || index}`
              }
              className="border-b border-[rgb(var(--color-border))]"
            >
              {columns.map((key) => (
                <td
                  className="max-w-sm whitespace-nowrap p-3 tabular-nums"
                  key={key}
                >
                  {[
                    "source_ts",
                    "coverage_from",
                    "through_ts",
                    "started_at",
                    "completed_at",
                    "first_seen_at",
                    "updated_at",
                  ].includes(key) ? (
                    dateTime(row[key])
                  ) : key === "time_ms" ? (
                    dateTime(row[key] / 1000)
                  ) : key === "evidence" || key === "source" ? (
                    <details>
                      <summary className="cursor-pointer">
                        View evidence
                      </summary>
                      <pre className="max-h-64 overflow-auto whitespace-pre-wrap">
                        {JSON.stringify(row[key], null, 2)}
                      </pre>
                    </details>
                  ) : (
                    monetaryColumns.has(key) ? money(row[key]) : display(row[key])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SyncControls({ status, refresh }) {
  const [draft, setDraft] = useState(null);
  const settings = draft || status?.settings || {};
  const save = useMutation({
    mutationFn: () =>
      apiRequest("/api/dealing-risk/sync/settings", {
        method: "PUT",
        body: JSON.stringify(settings),
      }),
    onSuccess: refresh,
  });
  const sync = useMutation({
    mutationFn: (kind) =>
      apiRequest("/api/dealing-risk/sync/run", {
        method: "POST",
        body: JSON.stringify({ kind }),
      }),
    onSuccess: refresh,
  });
  const change = (key, value) => setDraft({ ...settings, [key]: value });
  return (
    <div className="space-y-4">
      <div className={`${card} flex flex-wrap items-end gap-3`}>
        <label className="text-xs text-muted">
          History interval
          <select
            aria-label="History interval"
            className="field-input mt-1"
            value={settings.interval_minutes || 60}
            onChange={(event) =>
              change("interval_minutes", Number(event.target.value))
            }
          >
            <option value={30}>30 minutes</option>
            <option value={60}>60 minutes</option>
            {settings.interval_minutes &&
              ![30, 60].includes(settings.interval_minutes) && (
                <option value={settings.interval_minutes}>
                  {settings.interval_minutes} minutes
                </option>
              )}
          </select>
        </label>
        <label className="text-xs text-muted">
          Snapshots (seconds)
          <input
            className="field-input mt-1"
            type="number"
            min={60}
            max={3600}
            value={settings.snapshot_seconds || 300}
            onChange={(event) =>
              change("snapshot_seconds", Number(event.target.value))
            }
          />
        </label>
        <label className="text-xs text-muted">
          Reconcile hour UTC
          <input
            className="field-input mt-1"
            type="number"
            min={0}
            max={23}
            value={settings.reconciliation_hour ?? 2}
            onChange={(event) =>
              change("reconciliation_hour", Number(event.target.value))
            }
          />
        </label>
        <label className="text-xs text-muted">
          Backfill from UTC
          <input
            className="field-input mt-1"
            type="date"
            max={today()}
            value={settings.backfill_from || ""}
            onChange={(event) => change("backfill_from", event.target.value)}
          />
        </label>
        <button
          className="btn-primary text-xs"
          disabled={save.isPending || !draft}
          onClick={() => save.mutate()}
        >
          Save schedule
        </button>
        <button
          className="btn-secondary text-xs"
          disabled={sync.isPending}
          onClick={() => sync.mutate("incremental")}
        >
          Sync now
        </button>
        <button
          className="btn-secondary text-xs"
          disabled={sync.isPending}
          onClick={() => sync.mutate("reconcile")}
        >
          Reconcile
        </button>
      </div>
      {settings.risk_thresholds && (
        <div className={`${card} flex flex-wrap items-end gap-3`}>
          <p className="w-full text-xs text-muted">
            Observed snapshot signals · Evidence is retained in Risk and
            Anti-Fraud.
          </p>
          {Object.entries(settings.risk_thresholds).map(([key, value]) => (
            <label key={key} className="text-xs text-muted">
              {key.replaceAll("_", " ")}
              {typeof value === "boolean" ? (
                <input
                  className="ml-2"
                  type="checkbox"
                  checked={value}
                  onChange={(event) =>
                    change("risk_thresholds", {
                      ...settings.risk_thresholds,
                      [key]: event.target.checked,
                    })
                  }
                />
              ) : (
                <input
                  className="field-input mt-1 !w-36"
                  type="number"
                  min="0.01"
                  step="any"
                  value={value}
                  onChange={(event) =>
                    change("risk_thresholds", {
                      ...settings.risk_thresholds,
                      [key]: Number(event.target.value),
                    })
                  }
                />
              )}
            </label>
          ))}
        </div>
      )}
      {(save.error || sync.error) && (
        <p role="alert" className="text-sm text-danger">
          {(save.error || sync.error).message}
        </p>
      )}
      {(save.isSuccess || sync.isSuccess) && (
        <p role="status" className="text-xs text-emerald-400">
          {sync.isSuccess
            ? "Background job queued. This page stays available."
            : "Schedule saved. The worker reads it automatically."}
        </p>
      )}
      <div className={card}>
        <p className="mb-3 text-xs text-muted">
          Coverage is checkpointed per group. Snapshots start at archive
          activation; earlier floating P&L, equity, margin and exposure are
          unavailable. Source deletions require tombstones and are never
          inferred from an empty response.
        </p>
        <DataTable
          rows={status?.coverage}
          columns={[
            "account_group",
            "coverage_from",
            "through_ts",
            "updated_at",
          ]}
        />
      </div>
      <div className={card}>
        <DataTable
          rows={status?.jobs}
          columns={[
            "kind",
            "status",
            "started_at",
            "completed_at",
            "duration_seconds",
            "fetched",
            "inserted",
            "updated",
            "skipped",
            "failed_batches",
            "retries",
            "error",
          ]}
        />
      </div>
      <p className="text-xs text-muted">
        Source capabilities: {display(status?.capabilities)} · Corrections
        detected: {status?.discrepancies ?? "Unavailable"}
      </p>
      <div className={card}>
        <h3 className="mb-3 text-sm font-medium">
          Source reconciliation comparisons
        </h3>
        <p className="mb-3 text-xs text-muted">
          Unmatched comparisons: {status?.unresolved_comparisons ?? 0}. Missing
          source rows remain available for investigation; deletions are not
          guessed.
        </p>
        <DataTable
          rows={status?.reconciliation_audits}
          columns={[
            "account_group",
            "from_ts",
            "to_ts",
            "source_records",
            "stored_records",
            "source_net",
            "stored_net",
            "status",
            "checked_at",
          ]}
        />
      </div>
    </div>
  );
}

function ExportControls({ filters }) {
  const [job, setJob] = useState(null);
  const [dataset, setDataset] = useState("accounts");
  const create = useMutation({
    mutationFn: (format) =>
      apiRequest("/api/dealing-risk/analytics/exports", {
        method: "POST",
        body: JSON.stringify({ ...filters, format, report: dataset }),
      }),
    onSuccess: setJob,
  });
  const status = useQuery({
    queryKey: ["history-export", job?.id],
    enabled: Boolean(job?.id),
    queryFn: ({ signal }) =>
      apiRequest(`/api/dealing-risk/analytics/exports/${job.id}`, { signal }),
    refetchInterval: (query) =>
      ["completed", "failed"].includes(query.state.data?.status) ? false : 2000,
  });
  async function download() {
    const { blob } = await apiDownload(
      `/api/dealing-risk/analytics/exports/${job.id}/download`,
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `risk-analytics-${job.id}.${status.data.format}`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const [downloadError, setDownloadError] = useState("");
  return (
    <div className={`${card} space-y-3`}>
      <p className="text-xs text-muted">
        Export the complete selected dataset for the current filters, with
        currency totals where applicable, date boundaries and UTC generation
        time. Exports run in the background and remain available for 24 hours.
      </p>
      <label className="inline-block text-xs text-muted">
        Export dataset
        <select
          aria-label="Export dataset"
          className="field-input mt-1"
          value={dataset}
          onChange={(event) => setDataset(event.target.value)}
        >
          {[
            ["accounts", "Account P&L"],
            ["pnl", "Daily P&L"],
            ["exposure", "Exposure snapshots"],
            ["trading-activity", "Deals and executions"],
            ["risk-events", "Risk events"],
          ].map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-wrap gap-2">
        {["csv", "xlsx"].map((format) => (
          <button
            key={format}
            className="btn-secondary text-xs"
            disabled={create.isPending}
            onClick={() => create.mutate(format)}
          >
            <Download size={14} /> Export {format.toUpperCase()}
          </button>
        ))}
        {status.data?.status === "completed" && (
          <button
            className="btn-primary text-xs"
            onClick={() =>
              download().catch((error) => setDownloadError(error.message))
            }
          >
            Download report
          </button>
        )}
      </div>
      {job && (
        <p className="text-xs" role="status">
          Export: {status.data?.status || job.status}
        </p>
      )}
      {(create.error ||
        status.error ||
        status.data?.error ||
        downloadError) && (
        <p role="alert" className="text-xs text-danger">
          {create.error?.message ||
            status.error?.message ||
            status.data?.error ||
            downloadError}
        </p>
      )}
    </div>
  );
}

export default function RiskAnalytics() {
  const [section, setSection] = useState("overview");
  const [filters, setFilters] = useState({
    from_date: before(29),
    to_date: today(),
    group: "",
    login: "",
    symbol: "",
    currency: "",
  });
  const [dimension, setDimension] = useState("day");
  const [exposureDimension, setExposureDimension] = useState("symbol");
  const [kind, setKind] = useState("deal");
  const [severity, setSeverity] = useState("");
  const [descending, setDescending] = useState(false);
  const [sort, setSort] = useState("label");
  const [page, setPage] = useState(1);
  const client = useQueryClient();
  const authorized = Boolean(getStoredUser()?.is_admin);
  const valid = Boolean(
    filters.from_date &&
    filters.to_date &&
    filters.from_date <= filters.to_date &&
    filters.to_date <= today() &&
    (!filters.login || Number(filters.login) > 0),
  );
  const status = useQuery({
    queryKey: ["history-status"],
    queryFn: ({ signal }) =>
      apiRequest("/api/dealing-risk/sync/status", { signal }),
    enabled: authorized,
    refetchInterval: 10000,
  });
  const catalog = useQuery({
    queryKey: ["history-catalog"],
    queryFn: ({ signal }) =>
      apiRequest("/api/dealing-risk/analytics/catalog", { signal }),
    enabled: authorized,
    refetchInterval: 30000,
  });
  const endpoint = section === "reports" ? "pnl" : section;
  const path = `/api/dealing-risk/analytics/${endpoint}?${queryString({ ...filters, dimension: section === "exposure" ? exposureDimension : dimension, kind, severity, descending, sort, page, page_size: 100 })}`;
  const report = useQuery({
    queryKey: ["risk-analytics", path],
    queryFn: ({ signal }) => apiRequest(path, { signal }),
    enabled: authorized && valid && section !== "sync",
    placeholderData: keepPreviousData,
    refetchInterval: 30000,
  });
  const data = report.data;
  const managerTimezone = status.data?.manager_timezone;
  const managerTime = (value) => {
    if (value == null || !managerTimezone) return "Unavailable";
    const instant = new Date(typeof value === "number" ? value * 1000 : value);
    if (!Number.isFinite(instant.getTime())) return "Unavailable";
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: managerTimezone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
      timeZoneName: "shortOffset",
    }).formatToParts(instant);
    const part = (type) => parts.find((entry) => entry.type === type)?.value || "";
    return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")} ${part("timeZoneName")}`;
  };
  const update = (key, value) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(1);
  };
  const refreshStatus = () =>
    client.invalidateQueries({ queryKey: ["history-status"] });
  const rows = data?.rows || [];
  const chartCurrency = filters.currency || data?.totals?.[0]?.currency;
  const chartRows = (data?.series || rows)
    .filter((row) => row.currency === chartCurrency)
    .map((row) => ({
      ...row,
      net: row.net == null ? null : Number(row.net),
      cumulative: row.cumulative == null ? null : Number(row.cumulative),
    }));
  const equityRows = (data?.snapshots?.rows || [])
    .filter(
      (row) =>
        row.currency ===
        (chartCurrency || data?.snapshots?.rows?.[0]?.currency),
    )
    .toSorted((left, right) => left.source_ts - right.source_ts)
    .map((row) => ({
      time: dateTime(row.source_ts),
      equity: row.equity == null ? null : Number(row.equity),
      balance: row.balance == null ? null : Number(row.balance),
    }));
  const columns =
    section === "exposure"
      ? [
          "source_ts",
          "label",
          "symbol",
          "currency",
          "buy_lots",
          "sell_lots",
          "gross_lots",
          "net_lots",
          "gross_notional",
          "net_notional",
          "unvalued_positions",
          "concentration_pct",
        ]
      : section === "trading-activity"
        ? [
            "time_ms",
            "source_id",
            "login",
            "account_group",
            "symbol",
            "currency",
            "action",
            "entry",
            "lots",
            "profit",
            "commission",
            "swap",
            "fee",
            "net",
            "source",
          ]
        : section === "risk-events"
          ? [
              "first_seen_at",
              "login",
              "account_group",
              "symbol",
              "detector",
              "severity",
              "status",
              "evidence",
            ]
          : [
              "label",
              "currency",
              "profit",
              "commission",
              "swap",
              "fee",
              "net",
              "lots",
              "deals",
              "buy_deals",
              "sell_deals",
            ];
  const coverage = (status.data?.coverage || []).filter(
    (row) => !filters.group || row.account_group === filters.group,
  );
  const coverageStart = coverage.length
    ? Math.max(...coverage.map((row) => row.coverage_from))
    : null;
  const coverageEnd = coverage.length
    ? Math.min(...coverage.map((row) => row.through_ts))
    : null;
  const pendingGroups = (status.data?.configured_groups || []).filter(
    (group) =>
      (!filters.group || group === filters.group) &&
      !coverage.some((row) => row.account_group === group),
  );

  if (!authorized)
    return (
      <div className="p-6">
        <h1 className="text-xl font-semibold">Risk Analytics</h1>
        <p className="mt-3 text-sm text-muted">
          Administrator access is required for archived account data and risk
          evidence.
        </p>
      </div>
    );
  return (
    <div
      className="mx-auto max-w-[1800px] space-y-4 p-4 sm:p-6"
      data-testid="risk-analytics"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link
            to="/dealing-risk"
            className="inline-flex items-center gap-1 text-xs text-muted"
          >
            <ArrowLeft size={13} /> Live dashboard
          </Link>
          <h1 className="mt-2 text-xl font-semibold">Risk Analytics</h1>
          <p className="mt-1 text-xs text-muted">
            Persistent history · UTC · Client P&L · Currencies reported
            separately
          </p>
        </div>
        <div className="text-right text-xs text-muted">
          <p>
            MT5{" "}
            {status.data?.source_connected
              ? "connected"
              : "disconnected / unavailable"}
          </p>
          <p>Last sync: {managerTime(status.data?.last_success)}</p>
          <p className={status.data?.snapshot_stale ? "text-amber-400" : ""}>
            Snapshot: {managerTime(status.data?.latest_snapshot)}{" "}
            {status.data?.snapshot_stale
              ? "· Stale / unavailable"
              : "· Observed"}
          </p>
        </div>
      </header>
      <nav
        className="flex gap-1 overflow-x-auto border-b border-[rgb(var(--color-border))] pb-2"
        aria-label="Analytics reports"
      >
        {SECTIONS.map(([key, label]) => (
          <button
            className={`whitespace-nowrap rounded-lg px-3 py-2 text-xs ${section === key ? "bg-white/10 text-primary" : "text-muted"}`}
            key={key}
            onClick={() => {
              setSection(key);
              setPage(1);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {section !== "sync" && (
        <p className="text-xs text-amber-400">
          Common synchronized coverage for the selected groups:{" "}
          {coverageStart
            ? `${dateTime(coverageStart)} – ${dateTime(coverageEnd)}`
            : "Not available yet"}
          .
          {pendingGroups.length
            ? ` ${pendingGroups.length} configured group(s) still await initial backfill.`
            : ""}{" "}
          Values outside this coverage may be incomplete; inspect Data
          Synchronization Status for each group.
        </p>
      )}
      {section !== "sync" && (
        <div className={`${card} flex flex-wrap items-end gap-3`}>
          <label className="text-xs text-muted">
            From UTC
            <input
              aria-label="From UTC"
              className="field-input mt-1"
              type="date"
              max={today()}
              value={filters.from_date}
              onChange={(event) => update("from_date", event.target.value)}
            />
          </label>
          <label className="text-xs text-muted">
            To UTC
            <input
              aria-label="To UTC"
              className="field-input mt-1"
              type="date"
              min={filters.from_date}
              max={today()}
              value={filters.to_date}
              onChange={(event) => update("to_date", event.target.value)}
            />
          </label>
          <div className="flex gap-1">
            {[
              [0, "Daily"],
              [6, "Weekly"],
              [29, "Monthly"],
            ].map(([days, label]) => (
              <button
                key={label}
                className="btn-secondary text-xs"
                onClick={() => {
                  setFilters((current) => ({
                    ...current,
                    from_date: before(days),
                    to_date: today(),
                  }));
                  setPage(1);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="text-xs text-muted">
            Group
            <select
              aria-label="Group"
              className="field-input mt-1 max-w-60"
              value={filters.group}
              onChange={(event) => update("group", event.target.value)}
            >
              <option value="">All archived groups</option>
              {catalog.data?.groups.map((group) => (
                <option key={group}>{group}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted">
            Login
            <input
              aria-label="Login"
              className="field-input mt-1 !w-32"
              type="number"
              min={1}
              value={filters.login}
              onChange={(event) => update("login", event.target.value)}
            />
          </label>
          <label className="text-xs text-muted">
            Symbol
            <select
              aria-label="Symbol"
              className="field-input mt-1"
              value={filters.symbol}
              onChange={(event) => update("symbol", event.target.value)}
            >
              <option value="">All symbols</option>
              {catalog.data?.symbols.map((symbol) => (
                <option key={symbol}>{symbol}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted">
            Currency
            <select
              aria-label="Currency"
              className="field-input mt-1"
              value={filters.currency}
              onChange={(event) => update("currency", event.target.value)}
            >
              <option value="">Separate by currency</option>
              {catalog.data?.currencies.map((currency) => (
                <option key={currency}>{currency}</option>
              ))}
            </select>
          </label>
          <button
            className="btn-secondary text-xs"
            disabled={report.isFetching}
            onClick={() => report.refetch()}
          >
            <RefreshCw
              size={13}
              className={report.isFetching ? "animate-spin" : ""}
            />{" "}
            Refresh
          </button>
        </div>
      )}
      {!valid && section !== "sync" && (
        <p role="alert" className="text-sm text-danger">
          Choose an ordered UTC date range ending on or before today and a
          positive account login.
        </p>
      )}
      {(report.error || status.error || catalog.error) && (
        <p role="alert" className="text-sm text-danger">
          {report.error?.message ||
            status.error?.message ||
            catalog.error?.message}{" "}
          Existing stored data remains visible.
        </p>
      )}
      {section === "sync" ? (
        <SyncControls status={status.data} refresh={refreshStatus} />
      ) : (
        <>
          <p role="status" className="text-xs text-muted">
            {report.isFetching
              ? "Updating stored report…"
              : `Report generated: ${dateTime(data?.generated_at)}`}
            {report.isPlaceholderData
              ? " · Previous scope shown while the selected scope loads"
              : ""}
          </p>
          {section === "reports" && <ExportControls filters={filters} />}
          {!!data?.totals?.length && (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {data.totals.map((total) => (
                <div key={total.currency} className={card}>
                  <p className="text-xs text-muted">
                    Realized client net · {total.currency}
                  </p>
                  <p
                    className={`mt-2 text-xl tabular-nums ${Number(total.net) < 0 ? "text-red-400" : ""}`}
                  >
                    {money(total.net)}
                  </p>
                  <p className="mt-2 text-xs text-muted">
                    {total.deals} executions · {display(total.lots)} lots ·{" "}
                    {total.active_accounts} active accounts
                  </p>
                  {section === "overview" && (
                    <p className="mt-2 text-xs text-muted">
                      Previous period net:{" "}
                      {money(
                        data.previous_period?.find(
                          (row) => row.currency === total.currency,
                        )?.net,
                      )}
                      <br />
                      Maximum realized drawdown:{" "}
                      {data.curves?.[total.currency]?.available
                        ? money(data.curves[total.currency].max_drawdown)
                        : "Unavailable"}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          {!!data?.statistics?.length && (
            <div className={card}>
              <DataTable
                rows={data.statistics}
                columns={[
                  "currency",
                  "winning_accounts",
                  "losing_accounts",
                  "unavailable_accounts",
                  "minimum_account_net",
                  "maximum_account_net",
                  "average_account_net",
                ]}
              />
            </div>
          )}
          {["pnl", "accounts", "reports"].includes(section) && (
            <div className="flex items-end gap-3">
              <label className="text-xs text-muted">
                Sort by
                <select
                  aria-label="Sort by"
                  className="field-input mt-1"
                  value={sort}
                  onChange={(event) => {
                    setSort(event.target.value);
                    setPage(1);
                  }}
                >
                  {["label", "net", "lots", "deals"].map((key) => (
                    <option key={key}>{key}</option>
                  ))}
                </select>
              </label>
              <button
                className="btn-secondary text-xs"
                onClick={() => {
                  setDescending((current) => !current);
                  setPage(1);
                }}
              >
                Order: {descending ? "Descending" : "Ascending"}
              </button>
            </div>
          )}
          {section === "exposure" && (
            <label className="inline-block text-xs text-muted">
              Exposure grouping
              <select
                aria-label="Exposure grouping"
                className="field-input mt-1"
                value={exposureDimension}
                onChange={(event) => {
                  setExposureDimension(event.target.value);
                  setPage(1);
                }}
              >
                {[
                  ["symbol", "Symbol"],
                  ["login", "Account and symbol"],
                  ["account_group", "Group and symbol"],
                ].map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {section === "pnl" && (
            <label className="inline-block text-xs text-muted">
              Group report by
              <select
                aria-label="Group report by"
                className="field-input mt-1"
                value={dimension}
                onChange={(event) => {
                  setDimension(event.target.value);
                  setPage(1);
                }}
              >
                {[
                  ["day", "Daily"],
                  ["week", "Weekly (UTC year/week)"],
                  ["month", "Monthly"],
                  ["login", "Account"],
                  ["account_group", "Group"],
                  ["symbol", "Symbol"],
                ].map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {section === "trading-activity" && (
            <label className="inline-block text-xs text-muted">
              History type
              <select
                className="field-input mt-1"
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value);
                  setPage(1);
                }}
              >
                <option value="deal">Deals and executions</option>
                <option value="order">
                  Historical orders (when supported)
                </option>
                <option value="closed">
                  Closing executions (OUT / INOUT / OUT_BY)
                </option>
              </select>
            </label>
          )}
          {section === "risk-events" && (
            <label className="inline-block text-xs text-muted">
              Severity
              <select
                className="field-input mt-1"
                value={severity}
                onChange={(event) => {
                  setSeverity(event.target.value);
                  setPage(1);
                }}
              >
                {["", "critical", "high", "elevated", "normal"].map((key) => (
                  <option key={key} value={key}>
                    {key || "All severities"}
                  </option>
                ))}
              </select>
            </label>
          )}
          {["overview", "pnl"].includes(section) &&
            ["day", "week", "month"].includes(dimension) &&
            chartRows.length > 0 && (
              <div className={card}>
                <p className="text-xs text-muted">
                  {section === "overview"
                    ? "Cumulative realized net"
                    : "Realized net"}{" "}
                  · {chartCurrency}
                </p>
                <div className="mt-3 h-64">
                  <SafeResponsiveContainer>
                    <AreaChart data={chartRows}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.12} />
                      <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                      <YAxis tick={{ fontSize: 10 }} />
                      <Tooltip formatter={(value) => money(value)} />
                      <Area
                        type="linear"
                        dataKey={section === "overview" ? "cumulative" : "net"}
                        stroke="#49c9aa"
                        fill="#49c9aa"
                        fillOpacity={0.1}
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                    </AreaChart>
                  </SafeResponsiveContainer>
                </div>
              </div>
            )}
          <div className={card}>
            <DataTable rows={rows} columns={columns} />
            <div className="mt-3 flex items-center justify-between text-xs text-muted">
              <span>
                {data?.total ?? 0} rows · Page {page}
              </span>
              <div className="flex gap-2">
                <button
                  className="btn-secondary text-xs"
                  disabled={page <= 1 || report.isPlaceholderData}
                  onClick={() => setPage((current) => current - 1)}
                >
                  Previous
                </button>
                <button
                  className="btn-secondary text-xs"
                  disabled={
                    page * 100 >= (data?.total || 0) || report.isPlaceholderData
                  }
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                </button>
                {![
                  "overview",
                  "exposure",
                  "risk-events",
                  "accounts",
                  "pnl",
                  "reports",
                ].includes(section) && (
                  <button
                    className="btn-secondary text-xs"
                    onClick={() => setDescending((current) => !current)}
                  >
                    Order: {descending ? "Descending" : "Ascending"}
                  </button>
                )}
              </div>
            </div>
          </div>
          {["overview", "accounts"].includes(section) && (
            <div className={card}>
              <h2 className="mb-3 text-sm font-medium">
                Observed account balance, equity and margin
              </h2>
              <DataTable
                rows={data?.snapshots?.rows}
                columns={[
                  "source_ts",
                  "currency",
                  "balance",
                  "equity",
                  "floating",
                  "margin",
                  "margin_utilization_pct",
                  "margin_level_pct",
                  ...(section === "accounts" ? ["login"] : []),
                  "free_margin",
                  "positions",
                  "accounts",
                ]}
              />
              <p className="mt-3 text-xs text-muted">
                Account-wide values; symbol filtering does not allocate equity
                or margin to an instrument. Snapshots are historical
                observations, not current live values.
              </p>
            </div>
          )}
          {["overview", "accounts"].includes(section) &&
            equityRows.length > 0 && (
              <div className={card}>
                <h2 className="text-sm font-medium">
                  Observed equity and balance trend
                </h2>
                <p className="mt-1 text-xs text-muted">
                  {chartCurrency || data?.snapshots?.rows?.[0]?.currency} ·
                  Latest available snapshot samples within this range · No
                  pre-archive history
                </p>
                <div className="mt-3 h-64">
                  <SafeResponsiveContainer>
                    <AreaChart data={equityRows}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.12} />
                      <XAxis dataKey="time" tick={{ fontSize: 9 }} />
                      <YAxis tick={{ fontSize: 10 }} />
                      <Tooltip formatter={(value) => money(value)} />
                      <Area
                        dataKey="equity"
                        stroke="#49c9aa"
                        fillOpacity={0}
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                      <Area
                        dataKey="balance"
                        stroke="#94a3b8"
                        fillOpacity={0}
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                    </AreaChart>
                  </SafeResponsiveContainer>
                </div>
              </div>
            )}
          {section === "risk-events" && (
            <p className="text-xs text-muted">
              <Link className="underline" to="/risk-automation">
                Configure risk controls
              </Link>{" "}
              ·{" "}
              <Link className="underline" to="/operations">
                Review and resolve evidence in Operations
              </Link>
            </p>
          )}
          {data?.series_truncated && (
            <p role="alert" className="text-xs text-amber-400">
              Chart series is limited to 10,000 currency/day points. Narrow the
              dates or currency; drawdown is unavailable for this truncated
              series.
            </p>
          )}
          <p className="text-xs leading-5 text-muted">
            {data?.definition} {data?.drawdown_definition}
          </p>
        </>
      )}
    </div>
  );
}
