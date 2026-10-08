"""The default transport, against a real socket on loopback."""

from __future__ import annotations

import gzip
import socket
import threading
import time
import tracemalloc

import pytest

from esportsodds import TransportError
from esportsodds._transport import TransportRequest, TransportResponse, UrllibTransport, _decode

from .localserver import LocalServer, Reply, Seen, free_port, sleepy


@pytest.fixture
def transport() -> UrllibTransport:
    # proxies={} so a developer's HTTP_PROXY / system proxy can never swallow a loopback request.
    return UrllibTransport(proxies={})


def get(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> TransportRequest:
    return TransportRequest("GET", url, headers or {}, timeout)


def ok(body: bytes = b"{}", **headers: str) -> Reply:
    return 200, {"Content-Type": "application/json", **headers}, body


def test_get_returns_status_headers_and_body(transport: UrllibTransport) -> None:
    with LocalServer(lambda _: ok(b'{"a": 1}', **{"X-Request-Id": "r-1"})) as srv:
        resp = transport.send(get(srv.url + "/v1/cs2/matches?limit=1", {"X-Test": "yes"}))
    assert resp.status == 200
    assert resp.body == b'{"a": 1}'
    assert resp.headers["x-request-id"] == "r-1"
    seen = srv.seen[0]
    assert seen.method == "GET"
    assert seen.path == "/v1/cs2/matches?limit=1"
    assert seen.headers["x-test"] == "yes"


def test_it_asks_for_gzip_unless_the_caller_overrides(transport: UrllibTransport) -> None:
    with LocalServer(lambda _: ok()) as srv:
        transport.send(get(srv.url))
        transport.send(get(srv.url, {"Accept-Encoding": "identity"}))
    assert srv.seen[0].headers["accept-encoding"] == "gzip"
    assert srv.seen[1].headers["accept-encoding"] == "identity"


def test_post_is_sent_as_post_with_an_empty_body(transport: UrllibTransport) -> None:
    with LocalServer(lambda _: ok(b'{"data": {}}')) as srv:
        resp = transport.send(TransportRequest("POST", srv.url + "/v1/cs2/ws-token", {}, 5.0))
    assert resp.status == 200
    assert srv.seen[0].method == "POST"
    assert srv.seen[0].body == b""


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 502, 503])
def test_an_error_status_is_a_response_not_an_exception(
    transport: UrllibTransport, status: int
) -> None:
    body = b'{"error": {"code": "x", "message": "y"}}'
    with LocalServer(lambda _: (status, {"Retry-After": "2"}, body)) as srv:
        resp = transport.send(get(srv.url))
    assert resp.status == status
    assert resp.body == body
    assert resp.headers["retry-after"] == "2"


@pytest.mark.parametrize("encoding", ["gzip", "GZIP", " gzip ", "x-gzip"])
def test_a_gzip_body_is_decoded(transport: UrllibTransport, encoding: str) -> None:
    payload = b'{"data": [' + b'{"k": "v"},' * 1000 + b'{"k": "v"}]}'
    with LocalServer(lambda _: ok(gzip.compress(payload), **{"Content-Encoding": encoding})) as srv:
        resp = transport.send(get(srv.url))
    assert resp.body == payload


def test_an_unknown_content_encoding_is_refused(transport: UrllibTransport) -> None:
    with (
        LocalServer(lambda _: ok(b"\x00\x01", **{"Content-Encoding": "br"})) as srv,
        pytest.raises(TransportError, match="Content-Encoding"),
    ):
        transport.send(get(srv.url))


def test_corrupt_gzip_is_a_transport_error(transport: UrllibTransport) -> None:
    with (
        LocalServer(lambda _: ok(b"this is not gzip", **{"Content-Encoding": "gzip"})) as srv,
        pytest.raises(TransportError, match="gzip"),
    ):
        transport.send(get(srv.url))


def test_truncated_gzip_is_a_transport_error(transport: UrllibTransport) -> None:
    cut = gzip.compress(b"x" * 10_000)[:-12]
    with (
        LocalServer(lambda _: ok(cut, **{"Content-Encoding": "gzip"})) as srv,
        pytest.raises(TransportError, match="gzip"),
    ):
        transport.send(get(srv.url))


def test_a_decompression_bomb_is_refused() -> None:
    small_limit = UrllibTransport(proxies={}, max_body_bytes=1_000_000)
    bomb = gzip.compress(b"\x00" * 20_000_000)  # about 20 KB on the wire, 20 MB once inflated
    assert len(bomb) < 100_000
    with (
        LocalServer(lambda _: ok(bomb, **{"Content-Encoding": "gzip"})) as srv,
        pytest.raises(TransportError, match="larger than"),
    ):
        small_limit.send(get(srv.url))


