import { StrictMode } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { loadRuntimeConfig } from "./runtimeConfig";
import "./index.css";

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

/**
 * React 19 surfaces render errors through these callbacks. Without them a throw in
 * render is silent apart from a blank page — which is exactly how a single null
 * timestamp in a crawled row could take the whole console down with no trace.
 */
function report(kind: string, error: unknown, info: unknown) {
  // eslint-disable-next-line no-console
  console.error(`[${kind}]`, error, info);
  // Hook for Sentry/Datadog later; kept in one place so it is a one-line change.
  (window as unknown as { __exoraError?: (e: unknown) => void }).__exoraError?.(error);
}

/**
 * React 19's `root.render(children, options)` accepts error callbacks at runtime, but
 * the bundled `@types/react` does not yet declare the second parameter. Cast once,
 * here, rather than losing the handlers entirely.
 */
type RootWithOptions = (node: ReactNode, options: Record<string, unknown>) => void;

const root = createRoot(container) as unknown as { render: RootWithOptions };

// Organisation name and operator timezone must be in place before anything renders a date.
void loadRuntimeConfig().finally(() =>
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
    {
      onCaughtError: (error: unknown, info: unknown) => report("caught", error, info),
      onUncaughtError: (error: unknown, info: unknown) => report("uncaught", error, info),
      onRecoverableError: (error: unknown, info: unknown) => {
        // Hydration/style-loader hiccups are recoverable and noisy; log, don't alarm.
        // eslint-disable-next-line no-console
        console.warn("[recoverable]", error, info);
      },
    },
  ),
);