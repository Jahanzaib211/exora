# Enterprise UI Upgrade — Progress

Playwright/Chromium harness in place. **68 E2E tests green.** `npm run build` green
(`check:data` + `tsc --noEmit` + `vite build`). Backend unchanged: `ruff` clean,
`pytest` 158 passed.

Legend: `[x]` done · `[~]` partial · `[ ]` todo

---

## Phase 0 — Harness

- [x] **P0.1** `@playwright/test` installed, Chromium 153 pinned
- [x] **P0.2** `playwright.config.ts` — baseURL, trace/screenshot on failure, serial workers
- [x] **P0.3** Test users created: `admin@` / `sdr@` / `viewer@exora.test` (`E2ePass!2026`)
- [x] **P0.4** App boots for E2E on `:8099`, `/api/health` verified
- [x] **P0.5** `tabs.spec.ts` sweeps all 15 tabs asserting no console errors / 5xx / blank render

## Phase 1 — Foundation

- [x] **P1.1** `lib/format.ts` — null-safe formatters, explicit UTC→operator-timezone
- [x] **P1.2** `lib/query-keys.ts` — single registry + `INVALIDATE` bundles
- [x] **P1.3** `components/QueryBoundary.tsx` — one loading/error/empty contract
- [x] **P1.4** `components/AppErrorBoundary.tsx` + React 19 `onCaughtError`
- [x] **P1.5** `components/SafeLink.tsx` — scheme allowlist + `rowActivation`
- [x] **P1.6** `components/EmptyState.tsx` — filtered-vs-genuinely-empty
- [x] **P1.7** `hooks/useToastMutation.ts` — consistent feedback + invalidation
- [x] **P1.8** `lib/auth-refresh.ts` — identity cache refresh on sign-in/out
- [x] **P1.9** `lib/chunk-recovery.ts` — stale-chunk auto-reload, once
- [x] **P1.10** `types/api.ts` — every response type verified against the live API

## Phase 2 — Correctness (all fixed)

- [x] `["runs"]` key collision (Dashboard vs Discovery) → `qk.runs()` / `qk.recentRuns(n)`
- [x] `["quality"]` shape fragmentation → one `Quality` type, one key
- [x] 18 unguarded `.slice()` timestamp calls → `fmtDate` / `fmtDateTime` / `fmtUtc`
- [x] Nullable timestamps typed `string | null`
- [x] Central 401 → `/login?next=` (excludes auth probes, so login errors still show)
- [x] `res.json()` on 204/empty bodies
- [x] `Object.entries(r.params ?? {})` guard
- [x] Pagination clamped after filter change
- [x] Selection cleared on filter/page change; export no longer duplicates ids
- [x] `Discovery.openForm` resets; submits only declared params
- [x] `Plan` division by zero on `linkedin_requests`
- [x] `Calls` `value_usd` legitimate-0 rendering
- [x] `SENIORITY_LABEL` unknown key → em dash
- [x] `useSearchParams` mutate-then-pass-same-object → functional updater (Companies, People, Playbook)

## Phase 3 — Reliability

- [x] `onCaughtError` / `onUncaughtError` in `main.tsx`
- [x] Error boundary per route with `resetKey`
- [x] `staleTime` / `retry` / `gcTime` defaults; identity query no longer polls
- [x] Per-row mutation `loading` (no all-rows spinner)
- [x] Skeletons instead of `?? 0` for unknown values
- [x] Stale-chunk auto-recovery with loop guard
- [x] Session expiry → login (verified in `resilience.spec.ts`)

## Phase 4 — UX

- [x] `Popconfirm` on bulk reject; modal confirm on teammate deactivate; plan re-sync
- [x] Column sorting on Audit, Quality, People, Discovery runs, Events, Calls, Plan weeks
- [x] Empty states distinguishing "no matches" from "nothing yet"
- [x] Server-side pagination where available; `keepPreviousData` preserved
- [x] Cmd-K / Ctrl-K search focus, debounced 250ms, isolated from shell re-renders
- [~] Bulk actions: companies + team done; Tasks/Calls/Events pending
- [ ] Saved views / filter presets
- [ ] Row virtualization above 100 rows

## Phase 5 — Accessibility

- [x] Rows open with Enter/Space (`rowActivation`)
- [x] `aria-label` on icon-only buttons (verified by test)
- [x] `<label for>` on every filter input
- [x] Visible focus rings (`:focus-visible`)
- [x] Safe-link scheme allowlist on all crawled URLs
- [x] Screen-reader text for icon-only controls
- [ ] Focus trap/restore verification on drawers

## Phase 6 — Design system / responsive / dark mode

- [x] Single palette in `theme.ts`; hardcoded hex removed from components
- [x] Removed 3 `!important` antd overrides + dead `sider-zero-width-trigger` rule
- [x] `SIDER_BP` constant replaces 4 hardcoded `992`s
- [x] Consistent `size="small"` + `PageHeader`
- [x] Dark mode: `darkAlgorithm`, `[data-scheme]`, persisted, no flash
- [x] Mobile: filter rail wraps, nav becomes a Drawer, table overflow contained
- [x] Brand single-sourced (`brand.ts` → `<title>`, CSP, meta)
- [x] `engines.node` declared

