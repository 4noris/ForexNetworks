import { expect, test } from "@playwright/test";

const username = process.env.E2E_USERNAME;
const password = process.env.E2E_PASSWORD;
const routes = [
  "/dealing-risk",
  "/manager",
  "/market-watch",
  "/operations",
  "/risk-automation",
  "/deal-report",
  "/real-brokerage",
  "/settings",
];

test.beforeEach(async ({ page }) => {
  test.skip(
    !username || !password,
    "Set E2E_USERNAME and E2E_PASSWORD for a non-production audit user.",
  );
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page).toHaveURL(/\/dealing-risk$/);
});

test("all protected pages render without browser or server failures", async ({
  page,
  isMobile,
}, testInfo) => {
  const browserErrors = [];
  const serverFailures = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() >= 500)
      serverFailures.push(`${response.status()} ${response.url()}`);
  });

  for (const route of routes) {
    await test.step(route, async () => {
      if (isMobile)
        await page.getByRole("button", { name: "Open navigation" }).click();
      await page.locator(`a[href="${route}"]`).first().click();
      await expect(page).toHaveURL(new RegExp(`${route.replace("/", "\\/")}$`));
      await expect(page.locator("main")).toBeVisible();
      await expect(page).not.toHaveURL(/\/(?:login|change-password)$/);
      await page.waitForTimeout(600);
      const horizontallyClipped = await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth + 1,
      );
      expect(
        horizontallyClipped,
        `${route} must not overflow the viewport`,
      ).toBe(false);
    });
  }

  expect(browserErrors, `Browser errors in ${testInfo.project.name}`).toEqual(
    [],
  );
  expect(serverFailures, `Server failures in ${testInfo.project.name}`).toEqual(
    [],
  );
});

test("mobile navigation opens, routes, and closes", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "Mobile navigation is validated in the mobile project.");
  const openNavigation = page.getByRole("button", { name: "Open navigation" });
  await expect(openNavigation).toBeVisible();
  await openNavigation.click();
  const settingsLink = page.getByRole("link", { name: "Controls" });
  await expect(settingsLink).toBeVisible();
  await settingsLink.click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.locator("aside")).not.toBeInViewport();
});
