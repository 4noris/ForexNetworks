import { appendPnlSample } from "./pnlMovement.js";

export function liveMoneyView(summary = {}, preferred = "USD") {
  if (summary.scoped_accounts === 0 && ["balance", "equity", "margin", "margin_free", "floating_pnl"].every((key) => summary[key] === 0)) {
    return { currency: "", available: [], totals: summary, valid: true };
  }
  const totals = summary.currency_totals || {};
  const available = Object.keys(totals).filter((key) => key !== "UNKNOWN").sort();
  // Older single-currency snapshots remain compatible during rollout/recovery.
  if (!available.length && summary.currencies?.length === 1 && summary.currencies[0] !== "UNKNOWN") {
    const currency = summary.currencies[0];
    return { currency, available: [currency], totals: summary, valid: !Number(summary.estimated_accounts || 0) };
  }
  const currency = available.includes(preferred) ? preferred
    : available.includes("USD") ? "USD" : available[0] || "";
  return { currency, available, totals: totals[currency] || {}, valid: Boolean(currency) && !Number(summary.estimated_accounts || 0) };
}

export function appendLiveMoneySample(samples, payload) {
  const summary = payload?.summary || {};
  const { available } = liveMoneyView(summary);
  const currencySamples = {};
  for (const currency of available) {
    const view = liveMoneyView(summary, currency);
    if (!view.valid) continue;
    const sample = appendPnlSample([], { ...payload, summary: view.totals })[0];
    if (sample) currencySamples[currency] = sample;
  }
  const sample = Object.values(currencySamples)[0];
  if (!sample || (samples.at(-1)?.sourceMs ?? 0) >= sample.sourceMs) return samples;
  return [...samples, { ...sample, currencySamples }].slice(-60);
}

export function currencyMovement(samples, currency) {
  return samples.flatMap((sample) => sample.currencySamples?.[currency] ? [sample.currencySamples[currency]] : []);
}