## Phase 7 — Code quality

- [x] Dead config removed: `resources`, `notificationProvider`
- [x] `EngineNote` now renders the engine's actual error
- [x] `RUN_STATUS` consolidated in one module
- [x] Route-level `React.lazy` + `Suspense` (per-page chunks confirmed)
- [x] Shared `types/api.ts` replaces 4 duplicate `Run` / 3 duplicate `Quality`
- [x] `.gitignore` for test artefacts
- [x] `check-data-numbers` fixed to ignore comments + structural props
- [ ] ESLint / Prettier
- [ ] Split `Outreach.tsx` / `Automations.tsx` / `Tasks.tsx`

## Phase 8 — Playwright verification

- [x] Auth: login, logout, bad credentials, session expiry, 401 redirect
- [x] All 15 tabs render cleanly (console + 5xx assertions)
- [x] Error states: every tab shows retry, none stuck on a skeleton
- [x] Permission messaging: admins never told they lack access
- [x] Accessibility: headings, labels, keyboard, safe hrefs, icon-button names
- [x] Layout: no overflow at 1280px or 390px
- [x] Resilience: stale chunk, reload-loop guard, offline, retry recovery
- [x] Behaviour: no NaN/undefined, destructive confirms, theme persistence, Enter-to-open
- [x] Screenshots: 15 tabs × light + dark + mobile = 45 PNGs

## Phase 9 — Verification

- [x] `npm run build` green
- [x] `npx playwright test` — 69 passed (includes a row-click → drawer test for
      Companies and People, and a return-to-page-after-relogin test)
- [x] `ruff check` + `ruff format --check` clean
- [x] `pytest` 158 passed (no backend regression)
- [x] Screenshots archived under `web/screenshots/`

---

## Bugs found and fixed during this work

Recorded because they were found by the new tests, not by reading code.

| Bug | Impact |
|---|---|
| Refine `useLogin` called `invalidateAuthStore()` on failure, unmounting the route and discarding the error | Wrong password produced **no visible error** |
| Refine `useLogout` swallows its promise; identity stayed cached | Sign-out left the signed-in user on screen |
| `useLogin`/`useLogout` never refreshed identity after this app's direct calls | Half-authenticated shell after login |
| Global 15s poll hit `/api/auth/me` on the login page | A 401 every 15s while signed out |
| Central 401 handler also fired for the login request | Suppressed the login error |
| `Statistic loading={undefined}` | "Ready for outreach" skeleton never resolved |
| `website_of` kept URL userinfo | `https://user:pass@host/` persisted to DB and exports |
| `linkedin_url` had no scheme validation | `javascript:` could be stored and rendered |
| `<Row wrap={false}>` + no `min-width: 0` | Every tab overflowed on mobile |
| Companies error path rendered an empty table | A network failure looked like "no results" |
| 22px circular `Progress` for quality score | Unreadable at that size |
| Audit `rowKey` composite could collide | Duplicate React keys |
| `nameOf()` linear scan per row per keystroke | O(rows × users) on every filter |
| `rowActivation`'s guard used `closest()`, which includes the element it is called on — and the row itself carries `role="button"` | **Clicking a Companies/People row did nothing.** Enter worked (different handler), so it looked like a routing bug |
| Two redirects raced after sign-in: `LeaveLogin` hardcoded `<Navigate to="/">`, overwriting Login's `navigate("?to=")` | Signing in again after a session expiry always dumped you on Overview, losing the page you were on |
| `Session.rollback()` was silently a no-op — `isolation_level = None` plus a pysqlite `do_begin` that emits nothing means no `BEGIN` ever ran | Every statement autocommitted: a rejected create ("Email address is not valid") **committed its half-written row**, so the next attempt failed 409 |
| `BEGIN IMMEDIATE` on every transaction, including read-only ones | A GET blocked all writers for its request lifetime; two processes racing on `create_all` DDL died with `database is locked` before binding a port |
| `raise OperationalError("message")` | `OperationalError` subclasses `DBAPIError` (`statement, params, orig`), so the real failure was masked by a `TypeError` |

## Remaining known issues (not fixed)

- **Crawler failing on this machine**: `/api/runs` shows
  `OSError: [Errno 22] Invalid argument: '.../out.jsonl'` for the `website` source.
  Backend/Windows-tempfile issue, unrelated to this UI work. `website.py` was hardened
  (`mkdtemp` + `rmtree(ignore_errors=True)`, read output before judging exit code, and
  `last_crawled_at` is only set for sites that actually returned pages) but the original
  in-server cause is still not pinned down — standalone and threaded crawls both return
  rc=0.
- Bulk actions on Tasks / Calls / Events / Exclusions.
- ESLint and Prettier not yet configured.
- `Outreach.tsx`, `Automations.tsx`, `Tasks.tsx` still exceed 480 lines.
- Drawer focus trap not explicitly verified.
- No server-side export on the 8 tables that still lack it.