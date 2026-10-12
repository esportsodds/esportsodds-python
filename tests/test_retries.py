"""What the client retries, how long it waits, and what it refuses to retry.

Every retry here costs real money for a real customer (a 500 counts against the 50,000-request
monthly allowance) or can burn it for nothing (a quota error cannot succeed), so the rules are
pinned one by one. Time is a list: the client's sleep is replaced by `rig.sleeps.append` and its
jitter by a stub, so a test asserts the exact waits and runs instantly.
"""

from __future__ import annotations

from typing import Any

import pytest

from esportsodds import (
    AuthenticationError,
    InvalidCursorError,
    InvalidRequestError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    TransportError,
)

from .conftest import StubRandom, error, html, item, make_rig, page, reply

MATCH_ID = "00000000-0000-7000-8000-000000000001"


# ---------------------------------------------------------------------------------------------
# Rate limit (429 rate_limited): wait exactly what the server says, then try again
# ---------------------------------------------------------------------------------------------


def test_a_rate_limit_waits_exactly_retry_after_then_succeeds() -> None:
    rig = make_rig(error(429, "rate_limited", retry_after="2"), page([{"id": "a"}]))
    got = rig.client.matches.list()
    assert got.data == [{"id": "a"}]
    assert rig.sleeps == [2.0]
    assert len(rig.transport.requests) == 2


def test_the_wait_carries_jitter_so_workers_do_not_retry_in_lockstep() -> None:
    rig = make_rig(error(429, "rate_limited", retry_after="2"), page([]))
    rig.rng.fraction = 0.5  # the stub returns the middle of whatever span it is asked for
    rig.client.matches.list()
    assert rig.rng.calls == [(0.0, 0.5)]
    assert rig.sleeps == [2.25]


def test_the_jitter_never_shortens_the_wait() -> None:
    for fraction in (0.0, 0.25, 0.999):
        rig = make_rig(error(429, "rate_limited", retry_after="3"), page([]))
        rig.rng.fraction = fraction
        rig.client.matches.list()
        assert 3.0 <= rig.sleeps[0] <= 3.5


def test_rate_limit_retries_are_bounded() -> None:
    rig = make_rig(*[error(429, "rate_limited", retry_after="1") for _ in range(10)])
    with pytest.raises(RateLimitError) as info:
        rig.client.matches.list()
    assert info.value.retry_after == 1.0
    assert len(rig.transport.requests) == 4  # the first try and max_retries=3 retries
    assert rig.sleeps == [1.0, 1.0, 1.0]


@pytest.mark.parametrize("max_retries", [0, 1, 2, 5])
def test_max_retries_is_honoured(max_retries: int) -> None:
    rig = make_rig(
        *[error(429, "rate_limited", retry_after="1") for _ in range(10)], max_retries=max_retries
    )
    with pytest.raises(RateLimitError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 1 + max_retries
    assert len(rig.sleeps) == max_retries


def test_a_wait_longer_than_a_minute_is_not_slept_through() -> None:
    rig = make_rig(error(429, "rate_limited", retry_after="61"))
    with pytest.raises(RateLimitError):
        rig.client.matches.list()
    assert rig.sleeps == []
    assert len(rig.transport.requests) == 1


def test_a_wait_of_exactly_a_minute_is_still_taken() -> None:
    rig = make_rig(error(429, "rate_limited", retry_after="60"), page([]))
    rig.client.matches.list()
    assert rig.sleeps == [60.0]


def test_a_rate_limit_without_retry_after_backs_off_instead() -> None:
    rig = make_rig(error(429, "rate_limited"), page([]))
    rig.client.matches.list()
    assert len(rig.sleeps) == 1
    assert 0.25 <= rig.sleeps[0] <= 0.5  # the first backoff window


def test_an_unknown_429_code_is_treated_like_a_rate_limit() -> None:
    # The API may add a throttle code tomorrow. A short Retry-After says "try again soon".
    rig = make_rig(error(429, "brand_new_throttle", retry_after="1"), page([]))
    rig.client.matches.list()
    assert rig.sleeps == [1.0]


def test_an_unknown_429_code_with_a_long_wait_is_not_retried() -> None:
    rig = make_rig(error(429, "brand_new_quota", retry_after="1209600"))
    with pytest.raises(RateLimitError):
        rig.client.matches.list()
    assert rig.sleeps == []


# ---------------------------------------------------------------------------------------------
# Quota (429 quota_*): never retried
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "quota_exceeded",
        "trial_quota_exceeded",
        "trial_endpoint_quota_exceeded",
        "bulk_quota_exceeded",
    ],
)
def test_a_quota_error_is_raised_at_once_without_sleeping(code: str) -> None:
    # Retry-After on a quota 429 is the time to the end of the month: two weeks here. Sleeping it
    # would hang a caller for weeks, and retrying sooner cannot help.
    rig = make_rig(
        error(429, code, retry_after="1209600", x_quota_reset="2026-11-01T00:00:00Z"),
        page([]),
    )
    with pytest.raises(QuotaExceededError) as info:
        rig.client.matches.list()
    assert info.value.code == code
    assert info.value.retry_after == 1209600.0
    assert info.value.reset_at is not None
    assert rig.sleeps == []
    assert len(rig.transport.requests) == 1


