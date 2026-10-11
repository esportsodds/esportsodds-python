"""Every promise the README makes, held to the code.

A README is the first thing a user runs. The quickstart must run, the table of methods must be the
methods that exist, the table of errors must say what the client really retries, and every snippet
must work. These tests execute the README's own code against a scripted transport; nothing here
touches the network.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import io
import json
import re
import sys
import types
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

import esportsodds
from esportsodds import Client, TransportError
from esportsodds import _client as client_module
from esportsodds._errors import ERROR_CODES
from esportsodds._resources import DEFAULT_CHANGE_OVERLAP
from esportsodds._routes import ROUTES
from esportsodds._transport import TransportResponse, UrllibTransport

from . import readme_blocks as rb
from .conftest import BASE_URL, FAKE_KEY, FakeTransport, error, item, make_rig, page, reply

REPO_URL = "https://github.com/esportsodds/esportsodds-python"
TEXT = rb.read()
SECTIONS = rb.sections(TEXT)
# Prose wraps across lines; phrases are checked against the text with its whitespace collapsed.
FLAT = {name: " ".join(body.split()) for name, body in SECTIONS.items()}
BLOCKS = rb.blocks(TEXT)

ID_1 = "00000000-0000-7000-8000-000000000001"
ID_2 = "00000000-0000-7000-8000-000000000002"


def row(n: int = 1, **extra: Any) -> dict[str, Any]:
    base = {
        "id": f"00000000-0000-7000-8000-00000000000{n}",
        "scheduled_at": f"2026-10-0{n}T14:00:00Z",
        "changed_at": f"2026-10-0{n}T15:00:00.000000Z",
        "status": "scheduled",
        "team_a_name": f"Team {chr(64 + 2 * n - 1)}",
        "team_b_name": f"Team {chr(64 + 2 * n)}",
    }
    return {**base, **extra}


def python_block(section: str, containing: str) -> str:
    found = [
        b for b in BLOCKS if b.section == section and b.lang == "python" and containing in b.code
    ]
    assert len(found) == 1, (section, containing, len(found))
    return found[0].code


def run(code: str, namespace: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Execute README code; return what it printed and its final namespace."""
    scope: dict[str, Any] = dict(namespace or {})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<README.md>", "exec"), scope)  # noqa: S102 - the README's own snippet
    return out.getvalue(), scope


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeTransport]:
    """Make `Client()` talk to a scripted transport, with a fake key in the environment."""

    def install(*script: Any) -> FakeTransport:
        transport = FakeTransport(*script)
        monkeypatch.setattr(client_module, "UrllibTransport", lambda *_a, **_k: transport)
        monkeypatch.setenv("ESPORTSODDS_API_KEY", FAKE_KEY)
        monkeypatch.delenv("ESPORTSODDS_BASE_URL", raising=False)
        return transport

    return install


# --- the shape of the document --------------------------------------------------------------------

SECTION_ORDER = [
    "Install",
    "Quickstart",
    "Your API key and the plan",
    "Configuration",
    "Methods",
    "Pagination, the bulk export and the change feed",
    "Errors",
    "Rate limits and retries",
    "WebSocket (match updates)",
    "Examples",
    "FAQ",
    "Licence",
]


def test_the_sections_are_these_and_in_this_order() -> None:
    assert rb.headings(TEXT) == SECTION_ORDER


def test_the_document_starts_with_its_title() -> None:
    assert TEXT.splitlines()[0] == "# esportsodds"


def test_every_python_block_parses() -> None:
    parsed = 0
    for block in BLOCKS:
        if block.lang == "python":
            ast.parse(block.code, filename=f"README.md:{block.line}")
            parsed += 1
    assert parsed >= 9


def test_no_block_hides_a_real_key_or_a_real_id() -> None:
    for block in BLOCKS:
        for match in re.findall(r"eo_(?:live|test)_([A-Za-z0-9]+)", block.code):
            assert len(set(match)) <= 2, block.line
        for uuid in re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", block.code
        ):
            assert uuid.startswith("00000000-0000-"), block.line


# --- install --------------------------------------------------------------------------------------


def test_the_install_section_is_honest_about_pypi_and_names_the_python_floor() -> None:
    body = FLAT["Install"]
    assert "pip install esportsodds" in body
    assert "Python 3.10 or newer" in body
    # Before the first release the section carries ONE pre-release note (the marker publish.yml
    # refuses to release with) and the install-from-GitHub line the note points at. The release
    # checklist deletes both together, so this must hold in either state, never half of each.
    git_install = "pip install git+https://github.com/esportsodds/esportsodds-python"
    marker = body.count("not yet on PyPI")
    assert marker in (0, 1)
    assert (git_install in body) == (marker == 1)
    assert 'requires-python = ">=3.10"' in (rb.ROOT / "pyproject.toml").read_text(encoding="utf-8")


