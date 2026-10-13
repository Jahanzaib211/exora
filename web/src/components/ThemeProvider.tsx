import { BulbOutlined, BulbFilled } from "@ant-design/icons";
import { ConfigProvider, type ThemeConfig } from "antd";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { applyStoredScheme, buildTheme, readScheme, writeScheme, type ColourScheme } from "../theme";

/**
 * Colour-scheme provider.
 *
 * antd's algorithm swap is the right mechanism, but three hardcoded hexes in
 * `Shell.tsx`/`Login.tsx` and six greys scattered across pages meant a dark theme
 * would have needed a per-component rewrite. Those are gone; everything now derives
 * from `buildTheme()`, so switching is one state change.
 */

type Ctx = { scheme: ColourScheme; toggle: () => void };
const ThemeModeContext = createContext<Ctx>({ scheme: "light", toggle: () => {} });

export const useThemeMode = () => useContext(ThemeModeContext);

export function ExoraThemeProvider({ children }: { children: ReactNode }) {
  const [scheme, setScheme] = useState<ColourScheme>(() => applyStoredScheme());

  useEffect(() => {
    writeScheme(scheme);
  }, [scheme]);

  const toggle = useCallback(() => setScheme((s) => (s === "dark" ? "light" : "dark")), []);

  const value = useMemo(() => ({ scheme, toggle }), [scheme, toggle]);

  return (
    <ThemeModeContext.Provider value={value}>
      <ScopedTheme scheme={scheme}>{children}</ScopedTheme>
    </ThemeModeContext.Provider>
  );
}

function ScopedTheme({ scheme, children }: { scheme: ColourScheme; children: ReactNode }) {
  const themeConfig: ThemeConfig = useMemo(() => buildTheme(scheme), [scheme]);
  // antd v5 generates component styles from this token tree via CSS-in-JS, so there is
  // nothing to bridge into custom properties. Plain CSS gets its light/dark values
  // from the `[data-scheme]` selectors in index.css instead.
  return <ConfigProvider theme={themeConfig}>{children}</ConfigProvider>;
}

/** Header control for switching between light and dark. */
export function ThemeToggle() {
  const { scheme, toggle } = useThemeMode();
  const next = scheme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      className="theme-toggle"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      data-testid="theme-toggle"
    >
      {scheme === "dark" ? <BulbOutlined /> : <BulbFilled />}
    </button>
  );
}

export { readScheme };