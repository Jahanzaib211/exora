"""Pattern guesses (offline) and the opt-in SMTP probe (env-gated, catch-all aware, TTL-cached)."""

import time

import pytest

from prospector.contact import patterns


@pytest.fixture(autouse=True)
def _clean():
    patterns._cache.clear()
    patterns._last_probe.clear()
    yield
    patterns._cache.clear()
    patterns._last_probe.clear()


def test_common_patterns_and_confidences():
    got = {c.email: c.confidence for c in patterns.guess_patterns("ada lovelace", "exora.com")}
    assert got["ada.lovelace@exora.com"] == 0.9
    assert got["alovelace@exora.com"] == 0.75
    assert got["adal@exora.com"] == 0.65
    assert got["ada@exora.com"] == 0.6
    assert got["lovelace@exora.com"] == 0.45


def test_candidates_carry_their_pattern_name():
    got = {c.email: c.pattern for c in patterns.guess_patterns("ada lovelace", "exora.com")}
    assert got["ada.lovelace@exora.com"] == "first.last"
    assert got["alovelace@exora.com"] == "flast"


def test_titles_are_stripped_and_names_folded():
    got = {c.email for c in patterns.guess_patterns("Dr. ADA Lovelace", "exora.com")}
    assert "ada.lovelace@exora.com" in got
    got = {c.email for c in patterns.guess_patterns("jos\u00e9 garc\u00eda", "exora.com")}
    assert "jose.garcia@exora.com" in got


def test_single_name_yields_only_the_first_name_guess():
    got = [c.email for c in patterns.guess_patterns("cher", "exora.com")]
    assert got == ["cher@exora.com"]


def test_empty_inputs_yield_nothing():
    assert patterns.guess_patterns("", "exora.com") == []
    assert patterns.guess_patterns("   ", "exora.com") == []
    assert patterns.guess_patterns("ada lovelace", "") == []
    assert patterns.guess_patterns("ada lovelace", "https://") == []


def test_probe_is_off_unless_the_operator_turns_it_on(monkeypatch):
    monkeypatch.delenv("PROSPECTOR_SMTP_PROBE", raising=False)
    assert patterns.probe_enabled() is False
    monkeypatch.setenv("PROSPECTOR_SMTP_PROBE", "1")
    assert patterns.probe_enabled() is True
    monkeypatch.setenv("PROSPECTOR_SMTP_PROBE", "off")
    assert patterns.probe_enabled() is False


def test_probe_sender_defaults_to_the_null_reverse_path(monkeypatch):
    monkeypatch.delenv("PROSPECTOR_PROBE_FROM", raising=False)
    assert patterns.probe_sender() == "<>"
    monkeypatch.setenv("PROSPECTOR_PROBE_FROM", "ops@example.com")
    assert patterns.probe_sender() == "ops@example.com"


def test_probe_ttl_default_floor_and_garbage(monkeypatch):
    monkeypatch.delenv("PROSPECTOR_PROBE_TTL_HOURS", raising=False)
    assert patterns.probe_ttl() == 24 * 3600
    monkeypatch.setenv("PROSPECTOR_PROBE_TTL_HOURS", "0")  # a floor keeps one address from re-probing every second
    assert patterns.probe_ttl() == 300
    monkeypatch.setenv("PROSPECTOR_PROBE_TTL_HOURS", "soon")
    assert patterns.probe_ttl() == 24 * 3600


def test_verify_email_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PROSPECTOR_SMTP_PROBE", raising=False)
    r = patterns.verify_email("ada@exora.com")
    assert r.status == "disabled" and r.source == "smtp-rcpt" and "PROSPECTOR_SMTP_PROBE" in r.reason


def test_verify_email_rejects_non_addresses():
    assert patterns.verify_email("nope").status == "unknown"
    assert patterns.verify_email("").status == "unknown"


def test_verify_email_reports_accepted_after_the_catchall_probe(monkeypatch):
    monkeypatch.setenv("PROSPECTOR_SMTP_PROBE", "1")
    seen = []

    def probe(address: str) -> bool | None:
        seen.append(address)
        if address.startswith("probe."):  # a made-up local part
            return False  # not a catch-all
        return address.startswith("ada")

    r = patterns.verify_email("ada@exora.com", probe=probe)
    assert r.status == "accepted" and r.cached is False
    assert any(a.startswith("probe.") for a in seen)  # catch-all checked first
    again = patterns.verify_email("ada@exora.com", probe=probe)
    assert again.cached is True and again.status == "accepted"
    assert seen.count("ada@exora.com") == 1  # the TTL cache never re-probes inside its window


def test_verify_email_reports_rejected_and_unknown(monkeypatch):
    monkeypatch.setenv("PROSPECTOR_SMTP_PROBE", "1")

    def probe(address: str) -> bool | None:
        if address.startswith("probe."):
            return False  # not a catch-all
        if address.startswith("nope"):
            return False  # 5xx: this mailbox does not exist
        return None  # greylisting / no answer

    assert patterns.verify_email("nope@exora.com", probe=probe).status == "rejected"
    # unknown is never cached: a retry may answer differently
    assert patterns.verify_email("maybe@exora.com", probe=probe).status == "unknown"
    assert patterns.verify_email("maybe@exora.com", probe=probe).status == "unknown"


def test_catch_all_domain_never_claims_acceptance(monkeypatch):
    monkeypatch.setenv("PROSPECTOR_SMTP_PROBE", "1")
    r = patterns.verify_email("anyone@exora.com", probe=lambda address: True)
    assert r.status == "catch-all" and "catch-all" in r.reason


def test_is_catchall_three_answers(monkeypatch):
    monkeypatch.delenv("PROSPECTOR_SMTP_PROBE", raising=False)
    assert patterns.is_catchall("exora.com", probe=lambda a: False) is False
    assert patterns.is_catchall("exora.com", probe=lambda a: True) is True
    assert patterns.is_catchall("exora.com", probe=lambda a: None) is None
    assert patterns.is_catchall("exora.com") is None  # probing off: probe_address answers nothing (no DNS)


def test_throttle_paces_one_probe_per_second_per_host(monkeypatch):
    delays = []
    monkeypatch.setattr(time, "sleep", delays.append)
    patterns._last_probe["mx.exora.com"] = time.monotonic() - 0.9  # the last probe was 0.9s ago
    patterns._throttle("mx.exora.com")
    assert delays and 0 < delays[0] <= 0.3  # the slot is one full interval after the previous one
    patterns._last_probe.clear()


def test_random_localpart_cannot_be_a_real_mailbox():
    part = patterns.random_localpart()
    assert part.startswith("probe.") and len(part) > 12
    assert part != patterns.random_localpart()
