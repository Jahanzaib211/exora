/**
 * Where a user should land once they have signed in.
 *
 * Two different components ask for this and they use different parameter names, so
 * reading only one of them silently loses the destination:
 *
 *   - Refine's `<Authenticated>` fallback redirects with `?to=`
 *   - api.ts's central 401 handler redirects with `?next=`
 *
 * The value is only trusted when it is a same-origin path; an absolute URL here would
 * turn the login form into an open redirect, and `//host` would too (browsers treat it
 * as protocol-relative).
 */
export function returnTo(params: URLSearchParams): string | null {
  const dest = params.get("next") ?? params.get("to");
  if (!dest) return null;
  return dest.startsWith("/") && !dest.startsWith("//") ? dest : null;
}
