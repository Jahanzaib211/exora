/**
 * Recovery for stale lazy chunks after a redeploy.
 *
 * Vite names each lazy chunk with a content hash, and `npm run build` deletes the
 * previous ones. A tab that was already open across a deploy therefore holds a
 * reference to a file that no longer exists, and the next `import()` fails with:
 *
 *   Failed to fetch dynamically imported module: /assets/Discovery-DKovhOUY.js
 *
 * The error surfaces as a route-level crash, which is correct but unhelpful: the fix
 * is trivial (fetch the new index.html and reload) yet nothing did it. This module
 * detects that specific failure and reloads once.
 *
 * The `sessionStorage` guard is essential. Without it, a chunk that genuinely cannot
 * be fetched — a proxy stripping requests, a corrupt deploy, an offline client — would
 * put the tab in an infinite reload loop.
 */

const KEY = "exora.chunk-reload";

/** Matches the dynamic-import failure across browsers. */
function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const message = error.message;
  return (
    /Failed to fetch dynamically imported module/i.test(message) ||
    /Importing a module script failed/i.test(message) ||
    /error loading dynamically imported module/i.test(message) ||
    // Vite surfaces a preload-link failure as a bare "Unable to preload CSS".
    /Unable to preload CSS/i.test(message) ||
    /ChunkLoadError/i.test(error.name)
  );
}

/**
 * Reload once if this error is a stale chunk. Returns true when a reload was issued,
 * so callers can avoid also rendering the error screen.
 */
export function recoverFromStaleChunk(error: unknown): boolean {
  if (!isChunkLoadError(error)) return false;
  if (typeof window === "undefined") return false;

  let alreadyTried = false;
  try {
    alreadyTried = window.sessionStorage.getItem(KEY) === "1";
    // Arm the guard for the coming reload.
    window.sessionStorage.setItem(KEY, "1");
  } catch {
    // Private mode / storage disabled: still attempt one reload, but we cannot
    // remember that we did, so cap it by the sessionStorage failure itself.
  }

  if (alreadyTried) return false;

  // `location.reload()` re-fetches index.html, which carries the new chunk hashes.
  window.location.reload();
  return true;
}

/** Clear the guard once the app is healthy again, so a later deploy can recover too. */
export function clearStaleChunkGuard() {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked */
  }
}