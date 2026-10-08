"""HTTP header helpers: a case-insensitive mapping and `Retry-After` parsing."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import timezone
from email.utils import parsedate_to_datetime

__all__ = ["Headers", "parse_retry_after"]


class Headers(Mapping[str, str]):
    """Response headers: read-only, with names compared case-insensitively.

    A name that appears more than once is combined into one comma-separated value
    (RFC 9110 section 5.3), so `Headers` never silently drops a repeat.
    """

    __slots__ = ("_items",)

    def __init__(
        self,
        source: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> None:
        items: dict[str, str] = {}
        pairs = source.items() if isinstance(source, Mapping) else (source or ())
        for name, value in pairs:
            key = name.lower()
            items[key] = f"{items[key]}, {value}" if key in items else value
        self._items = items

    def __getitem__(self, name: str) -> str:
        return self._items[name.lower()]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"Headers({self._items!r})"


_DELAY_SECONDS = re.compile(r"\d+(?:\.\d+)?", re.ASCII)


def parse_retry_after(value: str | None, *, now: float) -> float | None:
    """Seconds to wait, from a `Retry-After` value, or None when it is absent or unreadable.

    The API sends whole seconds. RFC 9110 also allows an HTTP date (a proxy in front of it might
    send one), which is resolved against `now` (seconds since the epoch, injected so tests do not
    depend on the clock). A date already in the past is a wait of zero, never a negative one.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if _DELAY_SECONDS.fullmatch(text):
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if (
        when.tzinfo is None
    ):  # a "-0000" zone parses as naive, and naive means local time to .timestamp()
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - now)
