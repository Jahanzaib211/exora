# Exora: scope review

Every request made in the build session, with its status. **Done** means built and verified by tests or a live run. **Partial** means part of it works and the gap is named. **Missing** means not built yet. **Not planned** means deliberately left out, with the reason.

Status as of 2026-10-04 (start of the final pass). The final state is recorded at the end of this file.

## 1. Platform and data

| Request | Status | Notes |
|---|---|---|
| Review every file in the campaign folder | Done | Three reviews: campaign docs, outreach engine, harness evaluation |
| Self-hosted, open-source stack on this laptop | Partial | Python 3.12, Node 24, uv, all libraries installed without admin rights. WSL2 and Docker need an admin install and a reboot (commands given). No local language model is running yet |
| Lead discovery from lawful public sources | Done (back end) | OpenStreetMap, GLEIF, Wikidata, Companies House (needs a free key), add-by-domain, target sheet, job boards |
| News and funding signals per company | Done | GDELT news index + Hacker News connectors, conservative name matching, per-company weekly cap |
| SEC EDGAR (US public companies) | Not planned | Law, accounting and clinic buyers are private firms; public-company filings add little for this ICP |
| Website crawler, chosen by evidence | Done | Scrapy, picked by a bake-off of 8 tools (`scraper-eval/results/BAKEOFF.md`); sitemap, team, contact and about pages, robots.txt obeyed |
| Rich company profile from each crawl | Done (back end) | Description, social profiles, address, technology, email provider, careers page, job board, buying signals (AI, security certification, data protection, WhatsApp, booking, Arabic content) |
| Every field sourced | Done | Field-level source records with URL and date; manual edits recorded under source "manual" |
| **Email and phone as a first-class live feature** | Done | Captured, merged, scored, filterable, KPI-tracked and exported — see section 1a for measured coverage |
| Email validation and phone normalisation | Done | **60 new tests** in `tests/test_enrich_*.py`, all offline (injected resolvers and probes, `httpx.MockTransport`). RFC syntax + IDN→punycode, MX, null-MX, reject-all SPF, A-fallback; `phonenumbers` E.164. See section 1a |
| Verification and quality score | Done | DNS, mail records, contactability, decision-maker, corroboration, registry status |
| Fit score with reasons | Done | ICP rules from docs 01 and 04 |
| "Golden" records for paying clients | Missing | Strict tier plus an "established firm" rule |
| 24/7 automations | Done (back end) | Scheduler, rotating city and keyword lists, auto-crawl of new companies, re-verification, daily snapshots, stuck-run recovery, keep-awake on Windows |
| Runs keep going after this session ends | Missing | Detached server process and start at login |
| Deployment with Coolify | Partial | Built: multi-stage `Dockerfile` (campaign root context), `docker-compose.yml`, `.env.example`, `.dockerignore`, `docs/DEPLOYMENT.md`. **Not build-verified** — Docker is not installed on this machine, so `docker compose up -d --build` still has to be run on the target host |
| Consistent database backups | Done | `scripts/backup.py` uses `VACUUM INTO` (a plain file copy is torn in WAL mode); `--keep N` pruning; 9 tests; documented in `docs/DEPLOYMENT.md` |

### 1a. Email and phone — new live feature

Contact details are first-class data: captured from lawful public sources, merged across them, scored by the
quality checks, filterable from the UI, tracked as a daily KPI and carried into every export — each value kept
with the source and date that produced it.

Measured on the running database (2026-10-04, re-measured after the crawl pass — the table above it was
superseded as the corpus grew from 2,372 to 4,230 companies and 1,167 to 5,934 people):

| Field | Records holding a value |
|---|---|
| Company email (`Company.general_email`) | 546 of 4,230 companies (12.9%) |
| Company phone (`Company.phone`) | 1,663 of 4,230 companies (39.3%) |
| Company LinkedIn (`Company.linkedin_url`) | 572 of 4,230 companies (13.5%) |
| Person email (`Person.email`) | 1,502 of 5,934 people (25.3%) |
| Person phone (`Person.phone`) | **31 of 5,934 people (0.5%) — was 0** |
| Person LinkedIn (`Person.linkedin_url`) | **8 of 5,934 people (0.1%) — was 0** |

