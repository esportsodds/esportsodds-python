"""The whole stack on a real loopback socket: Client, UrllibTransport and a tiny HTTP server.

Everything else in this suite uses a scripted fake; these tests are the proof that the real
transport and the client agree about headers, compression, redirects and retries. The server only
ever listens on 127.0.0.1, and nothing here touches the network beyond it.
"""

from __future__ import annotations

import gzip
import json
import threading
from typing import Any

import pytest

from esportsodds import (
    ApiError,
    Client,
    QuotaExceededError,
    TransportError,
    UrllibTransport,
    __version__,
)

from .conftest import FAKE_KEY
from .localserver import LocalServer, Reply, Seen, free_port

JSON = {"Content-Type": "application/json"}


def body(obj: object) -> bytes:
    return json.dumps(obj).encode()


def make_client(url: str, **kwargs: Any) -> tuple[Client, list[float]]:
    client = Client(api_key=FAKE_KEY, base_url=url, transport=UrllibTransport(proxies={}), **kwargs)
    sleeps: list[float] = []

    def record(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 25:
            # No setting allows this many retries. A real socket never runs out of replies the way a
            # scripted fake does, so an unbounded loop would spin here until the per-test deadline.
            raise AssertionError("still retrying after 25 waits: the retry loop has no bound")

    client._sleep = record
    return client, sleeps


def page_reply(rows: list[dict[str, Any]], cursor: str | None = None) -> Reply:
    return 200, JSON, body({"data": rows, "meta": {"count": len(rows), "next_cursor": cursor}})


def test_headers_and_compression_end_to_end() -> None:
    rows = [{"id": f"row-{i}", "team_a_name": "Team A"} for i in range(200)]
    payload = gzip.compress(body({"data": rows, "meta": {"count": 200, "next_cursor": None}}))

    def handle(_: Seen) -> Reply:
        return 200, {**JSON, "Content-Encoding": "gzip", "X-Request-Id": "req-1"}, payload

    with LocalServer(handle) as srv:
        client, _ = make_client(srv.url)
        got = client.matches.list(status="completed", limit=200)

    assert got.data == rows
    seen = srv.seen[0]
    assert seen.method == "GET"
    assert seen.path == "/v1/cs2/matches?status=completed&limit=200"
    assert seen.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert seen.headers["user-agent"] == f"esportsodds-python/{__version__}"
    assert seen.headers["accept"] == "application/json"
    assert seen.headers["accept-encoding"] == "gzip"
    assert FAKE_KEY not in seen.path
    assert client.last_response is not None
    assert client.last_response.request_id == "req-1"


def test_health_sends_no_credentials() -> None:
    with LocalServer(lambda _: (200, JSON, body({"status": "ok"}))) as srv:
        client, _ = make_client(srv.url)
        assert client.health() == {"status": "ok"}
    assert srv.seen[0].path == "/health"
    assert "authorization" not in srv.seen[0].headers


def test_a_walk_over_the_wire_follows_cursors() -> None:
    pages = {
        None: page_reply([{"id": "a"}], "c1"),
        "c1": page_reply([{"id": "b"}], "c2"),
        "c2": page_reply([{"id": "c"}], None),
    }

    def handle(seen: Seen) -> Reply:
        cursor = None
        if "cursor=" in seen.path:
            cursor = seen.path.split("cursor=")[1].split("&")[0]
        return pages[cursor]

    with LocalServer(handle) as srv:
        client, _ = make_client(srv.url)
        got = [row["id"] for row in client.matches.iter(limit=1)]
    assert got == ["a", "b", "c"]
    assert len(srv.seen) == 3


def test_a_rate_limit_is_retried_over_the_wire() -> None:
    calls = {"n": 0}

    def handle(_: Seen) -> Reply:
        calls["n"] += 1
        if calls["n"] == 1:
            err = {"error": {"code": "rate_limited", "message": "slow down", "request_id": "r"}}
            return 429, {**JSON, "Retry-After": "1"}, body(err)
        return page_reply([{"id": "ok"}])

    with LocalServer(handle) as srv:
        client, sleeps = make_client(srv.url)
        got = client.matches.list()
    assert got.data == [{"id": "ok"}]
    assert len(srv.seen) == 2
    assert len(sleeps) == 1
    assert 1.0 <= sleeps[0] <= 1.5


def test_a_quota_error_is_raised_once_over_the_wire() -> None:
    err = {"error": {"code": "quota_exceeded", "message": "out", "request_id": "rid-q"}}
    headers = {**JSON, "Retry-After": "1209600", "X-Quota-Reset": "2026-11-01T00:00:00Z"}

    with LocalServer(lambda _: (429, headers, body(err))) as srv:
        client, sleeps = make_client(srv.url)
        with pytest.raises(QuotaExceededError) as info:
            client.matches.list()
    assert len(srv.seen) == 1
    assert sleeps == []
    assert info.value.request_id == "rid-q"
    assert info.value.reset_at is not None
    assert info.value.reset_at.year == 2026


def test_an_error_page_from_a_proxy_does_not_crash_the_client() -> None:
    html = b"<html><body><h1>502 Bad Gateway</h1></body></html>"

    with LocalServer(lambda _: (502, {"Content-Type": "text/html"}, html)) as srv:
        client, sleeps = make_client(srv.url, max_retries=1)
        with pytest.raises(ApiError) as info:
            client.matches.list()
    assert info.value.status == 502
    assert info.value.code is None
    assert len(srv.seen) == 2  # one retry, as for any gateway error
    assert len(sleeps) == 1


def test_a_redirect_is_never_followed_so_the_key_cannot_leave() -> None:
    with LocalServer(lambda _: (200, JSON, body({"data": []}))) as elsewhere:
        target = elsewhere.url + "/v1/cs2/matches"
        with LocalServer(lambda _: (302, {"Location": target}, b"")) as origin:
            client, sleeps = make_client(origin.url)
            with pytest.raises(ApiError) as info:
                client.matches.list()
        assert info.value.status == 302
        assert elsewhere.seen == []
    assert sleeps == []


def test_connection_refused_is_retried_then_raised() -> None:
    client, sleeps = make_client(f"http://127.0.0.1:{free_port()}", max_retries=2)
    with pytest.raises(TransportError):
        client.matches.list()
    assert len(sleeps) == 2


def test_the_default_transport_is_the_urllib_one() -> None:
    assert isinstance(Client(api_key=FAKE_KEY)._transport, UrllibTransport)


def test_concurrent_threads_each_see_their_own_last_response() -> None:
    counter = {"n": 0}
    lock = threading.Lock()

    def handle(_: Seen) -> Reply:
        with lock:
            counter["n"] += 1
            request_id = f"req-{counter['n']}"
        return 200, {**JSON, "X-Request-Id": request_id}, body({"data": [], "meta": {}})

    observed: dict[int, list[str]] = {}

    def worker(index: int, client: Client) -> None:
        mine = []
        for _ in range(5):
            client.coverage.list()
            info = client.last_response
            assert info is not None
            assert info.request_id is not None
            mine.append(info.request_id)
        observed[index] = mine

    with LocalServer(handle) as srv:
        client, _ = make_client(srv.url)
        threads = [threading.Thread(target=worker, args=(i, client)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert len(observed) == 8
    everything = [rid for ids in observed.values() for rid in ids]
    assert len(everything) == 40
    assert len(set(everything)) == 40  # no id was seen by two threads, none was lost
    assert len(srv.seen) == 40
