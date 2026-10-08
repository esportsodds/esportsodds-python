"""The change feed, the bulk export and a match's revision history.

Hand-written fixtures only: placeholder team names and all-zero UUIDs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from esportsodds import InvalidResponseError

from .conftest import error, make_rig, page

UTC = timezone.utc
MATCH_ID = "00000000-0000-7000-8000-000000000001"


def match(mid: str, changed_at: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": mid,
        "changed_at": changed_at,
        "team_a_name": "Team A",
        "team_b_name": "Team B",
        **extra,
    }


# ---------------------------------------------------------------------------------------------
# iter_changes
# ---------------------------------------------------------------------------------------------


def test_the_window_is_widened_by_ten_minutes() -> None:
    # Ingestion commits in batches, so a row can become visible minutes after the `changed_at` it
    # carries. Resuming from the exact last value skips such a row for good.
    rig = make_rig(page([], None))
    list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z"))
    assert rig.transport.query(0)["changed_since"] == "2026-09-18T11:50:00Z"


def test_the_feed_is_walked_in_change_order_on_the_list_route() -> None:
    rig = make_rig(page([], None))
    list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z"))
    assert rig.transport.path(0) == "/v1/cs2/matches"
    q = rig.transport.query(0)
    assert q["sort"] == "changed_at"
    assert q["limit"] == "500"
    assert "include" not in q


def test_asking_for_sub_resources_switches_to_the_bulk_route() -> None:
    rig = make_rig(page([], None))
    list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z", include=["maps", "stats"]))
    assert rig.transport.path(0) == "/v1/cs2/matches/bulk"
    q = rig.transport.query(0)
    assert q["include"] == "maps,stats"
    assert q["limit"] == "25"
    assert q["sort"] == "changed_at"
    assert q["changed_since"] == "2026-09-18T11:50:00Z"


@pytest.mark.parametrize(
    ("since", "want"),
    [
        ("2026-09-18T12:00:00Z", "2026-09-18T11:50:00Z"),
        ("2026-09-18T12:00:00.5Z", "2026-09-18T11:50:00.500000Z"),
        ("2026-09-18T12:00:00.123456789Z", "2026-09-18T11:50:00.123456Z"),
        ("2026-09-18T14:00:00+02:00", "2026-09-18T11:50:00Z"),
        (datetime(2026, 9, 18, 12, 0, tzinfo=UTC), "2026-09-18T11:50:00Z"),
        (datetime(2026, 9, 18, 12, 0), "2026-09-18T11:50:00Z"),  # naive means UTC
        (datetime(2026, 9, 18, 14, 0, tzinfo=timezone(timedelta(hours=2))), "2026-09-18T11:50:00Z"),
        ("2026-09-18T00:05:00Z", "2026-09-17T23:55:00Z"),  # across midnight
    ],
)
def test_since_may_be_a_string_or_a_datetime(since: Any, want: str) -> None:
    rig = make_rig(page([], None))
    list(rig.client.matches.iter_changes(since))
    assert rig.transport.query(0)["changed_since"] == want


def test_a_different_overlap_can_be_asked_for() -> None:
    rig = make_rig(page([], None), page([], None))
    list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z", overlap=timedelta(minutes=30)))
    list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z", overlap=timedelta(0)))
    assert rig.transport.query(0)["changed_since"] == "2026-09-18T11:30:00Z"
    assert rig.transport.query(1)["changed_since"] == "2026-09-18T12:00:00Z"


def test_a_bad_since_or_overlap_fails_at_the_call_not_at_the_first_row() -> None:
    rig = make_rig()
    with pytest.raises(ValueError, match="RFC 3339"):
        rig.client.matches.iter_changes("yesterday")
    with pytest.raises(ValueError, match="overlap"):
        rig.client.matches.iter_changes("2026-09-18T12:00:00Z", overlap=timedelta(minutes=-1))
    assert rig.transport.requests == []


def test_the_walk_keeps_its_window_and_order_on_every_page() -> None:
    rig = make_rig(
        page([match("A", "2026-09-18T12:01:00Z")], "c1"),
        page([match("B", "2026-09-18T12:02:00Z")], None),
    )
    got = list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z"))
    assert [m["id"] for m in got] == ["A", "B"]
    first, second = rig.transport.query(0), rig.transport.query(1)
    assert second["cursor"] == "c1"
    for key in ("changed_since", "sort", "limit"):
        assert second[key] == first[key]  # a cursor is bound to the sort it was minted under


def test_an_identical_row_seen_twice_is_yielded_once() -> None:
    rig = make_rig(
        page([match("A", "t1"), match("B", "t2")], "c1"),
        page([match("B", "t2"), match("C", "t3")], None),
    )
    got = list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z"))
    assert [(m["id"], m["changed_at"]) for m in got] == [("A", "t1"), ("B", "t2"), ("C", "t3")]


def test_a_match_that_changes_again_mid_walk_is_yielded_again_with_its_newer_state() -> None:
    # De-duplicating on `id` alone would swallow this second version, and a consumer whose
    # checkpoint then moved past it would never see the update.
    rig = make_rig(
        page([match("A", "t1", status="live")], "c1"),
        page([match("A", "t5", status="completed")], None),
    )
    got = list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z"))
    assert [(m["id"], m["status"]) for m in got] == [("A", "live"), ("A", "completed")]


def test_rows_without_an_id_or_stamp_are_passed_through() -> None:
    rig = make_rig(page([{"x": 1}, {"x": 1}, {"id": "A"}, {"id": "A"}], None))
    assert len(list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z"))) == 4


def test_the_feed_can_be_capped() -> None:
    rig = make_rig(page([match("A", "t1")], "c1"), page([match("B", "t2")], "c2"))
    got = list(rig.client.matches.iter_changes("2026-09-18T12:00:00Z", max_pages=1))
    assert [m["id"] for m in got] == ["A"]
    assert len(rig.transport.requests) == 1


# ---------------------------------------------------------------------------------------------
# The bulk export
# ---------------------------------------------------------------------------------------------


def test_bulk_takes_includes_as_a_list() -> None:
    rig = make_rig(page([], None))
    rig.client.matches.bulk(include=["maps", "stats", "rounds"], status="completed", limit=10)
    assert rig.transport.path() == "/v1/cs2/matches/bulk"
    assert rig.transport.query() == {
        "include": "maps,stats,rounds",
        "status": "completed",
        "limit": "10",
    }


def test_iter_bulk_pages_at_the_servers_ceiling_and_follows_the_cursor() -> None:
    rig = make_rig(page([match("A", "t1", maps=[])], "c1"), page([match("B", "t2", maps=[])], None))
    got = list(rig.client.matches.iter_bulk(include=["maps"], status="completed"))
    assert [m["id"] for m in got] == ["A", "B"]
    q0, q1 = rig.transport.query(0), rig.transport.query(1)
    assert q0["limit"] == "25"
    assert q0["include"] == "maps"
    assert q0["status"] == "completed"
    assert q1["cursor"] == "c1"
    assert all(r.timeout >= 120 for r in rig.transport.requests)


def test_pages_bulk_exposes_the_cursor_for_a_resumable_backfill() -> None:
    rig = make_rig(page([match("A", "t1")], "resume-1"), page([match("B", "t2")], "resume-2"))
    cursors = [p.next_cursor for p in rig.client.matches.pages_bulk(include=["maps"], max_pages=2)]
    assert cursors == ["resume-1", "resume-2"]


def test_pages_bulk_can_resume() -> None:
    rig = make_rig(page([], None))
    list(rig.client.matches.pages_bulk(cursor="saved", sort="scheduled_at"))
    q = rig.transport.query(0)
    assert q["cursor"] == "saved"
    assert q["sort"] == "scheduled_at"


def test_a_busy_bulk_route_is_waited_out_and_the_page_is_not_lost() -> None:
    rig = make_rig(error(503, "unavailable", retry_after="2"), page([match("A", "t1")], None))
    assert [m["id"] for m in rig.client.matches.iter_bulk()] == ["A"]
    assert rig.sleeps == [2.0]


# ---------------------------------------------------------------------------------------------
# iter_history
# ---------------------------------------------------------------------------------------------


def revisions(*stamps: str) -> list[dict[str, Any]]:
    return [
        {"changed_at": s, "changed": ["status"], "changes": {}, "was_completed": False}
        for s in stamps
    ]


NEWEST = ["2026-09-21T19:42:07.318402Z", "2026-09-21T19:00:00Z", "2026-09-21T18:30:00.5Z"]
OLDER = ["2026-09-21T18:00:00Z", "2026-09-21T17:30:00Z", "2026-09-21T17:00:00Z"]


def test_history_walks_back_with_before_set_to_the_last_stamp_verbatim() -> None:
    rig = make_rig(
        page(revisions(*NEWEST)),
        page(revisions(*OLDER)),
        page(revisions("2026-09-21T16:00:00Z")),
    )
    got = list(rig.client.matches.iter_history(MATCH_ID, limit=3))
    assert len(got) == 7
    assert [r.get("before") for r in (rig.transport.query(i) for i in range(3))] == [
        None,
        "2026-09-21T18:30:00.5Z",  # byte for byte what the API sent: no parse and reformat
        "2026-09-21T17:00:00Z",
    ]
    assert all(rig.transport.query(i)["limit"] == "3" for i in range(3))
    assert rig.transport.path(0) == f"/v1/cs2/matches/{MATCH_ID}/history"


def test_history_stops_on_a_short_page() -> None:
    rig = make_rig(page(revisions(*NEWEST)), page(revisions(OLDER[0])), page(revisions("never")))
    assert len(list(rig.client.matches.iter_history(MATCH_ID, limit=3))) == 4
    assert len(rig.transport.requests) == 2


def test_history_stops_on_an_empty_page_after_a_full_one() -> None:
    rig = make_rig(page(revisions(*NEWEST)), page([]))
    assert len(list(rig.client.matches.iter_history(MATCH_ID, limit=3))) == 3
    assert len(rig.transport.requests) == 2


def test_history_asks_for_the_maximum_page_by_default_and_never_more() -> None:
    rig = make_rig(page([]), page([]), page([]))
    list(rig.client.matches.iter_history(MATCH_ID))
    list(rig.client.matches.iter_history(MATCH_ID, limit=500))
    assert rig.transport.query(0)["limit"] == "200"
    assert (
        rig.transport.query(1)["limit"] == "200"
    )  # the server clamps to 200; "short page" must too


def test_history_with_a_full_oversized_request_still_pages_correctly() -> None:
    # limit=500 is clamped to 200 by the client, so a 200-row page is "full" and the walk goes on.
    full = revisions(*[f"2026-09-21T10:{59 - i // 60:02d}:{59 - i % 60:02d}Z" for i in range(200)])
    rig = make_rig(page(full), page(revisions("2026-09-21T05:00:00Z")))
    assert len(list(rig.client.matches.iter_history(MATCH_ID, limit=500))) == 201


def test_a_history_that_does_not_move_back_stops_the_walk_loudly() -> None:
    rig = make_rig(page(revisions(*NEWEST)), page(revisions(*NEWEST)), page(revisions(*NEWEST)))
    with pytest.raises(InvalidResponseError, match="history"):
        list(rig.client.matches.iter_history(MATCH_ID, limit=3))
    assert len(rig.transport.requests) == 2


def test_a_history_row_without_a_stamp_is_an_invalid_response() -> None:
    rig = make_rig(page([{"changed": []}, {"changed": []}, {"changed": []}]))
    with pytest.raises(InvalidResponseError, match="changed_at"):
        list(rig.client.matches.iter_history(MATCH_ID, limit=3))


def test_history_can_be_capped() -> None:
    rig = make_rig(page(revisions(*NEWEST)), page(revisions(*OLDER)))
    assert len(list(rig.client.matches.iter_history(MATCH_ID, limit=3, max_pages=1))) == 3
    assert len(rig.transport.requests) == 1


@pytest.mark.parametrize("bad", [0, -5])
def test_history_limit_must_be_positive(bad: int) -> None:
    rig = make_rig()
    with pytest.raises(ValueError, match="limit"):
        rig.client.matches.iter_history(MATCH_ID, limit=bad)


def test_one_page_of_history_is_available_directly() -> None:
    rig = make_rig(page(revisions(*NEWEST)))
    got = rig.client.matches.history(MATCH_ID, limit=3, before="2026-09-22T00:00:00Z")
    assert len(got) == 3
    assert rig.transport.query() == {"limit": "3", "before": "2026-09-22T00:00:00Z"}


@pytest.mark.usefixtures("tokyo_time")
@pytest.mark.parametrize(
    ("since", "want"),
    [
        (datetime(2026, 9, 18, 12, 0), "2026-09-18T11:50:00Z"),  # naive means UTC
        (date(2026, 9, 18), "2026-09-17T23:50:00Z"),  # a date is midnight UTC, not local midnight
        ("2026-09-18T12:00:00Z", "2026-09-18T11:50:00Z"),
    ],
)
def test_the_window_does_not_depend_on_the_machines_timezone(since: Any, want: str) -> None:
    rig = make_rig(page([], None))
    list(rig.client.matches.iter_changes(since))
    assert rig.transport.query(0)["changed_since"] == want
