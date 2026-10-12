"""Everything this package raises, and the mapping from the API's error envelope to it.

The API answers every failure with `{"error": {"code", "message", "request_id"}}`. `code` is the
stable part (branch on it); `message` is prose. This module maps a code to an exception class, and
falls back to the HTTP status for anything it does not recognise, so a code the API adds tomorrow
(or a proxy's HTML error page) still raises something sensible instead of crashing the caller.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ._headers import Headers, parse_retry_after
from ._time import parse_rfc3339

__all__ = [
    "ERROR_CODES",
    "ApiError",
    "AuthenticationError",
    "ConflictError",
    "EsportsOddsError",
    "InvalidCursorError",
    "InvalidRequestError",
    "InvalidResponseError",
    "NotFoundError",
    "PermissionDeniedError",
    "QuotaExceededError",
    "RateLimitError",
    "ServerError",
    "ServiceUnavailableError",
    "TransportError",
    "error_from_response",
]

# How much of a reply that is not JSON is kept on the exception. A proxy's error page can be large.
_BODY_KEEP = 2_000


class EsportsOddsError(Exception):
    """Base class for every exception this package raises."""


class TransportError(EsportsOddsError):
    """The request produced no usable HTTP reply: connection failure, timeout, TLS failure or a
    reply that was cut short. The original exception is chained as `__cause__`."""


class InvalidResponseError(EsportsOddsError):
    """The server answered, but not with the JSON this client expects (for example a 200 with an
    HTML body, or a list endpoint whose `data` is not a list)."""

    def __init__(
        self, message: str, *, status: int | None = None, request_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id


class ApiError(EsportsOddsError):
    """The API answered with an error status.

    Attributes:
        status: The HTTP status code.
        code: The API's stable error code (for example ``quota_exceeded``), or None when the
            reply carried no coded envelope.
        message: Human-readable detail. Not part of the API contract; log it, never match on it.
        request_id: The id to quote when reporting a problem (also the `X-Request-Id` header).
        retry_after: Seconds the server asked the caller to wait, when it said.
        headers: The response headers.
        body: The parsed JSON body, or a truncated copy of the text when it was not JSON.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        headers: Headers | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after
        self.headers = headers if headers is not None else Headers()
        self.body = body

    def __str__(self) -> str:
        if self.code:
            text = f"{self.status} {self.code}: {self.message}"
        elif self.message.startswith(f"HTTP {self.status}"):
            text = self.message
        else:
            text = f"{self.status}: {self.message}"
        if self.request_id:
            text += f" (request_id={self.request_id})"
        return text

    def __reduce__(self) -> tuple[Any, ...]:
        # Exceptions pickle as `cls(*args)`, which cannot rebuild a keyword-only __init__.
        return (_rebuild_api_error, (type(self), dict(self.__dict__)))


def _rebuild_api_error(cls: type[ApiError], state: dict[str, Any]) -> ApiError:
    err = cls.__new__(cls)
    Exception.__init__(err, state["message"])
    err.__dict__.update(state)
    return err


class AuthenticationError(ApiError):
    """401: the key is missing or not accepted.

    The API answers every unusable key the same way (`invalid_api_key`) so a response never says
    why: an unknown or revoked key, and a key whose subscription has ended, is paused or was never
    paid, all look alike.
    """


class PermissionDeniedError(ApiError):
    """403: the key is valid but may not do this (`forbidden`, or `sandbox_scope` when a sandbox key
    asks for data outside its sample)."""


class NotFoundError(ApiError):
    """404: no such game, or no such resource.

    Also raised for a match id retired by a merge (status 308, code `match_merged`): the client does
    not follow redirects, so read the surviving id from `headers.get("x-merged-into")` and store it.
    """


class InvalidRequestError(ApiError):
    """400: the request was malformed (a bad filter value, a bad id format). Retrying unchanged
    will fail identically."""


class InvalidCursorError(InvalidRequestError):
    """400 `invalid_cursor`: the cursor is stale or was minted under a different `sort`. Start
    again from the first page."""


class ConflictError(ApiError):
    """409: the request conflicts with the resource's current state."""


