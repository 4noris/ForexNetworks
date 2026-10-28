/**
 * Shared data formatting utilities for the MT5 Brokerage Suite.
 */

function observed(value) {
  if (value == null || typeof value === "boolean" || (typeof value === "string" && !value.trim())) return null;
  if (!["number", "string"].includes(typeof value)) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function formatNumber(value, digits = 2) {
  const numeric = observed(value);
  if (numeric === null) return "Unavailable";
  return numeric.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatCurrency(value) {
  const numeric = observed(value);
  if (numeric === null) return "Unavailable";
  return numeric.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
  });
}

export function formatPercent(value) {
  const numeric = observed(value);
  if (numeric === null) return "Unavailable";
  return `${numeric.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}%`;
}

export function formatCompact(value) {
  const numeric = observed(value);
  if (numeric === null) return "Unavailable";
  return numeric.toLocaleString(undefined, {
    notation: "compact",
    compactDisplay: "short",
    maximumFractionDigits: 2,
  });
}

export function getManagerOffsetMinutes(mt5NowIso) {
  if (!mt5NowIso) return 0;
  const mt5Date = new Date(mt5NowIso);
  if (Number.isNaN(mt5Date.getTime())) return 0;

  // Calculate difference between MT5 server time and browser local UTC time
  const localNow = new Date();
  const diffMs = mt5Date.getTime() - localNow.getTime();
  return Math.round(diffMs / 60_000);
}
