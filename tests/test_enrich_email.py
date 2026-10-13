"""Email validation verdicts: RFC syntax via email-validator, then public DNS through an injected resolver."""

from datetime import datetime

from prospector.contact import emailcheck
from prospector.contact.emailcheck import DnsTemporaryError, check_email, spf_issues


def resolver(mx=None, a=None, txt=None, fail=False):
    """Fake DNS: a (domain, rtype) -> records lookup that records what it was asked."""
    seen = []

    def look(domain, rtype):
        seen.append((domain, rtype))
        if fail:
            raise DnsTemporaryError("timed out")
        return {"MX": mx or [], "A": a or [], "AAAA": [], "TXT": txt or []}.get(rtype, [])

    look.seen = seen
    return look


def test_valid_address_with_mx():
    look = resolver(mx=["mx.smithlaw.co.uk"])
    v = check_email("Jane@SmithLaw.co.uk", resolver=look)
    assert v.verdict == "valid" and "MX present (mx.smithlaw.co.uk)" in v.reason
    assert v.email == "jane@smithlaw.co.uk"  # normalized to lower case
    assert v.source == "email-validator+dns" and isinstance(v.checked_at, datetime)


def test_syntax_errors_are_invalid_without_dns():
    look = resolver()
    for bad in ("not-an-email", "jane@", "@smithlaw.co.uk", "jane @smithlaw.co.uk", ""):
        v = check_email(bad, resolver=look)
        assert v.verdict == "invalid" and v.reason
    assert look.seen == []  # nothing is looked up when the address cannot be an address


def test_null_mx_is_invalid():
    v = check_email("jane@smithlaw.co.uk", resolver=resolver(mx=["."]))
    assert v.verdict == "invalid" and "null MX" in v.reason


def test_missing_mx_falls_back_to_a_record():
    look = resolver(a=["93.184.216.34"])
    v = check_email("jane@smithlaw.co.uk", resolver=look)
    assert v.verdict == "risky" and "implicit MX" in v.reason
    assert ("smithlaw.co.uk", "A") in look.seen


def test_domain_that_does_not_exist_is_invalid():
    v = check_email("jane@nowhere-fdm9x.com", resolver=resolver())
    assert v.verdict == "invalid" and "does not exist" in v.reason


def test_dns_failure_is_unknown_not_invalid():
    v = check_email("jane@smithlaw.co.uk", resolver=resolver(fail=True))
    assert v.verdict == "unknown" and "DNS query for MX failed" in v.reason


def test_reject_all_spf_is_risky():
    look = resolver(mx=["mx.smithlaw.co.uk"], txt=['"v=spf1" "-all"'])
    v = check_email("jane@smithlaw.co.uk", resolver=look)
    assert v.verdict == "risky" and "SPF -all" in v.reason


def test_several_spf_records_are_risky():
    look = resolver(mx=["mx.smithlaw.co.uk"], txt=["v=spf1 ~all", "v=spf1 -all"])
    v = check_email("jane@smithlaw.co.uk", resolver=look)
    assert v.verdict == "risky" and "several SPF" in v.reason


def test_idn_domain_is_queried_in_punycode():
    look = resolver(mx=["mx.example.com"])
    v = check_email("info@exämple.com", resolver=look)
    assert v.verdict == "valid"
    assert any(domain.startswith("xn--") for domain, _ in look.seen)


def test_smtputf8_local_part_is_risky_without_any_lookup():
    look = resolver()
    v = check_email("josé@example.com", resolver=look)
    assert v.verdict == "risky" and "SMTPUTF8" in v.reason
    assert look.seen == []


def test_spf_issues_helper():
    assert spf_issues([]) is None
    assert spf_issues(['"v=spf1" "include:_spf.google.com" "~all"']) is None
    assert spf_issues(["v=spf1 +all"]) is None  # permissive, not a bounce risk
    assert "several SPF" in spf_issues(["v=spf1 ~all", "v=spf1 ~all"])
    assert "-all" in spf_issues(["v=spf1 -all"])


def test_source_is_reexported():
    assert emailcheck.SOURCE == "email-validator+dns"
