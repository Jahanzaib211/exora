"""Offline phone verdicts: the phonenumbers number plans judge valid / possible / invalid without a wire."""

from datetime import datetime

from prospector.contact import phone


def test_valid_international_numbers_normalize_to_e164():
    assert phone.e164("+966 11 402 1234", "SA") == "+966114021234"
    assert phone.e164("+971 4 402 1234", "AE") == "+97144021234"
    assert phone.e164("+1 415 555 2671", "US") == "+14155552671"
    assert phone.e164("+44 20 7946 0958", "GB") == "+442079460958"


def test_local_formats_are_judged_with_the_region_hint():
    v = phone.analyze("0161 832 1234", "GB")
    assert v.verdict == "valid" and v.e164 == "+441618321234"
    assert v.region == "GB" and "valid" in v.reason

    v = phone.analyze("041234567", "AE")
    assert v.verdict == "possible" and v.e164 is None  # plausible shape, not a known allocation


def test_lenient_numbers_are_possible_not_valid():
    v = phone.analyze("+1 555 555 5555")
    assert v.verdict == "possible" and v.e164 is None  # the 555 exchange is not a real allocation
    assert "possible" in v.reason

    v = phone.analyze("07700 900123", "GB")  # the Ofcom fictional range: plausible shape, not allocated
    assert v.verdict == "possible" and v.e164 is None


def test_junk_is_invalid_with_a_reason():
    assert phone.analyze("").verdict == "invalid"
    assert phone.analyze("not a number").verdict == "invalid"
    assert "cannot parse" in phone.analyze("not a number").reason
    assert phone.analyze("0161 832 1234").verdict == "invalid"  # local format, no region to judge it with
    short = phone.analyze("123", "US")
    assert short.verdict == "invalid" and "wrong length" in short.reason


def test_verdicts_carry_provenance():
    v = phone.analyze("+44 20 7946 0958")
    assert v.source == "phonenumbers"
    assert isinstance(v.checked_at, datetime)
    assert v.number_type and v.raw == "+44 20 7946 0958"


def test_e164_refuses_possible_and_invalid_numbers():
    assert phone.e164("+1 555 555 5555") is None
    assert phone.e164("garbage") is None
    assert phone.e164(None) is None


def test_candidate_cleans_tokens_before_storage():
    assert phone.candidate("+44 20 7946 0958") == "+442079460958"  # valid international -> E.164
    assert phone.candidate("+1 555 555 5555") == "+15555555555"  # possible -> kept as typed
    assert phone.candidate("0161 832 1234") == "01618321234"  # local format, no region yet -> digits
    assert phone.candidate("0161 832 1234", "GB") == "+441618321234"  # region known -> E.164
    assert phone.candidate("tel: +44 20 7946 0958".replace("tel: ", "")) == "+442079460958"


def test_candidate_drops_impossible_tokens():
    assert phone.candidate(None) is None
    assert phone.candidate("not a number") is None
    assert phone.candidate("123") is None  # too short to be a number
    assert phone.candidate("12345678901234567890") is None  # longer than E.164 allows
    assert phone.candidate("+1 555 555 5555 x +44") is None  # two country markers: not one number
