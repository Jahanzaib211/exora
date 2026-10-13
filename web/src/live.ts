// Central live-refresh policy — the single place that decides how often data refetches.
//
// Refine's <Refine> container builds THE app's one QueryClient from
// `options.reactQuery.clientConfig` and wraps the tree in a single
// QueryClientProvider (see node_modules/@refinedev/core/dist/index.mjs,
// `var Refine = ...` -> `new QueryClient({...})` -> `<QueryClientProvider client=...>`).
// We pass `liveQueryClientConfig` from App.tsx, so these defaults reach every
// useQuery() in the app — including pages that never mention refetchInterval,
// queries added later, and Refine's own identity query.
//
// Keep every interval number here; pages must reference REFRESH.*, never a literal.
import type { QueryClientConfig } from "@tanstack/react-query";

export const REFRESH = {
  /** Operational queues: discovery runs that are queued/running right now. */
  operational: 3000,
  /** Dashboards and operational readouts: Overview, Analytics, Quality, Calls, Routine. */
  dashboard: 10_000,
  /** List screens, facets and detail drawers — also the app-wide safety-net default. */
  lists: 15_000,
  /** Slow-moving reference data: plan, events, team, discovery sources. */
  background: 30_000,
  /** Static content that cannot change on its own (Docs markdown): never poll. */
  static: false,
} as const;

/**
 * Keys that must never poll.
 *
 * Refine drives the identity query (`/api/auth/me`) through the same QueryClient,
 * so the global `refetchInterval` applied to it too. Anyone sitting on the login
 * screen generated a 401 every 15 seconds, which both spammed the console and made
 * the sign-in page look broken. Identity is re-checked on navigation and on any real
 * 401 (see api.ts), which is the correct trigger.
 */
/**
 * Refine builds its identity query key as `["auth", { action: "identity" }]`, so the
 * first element is the discriminator.
 */
export const liveQueryClientConfig: QueryClientConfig = {
  defaultOptions: {
    queries: {
      // Retry transient failures (network blips, 5xx) but never retry a decision the
      // server has already made: a 4xx will fail identically every time.
      retry: (failureCount, error) => {
        const status = (error as { status?: number })?.status;
        if (status !== undefined && status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
      // Safety net: any query without an explicit refetchInterval still streams live.
      refetchInterval: (query) => {
        if (query.queryKey[0] === "auth") return false;
        return REFRESH.lists;
      },
      // An unfocused/hidden tab must never hammer the API.
      refetchIntervalInBackground: false,

      // Without this, staleTime defaults to 0: every mount and every window focus
      // refetched the whole page, and two components sharing a key would briefly
      // render each other's payload.
      staleTime: 5_000,

      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),

      // Keep a page's data around briefly so navigating back is instant, but don't
      // let the cache grow without bound on a long-lived dashboard session.
      gcTime: 5 * 60_000,
    },
    mutations: {
      // A failed write is worth retrying once for network reasons only.
      retry: 0,
    },
  },
};