| Capability | Status | Where it lives |
|---|---|---|
| Store email and phone on companies and people | Done | `models.py` — `Company.general_email`, `Company.phone`, `Person.email`, `Person.phone` |
| Merge values from every source and keep provenance | Done | `resolve.py` — a differing address is recorded as an `other_email` observation against its source |
| Turn contactability into a quality score | Done | `verify.py` — `has_contact` "Has a business email or phone" (weight 15) and `email_on_domain` "Email is on the company's own domain" (weight 10) |
| Filter from the UI | Done | `prospects_api.py` — `has_email` and `has_phone` on companies; `has_email` on people |
| Track coverage every day | Done | `scheduler.py` — `with_email`, exposed on `/api/dashboard` |
| Export | Done | `exports.py` — CSV, Excel and Word profiles all carry `Email` and `Phone` |
| Phone as an outreach channel | Not planned | Deliberately excluded: marketing by phone needs prior consent in KSA, UK and EU (`00_README_Start_Here.md`, "What is deliberately not included"). Phone stays a data-quality field |

**Delivered in this pass (verified: 60 enrichment tests, full suite 158 passed):**

| Capability | Status | Where it lives |
|---|---|---|
| Email verdicts `valid / risky / invalid / unknown` | Done | `contact/emailcheck.py` — RFC syntax, IDN→punycode, MX lookup, null-MX, reject-all SPF, A-record fallback |
| Phone verdicts `valid / possible / invalid`, E.164 only when actually valid | Done | `contact/phone.py` (`phonenumbers`); used by `normalize.phone_e164` and by the crawler's phone-token judge (judged with the page's region) |
| One verdict per stored contact, with source and `checked_at` | Done | `connectors/contact_checks.py` — a maintenance job exposed as the `contact-check` source; skips recently checked values |
| Opt-in SMTP RCPT probe, 1/s per MX, TTL-cached, null reverse path `<>` | Done, **off by default** | `PROSPECTOR_SMTP_PROBE` |
| Three new free sources with provenance receipts | Done | `github` (commit authors, own-domain addresses only), `sec-formd` (funding signal, issuer address, officers), `france-registry` (SIREN, status; non-diffusable records skipped, birth dates never read) |
| Fetched-at provenance on every receipt | Done | `CompanyRecord.fetched_at` → `Evidence.fetched_at` |
| Pattern guesses with confidence (0.90 → 0.45), catch-all aware | **Built, not wired** | `contact/patterns.py` — see the note below |

#### Person phone and profile — added in this pass (people were name-and-title only)

People were carried as a name, a title and (sometimes) an email: `Person.phone` and `Person.linkedin_url`
were columns that no code path could ever fill, and `linkedin_url` was not even in the observation loop, so
a profile that *had* landed had no receipt behind it. Both now come from pages the company publishes itself.

| Change | Where it lives |
|---|---|
| Direct-dial read from a person's own team card | `crawl/extract.py` — `_card_people` reads `a[href^="tel:"]` inside the card, the same selector the page-level phone harvest already used |
| Profile read from the company's own page | `crawl/extract.py` — `_linkedin_profile()` accepts only `linkedin.com/in/<slug>`; share, intent and `/company/` links are rejected, so a link can never identify no one or the wrong one |
| Structured-data path too | `crawl/extract.py` — `_jsonld_people` now reads `telephone` and `sameAs` from a JSON-LD `Person` |
| Scoped to the right person | `crawl/extract.py` — contact details are taken only from the person's own card, never the page, so a colleague's link is not stored against them |
| Richest sighting wins | `connectors/website.py` — a name mentioned in passing on the home page loses to the full card on `/team`; a record is never assembled from two pages, so `url` and `excerpt` still describe every field on it |
| Profile gets a receipt | `resolve.py` — `linkedin_url` added to the person observation tuple |
| Shown in the UI | `People.tsx` (Phone and LinkedIn columns + a LinkedIn row in the drawer), `Companies.tsx` (People tab) |

