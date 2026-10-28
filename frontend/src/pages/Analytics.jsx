import { initialBookGroups } from "../lib/bookDefaults";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import gsap from "gsap";
import {
  AlertCircle,
  ArrowUpRight,
  BarChart3,
  Download,
  DollarSign,
  Loader2,
  RefreshCw,
  Table2,
  Users,
} from "lucide-react";
import AnimatedCard from "../components/AnimatedCard";
import GroupTreeSelect from "../components/GroupTreeSelect";
import { formatTradeCount, getTradeBreakdown } from "../lib/utils";
import {
  downloadBrokerageExport,
  fetchBrokerageReport,
  fetchCompleteMt5Groups,
  getBookGroupSettings,
  fetchDiffReport,
  getCachedGroupsForProfile,
  storeCachedGroupsForProfile,
  useStoredProfile,
} from "../lib/api";

const DAILY_COLUMNS = [
  { key: "Login", label: "Login", type: "text" },
  { key: "Name", label: "Name", type: "text" },
  { key: "Prev Balance", label: "Prev Balance", type: "money", align: "right" },
  { key: "Deposit", label: "Deposit", type: "money", align: "right" },
  { key: "Closed P/L", label: "Closed P/L", type: "money", align: "right" },
  { key: "Balance", label: "Balance", type: "money", align: "right" },
  { key: "Credit", label: "Credit", type: "money", align: "right" },
  { key: "Floating", label: "Floating", type: "money", align: "right" },
  { key: "Equity", label: "Equity", type: "money", align: "right" },
  { key: "Margin", label: "Margin", type: "money", align: "right" },
  { key: "Free Margin", label: "Free Margin", type: "money", align: "right" },
];

const SUMMARY_COLUMNS = [
  { key: "Login", label: "Login", type: "text" },
  { key: "Name", label: "Name", type: "text" },
  { key: "Total Trades", label: "Total Trades", type: "count", align: "right" },
  {
    key: "Winning Trades",
    label: "Winning Trades",
    type: "count",
    align: "right",
  },
  {
    key: "Losing Trades",
    label: "Losing Trades",
    type: "count",
    align: "right",
  },
  {
    key: "Breakeven Trades",
    label: "Breakeven Trades",
    type: "count",
    align: "right",
  },
  { key: "Deposit", label: "Deposit", type: "money", align: "right" },
  { key: "Withdraw", label: "Withdraw", type: "money", align: "right" },
  { key: "In/Out", label: "In/Out", type: "money", align: "right" },
  { key: "Credit", label: "Credit", type: "money", align: "right" },
  { key: "Additional", label: "Additional", type: "money", align: "right" },
  { key: "Volume", label: "Volume", type: "number", align: "right" },
  { key: "Commission", label: "Commission", type: "money", align: "right" },
  { key: "Fee", label: "Fee", type: "money", align: "right" },
  { key: "Swap", label: "Swap", type: "money", align: "right" },
  {
    key: "Profit",
    label: "Profit",
    type: "money",
    align: "right",
    emphasize: true,
  },
];

const DAILY_SUMMARY_COLUMNS = [
  { key: "Date", label: "Date", type: "text" },
  {
    key: "Opening Equity",
    label: "Opening Equity",
    type: "money",
    align: "right",
  },
  {
    key: "Closing Equity",
    label: "Closing Equity",
    type: "money",
    align: "right",
  },
  { key: "Deposit", label: "Deposit", type: "money", align: "right" },
  { key: "Withdraw", label: "Withdraw", type: "money", align: "right" },
  { key: "Credit", label: "Credit", type: "money", align: "right" },
  { key: "Additional", label: "Additional", type: "money", align: "right" },
  { key: "Broker PnL", label: "Broker PnL", type: "money", align: "right" },
  { key: "Net PnL %", label: "Net PnL %", type: "number", align: "right" },
];

const EMPTY_REPORT = {
  selected_groups: [],
  opening_report: [],
  closing_report: [],
  summary: [],
  summary_with_total: [],
  accounts_display: [],
  group_summary: [],
  books_summary: [],
  daily_summary: [],
  lp_summary: [],
  summary_range: [],
  brokerage_totals: {
    opening_equity: 0,
    closing_equity: 0,
    difference: 0,
    deposit: 0,
    withdrawal: 0,
    credit_in: 0,
    credit_out: 0,
    net_credit: 0,
    additional: 0,
    total_lp_pnl: 0,
    client_a_book_pnl: 0,
    all_clients_pnl: 0,
    net_brokerage: 0,
  },
  warnings: [],
};

