"""Contact enrichment: judging and normalising the business contact details a company publishes.

Submodules:
    emailcheck - RFC syntax, IDN normalization, MX / null-MX / reject-all SPF verdicts (keyless, DNS only)
    patterns   - email pattern guesses from a name + domain, catch-all detection, optional SMTP RCPT probe
    phone      - offline number validation (possible / valid, type, region) with `phonenumbers`

Everything is provenance-first: each verdict carries a source and a checked_at timestamp so it can be
stored with the value it judged. Compliance (KSA PDPL / UK PECR / EU GDPR): only business contact details
published by the company itself, never a private individual's personal address or number.
"""