**Provenance and licensing.** Nothing is read from LinkedIn: its User Agreement forbids collecting member
data there, so the only source is a `/in/` link printed on the company's own site, fetched under robots.txt
by the crawl that was already running. There is no free legitimate bulk source of person LinkedIn URLs or
personal mobile numbers — people-search datasets are paid and carry consent problems — which is why
coverage is small and will stay small. Measured honestly: **31 person phones and 8 profiles**, both up from
exactly 0, all with a source URL and retrieval time.

Verified against live sites before wiring: a 60-company probe produced 5 profiles (`sandstone.com/about`,
`geosurge.ai/team`) and 2 direct dials (`hugheshubbard.com/people/…`) from real pages; a regression test
covers each extraction path.

**Honest gap — `contact/patterns.py` has no caller.** The pattern engine is tested and importable, but
nothing invokes it during enrichment. That is deliberate rather than an oversight: wiring it in would mean
*storing addresses nobody has published*, which is exactly the inferred-personal-email territory the
campaign docs exclude (`00_README_Start_Here.md`, "What is deliberately not included"). Showing the guesses
as suggestions a human confirms first is an owner decision and is logged in section 5.

**Not built (traded for the three connectors above):** the Crawl4AI/`curl_cffi` website upgrade and an
Ofcom phone source. Every enrichment test mocks DNS/HTTP/SMTP — there is no live-network test.

## 2. Application

| Request | Status | Notes |
|---|---|---|
| Professional, enterprise-grade UI | Partial | Every back end feature now has a screen (Companies, People, Discovery, Quality, Calls, Kpis, Docs, Plan/Routine/Events, Team, Tasks, Outreach, Engine, Automations, Audit). Still open: responsive/phone layouts and the "does not look AI-generated" design pass |
| Every number streams live | Done | One policy file `web/src/live.ts` (`REFRESH.*`), injected into Refine's single QueryClient via `reactQuery.clientConfig`; every page uses the constants, never a literal. Guard `web/src/check-data-numbers.mjs` blocks hardcoded display numbers and runs inside `npm run build` |
| Full create, read, update, delete | Done (back end) | Companies, people, notes, automations, runs (cancel, retry), exclusions, users |
| Exports: CSV, Excel, Word profile | Done | Formula-injection safe; Excel includes a People sheet and an "About this export" sheet. Export/notes/enrich actions wired into the Companies drawer |
| Assign jobs to team members | Done | Tasks screen with list, filters and `/api/tasks/summary` (assignee, due date, status, "My tasks") |
| Data engine control center with charts | Done | Data engine screen: status, queue, 90-day growth series, coverage; ordinal blue ramp `#184f95 → #86b6ef` |
| Outreach section: promote, approvals, LinkedIn queue | Done | Screen over the engine's own functions, so its compliance gates apply |
| Automations screen | Done | Create, enable/disable, run-now over `/api/automations` |
| Audit log screen | Done | Filterable activity trail over `/api/audit` |
| Name and logo | Done (decision) | Exora; logo and tagline chosen; not yet in the app |
| Works on every machine and on phones | Missing | Responsive layouts, bundled fonts |
| Does not look AI-generated | Missing | Design pass on the rebuilt UI |
| Caching and performance | Done (back end) | Versioned aggregate cache, gzip, immutable asset caching |
| Security hardening | Done | Sessions, roles, CSRF header, login throttling, security headers, safe errors, LIKE escaping, path whitelist |

## 3. Outreach engine (review findings)

