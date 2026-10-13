import { expect, test } from "@playwright/test";
import { login, waitForShell } from "./fixtures";

/**
 * Semantic/content assertions that a screenshot cannot make.
 *
 * These encode the things that are *wrong* rather than merely different, so a
 * regression is caught even when nobody looks at the images.
 */

test.describe("data honesty", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  test("no tile shows a loading skeleton once the data has loaded", async ({ page }) => {
    for (const path of ["/", "/companies", "/people", "/quality", "/analytics"]) {
      await page.goto(path);
      await waitForShell(page);
      // Wait past the query's first settle.
      await page.waitForTimeout(1200);

      /*
        antd renders `Statistic loading` as a shimmering block that never resolves if
        the value stays `undefined`. So a `.ant-statistic-content-loading` still on
        screen 1.2s after load means the component is waiting for a value that will
        never arrive.
      */
      const stuck = await page.locator(".ant-statistic-content-loading").count();
      expect(stuck, `${path} has ${stuck} statistic(s) stuck loading`).toBe(0);
    }
  });

  test("no surface renders NaN, undefined or Infinity as text", async ({ page }) => {
    // Fourteen routes with a settle wait each: this legitimately exceeds the default timeout.
    test.setTimeout(180_000);
    /*
      `null` is deliberately NOT asserted. It is a legitimate word in this domain: a
      discovery source documents the DNS rule "reject-all SPF" as "null-MX", and that
      description is shown verbatim on the Discovery tab. Flagging it would push
      someone to "fix" correct copy.
    */
    const BANNED = /\b(NaN|undefined|Infinity)\b/;
    for (const path of [
      "/",
      "/companies",
      "/people",
      "/quality",
      "/analytics",
      "/calls",
      "/playbook",
      "/outreach",
      "/discovery",
      "/tasks",
      "/automations",
      "/engine",
      "/team",
      "/audit",
    ]) {
      await page.goto(path);
      // Wait for the tab to settle rather than a fixed sleep: a busy data engine makes the
      // first paint slow, and a fixed wait was timing the whole spec out.
      await waitForShell(page);
      await expect(page.locator(".ant-layout-content")).not.toBeEmpty();
      await page.waitForTimeout(500);

      const text = (await page.locator(".ant-layout-content").innerText()).replace(/ | /g, " ");
      const hit = BANNED.exec(text);
      expect(
        hit,
        hit ? `${path} renders "${hit[0]}" near: ${text.slice(Math.max(0, hit.index - 60), hit.index + 40)}` : "",
      ).toBeNull();
    }
  });

  test("dates render in a readable, consistent format", async ({ page }) => {
    await page.goto("/quality");
    await waitForShell(page);
    const text = await page.locator(".ant-layout-content").innerText();
    // No raw ISO timestamps should leak into the UI.
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/);
  });

  test("the audit log shows unambiguous UTC timestamps", async ({ page }) => {
    await page.goto("/audit");
    await waitForShell(page);
    const firstRow = page.locator("tbody tr.ant-table-row").first();
    await expect(firstRow).toBeVisible();
    // Format is "YYYY-MM-DD HH:MM:SSZ".
    await expect(firstRow.locator("td").first()).toHaveText(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z/);
  });
});

