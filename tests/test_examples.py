"""Every script in examples/ runs, against a scripted transport.

The examples are what people copy, so a renamed argument or a changed return shape must break a
test here instead of a stranger's afternoon. Each script is executed for real (`runpy`, as
`__main__`), with its HTTP stack swapped for a fake and its sleeps removed; what it prints, writes
and asks the API for is checked.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import random
import re
import runpy
import sys
import time
import types
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from esportsodds import _client as client_module
from esportsodds._routes import ROUTES
from esportsodds._transport import TransportResponse

from .conftest import FAKE_KEY, FakeTransport, error, item, page
from .readme_blocks import ROOT

EXAMPLES = ROOT / "examples"
ID_1 = "00000000-0000-7000-8000-000000000001"
ID_2 = "00000000-0000-7000-8000-000000000002"


def match(n: int, **extra: Any) -> dict[str, Any]:
    base = {
        "id": f"00000000-0000-7000-8000-00000000000{n}",
        "scheduled_at": f"2026-10-0{n}T14:00:00Z",
        "changed_at": f"2026-10-0{n}T15:00:00.000000Z",
        "status": "scheduled",
        "team_a_name": f"Team {chr(64 + 2 * n - 1)}",
        "team_b_name": f"Team {chr(64 + 2 * n)}",
    }
    return {**base, **extra}


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeTransport]:
    """`Client()` talks to a scripted transport and never sleeps; the key is a fake one."""

    def install(*script: Any) -> FakeTransport:
        transport = FakeTransport(*script)
        monkeypatch.setattr(client_module, "UrllibTransport", lambda *_a, **_k: transport)
        monkeypatch.setenv("ESPORTSODDS_API_KEY", FAKE_KEY)
        monkeypatch.delenv("ESPORTSODDS_BASE_URL", raising=False)
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)
        return transport

    return install


def run_script(
    name: str, *argv: str, capsys: pytest.CaptureFixture[str]
) -> tuple[str, int | str | None]:
    """Run examples/<name> as __main__; return (stdout, the SystemExit code or None)."""
    code: int | str | None = None
    old = sys.argv
    sys.argv = [name, *argv]
    try:
        runpy.run_path(str(EXAMPLES / name), run_name="__main__")
    except SystemExit as exit_:
        code = exit_.code
    finally:
        sys.argv = old
    return capsys.readouterr().out, code


# --- every script ---------------------------------------------------------------------------------

SCRIPTS = sorted(p.name for p in EXAMPLES.glob("*.py"))


def test_the_expected_scripts_are_there() -> None:
    assert SCRIPTS == [
        "backfill_matches.py",
        "follow_changes.py",
        "handle_errors.py",
        "quickstart.py",
        "team_report.py",
        "upcoming_with_odds.py",
        "watch_matches.py",
    ]


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_script_has_a_docstring_that_says_how_to_run_it(name: str) -> None:
    tree = ast.parse((EXAMPLES / name).read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree)
    assert doc, name
    assert f"python examples/{name}" in doc, name


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_script_takes_its_key_from_the_environment_never_from_the_source(name: str) -> None:
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    assert "api_key=" not in text
    assert "eo_live_" not in text.replace("ESPORTSODDS_API_KEY=...", "")


# The quickstart is a flat script by design (it is the README's code); every other script is a
# module whose work sits behind `main()`, so importing it does nothing.
IMPORTABLE = [name for name in SCRIPTS if name != "quickstart.py"]


@pytest.mark.parametrize("name", IMPORTABLE)
def test_each_script_can_be_imported_without_side_effects(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    namespace = runpy.run_path(str(EXAMPLES / name), run_name="not_main")
    assert callable(namespace.get("main")), name
    assert capsys.readouterr().out == ""  # and nothing was printed, or the network touched


# --- the scripts, one by one ----------------------------------------------------------------------


def test_quickstart(wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]) -> None:
    transport = wire(page([match(1), match(2)]))
    out, code = run_script("quickstart.py", capsys=capsys)
    assert code is None
    assert out.splitlines() == [
        "2026-10-01T14:00:00Z Team A vs Team B",
        "2026-10-02T14:00:00Z Team C vs Team D",
    ]
    assert transport.query(0) == {"status": "scheduled", "limit": "5"}


def test_upcoming_with_odds(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    board = page([{"label": "Team A", "price": 1.85}, {"label": "Team B", "price": 2.05}])
    transport = wire(
        page([match(1), match(2)]),
        board,
        page([], x_quota_remaining="49990"),
    )
    out, _ = run_script("upcoming_with_odds.py", capsys=capsys)
    assert out.splitlines() == [
        "2026-10-01T14:00:00Z Team A vs Team B",
        "    Team A: 1.85",
        "    Team B: 2.05",
        "2026-10-02T14:00:00Z Team C vs Team D",
        "    no market line yet",
        "49990 requests left this month",
    ]
    assert transport.query(1) == {"match": ID_1, "latest": "1", "market": "match_winner"}
    assert transport.query(2)["match"] == match(2)["id"]
    assert len(transport.requests) == 3


WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def requests_the_docstring_promises(name: str, pattern: str) -> int:
    """The number a script's docstring gives for its requests ("Five requests in all.")."""
    doc = ast.get_docstring(ast.parse((EXAMPLES / name).read_text(encoding="utf-8"))) or ""
    found = re.search(pattern, doc, re.I)
    assert found, (name, pattern, doc)
    return WORDS[found.group(1).lower()]


