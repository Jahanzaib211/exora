from datetime import date

from prospector.fit import evaluate_fit
from prospector.models import QualityCheck, Signal
from prospector.resolve import CompanyRecord, PersonRecord, upsert_company
from prospector.verify import DnsCache, dmarc_policy, email_provider, score, tier_of, verify_company


def fake_dns(domain: str, rtype: str) -> list[str]:
    if domain == "_dmarc.good.com":
        return []
    if domain != "good.com":
        return []
    return ["good-com.mail.protection.outlook.com"] if rtype == "MX" else ["1.2.3.4"]


def test_score_uses_only_applicable_checks():
    assert score({"has_website": (True, ""), "registry_active": (None, "")}) == 100
    assert score({"has_website": (True, ""), "domain_resolves": (False, "")}) == 50


def test_email_provider_from_mx():
    assert email_provider(["acme-com.mail.protection.outlook.com"]) == "Microsoft 365"
    assert email_provider(["aspmx.l.google.com", "alt1.aspmx.l.google.com"]) == "Google Workspace"
    assert email_provider(["mx1.mimecast.com"]) == "Mimecast (gateway)"
    assert email_provider(["mail.acme.com"]) == "Self-hosted / other"
    assert email_provider([]) is None


def test_verify_records_every_check_and_scores_fit(db):
    cache = DnsCache(resolver=fake_dns, rdap=lambda domain: None)  # offline: no registry lookups
    with db.session() as s:
        good, _ = upsert_company(
            s,
            CompanyRecord(
                "website",
                None,
                "Good Co",
                website="good.com",
                country="GB",
                segment="Law",
                general_email="info@good.com",
                people=[PersonRecord("Ann Lee", "Founder")],
                signals=[("site_mention", "Website mentions AI adoption", "https://good.com/")],
            ),
        )
        bad, _ = upsert_company(s, CompanyRecord("osm", None, "Bad Co", website="bad.com"))
        assert verify_company(s, good, cache) > verify_company(s, bad, cache)
        assert good.email_provider == "Microsoft 365"
        checks = {q.check: q.passed for q in s.query(QualityCheck).filter_by(entity_id=bad.id)}
        assert checks["domain_resolves"] is False and checks["has_contact"] is False
        assert "registry_active" not in checks  # not applicable: no registry source
        fit, reasons = evaluate_fit(s, good)
        met = {r["rule"] for r in reasons if r["met"]}
        assert {"segment", "market", "decision_maker", "contact", "cloud_mail", "ai"} <= met
        assert "established" not in met
        assert fit == good.fit_score == 70
        assert s.query(Signal).filter_by(company_id=good.id).count() == 1
        assert good.email_security == "missing" and bad.email_security is None  # no MX: not evaluated
        # a domain registered long ago marks an established firm
        old = DnsCache(resolver=fake_dns, rdap=lambda domain: date(2001, 5, 1))
        good.domain_created_on = None
        verify_company(s, good, old)
        assert good.domain_created_on == date(2001, 5, 1) and good.fit_score == 80


def test_dmarc_policy():
    assert dmarc_policy(['"v=DMARC1; p=reject; rua=mailto:x@y.com"']) == "reject"
    assert dmarc_policy(['"v=DMARC1;" "p=quarantine"']) == "quarantine"
    assert dmarc_policy(['"v=DMARC1; rua=mailto:x@y.com"']) == "none"
    assert dmarc_policy(['"google-site-verification=abc"']) == "missing"
    assert dmarc_policy([]) == "missing"


def _results(**ok):
    keys = ["has_website", "domain_resolves", "domain_has_mx", "has_contact", "has_decision_maker", "corroborated"]
    return {k: (ok.get(k, True), "") for k in keys}


def test_tier_rules():
    assert tier_of(_results(), 85, 65) == "golden"
    assert tier_of(_results(corroborated=False), 85, 65) == "verified"  # one source is not enough for golden
    assert tier_of(_results(), 85, 55) == "verified"  # fit below the golden bar
    assert tier_of(_results(has_contact=False), 70, 70) == "partial"
    assert tier_of(_results(has_website=False, has_contact=False, domain_resolves=False), 10, 10) == "raw"
