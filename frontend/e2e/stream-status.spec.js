import { expect, test } from "@playwright/test";

for (const sourceConnected of [true, false]) {
  test(`delayed MT5 observations show ${sourceConnected ? "updating" : "synchronizing"} without claiming live data`, async ({ page }) => {
    const reply = (path) => {
      if (path.includes("/manager/status")) return {
        status: "warming", source_connected: sourceConnected, last_update: Date.now() / 1000 - 20,
        error: sourceConnected ? "MT5 stream connected; waiting for current account observations" : "MT5 synchronizing; waiting for current account data",
      };
      if (path.includes("/settings/books")) return { a_book: [], b_book: [], cent: [], hybrid: [] };
      if (path.includes("/notifications/news-push")) return {
        settings: { enabled: false, minimum_profit: 100, currency: "USD", message: "Account {login}" }, counts: {}, recent: [],
      };
      return {};
    };
    await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
    await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
    await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
    }));
    await page.goto("/settings");
    await expect(page.getByText(sourceConnected ? "Updating" : "Synchronizing", { exact: true })).toBeVisible();
    await expect(page.getByText("MT5 data status", { exact: false })).toBeVisible();
    await expect(page.getByText("Disconnected", { exact: true })).toHaveCount(0);
    await expect(page.getByText("MT5 offline", { exact: true })).toHaveCount(0);
    await expect(page.getByText("MT5 live", { exact: true })).toHaveCount(0);
    if (test.info().project.name === "desktop-edge") {
      await expect(page.getByText(sourceConnected ? "MT5 updating" : "MT5 synchronizing", { exact: true })).toBeVisible();
    }
  });
}

test("Controls recovers with the shared live status without resetting unsaved inputs", async ({ page }) => {
  let recovered = false;
  const reply = (path) => {
    if (path.includes("/manager/status")) return recovered
      ? { status: "connected", last_update: Date.now() / 1000 }
      : { status: "warming", source_connected: true, last_update: Date.now() / 1000 - 20,
        error: "MT5 stream connected; waiting for current account observations" };
    if (path.includes("/settings/books")) return { a_book: [], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/notifications/news-push")) return {
      settings: { enabled: false, minimum_profit: 100, currency: "USD", message: "Account {login}" }, counts: {}, recent: [],
    };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
  }));
  await page.goto("/settings");
  await expect(page.getByText("Updating", { exact: true })).toBeVisible();
  await page.getByLabel("Test push message").fill("Unsaved test message");
  recovered = true;
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByText("MT5 data status", { exact: false })).toHaveCount(0);
  await expect(page.getByLabel("Test push message")).toHaveValue("Unsaved test message");
});
