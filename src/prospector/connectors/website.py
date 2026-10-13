"""Website enrichment: crawl company sites already in the database and attach what they publish."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import zlib
from collections import defaultdict
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from ..crawl.extract import FoundPerson, context, extract_page, phone_context
from ..models import Company, utcnow
from ..normalize import ascii_fold, best_contact_email, email_kind
from ..resolve import CompanyRecord, EvidenceRecord, PersonRecord, page_evidence
from .base import Connector, ParamSpec

log = logging.getLogger(__name__)

BATCH_TIMEOUT_S = 60 * 60
SEGMENTS = ["Law", "CPA", "MSP", "Healthcare", "AI startup", "KSA SME", "EU mid-market"]


def _decode_crawl_output(raw: bytes) -> str:
    """Recover the crawler's JSONL from the bytes it wrote to stdout.

    The payload is a gzip stream (see crawl.spider), and three outcomes have to be tolerated
    without losing the pages that did arrive:

    * a crawl killed part way through leaves a truncated stream - `decompressobj` still hands back
      everything it managed to decode instead of raising, so only the tail is lost;
    * a stray write ahead of the payload makes it not gzip at all - `zlib.error` - and the bytes are
      then read as they are, which is also what an unversioned plain payload looks like;
    * no output at all is an empty result, not an error, so the caller can report it precisely.

    Retrying is deliberately absent: this is a pipe, and there is nothing to wait for. The
    file-based version spent its retries on a scanner that would never lift the block.
    """
    if not raw:
        return ""
    try:
        data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
    except zlib.error:
        return raw.decode("utf-8", "replace")  # not gzip: take the bytes at face value
    return data.decode("utf-8", "replace")


def _detail(p: FoundPerson) -> int:
    """How much one sighting tells us — the most complete record of a person wins."""
    return sum(1 for v in (p.title, p.email, p.phone, p.linkedin_url) if v)


def _owner_of(email: str, people: list[PersonRecord]) -> PersonRecord | None:
    """jane.doe@ / jdoe@ / jane@ belongs to Jane Doe when unambiguous."""
    local = re.sub(r"[^a-z]", "", email.split("@", 1)[0].lower())
    hits = []
    for p in people:
        parts = [re.sub(r"[^a-z]", "", w) for w in ascii_fold(p.name).lower().split()]
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        if not first or not last:
            # A name with no Latin letters in one of its tokens (a JSON-LD person named "1 2",
            # "!! ??") leaves an empty string here. Indexing it crashed the whole crawl run
            # (runs 75, 111 and 225 all ended "IndexError: string index out of range"), so the
            # run stopped early and the companies after it were never crawled.
            continue
        if local in {first + last, first[0] + last, first, last + first, first + last[0]}:
            hits.append(p)
    return hits[0] if len(hits) == 1 else None


class WebsiteConnector(Connector):
    key = "website"
    name = "Company websites"
    description = (
        "Crawls company sites (home, sitemap, team, about, contact pages) for people and titles, business "
        "emails and phones, social profiles, technology, job boards and buying signals."
    )
    licence = "Public pages; robots.txt obeyed"
    needs_db = True
    params = [
        ParamSpec("segment", "Segment", "select", False, SEGMENTS),
        ParamSpec("country", "Country (ISO-2)", "text", False),
        ParamSpec("limit", "Max companies", "int", False, default=25),
        ParamSpec("recrawl_days", "Skip if crawled within (days)", "int", False, default=30,
                  help="0 re-crawls everything selected"),
        ParamSpec("company_ids", "Company IDs", "ids", False, hidden=True),
    ]  # fmt: skip

    def __init__(self, http, api_key=None, db=None) -> None:
        super().__init__(http, api_key)
        self.db = db
        self.crawled_ids: list[int] = []  # the run marks these after its own commit (no second writer)
        self.fetch_note: str | None = None  # set when the crawl ran but nothing was retrievable

    def targets(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        limit = int(params.get("limit") or 25)
        with self.db.session() as s:
            q = select(Company.id, Company.website).where(Company.website.is_not(None))
            ids = params.get("company_ids")
            if ids:
                q = q.where(Company.id.in_(ids))  # explicit request: ignore recency and filters
                limit = max(limit, len(ids))
            else:
                days = int(params.get("recrawl_days") if params.get("recrawl_days") is not None else 30)
                if days > 0:
                    cutoff = utcnow() - timedelta(days=days)
                    q = q.where(or_(Company.last_crawled_at.is_(None), Company.last_crawled_at < cutoff))
                if params.get("segment"):
                    q = q.where(Company.segment == params["segment"])
                if params.get("country"):
                    q = q.where(Company.country == str(params["country"]).upper())
            # never-crawled first, then best fit
            q = q.order_by(Company.last_crawled_at.is_not(None), Company.fit_score.desc().nullslast(), Company.id)
            return [{"company_id": cid, "website": w} for cid, w in s.execute(q.limit(limit))]

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        targets = self.targets(params)
        if not targets:
            return
        # No temp directory: targets go in on stdin and results come back on stdout, so the scraped
        # HTML never becomes a file for the real-time scanner to block. Captured as bytes rather
        # than text because the payload is gzip; stderr is decoded leniently because it is only ever
        # quoted inside an error message, and a stray non-UTF-8 byte there must not fail the run.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "prospector.crawl.spider"],
                input=json.dumps(targets).encode("utf-8"),
                capture_output=True,
                timeout=BATCH_TIMEOUT_S,
                env=env,
            )
            stderr = proc.stderr.decode("utf-8", "replace")
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"crawler timed out after {BATCH_TIMEOUT_S:.0f}s") from None

        # Decode whatever was produced before judging the exit code. The old check was
        # `returncode != 0 and not outp.exists()`, so a crawler that wrote partial output and
        # then died was reported as a *successful* run.
        pages: dict[int, list[tuple[str, str]]] = defaultdict(list)
        summary: dict[str, Any] | None = None
        text = _decode_crawl_output(proc.stdout)
        for lineno, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A truncated final line is expected when the crawler is killed mid-write.
                log.warning("crawl output line %d is not valid JSON; ignoring", lineno)
                continue
            if "company_id" in row:
                pages[row["company_id"]].append((row["url"], row["html"]))
            elif "summary" in row:
                summary = row["summary"]  # written last, so its presence proves the crawl finished

        if returncode != 0:
            raise RuntimeError(f"crawler exited {returncode} after {len(pages)}/{len(targets)} sites: {stderr[-500:]}")
        if not pages:
            if summary is None:
                # exit 0 without the closing summary means the crawl never really ran to completion,
                # which is a fault and should be reported as one.
                raise RuntimeError(
                    f"crawler produced no pages for {len(targets)} sites: {stderr[-500:] or '(no output)'}"
                )
            # The crawl ran to completion and not one target produced a page: dead DNS, a refused
            # connection, a 429. That is a property of the targets, not a fault in the crawler.
            # Raising here turned every scheduled slot red for as long as the queue held nothing
            # but unreachable hosts - and because a target that yields nothing is never stamped
            # `last_crawled_at`, it was selected again on the very next run, so the queue could
            # never drain. 111 of 252 website runs failed this way.
            self.fetch_note = (
                f"0 of {len(targets)} sites returned a page "
                f"({summary.get('errors', 0)} requests failed: DNS, refused or rate-limited)"
            )
            log.warning("crawl of %d sites returned no pages: %s", len(targets), summary)
            return

        names = self._names([t["company_id"] for t in targets])
        # Only sites that actually returned pages count as crawled. Marking every target here meant a
        # failed or partial crawl still stamped `last_crawled_at`, and the `recrawl_days` filter then
        # suppressed those companies for 30 days.
        self.crawled_ids = sorted(pages)
        for t in targets:
            cid = t["company_id"]
            if cid in pages and cid in names:
                yield self.build_record(names[cid], t["website"], pages[cid])

    def _names(self, ids: list[int]) -> dict[int, tuple[str, str | None]]:
        with self.db.session() as s:
            return {c.id: (c.name, c.domain) for c in s.scalars(select(Company).where(Company.id.in_(ids)))}

    @staticmethod
    def build_record(ident: tuple[str, str | None], website: str, pages: list[tuple[str, str]]) -> CompanyRecord:
        """Merge the findings of all crawled pages of one site into one sourced record, with receipts."""
        name, domain = ident
        emails: dict[str, str] = {}
        phones: dict[str, str] = {}  # digits -> page
        people: dict[str, PersonRecord] = {}
        socials: dict[str, str] = {}
        tech: dict[str, str] = {}  # technology -> page
        mentions: dict[str, str] = {}
        first: dict[str, str] = {}  # field -> page where it was first found
        description = site_name = address = careers = None
        ats = None
        evidence: list[EvidenceRecord] = []
        texts: dict[str, str] = {}
        # home page first: its description and name describe the whole organisation
        ordered = sorted(pages, key=lambda p: 0 if p[0].rstrip("/") == website.rstrip("/") else 1)
        for url, html in ordered:
            f = extract_page(html, url)
            evidence.append(page_evidence(url, html, f.title))
            texts[url] = f.text
            for e in sorted(f.emails):
                emails.setdefault(e, url)
            for p in sorted(f.phones):
                phones.setdefault(p, url)
            for p in f.people:
                key = p.name.lower()
                prev = people.get(key)
                # Keep the richest sighting rather than the first: a name mentioned in passing on
                # the home page loses to the full card on /team. A record is never built from two
                # pages — `url` and `excerpt` describe every field on it — so the receipt behind a
                # stored value always points at the page that actually carried it.
                if prev is None or _detail(p) > _detail(prev):
                    people[key] = PersonRecord(
                        name=p.name,
                        title=p.title,
                        email=p.email,
                        url=p.url,
                        phone=p.phone,
                        linkedin_url=p.linkedin_url,
                        excerpt=context(f.text, p.name),
                    )
            for network, link in f.socials.items():
                if network not in socials:
                    socials[network], first[f"social:{network}"] = link, url
            for li in sorted(f.linkedin):
                if "linkedin" not in socials:
                    socials["linkedin"], first["social:linkedin"] = li, url
            for t in f.tech:
                tech.setdefault(t, url)
            for label, page in f.mentions.items():
                mentions.setdefault(label, page)
            for fname, value in (("description", f.description), ("site_name", f.site_name),
                                 ("address", f.address), ("careers_url", f.careers_url)):  # fmt: skip
                if value and fname not in first:
                    first[fname] = url
            description = description or f.description
            site_name = site_name or f.site_name
            address = address or f.address
            careers = careers or f.careers_url
            ats = ats or f.ats
        plist = list(people.values())
        role = [e for e in emails if email_kind(e, domain) == "role"]
        for e in emails:
            if email_kind(e, domain) == "published":
                owner = _owner_of(e, plist)
                if owner and not owner.email:
                    owner.email = e
        general = best_contact_email(role)
        phone = next(iter(phones), None)
        receipts: dict[str, tuple[str | None, str | None]] = {}
        if general:
            receipts["general_email"] = (emails[general], context(texts.get(emails[general], ""), general))
        if phone:
            page = phones[phone]
            receipts["phone"] = (page, phone_context(texts.get(page, ""), phone))
        for fname, value in (("description", description), ("address", address), ("careers_url", careers)):
            if value and fname in first:
                receipts[fname] = (first[fname], context(texts.get(first[fname], ""), value[:60]) or None)
        for key, page in first.items():
            if key.startswith("social:"):
                receipts[key] = (page, None)
        for t, page in tech.items():
            receipts[f"tech:{t}"] = (page, None)
        signals = [("site_mention", f"Website mentions {label}", page) for label, page in sorted(mentions.items())]
        if careers or ats:
            signals.append(("hiring", "Has a careers page" if careers else "Has a public job board", careers))
        return CompanyRecord(
            source="website",
            url=website,
            name=name,
            website=website,
            address=address,
            phone=phone,
            general_email=general,
            description=description,
            careers_url=careers,
            site_name=site_name,
            socials=socials,
            tech=sorted(tech),
            ats=ats,
            confidence=0.9,
            people=plist,
            signals=signals,
            evidence=evidence,
            receipts=receipts,
        )
