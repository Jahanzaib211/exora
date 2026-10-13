"""Deeper website extraction, record building, resolve of rich fields, exclusions."""

import pytest
from sqlalchemy import select

from prospector.connectors.domains import parse_domains
from prospector.connectors.website import WebsiteConnector
from prospector.crawl.extract import extract_page, filter_candidates, sitemap_locs
from prospector.models import Company, Exclusion, Signal
from prospector.resolve import CompanyRecord, ExcludedRecord, upsert_company

HOME = """
<html><head>
<meta name="description" content="Award-winning   solicitors in Manchester.">
<meta property="og:site_name" content="Smith &amp; Co Solicitors">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "LegalService", "name": "Smith & Co Solicitors",
 "telephone": "+44 161 000 1111", "email": "hello@smithco.co.uk",
 "address": {"@type": "PostalAddress", "streetAddress": "1 King St", "addressLocality": "Manchester",
             "postalCode": "M2 4LQ", "addressCountry": "GB"},
 "sameAs": ["https://www.linkedin.com/company/smith-co/", "https://twitter.com/smithco"]}
</script>
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-1"></script>
<script>var consent = "We comply with GDPR and use ChatGPT"; </script>
<link rel="stylesheet" href="/wp-content/themes/x/style.css">
</head><body>
<p>We use artificial intelligence responsibly. ISO 27001 certified.</p>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
<a href="https://www.facebook.com/smithcosolicitors">Facebook</a>
<a href="https://wa.me/447700900000">WhatsApp us</a>
<a href="/careers/">Join us</a>
<a href="https://boards.greenhouse.io/smithco">Open roles</a>
</body></html>
"""


def test_extract_rich_fields():
    f = extract_page(HOME, "https://www.smithco.co.uk/")
    assert f.description == "Award-winning solicitors in Manchester."
    assert f.site_name == "Smith & Co Solicitors"
    assert f.address == "1 King St, Manchester, M2 4LQ, GB"
    assert f.socials["linkedin"] == "https://linkedin.com/company/smith-co"
    assert f.socials["x"] == "https://twitter.com/smithco"
    assert f.socials["facebook"] == "https://facebook.com/smithcosolicitors"  # share link ignored
    assert {"WordPress", "Google Tag Manager"} <= f.tech
    assert f.careers_url == "https://www.smithco.co.uk/careers/"
    assert f.ats == ("greenhouse", "smithco")
    assert "hello@smithco.co.uk" in f.emails and "+441610001111" in f.phones
    # mentions come from visible text only: the GDPR/ChatGPT words inside <script> do not count
    assert set(f.mentions) == {"AI adoption", "Security certification", "WhatsApp channel"}


def test_sitemap_and_candidates():
    xml = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://smithco.co.uk/our-people/</loc></url><url><loc>https://smithco.co.uk/blog/x</loc></url>
    <url><loc> https://smithco.co.uk/contact-us </loc></url><url><loc>https://other.com/team</loc></url></urlset>"""
    locs = sitemap_locs(xml)
    assert len(locs) == 4
    assert filter_candidates(locs, "https://smithco.co.uk/", 10) == [
        "https://smithco.co.uk/our-people/",
        "https://smithco.co.uk/contact-us",
    ]


def test_build_record_merges_pages_and_signals():
    team = '<div class="team-member"><h3>Jane Doe</h3><p>Managing Partner</p></div>'
    rec = WebsiteConnector.build_record(
        ("smithco.co.uk", "smithco.co.uk"),
        "https://www.smithco.co.uk/",
        [("https://www.smithco.co.uk/team", team), ("https://www.smithco.co.uk/", HOME)],
    )
    assert rec.site_name == "Smith & Co Solicitors" and rec.description.startswith("Award-winning")
    assert rec.general_email == "hello@smithco.co.uk"
    assert [p.name for p in rec.people] == ["Jane Doe"]
    kinds = {k for k, _, _ in rec.signals}
    assert kinds == {"site_mention", "hiring"}
    assert rec.ats == ("greenhouse", "smithco")


def test_placeholder_name_replaced_by_site_name_and_rich_fields_merge(db):
    with db.session() as s:
        c, _ = upsert_company(s, CompanyRecord("import", None, "smithco.co.uk", website="smithco.co.uk"))
        assert c.name == "smithco.co.uk"
        rec = WebsiteConnector.build_record(("smithco.co.uk", "smithco.co.uk"), "https://smithco.co.uk/",
                                            [("https://smithco.co.uk/", HOME)])  # fmt: skip
        c2, created = upsert_company(s, rec)
        assert not created and c2.id == c.id
        assert c.name == "Smith & Co Solicitors"
        assert c.socials["x"] == "https://twitter.com/smithco" and c.linkedin_url
        assert "WordPress" in c.tech and c.ats == {"provider": "greenhouse", "token": "smithco"}
        # a real name is never overwritten by a website's self-description
        rec.site_name = "Something Else"
        upsert_company(s, rec)
        assert c.name == "Smith & Co Solicitors"
        assert s.query(Signal).filter_by(company_id=c.id).count() == 4  # 3 mentions + hiring, no duplicates


def test_excluded_companies_are_not_re_added(db):
    with db.session() as s:
        s.add(Exclusion(value="domain:spam.com", label="Spam Ltd"))
        s.flush()
        with pytest.raises(ExcludedRecord):
            upsert_company(s, CompanyRecord("osm", None, "Spam Ltd", website="https://spam.com"))
        assert s.scalar(select(Company.id)) is None


def test_parse_domains():
    text = "https://www.smithco.co.uk/team, acme.com\nacme.com; not a domain <jane@firm.io>"
    assert parse_domains(text) == ["smithco.co.uk", "acme.com", "firm.io"]
