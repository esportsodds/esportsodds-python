"""The HTTP layer, kept behind a one-method protocol so it can be swapped.

`UrllibTransport` is the default: standard library only, so installing this package pulls in
nothing and cannot clash with the `requests` or `httpx` a project already pins. Anything with a
`send(TransportRequest) -> TransportResponse` method works in its place (a `requests.Session` for
connection reuse, a recording fake in a test).
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.message import Message
from typing import IO, Any, Protocol

from ._errors import TransportError
from ._headers import Headers

__all__ = ["Transport", "TransportRequest", "TransportResponse", "UrllibTransport"]

# Far above the biggest page the API sends (a full bulk page is about 10 MB inflated), and far
# below anything that could take a process down. It bounds the inflated size too: a hostile server
# behind a custom base_url could otherwise send a few kilobytes of gzip that expand to gigabytes.
DEFAULT_MAX_BODY_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class TransportRequest:
    """One HTTP request, as handed to a transport."""

    method: str
    url: str
    headers: Mapping[str, str]
    timeout: float
    body: bytes | None = None


class TransportResponse:
    """One HTTP reply. An error status is an ordinary reply, not an exception: the client decides
    what a 404 or a 429 means. `body` is already decompressed."""

    __slots__ = ("body", "headers", "status")

    def __init__(
        self,
        status: int,
        headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self.headers = headers if isinstance(headers, Headers) else Headers(headers)
        self.body = body

    def __repr__(self) -> str:
        return f"TransportResponse(status={self.status}, {len(self.body)} bytes)"


class Transport(Protocol):
    """What the client needs from an HTTP stack.

    Return a `TransportResponse` for every HTTP reply, whatever its status. Raise `TransportError`
    (chaining the original exception) when no reply arrives: refused connection, timeout, TLS
    failure, a body cut short. Do not follow redirects: the request carries the API key, and it
    must not be forwarded to wherever a redirect points.
    """

    def send(self, request: TransportRequest) -> TransportResponse: ...


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would re-send every request header, `Authorization` included, to the redirect target
    (whatever host that is). The API redirects only a match id retired by a merge (308
    `match_merged`), and even that is refused: a 3xx surfaces as an ordinary reply instead."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class UrllibTransport:
    """The default transport, built on `urllib`.

    Args:
        proxies: Passed to `urllib.request.ProxyHandler`: a mapping such as
            ``{"https": "http://proxy.example:3128"}``. None (the default) reads the proxy
            settings from the environment, like any urllib program; ``{}`` uses no proxy.
        max_body_bytes: The largest reply accepted, before and after decompression.
    """

    def __init__(
        self,
        *,
        proxies: Mapping[str, str] | None = None,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        self._max = max_body_bytes
        handlers: list[urllib.request.BaseHandler] = [_NoRedirects()]
        if proxies is not None:
            handlers.append(urllib.request.ProxyHandler(dict(proxies)))
        self._opener = urllib.request.build_opener(*handlers)

    def send(self, request: TransportRequest) -> TransportResponse:
        if urllib.parse.urlsplit(request.url).scheme not in ("http", "https"):
            raise ValueError(f"only http and https URLs are supported, got {request.url!r}")
        headers = {"Accept-Encoding": "gzip", **request.headers}
        req = urllib.request.Request(  # noqa: S310 - scheme checked just above
            request.url, data=request.body, headers=headers, method=request.method
        )
        try:
            try:
                with self._opener.open(req, timeout=request.timeout) as reply:
                    return self._read(reply.status, reply.headers, reply)
            except urllib.error.HTTPError as err:  # non-2xx, and a 3xx we declined to follow
                with err:
                    return self._read(err.code, err.headers, err)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            host = urllib.parse.urlsplit(request.url).netloc
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                reason = "timed out"
            raise TransportError(f"request to {host} failed: {reason}") from exc

    def _read(self, status: int, headers: Message[str, str], fp: IO[bytes]) -> TransportResponse:
        raw = fp.read(self._max + 1)
        if len(raw) > self._max:
            raise TransportError(f"response body is larger than the {self._max} byte limit")
        # read(n) returns whatever arrived before the connection closed, without complaint, so a
        # body cut short would pass for a complete one. A chunked body detects its own end.
        declared = headers.get("Content-Length")
        if declared is not None and declared.strip().isdigit() and int(declared) != len(raw):
            raise TransportError(
                f"response body is shorter than its Content-Length ({len(raw)} of {declared} bytes)"
            )
        body = _decode(raw, headers.get("Content-Encoding"), self._max)
        return TransportResponse(status, headers.items(), body)


def _decode(raw: bytes, encoding: str | None, limit: int) -> bytes:
    name = (encoding or "identity").strip().lower()
    if name in ("", "identity"):
        return raw
    if name not in ("gzip", "x-gzip"):
        raise TransportError(f"unsupported Content-Encoding {name!r}")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(raw, limit + 1)
    except zlib.error as exc:
        raise TransportError(f"response is not valid gzip: {exc}") from exc
    if len(out) > limit:
        raise TransportError(f"inflated response is larger than the {limit} byte limit")
    if not inflater.eof:
        raise TransportError("response is truncated gzip")
    return out
