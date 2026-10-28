import {
  formatNumber,
  formatCurrency,
  formatPercent,
  formatCompact,
} from "./formatters";

export function buildSummaryRows(rows = []) {
  if (!rows || rows.length === 0) {
    return { rows: [], summary: null };
  }

  const rawSummaryRow =
    rows.find(
      (row) =>
        String(row.Symbol || "")
          .trim()
          .toLowerCase() === "summary",
    ) ||
    rows.find(
      (row) =>
        String(row.Symbol || "")
          .trim()
          .toLowerCase() === "total",
    ) ||
    rows.at(-1);

  const total = rows.reduce(
    (carry, row) => ({
      positionClients: carry.positionClients + Number(row.PositionClients || 0),
      positionCoverage:
        carry.positionCoverage + Number(row.PositionCoverage || 0),
      buyClients: carry.buyClients + Number(row.VolumeBuyClients || 0),
      buyCoverage: carry.buyCoverage + Number(row.VolumeBuyCoverage || 0),
      sellClients: carry.sellClients + Number(row.VolumeSellClients || 0),
      sellCoverage: carry.sellCoverage + Number(row.VolumeSellCoverage || 0),
      net: carry.net + Number(row.VolumeNet || 0),
      profitClients: carry.profitClients + Number(row.ProfitFullClients || 0),
      profitCoverage:
        carry.profitCoverage + Number(row.ProfitFullCoverage || 0),
      uncovered: carry.uncovered + Number(row.ProfitUncoveredFull || 0),
    }),
    {
      positionClients: 0,
      positionCoverage: 0,
      buyClients: 0,
      buyCoverage: 0,
      sellClients: 0,
      sellCoverage: 0,
      net: 0,
      profitClients: 0,
      profitCoverage: 0,
      uncovered: 0,
    },
  );

  const normalized = rows
    .filter((row) => row !== rawSummaryRow)
    .map((row) => ({
      Symbol: String(row.Symbol || ""),
      Positions: `${Number(row.PositionClients || 0)} / ${Number(row.PositionCoverage || 0)}`,
      "Buy Volume": `${formatNumber(row.VolumeBuyClients, 2)} / ${formatNumber(row.VolumeBuyCoverage, 2)}`,
      "Buy Price": `${formatNumber(row.PriceBuyClients, Number(row.Digits || 5))} / ${formatNumber(row.PriceBuyCoverage, Number(row.Digits || 5))}`,
      "Sell Volume": `${formatNumber(row.VolumeSellClients, 2)} / ${formatNumber(row.VolumeSellCoverage, 2)}`,
      "Sell Price": `${formatNumber(row.PriceSellClients, Number(row.Digits || 5))} / ${formatNumber(row.PriceSellCoverage, Number(row.Digits || 5))}`,
      "Net Volume": formatNumber(row.VolumeNet, 2),
      "Profit (USD)": `${formatCurrency(row.ProfitFullClients)} / ${formatCurrency(row.ProfitFullCoverage)}`,
      "Uncovered (USD)": formatCurrency(row.ProfitUncoveredFull),
    }));

  const summary = rawSummaryRow
    ? {
        positionClients: Number(
          rawSummaryRow.PositionClients ?? total.positionClients,
        ),
        positionCoverage: Number(
          rawSummaryRow.PositionCoverage ?? total.positionCoverage,
        ),
        buyClients: Number(rawSummaryRow.VolumeBuyClients ?? total.buyClients),
        buyCoverage: Number(
          rawSummaryRow.VolumeBuyCoverage ?? total.buyCoverage,
        ),
        sellClients: Number(
          rawSummaryRow.VolumeSellClients ?? total.sellClients,
        ),
        sellCoverage: Number(
          rawSummaryRow.VolumeSellCoverage ?? total.sellCoverage,
        ),
        net: Number(rawSummaryRow.VolumeNet ?? total.net),
        profitClients: Number(
          rawSummaryRow.ProfitFullClients ?? total.profitClients,
        ),
        profitCoverage: Number(
          rawSummaryRow.ProfitFullCoverage ?? total.profitCoverage,
        ),
        uncovered: Number(rawSummaryRow.ProfitUncoveredFull ?? total.uncovered),
      }
    : total;

  if (rawSummaryRow) {
    normalized.push({
      Symbol: String(rawSummaryRow.Symbol || "Summary"),
      Positions: `${formatNumber(summary.positionClients, 0)} / ${formatNumber(summary.positionCoverage, 0)}`,
      "Buy Volume": `${formatNumber(summary.buyClients, 2)} / ${formatNumber(summary.buyCoverage, 2)}`,
      "Buy Price": "",
      "Sell Volume": `${formatNumber(summary.sellClients, 2)} / ${formatNumber(summary.sellCoverage, 2)}`,
      "Sell Price": "",
      "Net Volume": formatNumber(summary.net, 2),
      "Profit (USD)": `${formatCurrency(summary.profitClients)} / ${formatCurrency(summary.profitCoverage)}`,
      "Uncovered (USD)": formatCurrency(summary.uncovered),
    });
  }

  return { rows: normalized, summary };
}

