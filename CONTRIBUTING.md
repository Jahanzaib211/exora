# Contributing to Exora

Thanks for helping. Bug reports, fixes, new discovery sources and documentation improvements are all welcome.

## Set up

You need Git, Node.js 24 and [uv](https://docs.astral.sh/uv/) (the setup script installs uv if it is missing).

```bash
git clone https://github.com/Jahanzaib211/exora.git && cd exora
./scripts/setup.sh --dev                                   # Windows: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev
```

Run the app while you work:

```bash
uv run prospector serve      # API + built UI on http://127.0.0.1:8090
cd web && npm run dev        # UI with hot reload on http://127.0.0.1:5173 (proxies /api to :8090)
```

## Before you open a pull request

Run the same checks CI runs:

```bash
./scripts/check.sh           # Windows: powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

That is ruff lint and format, the pytest suite and the web typecheck and build. The browser tests (Playwright) run in CI; to run them locally, see the header of `web/playwright.config.ts`.

## Guidelines

- **Tests never touch the network.** Connector tests use recorded responses through an `httpx.MockTransport`; see `tests/test_connectors.py`.
- **No real personal data** in code, tests, fixtures, screenshots or issues. Use the reserved `example.com` / `.example` domains and obviously fictional names.
- **New discovery sources** must be lawful and attributable: record the licence in the connector's `licence` attribute, obey robots.txt and rate limits, and never scrape LinkedIn or evade anti-bot measures.
- **Every displayed number comes from the API.** `npm run build` fails on hardcoded display numbers in the UI (`web/src/check-data-numbers.mjs`).
- Keep pull requests focused, and describe what changed and how you tested it.

## Reporting bugs

Open an issue with the steps to reproduce, what you expected, what happened, your OS and how you run Exora (Docker, scripts or pm2). Remove any personal data from logs before pasting them. For security problems, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
