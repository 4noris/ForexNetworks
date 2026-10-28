import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixture = (params) => JSON.parse(execFileSync(path.join(root, ".venv", "Scripts", "python.exe"),
  [path.join(root, "frontend", "e2e", "fixtures", "exposure_book.py")], {
    input: JSON.stringify({ params }), encoding: "utf8", windowsHide: true, timeout: 10000,
  }));

test("Exposure applies a pushed delta without another read and keeps one active realized subscription", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let live;
  let exposureReads = 0;
  const performanceSubscriptions = new Set();
  const reply = (target, body = {}) => {
    if (target.includes("/mt5/groups")) return { groups: ["Fixture A"], count: 1 };
    if (target.includes("/settings/books")) return { a_book: ["Fixture A"], b_book: [], cent: [], hybrid: [] };
    if (target.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    if (target.includes("/risk/exposure")) { exposureReads++; return fixture(body); }
    if (target.includes("/risk/performance")) return { status: "warming", periods: {}, cache: { refreshing: true } };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => route.fulfill({ json: reply(new URL(route.request().url()).pathname, route.request().postDataJSON() || {}) }));
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw));
    if (message.type === "subscribe" && message.topic === "exposure") {
      live = { socket, id: message.id };
      socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data: fixture(message.params) }));
    }
    if (message.type === "subscribe" && message.topic === "performance") {
      performanceSubscriptions.add(message.id);
      socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data: reply("/api/risk/performance") }));
    }
    if (message.type === "unsubscribe") performanceSubscriptions.delete(message.id);
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path, message.body) }));
  }));
  await page.goto("/dealing-risk?tab=exposure&group=Fixture%20A");
  const panel = page.getByRole("heading", { name: "Exposure by symbol", exact: true }).locator("../..");
  const floating = panel.locator("tbody tr").filter({ hasText: "XAUUSD" }).locator("td").nth(7);
  await expect(floating).toHaveText("14");
  const before = exposureReads;
  live.socket.send(JSON.stringify({ type: "delta", id: live.id, base: 1, version: 2,
    changes: [{ path: ["symbols", 0, "floating_pnl"], value: 17 }] }));
  await expect(floating).toHaveText("17");
  expect(exposureReads).toBe(before);
  await page.locator("summary").filter({ hasText: "Turnover & Closed P&L" }).click();
  await expect.poll(() => performanceSubscriptions.size).toBe(1);
  await page.getByRole("button", { name: "P&L Leaders", exact: true }).click();
  await expect(page.getByRole("heading", { name: "P&L Winners & Losers" })).toBeVisible();
  expect(performanceSubscriptions.size).toBe(1);
  expect(errors).toEqual([]);
});
