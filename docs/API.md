# Exora: API reference

REST over JSON. The live, typed schema is at `/api/openapi.json`, with interactive docs at `/api/swagger` (sign in first).

**Auth:** `POST /api/auth/login` sets an HttpOnly session cookie. Every non-GET request must send the header `X-Requested-With: prospector`.

**Roles:** viewer (read), sdr (read and work), admin (everything, including users).

| Endpoint | Role | Screen | Source of truth |
|---|---|---|---|
| `GET /api/health` | public | Health checks (Docker, Coolify, systemd) | `{"ok": true, "engine": <bool>}` |
| `GET /api/config` | public | Sign-in, shell (before first render) | `Settings`: brand, tagline, `org_name`, `operator_timezone`, version |
| `POST /api/auth/login` | public | Sign-in | `users` table |
| `POST /api/auth/logout`, `GET /api/auth/me` | signed in | Sign-in, shell | `users`, session |
| `GET /api/companies` | viewer | Companies | `companies`; filters `q`, `segment`, `country`, `status`, `min_quality`, `has_email`, `has_phone`, `has_people`; `sort`, `page`, `page_size` |
| `POST /api/companies` | sdr | Companies (add) | `companies` |
| `GET /api/companies/facets` | viewer | Companies filters, Overview | `companies`, `people` |
| `GET /api/companies/{id}` | viewer | Company drawer | `companies`, `people`, `signals`, `observations` (sources), `quality_checks` |
| `PATCH /api/companies/{id}` | sdr | Company drawer: edit, Mark ready, Reject | `companies.status`, `owner_id`, `segment` |
| `DELETE /api/companies/{id}` | sdr | Company drawer | `companies` |
| `POST /api/companies/bulk` | sdr | Companies bulk bar | `companies.status` |
| `POST /api/companies/{id}/enrich` | sdr | Company drawer: Enrich | queues a `runs` row (website crawl) |
| `GET /api/companies/{id}/notes`, `POST /api/companies/{id}/notes` | viewer, sdr | Company drawer: Notes | `notes` |
| `PATCH /api/notes/{id}`, `DELETE /api/notes/{id}` | sdr | Company drawer: Notes | `notes` |
| `GET /api/companies/{id}/profile.docx` | sdr | Company drawer: Word profile | `companies`, `people` |
| `GET /api/companies/{id}/tasks` | viewer | Company drawer: Tasks | `tasks` |
| `GET /api/people` | viewer | People | `people`; filters `q`, `seniority`, `segment`, `country`, `has_email`, `company_id` |
| `POST /api/people` | sdr | People (add) | `people` |
| `GET /api/people/{id}` | viewer | Person drawer | `people`, `observations` |
| `PATCH /api/people/{id}` | sdr | Person drawer | `people` |
| `DELETE /api/people/{id}` | sdr | Person drawer | `people` |
| `GET /api/sources` | viewer | Discovery source cards | Connector registry (`prospector/connectors`) |
| `GET /api/runs` | viewer | Discovery run history | `runs` (durable queue) |
| `POST /api/runs` | sdr | Discovery: New run | `runs` |
| `POST /api/runs/{id}/cancel` | sdr | Run history: Cancel (queued runs only) | `runs.status` → `cancelled`; 404 if missing, 409 if already running or terminal |
| `POST /api/runs/{id}/retry` | sdr | Run history: Retry (finished runs) | new `runs` row with the same `source` and `params`; 409 while queued/running |
| `GET /api/quality` | viewer | Data quality, Overview, Engine | `quality_checks`, `companies.quality_score` (cached 60s) |
| `GET /api/engine` | viewer | Engine | `companies`, `quality_checks`, `runs` |
| `GET /api/signals` | viewer | Automations | `signals` |
| `GET /api/exclusions` | sdr | Automations | `exclusions` |
| `DELETE /api/exclusions/{id}` | admin | Automations | `exclusions` |
| `GET /api/automations` | viewer | Automations | `automations`, `runs` |
| `POST /api/automations`, `PATCH /api/automations/{id}`, `DELETE /api/automations/{id}` | sdr | Automations | `automations` |
| `POST /api/automations/{id}/run` | sdr | Automations: Run now | `runs` |
| `GET /api/tasks` | viewer | Tasks | `tasks` |
| `POST /api/tasks`, `PATCH /api/tasks/{id}`, `DELETE /api/tasks/{id}` | sdr | Tasks | `tasks` |
| `POST /api/tasks/bulk` | sdr | Companies bulk bar: create tasks | `tasks` |
| `GET /api/tasks/summary` | viewer | Tasks | `tasks` |
| `GET /api/outreach/summary` | viewer | Outreach | outreach engine database |
| `GET /api/outreach/drafts` | viewer | Outreach: Drafts | outreach engine database |
| `POST /api/outreach/drafts/{id}/decision` | sdr | Outreach: Approve / reject | outreach engine database |
| `GET /api/outreach/leads`, `GET /api/outreach/linkedin` | viewer | Outreach | outreach engine database |
| `POST /api/outreach/linkedin/{id}`, `POST /api/outreach/promote`, `POST /api/outreach/tick` | sdr | Outreach | outreach engine database (`tick` advances due steps) |
| `GET /api/calls`, `POST /api/calls`, `PATCH /api/calls/{id}` | viewer, sdr | Calls & pilots | `calls`; feeds the conversion KPIs |
| `GET /api/plan` | viewer | Playbook, 30-day plan | `09_30_Day_Execution_Plan_and_KPIs.md` (content), `plan_tasks` (progress) |
| `PATCH /api/plan/tasks/{id}` | sdr | 30-day plan | `plan_tasks` |
| `POST /api/plan/resync` | admin | 30-day plan | `plan_tasks` |
| `GET /api/routine` | viewer | Playbook, Daily routine | 09 daily routine; `routine_checks` |
| `POST /api/routine/{id}/toggle` | sdr | Daily routine | `routine_checks` |
| `GET /api/events` | viewer | Playbook, Events | 09 events list; `events.status`, `notes` |
| `PATCH /api/events/{id}` | sdr | Playbook, Events | `events` |
| `GET /api/kpis` | viewer | Analytics | 09 KPI table; engine database; `kpi_entries` |
| `POST /api/kpis/{key}/entries` | sdr | Analytics: manual reading | `kpi_entries` (404 for an unknown KPI) |
| `GET /api/export/companies`, `GET /api/export/people` | sdr | Companies, People: Export | CSV / Excel / Word profile, formula-injection safe |
| `GET /api/docs`, `GET /api/docs/content?path=` | viewer | Documents | Whitelisted markdown files in the campaign folder |
| `GET /api/users` | sdr | Team | `users` |
| `POST /api/users`, `PATCH /api/users/{id}` | admin | Team (add, edit, deactivate) | `users` |
| `GET /api/audit` | admin | Audit | `audit` |
| `GET /api/dashboard` | viewer | Overview | Plan (09), KPIs (09), outreach engine database |
| `GET /api/health` | public | Uptime monitor | process |

**Errors:** `{"detail": "..."}` with 400 (validation), 401 (not signed in), 403 (role or missing header), 404 (missing row), 409 (conflict: duplicate, or the run is not in a state this action allows), 429 (login throttled) or 500 (logged server-side; no internals are returned).

**Run states:** `queued` → `running` → `done` | `failed` | `cancelled`. Cancel is only valid from `queued` (once a worker has claimed a run it owns it); retry is only valid from a terminal state, so the two never double up on work.