const USD_NUMBER_FORMAT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatMoney(value) {
  const numeric = Number(value || 0);
  const safeNumeric = Number.isFinite(numeric) ? numeric : 0;
  const formatted = USD_NUMBER_FORMAT.format(Math.abs(safeNumeric));

  return `${safeNumeric < 0 ? "-" : ""}$${formatted}`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function getUtcDateString(date = new Date()) {
  return date.toISOString().slice(0, 10);
}

function buildReportKey(
  reportMode,
  oeDate,
  ceDate,
  summaryFrom,
  summaryTo,
  selectedGroups,
  excludedLogins,
  rulesText,
) {
  const groupsKey = [...(selectedGroups || [])].sort().join("\u0001");
  const excludedKey = [...(excludedLogins || [])]
    .sort((left, right) => left - right)
    .join("\u0001");
  return [
    reportMode,
    oeDate,
    ceDate,
    summaryFrom,
    summaryTo,
    groupsKey,
    excludedKey,
    String(rulesText || "").trim(),
  ].join("|");
}

function normalizeArray(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeLoginKey(value) {
  return String(value ?? "").trim();
}

function mergeTradeCountsByLogin(accountRows, summaryRows) {
  const summaryLookup = new Map();

  normalizeArray(summaryRows).forEach((row) => {
    const loginKey = normalizeLoginKey(row?.Login);
    if (!loginKey || loginKey.toLowerCase() === "total") {
      return;
    }
    if (!summaryLookup.has(loginKey)) {
      summaryLookup.set(loginKey, row);
    }
  });

  return normalizeArray(accountRows).map((row) => {
    const summaryRow = summaryLookup.get(normalizeLoginKey(row?.Login));
    if (!summaryRow) {
      return row;
    }

    return {
      ...row,
      "Total Trades":
        summaryRow?.["Total Trades"] ??
        summaryRow?.Total_Trades ??
        row?.["Total Trades"] ??
        row?.Total_Trades,
      "Winning Trades":
        summaryRow?.["Winning Trades"] ??
        summaryRow?.Winning_Trades ??
        row?.["Winning Trades"] ??
        row?.Winning_Trades,
      "Losing Trades":
        summaryRow?.["Losing Trades"] ??
        summaryRow?.Losing_Trades ??
        row?.["Losing Trades"] ??
        row?.Losing_Trades,
      "Breakeven Trades":
        summaryRow?.["Breakeven Trades"] ??
        summaryRow?.Breakeven_Trades ??
        row?.["Breakeven Trades"] ??
        row?.Breakeven_Trades,
      "Win %": summaryRow?.["Win %"] ?? row?.["Win %"],
      "Loss %": summaryRow?.["Loss %"] ?? row?.["Loss %"],
    };
  });
}

function parseLoginList(text) {
  return Array.from(
    new Set(
      String(text || "")
        .split(/[\s,;]+/)
        .map((token) => token.trim())
        .filter(Boolean)
        .map((token) => Number(token))
        .filter((value) => Number.isInteger(value) && value > 0),
    ),
  );
}

function normalizeReport(data) {
  return {
    selected_groups: normalizeArray(data?.selected_groups),
    opening_report: normalizeArray(data?.opening_report),
    closing_report: normalizeArray(data?.closing_report),
    summary: normalizeArray(data?.summary),
    summary_with_total: normalizeArray(data?.summary_with_total),
    accounts_display: normalizeArray(data?.accounts_display),
    group_summary: normalizeArray(data?.group_summary),
    books_summary: normalizeArray(data?.books_summary),
    daily_summary: normalizeArray(data?.daily_summary),
    lp_summary: normalizeArray(data?.lp_summary),
    summary_range: normalizeArray(data?.summary_range),
    profile_name: data?.profile_name || data?.profileName || "",
    brokerage_totals: {
      opening_equity: Number(
        data?.opening_equity_total ??
          data?.brokerage_totals?.opening_equity ??
          0,
      ),
      closing_equity: Number(
        data?.closing_equity_total ??
          data?.brokerage_totals?.closing_equity ??
          0,
      ),
      difference: Number(
        data?.equity_difference ?? data?.brokerage_totals?.difference ?? 0,
      ),
      deposit: Number(data?.brokerage_totals?.deposit ?? 0),
      withdrawal: Number(data?.brokerage_totals?.withdrawal ?? 0),
      credit_in: Number(data?.brokerage_totals?.credit_in ?? 0),
      credit_out: Number(data?.brokerage_totals?.credit_out ?? 0),
      net_credit: Number(data?.brokerage_totals?.net_credit ?? 0),
      additional: Number(data?.brokerage_totals?.additional ?? 0),
      total_lp_pnl: Number(data?.brokerage_totals?.total_lp_pnl ?? 0),
      client_a_book_pnl: Number(data?.brokerage_totals?.client_a_book_pnl ?? 0),
      all_clients_pnl: Number(data?.brokerage_totals?.all_clients_pnl ?? 0),
      net_brokerage: Number(
        data?.net_brokerage ?? data?.brokerage_totals?.net_brokerage ?? 0,
      ),
    },
    warnings: normalizeArray(data?.warnings),
  };
}

function csvEscape(value) {
  const text = value === null || value === undefined ? "" : String(value);
  if (/[",\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function recordsToCsv(rows, columns) {
  const normalizedRows = normalizeArray(rows);
  const header = columns.map((column) => csvEscape(column.label)).join(",");
  const body = normalizedRows
    .map((row) =>
      columns.map((column) => csvEscape(row?.[column.key])).join(","),
    )
    .join("\n");
  return [header, body].filter(Boolean).join("\n");
}

function downloadCsv(filename, rows, columns) {
  const csv = recordsToCsv(rows, columns);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function buildSummaryTotals(rows) {
  return normalizeArray(rows).reduce(
    (totals, row) => {
      const deposit = Number(row?.Deposit || 0);
      const withdrawal = Number(row?.Withdraw || 0);
      const credit = Number(row?.Credit || 0);
      const additional = Number(row?.Additional || 0);
      const volume = Number(row?.Volume || 0);
      const commission = Number(row?.Commission || 0);
      const fee = Number(row?.Fee || 0);
      const swap = Number(row?.Swap || 0);
      const profit = Number(row?.Profit || 0);

      return {
        deposit: totals.deposit + deposit,
        withdrawal: totals.withdrawal + withdrawal,
        credit: totals.credit + credit,
        additional: totals.additional + additional,
        volume: totals.volume + volume,
        commission: totals.commission + commission,
        fee: totals.fee + fee,
        swap: totals.swap + swap,
        profit: totals.profit + profit,
      };
    },
    {
      deposit: 0,
      withdrawal: 0,
      credit: 0,
      additional: 0,
      volume: 0,
      commission: 0,
      fee: 0,
      swap: 0,
      profit: 0,
    },
  );
}

function MetricCard({ label, value, tone = "neutral", icon: Icon, delay = 0 }) {
  const toneColor =
    tone === "positive"
      ? "#10b981"
      : tone === "negative"
        ? "#ef4444"
        : "rgb(var(--color-text))";
  return (
    <AnimatedCard className="p-4" delay={delay}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p
            className="text-[10px] font-semibold uppercase tracking-[0.2em]"
            style={{ color: "rgb(var(--color-text-3))" }}
          >
            {label}
          </p>
          <p
            className="mt-2 text-xl font-semibold tabular-nums"
            style={{ color: toneColor }}
          >
            {value}
          </p>
        </div>
        {Icon && (
          <div
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg"
            style={{
              background: "var(--btn-ghost-bg)",
              border: "1px solid var(--inp-border)",
              color: "rgb(var(--color-text-2))",
            }}
          >
            <Icon size={15} />
          </div>
        )}
      </div>
    </AnimatedCard>
  );
}

function RankedListCard({ title, rows, emptyMessage, delay = 0 }) {
  return (
    <AnimatedCard className="p-4" delay={delay}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3
          className="text-sm font-semibold"
          style={{ color: "rgb(var(--color-text))" }}
        >
          {title}
        </h3>
        <span className="badge">{rows.length} accounts</span>
      </div>
      <div className="max-h-[320px] space-y-1.5 overflow-y-auto scrollbar-hidden">
        {rows.map((row, index) => {
          const profit = Number(row?.Profit ?? row?.["NET PNL USD"] ?? 0);
          const login = row?.Login ?? "-";
          const { winningTrades, losingTrades, totalTrades } =
            getTradeBreakdown(row);
          return (
            <div
              key={`${title}-${login}-${index}`}
              className="flex items-center justify-between gap-3 rounded-xl p-2.5"
              style={{
                background: "var(--inp-bg)",
                border: "1px solid var(--inp-border)",
              }}
            >
              <div className="min-w-0">
                <p
                  className="truncate text-xs font-medium"
                  style={{ color: "rgb(var(--color-text))" }}
                >
                  {row?.Name || `Login ${login}`}
                </p>
                <p
                  className="text-[10px]"
                  style={{ color: "rgb(var(--color-text-3))" }}
                >
                  #{login}
                </p>
                <div className="mt-1 flex flex-wrap gap-1.5">
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
              <span
                className={`shrink-0 text-xs font-semibold tabular-nums ${profit >= 0 ? "text-success" : "text-danger"}`}
              >
                {formatMoney(profit)}
              </span>
            </div>
          );
        })}
        {rows.length === 0 && (
          <p className="text-xs" style={{ color: "rgb(var(--color-text-3))" }}>
            {emptyMessage}
          </p>
        )}
      </div>
    </AnimatedCard>
  );
}

function ReportTable({
  title,
  rows,
  columns,
  filename,
  emptyMessage,
  loading,
}) {
  const normalizedRows = normalizeArray(rows);
  const hasRows = normalizedRows.length > 0;
  const totalRowIndex = normalizedRows.findIndex(
    (row) => String(row?.Login ?? "").toLowerCase() === "total",
  );

  const handleDownload = () => {
    if (!hasRows) {
      return;
    }
    downloadCsv(filename, normalizedRows, columns);
  };

  return (
    <AnimatedCard className="overflow-hidden">
      <div
        className="flex items-center justify-between gap-3 border-b px-4 py-3"
        style={{ borderColor: "rgb(var(--color-border))" }}
      >
        <h3
          className="text-sm font-semibold"
          style={{ color: "rgb(var(--color-text))" }}
        >
          {title}
        </h3>
        <div className="flex items-center gap-3">
          <span
            className="text-xs"
            style={{ color: "rgb(var(--color-text-3))" }}
          >
            {hasRows ? `${normalizedRows.length} rows` : "No data"}
          </span>
          <button
            onClick={handleDownload}
            disabled={!hasRows}
            className="btn-ghost h-8 w-8 justify-center p-0"
            title={`Download ${title} CSV`}
            aria-label={`Download ${title} CSV`}
          >
            <Download size={14} />
          </button>
        </div>
      </div>
      {loading && !hasRows ? (
        <div
          className="flex items-center justify-center py-12"
          style={{ color: "rgb(var(--color-text-3))" }}
        >
          <Loader2 size={16} className="mr-2 animate-spin" />
          Loading…
        </div>
      ) : hasRows ? (
        <div className="max-h-[420px] overflow-auto scrollbar-hidden">
          <table
            className="w-full table-fixed whitespace-nowrap text-left text-xs"
            style={{ minWidth: `${Math.max(760, columns.length * 112)}px` }}
          >
            <colgroup>
              {columns.map((col) => (
                <col
                  key={col.key}
                  style={{
                    width:
                      col.key === "Name"
                        ? "180px"
                        : col.key === "Date"
                          ? "126px"
                          : "112px",
                  }}
                />
              ))}
            </colgroup>
            <thead
              className="sticky top-0 z-10"
              style={{ background: "var(--inp-bg)" }}
            >
              <tr>
                {columns.map((col) => (
                  <th
                    key={col.key}
                    className={`px-4 py-3 font-semibold uppercase tracking-[0.12em] ${col.align === "right" ? "text-right" : "text-left"}`}
                    style={{ color: "rgb(var(--color-text-3))" }}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody style={{ borderTop: "1px solid rgb(var(--color-border))" }}>
              {normalizedRows.map((row, index) => {
                const isTotalRow =
                  String(row?.Login ?? "").toLowerCase() === "total" ||
                  index === totalRowIndex;
                const profit = Number(row?.Profit || 0);
                return (
                  <tr
                    key={`${row?.Login ?? "row"}-${index}`}
                    style={isTotalRow ? { background: "var(--inp-bg)" } : {}}
                  >
                    {columns.map((col) => {
                      const raw = row?.[col.key];
                      let val =
                        raw === null || raw === undefined || raw === ""
                          ? col.type === "text"
                            ? "-"
                            : 0
                          : raw;
                      if (col.type === "money") val = formatMoney(val);
                      else if (col.type === "count")
                        val = formatTradeCount(val);
                      else if (col.type === "number") val = formatNumber(val);
                      else val = String(val);
                      const isProfit = Boolean(col.emphasize) && !isTotalRow;
                      return (
                        <td
                          key={col.key}
                          className={`px-4 py-2.5 ${col.align === "right" ? "text-right tabular-nums" : ""} ${isProfit ? (profit >= 0 ? "text-success" : "text-danger") : ""} ${col.key === "Login" ? "font-medium" : ""}`}
                          style={{
                            color: isProfit
                              ? undefined
                              : isTotalRow
                                ? "rgb(var(--color-text))"
                                : "rgb(var(--color-text-2))",
                            borderBottom: "1px solid rgb(var(--color-border))",
                          }}
                        >
                          {val}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div
          className="py-10 text-center text-xs"
          style={{ color: "rgb(var(--color-text-3))" }}
        >
          {emptyMessage}
        </div>
      )}
    </AnimatedCard>
  );
}

export default function Analytics({ reportMode = "brokerage", active = true }) {
  const containerRef = useRef(null);
  const profile = useStoredProfile();
  const [availableGroups, setAvailableGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [groupFilterRulesText, setGroupFilterRulesText] = useState("");
  const [excludedAccountsText, setExcludedAccountsText] = useState("");
  const [oeDate, setOeDate] = useState(() => getUtcDateString(new Date(Date.now() - 86400000)));
  const [ceDate, setCeDate] = useState(() => getUtcDateString());
  const [customSummaryDates, setCustomSummaryDates] = useState(false);
  const [customSummaryFrom, setCustomSummaryFrom] = useState(() => getUtcDateString());
  const [customSummaryTo, setCustomSummaryTo] = useState(() => getUtcDateString());
  const summaryFrom = customSummaryDates
    ? customSummaryFrom
    : getUtcDateString(new Date(Date.parse(`${oeDate}T00:00:00Z`) + 86400000));
  const summaryTo = customSummaryDates ? customSummaryTo : ceDate;
  const [report, setReport] = useState(EMPTY_REPORT);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loadingReport, setLoadingReport] = useState(false);
  const [error, setError] = useState("");
  const [lastLoadedAt, setLastLoadedAt] = useState(null);
  const [lastLoadedKey, setLastLoadedKey] = useState("");
  const reportController = useRef(null);
  const reportGeneration = useRef(0);
  const isDiffReport = reportMode === "diff";
  const isRealBrokerageReport = reportMode === "real-brokerage";
  // "Real" means signed values exactly as MT5 reported them. Flooring negative
  // equity changes the brokerage total and is reserved for the explicit diff view.
  const clipNegativeEquity = false;
  const fetchReport = isDiffReport ? fetchDiffReport : fetchBrokerageReport;
  const pageTitle = isDiffReport
    ? "MT5 Diff + Trades Summary"
    : isRealBrokerageReport
      ? "Real Brokerage"
      : "MT5 Daily + Trades Summary";
  const noProfileDescription = isDiffReport
    ? "Select or create an MT5 profile in Settings before loading the diff report."
    : isRealBrokerageReport
      ? "Select or create an MT5 profile in Settings before loading the real brokerage report."
      : "Select or create an MT5 profile in Settings before loading the brokerage report.";
  const reportErrorMessage = isDiffReport
    ? "Failed to load diff report"
    : isRealBrokerageReport
      ? "Failed to load real brokerage report"
      : "Failed to load brokerage report";
  const dailySnapshotDescription = isDiffReport
    ? "Latest closing daily snapshot using the exact MT5 equity values."
    : isRealBrokerageReport
      ? "Latest closing daily snapshot used for the brokerage totals and summary equity."
      : "Latest closing daily snapshot used for the opening and closing equity totals.";
  const reportsSectionTitle = isDiffReport
    ? "MT5 Diff Reports"
    : isRealBrokerageReport
      ? "Summary Equity"
      : "MT5 Daily Reports";
  const dailyCsvPrefix = isDiffReport
    ? "mt5_diff_daily_report"
    : isRealBrokerageReport
      ? "mt5_real_brokerage_daily_report"
      : "mt5_daily_report";
  const summaryCsvPrefix = isDiffReport
    ? "mt5_diff_trades_summary"
    : isRealBrokerageReport
      ? "mt5_real_brokerage_trades_summary"
      : "mt5_trades_summary";
  const excludedLogins = useMemo(
    () => parseLoginList(excludedAccountsText),
    [excludedAccountsText],
  );
  const exportButtonLabel = isDiffReport
    ? "Download Full Report"
    : isRealBrokerageReport
      ? "Download Full Real Brokerage Report"
      : "Download Full Brokerage Report";

  const currentReportKey = buildReportKey(
    reportMode,
    oeDate,
    ceDate,
    summaryFrom,
    summaryTo,
    selectedGroups,
    excludedLogins,
    groupFilterRulesText,
  );
  const reportIsFresh = lastLoadedKey === currentReportKey;

  const summaryRows = useMemo(() => {
    const rows = normalizeArray(report.summary);
    return rows.filter(
      (row) => String(row?.Login ?? "").toLowerCase() !== "total",
    );
  }, [report.summary]);
  const accountRows = useMemo(
    () => normalizeArray(report.accounts_display),
    [report.accounts_display],
  );
  const accountRowsWithTradeCounts = useMemo(
    () => mergeTradeCountsByLogin(accountRows, summaryRows),
    [accountRows, summaryRows],
  );

  const summaryRowsWithTotal = useMemo(() => {
    if (normalizeArray(report.summary_with_total).length > 0) {
      return report.summary_with_total;
    }
    return report.summary;
  }, [report.summary, report.summary_with_total]);

  const summaryTotals = useMemo(
    () => buildSummaryTotals(summaryRows),
    [summaryRows],
  );
  const dailySummaryRows = useMemo(
    () => normalizeArray(report.daily_summary),
    [report.daily_summary],
  );
  const lpSummaryRows = useMemo(
    () => normalizeArray(report.lp_summary),
    [report.lp_summary],
  );
  const rankedSummaryRows = useMemo(
    () =>
      [...accountRowsWithTradeCounts].sort(
        (left, right) =>
          Number(right?.["NET PNL USD"] || 0) -
          Number(left?.["NET PNL USD"] || 0),
      ),
    [accountRowsWithTradeCounts],
  );
  const topWinners = useMemo(
    () => rankedSummaryRows.slice(0, 5),
    [rankedSummaryRows],
  );
  const topLosers = useMemo(
    () =>
      [...accountRowsWithTradeCounts]
        .sort(
          (left, right) =>
            Number(left?.["NET PNL USD"] || 0) -
            Number(right?.["NET PNL USD"] || 0),
        )
        .slice(0, 5),
    [accountRowsWithTradeCounts],
  );
  const summaryTradeStats = useMemo(
    () =>
      accountRowsWithTradeCounts.reduce(
        (stats, row) => {
          const profit = Number(row?.["NET PNL USD"] || row?.Profit || 0);
          const volume = Number(row?.["Closed Lots"] || row?.Volume || 0);
          const commission = Number(row?.Commission || 0);
          const fee = Number(row?.Fee || 0);
          const swap = Number(row?.Swap || 0);
          const { winningTrades, losingTrades, breakevenTrades, totalTrades } =
            getTradeBreakdown(row);

          return {
            totalClients: stats.totalClients + 1,
            totalTrades: stats.totalTrades + totalTrades,
            winningTrades: stats.winningTrades + winningTrades,
            losingTrades: stats.losingTrades + losingTrades,
            breakevenTrades: stats.breakevenTrades + breakevenTrades,
            totalProfit: stats.totalProfit + profit,
            totalVolume: stats.totalVolume + volume,
            totalCommission: stats.totalCommission + commission,
            totalFee: stats.totalFee + fee,
            totalSwap: stats.totalSwap + swap,
          };
        },
        {
          totalClients: 0,
          totalTrades: 0,
          winningTrades: 0,
          losingTrades: 0,
          breakevenTrades: 0,
          totalProfit: 0,
          totalVolume: 0,
          totalCommission: 0,
          totalFee: 0,
          totalSwap: 0,
        },
      ),
    [accountRowsWithTradeCounts],
  );
  const openingEquity = Number(report.brokerage_totals?.opening_equity || 0);
  const closingEquity = Number(report.brokerage_totals?.closing_equity || 0);
  const computedNetBrokerage =
    closingEquity -
    openingEquity -
    summaryTotals.deposit -
    summaryTotals.credit -
    summaryTotals.additional +
    summaryTotals.withdrawal;
  const netBrokerage = Number.isFinite(computedNetBrokerage)
    ? computedNetBrokerage
    : Number(report.brokerage_totals?.net_brokerage || 0);

  const closingRows = normalizeArray(report.closing_report);
  const warnings = normalizeArray(report.warnings);
  const selectedCount = normalizeArray(selectedGroups).length;

  const loadGroups = async ({ refresh = false } = {}) => {
    if (!profile) {
      return [];
    }

    const cachedGroups = getCachedGroupsForProfile(profile.id);
    if (!refresh && cachedGroups.length > 0) {
      setAvailableGroups(cachedGroups);
    }

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
  };

  const runReport = async ({
    selectedGroupsOverride = null,
    groupsOverride = null,
    oeDateOverride = null,
    ceDateOverride = null,
    summaryFromOverride = null,
    summaryToOverride = null,
    rulesOverride = null,
  } = {}) => {
    if (!profile) {
      return;
    }

    const nextSelectedGroups = Array.isArray(selectedGroupsOverride)
      ? selectedGroupsOverride
      : selectedGroups;
    const nextGroups = Array.isArray(groupsOverride)
      ? groupsOverride
      : nextSelectedGroups;
    const nextOeDate = oeDateOverride || oeDate;
    const nextCeDate = ceDateOverride || ceDate;
    const nextSummaryFrom = summaryFromOverride || summaryFrom;
    const nextSummaryTo = summaryToOverride || summaryTo;
    const nextRules =
      rulesOverride !== null ? rulesOverride : groupFilterRulesText;

    if (!nextSelectedGroups.length && !String(nextRules || "").trim()) {
      setError("Select at least one group.");
      setReport(EMPTY_REPORT);
      setLastLoadedKey("");
      return;
    }

    const today = getUtcDateString();
    if (nextOeDate > nextCeDate || nextCeDate > today || nextSummaryTo > today ||
      (customSummaryDates && nextSummaryFrom > nextSummaryTo)) {
      setError("Choose ordered equity and summary dates in UTC, with no future dates.");
      setReport(EMPTY_REPORT);
      setLastLoadedKey("");
      return;
    }

    setError("");
    setLoadingReport(true);
    reportController.current?.abort();
    const controller = new AbortController();
    reportController.current = controller;
    const generation = ++reportGeneration.current;

    try {
      const data = await fetchReport(
        {
          server: profile.server,
          login: profile.mt5_login,
          password: profile.mt5_password,
          selected_groups: nextGroups,
          excluded_logins: excludedLogins,
          group_filter_rules: nextRules,
          profile_name: profile.name,
          clip_negative_equity: clipNegativeEquity,
          opening_equity_date: nextOeDate,
          closing_equity_date: nextCeDate,
          date_from: nextSummaryFrom,
          date_to: nextSummaryTo,
        },
        { signal: controller.signal },
      );

      if (generation !== reportGeneration.current) return;
      if (!String(nextRules || "").trim()) {
        const expectedGroups = [...new Set(nextGroups)].sort();
        const returnedGroups = [
          ...new Set(normalizeArray(data.selected_groups)),
        ].sort();
        if (
          expectedGroups.length !== returnedGroups.length ||
          expectedGroups.some((group, index) => group !== returnedGroups[index])
        ) {
          throw new Error(
            "MT5 report group scope did not match the current selection.",
          );
        }
      }

      const normalized = normalizeReport(data);
      setReport(normalized);
      setLastLoadedAt(new Date());
      setLastLoadedKey(
        buildReportKey(
          reportMode,
          nextOeDate,
          nextCeDate,
          nextSummaryFrom,
          nextSummaryTo,
          nextGroups,
          excludedLogins,
          nextRules,
        ),
      );
    } catch (err) {
      if (generation !== reportGeneration.current) return;
      setReport(EMPTY_REPORT);
      setLastLoadedAt(null);
      setLastLoadedKey("");
      if (err.message !== "Request was cancelled")
        setError(err.message || reportErrorMessage);
    } finally {
      if (generation === reportGeneration.current) {
        setLoadingReport(false);
        reportController.current = null;
      }
    }
  };

  const handleDownloadExcel = async () => {
    if (!profile) {
      return;
    }

    try {
      const { blob, filename } = await downloadBrokerageExport({
        server: profile.server,
        login: profile.mt5_login,
        password: profile.mt5_password,
        opening_equity_date: oeDate,
        closing_equity_date: ceDate,
        date_from: summaryFrom,
        date_to: summaryTo,
        profile_name: profile.name,
        clip_negative_equity: clipNegativeEquity,
        selected_groups: selectedGroups,
        excluded_logins: excludedLogins,
        group_filter_rules: groupFilterRulesText,
        cached_report: reportIsFresh ? report : null,
      });

      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download =
        filename ||
        `${isDiffReport ? "Diff" : isRealBrokerageReport ? "RealBrokerage" : "Brokerage"}_${oeDate}_to_${ceDate}.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setError(error.message || "Failed to download Excel report");
    }
  };

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    gsap.fromTo(
      containerRef.current,
      { opacity: 0, y: 20 },
      { opacity: 1, y: 0, duration: 0.7, ease: "power3.out" },
    );
  }, []);

  useEffect(() => {
    if (!profile) {
      setAvailableGroups([]);
      setSelectedGroups([]);
      setReport(EMPTY_REPORT);
      setError("");
      setLastLoadedAt(null);
      setLastLoadedKey("");
      setLoadingGroups(false);
      setLoadingReport(false);
      return;
    }

    let alive = true;

    (async () => {
      try {
        setAvailableGroups([]);
        setSelectedGroups([]);
        setReport(EMPTY_REPORT);
        setError("");
        setLastLoadedAt(null);
        setLastLoadedKey("");
        setGroupFilterRulesText("");
        setExcludedAccountsText("");

        const [nextGroups, defaultBooks] = await Promise.all([loadGroups({ refresh: true }), getBookGroupSettings()]);
        if (!alive) {
          return;
        }

        setSelectedGroups(initialBookGroups(nextGroups, defaultBooks));
      } catch (err) {
        if (!alive) {
          return;
        }
        setError(err.message || reportErrorMessage);
      }
    })();

    return () => {
      alive = false;
    };
    // Run on selected profile changes. The button handles all later refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile, reportMode]);

  useEffect(() => {
    if (!profile || !active) return undefined;
    let alive = true;
    const refreshCatalog = async () => {
      try {
        const nextGroups = await fetchCompleteMt5Groups(profile);
        if (!alive) return;
        setAvailableGroups(nextGroups);
        storeCachedGroupsForProfile(profile.id, nextGroups);
      } catch {
        // Keep the last usable catalog during a transient Manager outage.
      }
    };
    const timer = window.setInterval(refreshCatalog, 15000);
    const handleFocus = () => refreshCatalog();
    window.addEventListener("focus", handleFocus);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", handleFocus);
    };
  }, [profile, active]);

  useEffect(() => () => {
    reportController.current?.abort();
    reportGeneration.current += 1;
  }, []);

  if (!profile) {
    return (
      <div ref={containerRef} className="max-w-5xl mx-auto">
        <AnimatedCard className="p-8 space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs uppercase tracking-[0.24em] text-gray-300">
            <AlertCircle size={14} />
            Profile required
          </div>
          <div className="space-y-2">
          <h1 className="text-2xl font-bold text-[rgb(var(--color-text))]">{pageTitle}</h1>
            <p className="text-[rgb(var(--color-text-2))]">{noProfileDescription}</p>
          </div>
          <Link
            to="/settings"
            className="inline-flex items-center gap-2 rounded-xl bg-accent-500 px-4 py-3 font-medium text-dark-900 transition-opacity hover:opacity-90"
          >
            Go to Settings
          </Link>
        </AnimatedCard>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="mx-auto w-full max-w-[1600px] space-y-5">
      <AnimatedCard className="space-y-5 p-4 sm:p-5">
        <div>
          <h1 className="text-lg font-semibold text-[rgb(var(--color-text))]">Brokerage overview</h1>
          <p className="mt-1 text-xs text-[rgb(var(--color-text-2))]">Choose a report scope and UTC dates, then load the report.</p>
        </div>
        <details className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3">
          <summary className="cursor-pointer text-xs font-semibold text-[rgb(var(--color-text-2))]">
            Advanced group and account filters
            {(groupFilterRulesText.trim() || excludedAccountsText.trim()) && <span className="ml-2 rounded-full bg-[rgb(var(--color-accent)/0.12)] px-2 py-0.5 text-[rgb(var(--color-accent))]">Active</span>}
          </summary>
          <div className="mt-3 w-full">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            <label className="block">
              <span
                className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.22em]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                Group filter rules
              </span>
              <textarea
                value={groupFilterRulesText}
                onChange={(e) => setGroupFilterRulesText(e.target.value)}
                placeholder={"!Demo*\nBroker\\*"}
                rows={2}
                className="field-input text-xs w-full"
                disabled={loadingGroups || loadingReport}
              />
            </label>
            <label className="block">
              <span
                className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.22em]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                Exclude accounts
              </span>
              <textarea
                value={excludedAccountsText}
                onChange={(e) => setExcludedAccountsText(e.target.value)}
                placeholder={"100001, 100002"}
                rows={2}
                className="field-input text-xs w-full"
                disabled={loadingGroups || loadingReport}
              />
            </label>
          </div>
          </div>
        </details>

        <div className="flex w-full min-w-0 flex-col gap-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3 sm:p-4">
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-[0.14em] text-[rgb(var(--color-text-2))]">Equity snapshots</h2>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="min-w-0 text-xs font-medium text-[rgb(var(--color-text-2))]">Opening equity (UTC)
                  <input type="date" value={oeDate} max={getUtcDateString()} onChange={(event) => setOeDate(event.target.value)} className="field-input mt-1 w-full min-w-0" />
                </label>
                <label className="min-w-0 text-xs font-medium text-[rgb(var(--color-text-2))]">Closing equity (UTC)
                  <input type="date" value={ceDate} min={oeDate} max={getUtcDateString()} onChange={(event) => setCeDate(event.target.value)} className="field-input mt-1 w-full min-w-0" />
                </label>
              </div>
            </section>
            <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-3 sm:p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-[rgb(var(--color-text-2))]">Summary transactions</h2>
                <label className="flex items-center gap-2 text-xs text-[rgb(var(--color-text-2))]">
                  <input type="checkbox" checked={customSummaryDates} onChange={(event) => {
                    if (event.target.checked) {
                      setCustomSummaryFrom(summaryFrom);
                      setCustomSummaryTo(summaryTo);
                    }
                    setCustomSummaryDates(event.target.checked);
                  }} /> Separate dates
                </label>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="min-w-0 text-xs font-medium text-[rgb(var(--color-text-2))]">From (UTC)
                  <input type="date" value={summaryFrom} max={getUtcDateString()} disabled={!customSummaryDates} onChange={(event) => setCustomSummaryFrom(event.target.value)} className="field-input mt-1 w-full min-w-0 disabled:opacity-70" />
                </label>
                <label className="min-w-0 text-xs font-medium text-[rgb(var(--color-text-2))]">To (UTC)
                  <input type="date" value={summaryTo} max={getUtcDateString()} disabled={!customSummaryDates} onChange={(event) => setCustomSummaryTo(event.target.value)} className="field-input mt-1 w-full min-w-0 disabled:opacity-70" />
                </label>
              </div>
              <p className="mt-2 text-[11px] text-[rgb(var(--color-text-3))]">{customSummaryDates ? "Using a separate transaction period" : "Automatically follows the equity dates"}</p>
            </section>
          </div>

          <div className="grid gap-3 border-t border-[rgb(var(--color-border))] pt-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <GroupTreeSelect
              groups={availableGroups}
              value={selectedGroups}
              onChange={setSelectedGroups}
              className="min-w-0 flex-1"
              label="Groups"
            />
            <div className="flex w-full gap-2 sm:w-auto">
              <button
              onClick={() => runReport({ groupsOverride: selectedGroups })}
              disabled={loadingGroups || loadingReport}
              className="btn-primary h-11 min-w-0 flex-1 justify-center px-4 sm:flex-none"
              title={
                loadingReport || loadingGroups ? "Loading report" : "Load report"
              }
              aria-label={
                loadingReport || loadingGroups ? "Loading report" : "Load report"
              }
            >
              {loadingReport || loadingGroups ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              <span>{loadingReport ? "Loading..." : "Load report"}</span>
              </button>
              <button
              onClick={handleDownloadExcel}
              disabled={loadingGroups || loadingReport || !reportIsFresh || !lastLoadedAt}
              className="btn-ghost h-11 w-11 shrink-0 justify-center p-0"
              title={exportButtonLabel}
              aria-label={exportButtonLabel}
            >
              <Download size={15} />
              </button>
            </div>
          </div>
        </div>
      </AnimatedCard>

      {loadingGroups && (
        <AnimatedCard className="px-4 py-3">
          <p className="text-xs" style={{ color: "rgb(var(--color-text-2))" }}>
            Loading groups…
          </p>
        </AnimatedCard>
      )}
      {error && (
        <AnimatedCard className="px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertCircle size={13} className="text-danger" />
              <p className="text-xs text-danger">{error}</p>
            </div>
            <button
              onClick={() => runReport({ groupsOverride: selectedGroups })}
              disabled={loadingGroups || loadingReport}
              className="btn-ghost text-xs py-1 px-2"
            >
              Retry
            </button>
          </div>
        </AnimatedCard>
      )}
      {warnings.length > 0 && (
        <AnimatedCard className="px-4 py-3">
          <p className="text-xs text-amber-400">
            {warnings.slice(0, 3).join(" · ")}
          </p>
        </AnimatedCard>
      )}
      {!loadingGroups && !loadingReport && !lastLoadedAt && !error && (
        <AnimatedCard className="flex flex-col items-center px-5 py-10 text-center sm:py-14">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[rgb(var(--color-accent)/0.1)] text-[rgb(var(--color-accent))]">
            <BarChart3 size={22} />
          </div>
          <h2 className="mt-4 text-base font-semibold text-[rgb(var(--color-text))]">Ready to build your report</h2>
          <p className="mt-2 max-w-md text-sm text-[rgb(var(--color-text-2))]">
            Select the groups and UTC dates above. The report loads only when you click Load report.
          </p>
          <button className="btn-primary mt-5" onClick={() => runReport({ groupsOverride: selectedGroups })}>
            <RefreshCw size={14} /> Load report
          </button>
        </AnimatedCard>
      )}
      {!reportIsFresh && lastLoadedAt && (
        <AnimatedCard className="px-4 py-3">
          <p className="text-xs text-amber-400">
            Filters changed — click Load report to refresh.
          </p>
        </AnimatedCard>
      )}

      {loadingReport && (
        <AnimatedCard className="flex items-center gap-2 px-4 py-3 text-xs text-[rgb(var(--color-text-2))]" role="status">
          <Loader2 size={14} className="animate-spin" /> Loading the selected report...
        </AnimatedCard>
      )}

      {Boolean(lastLoadedAt) && reportIsFresh && <>

      {!isRealBrokerageReport && <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <MetricCard
          label="Opening Equity"
          value={formatMoney(openingEquity)}
          icon={DollarSign}
          delay={0.05}
        />
        <MetricCard
          label="Closing Equity"
          value={formatMoney(closingEquity)}
          icon={DollarSign}
          delay={0.1}
        />
        <MetricCard
          label="Net Brokerage"
          value={formatMoney(netBrokerage)}
          tone={netBrokerage >= 0 ? "positive" : "negative"}
          icon={ArrowUpRight}
          delay={0.15}
        />
      </div>}
      <AnimatedCard className="p-4">
        <div className="mb-3 flex items-center gap-2">
          <Table2 size={14} style={{ color: "rgb(var(--color-text-3))" }} />
          <h3
            className="text-sm font-semibold"
            style={{ color: "rgb(var(--color-text))" }}
          >
            {reportsSectionTitle}
          </h3>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
          {[
            ["Groups", selectedCount],
            ["Clients", summaryTradeStats.totalClients],
            [
              "Win/Loss Trades",
              `${formatTradeCount(summaryTradeStats.winningTrades)}/${formatTradeCount(summaryTradeStats.losingTrades)}`,
            ],
          ].map(([l, v]) => (
            <div
              key={l}
              className="rounded-xl p-3"
              style={{
                background: "var(--inp-bg)",
                border: "1px solid var(--inp-border)",
              }}
            >
              <p
                className="text-[10px] font-semibold uppercase tracking-[0.18em]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                {l}
              </p>
              <p
                className="mt-1.5 text-base font-semibold tabular-nums"
                style={{ color: "rgb(var(--color-text))" }}
              >
                {v}
              </p>
            </div>
          ))}
          <div
            className="rounded-xl p-3"
            style={{
              background: "var(--inp-bg)",
              border: "1px solid var(--inp-border)",
            }}
          >
            <p
              className="text-[10px] font-semibold uppercase tracking-[0.18em]"
              style={{ color: "rgb(var(--color-text-3))" }}
            >
              Net Profit
            </p>
            <p
              className={`mt-1.5 text-base font-semibold tabular-nums ${summaryTradeStats.totalProfit >= 0 ? "text-success" : "text-danger"}`}
            >
              {formatMoney(summaryTradeStats.totalProfit)}
            </p>
          </div>
        </div>
      </AnimatedCard>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <RankedListCard
          title="Top Winners"
          rows={topWinners}
          emptyMessage="No winning accounts."
          delay={0.05}
        />
        <RankedListCard
          title="Top Losers"
          rows={topLosers}
          emptyMessage="No losing accounts."
          delay={0.1}
        />
      </div>

      {!isRealBrokerageReport && <ReportTable
        title="Daily Equity Summary"
        description="Opening and closing equity by day with broker PnL, deposits, withdrawals, credit, and net percentage."
        rows={dailySummaryRows}
        columns={DAILY_SUMMARY_COLUMNS}
        filename={`${dailyCsvPrefix}_equity_${summaryFrom.replaceAll("-", "")}_${summaryTo.replaceAll("-", "")}.csv`}
        emptyMessage="No daily equity summary rows returned for the selected filters."
        loading={loadingReport}
      />}

      {!isRealBrokerageReport && <ReportTable
        title="Daily Snapshot"
        description={dailySnapshotDescription}
        rows={closingRows}
        columns={DAILY_COLUMNS}
        filename={`${dailyCsvPrefix}_${ceDate.replaceAll("-", "")}.csv`}
        emptyMessage="No daily snapshot rows returned for the selected filters."
        loading={loadingReport}
      />}

      {!isRealBrokerageReport && <AnimatedCard className="p-4">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <h3 className="text-lg font-semibold text-[rgb(var(--color-text))]">
              MT5 Server Reports {"->"} Trades {"->"} Summary
            </h3>
            <p className="text-sm text-[rgb(var(--color-text-2))]">
              Trades summary for the selected date range with the total row
              appended.
            </p>
          </div>
          <div className="text-sm text-[rgb(var(--color-text-2))]">
            {summaryRows.length > 0
              ? `${summaryRows.length} client row${summaryRows.length === 1 ? "" : "s"}`
              : "No client rows returned"}
          </div>
        </div>
      </AnimatedCard>}

      {!isRealBrokerageReport && <MetricCard
        label="Net Brokerage"
        value={formatMoney(netBrokerage)}
        caption="Closing equity minus opening equity and net cash flow"
        tone={netBrokerage >= 0 ? "positive" : "negative"}
        icon={ArrowUpRight}
        delay={0.2}
      />}

      <ReportTable
        title="Trades Summary"
        description="Deposit, withdrawal, credit, volume and profit for the selected trades window."
        rows={summaryRowsWithTotal}
        columns={SUMMARY_COLUMNS}
        filename={`${summaryCsvPrefix}_${summaryFrom.replaceAll("-", "")}_${summaryTo.replaceAll("-", "")}.csv`}
        emptyMessage="No trades summary rows returned for the selected filters."
        loading={loadingReport}
      />

      {isRealBrokerageReport && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <MetricCard label="Opening Equity" value={formatMoney(openingEquity)} icon={DollarSign} delay={0.05} />
            <MetricCard label="Closing Equity" value={formatMoney(closingEquity)} icon={DollarSign} delay={0.1} />
          </div>
          <MetricCard label="Net Brokerage" value={formatMoney(netBrokerage)}
            caption="Closing equity minus opening equity and net cash flow"
            tone={netBrokerage >= 0 ? "positive" : "negative"} icon={ArrowUpRight} delay={0.15} />
        </div>
      )}

      {lpSummaryRows.length > 0 && (
        <ReportTable
          title="LP Summary"
          description="LP-level brokerage totals for the selected brokerage window."
          rows={lpSummaryRows}
          columns={[
            { key: "LP Name", label: "LP Name", type: "text" },
            { key: "PNL", label: "PnL", type: "money", align: "right" },
          ]}
          filename={`${dailyCsvPrefix}_lp_summary_${summaryFrom.replaceAll("-", "")}_${summaryTo.replaceAll("-", "")}.csv`}
          emptyMessage="No LP summary rows returned for the selected filters."
          loading={loadingReport}
        />
      )}
      </>}
    </div>
  );
}
