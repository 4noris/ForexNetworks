import { expect, test } from "@playwright/test";

test("Controls saves and disables news pushes without sending a client message", async ({ page }) => {
  const changes = [];
  let config = { enabled: false, minimum_profit: 100, currency: "USD", message: "Account {login}: {profit} {currency}; news matches {news_count}." };
  const reply = (path) => {
    if (path.includes("/notifications/news-push")) return { settings: config, counts: {}, recent: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    if (path.includes("/notifications/telegram/status")) return { settings: { enabled: false } };
    if (path.includes("/settings/books")) return { a_book: [], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/mt5/groups")) return { groups: ["real"], count: 1 };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/notifications/news-push" && route.request().method() === "PUT") {
      config = route.request().postDataJSON(); changes.push(config);
    }
    return route.fulfill({ json: reply(path) });
  });
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
  }));
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "News-trading push notifications" });
  const toggle = page.getByRole("switch", { name: "Send push notifications to news traders" });
  await expect(toggle).not.toBeChecked();
  await page.getByLabel("Minimum news-trade profit").fill("500");
  await page.getByLabel("News-trading client message").fill("Account {login}: profit {profit} {currency}; news matches {news_count}.");
  await toggle.check();
  await panel.getByRole("button", { name: "Save push settings", exact: true }).click();
  await expect.poll(() => changes.length).toBe(1);
  await expect(panel.getByText("Enabled", { exact: true })).toBeVisible();
  expect(changes[0].minimum_profit).toBe(500);
  expect(changes[0].enabled).toBe(true);
  await toggle.uncheck();
  await panel.getByRole("button", { name: "Save push settings", exact: true }).click();
  await expect.poll(() => changes.length).toBe(2);
  expect(changes[1].enabled).toBe(false);
  await expect(panel.getByText("Disabled", { exact: true })).toBeVisible();
});
