export function appendPnlSample(samples, payload, limit = 60) {
  const source = payload?.source_updated_at;
  const sourceMs =
    typeof source === "number" ? source * 1000 : Date.parse(source || "");
  const pnl = Number(payload?.summary?.floating_pnl);
  const equity = Number(payload?.summary?.equity);
  if (
    !Number.isFinite(sourceMs) ||
    payload?.summary?.floating_pnl == null ||
    payload?.summary?.equity == null ||
    sourceMs <= 0 ||
    !Number.isFinite(pnl) ||
    !Number.isFinite(equity) ||
    payload?.status === "warming" ||
    payload?.status === "disconnected" ||
    Number(payload?.data_age_seconds || 0) > 30
  ) {
    return samples;
  }

  const previous = samples.at(-1);
  if (previous && sourceMs <= previous.sourceMs) return samples;
  const timestamp = new Date(sourceMs);
  return [
    ...samples,
    {
      sourceMs,
      time: timestamp.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
      pnl,
      equity,
      symbolPnl: Object.fromEntries((payload?.symbols || [])
        .filter((row) => row.floating_pnl != null && Number.isFinite(Number(row.floating_pnl)))
        .map((row) => [row.symbol, Number(row.floating_pnl)])),
    },
  ].slice(-limit);
}

export function observedSymbolDelta(samples, symbol) {
  const previous = samples.at(-2)?.symbolPnl?.[symbol];
  const current = samples.at(-1)?.symbolPnl?.[symbol];
  return previous == null || current == null ? null : current - previous;
}
