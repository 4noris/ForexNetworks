import { expect, test } from "@playwright/test";

async function mockReads(page, query) {
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  const reply = (path) => {
    if (path.includes("/mt5/groups")) return { groups: ["real"], count: 1 };
    if (path.includes("/settings/books")) return { a_book: ["real"], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    return {};
  };
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type !== "request") return;
    const send = (data) => socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data }));
    if (!query(message.path, send)) send(reply(message.path));
  }));
}

test("command header and refresh control stay the same size while refreshing", async ({ page }) => {
  let held;
  let count = 0;
  const data = { status: "connected", source_updated_at: Date.now() / 1000, selected_groups: ["real"], currency: "USD", kpis: { net_lots: -0.25 }, risk_events: [], symbol_exposure: [], top_risk_accounts: [], what_changed: [], warnings: [] };
  await mockReads(page, (path, send) => {
    if (!path.includes("/dealer-command-center")) return false;
    if (++count === 1) send(data); else held = send;
    return true;
  });
  await page.goto("/command-center");
  await expect(page.getByText("Net Lots", { exact: true })).toBeVisible();
  await expect(page.getByText("-0.25", { exact: true })).toBeVisible();
  const button = page.getByRole("button", { name: "Refresh", exact: true });
  await expect(button).toBeEnabled();
  const before = await button.boundingBox();
  const section = page.getByRole("heading", { name: "Dealer Command Center" }).locator("xpath=ancestor::section");
  const header = await section.boundingBox();
  await button.click();
  const busy = page.getByRole("button", { name: "Refreshing...", exact: true });
  await expect(busy).toBeDisabled();
  const after = await busy.boundingBox();
  const updated = await section.boundingBox();
  expect(after.width).toBe(before.width);
  expect(after.height).toBe(before.height);
  expect(updated.height).toBe(header.height);
  expect(updated.y).toBe(header.y);
  await expect.poll(() => Boolean(held)).toBe(true);
  held(data);
  await expect(button).toBeEnabled();
});

test("book history keeps polling with unchanged timestamps and displays live balances", async ({ page }) => {
  let calls = 0;
  await mockReads(page, (path, send) => {
    if (!path.startsWith("/api/book-pnl?")) return false;
    calls++;
    send({ daily: [], intraday: [], totals: {}, live: { balance: 100, equity: 127, floating: 11 }, configured_groups: 1, backfill: { refreshing: true, message: "Loading historical daily data" }, coverage: { available_days: 0, expected_days: 7, complete: false }, last_updated: null });
    return true;
  });
  await page.goto("/bbook-pnl");
  await expect(page.getByText("Live equity", { exact: true })).toBeVisible();
  await expect(page.getByText("$127.00", { exact: true })).toBeVisible();
  await expect.poll(() => calls, { timeout: 15000 }).toBeGreaterThanOrEqual(3);
  await expect(page.getByText("Loading book history...", { exact: true })).toHaveCount(0);
  await expect(page.getByText("No book history for this range.", { exact: true })).toBeVisible();
});
