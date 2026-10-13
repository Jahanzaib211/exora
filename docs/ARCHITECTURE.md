# Exora: architecture

A self-hosted, Apollo.io-style prospecting CRM. It discovers companies and people from lawful public sources, records where every field came from, verifies and scores each record, and can hand qualified leads to an optional outreach engine. Everything is open source. It runs as one process on a laptop or a server (native, Docker or Coolify), on SQLite by default or Postgres.

## 1. System context

```
          Public sources                         Team (browser)
  OSM · GLEIF · Companies House · SEC             │  HTTPS via a reverse proxy
  Wikidata · NPI · job boards · news              ▼
  company websites (robots.txt obeyed)     ┌──────────────┐
            │                              │  Web UI       │  React + Refine + Ant Design
            ▼                              └──────┬───────┘
   ┌──────────────────┐    runs table     ┌──────┴───────┐
   │ Discovery worker │◄─────────────────►│  API          │  FastAPI, REST + OpenAPI
   │ connectors,      │   (durable queue) │  auth, RBAC,  │
   │ crawler, verify  │                   │  search       │
   └────────┬─────────┘                   └──────┬───────┘
            │            ┌────────────────┐       │
            └───────────►│ SQLite / Postgres│◄────┘
                         │  prospects,     │
                         │  provenance,    │◄────── Outreach engine (optional, separate
                         │  runs, audit    │        package): sequences, approvals, replies
                         └────────────────┘

  By default the worker and scheduler run inside the web process (`prospector serve`);
  `prospector worker` runs them on their own.
```

## 2. Pipeline (Apollo-style)

| Stage | What happens | Where |
|---|---|---|
| 1. Discover | A connector pulls candidate companies for a segment and geography (e.g. law offices in Manchester, Saudi law firms in GLEIF). | `src/prospector/connectors/*` |
| 2. Normalize | Country to ISO-2, phone to E.164, website to its registrable domain, legal suffixes stripped for matching. | `src/prospector/normalize.py` |
| 3. Resolve | Identity resolution: match by domain, then by registry id (LEI, company number), then by fuzzy name plus country. Matches merge into one company; every source is kept. | `src/prospector/resolve.py` |
| 4. Enrich | Crawl the company website (home, team, about, contact pages) and extract named people with their titles, the direct-dial number and LinkedIn profile printed on their own card, published business emails, phone numbers and the LinkedIn company page. | `src/prospector/crawl/*`, `src/prospector/contact/*` |
| 5. Verify | The domain resolves, has an MX record and serves a website; each email is on the company's own domain; role addresses (info@) are separated from named ones; free-mail is flagged. | `src/prospector/verify.py` |
| 6. Score | Fit score (segment, size, signals, with reasons) and data-quality score (weighted checks). | `src/prospector/fit.py`, `src/prospector/verify.py` |
| 7. Review | A teammate marks a record Ready or Rejected in the UI. | UI |
| 8. Promote | Ready leads are upserted into the optional outreach engine and enrolled; its region compliance rules apply there. | `src/prospector/outreach.py` |

Each stage is idempotent and runs as a job (section 5), so failed or partial runs can be retried safely.

## 3. Data model: every field is sourced

Records are built from **observations**. An observation is one value for one field, seen at one URL, from one source, at one time. The record shows the best current value, and the UI shows the full trail behind it ("each lead sourced with detail").

