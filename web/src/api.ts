// API client, auth provider and a minimal data provider for Refine.
// Session = HttpOnly cookie set by the server; every write sends X-Requested-With (CSRF guard).
import type { AuthProvider, DataProvider } from "@refinedev/core";

export class ApiError extends Error {
  status: number;
  /** Set for 409 conflicts, where the body carries `{message, existing_id}`. */
  existingId?: number;
  constructor(status: number, message: string, existingId?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.existingId = existingId;
  }
}

/**
 * A 401 on a normal request means the session is gone: redirect to login once.
 *
 * Exceptions, both of which caused real breakage:
 *  - The login request itself. `/api/auth/login` legitimately answers 401 for bad
 *    credentials, and Refine renders that as an inline error on the form. Treating
 *    it as an expired session made the error message never appear.
 *  - The identity probe `/api/auth/me`, which is *expected* to 401 before sign-in.
 */
function isAuthProbe(path: string): boolean {
  return path.startsWith("/api/auth/me") || path.startsWith("/api/auth/login") || path.startsWith("/api/auth/logout");
}

let redirecting = false;
export function resetUnauthorizedGuard() {
  redirecting = false;
}

function handleUnauthorized(path: string) {
  if (redirecting) return;
  if (isAuthProbe(path)) return;
  // Already on the sign-in screen: nothing to redirect to, and the login form
  // owns its own error display.
  if (window.location.pathname.startsWith("/login")) return;
  redirecting = true;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.assign(`/login?next=${next}`);
}

/** Parse a FastAPI error body into a readable message. */
async function readError(res: Response): Promise<{ message: string; existingId?: number }> {
  let raw: unknown;
  try {
    raw = await res.json();
  } catch {
    return { message: res.statusText || `Request failed (${res.status})` };
  }
  const detail = (raw as { detail?: unknown })?.detail;
  if (typeof detail === "string") return { message: detail };
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; existing_id?: unknown };
    // FastAPI validation errors arrive as an array of {loc, msg}.
    if (Array.isArray(detail)) {
      const parts = (detail as { loc?: unknown[]; msg?: string }[])
        .map((e) => (e.loc ? `${e.loc.filter((x) => x !== "body").join(".")}: ${e.msg}` : e.msg))
        .filter(Boolean);
      if (parts.length) return { message: parts.join("; ") };
    }
    if (typeof d.message === "string") {
      return { message: d.message, existingId: typeof d.existing_id === "number" ? d.existing_id : undefined };
    }
  }
  return { message: JSON.stringify(detail ?? raw) };
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "prospector",
        ...(init.headers || {}),
      },
    });
  } catch (e) {
    // Network-level failure: keep the message actionable rather than "Failed to fetch".
    throw new ApiError(0, navigator.onLine ? "Could not reach the server." : "You appear to be offline.", undefined);
  }

  if (res.status === 401) {
    handleUnauthorized(path);
  }

  if (!res.ok) {
    const { message, existingId } = await readError(res);
    throw new ApiError(res.status, message, existingId);
  }

  // 204/205 and empty bodies are valid success responses; `res.json()` would throw
  // "Unexpected end of JSON input" and surface as a bogus failure toast.
  if (res.status === 204 || res.status === 205) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

/**
 * Fetch a generated file (CSV/XLSX) and hand it to the browser.
 * Returns false when the download was rejected so callers can raise a toast.
 */
export async function download(path: string, fallbackName: string): Promise<boolean> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "X-Requested-With": "prospector" },
  });
  if (!res.ok) {
    const { message } = await readError(res);
    throw new ApiError(res.status, message);
  }
  const blob = await res.blob();
  // Prefer the server's filename so exports match what the API calls them.
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick; revoking synchronously cancels the download in Safari.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}

export type Me = { id: number; email: string; name: string; role: "admin" | "sdr" | "viewer"; active: boolean };

export const authProvider: AuthProvider = {
  login: async ({ email, password }) => {
    try {
      await post("/api/auth/login", { email, password });
      return { success: true, redirectTo: "/" };
    } catch (e) {
      return { success: false, error: { name: "Sign-in failed", message: (e as Error).message } };
    }
  },
  logout: async () => {
    try {
      await post("/api/auth/logout");
    } catch {
      // Clearing the client-side cookie is still the right outcome if the call fails.
    }
    return { success: true, redirectTo: "/login" };
  },
  check: async () => {
    // A fresh attempt after a previous redirect, so a second expiry is noticed.
    resetUnauthorizedGuard();
    try {
      await api("/api/auth/me");
      return { authenticated: true };
    } catch {
      return { authenticated: false, redirectTo: "/login", logout: true };
    }
  },
  onError: async (error) => {
    if ((error as ApiError)?.status === 401) return { logout: true, redirectTo: "/login" };
    return {};
  },
  getIdentity: async () => {
    try {
      return await api<Me>("/api/auth/me");
    } catch {
      return null;
    }
  },
  getPermissions: async () => {
    try {
      return (await api<Me>("/api/auth/me")).role;
    } catch {
      return null;
    }
  },
};

/**
 * Pages call the JSON API directly through `api()`; this provider exists only to
 * satisfy Refine's type contract and is never invoked.
 */
const notUsed = () => Promise.reject(new Error("not used: pages call the API directly"));
export const dataProvider: DataProvider = {
  getApiUrl: () => "/api",
  getList: notUsed,
  getOne: notUsed,
  create: notUsed,
  update: notUsed,
  deleteOne: notUsed,
};