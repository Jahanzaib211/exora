/**
 * Deployment settings the UI needs before it can render anything: the organisation name
 * shown under the logo and the operator timezone every date is printed in.
 *
 * They come from the public `/api/config` endpoint rather than the bundle, so one build
 * serves any install. The call is awaited once, before the first render, with a short
 * timeout: a slow or failing API still gets the app on screen with neutral defaults
 * (no organisation line, the browser's timezone) instead of a blank page.
 */
import { setOrg } from "./brand";
import { setOperatorTz } from "./lib/format";

export interface RuntimeConfig {
  brand: string;
  tagline: string;
  org: string;
  timezone: string;
  version: string;
}

export let APP_VERSION = "";

export async function loadRuntimeConfig(timeoutMs = 3000): Promise<void> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch("/api/config", { signal: controller.signal, credentials: "same-origin" });
    if (!res.ok) return;
    const cfg = (await res.json()) as Partial<RuntimeConfig>;
    setOrg(cfg.org);
    setOperatorTz(cfg.timezone);
    APP_VERSION = cfg.version ?? "";
  } catch {
    // Offline, aborted or not JSON: keep the defaults.
  } finally {
    window.clearTimeout(timer);
  }
}
