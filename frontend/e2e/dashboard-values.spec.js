import { expect, test } from "@playwright/test";

test("dashboard rolls actual source digits without a highlight, shadow or numeric interpolation", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let pnl = 23.76;
  let stream;
  let version = 1;
  const snapshot = () => ({ status: "connected", generated_at: new Date().toISOString(), source_updated_at: Date.now() / 1000,
    summary: { active_accounts: 1, open_positions: 4, buy_lots: 1.24256789, sell_lots: .002,
      net_lots: 1.24056789, gross_lots: 1.24456789, high_lot_threshold: 1, large_positions: 1,
      flagged_accounts: 0, critical_accounts: 0, currency_totals: { USD: { accounts: 1, balance: 1000,
        equity: 1000 + (pnl || 0), floating_pnl: pnl, margin: 10, margin_free: 990 } } },
    accounts: [{ login:1, name:"fixture", group:"fixture", currency:"USD", balance:1000,
      equity:1000+(pnl||0), margin:10, margin_free:990+(pnl||0), floating_pnl:pnl, severity:"normal", signals:[] }],
    positions: [.001,.007,.002,1.23456789].map((volume,index) => ({ticket:String(index+1),login:1,
      group:"fixture",symbol:"TEST",action:index===2?"SELL":"BUY",volume,profit:index===0?pnl:0,
      price_open:100,price_current:101,currency:"USD"})), symbols: [], warnings: [], network: {} });
  const reply = (path) => {
    if (path.includes("/mt5/groups")) return { groups: ["fixture"] };
    if (path.includes("/settings/books")) return { a_book: ["fixture"], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    if (path.includes("/risk/live")) return snapshot();
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
    if (message.type === "subscribe" && message.topic === "live-risk") {
      stream = { socket, id: message.id };
      socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data: snapshot() }));
    }
  }));
  await page.routeWebSocket("**/api/operations/ws", () => {});
  await page.goto("/dealing-risk?tab=live");
  const card = (name) => page.locator(".risk-stat-card").filter({ has: page.getByText(name, { exact: true }) });
  const floating = card("Floating P&L");
  await expect(floating).toContainText("23.76");
  await expect(card("Buy Lots")).toContainText("1.24256789");
  await expect(card("Gross Lots")).toContainText("1.24456789");
  await expect(card("Sell Lots")).toContainText("0.002");
  await floating.locator(".observed-number").evaluate((element) => {
    window.displayedAmounts = [element.textContent];
    window.rollingEvidence = null;
    const observer = new MutationObserver(() => {
      window.displayedAmounts.push(element.textContent);
      if (element.dataset.observedValue === "9876.54" && !window.rollingEvidence) {
        const animations = document.getAnimations().filter((animation) => element.contains(animation.effect.target));
        window.rollingEvidence = { count: animations.length,
          outgoing: animations.filter((animation) => animation.effect.pseudoElement === "::before").length,
          durations: animations.map((animation) => animation.effect.getTiming().duration) };
      }
    });
    observer.observe(element, { childList: true, characterData: true, subtree: true });
  });
  pnl = 9876.54;
  stream.socket.send(JSON.stringify({ type: "snapshot", id: stream.id, version: ++version, data: snapshot() }));
  await expect(floating).toContainText("9,876.54");
  expect(await floating.locator(".observed-number").evaluate((element) => ({
    background: getComputedStyle(element).backgroundColor,
    shadow: getComputedStyle(element).boxShadow,
  }))).toEqual({background:"rgba(0, 0, 0, 0)",shadow:"none"});
  const rolling = await page.evaluate(() => window.rollingEvidence);
  expect(rolling.count).toBeGreaterThan(0);
  expect(rolling.outgoing).toBeGreaterThan(0);
  expect(rolling.durations.every((duration) => duration <= 160)).toBeTruthy();
  await page.waitForTimeout(280);
  expect(await page.evaluate(() => [...new Set(window.displayedAmounts)])).toEqual(["23.76", "9,876.54"]);
  expect(await floating.locator(".observed-number").evaluate((element) => element.getAnimations({subtree:true}).length)).toBe(0);
  for (const next of [9876.55, 9876.56, -8765.43]) {
    pnl = next;
    stream.socket.send(JSON.stringify({ type: "snapshot", id: stream.id, version: ++version, data: snapshot() }));
  }
  await expect(floating.locator(".observed-number")).toHaveAttribute("data-observed-value", "-8765.43");
  await expect(floating).toContainText("-8,765.43");
  await page.waitForTimeout(180);
  expect(await floating.locator(".observed-number").evaluate((element) => element.getAnimations({subtree:true}).length)).toBe(0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  pnl = 0;
  stream.socket.send(JSON.stringify({ type: "snapshot", id: stream.id, version: ++version, data: snapshot() }));
  await expect(floating).toContainText("0.00");
  expect(await floating.locator(".observed-number").evaluate((element) => element.getAnimations({subtree:true}).length)).toBe(0);
  pnl = null;
  stream.socket.send(JSON.stringify({ type: "snapshot", id: stream.id, version: ++version, data: snapshot() }));
  await expect(floating).toContainText("Unavailable");
  await expect(floating).not.toContainText("0.00");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("dashboard-exact-values.png") });
  expect(errors).toEqual([]);
});

test("Manager Overview shows currency-separated balances without inventing a monthly comparison", async ({ page }) => {
  const body = { status:"connected", last_update:Date.now()/1000, data_age_seconds:0,
    errors:[], metrics:{}, summary_rows:[], exposure_rows:[], users:[], totals:{ visible_accounts:2,
      enabled_accounts:2, open_positions:0, currency_totals:{
        USD:{ accounts:1, balance:100, equity:110, margin:5, margin_free:105, floating_pnl:10 },
        USC:{ accounts:1, balance:10000, equity:11000, margin:500, margin_free:10500, floating_pnl:1000 },
      } } };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id:1, username:"fixture", is_admin:true, must_change_pw:false })));
  const reply = (path) => path.includes("/manager/dashboard") ? body
    : path.includes("/manager/status") ? {status:"connected",last_update:Date.now()/1000} : {};
  await page.route("**/api/**", (route) => route.fulfill({ json:reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message=JSON.parse(String(raw));
    if(message.type==="request") socket.send(JSON.stringify({type:"response",id:message.id,status:200,data:reply(message.path)}));
    if(message.type==="subscribe" && message.topic==="dashboard") socket.send(JSON.stringify({type:"snapshot",id:message.id,version:1,data:body}));
  }));
  await page.routeWebSocket("**/api/operations/ws", () => {});
  await page.goto("/manager");
  const equity = page.getByText("Total Equity", {exact:true}).locator("../..");
  await expect(page.getByLabel("Manager money currency")).toHaveValue("USD");
  await expect(equity).toContainText("110.00 USD");
  await expect(equity).not.toContainText("11,110");
  await page.getByLabel("Manager money currency").selectOption("USC");
  await expect(equity).toContainText("11,000.00 USC");
  await expect(page.getByText("vs last month", {exact:true})).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
});