| Table | Purpose |
|---|---|
| `companies` | Resolved company: name, domain, country, city, segment, size band, registry ids, fit and quality scores, status (`new`, `ready`, `rejected`, `in_outreach`), owner |
| `people` | Resolved person at a company: name, title, seniority, role category, business email, email status, direct-dial phone and LinkedIn URL (the last two only when published on the company's own site — never read from LinkedIn itself) |
| `observations` | `entity_type`, `entity_id`, `field`, `value`, `source`, `url`, `retrieved_at`, `confidence`, `run_id` |
| `signals` | Hiring, funding, regulatory or news events tied to a company, with date and URL |
| `sources` | Connector catalogue: name, licence and attribution, cadence, enabled flag, credentials reference |
| `runs` | One execution of a connector or crawl: parameters, counts (found, new, updated, failed), errors, timings |
| `jobs` | Durable work queue (section 5) |
| `quality_checks` | Per-record check results that feed the quality score and the Data quality screen |
| `suppression` | Domains and emails never to collect or contact (shared with the outreach engine) |
| `users`, `audit` | Team accounts and an append-only audit trail |

Hand-researched accounts are imported from a CSV (`PROSPECTOR_SEED_CSV`, example in `playbook/accounts.example.csv`) as observations from source `seed-sheet`. They go through the same verification as every other record.

## 4. Sources (lawful, open, attributable)

| Source | Gives | Access | Licence and terms |
|---|---|---|---|
| OpenStreetMap (Overpass) | Offices by type and location: lawyer, accountant, IT, clinic; website, phone, address | Free, rate-limited | ODbL; attribution stored |
| GLEIF LEI | Legal entities worldwide incl. KSA and EU; status, address | Free API | CC0 |
| Companies House (UK) | Companies by SIC code and location, officers | Free API key | UK Open Government Licence |
| SEC EDGAR (US) | Public companies by SIC | Free; declared User-Agent | Public domain |
| Wikidata | Companies by industry and country | SPARQL | CC0 |
| Greenhouse, Lever, Ashby boards | Hiring signals | Public JSON | Each board's terms |
| News and funding RSS, GDELT | Funding and regulatory signals | Free | Per publisher |
| Company websites | People, titles, published business contacts | Crawler | robots.txt, rate limits, honest User-Agent |
| Licensed enrichment (optional) | Verified work emails | Paid API key | Vendor terms |

**Out of scope by design:** LinkedIn scraping or automation, personal emails and phones, guessed email addresses for EU/KSA contacts, CAPTCHA or anti-bot evasion. These are unlawful for marketing under GDPR, PECR and PDPL, or break the platforms' terms. SMTP mailbox probing exists but is off unless `PROSPECTOR_SMTP_PROBE=1`, throttled per mail server, and reports every uncertain answer as "unknown".

## 5. Jobs and scheduling

- **Queue:** the `runs` table, claimed with a conditional update (plus `SELECT … FOR UPDATE SKIP LOCKED` on Postgres). No Redis or broker to run, and queued work survives restarts. On SQLite one process claims runs, with `PROSPECTOR_WORKER_THREADS` threads.
- **Scheduler:** each source has a cadence (registries weekly, OSM and job boards daily, news hourly). The scheduler enqueues due runs. On the laptop it works while the machine is awake; on a server it is 24/7.
- **Politeness:** per-domain concurrency of 1, a 2 s delay, robots.txt cache, exponential back-off on 429/5xx, and a global request budget per hour.

## 6. Crawler choice

Decided by measurement, not reputation. A bake-off on the development laptop showed:
- Scrapy: 14 s and 89 MB per site, with robots.txt, delay and retries built in.
- Browser-based tools (Playwright, Crawl4AI, Crawlee-Playwright, Selenium): 0.9–1.2 GB each.

So the crawler is Scrapy over plain HTTP, run as a child process (`python -m prospector.crawl.spider`) per batch so a crash or memory spike never takes down the app. Client-rendered sites yield fewer pages; a browser renderer was deliberately left out for its memory cost.

## 7. API

REST with an OpenAPI schema at `/api/openapi.json`, plus interactive docs at `/api/swagger`.

**Why not GraphQL / Apollo Router:** federation solves the problem of many teams owning many services behind one graph. This system has one backend and one team, and REST plus OpenAPI gives typed clients, caching and simpler authorization with less machinery. The UI's data needs (filtered tables, record detail with provenance) map directly to resource endpoints.

Every endpoint is listed in `docs/API.md`.

## 8. Security

- **Authentication:** scrypt password hashes; an HS256 session JWT in an HttpOnly, SameSite=Strict cookie (Secure behind HTTPS); a CSRF header required on writes; login throttling.
- **Authorization:** roles admin, sdr and viewer, enforced server-side on every route.
- **Secrets:** environment only (`.env` locally, Coolify secrets in production); never in the repository.
- **Docs endpoint:** serves only whitelisted markdown files, so paths outside the whitelist cannot be read.
- **Audit:** every write is recorded with user, time and change.

## 9. Deployment

| Environment | How |
|---|---|
| Laptop or PC | `scripts/setup` then `scripts/start` (or pm2): one native process, SQLite |
| Linux server | The same scripts plus the systemd unit in `deploy/exora.service`, behind an HTTPS proxy |
| Docker / Coolify | `Dockerfile` + `docker-compose.yml`: the `app` service, optional `worker` and `tunnel` profiles, one data volume |

Details in [DEPLOYMENT.md](DEPLOYMENT.md).

## 10. Quality gates

Back end: ruff lint and format, pytest (unit, API and connector tests against recorded fixtures, no live network). Front end: the hardcoded-number guard, TypeScript strict typecheck and production build, and Playwright browser tests. `scripts/check.sh` / `scripts\check.ps1` run the first group locally; GitHub Actions runs everything on Ubuntu and Windows plus a fresh-clone install and a Docker build on every push.
