"""Re-check the contact details already in the database and store one verdict per value.

Maintenance job: every stored e-mail address and phone number is judged (offline for phones, DNS for
mail), and the verdict is written to `contact_checks` with its source and a checked_at timestamp - the
app's provenance rule. Nothing is sent, dialled or texted: this is a data-quality pass so a campaign
never mails an address the domain says will bounce, and exports never carry a malformed number.

    emails  RFC syntax + IDN normalization, MX / null-MX / reject-all SPF (`contact.emailcheck`)
    phones  offline number plans: valid / possible / invalid (`contact.phone`)
    optional  one SMTP RCPT probe per address, only when the operator turns both the run parameter and
              PROSPECTOR_SMTP_PROBE on (see `contact.patterns`; off by default, cached by TTL)

Values checked recently (within `older_than_days`) are skipped, so the job is cheap to re-run.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from ..contact import emailcheck, patterns, phone
from ..models import Company, ContactCheck, Person, utcnow
from .base import Connector, ParamSpec


class ContactCheckConnector(Connector):
    """Judge stored emails and phone numbers; upsert one ContactCheck row per (kind, value, source)."""

    key = "contact-check"
    name = "Check contact details"
    description = (
        "Validates stored emails (RFC + public DNS: MX, null-MX, reject-all SPF) and phone numbers "
        "(offline number plans), keeping one verdict per value with its source and timestamp."
    )
    licence = "Internal (public DNS lookups, offline number plans)"
    maintenance = True
    needs_db = True
    params = [
        ParamSpec("limit", "Max values", "int", False, default=200),
        ParamSpec("older_than_days", "Not checked for (days)", "int", False, default=7),
        ParamSpec(
            "smtp_probe",
            "SMTP RCPT probe",
            "select",
            False,
            ["off", "on"],
            "off",
            help="Also probe one address per domain (needs PROSPECTOR_SMTP_PROBE=1; off by default)",
        ),
    ]

    def __init__(self, http, api_key=None, db=None, settings=None, resolver=None, probe=None) -> None:
        super().__init__(http, api_key)
        self.db = db
        self.resolver = resolver  # injectable DNS resolver (tests, offline runs); None = real DNS
        self.probe = probe  # injectable RCPT probe (tests); None = the module's gated probe_address

    # -- candidates ------------------------------------------------------
    def _candidates(self, limit: int) -> tuple[list[str], list[tuple[str, str | None]]]:
        """-> (distinct emails, [(phone, region)]) of stored contact details, capped at `limit`."""
        with self.db.session() as s:
            emails: list[str] = []
            seen: set[str] = set()
            for (value,) in s.execute(
                select(Company.general_email).where(Company.general_email.is_not(None)).distinct()
            ):
                key = value.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    emails.append(key)
            for (value,) in s.execute(select(Person.email).where(Person.email.is_not(None)).distinct()):
                key = value.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    emails.append(key)
            phones: list[tuple[str, str, str | None]] = []
            seen_p: set[str] = set()
            for value, country in s.execute(
                select(Company.phone, Company.country).where(Company.phone.is_not(None)).distinct()
            ):
                if value not in seen_p:
                    seen_p.add(value)
                    phones.append((value, country))
            for value, country in s.execute(
                select(Person.phone, Company.country)
                .join(Company, Person.company_id == Company.id)
                .where(Person.phone.is_not(None))
                .distinct()
            ):
                if value not in seen_p:
                    seen_p.add(value)
                    phones.append((value, country))
        return emails[:limit], phones[: max(limit - len(emails), 0)]

    def _recent(self, cutoff) -> set[tuple[str, str, str]]:
        with self.db.session() as s:
            return {
                (kind, value, source)
                for kind, value, source in s.execute(
                    select(ContactCheck.kind, ContactCheck.value, ContactCheck.source).where(
                        ContactCheck.checked_at >= cutoff
                    )
                )
            }

    def _write(self, kind: str, value: str, verdict: str, reason: str, source: str, checked_at) -> None:
        """One row per (kind, value, source): refresh in place instead of duplicating the trail."""
        with self.db.session() as s:
            row = s.scalar(
                select(ContactCheck).where(
                    ContactCheck.kind == kind, ContactCheck.value == value, ContactCheck.source == source
                )
            )
            if row is None:
                s.add(
                    ContactCheck(
                        kind=kind, value=value, verdict=verdict, reason=reason, source=source,
                        checked_at=checked_at,
                    )
                )  # fmt: skip
            else:
                row.verdict, row.reason, row.checked_at = verdict, reason, checked_at

    # -- run -------------------------------------------------------------
    def run_maintenance(self, params: dict[str, Any]) -> tuple[int, int, str | None]:
        limit = int(params.get("limit") or 200)
        cutoff = utcnow() - timedelta(days=max(int(params.get("older_than_days") or 7), 0))
        want_probe = params.get("smtp_probe") == "on" and patterns.probe_enabled()
        emails, phones = self._candidates(limit)
        recent = self._recent(cutoff)
        found = len(emails) + len(phones)
        updated = 0

        for address in emails:
            verdict = None
            if ("email", address, emailcheck.SOURCE) not in recent:
                verdict = emailcheck.check_email(address, resolver=self.resolver)
                self._write("email", address, verdict.verdict, verdict.reason, emailcheck.SOURCE, verdict.checked_at)
                updated += 1
            known_invalid = verdict is not None and verdict.verdict == "invalid"
            if want_probe and not known_invalid and ("email", address, "smtp-rcpt") not in recent:
                result = patterns.verify_email(address, probe=self.probe)
                if result.status != "disabled":
                    self._write("email", address, result.status, result.reason, result.source, result.checked_at)
                    updated += 1

        for value, country in phones:
            if ("phone", value, phone.SOURCE) not in recent:
                verdict = phone.analyze(value, country)
                self._write("phone", value, verdict.verdict, verdict.reason, phone.SOURCE, verdict.checked_at)
                updated += 1

        message = None
        if updated:
            message = f"{updated} contact checks written ({len(emails)} emails, {len(phones)} numbers)"
        return found, updated, message
