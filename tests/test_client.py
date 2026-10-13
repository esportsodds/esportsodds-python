"""Client construction and the shape of every request it sends."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from esportsodds import (
    AuthenticationError,
    Client,
    InvalidResponseError,
    NotFoundError,
    ServerError,
    __version__,
)
from esportsodds._transport import TransportResponse

from .conftest import (
    BASE_URL,
    FAKE_KEY,
    FakeTransport,
    error,
    headers_of,
    html,
    item,
    make_rig,
    page,
    reply,
)

MATCH_ID = "00000000-0000-7000-8000-000000000001"


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------


def test_the_key_comes_from_the_argument() -> None:
    rig = make_rig(page([]))
    rig.client.coverage.list()
    assert headers_of(rig.transport.requests[0])["authorization"] == f"Bearer {FAKE_KEY}"


def test_the_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESPORTSODDS_API_KEY", FAKE_KEY)
    rig = make_rig(page([]), api_key=None)
    rig.client.coverage.list()
    assert headers_of(rig.transport.requests[0])["authorization"] == f"Bearer {FAKE_KEY}"


def test_the_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESPORTSODDS_API_KEY", "eo_live_" + "1" * 48)
    rig = make_rig(page([]))
    rig.client.coverage.list()
    assert headers_of(rig.transport.requests[0])["authorization"] == f"Bearer {FAKE_KEY}"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_key_is_refused_up_front(key: str | None) -> None:
    with pytest.raises(ValueError, match="ESPORTSODDS_API_KEY"):
        Client(api_key=key)


@pytest.mark.parametrize(
    "key", ["eo_live_a\nb", "eo_live_a b", "eo_live_\tb", "eo_live_é", "a\r\nX: y"]
)
def test_a_key_that_could_inject_a_header_is_refused_without_echoing_it(key: str) -> None:
    with pytest.raises(ValueError) as info:
        Client(api_key=key)
    assert key not in str(info.value)
    assert "eo_live_" not in str(info.value)


def test_surrounding_whitespace_in_the_key_is_trimmed() -> None:
    # A key pasted from a terminal or a .env file often carries a trailing newline.
    rig = make_rig(page([]), api_key=f"  {FAKE_KEY}\n")
    rig.client.coverage.list()
    assert headers_of(rig.transport.requests[0])["authorization"] == f"Bearer {FAKE_KEY}"


@pytest.mark.parametrize(
    ("given", "want"),
    [
        ("https://api.esportsodds.gg", "https://api.esportsodds.gg"),
        ("https://api.esportsodds.gg/", "https://api.esportsodds.gg"),
        ("https://api.example.test:8443", "https://api.example.test:8443"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1:9", "http://127.0.0.1:9"),
        ("http://127.1.2.3:9", "http://127.1.2.3:9"),
        ("http://[::1]:9", "http://[::1]:9"),
        ("http://LOCALHOST:8080", "http://LOCALHOST:8080"),
    ],
)
def test_base_urls_that_are_accepted(given: str, want: str) -> None:
    rig = make_rig(page([]), base_url=given)
    rig.client.coverage.list()
    assert rig.transport.requests[0].url.startswith(want + "/v1/")


@pytest.mark.parametrize(
    "given",
    [
        "http://api.esportsodds.gg",  # plain http would put the key on the wire in the clear
        "http://example.test",
        "http://localhost.example.test",  # looks local, is not
        "http://127.0.0.1.example.test",
        "http://10.0.0.5:8080",  # private is not loopback
        "ftp://example.test",
        "file:///etc/passwd",
        "api.esportsodds.gg",
        "//api.esportsodds.gg",
        "https://",
        "https://user:pass@api.esportsodds.gg",  # credentials in a URL end up in logs
        "https://api.esportsodds.gg?x=1",
        "https://api.esportsodds.gg#frag",
        "",
    ],
)
def test_base_urls_that_are_refused(given: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        Client(api_key=FAKE_KEY, base_url=given)


def test_the_base_url_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESPORTSODDS_BASE_URL", "http://127.0.0.1:1234")
    rig = make_rig(page([]), base_url=None)
    rig.client.coverage.list()
    assert rig.transport.requests[0].url.startswith("http://127.0.0.1:1234/v1/cs2/coverage")


def test_an_unsafe_base_url_from_the_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ESPORTSODDS_BASE_URL", "http://example.test")
    with pytest.raises(ValueError, match="ESPORTSODDS_BASE_URL"):
        Client(api_key=FAKE_KEY)


def test_the_default_base_url_is_the_production_api() -> None:
    rig = make_rig(page([]), base_url=None)
    rig.client.coverage.list()
    assert rig.transport.requests[0].url.startswith("https://api.esportsodds.gg/v1/cs2/coverage")


@pytest.mark.parametrize("game", ["cs2", "dota2", "game-1", "a_b"])
def test_games_that_are_accepted(game: str) -> None:
    rig = make_rig(page([]), game=game)
    rig.client.coverage.list()
    assert rig.transport.path() == f"/v1/{game}/coverage"


@pytest.mark.parametrize("game", ["", "CS2", "cs2/../x", "cs2?x=1", " cs2", "a b", "../"])
def test_games_that_are_refused(game: str) -> None:
    with pytest.raises(ValueError, match="game"):
        Client(api_key=FAKE_KEY, game=game)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"timeout": 0}, "timeout"),
        ({"timeout": -1}, "timeout"),
        ({"timeout": True}, "timeout"),  # a bool is an int to Python: True would mean 1 second
        ({"timeout": False}, "timeout"),
        ({"timeout": float("nan")}, "timeout"),
        ({"timeout": float("inf")}, "timeout"),
        ({"timeout": "30"}, "timeout"),
        ({"timeout": None}, "timeout"),
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": 1.5}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
    ],
)
def test_bad_numeric_settings_are_refused(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        Client(api_key=FAKE_KEY, **kwargs)


# ---------------------------------------------------------------------------------------------
# The key never leaks
# ---------------------------------------------------------------------------------------------


def test_the_key_goes_in_the_header_and_never_in_the_url() -> None:
    rig = make_rig(page([]), item({"id": MATCH_ID}))
    rig.client.matches.list(limit=1)
    rig.client.matches.get(MATCH_ID)
    for req in rig.transport.requests:
        assert FAKE_KEY not in req.url
        assert "apikey" not in req.url.lower()
        assert headers_of(req)["authorization"] == f"Bearer {FAKE_KEY}"


def test_repr_and_str_do_not_show_the_key() -> None:
    client = Client(api_key=FAKE_KEY, base_url=BASE_URL)
    for text in (repr(client), str(client), repr(vars(client)), repr(client.__dict__)):
        assert FAKE_KEY not in text
        assert FAKE_KEY[:-4] not in text
    assert "0000" in repr(client)  # the last four characters, enough to tell two keys apart
    assert "api.test.invalid" in repr(client)


def test_a_short_key_is_fully_masked() -> None:
    assert "abc" not in repr(Client(api_key="abc", base_url=BASE_URL))


def test_logging_never_contains_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="esportsodds")
    rig = make_rig(
        error(429, "rate_limited", retry_after="1"), error(500, "internal_error"), page([])
    )
    rig.client.coverage.list()
    assert caplog.records, "the retries should have been logged"
    for record in caplog.records:
        assert FAKE_KEY not in record.getMessage()
        assert FAKE_KEY not in str(record.__dict__)


def test_exceptions_never_contain_the_key() -> None:
    rig = make_rig(error(401, "invalid_api_key", "invalid API key"))
    with pytest.raises(AuthenticationError) as info:
        rig.client.coverage.list()
    exc = info.value
    for text in (str(exc), repr(exc), repr(exc.headers), repr(exc.body)):
        assert FAKE_KEY not in text


# ---------------------------------------------------------------------------------------------
# What every request looks like
# ---------------------------------------------------------------------------------------------


def test_standard_headers() -> None:
    rig = make_rig(page([]))
    rig.client.coverage.list()
    headers = headers_of(rig.transport.requests[0])
    assert headers["user-agent"] == f"esportsodds-python/{__version__}"
    assert headers["accept"] == "application/json"
    assert rig.transport.requests[0].method == "GET"


def test_the_user_agent_names_only_this_package() -> None:
    # It carries no key and no machine details.
    assert __version__ == "0.3.2"
    rig = make_rig(page([]))
    rig.client.coverage.list()
    assert headers_of(rig.transport.requests[0])["user-agent"] == "esportsodds-python/0.3.2"


def test_the_default_timeout_and_the_bulk_floor() -> None:
    rig = make_rig(page([]), page([]), page([]), page([]))
    rig.client.coverage.list()
    rig.client.matches.bulk()
    assert [r.timeout for r in rig.transport.requests] == [30.0, 120.0]


def test_a_longer_client_timeout_is_kept_for_bulk() -> None:
    rig = make_rig(page([]), timeout=300.0)
    rig.client.matches.bulk()
    assert rig.transport.requests[0].timeout == 300.0


def test_a_shorter_client_timeout_applies_to_ordinary_calls() -> None:
    rig = make_rig(page([]), timeout=5.0)
    rig.client.coverage.list()
    assert rig.transport.requests[0].timeout == 5.0


@pytest.mark.parametrize(
    ("kwargs", "want"),
    [
        ({}, {}),
        ({"status": "completed", "limit": 5}, {"status": "completed", "limit": "5"}),
        # None is "not given"; False is "not asked for".
        ({"status": None, "tournament": None}, {}),
        # Several values join with commas.
        ({"has": ["rounds", "depth"]}, {"has": "rounds,depth"}),
        ({"has": ("rounds",)}, {"has": "rounds"}),
        ({"has": []}, {}),
        # A date stays a date; a datetime becomes an RFC 3339 UTC timestamp.
        (
            {"date_from": date(2026, 9, 1), "date_to": "2026-09-30"},
            {"date_from": "2026-09-01", "date_to": "2026-09-30"},
        ),
        (
            {"changed_since": datetime(2026, 9, 18, 3, 49, 12, tzinfo=timezone.utc)},
            {"changed_since": "2026-09-18T03:49:12Z"},
        ),
        (
            {
                "changed_since": datetime(
                    2026, 9, 18, 5, 49, 12, tzinfo=timezone(timedelta(hours=2))
                )
            },
            {"changed_since": "2026-09-18T03:49:12Z"},
        ),
        ({"changed_since": "2026-09-18T03:49:12.5Z"}, {"changed_since": "2026-09-18T03:49:12.5Z"}),
        # A UUID object is fine wherever an id is.
        ({"tournament": UUID(MATCH_ID)}, {"tournament": MATCH_ID}),
        # Reserved characters are percent-encoded, not trusted.
        ({"sort": "a&b=c d"}, {"sort": "a&b=c d"}),
    ],
)
def test_query_values_are_encoded(kwargs: dict[str, Any], want: dict[str, str]) -> None:
    rig = make_rig(page([]))
    rig.client.matches.list(**kwargs)
    assert rig.transport.query() == want
    assert " " not in rig.transport.requests[0].url  # nothing went out unencoded
    assert "&b=c" not in rig.transport.requests[0].url.replace("%26b%3Dc", "")


def test_the_query_is_sent_in_the_documented_order() -> None:
    rig = make_rig(page([]))
    rig.client.matches.list(limit=5, status="completed", sort="changed_at", team=UUID(MATCH_ID))
    query = rig.transport.requests[0].url.split("?", 1)[1]
    assert [pair.split("=")[0] for pair in query.split("&")] == ["status", "team", "sort", "limit"]


def test_colons_and_commas_are_left_readable() -> None:
    rig = make_rig(page([]))
    rig.client.matches.list(has=["rounds", "depth"], changed_since="2026-09-18T03:49:12Z")
    url = rig.transport.requests[0].url
    assert "has=rounds,depth" in url
    assert "changed_since=2026-09-18T03:49:12Z" in url


def test_true_is_sent_as_one_and_false_is_left_out() -> None:
    rig = make_rig(page([]), page([]))
    rig.client.odds.list(match=MATCH_ID, latest=True)
    rig.client.odds.list(match=MATCH_ID, latest=False)
    assert rig.transport.query(0)["latest"] == "1"
    assert "latest" not in rig.transport.query(1)


def test_an_id_cannot_change_which_endpoint_is_called() -> None:
    rig = make_rig(item({"id": "x"}), item({"id": "x"}))
    rig.client.matches.get("a/b")
    rig.client.teams.get("../players?x=1")
    assert rig.transport.path(0) == "/v1/cs2/matches/a%2Fb"
    assert rig.transport.requests[1].url.endswith("/v1/cs2/teams/..%2Fplayers%3Fx%3D1")


BAD_IDS = ["", "   ", None, ".", "..", 5, b"bytes", ["x"]]

# Every way a caller can hand the client an id (or slug), so a refusal that exists on one method
# but not its siblings shows up.
ID_CALLS = {
    "matches.get": lambda c, v: c.matches.get(v),
    "matches.maps": lambda c, v: c.matches.maps(v),
    "matches.history": lambda c, v: c.matches.history(v),
    "matches.iter_history": lambda c, v: c.matches.iter_history(v),
    "teams.get": lambda c, v: c.teams.get(v),
    "teams.h2h": lambda c, v: c.teams.h2h(MATCH_ID, opponent=v),
    "teams.h2h.team": lambda c, v: c.teams.h2h(v, opponent=MATCH_ID),
    "tournaments.get": lambda c, v: c.tournaments.get(v),
    "players.get": lambda c, v: c.players.get(v),
    "players.career": lambda c, v: c.players.career(v),
}


@pytest.mark.parametrize("name", sorted(ID_CALLS))
@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_an_id_that_cannot_name_a_resource_is_refused_before_any_request(
    name: str, bad: Any
) -> None:
    # "." and ".." are the dangerous ones: percent-encoding leaves them alone, and a proxy or server
    # resolves them into a different URL (".." would address the collection above the resource).
    rig = make_rig()
    with pytest.raises(ValueError, match="id"):
        ID_CALLS[name](rig.client, bad)
    assert rig.transport.requests == []


def test_a_method_cannot_send_a_parameter_the_route_table_does_not_list() -> None:
    # The table is the contract. A method that drifts from it fails loudly here instead of sending
    # a parameter the API would silently ignore.
    rig = make_rig(page([]), item({"id": "x"}))
    with pytest.raises(TypeError, match="bogus"):
        rig.client._page("matches.list", None, bogus=1)
    with pytest.raises(TypeError, match="bogus"):
        rig.client._item("matches.get", "some-id", bogus=1)
    assert rig.transport.requests == []


def test_a_slug_works_where_an_id_does() -> None:
    rig = make_rig(item({"slug": "team-a"}))
    assert rig.client.teams.get("team-a")["slug"] == "team-a"
    assert rig.transport.path() == "/v1/cs2/teams/team-a"


def test_a_refused_match_id_does_not_suggest_a_slug_because_matches_have_none() -> None:
    rig = make_rig()
    with pytest.raises(ValueError, match="UUID") as caught:
        rig.client.matches.get("")
    assert "slug" not in str(caught.value)
    for get in (rig.client.teams.get, rig.client.players.get, rig.client.tournaments.get):
        with pytest.raises(ValueError, match="slug"):
            get("")


# ---------------------------------------------------------------------------------------------
# Reading replies
# ---------------------------------------------------------------------------------------------


def test_a_single_resource_comes_back_as_the_plain_data_dict() -> None:
    rig = make_rig(item({"id": MATCH_ID, "status": "completed"}))
    match = rig.client.matches.get(MATCH_ID)
    assert match == {"id": MATCH_ID, "status": "completed"}


def test_a_list_comes_back_as_a_page() -> None:
    rig = make_rig(page([{"id": "a"}, {"id": "b"}], next_cursor="abc"))
    got = rig.client.matches.list()
    assert got.data == [{"id": "a"}, {"id": "b"}]
    assert got.next_cursor == "abc"
    assert got.meta == {"count": 2, "next_cursor": "abc"}
    assert len(got) == 2
    assert [row["id"] for row in got] == ["a", "b"]


def test_a_page_without_meta_is_the_last_page() -> None:
    rig = make_rig(reply({"data": [{"id": "a"}]}))
    got = rig.client.matches.list()
    assert got.next_cursor is None
    assert got.meta == {}


@pytest.mark.parametrize(
    "bad_reply",
    [
        reply({"data": [], "meta": {"next_cursor": ""}}),  # null ends a list; "" must not
        reply({"data": {"not": "a list"}, "meta": {}}),
        reply({"meta": {}}),
        reply([1, 2, 3]),
        reply(None),
        reply({"data": [], "meta": {"next_cursor": 7}}),
        reply({"data": [], "meta": "meta"}),
        reply({"data": [1, 2], "meta": {}}),  # rows must be objects
        reply({"data": [{"id": "a"}, None], "meta": {}}),
        reply({"data": ["x"], "meta": {}}),
        reply({"data": [[{"id": "a"}]], "meta": {}}),
    ],
)
def test_a_list_reply_of_the_wrong_shape_is_an_invalid_response(bad_reply: Any) -> None:
    rig = make_rig(bad_reply)
    with pytest.raises(InvalidResponseError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 1  # a malformed body is not retried


@pytest.mark.parametrize(
    "bad_reply",
    [reply({"data": [1]}), reply({"data": None}), reply({}), reply([]), reply("x")],
)
def test_a_single_reply_of_the_wrong_shape_is_an_invalid_response(bad_reply: Any) -> None:
    rig = make_rig(bad_reply)
    with pytest.raises(InvalidResponseError):
        rig.client.matches.get(MATCH_ID)


def test_a_200_that_is_not_json_is_an_invalid_response_with_the_request_id() -> None:
    rig = make_rig(
        TransportResponse(200, {"X-Request-Id": "rid-9"}, b"<html>captive portal</html>")
    )
    with pytest.raises(InvalidResponseError, match="not JSON") as info:
        rig.client.matches.get(MATCH_ID)
    assert info.value.status == 200
    assert info.value.request_id == "rid-9"
    assert len(rig.transport.requests) == 1


@pytest.mark.parametrize(
    "body", [b"<html>captive portal</html>", b"", b"{not json", b"\xff\xfe\x00"]
)
def test_health_does_not_mistake_a_non_json_200_for_a_healthy_answer(body: bytes) -> None:
    # health() takes any JSON object as its answer, so a login page or an empty 200 from a proxy
    # must be an error, not `{}`.
    rig = make_rig(TransportResponse(200, {}, body))
    with pytest.raises(InvalidResponseError, match="not JSON"):
        rig.client.health()


@pytest.mark.parametrize("body", [b"[]", b'"ok"', b"null", b"7"])
def test_health_wants_a_json_object(body: bytes) -> None:
    rig = make_rig(TransportResponse(200, {}, body))
    with pytest.raises(InvalidResponseError):
        rig.client.health()


def test_an_error_status_raises_the_typed_exception() -> None:
    rig = make_rig(error(404, "not_found", "not found"))
    with pytest.raises(NotFoundError) as info:
        rig.client.matches.get(MATCH_ID)
    assert info.value.code == "not_found"
    assert info.value.status == 404
    assert len(rig.transport.requests) == 1


def test_a_gateway_page_raises_without_crashing() -> None:
    rig = make_rig(html(502), html(502), html(502), html(502))
    with pytest.raises(ServerError) as info:
        rig.client.matches.get(MATCH_ID)
    assert info.value.status == 502
    assert info.value.code is None


# ---------------------------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------------------------


def test_health_needs_no_key_and_sends_none() -> None:
    rig = make_rig(reply({"status": "ok"}))
    assert rig.client.health() == {"status": "ok"}
    request = rig.transport.requests[0]
    assert request.url == f"{BASE_URL}/health"
    assert "authorization" not in headers_of(request)


def test_health_reports_an_unavailable_service_instead_of_raising() -> None:
    rig = make_rig(reply({"status": "unavailable"}, 503))
    assert rig.client.health() == {"status": "unavailable"}
    assert len(rig.transport.requests) == 1  # a probe is never retried


def test_health_still_raises_for_other_failures() -> None:
    rig = make_rig(html(502))
    with pytest.raises(ServerError):
        rig.client.health()
    assert len(rig.transport.requests) == 1


# ---------------------------------------------------------------------------------------------
# last_response
# ---------------------------------------------------------------------------------------------


def test_last_response_is_empty_before_the_first_call() -> None:
    assert make_rig().client.last_response is None


def test_last_response_exposes_the_request_id_and_the_budget() -> None:
    rig = make_rig(
        page(
            [],
            x_request_id="rid-1",
            x_ratelimit_limit="100",
            x_ratelimit_remaining="99",
            x_quota_limit="50000",
            x_quota_remaining="49999",
            x_quota_reset="2026-11-01T00:00:00Z",
        )
    )
    rig.client.coverage.list()
    last = rig.client.last_response
    assert last is not None
    assert last.status == 200
    assert last.request_id == "rid-1"
    assert last.rate_limit.limit == 100
    assert last.rate_limit.remaining == 99
    assert last.rate_limit.quota_limit == 50000
    assert last.rate_limit.quota_remaining == 49999
    assert last.rate_limit.quota_reset == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_missing_or_garbled_budget_headers_read_as_none() -> None:
    rig = make_rig(page([], x_ratelimit_limit="lots", x_quota_reset="soon"))
    rig.client.coverage.list()
    rate = rig.client.last_response.rate_limit  # type: ignore[union-attr]
    assert rate.limit is None
    assert rate.remaining is None
    assert rate.quota_limit is None
    assert rate.quota_remaining is None
    assert rate.quota_reset is None


def test_last_response_is_updated_by_an_error_reply_too() -> None:
    rig = make_rig(error(404, "not_found", x_request_id="rid-2", x_quota_remaining="7"))
    with pytest.raises(NotFoundError):
        rig.client.matches.get(MATCH_ID)
    last = rig.client.last_response
    assert last is not None
    assert last.status == 404
    assert last.request_id == "rid-2"
    assert last.rate_limit.quota_remaining == 7


def test_last_response_is_per_thread() -> None:
    rig = make_rig(page([], x_request_id="main"), page([], x_request_id="worker"))
    rig.client.coverage.list()
    seen: dict[str, str | None] = {}

    def worker() -> None:
        seen["before"] = rig.client.last_response.request_id if rig.client.last_response else None
        rig.client.coverage.list()
        last = rig.client.last_response
        seen["after"] = last.request_id if last else None

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5)
    assert seen == {"before": None, "after": "worker"}
    assert rig.client.last_response.request_id == "main"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------------------------


class ClosableTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_close_and_the_context_manager_close_the_transport() -> None:
    transport = ClosableTransport()
    client = Client(api_key=FAKE_KEY, base_url=BASE_URL, transport=transport)
    client.close()
    assert transport.closed == 1
    with Client(api_key=FAKE_KEY, base_url=BASE_URL, transport=transport) as inside:
        assert isinstance(inside, Client)
    assert transport.closed == 2


def test_close_is_harmless_when_the_transport_has_nothing_to_close() -> None:
    make_rig().client.close()
    with make_rig().client:
        pass


def test_a_transport_without_send_is_refused_at_construction() -> None:
    class NotATransport:
        pass

    with pytest.raises(TypeError, match="send"):
        Client(api_key=FAKE_KEY, base_url=BASE_URL, transport=NotATransport())  # type: ignore[arg-type]


def test_the_game_is_readable() -> None:
    assert Client(api_key=FAKE_KEY, base_url=BASE_URL, game="dota2").game == "dota2"
    assert Client(api_key=FAKE_KEY, base_url=BASE_URL).game == "cs2"


def test_a_client_cannot_be_pickled_because_it_holds_the_key() -> None:
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(Client(api_key=FAKE_KEY, base_url=BASE_URL))
