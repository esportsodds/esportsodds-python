"""Header helpers: a case-insensitive mapping and `Retry-After` parsing."""

from __future__ import annotations

import pytest

from esportsodds._headers import Headers, parse_retry_after


def test_headers_are_case_insensitive_and_keep_values() -> None:
    h = Headers({"X-Request-Id": "abc", "Retry-After": "2"})
    assert h["x-request-id"] == "abc"
    assert h["RETRY-AFTER"] == "2"
    assert h.get("Missing") is None
    assert h.get("Missing", "dflt") == "dflt"
    assert "retry-after" in h
    assert len(h) == 2


def test_headers_from_pairs_join_repeated_names() -> None:
    # HTTP lets a name repeat; the combined value is the comma-joined list (RFC 9110 5.3).
    h = Headers([("Set-Cookie", "a=1"), ("set-cookie", "b=2"), ("X-One", "1")])
    assert h["set-cookie"] == "a=1, b=2"
    assert h["x-one"] == "1"


def test_headers_do_not_alias_the_input() -> None:
    source = {"A": "1"}
    h = Headers(source)
    source["A"] = "2"
    assert h["a"] == "1"


def test_headers_repr_does_not_hide_the_values() -> None:
    # Response headers hold no secret (the key is sent, never received), so a readable repr helps
    # debugging. This pins that nobody "fixes" it into a blank one by accident.
    assert "x-request-id" in repr(Headers({"X-Request-Id": "abc"}))


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("0", 0.0),
        ("2", 2.0),
        ("1209600", 1209600.0),  # a quota 429 carries the seconds to the month's end
        ("1.5", 1.5),
        (" 7 ", 7.0),
        ("-3", None),  # RFC 9110: delay-seconds is non-negative
        ("abc", None),
        ("", None),
        (None, None),
        ("nan", None),
        ("inf", None),
    ],
)
def test_parse_retry_after_seconds(value: str | None, want: float | None) -> None:
    assert parse_retry_after(value, now=0.0) == want


def test_parse_retry_after_http_date_uses_the_injected_clock() -> None:
    # 2026-10-21 07:28:00 GMT is 1792567680 seconds after the epoch.
    date = "Wed, 21 Oct 2026 07:28:00 GMT"
    assert parse_retry_after(date, now=1792567680 - 30) == 30.0
    # A date already in the past means "now", not a negative wait.
    assert parse_retry_after(date, now=1792567680 + 500) == 0.0


def test_parse_retry_after_rejects_a_garbage_date() -> None:
    assert parse_retry_after("Someday, 99 Foo 2026 99:99:99 GMT", now=0.0) is None


@pytest.mark.usefixtures("tokyo_time")
def test_a_date_with_an_unknown_zone_is_read_as_utc_not_local_time() -> None:
    # "-0000" means "UTC, but the sender's local zone is unknown". Python parses it as naive, and a
    # naive datetime's .timestamp() would read it in the machine's zone (nine hours off here).
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 -0000", now=1792567680.0 - 30) == 30.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now=1792567680.0 - 30) == 30.0