def test_a_sandbox_quota_error_is_raised_at_once_with_no_reset() -> None:
    # A sandbox key's allowance never resets: the API sends no Retry-After and no X-Quota-Reset.
    rig = make_rig(error(429, "sandbox_quota_exceeded"), page([]))
    with pytest.raises(QuotaExceededError) as info:
        rig.client.matches.list()
    assert info.value.code == "sandbox_quota_exceeded"
    assert info.value.retry_after is None
    assert info.value.reset_at is None
    assert rig.sleeps == []
    assert len(rig.transport.requests) == 1


def test_a_quota_error_with_a_tiny_retry_after_is_still_not_retried() -> None:
    # The decision is the code, not the size of the wait: a trial reset can sit in the past and
    # arrive as `Retry-After: 0`.
    rig = make_rig(error(429, "quota_exceeded", retry_after="0"), page([]))
    with pytest.raises(QuotaExceededError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 1


# ---------------------------------------------------------------------------------------------
# 503 (unavailable, including the bulk route's busy refusal)
# ---------------------------------------------------------------------------------------------


def test_a_busy_503_waits_retry_after_then_retries() -> None:
    rig = make_rig(error(503, "unavailable", retry_after="2"), page([]))
    rig.client.matches.bulk()
    assert rig.sleeps == [2.0]
    assert len(rig.transport.requests) == 2


def test_503_retries_are_bounded() -> None:
    rig = make_rig(*[error(503, "unavailable", retry_after="2") for _ in range(10)])
    with pytest.raises(ServiceUnavailableError):
        rig.client.matches.bulk()
    assert len(rig.transport.requests) == 4
    assert rig.sleeps == [2.0, 2.0, 2.0]


def test_a_503_without_retry_after_backs_off() -> None:
    rig = make_rig(html(503), html(503), page([]))
    rig.client.matches.list()
    assert len(rig.sleeps) == 2
    assert 0.25 <= rig.sleeps[0] <= 0.5
    assert 0.5 <= rig.sleeps[1] <= 1.0


# ---------------------------------------------------------------------------------------------
# Gateway errors (502 / 504) and the backoff curve
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [502, 504])
def test_a_gateway_error_is_retried_with_backoff(status: int) -> None:
    rig = make_rig(html(status), page([]))
    rig.client.matches.list()
    assert len(rig.sleeps) == 1
    assert len(rig.transport.requests) == 2


def test_the_backoff_window_doubles_and_is_capped() -> None:
    rig = make_rig(*[html(502) for _ in range(10)], max_retries=8)
    rig.rng.fraction = 1.0  # the top of each window
    with pytest.raises(ServerError):
        rig.client.matches.list()
    # windows: 0.5, 1, 2, 4, 8, 8, 8, 8 (the cap); at the top of each window the jitter is maxed
    assert rig.sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0]


def test_the_bottom_of_the_backoff_window_is_half() -> None:
    rig = make_rig(*[html(502) for _ in range(10)], max_retries=3)
    rig.rng.fraction = 0.0
    with pytest.raises(ServerError):
        rig.client.matches.list()
    assert rig.sleeps == [0.25, 0.5, 1.0]


# ---------------------------------------------------------------------------------------------
# 500 internal_error: billed, so retried at most twice
# ---------------------------------------------------------------------------------------------


def test_a_500_is_retried_at_most_twice_even_when_more_retries_are_allowed() -> None:
    rig = make_rig(*[error(500, "internal_error") for _ in range(10)])  # max_retries is 3
    with pytest.raises(ServerError) as info:
        rig.client.matches.list()
    assert info.value.code == "internal_error"
    assert len(rig.transport.requests) == 3  # one try and two retries, not four
    assert len(rig.sleeps) == 2


