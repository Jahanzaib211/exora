# Dependency inventory

Everything the app runs on, pinned, with what each pin is for and what is still open.

Regenerate the Python numbers with the snippets in [§7](#7-reproducing-this-document). Counts in §3
were taken from the original development `.venv` on 6 October 2026.

## 1. Summary

| Layer | Declared | Lockfile | Reproducible? |
| --- | --- | --- | --- |
| Python — `prospector` | 20 runtime + 2 dev + 1 `postgres` extra, all exact-pinned | `uv.lock` (88 packages) | ✅ `uv sync --frozen` in the setup scripts, the Dockerfile and CI |
| Python — `outreach-engine` | optional, separate package (not in this repository) | its own | see [§3](#3-the-optional-outreach-engine) |
| Node — `web` | 12 runtime + 6 dev, all exact-pinned | `web/package-lock.json` | ✅ `npm ci` everywhere |
| Container base | `python:3.12-slim-bookworm`, `node:24-bookworm-slim`, `ghcr.io/astral-sh/uv:0.12.22` | tag only | ⚠️ tags float; no digests |

Every direct dependency is exact-pinned (`pkg==1.2.3`, never `pkg>=1.2.3`), and both ecosystems have a
committed lockfile, so two installs of the same commit get the same transitive versions.

## 2. Python: what `prospector` declares

`pyproject.toml` → `[project].dependencies` (20), `[project.optional-dependencies].dev` (2) and
`.postgres` (1: `psycopg[binary]`, for `PROSPECTOR_DATABASE_URL=postgresql+psycopg://…`).

### Runtime

| Package | Pin | Why it is here |
| --- | --- | --- |
| `python-dotenv` | 1.2.4 | The CLI exports `.env` into the process environment, so variables read via `os.environ` (and by the crawler child process) work from `.env` too |
| `fastapi` | 0.141.1 | HTTP framework: routes, `Depends` auth, OpenAPI generation |
| `uvicorn` | 0.54.0 | ASGI server the `prospector serve` CLI wraps |
| `sqlalchemy` | 2.1.1 | ORM and Core over SQLite; session/transaction management in `db.py` |
| `pydantic` | 2.13.5 | Request/response models and validation |
| `pydantic-settings` | 2.15.0 | `Settings` class: `PROSPECTOR_*` env vars + `.env` |
| `pyjwt` | 2.15.1 | HS256 signing and verification of the session cookie |
| `typer` | 0.27.2 | CLI surface (`serve`, `worker`, `create-user`) |
| `httpx` | 0.28.1 | Outbound HTTP for connectors, RDAP/DNS verification, the engine bridge |
| `tzdata` | 2026.4 | IANA zone database for `ZoneInfo`; never imported, ships Windows tz rules |
| `tldextract` | 5.3.2 | Registrable domain from a URL (dedup and canonicalisation) |
| `phonenumbers` | 9.0.40 | Parse/normalise/validate phone numbers |
| `dnspython` | 2.8.0 | DNS and RDAP lookups used by `verify` |
| `email-validator` | 2.3.0 | Backs `pydantic.EmailStr`; never imported directly |
| `rapidfuzz` | 3.14.6 | Fuzzy matching for person/company dedup |
| `scrapy` | 2.19.0 | The crawler, always run as a **subprocess** so a crawl cannot take the API down |
| `beautifulsoup4` | 4.15.0 | HTML parsing for `crawl/extract.py` |
| `lxml` | 6.1.3 | The actual parser — `BeautifulSoup(html, "lxml")`; bs4 raises `FeatureNotFound` without it |
| `openpyxl` | 3.1.5 | `.xlsx` seed sheet and spreadsheet exports |
| `python-docx` | 1.2.0 | `.docx` exports |

### Dev (`optional-dependencies.dev`)

| Package | Pin | Why |
| --- | --- | --- |
| `pytest` | 9.1.1 | Test runner (75 test files) |
| `ruff` | 0.16.9 | Linter **and** formatter; `scripts/check.ps1` gates on both |

Nothing in the runtime list is unused. `tzdata` and `email-validator` are the two that never appear
in an `import` statement — they are data/provider packages, checked by executing the code that
depends on them rather than by scanning imports.

## 3. The optional outreach engine

`prospector` can drive a separate outreach engine package (`outreach_engine`), but **does not
declare it**: it is not published to an index and is not part of this repository. What that means:

- A standard install (`uv sync`, the setup scripts, the Docker image) has no engine, and
  `/api/health` reports `"engine": false`. The Outreach screens say the engine is not configured.
- The import is deliberately lazy — every `from outreach_engine import ...` sits inside a function
  behind `engine_enabled and engine_config` — so a missing engine degrades instead of crashing.
- The engine's tests in `tests/test_tasks_outreach.py` skip themselves when it is not installed.
- To attach one, see [DEPLOYMENT.md §8](DEPLOYMENT.md#8-optional-the-outreach-engine).

The figures below describe the original development virtualenv, which had the engine installed
editable next to the app.

`outreach-engine` itself declares **23 requirements** (21 runtime + `pytest`/`ruff` behind
`extra == "dev"`), reaching 93 packages. Of those, 11 are already pinned identically by `prospector`
(`beautifulsoup4`, `fastapi`, `httpx`, `pydantic`, `pydantic-settings`, `sqlalchemy`, `typer`,
`tzdata`, `uvicorn`, plus the two dev tools) — the pyproject comment about "same pins where shared"
holds, and the two lists have drifted apart nowhere.

### Where the 143 installed packages come from

| Bucket | Count | Meaning |
| --- | --- | --- |
| Reachable from `prospector`'s 21 declared requirements | 82 | the documented closure |
| Only reachable via `outreach-engine` | 57 | e.g. `langgraph*`, `openai`, `mcp`, `trafilatura`, `prometheus-client`, `psycopg*`, `dateparser`, `pyyaml`, `jinja2` |
| Not matched by a plain requirement string | 2 | `psycopg-binary` (the `psycopg[binary]` extra), `lxml_html_clean` (lxml's split-out HTML cleaner) |
| `prospector` itself, `pip` | 2 | — |
| **Total** | **143** | |

82 + 57 + 2 + 2 = 143.

## 4. Findings and what was done

### F1 — no Python lockfile (resolved)

The image used to run `pip install ./prospector`, re-resolving every transitive requirement on each
build. `uv.lock` is now committed; the setup scripts, the Dockerfile and CI all install with
`uv sync --frozen`, and CI fails if the lockfile no longer matches `pyproject.toml`
(`uv lock --check`).

### F2 — `path-to-regexp` advisory (resolved)

`npm audit` reported 3 high-severity issues (GHSA-j3q9-mxjg-w52f, sequential-optional-group DoS;
GHSA-27v5-c462-wpq7, ReDoS via multiple wildcards) in `path-to-regexp` 8.0.0–8.3.0:

```
@refinedev/antd@6.0.3 → @ant-design/pro-layout@7.22.7 → path-to-regexp@8.2.0
```

Neither `@ant-design/pro-layout` nor `path-to-regexp` is reachable from `src/` — verified absent from
`dist/assets/*.js` — so it was a build-time finding rather than an exploitable one. Resolved with an
override rather than `npm audit fix --force`, which would have downgraded `@refinedev/antd` to 6.0.0:

```jsonc
// web/package.json
"overrides": { "path-to-regexp": "8.4.2" }
```

Same major version, fixed release. `npm audit` now reports **0 vulnerabilities**.

### F3 — `prometheus-client` is installed but unused (accepted)

`prometheus-client==0.26.0` comes in via `outreach-engine` and `prospector` never imports it.
`/api/metrics` deliberately emits Prometheus text format from the standard library instead: pulling
it in as a direct dependency would mean declaring a package for one endpoint when it is already in
the tree by accident of the engine's requirements. **If** the metrics endpoint ever grows histograms
or label cardinality, declare `prometheus-client==0.26.0` explicitly and switch — do not start
importing it without declaring it.

### F4 — tests inherited `.env` (resolved)

`Settings.model_config` sets `env_file=".env"`, resolved from the working directory. The suite
therefore ran with whatever tunnel/cookie/host settings this machine had, so a green run meant "the
code works *and* `.env` happens to be benign". `tests/conftest.py` now pins `trust_proxy`,
`cookie_secure`, `cookie_same_site`, `api_docs_public`, `allowed_hosts`, `metrics_enabled`,
`metrics_token` and `login_ip_max_fails` explicitly; tests that need a non-default value opt in via
`variant(settings, ...)` in `tests/test_hardening.py`.

### F5 — the container ships without the engine (accepted, by design)

The engine is optional and lives outside this repository (§3), so the image does not contain it and
reports `"engine": false`. To bundle one, extend the image: copy the engine source in and run
`uv pip install /path/to/engine` after the `uv sync` steps.

## 5. Node (`web/`)

All 18 direct dependencies are exact-pinned; `package-lock.json` is committed; the Dockerfile uses
`npm ci`.

### Runtime (12)

| Package | Pin | Why |
| --- | --- | --- |
| `react`, `react-dom` | 19.3.0 | UI |
| `react-router` | 7.18.4 | Client routing |
| `@refinedev/core` | 5.0.12 | Auth, data-provider and query conventions |
| `@refinedev/antd` | 6.0.3 | Refine's antd binding |
| `@refinedev/react-router` | 2.0.4 | Refine ↔ router binding |
| `@tanstack/react-query` | 5.104.1 | Server-state cache behind Refine's hooks |
| `antd` | 5.29.3 | Component library |
| `@ant-design/icons` | 5.6.1 | Icons |
| `react-markdown` | 10.1.0 | Renders the campaign markdown in Docs |
| `remark-gfm` | 4.0.1 | Tables/strikethrough for `react-markdown` |
| `dayjs` | 1.11.23 | Date formatting |

### Dev (6)

`@playwright/test` 1.63.0, `typescript` 5.9.3, `vite` 8.3.2, `@vitejs/plugin-react` 6.1.1,
`@types/react` 19.3.0, `@types/react-dom` 19.3.0.

`engines` pins Node to `>=24 <25`, matching `node:24-bookworm-slim` in the Dockerfile.

## 6. Container and system dependencies

| Layer | Version | Notes |
| --- | --- | --- |
| Build image | `node:24-bookworm-slim` | runs `npm ci` + `tsc --noEmit` + `vite build` |
| Runtime image | `python:3.12-slim-bookworm` | no Node, no compilers, no dev deps |
| apt | `curl`, `tini` | healthcheck probe and PID 1 reaper (`ENTRYPOINT ["tini", "--"]`) |
| Python tooling | `ghcr.io/astral-sh/uv:0.12.22` | copies the `uv` binary into the runtime image for `uv sync --frozen` |
| Tunnel | `cloudflare/cloudflared:2026.9.3` | optional compose service `tunnel` (profile `tunnel`) |

Base images are pinned by tag, not by digest. Pinning `python:3.12-slim-bookworm@sha256:…` is the
remaining reproducibility gap on this layer.

## 7. Reproducing this document

```powershell
# Declared vs installed vs transitive closure, and what is orphaned (from the repository root)
.venv\Scripts\python.exe -c @'
import tomllib, pathlib, importlib.metadata as md
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name as C

p = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
declared = p["project"]["dependencies"] + p["project"]["optional-dependencies"]["dev"]

inst, reqs = {}, {}
for d in md.distributions():
    if d.metadata["Name"]:
        inst[C(d.metadata["Name"])] = d.version
        reqs[C(d.metadata["Name"])] = d.metadata.get_all("Requires-Dist") or []

def closure(roots):
    seen, q = set(), [str(Requirement(r)) for r in roots]
    while q:
        r = Requirement(q.pop())
        if r.marker and not r.marker.evaluate({}):
            continue
        if C(r.name) in seen:
            continue
        seen.add(C(r.name))
        q += [str(Requirement(x)) for x in reqs.get(C(r.name), [])]
    return seen

mine = closure(declared)
print(f"declared={len(declared)} installed={len(inst)} closure={len(mine)} orphan={len(set(inst)-mine-{'prospector','pip'})}")
'@

# npm: exact tree, overrides in effect, advisories
cd web
npm ls --omit=dev          # direct prod tree
npm ls path-to-regexp      # shows "@6.0.3 ... path-to-regexp@8.4.2 overridden"
npm audit                  # 0 vulnerabilities
```