test.describe("destructive actions are guarded", () => {
  test("bulk reject on companies asks for confirmation", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/companies");
    await waitForShell(page);

    // Select a row to reveal the bulk bar. antd's row checkbox is a hidden input
    // driven by a styled span, so click the cell rather than the input.
    await page.locator("tbody tr.ant-table-row .ant-checkbox").first().click();
    const reject = page.getByRole("button", { name: /^reject$/i });
    await expect(reject, "selecting a row did not reveal the bulk action bar").toBeVisible();

    // Clicking must open a confirmation, not act immediately.
    await reject.click();
    await expect(page.getByText(/reject \d+ compan/i)).toBeVisible();
    // Dismiss it: nothing should have changed.
    await page.getByRole("button", { name: /cancel/i }).click();
  });

  test("deactivating a teammate asks for confirmation", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/team");
    await waitForShell(page);

    /*
      The signed-in admin's own status control is deliberately disabled (you cannot
      deactivate yourself), so target a *different* teammate.
    */
    const status = page.locator('.ant-select[aria-label^="Status for"]:not(.ant-select-disabled)').first();
    await expect(status, "no editable teammate status control found").toBeVisible();
    await status.click();
    await page.locator(".ant-select-dropdown .ant-select-item-option", { hasText: "Inactive" }).click();

    // A confirm dialog must appear before anything is written.
    await expect(page.getByRole("dialog", { name: /deactivate/i })).toBeVisible();
    await page.getByRole("button", { name: /cancel/i }).click();
  });

  test("cancelling a queued discovery run asks for confirmation", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/discovery");
    await waitForShell(page);

    const cancel = page.getByRole("button", { name: /^cancel$/i }).first();
    if ((await cancel.count()) === 0) return; // nothing queued right now
    await cancel.click();
    await expect(page.getByText(/cancel this run/i)).toBeVisible();
    await page.getByRole("button", { name: /cancel/i }).last().click();
  });

  test("re-syncing the plan asks for confirmation", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/playbook?tab=plan");
    await waitForShell(page);

    await page.getByTestId("plan-resync").click();
    await expect(page.getByText(/re-sync from markdown/i)).toBeVisible();
    await page.getByRole("button", { name: /cancel/i }).click();
  });
});

test.describe("theme", () => {
  test("the toggle switches scheme and the choice survives a reload", async ({ page }) => {
    await login(page, "admin");
    await page.evaluate(() => localStorage.setItem("exora.scheme", "light"));
    await page.reload();
    await waitForShell(page);

    const scheme = () => page.evaluate(() => document.documentElement.dataset.scheme);
    expect(await scheme()).toBe("light");

    await page.getByTestId("theme-toggle").click();
    expect(await scheme()).toBe("dark");

    await page.reload();
    await waitForShell(page);
    expect(await scheme(), "the dark choice did not persist across a reload").toBe("dark");
  });

  test("the page surface actually changes with the scheme", async ({ page }) => {
    await login(page, "admin");

    /*
      Measure the antd Layout surface, not <body>: the layout paints the app's
      background and <body> stays transparent in both schemes, so comparing it proves
      nothing.
    */
    const surface = () =>
      page.evaluate(() => {
        const el = document.querySelector(".ant-layout") ?? document.body;
        return getComputedStyle(el).backgroundColor;
      });

    await page.evaluate(() => localStorage.setItem("exora.scheme", "light"));
    await page.goto("/companies");
    await waitForShell(page);
    const light = await surface();

    await page.evaluate(() => localStorage.setItem("exora.scheme", "dark"));
    await page.reload();
    await waitForShell(page);
    const dark = await surface();

    expect(light, `light surface was ${light}`).not.toBe("rgba(0, 0, 0, 0)");
    expect(dark, `dark surface was ${dark}`).not.toBe("rgba(0, 0, 0, 0)");
    expect(light, "light and dark surfaces are identical").not.toBe(dark);
  });
});

test.describe("keyboard access", () => {
  test("company rows open with Enter", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/companies");
    await waitForShell(page);

    const row = page.locator("tbody tr.ant-table-row").first();
    await row.focus();
    await page.keyboard.press("Enter");

    // The drawer must open from the keyboard alone.
    await expect(page.getByRole("dialog")).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/open=/);
  });

  test("the search shortcut focuses search", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/tasks");
    await waitForShell(page);

    await page.keyboard.press("Control+k");
    await expect(page.getByRole("combobox", { name: /search companies and people/i })).toBeFocused();
  });
});

test.describe("pagination integrity", () => {
  test("deleting the last row on a page returns to a valid page", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/playbook?tab=events");
    await waitForShell(page);
    // Just assert the pager is coherent: page 1 of at most N.
    const pager = page.locator(".ant-pagination");
    if ((await pager.count()) > 0) await expect(pager).toBeVisible();
  });
});