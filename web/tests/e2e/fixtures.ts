import { expect, test as base, type ConsoleMessage, type Page } from "@playwright/test";

/**
 * Shared E2E fixtures.
 *
 * `tab` collects console errors and failed responses for the life of a test so any
 * spec can assert "this tab rendered cleanly" in one line. Console noise that is
 * provably benign (favicon 404s, React DevTools nag) is filtered out here rather
 * than in 20 different specs.
 */

export const CREDENTIALS = {
  admin: { email: "admin@exora.test", password: "E2ePass!2026" },
  sdr: { email: "sdr@exora.test", password: "E2ePass!2026" },
  viewer: { email: "viewer@exora.test", password: "E2ePass!2026" },
} as const;

export type Role = keyof typeof CREDENTIALS;

type Tab = {
  page: Page;
  consoleErrors: string[];
  pageErrors: string[];
  failedResponses: { url: string; status: number }[];
  clear(): void;
};

/** Console messages that are not defects. */
const IGNORED = [
  /favicon/i,
  /Download the React DevTools/i,
  /\[vite\] connect/i,
  /\[vite\] hot updated/i,
];

function isServerError(r: { url: string; status: number }) {
  return r.status >= 500;
}

export const test = base.extend<{ tab: Tab }>({
  tab: async ({ page }, use) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: { url: string; status: number }[] = [];

    page.on("console", (m: ConsoleMessage) => {
      if (m.type() !== "error") return;
      const text = m.text();
      if (IGNORED.some((re) => re.test(text))) return;
      consoleErrors.push(text);
    });
    page.on("pageerror", (e) => pageErrors.push(e.message));
    page.on("response", (r) => {
      if (r.status() >= 400) failedResponses.push({ url: r.url(), status: r.status() });
    });

    await use({
      page,
      consoleErrors,
      pageErrors,
      failedResponses,
      clear() {
        consoleErrors.length = 0;
        pageErrors.length = 0;
        failedResponses.length = 0;
      },
    });
  },
});

export { expect };

/** Log in through the real form so the session cookie is set exactly as a user would. */
export async function login(page: Page, role: Role = "admin") {
  const { email, password } = CREDENTIALS[role];
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20_000 });
}

/**
 * Wait until the shell has painted, so per-tab waits are stable.
 *
 * Above the breakpoint navigation lives in the sider; below it, it lives in a drawer
 * and the sider is absent by design. Either is a healthy shell, so accept both.
 */
export async function waitForShell(page: Page) {
  await expect(page.locator("aside.ant-layout-sider").or(page.getByTestId("nav-toggle"))).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/**
 * Assert the tab rendered cleanly: no uncaught exceptions, no console errors and no
 * 5xx responses. 4xx on an optional endpoint is surfaced by the page's own error state.
 */
export function expectClean(tab: Tab, label: string) {
  expect(tab.pageErrors, `${label}: uncaught page errors`).toEqual([]);
  expect(tab.consoleErrors, `${label}: console errors`).toEqual([]);
  const serverErrors = tab.failedResponses.filter(isServerError);
  expect(serverErrors, `${label}: 5xx responses`).toEqual([]);
}

/** Every route the sidebar/nav can reach. Used by the sweep specs. */
export const ALL_ROUTES = [
  { path: "/", name: "Overview" },
  { path: "/companies", name: "Companies" },
  { path: "/people", name: "People" },
  { path: "/discovery", name: "Discovery" },
  { path: "/quality", name: "Data quality" },
  { path: "/engine", name: "Data engine" },
  { path: "/automations", name: "Automations" },
  { path: "/outreach", name: "Outreach" },
  { path: "/tasks", name: "Tasks" },
  { path: "/calls", name: "Calls & pilots" },
  { path: "/playbook", name: "Playbook" },
  { path: "/analytics", name: "Analytics" },
  { path: "/docs", name: "Documents" },
  { path: "/team", name: "Team" },
  { path: "/audit", name: "Audit log" },
] as const;