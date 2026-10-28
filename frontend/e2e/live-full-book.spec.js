import { expect, test } from "@playwright/test";

test("the entire live book remains searchable, scrollable and updated beyond row 500", async ({ page }) => {
  const errors = [];
  let liveRpcReads = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  const baseTicket = 18446744073709500000n;
  const accounts = Array.from({ length: 579 }, (_, index) => ({ login: 200001 + index, name: `Fixture ${index + 1}`,
    group: "fixture", currency: "USD", equity: 110, balance: 100, margin: 5, margin_free: 105,
    floating_pnl: 10, margin_level: 2200, positions: 8, gross_lots: .08, severity: "normal", risk_score: 0, signals: [] }));
  const positions = Array.from({ length: 4817 }, (_, index) => ({ ticket: String(baseTicket + BigInt(index)),
    login: 200001 + index % 579, name: `Fixture ${1 + index % 579}`, group: "fixture", symbol: "TEST_ONLY",
    action: index % 2 ? "SELL" : "BUY", volume: .01, price_open: 100, price_current: 101, profit: index / 100, opened_at: "" }));
  let lastPrice = 101;
  const snapshot = () => ({ generated_at: new Date().toISOString(), status: "connected", source_updated_at: Date.now() / 1000,
    accounts, positions: positions.map((row, index) => index === 4816 ? { ...row, price_current: lastPrice } : row),
    symbols: [{ symbol: "TEST_ONLY", gross_lots: 48.17, buy_lots: 24.09, sell_lots: 24.08, net_lots: .01, positions: 4817, floating_pnl: 10 }],
    large_positions: [], warnings: [], network: { map_points: [], countries: [], providers: [], shared_clusters: [] },
    summary: { active_accounts: 579, open_positions: 4817, gross_lots: 48.17, net_lots: .01, flagged_accounts: 0,
      high_lot_threshold: 1, currency_totals: { USD: { accounts: 579, equity: 63690, balance: 57900, margin: 2895, margin_free: 60795, floating_pnl: 5790 } } } });
  const response = (path) => {
    if (path.includes("/mt5/groups")) return { groups: ["fixture"] };
    if (path.includes("/settings/books")) return { a_book: ["fixture"], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/risk/live")) return snapshot();
    if (path.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: response(new URL(route.request().url()).pathname) }));
  let live;
  let subscriptions = 0;
  let rejectStale = false;
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request" && message.path === "/api/risk/live") liveRpcReads += 1;
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: response(message.path) }));
    if (message.type === "subscribe" && message.topic === "live-risk") {
      subscriptions++;
      live = { socket, id: message.id };
      const data = snapshot();
      if (rejectStale) data.positions[4816].price_current = 765.43;
      socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data }));
    }
  }));
  await page.routeWebSocket("**/api/operations/ws", () => {});
  await page.goto("/dealing-risk?tab=live");
  const book = page.getByRole("region", { name: "All live accounts and positions" });
  await expect(book.getByRole("button", { name: "Accounts (579)", exact: true })).toBeVisible();
  await expect(book.getByRole("button", { name: "Positions (4,817)", exact: true })).toBeVisible();
  await expect(page.getByTestId("live-scope-counts")).toContainText("579 accounts");
  expect(await book.getByTestId("live-book-row").count()).toBeLessThanOrEqual(24);
  await page.waitForTimeout(1700);
  expect(liveRpcReads).toBe(0);
  const search = book.getByLabel("Search all live records");
  await search.fill("200579");
  await expect(book.getByTestId("live-book-row")).toHaveCount(1);
  await expect(book.getByTestId("live-book-row")).toContainText("Fixture 579");
  await book.getByRole("button", { name: "Positions (4,817)", exact: true }).click();
  const scroll = book.getByTestId("live-book-scroll");
  await scroll.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const lastTicket = String(baseTicket + 4816n);
  await expect(book.getByTestId("live-book-row").last()).toContainText(lastTicket);
  expect(await book.getByTestId("live-book-row").count()).toBeLessThanOrEqual(24);
  await search.fill(lastTicket);
  await expect(book.getByTestId("live-book-row")).toHaveCount(1);
  await expect(book.getByTestId("live-book-row")).toContainText("101");
  await expect.poll(() => Boolean(live)).toBe(true);
  lastPrice = 987.25;
  live.socket.send(JSON.stringify({ type: "snapshot", id: live.id, version: 2, data: snapshot() }));
  await expect(book.getByTestId("live-book-row")).toContainText("987.25");
  // A stale view can be stuck on an otherwise healthy multiplexed socket.
  // Keep global heartbeats flowing and require automatic per-view recovery.
  const stale = snapshot();
  stale.source_updated_at -= 60;
  stale.positions[4816].price_current = 111.11;
  rejectStale = true;
  const beforeRepair = subscriptions;
  live.socket.send(JSON.stringify({ type: "snapshot", id: live.id, version: 3, data: stale }));
  const heartbeat = setInterval(() => live.socket.send(JSON.stringify({ type: "heartbeat" })), 200);
  try {
    await expect(book.getByTestId("live-book-row")).not.toContainText("111.11");
    await expect.poll(() => subscriptions, { timeout: 6500 }).toBeGreaterThan(beforeRepair);
    await expect(book.getByTestId("live-book-row")).toContainText("765.43");
  } finally {
    clearInterval(heartbeat);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(errors).toEqual([]);
});
