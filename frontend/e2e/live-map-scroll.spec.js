import { expect, test } from "@playwright/test";

async function liveFixture(page) {
  const stamp = Date.now() / 1000;
  const positions = Array.from({ length: 100 }, (_, index) => ({ ticket: index + 1, position_id: index + 1, login: 7, name: "Fixture account", group: "real", symbol: "EURUSD", action: index < 40 ? "BUY" : "SELL", volume: .01, profit: -1, price_open: 1.1, price_current: 1.09, age_seconds: 60, opened_at: new Date((stamp - 60) * 1000).toISOString() }));
  const risk = { status: "connected", generated_at: new Date().toISOString(), source_updated_at: stamp,
    summary: { currencies: ["USD"], gross_lots: 1, buy_lots: .4, sell_lots: .6, net_lots: -.2, open_positions: 100, located_ips: 4 },
    accounts: [{ login: 7, name: "Fixture account", group: "real", balance: 1000, equity: 900, floating_pnl: -100, severity: "normal" }], positions, symbols: [], warnings: [],
    network: { providers: [], countries: [], shared_clusters: [], map_points: [
      { ip: "198.51.100.1", latitude: 19.07, longitude: 72.87, accounts: 1, location: "Mumbai, India", provider: "Fixture ISP", vps_status: "not_detected" },
      { ip: "198.51.100.2", latitude: 0, longitude: -179.9, accounts: 1, vps_status: "detected" },
      { ip: "198.51.100.3", latitude: 0, longitude: 179.9, accounts: 1, vps_status: "unknown" },
      { ip: "198.51.100.4", latitude: 0, longitude: 0, accounts: 1, vps_status: "not_detected" },
      { ip: "198.51.100.5", latitude: null, longitude: null, accounts: 1 },
      { ip: "198.51.100.6", latitude: 91, longitude: 300, accounts: 1 },
    ] },
  };
  const reply = (path) => {
    if (path.includes("/mt5/groups")) return { groups: ["real"], count: 1 };
    if (path.includes("/settings/books")) return { a_book: ["real"], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: stamp };
    if (path.includes("/risk/live")) return risk;
    if (path.includes("/notifications/news-push")) return { settings: { enabled: false, minimum_profit: 100, currency: "USD", message: "Account {login}" }, recent: [], counts: {} };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
    if (message.type === "subscribe" && message.topic === "live-risk") socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data: risk }));
  }));
}

test("Live Monitor net lots and full-world map support valid locations and zoom", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (cause) => errors.push(cause.message));
  await liveFixture(page);
  await page.goto("/dealing-risk?group=real");
  const net = page.locator(".risk-stat-card").filter({ has: page.getByText("Net Lots", { exact: true }) });
  await expect(net).toContainText("-0.2");
  const map = page.getByRole("group", { name: "Client IP locations map" });
  await expect(map).toBeVisible();
  const cardBox = await page.locator(".network-map-card").boundingBox();
  const frameBox = await page.locator(".network-map-frame").boundingBox();
  expect(Math.abs(frameBox.y + frameBox.height - (cardBox.y + cardBox.height - 20))).toBeLessThan(2);
  await expect(page.getByRole("link", { name: "Risk Analytics", exact: true })).toHaveCount(0);
  await expect(map.locator("[data-location]")).toHaveCount(4);
  await expect(map).toHaveAttribute("preserveAspectRatio", "xMidYMid meet");
  for (const marker of await map.locator("[data-location]").all()) {
    const rect = await marker.boundingBox(), bounds = await map.boundingBox();
    expect(rect.x).toBeGreaterThanOrEqual(bounds.x - 1);
    expect(rect.x + rect.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
  }
  await map.locator('[data-location="198.51.100.1"]').focus();
  await expect(map.locator("..").getByRole("status")).toContainText("Mumbai, India");
  await page.getByRole("button", { name: "Zoom in map" }).click();
  await expect(map.locator(":scope > g")).toHaveAttribute("transform", /scale\(2\)/);
  await page.getByRole("button", { name: "Reset map view" }).click();
  await expect(map.locator(":scope > g")).toHaveAttribute("transform", "translate(0 0) scale(1)");
  await page.locator(".network-map-card").screenshot({ path: testInfo.outputPath("network-map.png") });
  expect(errors).toEqual([]);
});

test("hidden scrollbars preserve nested table and main keyboard scrolling throughout the UI", async ({ page }) => {
  await liveFixture(page);
  await page.goto("/dealing-risk?group=real");
  const card = page.getByRole("region", { name: "All live accounts and positions" });
  await card.getByRole("button", { name: "Positions (100)", exact: true }).click();
  const tableScroll = card.getByTestId("live-book-scroll");
  await expect(tableScroll).toBeVisible();
  const state = await tableScroll.evaluate((element) => {
    element.scrollTop = 200; element.scrollLeft = 100;
    return { y: element.scrollTop, x: element.scrollLeft, width: getComputedStyle(element).scrollbarWidth };
  });
  expect(state.y).toBeGreaterThan(0);
  expect(state.x).toBeGreaterThan(0);
  expect(state.width).toBe("none");
  await page.goto("/settings");
  const textarea = page.getByLabel("News-trading client message");
  await textarea.fill(Array.from({ length: 60 }, (_, i) => `Line ${i}`).join("\n"));
  const textState = await textarea.evaluate((element) => { element.scrollTop = 120; return { y: element.scrollTop, width: getComputedStyle(element).scrollbarWidth }; });
  expect(textState.y).toBeGreaterThan(0);
  expect(textState.width).toBe("none");
  const main = page.getByRole("main", { name: "Main content" });
  await main.evaluate((element) => { element.scrollTop = 0; });
  await main.focus();
  await page.keyboard.press("PageDown");
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});
