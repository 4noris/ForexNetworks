import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000",
    channel: "msedge",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-edge", use: { ...devices["Desktop Edge"] } },
    { name: "mobile-edge", use: { ...devices["Pixel 5"] } },
  ],
});