def test_a_500_follows_a_smaller_max_retries() -> None:
    rig = make_rig(*[error(500, "internal_error") for _ in range(10)], max_retries=1)
    with pytest.raises(ServerError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 2


def test_a_500_that_recovers() -> None:
    rig = make_rig(error(500, "internal_error"), item({"id": MATCH_ID}))
    assert rig.client.matches.get(MATCH_ID) == {"id": MATCH_ID}
    assert len(rig.sleeps) == 1


def test_the_500_budget_is_separate_from_other_retries() -> None:
    # 502, 500, 500, then a 500 that must not be retried: the gateway error did not use up a
    # 500 retry.
    rig = make_rig(
        html(502),
        error(500, "internal_error"),
        error(500, "internal_error"),
        error(500, "internal_error"),
        page([]),
        max_retries=10,
    )
    with pytest.raises(ServerError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 4


@pytest.mark.parametrize("status", [501, 505, 507])
def test_other_5xx_statuses_are_not_retried(status: int) -> None:
    rig = make_rig(html(status), page([]))
    with pytest.raises(ServerError) as info:
        rig.client.matches.list()
    assert info.value.status == status
    assert len(rig.transport.requests) == 1


# ---------------------------------------------------------------------------------------------
# Everything else is raised immediately
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scripted", "exc"),
    [
        (error(400, "invalid_parameter"), InvalidRequestError),
        (error(400, "invalid_cursor"), InvalidCursorError),
        (error(401, "invalid_api_key"), AuthenticationError),
        (error(401, "missing_api_key"), AuthenticationError),
        (error(404, "not_found"), NotFoundError),
        (error(404, "unknown_game"), NotFoundError),
        (html(418), Exception),
    ],
)
def test_client_errors_are_never_retried(scripted: Any, exc: type[Exception]) -> None:
    rig = make_rig(scripted, page([]))
    with pytest.raises(exc):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 1
    assert rig.sleeps == []


# ---------------------------------------------------------------------------------------------
# Network failures
# ---------------------------------------------------------------------------------------------


def test_a_network_failure_is_retried_then_raised() -> None:
    rig = make_rig(*[TransportError("connection refused") for _ in range(10)])
    with pytest.raises(TransportError, match="connection refused"):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 4
    assert len(rig.sleeps) == 3


def test_a_network_failure_that_recovers() -> None:
    rig = make_rig(TransportError("timed out"), page([{"id": "a"}]))
    assert rig.client.matches.list().data == [{"id": "a"}]
    assert len(rig.sleeps) == 1


def test_a_network_failure_is_raised_at_once_when_retries_are_off() -> None:
    rig = make_rig(TransportError("boom"), page([]), max_retries=0)
    with pytest.raises(TransportError):
        rig.client.matches.list()
    assert rig.sleeps == []


def test_the_ws_token_post_is_retried_like_a_get() -> None:
    rig = make_rig(
        TransportError("reset"), item({"token": "t", "expires_at": "2026-10-07T12:01:00Z"})
    )
    assert rig.client.ws_token()["token"] == "t"
    assert [r.method for r in rig.transport.requests] == ["POST", "POST"]


def test_health_does_not_retry_a_network_failure() -> None:
    rig = make_rig(TransportError("down"), reply({"status": "ok"}))
    with pytest.raises(TransportError):
        rig.client.health()
    assert len(rig.transport.requests) == 1


# ---------------------------------------------------------------------------------------------
# Mixed sequences
# ---------------------------------------------------------------------------------------------


def test_one_call_can_ride_out_several_different_failures() -> None:
    rig = make_rig(
        TransportError("reset"),
        error(429, "rate_limited", retry_after="1"),
        error(503, "unavailable", retry_after="2"),
        page([{"id": "ok"}]),
    )
    assert rig.client.matches.list().data == [{"id": "ok"}]
    assert len(rig.sleeps) == 3
    assert rig.sleeps[1:] == [1.0, 2.0]


def test_a_quota_error_in_the_middle_of_retries_still_stops_at_once() -> None:
    rig = make_rig(
        error(429, "rate_limited", retry_after="1"), error(429, "quota_exceeded"), page([])
    )
    with pytest.raises(QuotaExceededError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 2


def test_every_attempt_updates_last_response() -> None:
    rig = make_rig(
        error(429, "rate_limited", retry_after="1", x_request_id="first"),
        page([], x_request_id="second"),
    )
    rig.client.matches.list()
    assert rig.client.last_response is not None
    assert rig.client.last_response.request_id == "second"


def test_the_http_date_form_of_retry_after_uses_the_injected_clock() -> None:
    # The clock is pinned to 0 (the epoch) in make_rig, so this date is far in the future: past the
    # one-minute cap, so not slept through. It proves the date form is parsed rather than ignored.
    rig = make_rig(error(429, "rate_limited", retry_after="Wed, 21 Oct 2026 07:28:00 GMT"))
    with pytest.raises(RateLimitError) as info:
        rig.client.matches.list()
    assert info.value.retry_after is not None
    assert info.value.retry_after > 60


def test_a_fresh_stub_random_is_deterministic() -> None:
    # Guards the test double itself: the other tests lean on it.
    rng = StubRandom(0.5)
    assert rng.uniform(1.0, 3.0) == 2.0
    assert rng.calls == [(1.0, 3.0)]
