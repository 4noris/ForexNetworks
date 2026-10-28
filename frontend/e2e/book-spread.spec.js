import { expect, test } from "@playwright/test";

async function setup(page) {
  const scopes = [];
  const writes = [];
  const spread = { enabled: false, default_threshold_points: null, symbol_thresholds: {}, cooldown_seconds: 300 };
  const now = Date.now() / 1000;
  const reply = (target, body) => {
    const path = new URL(target, "http://fixture").pathname;
    if (path === "/api/dealer-command-center") {
      scopes.push(body.selected_groups);
      return { status: "connected", selected_groups: body.selected_groups, data_age_seconds: 0, kpis: {}, symbols: [], accounts: [] };
    }
    if (path === "/api/mt5/groups") return { groups: ["real\\A1", "real\\A2", "real\\B1"], default_group: "real\\B1" };
    if (path === "/api/settings/books") return { a_book: ["real\\A1", "real\\A2"], b_book: ["real\\B1"], cent: [], hybrid: [] };
    if (path === "/api/manager/status") return { status: "connected", last_update: now };
    if (path === "/api/notifications/spread/symbols") return { symbols: [{ symbol: "XAUUSD.x", description: "Gold", digits: 2 }], total: 1 };
    if (path === "/api/notifications/spread/settings") return { settings: { ...spread }, last_alert: null, telegram_enabled: false };
    if (path === "/api/notifications/telegram/status") return { bot_token: false, chat_id: false, settings: { enabled: false } };
    if (path === "/api/notifications/news-push") return { settings: { enabled: false, minimum_profit: 100, currency: "USD", message: "Test fixture" }, tests: [], recent: [] };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const body = req.postData() ? req.postDataJSON() : undefined;
    if (req.method() === "PUT" && new URL(req.url()).pathname === "/api/notifications/spread/settings") {
      writes.push(body);
      Object.assign(spread, body);
    }
    await route.fulfill({ json: reply(req.url(), body) });
  });
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path, message.body) }));
  }));
  await page.routeWebSocket("**/api/operations/ws", () => {});
  return { scopes, writes };
}

test("Command Center selects all configured A Book groups again on page reload", async ({ page }) => {
  const { scopes } = await setup(page);
  await page.goto("/command-center");
  await expect.poll(() => scopes.at(-1)).toEqual(["real\\A1", "real\\A2"]);
  await page.getByRole("button", { name: /Live Groups/ }).click();
  await expect(page.getByLabel("Book preset", { exact: true })).toHaveValue("a_book");
  await page.getByLabel("Book preset", { exact: true }).selectOption("b_book");
  await expect.poll(() => scopes.at(-1)).toEqual(["real\\B1"]);
  const count = scopes.length;
  await page.reload();
  await expect.poll(() => scopes.length).toBeGreaterThan(count);
  await expect.poll(() => scopes.at(-1)).toEqual(["real\\A1", "real\\A2"]);
});

test("Controls saves point thresholds and exact-symbol overrides without sending a test alert", async ({ page }) => {
  const { writes } = await setup(page);
  await page.goto("/settings#spread-alerts");
  await page.getByLabel("Enable automatic spread alerts").check();
  await page.getByLabel("Default threshold (points)", { exact: true }).fill("30");
  await page.getByLabel("Alert cooldown per symbol (seconds)").fill("60");
  await page.getByLabel("Search MT5 symbols", { exact: true }).fill("xau");
  await page.getByRole("button", { name: "Add XAUUSD.x", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add XAUUSD.x", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Spread symbol 1", { exact: true })).toHaveValue("XAUUSD.x");
  await page.getByLabel("Spread threshold 1", { exact: true }).fill("45");
  await page.getByRole("button", { name: "Save spread alerts", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({ enabled: true, default_threshold_points: 30, symbol_thresholds: { "XAUUSD.x": 45 }, cooldown_seconds: 60 });
  await expect(page.getByText("Spread alert settings saved", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