# --- the quickstart -------------------------------------------------------------------------------


def test_the_quickstart_is_at_most_eight_lines() -> None:
    code = python_block("Quickstart", "from esportsodds import Client")
    assert len(code.splitlines()) <= 8


def test_the_quickstart_runs_and_prints_the_fixtures(wire: Callable[..., FakeTransport]) -> None:
    transport = wire(page([row(1), row(2)]))
    out, _ = run(python_block("Quickstart", "from esportsodds import Client"))
    assert out.splitlines() == [
        "2026-10-01T14:00:00Z Team A vs Team B",
        "2026-10-02T14:00:00Z Team C vs Team D",
    ]
    assert transport.requests[0].method == "GET"
    assert transport.path(0) == "/v1/cs2/matches"
    assert transport.query(0) == {"status": "scheduled", "limit": "5"}
    assert transport.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert transport.requests[0].url.startswith("https://api.esportsodds.gg/")


def test_the_quickstart_output_shown_is_what_it_prints(wire: Callable[..., FakeTransport]) -> None:
    shown = [b for b in BLOCKS if b.section == "Quickstart" and b.lang == "text"]
    assert len(shown) == 1
    wire(
        page(
            [
                row(1, scheduled_at="2026-10-08T14:00:00Z"),
                row(2, scheduled_at="2026-10-08T17:30:00Z"),
            ]
        )
    )
    out, _ = run(python_block("Quickstart", "from esportsodds import Client"))
    assert out == shown[0].code


def test_the_quickstart_is_the_first_example_script() -> None:
    code = python_block("Quickstart", "from esportsodds import Client")
    script = (rb.ROOT / "examples" / "quickstart.py").read_text(encoding="utf-8")
    assert script.endswith(code), (
        "examples/quickstart.py must end with the README quickstart verbatim"
    )


# --- plan and key ---------------------------------------------------------------------------------


def test_the_plan_is_stated_as_it_is_sold() -> None:
    body = FLAT["Your API key and the plan"]
    assert "**$49 a month for 50,000 requests**" in body
    assert "per account" in body
    assert "calendar month (UTC)" in body
    assert "paid up front" in body
    assert "no free tier and no trial" in body
    assert "`quota_exceeded`" in body
    assert "QuotaExceededError" in body


# --- configuration --------------------------------------------------------------------------------


def configuration_tables() -> tuple[list[list[str]], list[list[str]]]:
    env, args = rb.tables(SECTIONS["Configuration"])
    return env, args


def test_the_environment_variables_are_the_ones_the_client_reads() -> None:
    env, _ = configuration_tables()
    assert [rb.code_spans(r[0])[0] for r in env] == [
        client_module.ENV_API_KEY,
        client_module.ENV_BASE_URL,
    ]


def test_the_constructor_table_is_the_constructor() -> None:
    _, args = configuration_tables()
    signature = inspect.signature(Client.__init__)
    params = [p for name, p in signature.parameters.items() if name != "self"]
    assert [rb.code_spans(r[0])[0] for r in args] == [p.name for p in params]
    for table_row, param in zip(args, params, strict=True):
        shown = rb.code_spans(table_row[1])[0]
        if param.name == "base_url":
            assert shown == client_module.DEFAULT_BASE_URL
        elif param.name == "game":
            assert shown == f'"{client_module.DEFAULT_GAME}"'
        else:
            assert shown == repr(param.default), param.name


def test_the_documented_timeout_floor_for_the_bulk_export_is_the_routes() -> None:
    _, args = configuration_tables()
    timeout_row = next(r for r in args if r[0].startswith("`timeout`"))
    assert f"at least {int(ROUTES['matches.bulk'].min_timeout)}" in timeout_row[2]


def test_an_unset_key_is_a_value_error_as_documented() -> None:
    _, args = configuration_tables()
    assert "`ValueError`" in next(r for r in args if r[0].startswith("`api_key`"))[2]
    with pytest.raises(ValueError, match="ESPORTSODDS_API_KEY"):
        Client()


def test_plain_http_is_refused_except_for_localhost_as_documented() -> None:
    with pytest.raises(ValueError, match="https"):
        Client(api_key=FAKE_KEY, base_url="http://api.example.invalid")
    Client(api_key=FAKE_KEY, base_url="http://localhost:8080").close()


def test_a_proxy_can_be_given_to_the_default_transport_as_documented() -> None:
    assert "UrllibTransport(proxies={" in TEXT
    transport = UrllibTransport(proxies={"https": "http://proxy.example:3128"})
    Client(api_key=FAKE_KEY, transport=transport).close()


