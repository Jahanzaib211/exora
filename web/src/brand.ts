/**
 * Product name in one place. The backend mirrors it in src/prospector/branding.py —
 * keep the two in step (BRAND feeds the API title, the CLI help and the exports).
 *
 * BRAND is the product. ORG is the company running this install, shown as the sidebar
 * and sign-in subtitle. It is deployment config (PROSPECTOR_ORG_NAME), not code: it is
 * filled in from `/api/config` before the first render (see runtimeConfig.ts) and stays
 * empty — and hidden — when unset.
 */
export const BRAND = "Exora";
export const TAGLINE = "Find what's out there.";
export let ORG = "";

export function setOrg(name: string | null | undefined): void {
  ORG = (name ?? "").trim();
}
