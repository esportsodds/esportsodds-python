"""Paging: follow `meta.next_cursor` until it is null, and never trust the size of a page.

The one stop signal the API guarantees is a null cursor. A page shorter than `limit` does not mean
the end (and a full one does not mean there is more), so stopping on a short page silently drops
rows. Each request also costs allowance, so the loops are lazy and cappable.
"""

from __future__ import annotations

from typing import Any

import pytest

from esportsodds import InvalidCursorError, InvalidResponseError, Page

from .conftest import error, make_rig, page


def rows(*ids: str) -> list[dict[str, Any]]:
    return [{"id": i} for i in ids]


def test_page_is_a_small_value_object() -> None:
    p = Page(data=rows("a", "b"), next_cursor="c", meta={"count": 2})
    assert list(p) == rows("a", "b")
    assert len(p) == 2
    assert p.has_more is True
    assert Page(data=[], next_cursor=None, meta={}).has_more is False


def test_pages_follows_the_cursor_until_it_is_null() -> None:
    rig = make_rig(
        page(rows("a", "b"), next_cursor="c1"),
        page(rows("c", "d"), next_cursor="c2"),
        page(rows("e"), next_cursor=None),
    )
    got = list(rig.client.matches.pages(limit=2))
    assert [[r["id"] for r in p.data] for p in got] == [["a", "b"], ["c", "d"], ["e"]]
    assert [q.get("cursor") for q in (rig.transport.query(i) for i in range(3))] == [
        None,
        "c1",
        "c2",
    ]
    assert all(rig.transport.query(i)["limit"] == "2" for i in range(3))


def test_a_short_page_with_a_cursor_keeps_paging() -> None:
    # The regression this guards: `if len(page) < limit: break`. The API can return a short page
    # that still has a cursor (rows filtered after the page was cut), and stopping drops the rest.
    rig = make_rig(
        page(rows("a"), next_cursor="c1"),  # limit is 100: this page is far short of it
        page(rows("b", "c"), next_cursor="c2"),
        page([], next_cursor="c3"),  # even an empty page can have a successor
        page(rows("d"), next_cursor=None),
    )
    assert [r["id"] for r in rig.client.matches.iter(limit=100)] == ["a", "b", "c", "d"]
    assert len(rig.transport.requests) == 4


def test_a_full_page_with_a_null_cursor_is_the_end() -> None:
    rig = make_rig(page(rows("a", "b", "c"), next_cursor=None), page(rows("never"), None))
    assert len(list(rig.client.matches.pages(limit=3))) == 1
    assert len(rig.transport.requests) == 1


def test_iter_yields_rows_across_pages() -> None:
    rig = make_rig(page(rows("a", "b"), "c1"), page(rows("c"), None))
    assert [r["id"] for r in rig.client.teams.iter()] == ["a", "b", "c"]


def test_iter_is_lazy_so_stopping_early_costs_nothing_more() -> None:
    rig = make_rig(page(rows("a", "b"), "c1"), page(rows("c", "d"), "c2"), page(rows("e"), None))
    it = rig.client.matches.iter()
    assert next(it)["id"] == "a"
    assert next(it)["id"] == "b"
    assert len(rig.transport.requests) == 1  # the second page is not fetched until it is needed
    it.close()
    assert len(rig.transport.requests) == 1


def test_max_pages_caps_the_requests() -> None:
    rig = make_rig(page(rows("a"), "c1"), page(rows("b"), "c2"), page(rows("c"), "c3"))
    got = list(rig.client.matches.pages(max_pages=2))
    assert len(got) == 2
    assert got[-1].next_cursor == "c2"  # the caller can resume from here
    assert len(rig.transport.requests) == 2


def test_max_pages_does_not_cut_a_shorter_walk() -> None:
    rig = make_rig(page(rows("a"), "c1"), page(rows("b"), None))
    assert len(list(rig.client.matches.pages(max_pages=10))) == 2


def test_iter_takes_max_pages_too() -> None:
    rig = make_rig(page(rows("a"), "c1"), page(rows("b"), "c2"), page(rows("c"), None))
    assert [r["id"] for r in rig.client.matches.iter(max_pages=2)] == ["a", "b"]


