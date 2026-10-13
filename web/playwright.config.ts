import { defineConfig, devices } from "@playwright/test";

/**
 * E2E config. The app under test is the real FastAPI server serving the built SPA,
 * so these tests exercise the same origin (cookies, CSP, static file serving) as production.
 *
 *   npm run build          # web/dist must exist first (the backend serves it)
 *   npx playwright test    # assumes the server is already up on E2E_BASE_URL
 *
 * The specs sign in as three accounts (tests/e2e/fixtures.ts). On a throwaway database, from the repo root:
 *   prospector create-user --email admin@exora.test --name "E2E Admin" --role admin --password 'E2ePass!2026'
 *   (and the same for sdr@exora.test / "E2E SDR" and viewer@exora.test / "E2E Viewer")
 *   prospector serve --port 8099
 * CI does exactly this (.github/workflows/ci.yml, job fresh-install).
 */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8099";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    // Deterministic viewport unless a test overrides it.
    viewport: { width: 1440, height: 900 },
    actionTimeout: 10_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
});