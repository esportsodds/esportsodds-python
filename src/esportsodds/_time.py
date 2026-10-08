"""RFC 3339 timestamps, without depending on the interpreter's `fromisoformat`.

Before Python 3.11, `datetime.fromisoformat` cannot read a trailing `Z` and only accepts a fraction
of exactly 3 or 6 digits. The API's timestamps end in `Z`, and Go (which writes them) trims trailing
zeros from the fraction (`12.0045Z`) and can print nine digits, so both are parsed here.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_UTC = timezone.utc

_RFC3339 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,9}))?"
    r"(?:([Zz])|([+-])(\d{2}):(\d{2}))",
    re.ASCII,
)


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC 3339 timestamp such as ``2026-09-18T03:49:12.004518Z``.

    Accepts ``Z``, ``z`` or a numeric offset, and a fraction of one to nine digits (digits beyond
    microseconds are dropped, never rounded up). The result is timezone-aware and in UTC.
    Raises `ValueError` for anything else, including a timestamp with no zone: guessing one would
    silently shift every time by the caller's offset.
    """
    if not isinstance(value, str):
        raise TypeError(f"expected an RFC 3339 string, got {type(value).__name__}")
    m = _RFC3339.fullmatch(value)
    if m is None:
        raise ValueError(f"not an RFC 3339 timestamp: {value!r}")
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    micro = int((m.group(7) or "").ljust(6, "0")[:6])
    try:
        if m.group(8):
            tz = _UTC
        else:
            sign = -1 if m.group(9) == "-" else 1
            offset = timedelta(hours=int(m.group(10)), minutes=int(m.group(11)))
            if int(m.group(11)) > 59:
                raise ValueError("minutes out of range")
            tz = timezone(sign * offset)
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=tz).astimezone(_UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"not an RFC 3339 timestamp: {value!r}") from exc


def format_rfc3339(value: datetime) -> str:
    """Format a datetime as UTC RFC 3339 with a ``Z``: ``2026-09-18T03:49:12Z``.

    A naive datetime is read as UTC; an aware one is converted. The fraction is written only when
    it is not zero.
    """
    v = value.replace(tzinfo=_UTC) if value.tzinfo is None else value.astimezone(_UTC)
    text = f"{v.year:04d}-{v.month:02d}-{v.day:02d}T{v.hour:02d}:{v.minute:02d}:{v.second:02d}"
    if v.microsecond:
        text += f".{v.microsecond:06d}"
    return text + "Z"


def to_query_value(value: str | date | datetime) -> str:
    """Render a time-like query argument.

    A string is passed through untouched (the API is the validator, and a caller who already holds
    an RFC 3339 string should not have it re-parsed). A `datetime` becomes an RFC 3339 UTC
    timestamp; a plain `date` stays ``YYYY-MM-DD``, which is the form the date filters take.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):  # before `date`: datetime is a subclass of date
        return format_rfc3339(value)
    return value.isoformat()
