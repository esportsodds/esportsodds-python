"""The README's promises about what the client does, each pinned to the behaviour behind it.

test_readme.py holds the README's tables, snippets and figures to the code. These are the sentences
a reader relies on without running anything: what happens to the key, which hosts are accepted,
what a ticket is, what the licence does and does not cover. A sentence nobody tests drifts: the code
changes and the page keeps promising the old thing. So each claim here is (1) present in the README
as written, and (2) true of the client, with the test that shows it named beside it. Rewording a
sentence means rewording it here, which is the point: it is a decision, not an accident.

Third-party names are not spelled in a shipped test (the export's scan forbids it); the two README
phrases that name one are pinned by tests/contract/test_readme_compliance.py, which is not shipped.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from esportsodds import AuthenticationError, Client, Page
from esportsodds._routes import ROUTES
from esportsodds._transport import _NoRedirects

from . import readme_blocks as rb
from .conftest import BASE_URL, FAKE_KEY, error, item, make_rig, page

TEXT = rb.read()
FLAT_TEXT = " ".join(TEXT.split())
SECTIONS = {name: " ".join(body.split()) for name, body in rb.sections(TEXT).items()}
INTRO = " ".join(TEXT.split("\n## ", 1)[0].split())

KEY_SECTION = "Your API key and the plan"
CONFIG = "Configuration"
WEBSOCKET = "WebSocket (match updates)"
LICENCE = "Licence"
FAQ = "FAQ"


def claim(section: str, phrase: str) -> None:
    """The README says `phrase` (whitespace collapsed) in `section`."""
    body = INTRO if section == "" else SECTIONS[section]
    assert " ".join(phrase.split()) in body, f"{section or 'the introduction'}: {phrase!r}"


def row(n: int = 1) -> dict[str, Any]:
    return {"id": f"00000000-0000-7000-8000-00000000000{n}", "changed_at": "2026-10-01T00:00:00Z"}


# --- the page ------------------------------------------------------------------------------------


def test_a_page_has_the_attributes_the_readme_lists() -> None:
    claim(
        "Quickstart",
        "A list call returns a `Page` (`page.data`, `page.next_cursor`, `page.has_more`, "
        "`page.meta`); a call for one thing returns a dict.",
    )
    first = Page(data=[row()], next_cursor="c1", meta={"count": 1})
    assert (first.data, first.next_cursor, first.has_more, first.meta) == (
        [row()],
        "c1",
        True,
        {"count": 1},
    )
    last = Page(data=[], next_cursor=None, meta={})
    assert last.has_more is False
    rig = make_rig(page([row()], "c9"), item(row()))
    assert isinstance(rig.client.matches.list(), Page)
    assert isinstance(rig.client.matches.get(row()["id"]), dict)


# --- the key --------------------------------------------------------------------------------------


def test_the_key_travels_only_in_the_authorization_header_never_in_a_url() -> None:
    claim(KEY_SECTION, "sends the key only in the `Authorization: Bearer` header, never in a URL.")
    rig = make_rig(page([row()]))
    rig.client.matches.list(limit=1, status="scheduled")
    request = rig.transport.requests[0]
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in request.url
    assert "key" not in request.url.lower().split("?", 1)[-1].replace("limit", "")


def test_repr_shows_only_the_last_four_characters_of_the_key() -> None:
    claim(KEY_SECTION, "It shows only the last four characters in `repr()`")
    key = "eo_live_" + "Z" * 40 + "1234"
    text = repr(Client(api_key=key, base_url=BASE_URL))
    assert "1234" in text
    assert "Z1234" not in text  # not five characters, not eight
    assert key not in text
    assert "ZZZZ" not in text


def test_the_key_is_never_put_in_an_error_message_or_a_log_line() -> None:
    claim(KEY_SECTION, "never puts the key in an error message or a log line.")
    # The log lines are pinned in test_client.py (test_logging_never_contains_the_key); this is
    # the error half, with the sentence it backs.
    rig = make_rig(error(401, "invalid_api_key", "the key was refused"))
    with pytest.raises(AuthenticationError) as caught:
        rig.client.matches.list()
    assert FAKE_KEY not in str(caught.value)
    assert FAKE_KEY not in repr(caught.value)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "127.0.0.2"])
def test_plain_http_is_accepted_for_localhost_and_other_loopback_addresses(host: str) -> None:
    claim(
        KEY_SECTION,
        "Plain `http://` is accepted for `localhost` and other loopback addresses "
        "(`127.0.0.1`, `::1`) alone",
    )
    claim(CONFIG, "HTTPS only, except `localhost` and other loopback addresses.")
    Client(api_key=FAKE_KEY, base_url=f"http://{host}:8080").close()


@pytest.mark.parametrize(
    "host",
    ["api.example.invalid", "10.0.0.5", "192.168.1.2", "0" + ".0.0.0", "localhost.example.invalid"],
)
def test_plain_http_is_refused_for_every_other_host(host: str) -> None:
    with pytest.raises(ValueError, match="https"):
        Client(api_key=FAKE_KEY, base_url=f"http://{host}")


def test_redirects_are_never_followed_so_the_key_cannot_go_to_another_host() -> None:
    claim(
        KEY_SECTION,
        "and redirects are never followed, so a key cannot be sent to a different host.",
    )
    # Exercised against real sockets in test_transport.py and test_integration.py; this is the
    # handler that makes it so: it declines every redirect, whatever the status or target.
    handler = _NoRedirects()
    for status in (301, 302, 303, 307, 308):
        declined: object = handler.redirect_request(  # type: ignore[func-returns-value]
            None, None, status, "Found", {}, "https://elsewhere.invalid/"
        )
        assert declined is None


def test_the_key_stays_on_your_server_advice_is_given() -> None:
    claim(KEY_SECTION, "Keep the key on your server.")
    claim(KEY_SECTION, "A browser or mobile app should call your backend, not the API.")


# --- configuration --------------------------------------------------------------------------------


def test_a_client_is_safe_to_share_between_threads() -> None:
    claim(CONFIG, "A `Client` is safe to share between threads.")
    claim(FAQ, "The client is synchronous and safe to share between threads.")
    rig = make_rig(*[page([row(n % 9 + 1)]) for n in range(24)])
    results: list[str] = []
    failures: list[BaseException] = []

    def work() -> None:
        try:
            results.extend(rig.client.matches.list(limit=1).data[0]["id"] for _ in range(3))
        except BaseException as exc:  # reported by the assertion below
            failures.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert failures == []
    assert len(results) == 24
    assert len(rig.transport.requests) == 24


def test_close_releases_only_what_a_custom_transport_holds() -> None:
    claim(
        CONFIG,
        "it closes a custom `transport` that has a `close()` method (the default one keeps no "
        "connections open, so it has nothing to release).",
    )

    class Closing:
        closed = 0

        def send(self, request: object) -> object:
            raise AssertionError("not called")

        def close(self) -> None:
            self.closed += 1

    custom = Closing()
    Client(api_key=FAKE_KEY, transport=custom).close()  # type: ignore[arg-type]
    assert custom.closed == 1
    Client(api_key=FAKE_KEY).close()  # the default transport has no close(): nothing to do


# --- the bulk export ------------------------------------------------------------------------------


def test_the_methods_table_says_how_big_a_bulk_page_is() -> None:
    claim("Methods", f"embedded (`include=`), {ROUTES['matches.bulk'].page_max} per request.")


# --- the odds and the model -----------------------------------------------------------------------


def test_the_model_line_is_coming_soon_everywhere_the_readme_mentions_it() -> None:
    claim("Methods", 'The model line (`source="eo_model"`) is coming soon and is not returned yet.')
    claim("Methods", "The model line is coming soon; the list may be empty.")
    paragraphs = [" ".join(block.split()) for block in TEXT.split("\n\n")]
    mentions = [p for p in paragraphs if "eo_model" in p or "model line" in p.lower()]
    assert len(mentions) >= 2
    for paragraph in mentions:
        assert "coming soon" in paragraph, paragraph
    assert "coming soon" in (type(Client(api_key=FAKE_KEY).model).__doc__ or "")


def test_the_odds_the_readme_describes_are_our_own_lines() -> None:
    claim("Methods", 'The odds are our own derived lines (`source="eo_market"`)')
    claim("Methods", "not any bookmaker's price.")


# --- the WebSocket --------------------------------------------------------------------------------


def test_a_ticket_lasts_sixty_seconds_and_may_be_reused_inside_that_window() -> None:
    claim(
        WEBSOCKET,
        "A ticket lasts 60 seconds and is not single-use, so a retry inside that window can reuse "
        "it; mint a new one once it has expired.",
    )
    # The client's own docstring says the same (and tests/contract compares it with the server).
    assert "not single-use" in (Client.ws_token.__doc__ or "")
    assert "60 seconds" in (Client.ws_token.__doc__ or "")


def test_the_key_never_appears_in_the_socket_address() -> None:
    claim(WEBSOCKET, "Your key never appears in the socket address.")
    client = Client(api_key=FAKE_KEY, base_url=BASE_URL)
    address = client.ws_url({"token": "TICKET", "expires_at": "2026-10-01T00:00:00Z"})
    assert address == "wss://api.test.invalid/v1/ws?token=TICKET"
    assert FAKE_KEY not in address
    assert FAKE_KEY[-4:] not in address


def test_the_socket_has_no_replay_and_a_gap_means_subscribe_again() -> None:
    claim(WEBSOCKET, "Reconnect with exponential backoff and jitter, and re-send your `subscribe`.")
    claim(WEBSOCKET, "There is no replay.")
    claim(WEBSOCKET, "`seq` counts up per match;")
    claim(
        WEBSOCKET,
        "if one is skipped, subscribe to that match again to get a fresh `snapshot`.",
    )
    claim(
        WEBSOCKET, "A `snapshot` is the full current state of a match and an `update` is a change."
    )


def test_the_socket_costs_no_requests() -> None:
    claim(
        WEBSOCKET,
        "and it costs no requests: neither the ticket, nor the connection, nor the messages count "
        "against your allowance.",
    )
    claim("FAQ", "The WebSocket costs none.")
    assert ROUTES["ws_token"].authenticated  # a ticket needs the key; the server does not bill it


def test_the_package_does_not_open_sockets_itself() -> None:
    claim(WEBSOCKET, "This package does not open sockets itself (that would need a dependency).")
    assert not hasattr(Client, "connect")
    assert not hasattr(Client, "websocket")


# --- the licence and what the data is -------------------------------------------------------------


def test_the_licence_covers_the_client_and_not_the_data() -> None:
    claim(LICENCE, "The licence covers this client, not the data it fetches:")
    claim(LICENCE, "the data is licensed to you under sections 4 and 5 of the")
    claim(
        LICENCE,
        "which allow you to build on it for your own purposes and do not allow redistributing it.",
    )


def test_the_readme_claims_no_affiliation_and_names_the_trademark_owner() -> None:
    claim(
        LICENCE,
        "Counter-Strike and CS2 are trademarks of Valve Corporation. esportsodds.gg is "
        "independent and is not affiliated with, endorsed by or sponsored by Valve.",
    )
    claim(FAQ, "esportsodds.gg is not affiliated with or endorsed by Valve or any stats site.")


# --- the introduction -----------------------------------------------------------------------------


def test_the_introduction_describes_a_client_for_a_paid_api() -> None:
    claim("", "This package is a client for a paid API;")
    claim("", "Standard library only: there is nothing else to install.")
    claim("", "returned as plain Python dicts.")
