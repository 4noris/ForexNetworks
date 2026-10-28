import { expect, test } from "@playwright/test";

test("Markout adjusts to seven inclusive UTC dates and keeps unverified dollars unavailable", async ({ page }, testInfo) => {
  const errors = [];
  let currentDayStream;
  const today = new Date().toISOString().slice(0, 10);
  page.on("pageerror", (error) => errors.push(error.message));
  const now = Date.now() / 1000;
  const summary = Object.fromEntries([1, 5, 10, 30, 60, 120, 300, 600, 900].map((horizon) => [String(horizon), {
    covered: 0, total: 0, coverage_pct: 0, positive_share_pct: null, average_client_mid_bps: null,
    median_client_mid_bps: null, volume_weighted_client_mid_bps: null,
  }]));
  const reply = (path) => {
    if (path === "/api/risk/markout") return { source: "Browser test fixture", generated_at: new Date().toISOString(), executions: [], summary, warnings: [], diagnostics: { latest_execution_date: "2026-10-06", recent_lookup_status: "complete" }, dashboard: {
      trades: 0, volume_usd: null, dpm: null, total_attribution_usd: null, internalised_pct: null, residual_usd: null,
      curves: [], cumulative: [], currency: null, unavailable_reason: "Hedge allocation records unavailable.", monetary_method: "No fabricated figures.",
    } };
    if (path.includes("/mt5/groups")) return { groups: ["real"], count: 1 };
    if (path.includes("/settings/books")) return { a_book: [], b_book: ["real"], cent: [], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: now };
    if (path.includes("/risk/live")) return { status: "connected", source_updated_at: now, accounts: [], positions: [], symbols: [], warnings: [], summary: {}, network: { map_points: [], providers: [], countries: [], shared_clusters: [] } };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "subscribe" && message.topic === "markout") {
      if (message.params.to_date === today) currentDayStream = { socket, id: message.id };
      socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data: reply("/api/risk/markout") }));
    }
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
  }));
  await page.goto("/dealing-risk?tab=markout&group=real");
  await page.getByRole("spinbutton", { name: "Client login" }).fill("900102");
  const from = page.getByLabel("From UTC", { exact: true });
  const to = page.getByLabel("To UTC", { exact: true });
  await from.fill("2026-10-01");
  await to.fill("2026-10-08");
  await expect(from).toHaveValue("2026-10-02");
  await expect(to).toHaveValue("2026-10-08");
  await expect(page.getByRole("button", { name: "Calculate", exact: true })).toBeEnabled();
  await to.fill("2026-10-07");
  await expect(page.getByRole("button", { name: "Calculate", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Calculate", exact: true }).click();
  await expect(page.getByRole("heading", { name: "No executed trades for these dates" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Execution markout", exact: true }).getByText("Unavailable", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Select latest trading day", exact: true }).click();
  await expect(from).toHaveValue("2026-10-06");
  await expect(to).toHaveValue("2026-10-06");
  await page.getByRole("button", { name: "Calculate", exact: true }).click();
  await expect(page.getByText("Fetched from MT5:", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Today (UTC)", exact: true }).click();
  await page.getByRole("button", { name: "Calculate", exact: true }).click();
  await expect(page.getByText("Fetched from MT5:", { exact: false })).toBeVisible();
  await expect.poll(() => Boolean(currentDayStream)).toBe(true);
  const refreshed = `${today}T09:12:34.000Z`;
  const incoming = reply("/api/risk/markout");
  incoming.executions = [{ deal: 1, time_utc: `${today}T09:00:00+00:00`, symbol: "TEST", side: "BUY",
    horizons: Object.fromEntries(Object.keys(summary).map((key) => [key, { status: "missing_future_quote", client_mid_bps: null }])) }];
  incoming.dashboard.trades = 1;
  incoming.summary = Object.fromEntries(Object.entries(summary).map(([key, value]) => [key, { ...value, total: 1 }]));
  await expect.poll(async () => {
    currentDayStream.socket.send(JSON.stringify({ type: "snapshot", id: currentDayStream.id, version: 2,
      data: { ...incoming, generated_at: refreshed } }));
    return page.getByText("Fetched from MT5:", { exact: false }).textContent();
  }).toContain(refreshed);
  await expect(page.getByText("Total attribution", { exact: true })).toBeVisible();
  await expect(page.getByText("Hedge feed required", { exact: true })).toHaveCount(3);
  await expect(page.getByRole("heading", { name: "No executed trades for these dates" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("markout.png"), fullPage: true });
});
