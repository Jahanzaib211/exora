# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on this repository. Do not open a public issue. You should get a reply within a few days; once a fix is released you are welcome to publish details.

## Running Exora safely

Exora stores personal data about the people it finds, so treat an install like any other system holding customer data.

- **Secret key.** Set a random `PROSPECTOR_SECRET_KEY` of 32+ characters (`prospector init` and the Docker image do this for you). The app refuses to listen on a network address with the default key. Rotating the key signs everyone out.
- **HTTPS only.** Never publish port 8090 to the internet. Put a reverse proxy with TLS in front (Coolify, Caddy, nginx or Cloudflare Tunnel), then set `PROSPECTOR_COOKIE_SECURE=true`, and set `PROSPECTOR_TRUST_PROXY=true` only when that proxy is the sole route in.
- **Hostnames.** On a public domain, set `PROSPECTOR_ALLOWED_HOSTS` to the hostnames you serve.
- **Accounts.** Give people the lowest role they need (viewer, sdr, admin). Every change is recorded in the audit log.
- **API schema.** `/api/swagger` and `/api/openapi.json` are admin-only unless `PROSPECTOR_API_DOCS_PUBLIC=true`.
- **Backups.** The database holds personal data: encrypt backups and keep them off the server (see docs/DEPLOYMENT.md).
- **Never commit** `.env`, `data/` or screenshots taken against a real database; `.gitignore` excludes them.

## What is already in place

scrypt password hashing; HS256 session tokens in an HttpOnly, SameSite cookie; a required `X-Requested-With` header on every write (CSRF); per-account and per-IP login throttling; role checks on every route; a whitelist for served documents; request IDs that cannot be forged into headers; security headers (nosniff, frame denial, HSTS behind HTTPS); and formula-injection-safe CSV and Excel exports.