def test_inflating_a_bomb_never_holds_more_than_the_limit_in_memory() -> None:
    # The size check alone is not enough: if the inflater is allowed to run to completion first, a
    # few kilobytes of gzip become gigabytes in memory before any limit is looked at. 48 MB of zeros
    # compress to a few hundred KB; with the inflater bounded the peak stays near the 1 MB limit.
    limit = 1_000_000
    bomb = gzip.compress(b"\x00" * 48_000_000, compresslevel=1)
    assert len(bomb) < 400_000  # a few hundred KB on the wire
    tracemalloc.start()
    try:
        with pytest.raises(TransportError, match="larger than"):
            _decode(bomb, "gzip", limit)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8_000_000, f"peaked at {peak:,} bytes"


def test_an_oversized_plain_body_is_refused() -> None:
    small_limit = UrllibTransport(proxies={}, max_body_bytes=1_000)
    with (
        LocalServer(lambda _: ok(b"y" * 5_000)) as srv,
        pytest.raises(TransportError, match="larger than"),
    ):
        small_limit.send(get(srv.url))


def test_a_body_exactly_at_the_limit_is_accepted() -> None:
    at_limit = UrllibTransport(proxies={}, max_body_bytes=1_000)
    with LocalServer(lambda _: ok(b"y" * 1_000)) as srv:
        assert at_limit.send(get(srv.url)).body == b"y" * 1_000


def test_redirects_are_not_followed_so_the_key_cannot_follow_them(
    transport: UrllibTransport,
) -> None:
    with LocalServer(lambda _: ok(b'{"leaked": true}')) as elsewhere:
        target = elsewhere.url + "/collect"
        with LocalServer(lambda _: (302, {"Location": target}, b"")) as origin:
            resp = transport.send(get(origin.url, {"Authorization": "Bearer not-a-real-key"}))
        assert resp.status == 302
        assert resp.headers["location"] == target
        assert elsewhere.seen == []  # the second host never saw a request, let alone the header


def test_connection_refused_is_a_transport_error_with_its_cause(
    transport: UrllibTransport,
) -> None:
    with pytest.raises(TransportError) as info:
        transport.send(get(f"http://127.0.0.1:{free_port()}/"))
    assert isinstance(info.value.__cause__, OSError)
    assert "127.0.0.1" in str(info.value)


def test_a_timeout_is_a_transport_error(transport: UrllibTransport) -> None:
    started = time.monotonic()
    with LocalServer(sleepy(2.0)) as srv, pytest.raises(TransportError, match="timed out"):
        transport.send(get(srv.url, timeout=0.2))
    assert time.monotonic() - started < 1.5  # it gave up at 0.2 s, not after the 2 s sleep


def test_a_reply_cut_short_is_a_transport_error(transport: UrllibTransport) -> None:
    # The server promises 100 bytes, sends 10 and hangs up.
    with (
        LocalServer(lambda _: (200, {"Content-Length": "100"}, b"0123456789")) as srv,
        pytest.raises(TransportError),
    ):
        transport.send(get(srv.url))


def test_a_server_that_hangs_up_is_a_transport_error(transport: UrllibTransport) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_and_close() -> None:
        conn, _ = listener.accept()
        conn.close()

    thread = threading.Thread(target=accept_and_close, daemon=True)
    thread.start()
    try:
        with pytest.raises(TransportError):
            transport.send(get(f"http://127.0.0.1:{port}/"))
    finally:
        thread.join(timeout=5)
        listener.close()


def test_only_http_and_https_urls_are_sent(transport: UrllibTransport) -> None:
    for url in ("file:///etc/hosts", "ftp://example.com/x", "gopher://example.com/"):
        with pytest.raises(ValueError, match="http"):
            transport.send(get(url))


def test_the_response_object_normalises_its_headers() -> None:
    resp = TransportResponse(200, {"X-A": "1"}, b"{}")
    assert resp.headers["x-a"] == "1"
    assert resp.status == 200
    assert resp.body == b"{}"
    assert "200" in repr(resp)


def test_responses_of_all_shapes_close_their_sockets(transport: UrllibTransport) -> None:
    # `filterwarnings = error` turns an unclosed socket into a failure on the next GC; hammering the
    # success, error and redirect paths makes a leak show up here rather than in a user's logs.
    seen: list[Seen] = []

    def handler(req: Seen) -> Reply:
        seen.append(req)
        n = len(seen) % 3
        if n == 0:
            return 302, {"Location": "http://127.0.0.1:1/"}, b""
        if n == 1:
            return 404, {}, b'{"error": {"code": "not_found", "message": "m"}}'
        return ok()

    with LocalServer(handler) as srv:
        for _ in range(30):
            transport.send(get(srv.url))
    assert len(seen) == 30


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
