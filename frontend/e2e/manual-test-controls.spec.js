import { expect, test } from "@playwright/test";

test("test push targets a typed MT5 login with auto alerts off and reuses its request after a timeout", async ({ page }) => {
  const attempts = [];
  const settings = { enabled: false, minimum_profit: 100, currency: "USD", message: "Account {login}: {profit} {currency}" };
  const reply = (path) => {
    if (path.includes("/notifications/news-push")) return { settings, counts: {}, recent: [], last_test: null };
    if (path.includes("/settings/books")) return { a_book: [], b_book: [], cent: [], hybrid: [] };
    if (path.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/notifications/news-push/test" && route.request().method() === "POST") {
      const body = route.request().postDataJSON(); attempts.push(body);
      if (attempts.length === 1) return route.abort("failed");
      return route.fulfill({ json: { login: body.login, request_id: body.request_id, status: "accepted", detail: "Test accepted by MT5. Check the account's mobile device." } });
    }
    return route.fulfill({ json: reply(path) });
  });
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path) }));
  }));
  await page.goto("/settings");
  const auto = page.getByRole("switch", { name: "Send push notifications to news traders" });
  await expect(auto).not.toBeChecked();
  await page.getByLabel("Test push MT5 login").fill("9007199254740993");
  await page.getByLabel("Test push message").fill("RMS test message only.");
  const form = page.getByRole("form", { name: "Send test MT5 push notification" });
  const send = form.getByRole("button", { name: "Send test notification", exact: true });
  await send.click();
  await expect(form.getByRole("status")).toContainText("unknown");
  await expect(send).toBeEnabled();
  await send.click();
  await expect(form.getByRole("status")).toContainText("accepted");
  expect(attempts).toHaveLength(2);
  expect(attempts[0].request_id).toBe(attempts[1].request_id);
  expect(attempts[0].login).toBe("9007199254740993");
  expect(attempts[0].message).toBe("RMS test message only.");
  await expect(auto).not.toBeChecked();
});

test("retired Risk Analytics links open Risk Center instead of an unavailable analytics page", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: {} }));
  }));
  await page.goto("/dealing-risk/analytics");
  await expect(page).toHaveURL(/\/risk-center$/);
  await expect(page.getByRole("link", { name: "Risk Analytics", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Risk Analytics", exact: true })).toHaveCount(0);
});