@pytest.mark.parametrize("bad", [0, -1])
def test_max_pages_must_be_positive(bad: int) -> None:
    rig = make_rig()
    with pytest.raises(ValueError, match="max_pages"):
        list(rig.client.matches.pages(max_pages=bad))


def test_pages_can_resume_from_a_saved_cursor() -> None:
    rig = make_rig(page(rows("x"), None))
    list(rig.client.matches.pages(cursor="saved-cursor"))
    assert rig.transport.query(0)["cursor"] == "saved-cursor"


def test_the_last_page_cursor_is_what_a_checkpoint_stores() -> None:
    rig = make_rig(page(rows("a"), "resume-here"), page(rows("b"), "resume-there"))
    seen = [p.next_cursor for p in rig.client.matches.pages(max_pages=2)]
    assert seen == ["resume-here", "resume-there"]


def test_a_cursor_that_does_not_advance_stops_the_walk_loudly() -> None:
    # A server bug that returns the same cursor forever would otherwise spend the whole allowance
    # on one page, one request at a time.
    rig = make_rig(page(rows("a"), "same"), page(rows("a"), "same"), page(rows("a"), "same"))
    with pytest.raises(InvalidResponseError, match="cursor"):
        list(rig.client.matches.pages())
    assert len(rig.transport.requests) == 2


def test_an_invalid_cursor_surfaces_as_its_own_exception() -> None:
    rig = make_rig(error(400, "invalid_cursor", "bad cursor"))
    with pytest.raises(InvalidCursorError):
        list(rig.client.matches.pages(cursor="stale"))


def test_iter_asks_for_the_largest_page_by_default() -> None:
    # Each request costs allowance, so the default for walking a list is the biggest page the
    # endpoint serves. The values are the documented maxima (the spec and the API's own limits).
    cases = [
        ("matches", 500),
        ("teams", 500),
        ("players", 500),
        ("tournaments", 500),
    ]
    for name, want in cases:
        rig = make_rig(page([], None))
        list(getattr(rig.client, name).iter())
        assert rig.transport.query(0)["limit"] == str(want), name
    rig = make_rig(page([], None))
    list(rig.client.odds.iter(match="00000000-0000-7000-8000-000000000001"))
    assert rig.transport.query(0)["limit"] == "1000"


def test_an_explicit_limit_wins() -> None:
    rig = make_rig(page([], None))
    list(rig.client.matches.iter(limit=7))
    assert rig.transport.query(0)["limit"] == "7"


def test_list_does_not_invent_a_limit() -> None:
    rig = make_rig(page([], None))
    rig.client.matches.list()
    assert "limit" not in rig.transport.query(0)


@pytest.mark.parametrize(
    ("namespace", "args"),
    [
        ("matches", {}),
        ("teams", {}),
        ("players", {}),
        ("tournaments", {}),
        ("odds", {"match": "00000000-0000-7000-8000-000000000001"}),
    ],
)
def test_every_paginated_namespace_walks_its_cursor(namespace: str, args: dict[str, Any]) -> None:
    rig = make_rig(page(rows("a"), "c1"), page(rows("b"), None))
    got = list(getattr(rig.client, namespace).iter(**args))
    assert [r["id"] for r in got] == ["a", "b"]
    assert rig.transport.query(1)["cursor"] == "c1"


def test_the_odds_snapshot_is_a_single_page() -> None:
    # Without `match` the snapshot is not paginated: next_cursor is always null.
    rig = make_rig(page(rows("a", "b"), None))
    assert [r["id"] for r in rig.client.odds.iter()] == ["a", "b"]
    assert len(rig.transport.requests) == 1


def test_filters_are_repeated_on_every_page() -> None:
    rig = make_rig(page(rows("a"), "c1"), page(rows("b"), None))
    list(rig.client.matches.pages(status="completed", sort="scheduled_at", has=["rounds"]))
    for i in (0, 1):
        q = rig.transport.query(i)
        assert q["status"] == "completed"
        assert q["sort"] == "scheduled_at"
        assert q["has"] == "rounds"
