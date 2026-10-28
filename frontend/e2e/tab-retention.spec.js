import { expect, test } from "@playwright/test";

test("main and risk tabs retain their mounted state", async ({ page, isMobile }) => {
  await page.addInitScript(() => {
    localStorage.setItem("user", JSON.stringify({
      username: "navigation-test", is_admin: false, must_change_pw: false,
    }));
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body = {};
    if (path.endsWith("/groups"))
      body = { groups: ["test\\group"], default_group: "test\\group" };
    if (path.endsWith("/dealer-command-center"))
      body = { status: "connected", selected_groups: ["test\\group"], what_changed: [], risk_events: [], symbol_exposure: [], top_risk_accounts: [] };
    if (path.endsWith("/risk/live"))
      body = { summary: {}, accounts: [], symbols: [], positions: [], network: {}, warnings: [], cache: { source: "manager" } };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/command-center");
  const commandHeading = page.getByRole("heading", { name: "Dealer Command Center" });
  await expect(commandHeading).toBeVisible();
  await commandHeading.evaluate((node) => { node.dataset.mountMarker = "retained"; });

  if (isMobile) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Dealing and Risk", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Dealing and Risk" })).toBeVisible();
  await page.getByRole("button", { name: "Anti-Fraud" }).click();
  const fraudHeading = page.getByText("Continuous anti-fraud monitoring");
  await expect(fraudHeading).toBeVisible();
  await fraudHeading.evaluate((node) => { node.dataset.mountMarker = "retained"; });
  await page.getByRole("button", { name: "Live Monitor" }).click();
  await page.getByRole("button", { name: "Anti-Fraud" }).click();
  expect(await fraudHeading.evaluate((node) => node.dataset.mountMarker)).toBe("retained");

  if (isMobile) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Command Center", exact: true }).click();
  expect(await commandHeading.evaluate((node) => node.dataset.mountMarker)).toBe("retained");
  await expect(page.getByText("Loading section...")).toHaveCount(0);
  expect(errors).toEqual([]);
});
