/**
 * Deployment settings the UI needs before it can render anything: the organisation name
 * shown under the logo, the operator timezone every date is printed in, and whether the
 * optional outreach engine is configured.
 *
 * They come from the public `/api/config` endpoint rather than the bundle, so one build
 * serves any install. The call is awaited once, before the first render, with a short
 * timeout: a slow or failing API still gets the app on screen with neutral defaults
 * (no organisation line, the browser's timezone) instead of a blank page.
 *
 * Only main.tsx imports this module. Pages read the values from brand.ts / lib/format.ts.
 */
import { setOrg, setOutreachEnabled } from "./brand";
import { setOperatorTz } from "./lib/format";

export interface RuntimeConfig {
  brand: string;
  tagline: string;
  org: string;
  timezone: string;
  version: string;
  outreach: boolean;
}

export async function loadRuntimeConfig(timeoutMs = 3000): Promise<void> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch("/api/config", { signal: controller.signal, credentials: "same-origin" });
    if (!res.ok) return;
    const cfg = (await res.json()) as Partial<RuntimeConfig>;
    setOrg(cfg.org);
    setOperatorTz(cfg.timezone);
    if (typeof cfg.outreach === "boolean") setOutreachEnabled(cfg.outreach);
  } catch {
    // Offline, aborted or not JSON: keep the defaults.
  } finally {
    window.clearTimeout(timer);
  }
}
