"""A tiny HTTP server on 127.0.0.1 for the tests that need a real socket.

Used by the transport tests and nothing else; every other test runs on `FakeTransport`. It
listens on loopback only, on an ephemeral port, and serves whatever the test's handler returns.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType


@dataclass
class Seen:
    """One request the server received."""

    method: str
    path: str
    headers: dict[str, str]  # lower-cased names
    body: bytes = b""


# What a handler returns: status, headers, body.
Reply = tuple[int, Mapping[str, str], bytes]
Handler = Callable[[Seen], Reply]


@dataclass
class LocalServer:
    handler: Handler
    seen: list[Seen] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def __enter__(self) -> LocalServer:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                seen = Seen(
                    method=self.command,
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                outer.seen.append(seen)
                status, headers, body = outer.handler(seen)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if not any(n.lower() == "content-length" for n in headers):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _serve  # noqa: N815 - the names http.server dispatches on
            do_POST = _serve  # noqa: N815

            def log_message(self, format: str, *args: object) -> None:
                pass

        class _Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        server = _Server(("127.0.0.1", 0), _Handler)
        self._server = server
        # A short poll interval: shutdown() waits for the loop to notice, which is 0.5 s by default
        # and would add that to every test.
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._server is not None
        assert self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"


def free_port() -> int:
    """A loopback port nothing is listening on (bind, read the number, release)."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


def sleepy(seconds: float, reply: Reply = (200, {}, b"{}")) -> Handler:
    """A handler that waits before answering, for timeout tests."""

    def handle(_: Seen) -> Reply:
        time.sleep(seconds)
        return reply

    return handle
