import { expect, test } from "@playwright/test";

test("a temporary source interruption keeps verified values visibly delayed and recovers via WebSocket", async ({ page }) => {
  const streams = [];
  const stamp = Date.now() / 1000;
  let data = { status: "connected", generated_at: new Date().toISOString(), source_updated_at: stamp,
    summary: { currencies: ["USD"], equity: 1000, floating_pnl: 12, gross_lots: 1, buy_lots: .7, sell_lots: .3, net_lots: .4, open_positions: 1 },
    accounts: [], positions: [], symbols: [], network: {}, warnings: [], cache: { source: "manager" } };
  const reply = (path) => {
    if (path.includes("/mt5/groups")) return { groups: ["real"], count: 1 };
    if (path.includes("/settings/books")) return { a_book: [], b_book: ["real"], cent: [], hybrid: [] };
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
  await page.goto("/dealing-risk?group=real");
  const equity = page.locator(".risk-stat-card").filter({ has: page.getByText("Total Equity", { exact: true }) });
  await expect(equity).toContainText("1,000");
  await expect.poll(() => streams.length).toBeGreaterThan(0);
  data = { status: "offline", summary: {}, accounts: [], positions: [], symbols: [], network: {}, warnings: ["Source temporarily interrupted"] };
  for (const { socket, id } of streams) socket.send(JSON.stringify({ type: "snapshot", id, version: 2, data }));
  await expect(page.getByText(/Updates delayed · last verified/)).toBeVisible();
  await expect(equity).toContainText("1,000");
  await expect(equity).not.toContainText("Unavailable");
  data = { status: "connected", generated_at: new Date().toISOString(), source_updated_at: Date.now()/1000,
    summary: { currencies: ["USD"], equity: 1050, floating_pnl: 25 }, accounts: [], positions: [], symbols: [], network: {}, warnings: [] };
  for (const { socket, id } of streams) socket.send(JSON.stringify({ type: "snapshot", id, version: 3, data }));
  await expect(equity).toContainText("1,050");
  await expect(page.getByText(/Updates delayed · last verified/)).toHaveCount(0);
});
