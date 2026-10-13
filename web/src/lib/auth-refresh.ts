/**
 * Re-read the identity after a change in session state.
 *
 * Refine caches the current user under `["auth", { action: "identity" }]` and derives
 * `check`/`permissions` from it. Its own `useLogin`/`useLogout` hooks call
 * `invalidateAuthStore()` for exactly this reason — but this app drives login directly
 * (see pages/Login.tsx for why) so it has to do the same job itself.
 *
 * Without this, signing in left the app in a half-authenticated state: the cookie was
 * set, but the shell still had the cached anonymous identity, so the header showed no
 * name and the sign-out control was absent.
 */

/** Every key Refine derives from the identity query. */
const AUTH_KEYS = [["auth"], ["check"], ["identity"], ["permissions"]] as const;

export async function refreshAuthCache(queryClient: {
  invalidateQueries: (filters: { queryKey: readonly unknown[] }) => Promise<unknown>;
}) {
  await Promise.all(AUTH_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

/** Drop every cached response, for use immediately after signing out. */
export async function clearAuthCache(queryClient: {
  clear: () => void;
  invalidateQueries: (filters: { queryKey: readonly unknown[] }) => Promise<unknown>;
}) {
  queryClient.clear();
  await Promise.all(AUTH_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}