def test_a_client_is_a_context_manager_and_has_close() -> None:
    with Client(api_key=FAKE_KEY, base_url=BASE_URL) as client:
        assert isinstance(client, Client)
    client.close()


# --- the table of methods -------------------------------------------------------------------------


def methods_table() -> list[list[str]]:
    (table,) = rb.tables(SECTIONS["Methods"])
    return table


def test_the_methods_table_lists_every_route_exactly_once() -> None:
    keys: list[str] = []
    for cells in methods_table():
        call = rb.code_spans(cells[0])[0]
        match = re.fullmatch(r"client\.([a-z_.0-9]+)\((.*)\)", call)
        assert match, call
        keys.append(match.group(1))
    assert sorted(keys) == sorted(ROUTES)
    assert len(keys) == len(set(keys)) == len(ROUTES) == 44


def test_each_row_has_the_routes_verb_and_path_and_return_type() -> None:
    for cells in methods_table():
        key = re.fullmatch(r"client\.([a-z_.0-9]+)\(.*\)", rb.code_spans(cells[0])[0]).group(1)  # type: ignore[union-attr]
        route = ROUTES[key]
        assert rb.code_spans(cells[1]) == [f"{route.method} {route.path}"], key
        want = "`Page`" if route.kind == "page" else "`dict`"
        assert cells[2] == want, key


