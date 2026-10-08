"""Pages, and the loop that follows `meta.next_cursor`.

One rule drives everything here: the only reliable end of a list is a null `meta.next_cursor`.
A page shorter than the page size is not the end (the API can cut a page and still have more), and
a full page is not proof of more. Stopping on a short page silently drops rows, so nothing in this
package does it (the one exception, a match's revision history, is not cursor-paginated and is
handled separately with its own safeguards).
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from typing import Any

from ._errors import InvalidResponseError

__all__ = ["Page", "iter_rows", "walk_pages"]


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a list endpoint.

    Attributes:
        data: The rows, as plain dicts exactly as the API sent them.
        next_cursor: Pass this as `cursor=` to fetch the next page; None means this is the last.
        meta: The reply's `meta` object (`count`, and for the bulk export `includes` and
            `max_limit`). Empty when the reply carried none.
    """

    data: list[dict[str, Any]]
    next_cursor: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @property
    def has_more(self) -> bool:
        """True when another page exists (``next_cursor`` is set)."""
        return self.next_cursor is not None


def walk_pages(
    fetch: Callable[[str | None], Page],
    *,
    cursor: str | None = None,
    max_pages: int | None = None,
) -> Generator[Page, None, None]:
    """Yield pages from `fetch(cursor)` until the API says there are no more.

    Args:
        fetch: Fetches one page given a cursor (None for the first page).
        cursor: Where to start; a `next_cursor` saved from an earlier walk resumes it.
        max_pages: Stop after this many requests. The last page yielded still carries its
            `next_cursor`, so a caller that hit the cap knows where to pick up.

    Raises:
        ValueError: `max_pages` is not a positive integer (raised when called, not at the first
            page).
        InvalidResponseError: The API returned a cursor it had already handed out, which would
            otherwise page forever, spending the allowance one request at a time.
    """
    if max_pages is not None and (
        isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1
    ):
        raise ValueError(f"max_pages must be a positive integer, got {max_pages!r}")
    return _walk(fetch, cursor, max_pages)


def _walk(
    fetch: Callable[[str | None], Page], cursor: str | None, max_pages: int | None
) -> Generator[Page, None, None]:
    seen: set[str] = set() if cursor is None else {cursor}
    fetched = 0
    while True:
        page = fetch(cursor)
        fetched += 1
        following = page.next_cursor
        if following is not None and following in seen:
            raise InvalidResponseError(
                "the API returned a cursor it had already returned; stopping so the walk does not "
                "repeat the same page forever"
            )
        yield page
        if following is None or (max_pages is not None and fetched >= max_pages):
            return
        seen.add(following)
        cursor = following


def iter_rows(pages: Generator[Page, None, None]) -> Generator[dict[str, Any], None, None]:
    """Yield every row of every page, fetching a page only when the previous one is used up."""
    try:
        for page in pages:
            yield from page.data
    finally:
        pages.close()
