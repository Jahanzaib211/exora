import { theme as antdTheme, type ThemeConfig } from "antd";

/**
 * One palette, defined once.
 *
 * The codebase previously carried three different blues (#2f5bea primary, #184f95/#2a78d6
 * in charts, and antd's stock #1677ff leaking through because `colorLink` was never
 * overridden) and six near-identical greys across `theme.ts`, `index.css`, `Shell.tsx`,
 * `Login.tsx`, `Engine.tsx` and `Tasks.tsx`. Anything not in `theme.ts` was a hardcoded
 * hex, which is why dark mode would have required rewriting every component.
 *
 * Everything visual now comes from these tokens or from antd's `useToken()`.
 */

export const BRAND_BLUE = "#2f5bea";
export const NAVY = "#0f1b2d";

/** Chart series colours — one set, reused by every chart in the app. */
export const SERIES = ["#2f5bea", "#14995b", "#d48806", "#8b5cf6", "#0ea5e9", "#d4380d"];

export type ColourScheme = "light" | "dark";

const LIGHT_TOKENS = {
  colorPrimary: BRAND_BLUE,
  colorInfo: BRAND_BLUE,
  colorSuccess: "#14995b",
  colorWarning: "#d48806",
  colorError: "#d4380d",
  colorBgLayout: "#f4f6fa",
  colorBgContainer: "#ffffff",
  colorBgElevated: "#ffffff",
  colorBorderSecondary: "#eef0f4",
  colorTextBase: "#1b2433",
  colorTextSecondary: "#5b6577",
  colorLink: BRAND_BLUE,
  colorLinkHover: "#1f47c4",
} as const;

const DARK_TOKENS = {
  colorPrimary: "#4d78ff",
  colorInfo: "#4d78ff",
  colorSuccess: "#2fbe74",
  colorWarning: "#e0a33a",
  colorError: "#e8573a",
  colorBgLayout: "#0d1420",
  colorBgContainer: "#151d2b",
  colorBgElevated: "#1b2434",
  colorBorderSecondary: "#26303f",
  colorTextBase: "#e6ebf2",
  colorTextSecondary: "#96a2b5",
  colorLink: "#6d92ff",
  colorLinkHover: "#8fadff",
} as const;

/**
 * Build the antd theme for a scheme.
 * `compactAlgorithm` is layered on top of the light/dark pair, not the base algorithm.
 */
export function buildTheme(scheme: ColourScheme): ThemeConfig {
  const dark = scheme === "dark";
  return {
    algorithm: [dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm, antdTheme.compactAlgorithm],
    token: {
      ...(dark ? DARK_TOKENS : LIGHT_TOKENS),
      borderRadius: 6,
      fontFamily:
        "'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, 'Noto Sans', 'Noto Naskh Arabic', sans-serif",
      fontSize: 13,
      wireframe: false,
    },
    components: {
      Layout: {
        siderBg: NAVY,
        headerBg: dark ? "#151d2b" : "#ffffff",
        bodyBg: dark ? "#0d1420" : "#f4f6fa",
        headerHeight: 52,
        headerPadding: "0 20px",
      },
      Menu: {
        darkItemBg: NAVY,
        darkSubMenuItemBg: NAVY,
        darkItemSelectedBg: "#22385c",
        darkGroupTitleColor: "#7d8ba3",
        darkItemColor: "rgba(255,255,255,0.78)",
        darkItemHoverBg: "#182a45",
        itemHeight: 34,
      },
      Table: {
        headerBg: dark ? "#1b2434" : "#f7f8fb",
        headerColor: dark ? "#96a2b5" : "#5b6577",
        rowHoverBg: dark ? "#1c2738" : "#f3f6ff",
        cellPaddingBlock: 10,
      },
      Card: { headerFontSize: 14 },
      Statistic: { titleFontSize: 12, contentFontSize: 22 },
      Button: { fontWeight: 500 },
      Tag: { defaultBg: dark ? "#1b2434" : "#fafafa" },
      Descriptions: { labelBg: dark ? "#1b2434" : "#fafafa" },
    },
  };
}

/** Default export kept so existing imports (`import { appTheme } from "../theme"`) work. */
export const appTheme = buildTheme("light");

/* ---------------------------------------------------------- scheme storage */

const SCHEME_KEY = "exora.scheme";

export function readScheme(): ColourScheme {
  if (typeof window === "undefined") return "light";
  try {
    const stored = window.localStorage.getItem(SCHEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage blocked */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function writeScheme(scheme: ColourScheme) {
  try {
    window.localStorage.setItem(SCHEME_KEY, scheme);
  } catch {
    /* storage blocked */
  }
  document.documentElement.dataset.scheme = scheme;
  document.documentElement.style.colorScheme = scheme;
}

/** Apply the scheme to <html> before first paint to avoid a flash. */
export function applyStoredScheme() {
  const scheme = readScheme();
  document.documentElement.dataset.scheme = scheme;
  document.documentElement.style.colorScheme = scheme;
  return scheme;
}