import { expect, test } from "@playwright/test";

test("news repeat badge appears once and redundant branding and cache labels are absent", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const stamp = Date.now() / 1000;
  const event = { event_id: "news-incident-1", rule: "news_trading", detector: "news_trading", login: 7, name: "News account", position_id: 77, symbol: "EURUSD", group: "real", severity: "high", data_quality: "VERIFIED", timestamp: new Date().toISOString(), evidence: "Verified calendar match", triggering_values: {}, thresholds: {}, news_flag: "News trader 3x", news_incident_count: 3 };
  const reply = (target) => {
    const path = new URL(target, "http://fixture").pathname;
    if (path.includes("/mt5/groups")) return { groups: ["real"], count: 1 };
    if (path.includes("/settings/books")) return { a_book: ["real"], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: stamp };
    if (path === "/api/risk/antifraud") return { status: "connected", generated_at: new Date((stamp - 10) * 1000).toISOString(), summary: { deals_scanned: 2, events: 1 }, events: [event, event], detectors: [], client_summaries: [], warnings: [], cache: { hit: true, stale: true, refreshing: true, age_seconds: 10 } };
    if (path === "/api/risk/live") return { status: "connected", generated_at: new Date().toISOString(), source_updated_at: stamp, summary: {}, positions: [], accounts: [], symbols: [], warnings: [], network: {} };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(route.request().url()) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
  }));
  await page.goto("/dealing-risk?group=real&tab=antifraud");
  await page.getByRole("button", { name: "Anti-Fraud", exact: true }).click();
  await expect(page.getByText("News trader 3x", { exact: true })).toHaveCount(1);
  await expect(page.locator('img[src="/brand-logo.png"]')).toHaveCount(0);
  await expect(page.getByText("Server cache", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Refreshing ·/)).toHaveCount(0);
  await expect(page.getByText(/Last completed scan/)).toBeVisible();
  expect(errors).toEqual([]);
});
