"""The client: configuration, the request loop with its retry policy, and response bookkeeping."""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import random
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

from ._errors import (
    ApiError,
    InvalidResponseError,
    QuotaExceededError,
    ServiceUnavailableError,
    TransportError,
    error_from_response,
)
from ._headers import Headers
from ._pagination import Page
from ._resources import Coverage, Matches, Model, Odds, Players, Rankings, Teams, Tournaments
from ._routes import CSV_PARAMS, ROUTES, Route
from ._time import parse_rfc3339, to_query_value
from ._transport import Transport, TransportRequest, TransportResponse, UrllibTransport
from ._version import __version__

__all__ = ["Client", "RateLimit", "ResponseInfo"]

_log = logging.getLogger("esportsodds")

DEFAULT_BASE_URL = "https://api.esportsodds.gg"
DEFAULT_GAME = "cs2"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
ENV_API_KEY = "ESPORTSODDS_API_KEY"
ENV_BASE_URL = "ESPORTSODDS_BASE_URL"

# The retry policy, in one place. See `_retry_wait` for how these combine.
MAX_RETRY_AFTER = 60.0  # never sleep longer than this because the server said to
SERVER_ERROR_RETRIES = 2  # a 500 is billed against the allowance, so it is retried at most twice
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0
RETRY_AFTER_JITTER = 0.5  # added to a server-requested wait so workers do not retry in lockstep

_KEY_RE = re.compile(r"[\x21-\x7e]+")  # printable ASCII with no spaces: safe in a header value
_GAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")
_DIGITS = re.compile(r"\d{1,18}")


class _Uniform(Protocol):
    """The one method of `random.Random` the client uses (a seam so tests need no randomness)."""

    def uniform(self, a: float, b: float, /) -> float: ...


