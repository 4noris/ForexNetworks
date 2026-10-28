import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export function formatTradeCount(value) {
  const numeric = Number(value || 0);
  const safeNumeric = Number.isFinite(numeric) ? numeric : 0;
  return safeNumeric.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function getTradeBreakdown(row = {}) {
  const readCount = (...values) => {
    for (const value of values) {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) {
        return numeric;
      }
    }
    return null;
  };

  const winningTrades = readCount(
    row?.["Winning Trades"],
    row?.Winning_Trades,
    row?.WinningTrades,
    row?.winningTrades,
  );
  const losingTrades = readCount(
    row?.["Losing Trades"],
    row?.Losing_Trades,
    row?.LosingTrades,
    row?.losingTrades,
  );
  const breakevenTrades = readCount(
    row?.["Breakeven Trades"],
    row?.Breakeven_Trades,
    row?.BreakevenTrades,
    row?.breakevenTrades,
  );
  const totalTrades = readCount(
    row?.["Total Trades"],
    row?.Total_Trades,
    row?.TotalTrades,
    row?.totalTrades,
  );

  const resolvedWinningTrades = winningTrades ?? 0;
  const resolvedLosingTrades = losingTrades ?? 0;
  const resolvedBreakevenTrades = breakevenTrades ?? 0;
  const resolvedTotalTrades =
    totalTrades ??
    resolvedWinningTrades + resolvedLosingTrades + resolvedBreakevenTrades;

  return {
    winningTrades: resolvedWinningTrades,
    losingTrades: resolvedLosingTrades,
    breakevenTrades: resolvedBreakevenTrades,
    totalTrades: resolvedTotalTrades,
  };
}
