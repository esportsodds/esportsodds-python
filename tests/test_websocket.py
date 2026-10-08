"""The WebSocket ticket and the address to connect to.

The client mints the ticket and builds the URL; it does not open the socket (the README shows how,
with the `websockets` package), so a runtime dependency stays out of the install.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import error, item, make_rig


def test_ws_token_posts_to_the_games_ticket_endpoint_and_returns_the_data() -> None:
    rig = make_rig(item({"token": "ticket-1", "expires_at": "2026-10-07T12:01:00Z"}))
    got = rig.client.ws_token()
    assert got == {"token": "ticket-1", "expires_at": "2026-10-07T12:01:00Z"}
    request = rig.transport.requests[0]
    assert request.method == "POST"
    assert rig.transport.path() == "/v1/cs2/ws-token"
    assert request.body is None
    assert "?" not in request.url


def test_ws_token_follows_the_clients_game() -> None:
    rig = make_rig(item({"token": "t", "expires_at": "x"}), game="dota2")
    rig.client.ws_token()
    assert rig.transport.path() == "/v1/dota2/ws-token"


def test_ws_token_errors_are_typed_like_any_others() -> None:
    from esportsodds import AuthenticationError

    rig = make_rig(error(401, "invalid_api_key"))
    with pytest.raises(AuthenticationError):
        rig.client.ws_token()


@pytest.mark.parametrize(
    ("base_url", "token", "want"),
    [
        ("https://api.test.invalid", "abc", "wss://api.test.invalid/v1/ws?token=abc"),
        # A trailing slash on the base URL does not double up.
        ("https://api.test.invalid/", "abc", "wss://api.test.invalid/v1/ws?token=abc"),
        # Plain http is only ever allowed for loopback, and its socket scheme follows.
        ("http://127.0.0.1:8080", "abc", "ws://127.0.0.1:8080/v1/ws?token=abc"),
        ("http://localhost:8080", "abc", "ws://localhost:8080/v1/ws?token=abc"),
        # A path prefix (a reverse proxy) is kept.
        ("https://api.test.invalid/prefix", "abc", "wss://api.test.invalid/prefix/v1/ws?token=abc"),
        ("https://api.test.invalid:8443", "abc", "wss://api.test.invalid:8443/v1/ws?token=abc"),
        # The ticket is percent-encoded whatever it contains: it is a URL component.
        (
            "https://api.test.invalid",
            "a b/c+d&e=f#g",
            "wss://api.test.invalid/v1/ws?token=a%20b%2Fc%2Bd%26e%3Df%23g",
        ),
        # Real tickets are base64url-ish and pass through untouched.
        ("https://api.test.invalid", "Ab-_.09~", "wss://api.test.invalid/v1/ws?token=Ab-_.09~"),
    ],
)
def test_ws_url(base_url: str, token: str, want: str) -> None:
    rig = make_rig(base_url=base_url)
    assert rig.client.ws_url(token) == want


def test_ws_url_takes_the_dict_ws_token_returned() -> None:
    rig = make_rig()
    ticket = {"token": "from-dict", "expires_at": "2026-10-07T12:01:00Z"}
    assert rig.client.ws_url(ticket) == "wss://api.test.invalid/v1/ws?token=from-dict"


def test_ws_url_never_carries_the_api_key() -> None:
    from .conftest import FAKE_KEY

    rig = make_rig()
    assert FAKE_KEY not in rig.client.ws_url("a-ticket")


@pytest.mark.parametrize("bad", ["", None, 5, b"bytes", {}, {"token": ""}, {"token": 5}, {"no": 1}])
def test_ws_url_refuses_what_is_not_a_ticket(bad: Any) -> None:
    rig = make_rig()
    with pytest.raises(ValueError, match="token"):
        rig.client.ws_url(bad)
