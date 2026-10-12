"""Error mapping: the API's coded envelope becomes a typed exception, and nothing crashes on a
reply that is not the envelope (a proxy's HTML page, a body of the wrong shape, a code that does not
exist yet)."""

from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone
from typing import Any

import pytest

from esportsodds import (
    ApiError,
    AuthenticationError,
    ConflictError,
    EsportsOddsError,
    InvalidCursorError,
    InvalidRequestError,
    InvalidResponseError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    TransportError,
)
from esportsodds._errors import ERROR_CODES, error_from_response
from esportsodds._headers import Headers

REQUEST_ID = "00000000-0000-7000-8000-000000000001"


def envelope(code: str, message: str = "boom", request_id: str | None = REQUEST_ID) -> bytes:
    inner: dict[str, Any] = {"code": code, "message": message}
    if request_id is not None:
        inner["request_id"] = request_id
    return json.dumps({"error": inner}).encode()


def build(status: int, body: bytes, headers: dict[str, str] | None = None) -> ApiError:
    return error_from_response(status, Headers(headers or {}), body, now=0.0)


# (code, the status the API sends it with, the class it must map to)
CODE_TABLE: list[tuple[str, int, type[ApiError]]] = [
    ("missing_api_key", 401, AuthenticationError),
    ("invalid_api_key", 401, AuthenticationError),
    ("unauthorized", 401, AuthenticationError),
    ("forbidden", 403, PermissionDeniedError),
    ("sandbox_scope", 403, PermissionDeniedError),
    ("not_found", 404, NotFoundError),
    ("unknown_game", 404, NotFoundError),
    ("match_merged", 308, NotFoundError),
    ("invalid_parameter", 400, InvalidRequestError),
    ("invalid_cursor", 400, InvalidCursorError),
    ("conflict", 409, ConflictError),
    ("rate_limited", 429, RateLimitError),
    ("quota_exceeded", 429, QuotaExceededError),
    ("trial_quota_exceeded", 429, QuotaExceededError),
    ("trial_endpoint_quota_exceeded", 429, QuotaExceededError),
    ("sandbox_quota_exceeded", 429, QuotaExceededError),
    ("sample_quota_exceeded", 429, QuotaExceededError),
    ("bulk_quota_exceeded", 429, QuotaExceededError),
    ("internal_error", 500, ServerError),
    ("unavailable", 503, ServiceUnavailableError),
]


@pytest.mark.parametrize(("code", "status", "cls"), CODE_TABLE)
def test_every_code_maps_to_its_class(code: str, status: int, cls: type[ApiError]) -> None:
    err = build(status, envelope(code, "some message"))
    assert type(err) is cls
    assert err.status == status
    assert err.code == code
    assert err.message == "some message"
    assert err.request_id == REQUEST_ID


def test_the_table_covers_every_known_code() -> None:
    assert {c for c, _, _ in CODE_TABLE} == set(ERROR_CODES)


def test_the_class_hierarchy() -> None:
    assert issubclass(ApiError, EsportsOddsError)
    assert issubclass(TransportError, EsportsOddsError)
    assert issubclass(InvalidResponseError, EsportsOddsError)
    assert not issubclass(TransportError, ApiError)
    assert not issubclass(InvalidResponseError, ApiError)
    assert issubclass(InvalidCursorError, InvalidRequestError)
    for _, _, cls in CODE_TABLE:
        assert issubclass(cls, ApiError)
    assert issubclass(EsportsOddsError, Exception)


def test_a_code_decides_the_class_before_the_status_does() -> None:
    # A 400 that is invalid_cursor is not just "a bad request": it has its own recovery.
    assert type(build(400, envelope("invalid_cursor"))) is InvalidCursorError
    assert type(build(400, envelope("invalid_parameter"))) is InvalidRequestError


def test_request_id_falls_back_to_the_header() -> None:
    err = build(
        500,
        envelope("internal_error", request_id=None),
        {"X-Request-Id": "from-header"},
    )
    assert err.request_id == "from-header"


def test_request_id_from_the_body_wins_over_the_header() -> None:
    err = build(500, envelope("internal_error"), {"X-Request-Id": "from-header"})
    assert err.request_id == REQUEST_ID


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (400, InvalidRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (429, RateLimitError),
        (500, ServerError),
        (502, ServerError),
        (503, ServiceUnavailableError),
        (504, ServerError),
        (418, ApiError),  # any other 4xx is still an ApiError, just the generic one
        (302, ApiError),  # so is a redirect the transport refused to follow
    ],
)
def test_a_reply_that_is_not_json_falls_back_to_the_status_class(
    status: int, cls: type[ApiError]
) -> None:
    html = b"<html><body><h1>502 Bad Gateway</h1></body></html>"
    err = build(status, html)
    assert type(err) is cls
    assert err.status == status
    assert err.code is None
    assert err.message == f"HTTP {status}"
    assert err.request_id is None


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"null",
        b"[]",
        b"42",
        b'"just a string"',
        b'{"error": null}',
        b'{"error": []}',
        b'{"unrelated": true}',
        b"\xff\xfe not utf-8",
        b'{"error": {"code": 7, "message": 9, "request_id": 1}}',  # wrong types, not strings
    ],
)
def test_a_body_of_the_wrong_shape_never_crashes(body: bytes) -> None:
    err = build(500, body)
    assert type(err) is ServerError
    assert err.code is None
    assert err.message == "HTTP 500"