def test_the_requests_upcoming_with_odds_says_it_makes_are_the_ones_it_makes(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    promised = requests_the_docstring_promises("upcoming_with_odds.py", r"(\w+) requests for")
    matches = [match(n) for n in range(1, 6)]
    transport = wire(page(matches), *[page([]) for _ in matches])
    run_script("upcoming_with_odds.py", capsys=capsys)
    assert len(transport.requests) == promised == 6


def test_the_requests_team_report_says_it_makes_are_the_ones_it_makes(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    promised = requests_the_docstring_promises("team_report.py", r"(\w+) requests in all")
    transport = wire(
        item({"name": "Team A"}),
        item({"name": "Team B"}),
        page([{"won": True}]),
        item({"team_wins": 1, "opponent_wins": 0}),
        page([{"rating": 1700.0, "as_of": "2026-10-01T00:00:00Z"}]),
    )
    run_script("team_report.py", "a", "--opponent", "b", capsys=capsys)
    assert len(transport.requests) == promised == 5


def test_team_report(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    transport = wire(
        item({"name": "Team A"}),
        item({"name": "Team B"}),
        page([{"won": True}, {"won": False}, {"won": True}]),
        item({"team_wins": 3, "opponent_wins": 1}),
        page([{"rating": 1712.4, "as_of": "2026-10-01T00:00:00Z"}]),
    )
    out, code = run_script("team_report.py", "team-a", "--opponent", "team-b", capsys=capsys)
    assert code == 0
    assert out.splitlines() == [
        "Team A",
        "  form, newest first: W L W",
        "  against Team B: 3-1",
        "  latest rating: 1712 (as of 2026-10-01T00:00:00Z)",
    ]
    assert [transport.path(i) for i in range(5)] == [
        "/v1/cs2/teams/team-a",
        "/v1/cs2/teams/team-b",
        "/v1/cs2/teams/team-a/form",
        "/v1/cs2/teams/team-a/h2h",
        "/v1/cs2/teams/team-a/ratings",
    ]
    assert transport.query(2) == {"limit": "5"}
    assert transport.query(3) == {"opponent": "team-b"}
    assert transport.query(4) == {"limit": "1"}


def test_team_report_for_a_team_that_does_not_exist(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    wire(error(404, "not_found"))
    out, code = run_script("team_report.py", "nope", "--opponent", "team-b", capsys=capsys)
    assert code == 1
    assert out == "no such team; check the slug\n"


def test_backfill_writes_json_lines_and_removes_its_cursor_when_done(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    transport = wire(
        page([match(1, maps=[{"n": 1}])], "c1"),
        page([match(2, maps=[], stats=[])], None, x_quota_remaining="49998"),
    )
    target = tmp_path / "matches.jsonl"
    out, code = run_script(
        "backfill_matches.py", str(target), "--since", "2026-02-01", capsys=capsys
    )
    assert code == 0
    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == [match(1)["id"], match(2)["id"]]
    assert not (tmp_path / "matches.jsonl.cursor").exists()
    assert f"wrote 2 matches to {target}" in out
    assert "49998 requests left this month" in out
    assert transport.path(0) == "/v1/cs2/matches/bulk"
    assert transport.query(0) == {
        "include": "maps,stats",
        "status": "completed",
        "date_from": "2026-02-01",
        "limit": "25",
    }
    assert transport.query(1)["cursor"] == "c1"


def test_backfill_resumes_from_the_cursor_it_saved(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "matches.jsonl"
    wire(page([match(1)], "c1"))
    out, _ = run_script("backfill_matches.py", str(target), "--max-pages", "1", capsys=capsys)
    assert "resuming" not in out
    assert (tmp_path / "matches.jsonl.cursor").read_text(encoding="utf-8") == "c1"
    assert len(target.read_text(encoding="utf-8").splitlines()) == 1

    transport = wire(page([match(2)], None))
    out, _ = run_script("backfill_matches.py", str(target), capsys=capsys)
    assert "resuming from the saved cursor" in out
    assert transport.query(0)["cursor"] == "c1"
    assert len(target.read_text(encoding="utf-8").splitlines()) == 2
    assert not (tmp_path / "matches.jsonl.cursor").exists()


def test_backfill_flushes_each_page_before_it_saves_the_cursor_that_points_past_it(
    wire: Callable[..., FakeTransport],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The docstring promises a run that crashes carries on from the saved cursor. That holds only
    # if the lines the cursor skips past are already on disk when the cursor is written.
    target = tmp_path / "matches.jsonl"
    wire(page([match(1), match(2)], "c1"), page([match(3)], "c2"), page([match(4)], None))
    seen: list[tuple[str, int]] = []
    real_write_text = Path.write_text

    def spy(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        if self.name.endswith(".cursor"):
            seen.append((data, len(target.read_text(encoding="utf-8").splitlines())))
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    run_script("backfill_matches.py", str(target), capsys=capsys)
    assert seen == [("c1", 2), ("c2", 3)]


def test_the_backfill_docstring_describes_what_the_script_does() -> None:
    source = (EXAMPLES / "backfill_matches.py").read_text(encoding="utf-8")
    doc = " ".join((ast.get_docstring(ast.parse(source)) or "").split())
    usage = re.search(r"backfill_matches\.py (\S+\.jsonl)", doc)
    assert usage, doc
    assert f"saved beside it ({usage.group(1)}.cursor)" in doc  # <output>.cursor, as written
    assert f"Each request is one bulk page: {ROUTES['matches.bulk'].page_max} matches" in doc
    assert "however much a page carries" in doc  # one request per page, whatever it holds


def test_follow_changes_holds_the_newest_copy_and_resumes_from_the_watermark(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    state = tmp_path / "state.json"
    transport = wire(page([match(1), match(2)]))
    out, code = run_script("follow_changes.py", str(state), capsys=capsys)
    assert code == 0
    assert out == f"2 changed; 2 matches held; since {match(2)['changed_at']}\n"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["since"] == match(2)["changed_at"]
    assert set(saved["matches"]) == {match(1)["id"], match(2)["id"]}
    assert transport.query(0)["sort"] == "changed_at"
    assert not (tmp_path / "state.json.tmp").exists()

    newer = match(2, status="completed", changed_at="2026-10-03T09:00:00.000000Z")
    transport = wire(page([newer]))
    out, _ = run_script("follow_changes.py", str(state), capsys=capsys)
    assert out == "1 changed; 2 matches held; since 2026-10-03T09:00:00.000000Z\n"
    # The walk starts ten minutes before the watermark, as the README says (the client leaves the
    # fraction off a whole second).
    assert transport.query(0)["changed_since"] == "2026-10-02T14:50:00Z"
    assert (
        json.loads(state.read_text(encoding="utf-8"))["matches"][match(2)["id"]]["status"]
        == "completed"
    )


def test_follow_changes_starts_a_new_state_file_one_day_back(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # No state yet: the first pass looks back a day (the client adds its ten-minute overlap).
    transport = wire(page([]))
    before = datetime.now(timezone.utc)
    run_script("follow_changes.py", str(tmp_path / "new.json"), capsys=capsys)
    after = datetime.now(timezone.utc)
    asked = datetime.fromisoformat(transport.query(0)["changed_since"].replace("Z", "+00:00"))
    overlap = timedelta(minutes=10)
    assert before - timedelta(days=1) - overlap - timedelta(seconds=2) <= asked
    assert asked <= after - timedelta(days=1) - overlap + timedelta(seconds=2)


def test_follow_changes_replaces_the_state_file_atomically(
    wire: Callable[..., FakeTransport],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "A reader never sees half a file": the new copy is written beside it and renamed over it.
    state = tmp_path / "state.json"
    wire(page([match(1)]))
    renames: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(source: Any, target: Any) -> None:
        renames.append((Path(source).name, Path(target).name))
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", spy)
    run_script("follow_changes.py", str(state), capsys=capsys)
    assert renames == [("state.json.tmp", "state.json")]
    assert json.loads(state.read_text(encoding="utf-8"))["since"] == match(1)["changed_at"]


def test_follow_changes_runs_a_pass_every_n_seconds_until_stopped(
    wire: Callable[..., FakeTransport],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(page([match(1)]), page([]))
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        run_script("follow_changes.py", str(tmp_path / "s.json"), "--every", "300", capsys=capsys)
    assert waits == [300.0, 300.0]
    assert capsys.readouterr().out.splitlines() == [
        f"1 changed; 1 matches held; since {match(1)['changed_at']}",
        f"0 changed; 1 matches held; since {match(1)['changed_at']}",
    ]


def test_follow_changes_stops_cleanly_when_the_quota_is_spent(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    wire(error(429, "quota_exceeded", retry_after="86400", x_quota_reset="2026-11-01T00:00:00Z"))
    out, code = run_script("follow_changes.py", str(tmp_path / "s.json"), capsys=capsys)
    assert code == 1
    assert out.startswith("out of requests until 2026-11-01")
    assert not (tmp_path / "s.json").exists()  # nothing half-written


def run_errors(
    first: Any,
    capsys: pytest.CaptureFixture[str],
    wire: Callable[..., FakeTransport],
    repeat: int = 1,
) -> str:
    wire(*([first] * repeat))
    out, code = run_script("handle_errors.py", capsys=capsys)
    assert code is None
    return out


def test_handle_errors_prints_the_budget_after_a_good_call(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    ok = TransportResponse(
        200,
        {"X-Request-Id": "req-1", "X-Quota-Limit": "50000", "X-Quota-Remaining": "49990"},
        json.dumps({"data": {"status": "scheduled"}}).encode(),
    )
    out = run_errors(ok, capsys, wire)
    assert out.splitlines() == [
        "scheduled",
        "request id: req-1",
        "left this month: 49990 of 50000",
    ]


@pytest.mark.parametrize(
    ("reply", "repeat", "expected"),
    [
        (
            error(404, "not_found"),
            1,
            "no such match; the id may be wrong (an unknown id is a 404, never a 400)",
        ),
        (error(401, "invalid_api_key"), 1, "the key was refused: invalid_api_key"),
        (error(429, "quota_exceeded", retry_after="86400"), 1, "out of requests until"),
        (error(429, "rate_limited", retry_after="1"), 4, "still rate limited after retrying"),
        (
            error(500, "internal_error", request_id="req-9"),
            3,
            "quote this id if it persists: req-9",
        ),
        (error(503, "unavailable", retry_after="1"), 4, "the API is struggling"),
    ],
)
def test_handle_errors_names_each_kind_of_failure(
    wire: Callable[..., FakeTransport],
    capsys: pytest.CaptureFixture[str],
    reply: TransportResponse,
    repeat: int,
    expected: str,
) -> None:
    assert expected in run_errors(reply, capsys, wire, repeat)


def test_handle_errors_does_not_mistake_a_400_for_an_unknown_id(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    # What its own message says: a malformed request is a 400 and is not "no such match".
    from esportsodds import InvalidRequestError

    wire(error(400, "invalid_parameter", "bad"))
    with pytest.raises(InvalidRequestError):
        run_script("handle_errors.py", capsys=capsys)


def test_handle_errors_reports_a_network_failure(
    wire: Callable[..., FakeTransport], capsys: pytest.CaptureFixture[str]
) -> None:
    from esportsodds import TransportError

    out = run_errors(TransportError("connection refused"), capsys, wire, repeat=4)
    assert "could not reach the API: connection refused" in out


# --- the WebSocket example ------------------------------------------------------------------------


class Socket:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = [json.dumps(f) for f in frames]
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> Socket:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self) -> Any:
        return self._replay()

    async def _replay(self) -> Any:
        for frame in self.frames:
            yield frame


class ConnectionClosedError(Exception):
    """The stand-in package's own ConnectionClosed, which the example catches by name."""


def install_websockets(
    monkeypatch: pytest.MonkeyPatch, attempts: list[Socket | Exception], urls: list[str]
) -> None:
    """A stand-in `websockets`: each connect() gets the next scripted socket, or raises the next
    scripted exception (ConnectionClosed or OSError, both of which the example survives)."""
    module = types.ModuleType("websockets")
    queue = list(attempts)

    def connect(url: str) -> Socket:
        urls.append(url)
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    module.connect = connect  # type: ignore[attr-defined]
    module.ConnectionClosed = ConnectionClosedError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "websockets", module)


def load_watch() -> types.SimpleNamespace:
    return types.SimpleNamespace(**runpy.run_path(str(EXAMPLES / "watch_matches.py"), run_name="m"))


def test_a_skipped_seq_is_noticed_and_only_for_updates() -> None:
    watch = load_watch()
    seen: dict[str, int] = {}
    frames = [
        {"type": "snapshot", "match_id": ID_1, "seq": 0},
        {"type": "update", "match_id": ID_1, "seq": 1},
        {"type": "update", "match_id": ID_1, "seq": 3},  # 2 was missed
        {"type": "update", "match_id": ID_1, "seq": 4},
        {
            "type": "update",
            "match_id": ID_2,
            "seq": 1,
        },  # another match's first update: no gap known
        {"type": "snapshot", "match_id": ID_1, "seq": 0},  # a re-sync restarts the count
        {"type": "update", "match_id": ID_1, "seq": 1},
        {"type": "subscribed", "data": {"match_ids": [ID_1]}},  # no match_id, no seq
    ]
    assert [watch.missed_a_frame(f, seen) for f in frames] == [
        False, False, True, False, False, False, False, False,
    ]  # fmt: skip


def test_the_subscribe_frame_is_the_one_the_guide_documents() -> None:
    watch = load_watch()
    assert json.loads(watch.subscribe_frame([ID_1, ID_2])) == {
        "type": "subscribe",
        "data": {"match_ids": [ID_1, ID_2]},
    }


def test_watching_subscribes_prints_frames_and_resubscribes_after_a_gap(
    wire: Callable[..., FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = wire(item({"token": "TICKET", "expires_at": "2026-10-07T12:01:00Z"}))
    socket = Socket(
        [
            {"type": "snapshot", "match_id": ID_1, "seq": 0, "data": {}},
            {"type": "update", "match_id": ID_1, "seq": 1, "data": {}},
            {"type": "update", "match_id": ID_1, "seq": 3, "data": {}},
        ]
    )
    urls: list[str] = []
    install_websockets(monkeypatch, [socket], urls)
    watch = load_watch()
    client = client_module.Client()
    asyncio.run(watch.watch(client, [ID_1], connections=1))
    assert urls == ["wss://api.esportsodds.gg/v1/ws?token=TICKET"]
    assert transport.requests[0].method == "POST"
    assert socket.sent == [
        {"type": "subscribe", "data": {"match_ids": [ID_1]}},
        {"type": "subscribe", "data": {"match_ids": [ID_1]}},  # again, for the gap
    ]
    assert capsys.readouterr().out.splitlines() == [
        f"{ID_1} snapshot 0",
        f"{ID_1} update 1",
        f"{ID_1} update 3",
    ]


def tickets(*names: str) -> list[TransportResponse]:
    return [item({"token": name, "expires_at": "2026-10-07T12:01:00Z"}) for name in names]


def no_sleeping(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every asyncio.sleep() instead of waiting, and make the jitter zero."""
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(random, "uniform", lambda _low, _high: 0.0)
    return waits


def test_a_dropped_connection_is_retried_with_a_fresh_ticket(
    wire: Callable[..., FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = wire(*tickets("ONE", "TWO"))
    urls: list[str] = []
    second = Socket([{"type": "snapshot", "match_id": ID_1, "seq": 0, "data": {}}])
    install_websockets(monkeypatch, [ConnectionClosedError("dropped"), second], urls)
    no_sleeping(monkeypatch)
    watch = load_watch()
    asyncio.run(watch.watch(client_module.Client(), [ID_1], connections=2))
    assert [u.rsplit("=", 1)[1] for u in urls] == ["ONE", "TWO"]
    assert len(transport.requests) == 2
    out = capsys.readouterr().out
    assert "connection lost: dropped" in out
    assert f"{ID_1} snapshot 0" in out


def test_the_wait_between_connections_doubles_on_failure_and_resets_after_a_success(
    wire: Callable[..., FakeTransport], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire(*tickets("A", "B", "C", "D"))
    install_websockets(
        monkeypatch,
        [OSError("down"), OSError("down"), Socket([]), OSError("down")],
        [],
    )
    waits = no_sleeping(monkeypatch)
    asyncio.run(load_watch().watch(client_module.Client(), [ID_1], connections=4))
    # fail -> 1 s; fail -> 2 s; a good connection resets the wait, so after it -> 1 s again; and the
    # last attempt ends the run without waiting.
    assert waits == [1.0, 2.0, 1.0]


def test_the_wait_between_connections_stops_growing_at_a_minute(
    wire: Callable[..., FakeTransport], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 9
    wire(*tickets(*[f"T{n}" for n in range(attempts)]))
    install_websockets(monkeypatch, [OSError("down")] * attempts, [])
    waits = no_sleeping(monkeypatch)
    asyncio.run(load_watch().watch(client_module.Client(), [ID_1], connections=attempts))
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_an_error_frame_ends_the_connection_and_is_reported(
    wire: Callable[..., FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wire(*tickets("ONE"))
    frames: list[dict[str, Any]] = [
        {"type": "error", "data": {"code": "connection_limit", "message": "too many"}},
        {"type": "snapshot", "match_id": ID_1, "seq": 0, "data": {}},  # never read
    ]
    install_websockets(monkeypatch, [Socket(frames)], [])
    no_sleeping(monkeypatch)
    asyncio.run(load_watch().watch(client_module.Client(), [ID_1], connections=1))
    out = capsys.readouterr().out
    assert "server error:" in out
    assert "connection_limit" in out
    assert "snapshot" not in out


def test_watching_needs_a_match_id(capsys: pytest.CaptureFixture[str]) -> None:
    watch = load_watch()
    assert watch.main([]) == 2
    assert "usage: watch_matches.py MATCH_ID" in capsys.readouterr().out
