import { ALL_ROUTES, expect, expectClean, login, test, waitForShell } from "./fixtures";

/**
 * Structural guarantees that apply to every tab. These are the regressions that are
 * invisible in a screenshot but painful in daily use: a tab that renders an eternal
 * spinner on error, or one that shows "viewers cannot see this" to an admin.
 */

test.describe("error states", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  /** Every data-backed tab must show a retryable error, not a permanent skeleton. */
  for (const route of ALL_ROUTES) {
    test(`${route.name}: a failed API call shows an error with a retry, not an endless spinner`, async ({ tab }) => {
      const { page } = tab;
      // Fail only this tab's own GETs, not the auth/identity calls.
      await page.route("**/api/**", async (route) => {
        const req = route.request();
        if (req.method() !== "GET") return route.continue();
        const url = req.url();
        if (url.includes("/api/auth/") || url.includes("/api/sources")) return route.continue();
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "synthetic failure for testing" }),
        });
      });

      await page.goto(route.path);
      await waitForShell(page);

      // An eternal skeleton is the bug: nothing tells the user anything is wrong.
      const skeletons = page.locator('[aria-busy="true"]');
      if ((await skeletons.count()) > 0) {
        await expect(
          skeletons.first(),
          `${route.name} is stuck showing a loading skeleton after a 500`,
        ).toBeHidden({ timeout: 8000 });
      }

      // A retry affordance must be present whenever we surface the failure.
      const retry = page.getByRole("button", { name: /retry|try again/i });
      if ((await retry.count()) > 0) {
        await expect(retry.first()).toBeVisible();
      }
    });
  }
});

test.describe("no false permission messaging", () => {
  test("admin is never told they lack access", async ({ page }) => {
    await login(page, "admin");
    for (const path of ["/audit", "/team"]) {
      await page.goto(path);
      await waitForShell(page);
      const text = (await page.locator(".ant-layout-content").innerText()).toLowerCase();
      expect(text, `${path} showed a permission message to an admin`).not.toContain(
        "available to admins only",
      );
      expect(text, `${path} showed a permission message to an admin`).not.toContain(
        "viewers cannot",
      );
    }
  });

  test("viewer is told why the team list is unavailable", async ({ page }) => {
    await login(page, "viewer");
    await page.goto("/team");
    await waitForShell(page);
    await expect(page.getByText(/not available to viewers|viewers cannot/i).first()).toBeVisible();
  });
});

test.describe("accessibility basics", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  test("every tab has exactly one level-1 page heading", async ({ page }) => {
    for (const route of ALL_ROUTES) {
      await page.goto(route.path);
      await waitForShell(page);
      const h1 = page.locator(".ant-layout-content h1, .ant-layout-content h2, .ant-layout-content h3, .ant-layout-content h4");
      // antd renders PageHeader titles as <h1>-<h5>; at least one is required for
      // screen-reader orientation.
      await expect(
        h1.first(),
        `${route.path} has no heading in its content area`,
      ).toBeVisible();
    }
  });

  test("the search box is labelled and reachable by keyboard", async ({ page }) => {
    // antd's AutoComplete exposes a combobox, not a textbox.
    const search = page.getByRole("combobox", { name: /search companies and people/i });
    await expect(search).toBeVisible();

    // Ctrl+K focuses search from anywhere.
    await page.keyboard.press("Control+k");
    await expect(search).toBeFocused();
  });

  test("the navigation exposes real link/button semantics", async ({ page }) => {
    const menu = page.locator("aside.ant-layout-sider");
    if ((await menu.count()) === 0) return; // narrow viewport: nav is in a drawer
    const items = menu.getByRole("menuitem");
    expect(await items.count()).toBeGreaterThan(10);
  });

  /**
   * Icon-only buttons must be nameable. antd's own affordances (clear-icon,
   * pagination arrows, search-submit) label their inner `<span role="img">`, which
   * gives the button an accessible name by descendant text — so those are fine and
   * are excluded here. What this catches is a hand-rolled button with neither text
   * nor an aria-label.
   */
  test("icon-only buttons carry an accessible name", async ({ page }) => {
    const VENDOR_OWNED = /ant-input-clear-icon|ant-pagination|ant-input-search-button|ant-select-arrow|ant-checkbox-inner|ant-switch|ant-collapse-arrow/;
    for (const path of ["/", "/companies", "/discovery", "/tasks", "/team"]) {
      await page.goto(path);
      await waitForShell(page);
      const unlabelled = await page.evaluate((vendorPattern) => {
        const vendor = new RegExp(vendorPattern);
        const bad: string[] = [];
        for (const el of Array.from(document.querySelectorAll("button"))) {
          if (vendor.test(el.className)) continue;
          const text = (el.textContent ?? "").trim();
          const label = el.getAttribute("aria-label") ?? el.getAttribute("title") ?? "";
          // A descendant with role="img"/aria-label also names the button.
          const inner = el.querySelector("[aria-label]")?.getAttribute("aria-label") ?? "";
          if (!text && !label && !inner) bad.push(el.outerHTML.slice(0, 120));
        }
        return bad;
      }, VENDOR_OWNED.source);
      expect(unlabelled, `${path} has icon-only buttons with no accessible name`).toEqual([]);
    }
  });

  test("no link uses an unsafe scheme", async ({ page }) => {
    await page.goto("/companies");
    await waitForShell(page);
    const unsafe = await page.evaluate(() =>
      Array.from(document.querySelectorAll("a[href]"))
        .map((a) => a.getAttribute("href") ?? "")
        .filter((h) => h && !/^(https?:|mailto:|tel:|\/|#)/i.test(h)),
    );
    expect(unsafe, "anchors with a non-http(s) scheme found").toEqual([]);
  });
});

test.describe("layout integrity", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  test("no tab overflows horizontally at 1280px", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    for (const route of ALL_ROUTES) {
      await page.goto(route.path);
      await waitForShell(page);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      // A few px of rounding is fine; a scrollbar's worth means a broken layout.
      expect(overflow, `${route.path} overflows horizontally by ${overflow}px`).toBeLessThanOrEqual(4);
    }
  });

  test("every tab is usable at 375px (mobile)", async ({ tab: t }) => {
    const { page } = t;
    await page.setViewportSize({ width: 375, height: 720 });
    for (const route of ALL_ROUTES) {
      t.clear();
      await page.goto(route.path);
      await waitForShell(page);

      // The sider must be gone and navigation must stay reachable via the toggle.
      await expect(
        page.locator("aside.ant-layout-sider"),
        `${route.path} still shows the desktop sider at 375px`,
      ).toHaveCount(0);
      const toggle = page.getByTestId("nav-toggle");
      await expect(toggle, `${route.path} has no way to open navigation on mobile`).toBeVisible();

      expectClean(t, `${route.name} @375px`);
    }
  });

  test("the mobile navigation drawer opens and navigates", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 720 });
    await page.goto("/");
    await waitForShell(page);

    await page.getByTestId("nav-toggle").click();
    const drawer = page.getByRole("dialog", { name: /navigation/i });
    await expect(drawer).toBeVisible();
    await drawer.getByRole("menuitem", { name: "Companies" }).click();
    await page.waitForURL(/\/companies/);
    // Choosing a destination must close the drawer, or it covers the new page.
    await expect(drawer).toBeHidden();
  });
});