| Finding | Status |
|---|---|
| Reply drafts sent to opted-out contacts; unmatched unsubscribes ignored; bounce misclassification; batch rollback re-sends; header injection; .env passwords; weak defaults; label parsing; KSA weekend; Windows config encoding; domain matching | Done (19 regression tests) |
| Suppression via CLI or MCP does not stop LinkedIn tasks; MCP lead id case | Missing |
| Docker healthcheck on worker containers | Missing |
| Unsubscribe link fetched by link scanners unsubscribes immediately | Missing |
| Link allow-list matches by prefix | Missing |
| Internal lead id exposed in email headers | Missing |
| Concurrent send loops can double-send | Missing |
| No data erasure command; no mailbox un-pause command | Missing |
| "Hi there," greeting when no contact name | Missing |
| Unconfirmed-region leads cannot be re-activated | Missing |
| Grounding check accepts numbers from shared aggregator pages | Missing |
| Documentation mismatches (KSA email unlock, reply drafts, init-db path, config keys) | Missing |

## 4. Campaign documents and assets

| Finding | Status |
|---|---|
| Stale tool references and template tokens in 09 | Done |
| Four carousel PDFs contain no slides | Missing |
| 05 cross-reference points to 07 instead of 11 | Missing |
| 13 claims "60+ sources" for 01; the table has 48 | Missing |
| 06 and stack still describe Activepieces as the sequencer | Missing |
| llama.cpp bind address differs between 06 and 12 | Missing |
| Voice price range narrower than the evidence in 04 | Missing |
| ENTRY tier starts at $500 but no package is below $750; private assistant range in 03 differs from P05 | Missing |
| WhatsApp KSA free-service window in 04 has expired | Missing |
| 13 inventory does not list the new components | Missing |
| Harness write-up: OpenClaw "sandbox on" has no matching setting; "install.sh in every runner" is not true; result counts | Missing |

## 5. Decisions only the owner can make

These cannot be completed in code. They are listed so nothing is lost.

- Admin install of WSL2 and Docker (commands in the session), and a reboot
- Secondary sending domains, warmed inboxes, and a registered postal address before any live email
- A Companies House API key (free) to enable that source
- Prices not yet set: the MSP wholesale price, P02 retainer level
- [CONFIRM] items in 11: SLA hours, remote-access method, log location, DPA/BAA, incident notification hours
- Trademark and domain check for "Exora" before public launch
- Gmail draft permission, Brevo domain authentication, demo video (from 07)
- Whether to surface `contact/patterns.py` email guesses in the UI. The engine is built and tested but
  deliberately has no caller: acting on it would mean storing addresses nobody published, which the campaign
  docs exclude. Wiring it as a confirm-first suggestion is a judgement call about compliance, not code

## 6. Final state of this pass (2026-10-04)

Quality gate (`scripts/check.ps1`) — **all five steps pass**:

| Check | Result |
|---|---|
| `ruff check src tests scripts` | clean |
| `ruff format --check` | 57 files formatted |
| prospector tests | **60 passed** |
| outreach-engine tests | **107 passed, 1 skipped** |
| `npm run build` (check:data → tsc → vite) | clean, 3985 modules |

Defects found and fixed in this pass:

- **Schema never converged.** The on-disk DB had 18 tables while `Base.metadata` declared 24, because
  `create_all()` ran once per process from whatever code that process had loaded. The live server started at
  02:14; the six models (`evidence`, `automations`, `daily_metrics`, `exclusions`, `tasks`, `notes`) were added
  at 03:15–03:32, so nothing ever created them and `/api/tasks`, `/api/automations`, `/api/audit`,
  `/api/exclusions`, `/api/notes`, `/api/signals` would have returned 500.
  Fix: `Database.__init__` now calls `create_all()`, so *every* entry point migrates at construction and no
  caller can forget. Regression test `tests/test_schema_migration.py` fails before the fix, passes after.
  Live DB migrated 18 → 24 tables, backup taken first (`data/prospector.db.bak-20261004`), all row counts
  unchanged, the six new tables empty.
- **Nothing refreshed on a schedule.** Only `Discovery.tsx` polled. Now a single policy file (`live.ts`)
  drives every query, with a safety-net default so future pages stream too, and polling pauses in unfocused
  tabs.
