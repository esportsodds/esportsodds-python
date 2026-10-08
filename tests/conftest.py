"""Shared test doubles: a scripted transport and a client wired to it.

No test here touches the network. `FakeTransport` plays back hand-written replies and records the
requests it was given, and the client's sleep, clock and jitter are replaced so a retry test runs in
microseconds and asserts the exact waits.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import FrameType
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from esportsodds import Client
from esportsodds._transport import TransportRequest, TransportResponse

# How long one test may run. A retry or paging loop with no bound (the very bug this suite exists to
# catch) would otherwise hang CI until its job timeout instead of failing one test in seconds. The
# slowest legitimate test takes well under a second. EO_TEST_DEADLINE overrides it (the deadline's
# own test sets it low).
DEADLINE_SECONDS = float(os.environ.get("EO_TEST_DEADLINE", "20"))

# Obviously fake: the prefix real keys carry, then zeros. Built by concatenation so no key-shaped
# literal sits in the source for a secret scanner to trip over.
FAKE_KEY = "eo_live_" + "0" * 48
BASE_URL = "https://api.test.invalid"

Scripted = TransportResponse | BaseException | Callable[[TransportRequest], TransportResponse]


class FakeTransport:
    """Plays back scripted replies in order and records every request."""

    def __init__(self, *script: Scripted) -> None:
        self.script: deque[Scripted] = deque(script)
        self.requests: list[TransportRequest] = []

    def send(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        item = self.script.popleft()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, TransportResponse):
            return item
        return item(request)

    # Conveniences for reading what was sent -------------------------------------------------

    @property
    def urls(self) -> list[str]:
        return [r.url for r in self.requests]

    def query(self, index: int = -1) -> dict[str, str]:
        return dict(parse_qsl(urlsplit(self.requests[index].url).query))

    def path(self, index: int = -1) -> str:
        return urlsplit(self.requests[index].url).path


def reply(body: object, status: int = 200, /, **headers: str) -> TransportResponse:
    """A JSON reply. Header names use `_` for `-`: `reply({}, retry_after="2")`."""
    wire = {name.replace("_", "-"): value for name, value in headers.items()}
    return TransportResponse(status, wire, json.dumps(body).encode())


def page(
    rows: list[dict[str, Any]], next_cursor: str | None = None, **headers: str
) -> TransportResponse:
    return reply(
        {"data": rows, "meta": {"count": len(rows), "next_cursor": next_cursor}}, **headers
    )


def item(data: dict[str, Any], **headers: str) -> TransportResponse:
    return reply({"data": data}, **headers)


def error(
    status: int,
    code: str,
    message: str = "boom",
    request_id: str = "00000000-0000-7000-8000-0000000000aa",
    **headers: str,
) -> TransportResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id}}
    return reply(body, status, **headers)


def html(status: int, text: str = "<html><h1>Bad Gateway</h1></html>") -> TransportResponse:
    return TransportResponse(status, {"Content-Type": "text/html"}, text.encode())


class StubRandom:
    """A random source with no randomness: `uniform(a, b)` returns a fixed fraction of the span."""

    def __init__(self, fraction: float = 0.0) -> None:
        self.fraction = fraction
        self.calls: list[tuple[float, float]] = []

    def uniform(self, a: float, b: float) -> float:
        self.calls.append((a, b))
        return a + (b - a) * self.fraction


@dataclass
class Rig:
    client: Client
    transport: FakeTransport
    sleeps: list[float] = field(default_factory=list)
    rng: StubRandom = field(default_factory=StubRandom)


def make_rig(*script: Scripted, **client_kwargs: Any) -> Rig:
    transport = FakeTransport(*script)
    client_kwargs.setdefault("api_key", FAKE_KEY)
    client_kwargs.setdefault("base_url", BASE_URL)
    client = Client(transport=transport, **client_kwargs)
    rig = Rig(client, transport)
    client._sleep = rig.sleeps.append
    client._clock = lambda: 0.0
    client._rng = rig.rng
    return rig


@pytest.fixture(autouse=True)
def _deadline() -> Iterator[None]:
    """Fail a test that outruns DEADLINE_SECONDS instead of letting it hang the run.

    Uses SIGALRM, so it needs no plugin (the tests must run with only pytest installed, for anyone
    running them from the sdist) and works on Linux and macOS. Where there is no SIGALRM (Windows)
    or the test is not on the main thread, there is simply no deadline.
    """
    alarm = getattr(signal, "SIGALRM", None)
    if alarm is None or threading.current_thread() is not threading.main_thread():
        yield
        return

    def on_alarm(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError(
            f"the test ran past its {DEADLINE_SECONDS:g}s deadline (an endless loop?)"
        )

    previous = signal.signal(alarm, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(alarm, previous)


@pytest.fixture
def tokyo_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run a test as if the machine were in Tokyo (UTC+9, no daylight saving).

    Almost every CI runner and server is in UTC, where reading a naive datetime as local time and
    reading it as UTC give the same answer, so a bug there is invisible. Under this fixture the two
    differ by nine hours. POSIX only (it needs `time.tzset`); skipped elsewhere.
    """
    tzset = getattr(time, "tzset", None)
    if tzset is None:
        pytest.skip("time.tzset is not available on this platform")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    tzset()
    assert datetime(2026, 1, 1).astimezone().utcoffset() == timedelta(hours=9)
    yield
    monkeypatch.undo()
    tzset()


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's real key or base URL must never leak into a test."""
    monkeypatch.delenv("ESPORTSODDS_API_KEY", raising=False)
    monkeypatch.delenv("ESPORTSODDS_BASE_URL", raising=False)


def headers_of(request: TransportRequest) -> Mapping[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}
