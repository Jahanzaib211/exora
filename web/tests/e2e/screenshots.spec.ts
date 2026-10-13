import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { ALL_ROUTES, login, waitForShell } from "./fixtures";

/**
 * Visual evidence sweep.
 *
 * Captures every tab in both colour schemes at desktop and mobile widths, and fails
 * the run if a page renders blank. The PNGs are the artefact to eyeball after a
 * design-system change; the assertion is the guard against a tab silently rendering
 * nothing.
 */

const OUT = path.resolve("screenshots");
const slug = (p: string) => (p === "/" ? "overview" : p.replace(/^\//, "").replace(/\//g, "-"));

test.describe.configure({ mode: "serial" });

for (const scheme of ["light", "dark"] as const) {
  test.describe(`${scheme} scheme`, () => {
    test(`capture every tab (${scheme})`, async ({ page }) => {
      test.setTimeout(180_000);
      const dir = path.join(OUT, scheme);
      fs.mkdirSync(dir, { recursive: true });

      await login(page, "admin");
      // Set the scheme explicitly rather than relying on the OS preference.
      await page.evaluate((s) => localStorage.setItem("exora.scheme", s), scheme);

      for (const route of ALL_ROUTES) {
        await page.goto(route.path);
        await waitForShell(page);
        await page.waitForTimeout(700);
        await page.screenshot({ path: path.join(dir, `${slug(route.path)}.png`) });

        // Guard against a blank capture.
        const content = page.locator(".ant-layout-content");
        await expect(content).toBeVisible();
        expect((await content.innerText()).trim().length, `${route.path} rendered blank (${scheme})`).toBeGreaterThan(20);
      }
    });
  });
}

test.describe("mobile", () => {
  test("capture every tab at 390px", async ({ page }) => {
    test.setTimeout(180_000);
    const dir = path.join(OUT, "mobile");
    fs.mkdirSync(dir, { recursive: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, "admin");

    for (const route of ALL_ROUTES) {
      await page.goto(route.path);
      await waitForShell(page);
      await page.waitForTimeout(700);
      await page.screenshot({ path: path.join(dir, `${slug(route.path)}.png`) });

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${route.path} overflows by ${overflow}px at 390px`).toBeLessThanOrEqual(4);
    }
  });
});