class RateLimitError(ApiError):
    """429 `rate_limited`: the key's token bucket is empty. The client retries these by itself;
    this is raised only when the retries are used up or the wait asked for is too long."""


class QuotaExceededError(ApiError):
    """429: the account's request allowance is spent (`quota_exceeded`, a legacy trial's
    `trial_quota_exceeded` / `trial_endpoint_quota_exceeded`, a sandbox key's
    `sandbox_quota_exceeded`, the public sample key's daily `sample_quota_exceeded`, or a plan's
    monthly bulk-export allowance, `bulk_quota_exceeded`).

    Never retried: nothing changes until the quota resets, and a refused request is not counted,
    so there is nothing to gain by trying again. See `reset_at`. A sandbox key's allowance never
    resets, so its error has no `reset_at` and no `retry_after`.
    """

    @property
    def reset_at(self) -> datetime | None:
        """When the allowance resets (the `X-Quota-Reset` header), or None if it was not sent."""
        value = self.headers.get("x-quota-reset")
        if value is None:
            return None
        try:
            return parse_rfc3339(value)
        except ValueError:
            return None


class ServerError(ApiError):
    """500 `internal_error` (or another 5xx the API does not describe). A 500 counts against the
    quota, so the client retries it only twice."""


class ServiceUnavailableError(ApiError):
    """503 `unavailable`: the service (or the bulk endpoint's two slots) is busy or down."""


# The spec's `error.code` enum. tests/contract fails if this table and the spec ever disagree.
ERROR_CODES: Mapping[str, type[ApiError]] = {
    "missing_api_key": AuthenticationError,
    "invalid_api_key": AuthenticationError,
    "unauthorized": AuthenticationError,
    "rate_limited": RateLimitError,
    "quota_exceeded": QuotaExceededError,
    "trial_quota_exceeded": QuotaExceededError,
    "trial_endpoint_quota_exceeded": QuotaExceededError,
    "sandbox_quota_exceeded": QuotaExceededError,
    "sample_quota_exceeded": QuotaExceededError,
    "bulk_quota_exceeded": QuotaExceededError,
    "unknown_game": NotFoundError,
    "not_found": NotFoundError,
    "match_merged": NotFoundError,
    "invalid_cursor": InvalidCursorError,
    "invalid_parameter": InvalidRequestError,
    "conflict": ConflictError,
    "forbidden": PermissionDeniedError,
    "sandbox_scope": PermissionDeniedError,
    "internal_error": ServerError,
    "unavailable": ServiceUnavailableError,
}

_STATUS_CLASSES: Mapping[int, type[ApiError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
    503: ServiceUnavailableError,
}

_NOT_JSON = object()


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def error_from_response(status: int, headers: Headers, body: bytes, *, now: float) -> ApiError:
    """Build the exception for a non-2xx reply. Never raises: every reply yields an `ApiError`.

    `now` is the clock (seconds since the epoch), used only to resolve an HTTP-date `Retry-After`.
    """
    try:
        parsed: Any = json.loads(body)
    except ValueError:  # includes UnicodeDecodeError and JSONDecodeError
        parsed = _NOT_JSON

    code = message = request_id = None
    if isinstance(parsed, dict):
        detail = parsed.get("error")
        if isinstance(detail, dict):
            code = _as_str(detail.get("code"))
            message = _as_str(detail.get("message"))
            request_id = _as_str(detail.get("request_id"))
        else:
            # The flat `{"error": "text"}` shape the API sent before 2026-07-26.
            message = _as_str(detail)

    if request_id is None:
        request_id = headers.get("x-request-id") or None
    kept: Any = (
        body.decode("utf-8", errors="replace")[:_BODY_KEEP] if parsed is _NOT_JSON else parsed
    )

    cls = ERROR_CODES.get(code) if code else None
    if cls is None:
        cls = _STATUS_CLASSES.get(status)
    if cls is None:
        cls = ServerError if 500 <= status <= 599 else ApiError
    return cls(
        message or f"HTTP {status}",
        status=status,
        code=code,
        request_id=request_id,
        retry_after=parse_retry_after(headers.get("retry-after"), now=now),
        headers=headers,
        body=kept,
    )
