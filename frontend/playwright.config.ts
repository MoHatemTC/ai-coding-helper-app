import { defineConfig, devices } from "@playwright/test";

/**
 * Smoke test config. Boots the Next.js dev server itself (webServer below)
 * so `npm run test:smoke` works standalone — no backend required for the
 * tests that don't need one; see tests-e2e/smoke.spec.ts for which ones do.
 */
export default defineConfig({
  testDir: "./tests-e2e",
  fullyParallel: true,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // This sandbox ships a pre-installed Chromium at a fixed path
        // rather than the exact revision @playwright/test expects. Pin to
        // it explicitly so tests run without trying to download a browser
        // (no network access for that here). Safe to remove this override
        // in a normal environment with `npx playwright install`.
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
          : {},
      },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
