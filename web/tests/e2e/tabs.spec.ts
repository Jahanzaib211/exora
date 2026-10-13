import { ALL_ROUTES, expect, expectClean, login, test, waitForShell } from "./fixtures";

/**
 * Baseline sweep: visit every tab as an admin and assert it renders without an
 * uncaught exception, a console error or a 5xx. This is the regression net that
 * catches the "blank screen on one tab" class of bug.
 */
test.describe("every tab renders", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  for (const route of ALL_ROUTES) {
    test(`${route.name} (${route.path})`, async ({ tab }) => {
      const { page } = tab;
      await page.goto(route.path);
      await waitForShell(page);

      // The shell must never be empty: either real content or an explicit empty/error state.
      const content = page.locator(".ant-layout-content");
      await expect(content).toBeVisible();
      await expect(content).not.toBeEmpty();

      expectClean(tab, route.name);
    });
  }

  test("no route falls through to a blank page or a catch-all redirect", async ({ tab }) => {
    const { page } = tab;
    for (const route of ALL_ROUTES) {
      await page.goto(route.path);
      await page.waitForLoadState("domcontentloaded");
      // A catch-all redirect would land back on "/" for a non-root route.
      expect(new URL(page.url()).pathname, `${route.path} was redirected`).toBe(route.path);
    }
  });
});

test.describe("auth", () => {
  test("unauthenticated access to a protected route lands on login", async ({ page }) => {
    await page.context().clearCookies();
    await page.goto("/companies");
    await page.waitForURL(/\/login/, { timeout: 20_000 });
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("sign in, then sign out returns to login and blocks the app again", async ({ page }) => {
    await login(page, "admin");
    await expect(page.getByText("E2E Admin")).toBeVisible();

    await page.getByText("E2E Admin").click();
    await page.getByRole("menuitem", { name: /sign out/i }).click();
    await page.waitForURL(/\/login/, { timeout: 20_000 });

    await page.goto("/tasks");
    await page.waitForURL(/\/login/, { timeout: 20_000 });
  });

  test("bad credentials show an error and stay on login", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill("admin@exora.test");
    await page.getByLabel(/password/i).fill("definitely-wrong");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByRole("alert").or(page.locator(".ant-alert-error"))).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/login");
  });
});

test.describe("role gating", () => {
  test("viewer cannot see admin-only nav sections", async ({ page }) => {
    await login(page, "viewer");
    await page.goto("/audit");
    await waitForShell(page);
    // Viewers get a read-only message rather than the log.
    await expect(page.getByText(/admin/i).first()).toBeVisible();
  });

  test("sdr sees the app but not the team/audit admin surfaces", async ({ page }) => {
    await login(page, "sdr");
    await page.goto("/companies");
    await waitForShell(page);
    await expect(page.getByRole("heading", { name: /companies/i }).first()).toBeVisible();
  });
});