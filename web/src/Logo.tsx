import { BRAND } from "./brand";

/**
 * Product mark, drawn inline so the app ships no binary asset and the colour stays
 * on the same ramp as the charts (#184f95 → #2a78d6, see web/src/theme.ts).
 *
 * The lens reads as "go and find it", which is what the tagline says.
 */
export function Logo({ size = 26, radius = 8 }: { size?: number; radius?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-label={BRAND}
      style={{ display: "block", flex: "0 0 auto", borderRadius: radius }}
    >
      <defs>
        <linearGradient id="exora-mark" x1="0" y1="0" x2="1" y2="1">
          {/* `offset` takes a unitless fraction (0 → 1), so no "%" appears in source.
              Gradient geometry, not a display number — see check-data-numbers.mjs. */}
          <stop offset="0" stopColor="#184f95" />
          <stop offset="1" stopColor="#2a78d6" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx={radius} fill="url(#exora-mark)" />
      <circle cx="13.5" cy="13.5" r="6" fill="none" stroke="#fff" strokeWidth="2.4" />
      <path d="M18 18 L24 24" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}