- **No deployment artefacts.** Dockerfile/compose/env-template/.dockerignore were absent; the Dockerfile
  written in this pass was initially broken (`COPY *.md` from a context with no root markdown, and
  `docs_root` resolving to `parents[3]` which is meaningless once installed in site-packages). Rewritten to
  build from the campaign root and set `PROSPECTOR_DOCS_ROOT` explicitly. Still requires a real
  `docker compose up -d --build` on a host with Docker.

Verified at runtime (app started on :8099, then stopped): `/api/health` 200; `tasks`, `tasks/summary`,
`engine`, `automations`, `audit`, `exclusions`, `signals`, `outreach/*`, `companies/{id}/notes` all 401
(auth-gated, **not** 500) — proving the routes exist and the migrated schema loads.

Not done in this pass: responsive/phone layouts, the design pass, and everything in section 5 (owner-only).

## 7. QA and hardening pass (2026-10-04, later)

An end-to-end sweep of every sidebar route, then every CRUD path, then the data layer.

**Sweep result:** all 15 routes render with real data and **zero console errors**; all 26 API endpoints
returned 200. Five defects were found, plus eight API defects fixed in the CRUD pass.

Quality gate now:

| Check | Result |
|---|---|
| `ruff check .` | clean across the whole repo (including tests and scratch dirs) |
| prospector tests | **158 passed** (60 baseline + 2 lock tests + 30 CRUD E2E + 60 enrichment + 3 run-failure) |
| outreach-engine tests | **107 passed, 1 skipped** |
| `npm run build` (check:data → tsc → vite) | clean, 3987 modules |

Defects found and fixed in this pass:

- **Navigation was dead below 992px.** The Sider's `breakpoint="lg"` collapsed it to width 0 while antd's
  zero-width trigger (z-index 1) sat *inside* the sticky Header (z-index 10), so `elementFromPoint(17, 25)`
  returned the Header and the hamburger was unclickable — no route was reachable on a laptop-width window.
  Fixed by owning the collapsed state in `Shell.tsx` (seeded from `window.innerWidth`, with a threshold-
  crossing resize listener and a hamburger rendered inside the Header). Verified: opens and *stays* open
  across repeated probes, and selecting a menu item navigates then auto-closes.
- **252 writes lost to `database is locked`.** Root cause, verified by reproducing it in isolation:
  `verify_many()` held one session across **50 companies**, and inside `verify_company` the `QualityCheck`
  loop's `SELECT` autoflushed an INSERT — taking SQLite's write lock — *before* the DMARC/TXT and RDAP
  lookups ran. Those are multi-second network calls, so the lock was held past `busy_timeout` (30s), at
  which point the second discovery worker, `_progress` and the API handlers all fail instantly rather than
  wait (a stale-snapshot/busy-timeout failure is never retried, so the record is dropped). Measured before
  the fix: **9 of 12** network lookups ran with the lock held. Fixed by (a) doing every network lookup in
  `verify_company` before the first write flush, and (b) committing per company in `verify_many`. Both
  re-measured at 0. Regression test `tests/test_write_lock.py` fails before the fix, passes after.
  `tests/test_discovery_lock.py` is a companion guard on the discovery loop.
- **A cancelled run rendered an uncoloured status badge.** `POST /api/runs/{id}/cancel` writes
  `status = "cancelled"`, but the status union and the badge/`Tag` maps on **Discovery, Overview,
  Automations and Engine** only knew `queued|running|done|failed`, so `RUN_STATUS[r.status]` was
  `undefined`. All four now include `cancelled`.
- **Cancel and retry had no UI at all.** Both routes existed but no screen called them, so they were
  orphan endpoints and a queued run could not be stopped from the app. Added to the Run history table
  beside Status (at the far end of the table they sat behind the horizontal scroll and were unreachable):
  **Cancel** for `queued`, **Retry** for `done`/`failed`/`cancelled`, each disabled while its own request
  is in flight. Verified in the browser: retry → `POST /api/runs/38/retry` 200 and a new row appears;
  cancel → `POST /api/runs/45/cancel` 200 and the row flips to `cancelled` with a Retry button. Zero
  console errors.
