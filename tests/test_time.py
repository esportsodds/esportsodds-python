"""RFC 3339 helpers.

`datetime.fromisoformat` before Python 3.11 cannot read a trailing `Z`, and before 3.11 it only
accepts a fraction of exactly 3 or 6 digits. The API's timestamps carry a `Z`, and Go trims
trailing zeros from the fraction ("12.0045Z") and can print nine digits, so the client parses them
itself.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from esportsodds._time import format_rfc3339, parse_rfc3339, to_query_value

UTC = timezone.utc


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("2026-09-18T03:49:12Z", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
        ("2026-09-18T03:49:12z", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
        ("2026-09-18t03:49:12Z", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
        ("2026-09-18 03:49:12Z", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
        # Fractions of every length Go can print (it trims trailing zeros).
        ("2026-09-18T03:49:12.5Z", datetime(2026, 9, 18, 3, 49, 12, 500000, tzinfo=UTC)),
        ("2026-09-18T03:49:12.0045Z", datetime(2026, 9, 18, 3, 49, 12, 4500, tzinfo=UTC)),
        ("2026-09-18T03:49:12.004518Z", datetime(2026, 9, 18, 3, 49, 12, 4518, tzinfo=UTC)),
        # Nine digits are truncated to microseconds, never rounded up into the next second.
        ("2026-09-18T03:49:12.999999999Z", datetime(2026, 9, 18, 3, 49, 12, 999999, tzinfo=UTC)),
        ("2026-09-18T03:49:12.123456789Z", datetime(2026, 9, 18, 3, 49, 12, 123456, tzinfo=UTC)),
        # Offsets are converted to UTC.
        ("2026-09-18T05:49:12+02:00", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
        ("2026-09-17T22:19:12-05:30", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
        ("2026-09-18T03:49:12+00:00", datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC)),
    ],
)
def test_parse_rfc3339_accepts(text: str, want: datetime) -> None:
    got = parse_rfc3339(text)
    assert got == want
    assert got.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "2026-09-18",  # a date, not a timestamp
        "2026-09-18T03:49:12",  # no zone: ambiguous, so refused rather than guessed
        "2026-13-18T03:49:12Z",  # month 13
        "2026-02-30T03:49:12Z",  # not a day
        "2026-09-18T25:49:12Z",
        "2026-09-18T03:49:12.Z",  # a dot with no digits
        "2026-09-18T03:49:12.1234567890Z",  # ten digits is not a thing Go prints
        "not a timestamp",
        "2026-09-18T03:49:12Zjunk",
        # Numeric offsets: RFC 3339 allows hours 00-23 and minutes 00-59, nothing past that.
        "2026-09-18T03:49:12+02:60",  # the minutes check is its own rule: timezone() accepts it
        "2026-09-18T03:49:12+02:75",  # as +03:15, a different instant, silently
        "2026-09-18T03:49:12-00:99",
        "2026-09-18T03:49:12+24:00",
        "2026-09-18T03:49:12+99:00",
        "2026-09-18T03:49:12+0200",  # no colon
        "2026-09-18T03:49:12+02",  # no minutes
    ],
)
def test_parse_rfc3339_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="RFC 3339"):
        parse_rfc3339(text)


def test_parse_rfc3339_rejects_non_strings() -> None:
    with pytest.raises(TypeError):
        parse_rfc3339(123)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "want"),
    [
        (datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC), "2026-09-18T03:49:12Z"),
        (datetime(2026, 9, 18, 3, 49, 12, 4518, tzinfo=UTC), "2026-09-18T03:49:12.004518Z"),
        # An aware datetime in another zone is converted, never relabelled.
        (
            datetime(2026, 9, 18, 5, 49, 12, tzinfo=timezone(timedelta(hours=2))),
            "2026-09-18T03:49:12Z",
        ),
        # A naive datetime is read as UTC (the documented convention).
        (datetime(2026, 9, 18, 3, 49, 12), "2026-09-18T03:49:12Z"),
    ],
)
def test_format_rfc3339(value: datetime, want: str) -> None:
    assert format_rfc3339(value) == want


def test_format_and_parse_round_trip() -> None:
    original = datetime(2026, 9, 18, 3, 49, 12, 4518, tzinfo=UTC)
    assert parse_rfc3339(format_rfc3339(original)) == original


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("2026-09-18T03:49:12Z", "2026-09-18T03:49:12Z"),  # strings pass through untouched
        ("anything the caller typed", "anything the caller typed"),
        (datetime(2026, 9, 18, 3, 49, 12, tzinfo=UTC), "2026-09-18T03:49:12Z"),
        (date(2026, 9, 18), "2026-09-18"),  # a date stays a date: the API wants YYYY-MM-DD
    ],
)
def test_to_query_value(value: str | date | datetime, want: str) -> None:
    assert to_query_value(value) == want


def test_a_datetime_is_a_date_but_formats_as_a_timestamp() -> None:
    # `datetime` subclasses `date`; the order of the isinstance checks decides which wins.
    assert to_query_value(datetime(2026, 9, 18, 0, 0, tzinfo=UTC)) == "2026-09-18T00:00:00Z"


# --- the machine's own timezone must never leak in -----------------------------------------------


@pytest.mark.usefixtures("tokyo_time")
def test_a_naive_datetime_is_utc_whatever_timezone_the_machine_is_in() -> None:
    naive = datetime(2026, 9, 18, 12, 0, 0)
    assert format_rfc3339(naive) == "2026-09-18T12:00:00Z"
    assert to_query_value(naive) == "2026-09-18T12:00:00Z"


@pytest.mark.usefixtures("tokyo_time")
def test_parsing_does_not_depend_on_the_machines_timezone() -> None:
    parsed = parse_rfc3339("2026-09-18T12:00:00Z")
    assert parsed == datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    assert parsed.timestamp() == 1789732800.0
    assert parse_rfc3339("2026-09-18T21:00:00+09:00") == parsed


@pytest.mark.usefixtures("tokyo_time")
def test_the_fixture_really_moves_the_clock() -> None:
    # Guards the guard: if local time did not really move, the tests above would prove nothing.
    local = datetime(2026, 9, 18, 12, 0).astimezone()
    assert local.utcoffset() == timedelta(hours=9)