class _Secret:
    """Holds the API key so that no repr, str, log line or pickle can show it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        # The last four characters tell two keys apart in a log; a short value shows nothing.
        tail = self._value[-4:] if len(self._value) >= 12 else ""
        return f"'...{tail}'"

    __str__ = __repr__

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError(
            "an API key cannot be pickled or copied; create the Client where it is used"
        )


@dataclass(frozen=True, slots=True)
class RateLimit:
    """The request budget the API reported on one response.

    Every field is None when its header was absent or unreadable.

    Attributes:
        limit: The per-second request budget (`X-RateLimit-Limit`).
        remaining: What is left of it right now (`X-RateLimit-Remaining`).
        quota_limit: The month's request allowance (`X-Quota-Limit`).
        quota_remaining: What is left of the month's allowance (`X-Quota-Remaining`).
        quota_reset: When the allowance resets, as a timezone-aware UTC datetime
            (`X-Quota-Reset`).
    """

    limit: int | None = None
    remaining: int | None = None
    quota_limit: int | None = None
    quota_remaining: int | None = None
    quota_reset: datetime | None = None

    @classmethod
    def from_headers(cls, headers: Headers) -> RateLimit:
        reset: datetime | None = None
        raw_reset = headers.get("x-quota-reset")
        if raw_reset is not None:
            try:
                reset = parse_rfc3339(raw_reset.strip())
            except ValueError:
                reset = None
        return cls(
            limit=_int_header(headers, "x-ratelimit-limit"),
            remaining=_int_header(headers, "x-ratelimit-remaining"),
            quota_limit=_int_header(headers, "x-quota-limit"),
            quota_remaining=_int_header(headers, "x-quota-remaining"),
            quota_reset=reset,
        )


def _int_header(headers: Headers, name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    text = value.strip()
    return int(text) if _DIGITS.fullmatch(text) else None


@dataclass(frozen=True, slots=True)
class ResponseInfo:
    """What came back with the most recent response, whatever its status.

    Attributes:
        status: The HTTP status code.
        request_id: The `X-Request-Id` to quote when reporting a problem.
        rate_limit: The request budget the response reported.
        headers: All response headers.
    """

    status: int
    request_id: str | None
    rate_limit: RateLimit
    headers: Headers


class Client:
    """A client for the esportsodds.gg API.

    Args:
        api_key: Your API key. Falls back to the ``ESPORTSODDS_API_KEY`` environment variable.
        base_url: The API's address; defaults to ``https://api.esportsodds.gg`` (or the
            ``ESPORTSODDS_BASE_URL`` environment variable). Plain ``http`` is accepted only for
            ``localhost`` and other loopback addresses, because the key travels with every request.
        game: The title to query. ``"cs2"`` is the only one with data today.
        timeout: Seconds to wait for a reply (the bulk export always gets at least 120).
        max_retries: How many times a failed request is retried (``0`` turns retries off).
            See the README for exactly what is and is not retried.
        transport: Swap in your own HTTP stack: any object with
            ``send(TransportRequest) -> TransportResponse``.

    Responses are plain dicts straight from the API; list calls return a `Page`. After any call,
    `last_response` holds the request id and the remaining request budget.

    A client is safe to share between threads.
    """

    matches: Matches
    odds: Odds
    teams: Teams
    tournaments: Tournaments
    players: Players
    rankings: Rankings
    coverage: Coverage
    model: Model

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        game: str = DEFAULT_GAME,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
    ) -> None:
        self._key = _Secret(_resolve_key(api_key))
        self._base_url = _resolve_base_url(base_url)
        self._game = _check_game(game)
        self._timeout = _check_timeout(timeout)
        self._max_retries = _check_max_retries(max_retries)
        if transport is not None and not callable(getattr(transport, "send", None)):
            raise TypeError("transport must have a send(TransportRequest) method")
        self._transport: Transport = transport if transport is not None else UrllibTransport()
        self._local = threading.local()
        # Seams: tests replace these so retries run instantly and deterministically.
        self._sleep: Callable[[float], None] = time.sleep
        self._clock: Callable[[], float] = time.time
        self._rng: _Uniform = random.Random()  # noqa: S311 - retry jitter, not a secret

        self.matches = Matches(self)
        self.odds = Odds(self)
        self.teams = Teams(self)
        self.tournaments = Tournaments(self)
        self.players = Players(self)
        self.rankings = Rankings(self)
        self.coverage = Coverage(self)
        self.model = Model(self)

    def __repr__(self) -> str:
        return f"Client(base_url={self._base_url!r}, game={self._game!r}, api_key={self._key!r})"

    def close(self) -> None:
        """Release the transport's resources, if it holds any (the default one holds none)."""
        closer = getattr(self._transport, "close", None)
        if callable(closer):
            closer()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def game(self) -> str:
        """The game this client queries."""
        return self._game

    @property
    def last_response(self) -> ResponseInfo | None:
        """The request id and request budget from this thread's most recent response (None before
        the first call). Updated by error replies too, and by each retry attempt."""
        info: ResponseInfo | None = getattr(self._local, "last", None)
        return info

    # --- calls that are not plain resources ------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Check the service. Needs no key and is never retried.

        Returns the body as sent: ``{"status": "ok"}`` when healthy, and
        ``{"status": "unavailable"}`` when the API is up but cannot reach its database (that
        answer is returned, not raised, since reporting it is the point of the call).

        Raises:
            ApiError: Any other failure, such as a gateway error page.
            TransportError: The service could not be reached at all.
        """
        try:
            parsed, info = self._send(ROUTES["health"], None, {})
        except ServiceUnavailableError as exc:
            if isinstance(exc.body, dict) and isinstance(exc.body.get("status"), str):
                return exc.body
            raise
        return _to_item(parsed, info, wrapped=False)

    def ws_token(self) -> dict[str, Any]:
        """Mint a short-lived ticket for the WebSocket (``{"token", "expires_at"}``).

        A ticket is valid for 60 seconds. It is not single-use, so a connection attempt retried
        inside that window can reuse it, but mint a fresh one once it has expired. Minting is
        authenticated and rate-limited but does not count against your request allowance.
        """
        return self._item("ws_token")

    def ws_url(self, token: str | Mapping[str, Any]) -> str:
        """The ``wss://`` address to connect to with a ticket from `ws_token`.

        Accepts the ticket string, or the dict `ws_token` returned. The client does not open the
        socket; see the README for a short example with the ``websockets`` package.
        """
        ticket: Any = token.get("token") if isinstance(token, Mapping) else token
        if not isinstance(ticket, str) or not ticket:
            raise ValueError("token must be the non-empty ticket string from ws_token()")
        scheme = "wss" if self._base_url.startswith("https://") else "ws"
        rest = self._base_url.split("://", 1)[1]
        return f"{scheme}://{rest}/v1/ws?token={quote(ticket, safe='')}"

    # --- what the resource namespaces call --------------------------------------------------

    def _page(self, key: str, ident: str | None = None, /, **query: Any) -> Page:
        parsed, info = self._send(ROUTES[key], ident, query)
        return _to_page(parsed, info)

    def _item(self, key: str, ident: str | None = None, /, **query: Any) -> dict[str, Any]:
        parsed, info = self._send(ROUTES[key], ident, query)
        return _to_item(parsed, info, wrapped=True)

    # --- the request loop -------------------------------------------------------------------

    def _send(
        self, route: Route, ident: str | None, query: Mapping[str, Any]
    ) -> tuple[Any, ResponseInfo]:
        """Send a request, retrying what is safe to retry; return the decoded JSON body."""
        url = self._url(route, ident, query)
        headers = {"Accept": "application/json", "User-Agent": f"esportsodds-python/{__version__}"}
        if route.authenticated:
            headers["Authorization"] = f"Bearer {self._key.reveal()}"
        request = TransportRequest(
            route.method, url, headers, max(self._timeout, route.min_timeout)
        )
        shown = urlsplit(url).path
        retries = 0
        server_retries = 0
        while True:
            try:
                response = self._transport.send(request)
            except TransportError as exc:
                if not route.retry or retries >= self._max_retries:
                    raise
                wait = _backoff(retries, self._rng)
                self._pause(route, shown, f"network error ({exc})", wait, retries)
                retries += 1
                continue

            info = self._record(response)
            if 200 <= response.status < 300:
                return _decode(response, info), info

            error = error_from_response(
                response.status, response.headers, response.body, now=self._clock()
            )
            wait_or_none = (
                _retry_wait(
                    error,
                    retries=retries,
                    server_retries=server_retries,
                    max_retries=self._max_retries,
                    rng=self._rng,
                )
                if route.retry
                else None
            )
            if wait_or_none is None:
                raise error
            if response.status == 500:
                server_retries += 1
            self._pause(
                route, shown, f"{error.status} {error.code or 'error'}", wait_or_none, retries
            )
            retries += 1

    def _pause(self, route: Route, path: str, reason: str, wait: float, retries: int) -> None:
        _log.info(
            "%s %s: %s; retrying in %.2fs (retry %d of %d)",
            route.method,
            path,
            reason,
            wait,
            retries + 1,
            self._max_retries,
        )
        self._sleep(wait)

    def _record(self, response: TransportResponse) -> ResponseInfo:
        headers = response.headers
        info = ResponseInfo(
            status=response.status,
            request_id=headers.get("x-request-id") or None,
            rate_limit=RateLimit.from_headers(headers),
            headers=headers,
        )
        self._local.last = info
        return info

    def _url(self, route: Route, ident: str | None, query: Mapping[str, Any]) -> str:
        unknown = sorted(set(query) - set(route.params))
        if unknown:  # a method passing a parameter the route table does not know: a bug here
            raise TypeError(f"{route.key} does not take {', '.join(unknown)}")
        path = route.path.replace("{game}", self._game)
        if route.takes_id:
            if ident is None:
                raise TypeError(f"{route.key} needs an id")
            path = path.replace("{id}", quote(ident, safe=""))
        text = _encode_query(route, query)
        return f"{self._base_url}{path}" + (f"?{text}" if text else "")


# ---------------------------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------------------------


def _backoff(retries: int, rng: _Uniform) -> float:
    """Exponential backoff with "equal jitter": half the window is fixed, half is random."""
    # The exponent is capped: the window stops growing at BACKOFF_CAP long before 2**16, and an
    # unbounded `2**retries` would overflow a float for a very large max_retries.
    window = min(BACKOFF_CAP, BACKOFF_BASE * (1 << min(retries, 16)))
    half = window / 2
    return half + rng.uniform(0.0, half)


def _retry_wait(
    error: ApiError,
    *,
    retries: int,
    server_retries: int,
    max_retries: int,
    rng: _Uniform,
) -> float | None:
    """How long to wait before retrying `error`, or None when it must be raised instead.

    - Quota errors are never retried: nothing changes until the month resets, and the
      `Retry-After` on them is the time to the reset (weeks), not a pause to sit through.
    - 429 and 503 wait the server's `Retry-After` (plus a little jitter), or back off when it
      sent none. A wait over a minute is raised rather than slept through.
    - 502 and 504 (a proxy's view of a restart) back off and retry.
    - 500 is retried at most twice, however many retries are allowed: the API counts a 500
      against the allowance, so each retry spends a request the caller is paying for.
    - Everything else (any other 4xx or 5xx) is raised immediately.
    """
    if retries >= max_retries or isinstance(error, QuotaExceededError):
        return None
    status = error.status
    if status in (429, 503):
        asked = error.retry_after
        if asked is None:
            return _backoff(retries, rng)
        if asked > MAX_RETRY_AFTER:
            return None
        return asked + rng.uniform(0.0, RETRY_AFTER_JITTER)
    if status in (502, 504):
        return _backoff(retries, rng)
    if status == 500 and server_retries < SERVER_ERROR_RETRIES:
        return _backoff(retries, rng)
    return None


# ---------------------------------------------------------------------------------------------
# Reading replies
# ---------------------------------------------------------------------------------------------


def _decode(response: TransportResponse, info: ResponseInfo) -> Any:
    try:
        return json.loads(response.body)
    except (ValueError, RecursionError) as exc:
        raise InvalidResponseError(
            f"the API answered {response.status} with a body that is not JSON",
            status=response.status,
            request_id=info.request_id,
        ) from exc


def _bad_shape(what: str, info: ResponseInfo) -> InvalidResponseError:
    return InvalidResponseError(
        f"unexpected response shape: {what}", status=info.status, request_id=info.request_id
    )


def _to_page(parsed: Any, info: ResponseInfo) -> Page:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
        raise _bad_shape("expected an object whose `data` is a list", info)
    rows = parsed["data"]
    if not all(isinstance(row, dict) for row in rows):
        raise _bad_shape("`data` holds something other than objects", info)
    meta = parsed.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise _bad_shape("`meta` is not an object", info)
    cursor = meta.get("next_cursor")
    if cursor is not None and not (isinstance(cursor, str) and cursor):
        # The API sends null, never "", on the last page. An empty or non-string cursor is not
        # something to guess about: reading it as "no more" could silently drop the rest.
        raise _bad_shape("`meta.next_cursor` is neither null nor a non-empty string", info)
    return Page(data=rows, next_cursor=cursor, meta=meta)


def _to_item(parsed: Any, info: ResponseInfo, *, wrapped: bool) -> dict[str, Any]:
    body = parsed.get("data") if wrapped and isinstance(parsed, dict) else parsed
    if not isinstance(body, dict):
        raise _bad_shape(
            "expected an object with a `data` object" if wrapped else "expected a JSON object", info
        )
    return body


# ---------------------------------------------------------------------------------------------
# Building requests
# ---------------------------------------------------------------------------------------------


def _scalar_text(name: str, value: Any) -> str | None:
    """One query value as the text that goes on the wire, or None for "leave it out"."""
    if value is None or value is False:
        return None
    if value is True:
        return "1"  # the API's boolean flags (`latest`, `rank`) are the string "1"
    if isinstance(value, (str, date)):  # a datetime is a date
        return to_query_value(value)
    if isinstance(value, (int, UUID)):
        return str(value)
    raise TypeError(
        f"{name} must be a string, int, UUID, date or datetime, not {type(value).__name__}"
    )


def _encode_query(route: Route, query: Mapping[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for name in route.params:  # the documented order, not the caller's
        if name not in query:
            continue
        value = query[name]
        text: str | None
        if (
            name in CSV_PARAMS
            and isinstance(value, Iterable)
            and not isinstance(value, (str, bytes))
        ):
            items = [_scalar_text(name, item) for item in value]
            if any(item is None for item in items):
                raise TypeError(f"{name} must not contain None or booleans")
            if isinstance(value, (set, frozenset)):
                items.sort(key=str)
            text = ",".join(str(item) for item in items) or None
        else:
            text = _scalar_text(name, value)
        if text is not None:
            pairs.append((name, text))
    # Commas and colons stay readable (`has=rounds,depth`, `changed_since=2026-09-18T00:00:00Z`);
    # everything else reserved is percent-encoded.
    return urlencode(pairs, quote_via=quote, safe=",:")


# ---------------------------------------------------------------------------------------------
# Validating configuration
# ---------------------------------------------------------------------------------------------


def _resolve_key(api_key: str | None) -> str:
    if api_key is not None and not isinstance(api_key, str):
        raise TypeError("api_key must be a string")
    key = (api_key or "").strip() or os.environ.get(ENV_API_KEY, "").strip()
    if not key:
        raise ValueError(
            f"no API key: pass api_key=... or set the {ENV_API_KEY} environment variable"
        )
    if not _KEY_RE.fullmatch(key):
        # Never echo the value: it may be a real key with a stray character.
        raise ValueError(
            "the API key contains characters that cannot appear in an HTTP header "
            "(whitespace, control characters or non-ASCII); check it was copied whole"
        )
    return key


def _resolve_base_url(base_url: str | None) -> str:
    if base_url is not None:
        return _check_base_url(base_url, "base_url")
    from_env = os.environ.get(ENV_BASE_URL, "").strip()
    if from_env:
        return _check_base_url(from_env, ENV_BASE_URL)
    return DEFAULT_BASE_URL


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_base_url(raw: str, origin: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{origin} must be a string")
    text = raw.strip()
    problem: str | None = None
    try:
        parts = urlsplit(text)
        host = parts.hostname
        _ = parts.port  # raises ValueError for a malformed or out-of-range port
    except ValueError:
        raise ValueError(f"{origin} is not a valid URL") from None
    if not text.isascii() or _BAD_URL_CHARS.search(text):
        problem = "must be plain ASCII with no spaces or control characters"
    elif parts.scheme not in ("http", "https") or not host:
        problem = "must be an http(s) URL with a host, such as https://api.esportsodds.gg"
    elif parts.username is not None or parts.password is not None:
        problem = "must not contain a username or password; the key is sent in a header"
    elif "?" in text or "#" in text:
        problem = "must not contain a query string or fragment"
    elif parts.scheme == "http" and not _is_loopback(host):
        problem = (
            "must use https; plain http is accepted only for localhost and other loopback "
            "addresses, because the API key is sent with every request"
        )
    if problem:
        raise ValueError(f"{origin} {problem}")
    return text.rstrip("/")


def _check_game(game: str) -> str:
    if not isinstance(game, str) or not _GAME_RE.fullmatch(game):
        raise ValueError(f"game must be a lowercase slug such as 'cs2', got {game!r}")
    return game


def _check_timeout(timeout: float) -> float:
    ok = (
        isinstance(timeout, (int, float))
        and not isinstance(timeout, bool)
        and math.isfinite(timeout)
        and timeout > 0
    )
    if not ok:
        raise ValueError(f"timeout must be a positive number of seconds, got {timeout!r}")
    return float(timeout)


def _check_max_retries(max_retries: int) -> int:
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError(f"max_retries must be a non-negative integer, got {max_retries!r}")
    return max_retries