def test_the_old_flat_error_shape_is_still_understood() -> None:
    # `{"error": "message"}` is what the API sent until 2026-07-26 and what some old guides still
    # parse. It carries no code, so the class comes from the status.
    err = build(404, b'{"error": "not found"}')
    assert type(err) is NotFoundError
    assert err.code is None
    assert err.message == "not found"


def test_an_unknown_code_keeps_its_code_and_uses_the_status_class() -> None:
    # The spec will grow codes. An old client must still raise something sensible.
    err = build(400, envelope("brand_new_code", "new thing"))
    assert type(err) is InvalidRequestError
    assert err.code == "brand_new_code"
    assert err.message == "new thing"


def test_a_retired_match_id_is_not_found_here_and_names_the_survivor() -> None:
    # The API answers a match id retired by a merge with 308 `match_merged`. The client does not
    # follow redirects (the key must never travel anywhere it was not sent), so this surfaces as the
    # same NotFoundError a retired id raised before, with the surviving id in a header.
    survivor = "00000000-0000-7000-8000-000000000002"
    err = build(308, envelope("match_merged", "merged"), {"X-Merged-Into": survivor})
    assert type(err) is NotFoundError
    assert err.status == 308
    assert err.headers.get("x-merged-into") == survivor


def test_an_internal_only_code_falls_back_by_status() -> None:
    # These two exist in the code base but never on the public surface, so the spec omits them.
    for code in ("terms_not_accepted", "subscription_required"):
        assert type(build(409, envelope(code))) is ConflictError


def test_rate_limit_error_carries_retry_after() -> None:
    err = build(429, envelope("rate_limited"), {"Retry-After": "2"})
    assert isinstance(err, RateLimitError)
    assert err.retry_after == 2.0


def test_quota_error_carries_the_reset_instant_and_the_wait() -> None:
    err = build(
        429,
        envelope("quota_exceeded"),
        {"Retry-After": "1209600", "X-Quota-Reset": "2026-10-01T00:00:00Z"},
    )
    assert isinstance(err, QuotaExceededError)
    assert err.retry_after == 1209600.0
    assert err.reset_at == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_a_malformed_reset_header_is_ignored_rather_than_raised() -> None:
    err = build(429, envelope("quota_exceeded"), {"X-Quota-Reset": "next tuesday"})
    assert isinstance(err, QuotaExceededError)
    assert err.reset_at is None


def test_retry_after_is_kept_on_a_503_too() -> None:
    err = build(503, envelope("unavailable"), {"Retry-After": "2"})
    assert err.retry_after == 2.0


def test_retry_after_http_date_is_resolved_against_the_injected_clock() -> None:
    err = error_from_response(
        429,
        Headers({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        envelope("rate_limited"),
        now=1792567680 - 5,
    )
    assert err.retry_after == 5.0


def test_str_names_the_status_code_message_and_request_id() -> None:
    err = build(401, envelope("invalid_api_key", "invalid API key"))
    text = str(err)
    for part in ("401", "invalid_api_key", "invalid API key", REQUEST_ID):
        assert part in text


def test_str_without_a_code_or_request_id_is_still_readable() -> None:
    assert str(build(502, b"<html>")) == "HTTP 502"


def test_response_headers_and_body_are_kept_for_debugging() -> None:
    err = build(429, envelope("quota_exceeded"), {"X-Quota-Remaining": "0"})
    assert err.headers["x-quota-remaining"] == "0"
    assert isinstance(err.body, dict)
    assert err.body["error"]["code"] == "quota_exceeded"


def test_a_non_json_body_is_kept_as_truncated_text() -> None:
    err = build(502, b"x" * 10_000)
    assert isinstance(err.body, str)
    assert len(err.body) <= 2_000


def test_errors_survive_pickling() -> None:
    # multiprocessing and task queues pickle exceptions; a keyword-only __init__ breaks that
    # unless the class says how to rebuild itself.
    err = build(
        429,
        envelope("quota_exceeded"),
        {"Retry-After": "9", "X-Quota-Reset": "2026-10-01T00:00:00Z"},
    )
    clone = pickle.loads(pickle.dumps(err))  # noqa: S301 - round-tripping our own object
    assert type(clone) is QuotaExceededError
    assert clone.status == 429
    assert clone.code == "quota_exceeded"
    assert clone.request_id == REQUEST_ID
    assert clone.retry_after == 9.0
    assert clone.reset_at == err.reset_at  # type: ignore[attr-defined]
    assert str(clone) == str(err)


def test_transport_and_response_errors_are_plain_exceptions() -> None:
    cause = ConnectionRefusedError("refused")
    err = TransportError("could not reach the API")
    err.__cause__ = cause
    assert "could not reach" in str(err)
    resp_err = InvalidResponseError("not JSON", status=200, request_id="r1")
    assert resp_err.status == 200
    assert resp_err.request_id == "r1"
    assert "not JSON" in str(resp_err)
