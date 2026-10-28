import { expect, test } from "@playwright/test";

test("retired archive route redirects to Risk Center without fetching obsolete reports", async ({ page }) => {
  const archived = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("user", JSON.stringify({ id: 1, username: "fixture", is_admin: true, must_change_pw: false })));
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/analytics/")) archived.push(path);
    return route.fulfill({ json: {} });
  });
  await page.goto("/dealing-risk/analytics");
  await expect(page).toHaveURL(/\/risk-center$/);
  await expect(page.getByRole("heading", { name: "Risk Center", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Risk Analytics", exact: true })).toHaveCount(0);
  expect(archived).toEqual([]);
  expect(errors).toEqual([]);
});

test("live risk updates existing KPIs without reloading or remounting the page", async ({
  page,
}) => {
  let calls = 0;
  let offline = false;
  await page.addInitScript(() =>
    localStorage.setItem(
      "user",
      JSON.stringify({
        username: "live-test",
        is_admin: true,
        must_change_pw: false,
      }),
    ),
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body = {};
    if (path.endsWith("/groups"))
      body = { groups: ["real-test"], default_group: "real-test" };
    if (path.endsWith("/settings/books")) body = { a_book: ["real-test"] };
    if (path.endsWith("/config"))
      body = { live_refresh_seconds: 5, operational_refresh_seconds: 30 };
    if (path.endsWith("/risk/live")) {
      calls += 1;
      body = {
        generated_at: new Date().toISOString(),
        source_updated_at: Date.now() / 1000,
        summary: {
          floating_pnl: calls * 100,
          equity: 1000,
          balance: 1000,
          margin: 100,
          margin_free: 900,
          currencies: ["USD"],
          open_positions: 1,
          gross_lots: 1,
        },
        accounts: [],
        positions: [],
        symbols: [],
        warnings: [],
        network: {},
        cache: { source: "manager" },
      };
      if (offline) body = { status: "offline", source_updated_at: null, generated_at: null, summary: { floating_pnl: null, equity: null }, warnings: ["Manager connection interrupted"], accounts: [], positions: [], symbols: [] };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/dealing-risk");
  const heading = page.getByRole("heading", {
    name: "Dealing and Risk",
    exact: true,
  });
  await expect(heading).toBeVisible();
  await heading.evaluate((node) => {
    node.dataset.mountMarker = "live-retained";
  });
  const kpi = page
    .locator(".risk-stat-card")
    .filter({ has: page.getByText("Floating P&L", { exact: true }) });
  await expect(kpi).toContainText("100");
  await expect.poll(() => calls, { timeout: 15000 }).toBeGreaterThan(1);
  await expect(kpi).toContainText("200");
  offline = true;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("rms:refresh-section", { detail: { path: "/dealing-risk" } })));
  await expect(page.getByText(/Updates delayed · last verified/)).toBeVisible();
  await expect(kpi).toContainText("200");
  offline = false;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("rms:refresh-section", { detail: { path: "/dealing-risk" } })));
  await expect(page.getByText(/Updates delayed · last verified/)).toHaveCount(0);
  await expect(kpi).not.toContainText("Unavailable");
  expect(await heading.evaluate((node) => node.dataset.mountMarker)).toBe(
    "live-retained",
  );
  await expect(page).toHaveURL(/\/dealing-risk$/);
  expect(
    await page.evaluate(
      () => performance.getEntriesByType("navigation").length,
    ),
  ).toBe(1);
});
