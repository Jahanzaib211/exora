import { CREDENTIALS, expect, login, test, waitForShell } from "./fixtures";

/**
 * Resilience tests for the failures that only appear in a real deployment.
 *
 * The stale-chunk case is the important one: a browser tab that was open across a
 * `npm run build` holds references to chunk filenames that the build deleted, and
 * every lazy route it visits afterwards throws "Failed to fetch dynamically imported
 * module". These tests simulate that precisely, by loading the app, then blocking
 * the chunk URLs that the *currently served* index.html points at.
 */

test.describe("stale lazy chunks after a redeploy", () => {
  test("a missing route chunk triggers a one-time reload, then the tab works", async ({ page }) => {
    await login(page, "admin");
    await waitForShell(page);

    /*
     * Reproduce a redeploy faithfully: the first lazy-chunk request for a route
     * fails, and after the reload succeeds — exactly what happens when the build
     * replaced /assets while the tab stayed open, because the reloaded index.html
     * carries the new chunk hashes.
     *
     * Failing *every* request would be wrong: it would leave the reloaded page
     * permanently broken and prove nothing about recovery.
     */
    const failedOnce = new Set<string>();
    await page.route("**/assets/*.js", async (route) => {
      const url = route.request().url();
      // Entry bundle and shared vendors are always available.
      if (/index-|vendor-|rolldown-runtime/.test(url)) return route.continue();
      if (!failedOnce.has(url)) {
        failedOnce.add(url);
        return route.abort("failed");
      }
      return route.continue();
    });

    // Client-side navigation, so the lazy import is genuinely exercised.
    await page.getByRole("menuitem", { name: "Discovery" }).click();

    await page.waitForURL(/\/discovery/, { timeout: 20_000 });
    // Wait for the recovery reload to settle, then for the route to actually paint.
    await page.waitForFunction(
      () => !document.body.innerText.includes("Loading"),
      undefined,
      { timeout: 20_000 },
    );
    await waitForShell(page);

    expect(failedOnce.size, "the test never actually failed a chunk load").toBeGreaterThan(0);

    await expect(
      page.getByText(/could not be displayed/i),
      "tab is stuck on the error boundary after a stale chunk",
    ).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /discovery/i }).first()).toBeVisible();
  });

  test("the reload happens at most once, so a genuinely missing chunk cannot loop", async ({ page }) => {
    await login(page, "admin");
    await waitForShell(page);

    let navigations = 0;
    page.on("framenavigated", (f) => {
      if (f === page.mainFrame()) navigations += 1;
    });

    // Block everything under /assets, including the entry bundle. Nothing can load,
    // so recovery is impossible and the tab must NOT enter a reload loop.
    await page.route("**/assets/*.js", (route) => route.abort("failed"));

    await page.getByRole("menuitem", { name: "Discovery" }).click();
    await page.waitForTimeout(4000);

    // One reload attempt is correct; a loop is not.
    expect(
      navigations,
      `expected at most 1 reload, saw ${navigations} — the recovery guard is not working`,
    ).toBeLessThanOrEqual(2);
  });

  test("a chunk error is not silently swallowed — it is reported if recovery fails", async ({ page }) => {
    await login(page, "admin");
    await waitForShell(page);

    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });

    // Pre-arm the guard so recovery declines to reload, exercising the reporting path.
    await page.evaluate(() => sessionStorage.setItem("exora.chunk-reload", "1"));
    await page.route("**/assets/*.js", async (route) => {
      const url = route.request().url();
      if (/index-|vendor-|rolldown-runtime/.test(url)) return route.continue();
      await route.abort("failed");
    });

    await page.getByRole("menuitem", { name: "Discovery" }).click();

    // The boundary must explain the failure and offer a way forward.
    await expect(page.getByText(/could not be displayed/i)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /try again/i })).toBeVisible();
    expect(
      errors.some((e) => /dynamically imported module|ChunkLoadError/i.test(e)),
      "a failed chunk load should be logged, not hidden",
    ).toBe(true);
  });
});

test.describe("offline and network failures", () => {
  /**
 * The route is installed before the first companies request and the page is loaded
 * with a full navigation, so the assertion is about a genuinely cold load. A soft
 * navigation would reuse `keepPreviousData` from an earlier visit and the table would
 * render cached rows instead of surfacing the failure.
 */
test("a dropped connection shows a retryable error rather than a blank screen", async ({ page }) => {
    await login(page, "admin");
    await page.route("**/api/companies?*", (route) => route.abort("failed"));
    await page.goto("/companies");
    await waitForShell(page);

    // The error state must be explicit — an empty table is indistinguishable from
    // "no companies match", which reads as a data problem rather than a network one.
    await expect(page.getByText(/could not load companies/i)).toBeVisible({ timeout: 20_000 });
    const retry = page.getByRole("button", { name: /^retry$/i }).first();
    await expect(retry).toBeVisible();
  });

  test("retrying after the connection returns recovers the view", async ({ page }) => {
    await login(page, "admin");

    let offline = true;
    await page.route("**/api/companies?*", (route) => (offline ? route.abort("failed") : route.continue()));
    await page.goto("/companies");
    await waitForShell(page);

    const retry = page.getByRole("button", { name: /^retry$/i }).first();
    await expect(retry).toBeVisible({ timeout: 20_000 });

    // Connection restored; the user's retry must actually bring the data back.
    offline = false;
    await retry.click();
    await expect(page.getByRole("table")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/could not load companies/i)).toHaveCount(0);
  });
});

test.describe("session expiry", () => {
  test("a session that dies mid-session returns the user to login", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/companies");
    await waitForShell(page);

    // Simulate the server forgetting the session: every API call now 401s.
    await page.context().clearCookies();
    await page.route("**/api/**", async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({ detail: "not signed in" }),
        });
      }
      return route.continue();
    });

    /*
      A hard navigation rather than clicking the sidebar. Clearing cookies makes Refine
      re-check identity on the next request, so the redirect can fire before the click
      lands — the click then waits forever for a menu item that is already gone. The
      behaviour under test (a 401 lands the user on /login) is identical either way.
    */
    await page.goto("/tasks");

    // The central 401 handler must send the user back to sign-in, not leave them
    // staring at a dead table.
    await page.waitForURL(/\/login/, { timeout: 20_000 });
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("signing in again returns the user to the page they were on", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/companies");
    await waitForShell(page);

    await page.context().clearCookies();
    await page.route("**/api/**", (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"not signed in"}' })
        : route.continue(),
    );

    await page.goto("/tasks");
    await page.waitForURL(/\/login/, { timeout: 20_000 });

    // Clear the interception so the real credentials can be submitted.
    await page.unroute("**/api/**");
    await page.getByLabel(/email/i).fill(CREDENTIALS.admin.email);
    await page.getByLabel(/password/i).fill(CREDENTIALS.admin.password);
    await page.getByRole("button", { name: /sign in/i }).click();

    // Refine redirects with `?to=` and api.ts with `?next=`; the login page must honour
    // both, otherwise the user silently loses their place.
    await page.waitForURL(/\/tasks/, { timeout: 20_000 });
    await expect(page.getByRole("heading", { name: /tasks/i }).first()).toBeVisible();
  });
});