def test_each_row_names_a_method_that_exists_and_takes_what_the_row_shows() -> None:
    client = Client(api_key=FAKE_KEY, base_url=BASE_URL)
    for cells in methods_table():
        call = rb.code_spans(cells[0])[0]
        key, shown = re.fullmatch(r"client\.([a-z_.0-9]+)\((.*)\)", call).groups()  # type: ignore[union-attr]
        target: Any = client
        for part in key.split("."):
            target = getattr(target, part)
        assert callable(target), key
        params = inspect.signature(target).parameters
        for token in [t.strip() for t in shown.split(",") if t.strip()]:
            name = token.split("=")[0]
            assert name in params, (key, name)
            if "=" not in token:
                assert params[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            else:
                assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
                assert params[name].default is inspect.Parameter.empty  # shown only when required


def test_every_description_is_one_plain_sentence_with_no_marketing() -> None:
    for cells in methods_table():
        text = cells[3]
        assert text.endswith("."), text
        assert len(text) <= 130, text


def test_the_filters_paragraph_matches_how_arguments_are_handled(
    wire: Callable[..., FakeTransport],
) -> None:
    body = FLAT["Methods"]
    assert "unknown argument raises `TypeError`" in body
    client = Client(api_key=FAKE_KEY, base_url=BASE_URL, transport=FakeTransport(page([])))
    with pytest.raises(TypeError):
        client.matches.list(bogus=1)  # type: ignore[call-arg]
    rig = make_rig(page([]))
    rig.client.matches.list(has=["rounds", "depth"])
    assert rig.transport.query(0)["has"] == "rounds,depth"
    rig = make_rig(page([]))
    from datetime import date, datetime, timezone

    rig.client.matches.list(date_from=date(2026, 9, 1), changed_since=datetime(2026, 9, 1, 12))
    assert rig.transport.query(0)["date_from"] == "2026-09-01"
    assert rig.transport.query(0)["changed_since"] == "2026-09-01T12:00:00Z"
    assert datetime(2026, 9, 1, 12, tzinfo=timezone.utc)  # a naive datetime above meant UTC


def test_the_derived_line_paragraph_names_no_book_and_promises_no_model() -> None:
    body = FLAT["Methods"]
    assert "not any bookmaker's price" in body
    assert 'source="eo_market"' in body
    assert "coming soon" in body


# --- pagination -----------------------------------------------------------------------------------


def test_the_manual_paging_loop_follows_the_cursor(wire: Callable[..., FakeTransport]) -> None:
    client_rig = make_rig(page([row(1)], "c1"), page([row(2)], None))
    out, _ = run(
        python_block("Pagination, the bulk export and the change feed", "while True:"),
        {"client": client_rig.client},
    )
    assert out.splitlines() == [row(1)["id"], row(2)["id"]]
    assert client_rig.transport.query(0) == {"status": "completed", "limit": "100"}
    assert client_rig.transport.query(1) == {"status": "completed", "limit": "100", "cursor": "c1"}


def test_the_iter_example_walks_lazily(wire: Callable[..., FakeTransport]) -> None:
    rig = make_rig(page([row(1), row(2)], "c1"), page([row(3)], None))
    out, _ = run(
        python_block("Pagination, the bulk export and the change feed", "client.matches.iter("),
        {"client": rig.client},
    )
    assert len(out.splitlines()) == 3
    assert rig.transport.query(0)["date_from"] == "2026-09-01"
    assert rig.transport.query(0)["limit"] == "500"  # the largest page, as the text says


def test_the_bulk_example_asks_for_the_sub_resources(wire: Callable[..., FakeTransport]) -> None:
    rig = make_rig(page([row(1, maps=[{}, {}])], None))
    out, _ = run(
        python_block("Pagination, the bulk export and the change feed", "iter_bulk"),
        {"client": rig.client},
    )
    assert out.strip() == f"{row(1)['id']} 2"
    assert rig.transport.path(0) == "/v1/cs2/matches/bulk"
    assert rig.transport.query(0)["include"] == "maps,stats"


def test_the_change_feed_example_leaves_the_watermark_to_store() -> None:
    rows = [row(1), row(2)]
    rig = make_rig(page(rows, None))
    out, scope = run(
        python_block("Pagination, the bulk export and the change feed", "iter_changes"),
        {"client": rig.client},
    )
    assert scope["newest"] == rows[-1]["changed_at"]
    assert len(out.splitlines()) == 2
    query = rig.transport.query(0)
    assert query["sort"] == "changed_at"
    assert query["limit"] == "500"
    assert "changed_since" in query


def test_the_pagination_claims_hold() -> None:
    body = FLAT["Pagination, the bulk export and the change feed"]
    assert "25 matches per request" in body
    assert ROUTES["matches.bulk"].page_max == 25
    assert (
        "`iter` and `pages` exist on `matches`, `odds`, `teams`, `tournaments` and `players`"
        in body
    )
    client = Client(api_key=FAKE_KEY, base_url=BASE_URL)
    for namespace in ("matches", "odds", "teams", "tournaments", "players"):
        assert callable(getattr(getattr(client, namespace), "iter"))  # noqa: B009
        assert callable(getattr(getattr(client, namespace), "pages"))  # noqa: B009
    assert "ten minutes" in body
    assert DEFAULT_CHANGE_OVERLAP.total_seconds() == 600


# --- errors ---------------------------------------------------------------------------------------

#                        status  retried?  (the README's last column, as a rule)
EXPECTED_STATUS = {
    "AuthenticationError": 401,
    "PermissionDeniedError": 403,
    "NotFoundError": 404,
    "InvalidRequestError": 400,
    "InvalidCursorError": 400,
    "ConflictError": 409,
    "RateLimitError": 429,
    "QuotaExceededError": 429,
    "ServiceUnavailableError": 503,
    "ServerError": 500,
}


def errors_table() -> list[list[str]]:
    (table,) = rb.tables(SECTIONS["Errors"])
    return table


def test_the_errors_table_lists_every_exception_the_package_raises() -> None:
    names = [rb.code_spans(r[0])[0] for r in errors_table()]
    expected = [
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "InvalidRequestError",
        "InvalidCursorError",
        "ConflictError",
        "RateLimitError",
        "QuotaExceededError",
        "ServiceUnavailableError",
        "ServerError",
        "ApiError",
        "TransportError",
        "InvalidResponseError",
    ]
    assert names == expected
    for name in names:
        assert issubclass(getattr(esportsodds, name), esportsodds.EsportsOddsError)


def test_the_codes_in_the_table_are_the_codes_the_client_maps() -> None:
    mapped: dict[str, str] = {}
    for cells in errors_table():
        name = rb.code_spans(cells[0])[0]
        for code in rb.code_spans(cells[1]):
            if code in ERROR_CODES or re.fullmatch(r"[a-z_]+", code):
                mapped[code] = name
    for code, cls in ERROR_CODES.items():
        if code.startswith("trial_"):
            assert code not in mapped  # legacy trials: not in the README, still QuotaExceededError
            assert cls is esportsodds.QuotaExceededError
            continue
        assert mapped.get(code) == cls.__name__, code
    assert set(mapped) == {c for c in ERROR_CODES if not c.startswith("trial_")} | {"any other"} - {
        "any other"
    }


def test_each_code_in_the_table_raises_the_exception_in_its_row_with_the_status_shown() -> None:
    checked = 0
    for cells in errors_table():
        name = rb.code_spans(cells[0])[0]
        if name not in EXPECTED_STATUS:
            continue
        assert cells[2] == str(EXPECTED_STATUS[name]), name
        for code in rb.code_spans(cells[1]):
            rig = make_rig(error(EXPECTED_STATUS[name], code), max_retries=0)
            with pytest.raises(esportsodds.ApiError) as caught:
                rig.client.matches.list()
            assert type(caught.value) is getattr(esportsodds, name), (name, code)
            assert caught.value.status == EXPECTED_STATUS[name]
            assert caught.value.code == code
            checked += 1
    # Every API code but the two legacy-trial ones (which the README leaves out), none skipped.
    assert checked == len(ERROR_CODES) - 2


def retry_outcome(first: TransportResponse | BaseException, *, repeats: int = 1) -> int:
    """How many times the transport was called when `first` is answered (then a 200 follows)."""
    rig = make_rig(*([first] * repeats), page([]))
    with contextlib.suppress(esportsodds.EsportsOddsError):
        rig.client.matches.list()
    return len(rig.transport.requests)


RETRIED_COLUMN: dict[str, tuple[Callable[[], TransportResponse], bool]] = {
    # exception row -> (a reply that triggers it, whether the table says it is retried)
    "AuthenticationError": (lambda: error(401, "invalid_api_key"), False),
    "PermissionDeniedError": (lambda: error(403, "forbidden"), False),
    "NotFoundError": (lambda: error(404, "not_found"), False),
    "InvalidRequestError": (lambda: error(400, "invalid_parameter"), False),
    "InvalidCursorError": (lambda: error(400, "invalid_cursor"), False),
    "ConflictError": (lambda: error(409, "conflict"), False),
    "RateLimitError": (lambda: error(429, "rate_limited", retry_after="1"), True),
    "QuotaExceededError": (lambda: error(429, "quota_exceeded", retry_after="86400"), False),
    "ServiceUnavailableError": (lambda: error(503, "unavailable", retry_after="2"), True),
    "ServerError": (lambda: error(500, "internal_error"), True),
}


def test_the_retried_column_is_what_the_client_does() -> None:
    for cells in errors_table():
        name = rb.code_spans(cells[0])[0]
        if name not in RETRIED_COLUMN:
            continue
        make, retried = RETRIED_COLUMN[name]
        says_yes = cells[3].startswith("Yes")
        says_never = cells[3].startswith(("No", "Never"))
        assert says_yes != says_never, (name, cells[3])
        assert says_yes == retried, name
        calls = retry_outcome(make())
        assert (calls == 2) == retried, (name, calls)


def test_a_server_error_is_retried_at_most_twice_however_high_max_retries_is() -> None:
    assert "at most twice" in errors_table()[9][3]
    rig = make_rig(*[error(500, "internal_error")] * 5, max_retries=10)
    with pytest.raises(esportsodds.ServerError):
        rig.client.matches.list()
    assert len(rig.transport.requests) == 3  # the first try and two retries


def test_only_502_and_504_of_the_other_statuses_are_retried() -> None:
    apierror_row = next(r for r in errors_table() if rb.code_spans(r[0])[0] == "ApiError")
    assert apierror_row[3] == "Only 502 and 504"
    for status, retried in (
        (502, True),
        (504, True),
        (500, True),
        (418, False),
        (501, False),
        (505, False),
    ):
        rig = make_rig(reply({}, status), page([]))
        with contextlib.suppress(esportsodds.EsportsOddsError):
            rig.client.matches.list()
        assert (len(rig.transport.requests) == 2) == retried, status


def test_a_network_failure_is_retried_and_a_garbled_reply_is_not() -> None:
    by_name = {rb.code_spans(r[0])[0]: r for r in errors_table()}
    assert by_name["TransportError"][3].startswith("Yes")
    assert by_name["InvalidResponseError"][3] == "No"
    assert retry_outcome(TransportError("connection refused")) == 2
    assert retry_outcome(TransportResponse(200, {}, b"<html>not json</html>")) == 1


def test_quota_exceeded_has_a_reset_time() -> None:
    rig = make_rig(
        error(429, "quota_exceeded", retry_after="86400", x_quota_reset="2026-11-01T00:00:00Z")
    )
    with pytest.raises(esportsodds.QuotaExceededError) as caught:
        rig.client.matches.list()
    assert str(caught.value.reset_at) == "2026-11-01 00:00:00+00:00"


def test_the_error_attributes_listed_exist() -> None:
    body = FLAT["Errors"]
    rig = make_rig(error(404, "not_found", request_id="r-1"))
    with pytest.raises(esportsodds.NotFoundError) as caught:
        rig.client.matches.get(ID_1)
    for attribute in ("status", "code", "message", "request_id", "retry_after", "headers", "body"):
        assert f"`{attribute}`" in body
        assert hasattr(caught.value, attribute), attribute
    assert caught.value.request_id == "r-1"
    assert rig.client.last_response is not None
    assert rig.client.last_response.status == 404


def test_the_error_handling_example_runs_for_each_branch() -> None:
    code = python_block("Errors", "except QuotaExceededError")
    quota = make_rig(
        error(429, "quota_exceeded", retry_after="86400", x_quota_reset="2026-11-01T00:00:00Z")
    )
    out, _ = run(code, {"client": quota.client})
    assert out.startswith("out of requests until 2026-11-01")
    limited = make_rig(error(429, "rate_limited", retry_after="2"), max_retries=0)
    out, _ = run(code, {"client": limited.client})
    assert out == "slow down; retry in 2.0 seconds\n"
    fine = make_rig(item({"id": ID_1}))
    out, _ = run(code, {"client": fine.client})
    assert out == ""
    assert fine.transport.path(0) == f"/v1/cs2/matches/{ID_1}"


def test_the_errors_text_claims_hold() -> None:
    body = FLAT["Errors"]
    assert "An unknown path id is a `404`" in body
    rig = make_rig(error(404, "not_found"))
    with pytest.raises(esportsodds.NotFoundError):
        rig.client.matches.get(ID_2)
    empty = make_rig(page([]))
    assert empty.client.matches.maps(ID_2).data == []


# --- rate limits and retries ----------------------------------------------------------------------


def test_the_rate_limit_example_reads_the_budget() -> None:
    rig = make_rig(
        page(
            [],
            x_quota_limit="50000",
            x_quota_remaining="49000",
            x_quota_reset="2026-11-01T00:00:00Z",
        )
    )
    rig.client.matches.list()
    out, _ = run(python_block("Rate limits and retries", "last_response"), {"client": rig.client})
    assert out.strip() == "49000 of 50000 left; resets 2026-11-01 00:00:00+00:00"


def test_last_response_is_none_before_the_first_call_as_the_comment_says() -> None:
    assert "None until the first call" in SECTIONS["Rate limits and retries"]
    assert Client(api_key=FAKE_KEY, base_url=BASE_URL).last_response is None


def test_the_retry_rules_in_the_text_are_the_clients() -> None:
    body = FLAT["Rate limits and retries"]
    assert "3 by default" in body
    assert inspect.signature(Client.__init__).parameters["max_retries"].default == 3
    assert "longer than a minute is raised" in body
    assert client_module.MAX_RETRY_AFTER == 60.0
    assert "up to half a second of" in body
    assert client_module.RETRY_AFTER_JITTER == 0.5
    assert "from half a second, doubling, up to eight seconds" in body
    assert (client_module.BACKOFF_BASE, client_module.BACKOFF_CAP) == (0.5, 8.0)
    assert "at most twice" in body
    assert client_module.SERVER_ERROR_RETRIES == 2


def test_retries_are_logged_at_info_on_the_esportsodds_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert "`INFO` on the `esportsodds` logger" in SECTIONS["Rate limits and retries"]
    rig = make_rig(error(503, "unavailable", retry_after="1"), page([]))
    with caplog.at_level("INFO", logger="esportsodds"):
        rig.client.matches.list()
    records = [r for r in caplog.records if r.name == "esportsodds"]
    assert len(records) == 1
    assert records[0].levelname == "INFO"
    assert FAKE_KEY not in caplog.text  # and the key is never in a log line


def test_the_budget_limits_quoted_are_the_ones_the_docs_state() -> None:
    body = FLAT["Rate limits and retries"]
    assert "20 requests a second sustained, bursts of 100" in body
    assert "50,000 requests per account" in body


# --- websocket ------------------------------------------------------------------------------------


class FakeSocket:
    """A stand-in for a `websockets` connection: records what is sent, replays scripted frames."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = [json.dumps(f) for f in frames]
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self) -> AsyncIterator[str]:
        return self._replay()

    async def _replay(self) -> AsyncIterator[str]:
        for frame in self.frames:
            yield frame


def fake_websockets(socket: FakeSocket, urls: list[str]) -> types.ModuleType:
    module = types.ModuleType("websockets")

    def connect(url: str) -> FakeSocket:
        urls.append(url)
        return socket

    module.connect = connect  # type: ignore[attr-defined]
    module.ConnectionClosed = type("ConnectionClosed", (Exception,), {})  # type: ignore[attr-defined]
    return module


def test_the_websocket_example_mints_a_ticket_connects_and_subscribes(
    wire: Callable[..., FakeTransport], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = wire(item({"token": "TICKET", "expires_at": "2026-10-07T12:01:00Z"}))
    socket = FakeSocket(
        [
            {"type": "snapshot", "match_id": ID_1, "seq": 0, "data": {}},
            {"type": "update", "match_id": ID_1, "seq": 1, "data": {}},
            {"type": "subscribed", "data": {"match_ids": [ID_1]}},
        ]
    )
    urls: list[str] = []
    monkeypatch.setitem(sys.modules, "websockets", fake_websockets(socket, urls))
    out, _ = run(python_block("WebSocket (match updates)", "websockets.connect"))
    assert transport.requests[0].method == "POST"
    assert transport.path(0) == "/v1/cs2/ws-token"
    assert urls == ["wss://api.esportsodds.gg/v1/ws?token=TICKET"]
    assert socket.sent == [{"type": "subscribe", "data": {"match_ids": [ID_1]}}]
    assert out.splitlines() == [f"{ID_1} 0 snapshot", f"{ID_1} 1 update"]


def test_the_websocket_claims_hold() -> None:
    body = FLAT["WebSocket (match updates)"]
    assert "60 seconds" in body
    assert "five connections" in body
    assert "54 seconds" in body
    assert "costs no" in body
    assert "pip install websockets" in body
    assert "wss://" not in body  # the address comes from ws_url(), never typed by the reader


# --- the rest of the document ---------------------------------------------------------------------


def test_the_asyncio_answer_runs(wire: Callable[..., FakeTransport]) -> None:
    code = python_block("FAQ", "asyncio.to_thread")
    rig = make_rig(page([row(1)]))
    wrapped = (
        "async def _faq(client):\n"
        + "".join(f"    {line}\n" for line in code.splitlines())
        + "    return matches\n"
    )
    scope: dict[str, Any] = {"asyncio": asyncio}
    exec(compile(wrapped, "<README.md>", "exec"), scope)  # noqa: S102 - the README's own snippet
    result = asyncio.run(scope["_faq"](rig.client))
    assert result.data[0]["id"] == row(1)["id"]
    assert rig.transport.query(0) == {"status": "scheduled", "limit": "10"}


def faq() -> dict[str, str]:
    """Each FAQ question (the bold text) with its answer, in order, whitespace collapsed."""
    parts = re.split(r"\*\*([^*]+\?)\*\* ", FLAT["FAQ"])
    assert parts[0].strip() == ""
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


FAQ_OPENINGS = {
    # (the name of the third party is in the README only, as the two phrases the lint allows; a
    # shipped test may not spell it, even as test data)
    "Is this an official API?": "No. It is not an official ",
    "Why not scrape?": "Scraping breaks whenever a page changes and many sites forbid it.",
    "Is there a free tier?": (
        "No. There is no free tier and no trial; the plan is $49 a month for 50,000 requests, "
        "paid up front."
    ),
    "What does a call cost?": "One request against the 50,000, whatever it returns.",
    "Does it work with asyncio?": "The client is synchronous and safe to share between threads.",
    "Can I use another HTTP library?": (
        "Yes. Pass `transport=` any object with a `send(TransportRequest) -> TransportResponse` "
        "method."
    ),
    "Which games are supported?": (
        'CS2 (`game="cs2"`). The `game` argument exists for later titles.'
    ),
    "Is it typed?": "Yes: the package ships `py.typed` and passes `mypy --strict`.",
    "Where do I report a bug in this client?": "In this repository's issues.",
}


def test_the_faq_asks_exactly_the_questions_these_tests_know_the_answers_to() -> None:
    assert list(faq()) == list(FAQ_OPENINGS)


@pytest.mark.parametrize(("question", "opening"), list(FAQ_OPENINGS.items()))
def test_each_faq_answer_opens_with_the_answer_the_code_supports(
    question: str, opening: str
) -> None:
    assert faq()[question].startswith(opening), faq()[question]


def test_the_faq_games_answer_is_what_the_client_does() -> None:
    rig = make_rig(page([row(1)]))
    assert rig.client.game == "cs2"  # the default is the only game with data
    rig.client.matches.list()
    assert rig.transport.requests[0].url.split("?")[0].endswith("/v1/cs2/matches")
    Client(api_key=FAKE_KEY, game="cs2").close()


def test_the_faq_transport_answer_is_the_protocol_the_client_calls() -> None:
    rig = make_rig(page([row(1)]))
    assert rig.client.matches.list().data[0]["id"] == row(1)["id"]
    assert len(rig.transport.requests) == 1  # a plain object with send() was enough
    assert hasattr(rig.transport, "send")


def test_the_faq_says_a_bug_report_goes_to_this_repositorys_issues() -> None:
    answer = faq()["Where do I report a bug in this client?"]
    assert "[esportsodds.gg](https://esportsodds.gg/contact)" in answer
    assert "account, billing or the data" in answer


def test_the_faq_free_tier_answer_points_at_the_public_demo() -> None:
    assert "[public demo](https://demo.esportsodds.gg)" in faq()["Is there a free tier?"]


def test_the_faq_answers_what_a_call_costs_correctly() -> None:
    body = FLAT["FAQ"]
    rig = make_rig(page([row(1)], "c1"), page([row(2)], None))
    list(rig.client.matches.iter())
    assert len(rig.transport.requests) == 2  # one request per page, as the answer says
    assert "one request per page" in body


def test_the_examples_table_lists_exactly_the_scripts_in_the_folder() -> None:
    (table,) = rb.tables(SECTIONS["Examples"])
    pattern = re.compile(rf"\({re.escape(REPO_URL)}/blob/main/examples/([a-z_]+\.py)\)")
    listed = []
    for cells in table:
        found = pattern.search(cells[0])
        assert found, cells[0]
        listed.append(found.group(1))
    listed.sort()
    present = sorted(p.name for p in (rb.ROOT / "examples").glob("*.py"))
    assert listed == present
    assert len(listed) == 7


def test_the_licence_section_matches_the_licence_file() -> None:
    licence = (rb.ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert "Copyright (c) 2026 Sellframe Ltd" in licence
    body = FLAT["Licence"]
    assert f"[MIT licence]({REPO_URL}/blob/main/LICENSE)" in body
    assert "sections 4 and 5" in body
    assert "trademarks of Valve Corporation" in body


def test_there_is_no_relative_link_because_pypi_does_not_resolve_them() -> None:
    # The same file is the PyPI project page, where `examples/quickstart.py` would resolve against
    # pypi.org and 404. In-page anchors are fine (PyPI gives each heading an id).
    for target in re.findall(r"\]\(([^)\s]+)\)", TEXT):
        assert target.startswith(("https://", "http://", "#")), target


def test_every_link_into_this_repository_names_a_path_in_the_package_folder() -> None:
    found = 0
    for kind, path in re.findall(rf"{re.escape(REPO_URL)}/(blob|tree)/main/([^)\s#]+)", TEXT):
        found += 1
        target = rb.ROOT / path
        assert target.is_file() if kind == "blob" else target.is_dir(), (kind, path)
    assert found >= 9  # the licence, the examples folder and the seven scripts


def test_every_absolute_link_is_to_one_of_our_own_sites_or_the_two_named_projects() -> None:
    allowed = (
        "https://esportsodds.gg/",
        "https://api.esportsodds.gg",
        "https://demo.esportsodds.gg",
        "https://docs.esportsodds.gg/",
        "https://github.com/esportsodds/esportsodds-python",
        "https://websockets.readthedocs.io/",
        "http://proxy.example:3128",  # the made-up proxy in the transport answer
    )
    for url in re.findall(r"\]\((https?://[^)\s]+)\)", TEXT):
        assert url.startswith(allowed), url
    for url in re.findall(r"(?<!\()https?://[^\s)`]+", TEXT):
        assert url.startswith(allowed), url


def public_text() -> dict[str, str]:
    """Every file whose words reach a reader: the README and changelog, the code, the examples."""
    paths = [rb.ROOT / "README.md", rb.ROOT / "CHANGELOG.md", rb.ROOT / "pyproject.toml"]
    for folder in ("src", "examples"):
        paths.extend(sorted((rb.ROOT / folder).rglob("*.py")))
    return {p.relative_to(rb.ROOT).as_posix(): p.read_text(encoding="utf-8") for p in paths}


def test_every_environment_variable_the_text_names_is_one_the_client_reads() -> None:
    reads = {client_module.ENV_API_KEY, client_module.ENV_BASE_URL}
    assert reads == {"ESPORTSODDS_API_KEY", "ESPORTSODDS_BASE_URL"}
    named: dict[str, set[str]] = {}
    for name, text in public_text().items():
        for variable in re.findall(r"\bESPORTSODDS_[A-Z0-9_]+\b", text):
            named.setdefault(variable, set()).add(name)
    assert {"ESPORTSODDS_API_KEY", "ESPORTSODDS_BASE_URL"} <= named.keys()  # the scan found them
    assert set(named) - reads == set(), {v: sorted(f) for v, f in named.items() if v not in reads}


def test_the_install_command_installs_the_package_this_project_builds() -> None:
    pyproject = (rb.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^name = "([^"]+)"$', pyproject, re.M)
    assert match, "pyproject.toml has no project name"
    name = match.group(1)
    install = [b for b in BLOCKS if b.section == "Install"]
    assert [(b.lang, b.code.strip()) for b in install] == [("bash", f"pip install {name}")]
    # every pip command anywhere in the README installs this package, the one named dependency of
    # the WebSocket example, or this repository itself (the pre-release note)
    allowed = {name, "websockets", "git+https://github.com/esportsodds/esportsodds-python"}
    commands = re.findall(r"pip install ([A-Za-z0-9_.:/+@-]+)", TEXT)
    assert name in commands
    assert set(commands) <= allowed, sorted(set(commands) - allowed)


def test_the_package_urls_in_pyproject_are_the_ones_the_readme_uses() -> None:
    pyproject = (rb.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'Repository = "https://github.com/esportsodds/esportsodds-python"' in pyproject
    assert "https://github.com/esportsodds/esportsodds-python" in TEXT
    assert 'Homepage = "https://esportsodds.gg/cs2-api"' in pyproject