- **A crawler crash silently killed whole runs — three times.** `_owner_of` (which attributes a
  published address like `jane.doe@` to a person on the same crawl) built `first[0]` and `last[0]`
  without checking the token was non-empty. A JSON-LD `Person` whose name tokenises to nothing usable
  (`"!! ??"`, `"1 2"`) raised `IndexError: string index out of range`, which escaped
  `connector.fetch()` and aborted the run. Runs **75, 111 and 225** all ended this way, so every
  company queued behind the bad record was never crawled. Reproduced in isolation, fixed by skipping
  names with no Latin letters in a token, guarded by `tests/test_run_failure.py` (fails on the original
  code). Note this is *not* a rollback-loss bug: while investigating I suspected `db.session()`'s
  rollback discarded the records already ingested — two characterization tests prove it does not, so
  that hypothesis was withdrawn rather than written up as a fix.
- **The product name was half-changed.** The backend had `BRAND = "Exora"` (`branding.py`, feeding the
  API title and CLI) while the web app still said **"ALI Prospector"** in four places, and
  `web/src/brand.ts` — which `branding.py`'s own docstring claims exists — did not exist. Created
  `brand.ts` (mirroring `branding.py`, plus `ORG = "ALI Technologies"` and the tagline) and an inline
  SVG mark, and pointed `App.tsx`, `Shell.tsx`, `Login.tsx` and `index.html` at them. The sidebar reads
  **Exora / ALI Technologies**: the product name changed, the company name did not, because the campaign
  docs name the entity ALI Technologies throughout (`00_README_Start_Here.md`, `02_USP_and_Positioning.md`).
  Verified in the browser: `document.title` is `Exora`, the logo renders, and `document.body.innerText`
  contains no "ALI Prospector".

**API defects fixed in the CRUD pass** (all covered by `tests/test_crud_e2e.py`): cancel answered 404
instead of 409 for a *finished* run; retry could re-queue an already queued/running run (duplicate work);
`PATCH /api/companies/{id}` accepted a non-existent `owner_id`; whitespace-only notes were stored; an
unknown KPI key raised an unhandled `KeyError` (500); logout skipped the CSRF header check every other
state-changing route enforces; `PATCH /api/plan/tasks/{id}` accepted a ghost `assignee_id`; and
`POST /api/companies/{id}/enrich` answered 200 for a missing company.

**Email/phone enrichment shipped** (60 new tests): `src/prospector/contact/**` gives email verdicts
(`valid/risky/invalid/unknown` — RFC syntax, IDN→punycode, MX, null-MX, reject-all SPF, A-fallback) and
phone verdicts with E.164 only when actually valid, plus the `contact-check` maintenance source and three
new free connectors (`github`, `sec-formd`, `france-registry`), each keeping a provenance receipt with its
fetched-at timestamp. New dependencies `email-validator==2.3.0`, `phonenumbers==9.0.40`, `dnspython==2.8.0`.
The one deliberately unwired piece is `contact/patterns.py`, recorded in section 5.

**Restarted pm2 to activate the backend fixes.** The server had been up since 04:42 while every backend fix
landed between 05:38 and 06:00, so *none of them were live* — the app was still losing writes to
`database is locked` and still returning 500s. Backup taken first with `scripts/backup.py` (`VACUUM INTO`,
no downtime, 13.7 MB). After `pm2 restart exora`: health 200, `contact_checks` migrated 24 → 25 tables
automatically, `/api/sources` returns **17** connectors, cancel on a terminal run returns **409** rather than
404, and an unknown KPI key returns **404 "KPI not found"** rather than an unhandled `KeyError`.

**Known gaps still open:** `GET /api/runs/{id}` does not exist (runs are list-only, so there is no run
detail page); `GET /api/companies/{id}/notes` returns `[]` for a missing company rather than 404; outreach
approve/reject is only tested against a disabled engine; and responsive/phone layouts plus the design pass
remain undone.

