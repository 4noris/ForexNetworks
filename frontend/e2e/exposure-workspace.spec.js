import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const python = path.join(root, ".venv", "Scripts", "python.exe");
const fixture = path.join(root, "frontend", "e2e", "fixtures", "exposure_book.py");
const compute = (params, tick = 0, rules, multiSymbols = false) => JSON.parse(execFileSync(python, [fixture], {
  input: JSON.stringify({ params, tick, rules, multi_symbols: multiSymbols }), encoding: "utf8", windowsHide: true, timeout: 10000,
}));

async function setup(page, { multiSymbols = false, silentStream = false } = {}) {
  const streams = [];
  const requests = [];
  const writes = [];
  let rules;
  const reply = (target, body = {}) => {
    if (target.includes("/mt5/groups")) return { groups: ["Fixture A", "Fixture B"], count: 2 };
    if (target.includes("/settings/books")) return { a_book: [], b_book: ["Fixture A"], cent: [], hybrid: [] };
    if (target.includes("/manager/status")) return { status: "connected", last_update: Date.now() / 1000 };
    if (target.includes("/risk/exposure")) return compute(body, 0, rules, multiSymbols);
    if (target.includes("/risk/performance")) return { status: "warming" };
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", async (route) => {
    const target = new URL(route.request().url()).pathname;
    if (target === "/api/operations/limits" && route.request().method() === "POST") {
      const rule = route.request().postDataJSON(); writes.push(rule);
      rules = [...(compute({ selected_groups: ["Fixture A"] }).limits), { id: 3, ...rule }];
      await route.fulfill({ json: { id: 3, ...rule } }); return;
    }
    await route.fulfill({ json: reply(target, route.request().postDataJSON() || {}) });
  });
  await page.routeWebSocket("**/api/live/ws", (socket) => socket.onMessage((raw) => {
    const message = JSON.parse(String(raw)); requests.push(message);
    if (message.type === "request") socket.send(JSON.stringify({ type: "response", id: message.id, status: 200, data: reply(message.path, message.body) }));
    if (message.type === "subscribe" && message.topic === "exposure") {
      const entry = { socket, id: message.id, params: message.params, version: 1 }; streams.push(entry);
      if (!silentStream) socket.send(JSON.stringify({ type: "snapshot", id: message.id, version: 1, data: compute(message.params, 0, rules, multiSymbols) }));
    }
  }));
  await page.goto("/dealing-risk?tab=exposure&group=Fixture%20A");
  await expect(page.getByTestId("exposure-workspace")).toBeVisible();
  return { streams, requests, writes, push(tick) {
    for (const stream of streams) stream.socket.send(JSON.stringify({ type: "snapshot", id: stream.id, version: ++stream.version, data: compute(stream.params, tick, rules, multiSymbols) }));
  } };
}

test("a silent exposure subscription falls back automatically and old pushes cannot overwrite newer values", async ({ page }) => {
  const context = await setup(page, { silentStream: true });
  const floating = page.getByTestId("exposure-workspace").getByText("Floating (USD)", { exact: true }).locator("..");
  await expect(floating).toContainText("14");
  expect(context.requests.some((message) => message.type === "request" && message.path === "/api/risk/exposure")).toBeTruthy();
  context.push(4);
  await expect(floating).toContainText("22");
  const stream = context.streams.at(-1);
  const old = compute(stream.params);
  old.source_updated_at -= 60;
  stream.socket.send(JSON.stringify({ type: "snapshot", id: stream.id, version: ++stream.version, data: old }));
  await expect(floating).toContainText("22");
  context.push(5);
  await expect(floating).toContainText("24");
});

test("exposure uses its own full-book stream, preserves trade IDs, drills down and receives fresh P&L", async ({ page }, testInfo) => {
  const context = await setup(page);
  const workspace = page.getByTestId("exposure-workspace");
  await expect(workspace.getByText("Floating (USD)", { exact: true }).locator("..")).toContainText("14");
  expect(context.requests.some((message) => message.topic === "live-risk")).toBeFalsy();
  expect(context.requests.some((message) => message.topic === "performance")).toBeFalsy();
  const groupRow = page.getByRole("row").filter({ has: page.getByRole("button", { name: /^(Expand|Collapse) group Fixture A$/ }) });
  await expect(groupRow.getByTestId("average-buy-price")).toContainText("100");
  await expect(groupRow.getByTestId("average-sell-price")).toContainText("120");
  await page.getByRole("button", { name: "Expand group Fixture A", exact: true }).click();
  await page.getByRole("button", { name: "Expand client 100200", exact: true }).click();
  await expect(page.getByText("18446744073709551615", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Collapse client 100200", exact: true })).toBeVisible();
  context.push(1);
  await expect(groupRow.getByTestId("average-buy-price")).toContainText("102");
  await expect(workspace.getByText("Floating (USD)", { exact: true }).locator("..")).toContainText("16");
  await page.getByRole("button", { name: "Refresh exposure", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("exposure-preview.png") });
  await page.getByLabel("Exposure direction", { exact: true }).selectOption("SELL");
  await expect(workspace.getByText("Positions", { exact: true }).locator("..").first()).toContainText("1");
  await expect(page.getByRole("button", { name: "Expand client 100200", exact: true })).toHaveCount(0);
  expect(context.requests.some((message) => message.type === "subscribe" && message.topic === "exposure" && message.params.direction === "SELL")).toBeTruthy();
  const bounds = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(bounds.width).toBeLessThanOrEqual(bounds.viewport + 1);
});

test("limits save through the existing HTTP controls and exact-login Enter expands the client", async ({ page }) => {
  const context = await setup(page);
  await page.getByLabel("Exposure login or name", { exact: true }).fill("100200");
  await page.getByLabel("Exposure login or name", { exact: true }).press("Enter");
  await expect(page.getByRole("button", { name: "Collapse client 100200", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Limits", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Limit scope", { exact: true }).selectOption("symbol");
  await dialog.getByLabel("Limit scope value", { exact: true }).fill("XAUUSD");
  await dialog.getByLabel("Critical lot threshold", { exact: true }).fill("5");
  await dialog.getByRole("button", { name: "Add limit", exact: true }).click();
  await expect.poll(() => context.writes.length).toBe(1);
  expect(context.writes[0]).toMatchObject({ scope_type: "symbol", scope_value: "XAUUSD", metric: "net_lots", threshold: 5 });
  expect(context.requests.some((message) => message.path === "/api/operations/limits" && message.method === "POST")).toBeFalsy();
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Limits", exact: true })).toBeFocused();
});


test("group and client average prices keep different symbols separate", async ({ page }) => {
  await setup(page, { multiSymbols: true });
  const groupRow = page.getByRole("row").filter({ has: page.getByRole("button", { name: /^(Expand|Collapse) group Fixture A$/ }) });
  const groupAverage = groupRow.getByTestId("average-buy-price");
  await expect(groupAverage).toContainText("By symbol (2)");
  await groupAverage.locator("summary").click();
  await expect(groupAverage).toContainText("TEST_OTHER");
  await expect(groupAverage).toContainText("1,000");
  await expect(groupAverage).toContainText("XAUUSD");
  await expect(groupAverage).not.toContainText("400");
  await page.getByRole("button", { name: "Expand group Fixture A", exact: true }).click();
  const clientRow = page.getByRole("row").filter({ has: page.getByRole("button", { name: "Expand client 100200", exact: true }) });
  await expect(clientRow.getByTestId("average-buy-price")).toContainText("By symbol (2)");
});
