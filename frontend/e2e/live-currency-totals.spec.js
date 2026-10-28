import { expect, test } from "@playwright/test";

test("all groups keeps real USD and cent money visible and updates the selected currency over WebSocket", async ({ page }) => {
  const streams = [];
  const money = (equity, margin, floating) => ({ accounts: 1, balance: equity - floating, equity, margin, margin_free: equity - margin, floating_pnl: floating });
  let data = { status: "connected", generated_at: new Date().toISOString(), source_updated_at: Date.now() / 1000,
    summary: { currencies: ["USD", "USC"], currency_totals: { USD: money(1000, 100, 25), USC: money(100000, 1000, 2500) },
      scoped_accounts: 2, equity: 101000, margin: 1100, floating_pnl: 2525, open_positions: 2, gross_lots: 2, net_lots: 0, buy_lots: 1, sell_lots: 1, large_positions: 0, high_lot_threshold: 1, flagged_accounts: 0, critical_accounts: 0 },
    accounts: [], positions: [], symbols: [], network: {}, warnings: [], cache: { source: "manager" } };
  const reply = (path) => {
    if (path.includes("/mt5/groups")) return { groups: ["real-usd", "cent-usc"], count: 2 };
    if (path.includes("/settings/books")) return { a_book: [], b_book: ["real-usd"], cent: ["cent-usc"], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    if (path.includes("/risk/live")) return data;
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
    if (message.type === "subscribe" && message.topic === "live-risk") {
      streams.push({ socket, id: message.id });
      socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data }));
    }
  }));
  await page.goto("/dealing-risk?group=real-usd");
  await page.getByText("Groups", { exact: true }).click();
  await page.getByRole("button", { name: "Select all", exact: true }).click();
  await page.keyboard.press("Escape");
  const card = (label) => page.locator(".risk-stat-card").filter({ has: page.getByText(label, { exact: true }) });
  await expect(page.getByLabel("Money currency")).toHaveValue("USD");
  await expect(card("Total Equity")).toContainText("1,000");
  await expect(card("Floating P&L")).toContainText("25");
  await expect(card("Margin Level %")).toContainText("1,000");
  await expect(card("Open Positions")).toContainText("2");
  await expect(page.getByText("Waiting for a fresh MT5 P&L snapshot.")).toHaveCount(0);
  await page.getByLabel("Money currency").selectOption("USC");
  await expect(card("Total Equity")).toContainText("100,000");
  await expect(card("Margin Level %")).toContainText("10,000");
  await expect(card("Open Positions")).toContainText("2");
  data = { ...data, source_updated_at: Date.now() / 1000 + 1,
    summary: { ...data.summary, currency_totals: { ...data.summary.currency_totals, USC: money(100500, 1000, 3000) } } };
  for (const { socket, id } of streams) socket.send(JSON.stringify({ type: "snapshot", id, version: 2, data }));
  await expect(card("Total Equity")).toContainText("100,500");
  await expect(card("Floating P&L")).toContainText("3,000");
  await expect(page.locator(".risk-stat-card").filter({ hasText: "Unavailable" })).toHaveCount(0);
});