export function buildExposureRows(rows = []) {
  const summary = { netUsd: 0, positiveUsd: 0, negativeUsd: 0 };

  const tableRows = rows.map((row) => {
    const clients = Number(row.VolumeClients || 0);
    const coverage = Number(row.VolumeCoverage || 0);
    const netUnits = clients - coverage;
    const rate = Number(row.PriceRate || 0);
    const netUsd = netUnits * rate;

    summary.netUsd += netUsd;
    if (netUsd >= 0) {
      summary.positiveUsd += netUsd;
    } else {
      summary.negativeUsd += netUsd;
    }

    return {
      Asset: String(row.Symbol || ""),
      Clients: formatCompact(clients),
      Coverage: formatCompact(coverage),
      "Net Total": formatNumber(netUnits, 0),
      Rate: formatNumber(row.PriceRate, Number(row.Digits || 5)),
      "Net Total (USD)": formatCurrency(netUsd),
      "Positive (USD)": formatCurrency(Math.max(netUsd, 0)),
      NetUSDValue: netUsd,
    };
  });

  return { rows: tableRows, summary };
}

export function buildMarginCallAlerts(rows = [], money = formatCurrency) {
  return rows
    .filter(
      (row) =>
        Number(row.margin_level || 0) > 0 &&
        Number(row.margin_level || 0) < 120,
    )
    .sort(
      (left, right) =>
        Number(left.margin_level || 0) - Number(right.margin_level || 0),
    )
    .slice(0, 8)
    .map((row) => {
      const marginLevel = Number(row.margin_level || 0);
      return {
        Login: Number(row.login || 0),
        Alert:
          marginLevel < 80 ? "Critical" : marginLevel < 100 ? "High" : "Watch",
        Name: String(row.name || "-"),
        Group: String(row.group || "-"),
        "Margin Level": formatPercent(marginLevel),
        Equity: money(row.equity),
        "Free Margin": money(row.free_margin),
        MarginLevelValue: marginLevel,
      };
    });
}

export function formatTickClock(value) {
  if (!value) return "Pending";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toISOString().slice(11, 19)} MT5`;
}

export function buildDisplayRows(rows = []) {
  return rows.map((row) => ({
    Symbol: row.symbol,
    Path: row.path || "-",
    Spread: row.spread_points,
    Bid: formatNumber(row.bid, row.digits),
    Ask: formatNumber(row.ask, row.digits),
    "Last Tick": formatTickClock(row.tick_time),
    "Quote Status": row.quote_status || "unavailable",
    "Tick Age": row.tick_age_seconds,
    Digits: row.digits,
    "Contract Size": formatNumber(row.contract_size, 2),
    "Tick Value": formatNumber(row.tick_value, 2),
    "Margin Initial": formatNumber(row.margin_initial, 2),
  }));
}

export function buildAccountExposureRows(rows = [], money = formatCurrency) {
  return rows
    .filter(
      (row) => Number.isFinite(row.margin) || Number.isFinite(row.floating_pnl),
    )
    .slice()
    .sort((left, right) => right.margin - left.margin)
    .slice(0, 5)
    .map((row) => ({
      Login: row.login,
      Name: row.name || "-",
      Group: row.group || "-",
      Margin: money(row.margin),
      "Margin Level": formatPercent(row.margin_level),
      "Floating P/L": money(row.floating_pnl),
      ExposureValue: Math.max(row.margin, 0),
    }));
}
