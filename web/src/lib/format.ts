/**
 * Formatting helpers — the single place that turns API values into display strings.
 *
 * Why this exists: the codebase previously had ~18 hand-rolled copies of
 * `iso.slice(0, 16).replace("T", " ")` scattered across pages. Every one of them
 * threw on a null timestamp, and none of them agreed on timezone. One null
 * `retrieved_at` in one crawled row was enough to blank the whole app.
 *
 * Timezone contract
 * -----------------
 * The backend stores naive UTC (`utcnow()` in models.py) and emits ISO strings with
 * no offset, e.g. "2026-10-04T02:14:33.123456". `new Date()` on that string would
 * treat it as *local* time, which is wrong by the UTC offset. These helpers parse it
 * as UTC explicitly and render in the operator's timezone, matching
 * `Settings.operator_timezone` on the server (PROSPECTOR_OPERATOR_TIMEZONE), which
 * `/api/config` hands over before the first render (see runtimeConfig.ts).
 */

import { BRAND } from "../brand";

/** The server's `operator_timezone`. Until `/api/config` answers, the browser's own zone. */
export let OPERATOR_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function setOperatorTz(zone: string | null | undefined): void {
  if (!zone) return;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: zone });
    OPERATOR_TZ = zone;
  } catch {
    // An unknown zone from a misconfigured server keeps the browser's zone rather than breaking dates.
  }
}

/** Rendered wherever a value is missing. A blank cell reads as a bug; an em dash reads as "none". */
export const DASH = "—";

/** Parse a naive-UTC ISO string from the API into a real Date. Returns null if unusable. */
export function parseApiDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  // A bare date ("2026-10-04") is treated as UTC midnight, consistent with the API.
  const normalised = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalised);
  const d = new Date(hasZone ? normalised : `${normalised}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmt(
  value: string | null | undefined,
  opts: Intl.DateTimeFormatOptions,
  fallback: string,
): string {
  const d = parseApiDate(value);
  if (!d) return fallback;
  try {
    return new Intl.DateTimeFormat("en-GB", { timeZone: OPERATOR_TZ, ...opts }).format(d);
  } catch {
    // An invalid timeZone must not take the page down.
    return new Intl.DateTimeFormat("en-GB", opts).format(d);
  }
}

/** `2026-10-04` -> `04 Oct 2026`. Null-safe. */
export function fmtDate(value: string | null | undefined): string {
  return fmt(value, { day: "2-digit", month: "short", year: "numeric" }, DASH);
}

/** `2026-10-04T02:14:33` -> `04 Oct 2026, 02:14`. Null-safe. */
export function fmtDateTime(value: string | null | undefined): string {
  return fmt(value, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }, DASH);
}

/** Exact UTC stamp, for audit trails where an unambiguous instant matters. */
export function fmtUtc(value: string | null | undefined): string {
  const d = parseApiDate(value);
  if (!d) return DASH;
  return `${d.toISOString().slice(0, 19).replace("T", " ")}Z`;
}

/** Coarse relative time ("3m ago", "in 2d"). Falls back to an absolute date past a week. */
export function relTime(value: string | null | undefined, now: Date = new Date()): string {
  const d = parseApiDate(value);
  if (!d) return DASH;
  const deltaSec = Math.round((d.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(deltaSec);
  if (abs < 45) return deltaSec < 0 ? "just now" : "in a moment";
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86_400, "hour"],
    [604_800, "day"],
    [2_629_800, "week"],
    [31_557_600, "month"],
  ];
  const div = units.find(([limit]) => abs < limit);
  if (!div) return fmtDateTime(value);
  const [limit, unit] = div;
  const amount = Math.round(deltaSec / limit);
  const text = new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(-amount, unit);
  // `format` already renders the sign ("2 days ago" / "in 2 days"); normalise to the past tense.
  return deltaSec < 0 ? text.replace(/^in /, "") : text;
}

/** Days until a date; negative when past. Null-safe. */
export function daysUntil(value: string | null | undefined, now: Date = new Date()): number | null {
  const d = parseApiDate(value);
  if (!d) return null;
  const a = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const b = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((a - b) / 86_400_000);
}

/* ------------------------------------------------------------------ numbers */

const NBSP = " ";

/** Locale-grouped integer. `1234` -> `1,234`. Null-safe. */
export function fmtNum(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return new Intl.NumberFormat("en-GB").format(value);
}

/**
 * Percentage from a 0-1 or 0-100 source, clamped and NaN-safe.
 * The backend is inconsistent about which it sends, so callers state which with `scale`.
 */
export function fmtPct(value: number | null | undefined, scale: 100 | 1 = 1, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const n = value * scale;
  return `${n.toFixed(digits)}%`;
}

/** A 0-1 ratio as a whole-number percentage — the common case for `*_pct` API fields. */
export function fmtRatioPct(value: number | null | undefined, digits = 0): string {
  return fmtPct(value, 100, digits);
}

const compact = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return compact.format(value);
}

/** Money. Handles 0 correctly (the old `x ? … : ""` pattern swallowed it). */
export function fmtMoney(value: number | null | undefined, currency = "USD", digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(digits)}`;
  }
}

/** Hours, trimmed: `2` -> `2 h`, `1.5` -> `1.5 h`. */
export function fmtHours(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${Number(value.toFixed(1))}${NBSP}h`;
}

/** Days, trimmed. */
export function fmtDays(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${Number(value.toFixed(1))}${NBSP}d`;
}

/** Clamp to 0-100 so antd `Progress` never receives NaN, Infinity or a negative bar. */
export function clampPct(value: number | null | undefined): number {
  if (value === null || value === undefined || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/** `true`/`false` as a tick or cross for table cells. */
export function yesNo(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value ? "Yes" : "No";
}

/** Trim a long string for a table cell. */
export function truncate(text: string | null | undefined, max = 60): string {
  if (!text) return DASH;
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Sentence-case a machine value (`hard_bounce` -> `Hard bounce`) for display. */
export function humanise(value: string | null | undefined): string {
  if (!value) return DASH;
  const s = value.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Turn a filename into a title: `09_plan_and_kpis.md` -> `Plan and kpis`. */
export function titleFromSlug(slug: string): string {
  const base = slug.replace(/\.[a-z0-9]+$/i, "").replace(/^\d+[_-]/, "").replace(/[_-]+/g, " ").trim();
  return base ? base.charAt(0).toUpperCase() + base.slice(1) : slug;
}

/** Pluralise: `pluralise(1,'company')` -> `1 company`, `pluralise(3,'company')` -> `3 companies`. */
export function pluralise(n: number, singular: string, plural?: string): string {
  return `${fmtNum(n)} ${n === 1 ? singular : (plural ?? `${singular}s`)}`;
}

/** `brand.ts` re-export so components can build titles without importing both. */
export { BRAND };