"""The MCP server: `esportsodds-mcp` speaks the Model Context Protocol over stdio.

It serves both protocol eras: clients that open with an `initialize` handshake (2025-11-25 and
earlier) and clients that put the protocol version in every request's `_meta` (2026-07-28). The
tools are built from the resource methods and `ROUTES`, so these tests hold each tool to both.
No test here touches the network: the client underneath is wired to `FakeTransport`.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from esportsodds import Client, __version__, mcp
from esportsodds._routes import ROUTES

from .conftest import error, item, make_rig, page

MATCH = "00000000-0000-7000-8000-000000000001"
TEAM = "00000000-0000-7000-8000-000000000002"
REQUEST_ID = "00000000-0000-7000-8000-00000000abcd"
MODERN = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientCapabilities": {},
}


def request(method: str, params: dict[str, Any] | None = None, id: Any = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call(name: str, arguments: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"name": name, **extra}
    if arguments is not None:
        params["arguments"] = arguments
    return request("tools/call", params)


def server_with(*script: Any) -> tuple[mcp.Server, Any]:
    rig = make_rig(*script)
    return mcp.Server(client_factory=lambda: rig.client), rig.transport


def result_of(response: Any) -> dict[str, Any]:
    assert isinstance(response, dict), response
    assert "error" not in response, response
    result: dict[str, Any] = response["result"]
    return result


def text_of(response: Any) -> str:
    content = result_of(response)["content"]
    assert [c["type"] for c in content] == ["text"]
    text: str = content[0]["text"]
    return text


def tools_by_name() -> dict[str, dict[str, Any]]:
    server = mcp.Server(client_factory=Client)
    listed = result_of(server.handle(request("tools/list")))["tools"]
    return {tool["name"]: tool for tool in listed}


# --- the handshake era (initialize) ---------------------------------------------------------------


@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"])
def test_initialize_answers_with_the_version_the_client_asked_for(version: str) -> None:
    server = mcp.Server(client_factory=Client)
    result = result_of(
        server.handle(
            request(
                "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {}}
            )
        )
    )
    assert result["protocolVersion"] == version
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "esportsodds"
    assert result["serverInfo"]["version"] == __version__
    assert result["instructions"]


def test_initialize_with_a_version_it_does_not_know_answers_with_the_newest_handshake_one() -> None:
    server = mcp.Server(client_factory=Client)
    result = result_of(server.handle(request("initialize", {"protocolVersion": "1999-01-01"})))
    assert result["protocolVersion"] == "2025-11-25"


def test_handshake_era_results_carry_no_modern_fields() -> None:
    server = mcp.Server(client_factory=Client)
    for method in ("initialize", "tools/list", "ping"):
        result = result_of(server.handle(request(method, {"protocolVersion": "2025-06-18"})))
        assert "resultType" not in result, method
        assert "_meta" not in result, method


@pytest.mark.parametrize(
    "method", ["notifications/initialized", "notifications/cancelled", "notifications/whatever"]
)
def test_a_notification_gets_no_reply(method: str) -> None:
    server = mcp.Server(client_factory=Client)
    assert server.handle({"jsonrpc": "2.0", "method": method, "params": {"requestId": 1}}) is None


def test_ping_answers_an_empty_result_with_the_same_id() -> None:
    server = mcp.Server(client_factory=Client)
    assert server.handle(request("ping", id="p-1")) == {"jsonrpc": "2.0", "id": "p-1", "result": {}}


def test_an_unknown_method_is_method_not_found() -> None:
    server = mcp.Server(client_factory=Client)
    response = server.handle(request("resources/list", id=7))
    assert response["id"] == 7
    assert response["error"]["code"] == -32601


@pytest.mark.parametrize(
    ("message", "echoed_id"),
    [
        (5, None),
        ({"jsonrpc": "2.0", "id": 3}, 3),
        ({"jsonrpc": "1.0", "id": "a", "method": "ping"}, "a"),
        ({"jsonrpc": "2.0", "id": 4, "method": 12}, 4),
        ({"jsonrpc": "2.0", "id": {"no": "objects"}, "method": "ping"}, None),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, None),
        ({"jsonrpc": "2.0", "id": 5, "method": "ping", "params": [1]}, 5),
    ],
)
def test_a_malformed_request_is_invalid_request(message: Any, echoed_id: Any) -> None:
    response = mcp.Server(client_factory=Client).handle(message)
    assert response["error"]["code"] == -32600
    assert response["id"] == echoed_id


def test_a_stray_response_gets_no_reply() -> None:
    server = mcp.Server(client_factory=Client)
    assert server.handle({"jsonrpc": "2.0", "id": 3, "result": {}}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 4, "error": {"code": 1, "message": "x"}}) is None


def test_a_line_that_is_not_json_is_a_parse_error_with_a_null_id() -> None:
    out = mcp.Server(client_factory=Client).handle_line(b"{not json")
    assert out is not None
    response = json.loads(out)
    assert response == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": response["error"]["message"]},
    }


def test_a_batch_answers_each_request_and_skips_notifications() -> None:
    server = mcp.Server(client_factory=Client)
    replies = server.handle(
        [
            request("ping", id=1),
            {"jsonrpc": "2.0", "method": "notifications/x"},
            request("ping", id=2),
        ]
    )
    assert [r["id"] for r in replies] == [1, 2]
    assert server.handle([{"jsonrpc": "2.0", "method": "notifications/x"}]) is None
    assert server.handle([])["error"]["code"] == -32600


# --- the modern era (per-request _meta) -----------------------------------------------------------


def test_discover_names_every_supported_version_and_the_server() -> None:
    result = result_of(
        mcp.Server(client_factory=Client).handle(request("server/discover", {"_meta": META}))
    )
    assert result["resultType"] == "complete"
    assert result["supportedVersions"][0] == MODERN
    assert set(result["supportedVersions"]) == {
        MODERN,
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    }
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "esportsodds"
    assert result["instructions"]


def test_a_modern_request_with_an_unknown_version_gets_the_unsupported_version_error() -> None:
    meta = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    response = mcp.Server(client_factory=Client).handle(request("tools/list", {"_meta": meta}))
    assert response["error"]["code"] == -32022
    assert response["error"]["data"]["requested"] == "1900-01-01"
    assert MODERN in response["error"]["data"]["supported"]


def test_a_modern_request_without_client_capabilities_is_invalid_params() -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": MODERN}
    response = mcp.Server(client_factory=Client).handle(request("tools/list", {"_meta": meta}))
    assert response["error"]["code"] == -32602


def test_modern_results_carry_the_result_type_and_the_server_identity() -> None:
    server, _ = server_with(item({"id": MATCH}))
    for message in (
        request("tools/list", {"_meta": META}),
        call("matches_get", {"match": MATCH}, _meta=META),
    ):
        result = result_of(server.handle(message))
        assert result["resultType"] == "complete"
        assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == __version__


# --- the tool list --------------------------------------------------------------------------------


def test_the_tools_are_the_curated_routes_in_a_fixed_order() -> None:
    names = list(tools_by_name())
    assert names == [key.replace(".", "_") for key, _title in mcp.TOOLS]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[a-z0-9_]{1,64}", n) for n in names)
    assert all(key in ROUTES for key, _title in mcp.TOOLS)


def test_no_tool_mints_tickets_pulls_the_bulk_export_or_probes_health() -> None:
    assert {"matches_bulk", "ws_token", "health"}.isdisjoint(tools_by_name())


def test_every_tool_is_read_only_and_titled() -> None:
    for name, tool in tools_by_name().items():
        assert tool["title"], name
        assert tool["annotations"]["readOnlyHint"] is True, name
        assert tool["annotations"]["openWorldHint"] is True, name


def test_each_tool_takes_its_methods_id_and_exactly_its_routes_query_parameters() -> None:
    for key, _title in mcp.TOOLS:
        tool = tools_by_name()[key.replace(".", "_")]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        route = ROUTES[key]
        id_params = [p for p in schema.get("required", []) if p not in route.params]
        assert len(id_params) == (1 if route.takes_id else 0), key
        assert set(schema["properties"]) == set(route.params) | set(id_params), key


def test_a_keyword_the_method_requires_is_required_too() -> None:
    schema = tools_by_name()["teams_h2h"]["inputSchema"]
    assert schema["required"] == ["team", "opponent"]
    server, transport = server_with()
    response = server.handle(call("teams_h2h", {"team": TEAM}))
    assert result_of(response)["isError"] is True
    assert "opponent" in text_of(response)
    assert transport.requests == []


def test_both_ids_of_a_head_to_head_are_sent() -> None:
    server, transport = server_with(item({}))
    server.handle(call("teams_h2h", {"team": TEAM, "opponent": "team-b"}))
    assert transport.path() == f"/v1/cs2/teams/{TEAM}/h2h"
    assert transport.query() == {"opponent": "team-b"}


def test_every_parameter_has_a_json_type_and_a_description() -> None:
    for name, tool in tools_by_name().items():
        for param, spec in tool["inputSchema"]["properties"].items():
            assert spec["type"] in ("string", "integer", "boolean"), (name, param)
            assert spec["description"].strip(), (name, param)
            assert "\n" not in spec["description"], (name, param)


def test_descriptions_come_from_the_docstrings_on_one_line_without_rst_quotes() -> None:
    tools = tools_by_name()
    assert tools["matches_maps"]["description"] == "Per-map scores for one match."
    status = tools["matches_list"]["inputSchema"]["properties"]["status"]["description"]
    assert "`scheduled`" in status
    for tool in tools.values():
        assert "``" not in tool["description"]
        assert "\n" not in tool["description"]


def test_the_match_list_tells_an_assistant_how_to_ask_for_upcoming_matches() -> None:
    # An assistant asked "what is on next?" reads these descriptions and nothing else. The default
    # order is newest first, so `status="scheduled"` alone returns the fixtures furthest away.
    tool = tools_by_name()["matches_list"]
    props = tool["inputSchema"]["properties"]
    assert "furthest away" in tool["description"]
    assert 'sort="scheduled_at"' in tool["description"]
    assert "soonest first" in props["sort"]["description"]
    assert 'sort="scheduled_at"' in props["status"]["description"]


def test_a_description_keeps_the_docstring_lines_that_wrap() -> None:
    has = tools_by_name()["matches_list"]["inputSchema"]["properties"]["has"]["description"]
    assert has.startswith("Only matches that carry all of these sub-resources, for example")
    assert '`["rounds", "depth"]`' in has
    assert has.endswith("Several values: comma-separated.")


def test_an_id_parameter_is_a_required_string_and_says_whether_a_slug_works() -> None:
    tools = tools_by_name()
    match = tools["matches_stats"]["inputSchema"]
    assert match["required"] == ["match"]
    assert match["properties"]["match"]["type"] == "string"
    assert "slug" not in match["properties"]["match"]["description"].replace("no slug", "")
    team = tools["teams_form"]["inputSchema"]["properties"]["team"]["description"]
    assert "slug" in team


def test_the_list_tools_say_they_return_twenty_rows_unless_asked() -> None:
    tools = tools_by_name()
    for key, _title in mcp.TOOLS:
        tool = tools[key.replace(".", "_")]
        if ROUTES[key].paginated:
            assert f"{mcp.DEFAULT_LIST_LIMIT} rows" in tool["description"], key
            assert "cursor" in tool["description"], key


def test_the_tool_list_is_the_same_on_every_call_and_needs_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ESPORTSODDS_API_KEY", raising=False)
    server = mcp.Server()
    first = server.handle(request("tools/list"))
    assert first == server.handle(request("tools/list"))
    assert len(result_of(first)["tools"]) == len(mcp.TOOLS)


# --- calling a tool -------------------------------------------------------------------------------


def test_a_list_tool_asks_for_twenty_rows_and_returns_the_rows_and_the_cursor() -> None:
    server, transport = server_with(page([{"id": MATCH}], next_cursor="c2"))
    response = server.handle(call("matches_list", {"status": "completed"}))
    assert result_of(response)["isError"] is False
    assert transport.path() == "/v1/cs2/matches"
    assert transport.query() == {"status": "completed", "limit": "20"}
    assert json.loads(text_of(response)) == {"data": [{"id": MATCH}], "next_cursor": "c2"}


def test_a_limit_the_model_gives_is_sent_as_given() -> None:
    server, transport = server_with(page([]))
    server.handle(call("teams_list", {"limit": 5}))
    assert transport.query()["limit"] == "5"


def test_a_list_tool_with_no_arguments_object_still_works() -> None:
    server, transport = server_with(page([]))
    result_of(server.handle(call("tournaments_list")))
    assert transport.query() == {"limit": "20"}


def test_a_route_with_its_own_small_default_is_left_to_it() -> None:
    server, transport = server_with(page([]))
    server.handle(call("teams_form", {"team": TEAM}))
    assert "limit" not in transport.query()


def test_an_item_tool_returns_the_object_and_fills_in_the_id() -> None:
    server, transport = server_with(item({"id": MATCH, "team_a_name": "Team A"}))
    response = server.handle(call("matches_get", {"match": MATCH, "include": ["maps", "stats"]}))
    assert transport.path() == f"/v1/cs2/matches/{MATCH}"
    assert transport.query() == {"include": "maps,stats"}
    assert json.loads(text_of(response)) == {"id": MATCH, "team_a_name": "Team A"}


@pytest.mark.parametrize(("active", "sent"), [(True, {"active": "true"}), (False, {})])
def test_a_boolean_argument_is_sent_only_when_true(active: bool, sent: dict[str, str]) -> None:
    server, transport = server_with(page([]))
    server.handle(call("teams_maps", {"team": TEAM, "active": active}))
    assert transport.query() == sent


def test_non_ascii_text_comes_back_intact() -> None:
    server, _ = server_with(item({"name": "Équipe Ünïcode"}))
    assert json.loads(text_of(server.handle(call("teams_get", {"team": TEAM})))) == {
        "name": "Équipe Ünïcode"
    }


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"match": MATCH, "colour": "red"}, "takes no argument colour. It takes: match, include."),
        ({}, "needs `match`"),
        ({"match": None}, "needs `match`"),
        ({"match": 12}, "`match` must be a string, not 12"),
        ({"match": [MATCH]}, "`match` must be a string"),
        ({"match": MATCH, "include": 3}, "`include` must be a string, not 3"),
        ({"match": MATCH, "include": ["maps", 3]}, "`include` must be a string"),
        ({"match": " "}, "`match` must not be empty"),
    ],
)
def test_bad_arguments_are_a_tool_error_and_send_nothing(
    arguments: dict[str, Any], says: str
) -> None:
    server, transport = server_with()
    response = server.handle(call("matches_get", arguments))
    assert result_of(response)["isError"] is True
    assert says in text_of(response)
    assert transport.requests == []


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"limit": "20"}, '`limit` must be an integer, not "20"'),
        ({"limit": True}, "`limit` must be an integer, not true"),
        ({"limit": 2.5}, "`limit` must be an integer, not 2.5"),
    ],
)
def test_an_integer_argument_must_be_an_integer(arguments: dict[str, Any], says: str) -> None:
    server, transport = server_with()
    response = server.handle(call("teams_list", arguments))
    assert result_of(response)["isError"] is True
    assert says in text_of(response)
    assert transport.requests == []


def test_a_boolean_argument_must_be_a_boolean() -> None:
    server, transport = server_with()
    response = server.handle(call("teams_maps", {"team": TEAM, "active": "false"}))
    assert result_of(response)["isError"] is True
    assert "`active` must be true or false" in text_of(response)
    assert transport.requests == []


def test_an_unknown_tool_is_a_protocol_error() -> None:
    server, transport = server_with()
    response = server.handle(call("matches_delete", {}))
    assert response["error"]["code"] == -32602
    assert "matches_delete" in response["error"]["message"]
    assert transport.requests == []


@pytest.mark.parametrize("params", [{"arguments": {}}, {"name": "teams_list", "arguments": [1]}])
def test_a_call_that_breaks_the_request_shape_is_invalid_params(params: dict[str, Any]) -> None:
    server, transport = server_with()
    assert server.handle(request("tools/call", params))["error"]["code"] == -32602
    assert transport.requests == []


def test_an_api_error_is_a_tool_error_with_its_code_and_request_id() -> None:
    server, _ = server_with(
        error(403, "sandbox_scope", "outside the sample", request_id=REQUEST_ID)
    )
    response = server.handle(call("teams_get", {"team": TEAM}))
    assert result_of(response)["isError"] is True
    text = text_of(response)
    assert "403 sandbox_scope: outside the sample" in text
    assert REQUEST_ID in text


def test_a_rejected_key_says_where_the_key_is_set() -> None:
    server, _ = server_with(error(401, "unauthorized", "invalid API key"))
    text = text_of(server.handle(call("teams_get", {"team": TEAM})))
    assert "ESPORTSODDS_API_KEY" in text


def test_with_no_key_a_call_is_a_tool_error_that_says_how_to_set_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ESPORTSODDS_API_KEY", raising=False)
    response = mcp.Server().handle(call("teams_list", {}))
    assert result_of(response)["isError"] is True
    assert "set ESPORTSODDS_API_KEY in this server's environment" in text_of(response)
    assert "MCP client's configuration" in text_of(response)


def test_an_optional_argument_given_as_null_is_left_out() -> None:
    server, transport = server_with(page([]))
    server.handle(call("matches_list", {"status": None, "team": TEAM}))
    assert transport.query() == {"team": TEAM, "limit": "20"}


def test_a_route_that_is_not_paginated_returns_rows_without_a_cursor() -> None:
    server, _ = server_with(page([{"map": "Map A"}], next_cursor=None))
    assert json.loads(text_of(server.handle(call("maps_list", {})))) == {"data": [{"map": "Map A"}]}


def test_the_client_is_built_once_and_reused() -> None:
    rig = make_rig(page([]), page([]))
    built: list[Client] = []

    def factory() -> Client:
        built.append(rig.client)
        return rig.client

    server = mcp.Server(client_factory=factory)
    server.handle(call("teams_list", {}))
    server.handle(call("teams_list", {}))
    assert len(built) == 1


def test_an_unexpected_failure_is_a_tool_error_not_a_crash() -> None:
    def broken() -> Client:
        raise RuntimeError("disk on fire")

    response = mcp.Server(client_factory=broken).handle(call("teams_list", {}))
    assert result_of(response)["isError"] is True
    assert "disk on fire" in text_of(response)


def test_the_key_never_appears_in_anything_the_server_writes() -> None:
    key = "eo_live_" + "5a" * 24  # distinctive, so a leak of any part of it is visible
    rig = make_rig(page([]), error(401, "unauthorized", "invalid API key"), item({}), api_key=key)
    server = mcp.Server(client_factory=lambda: rig.client)
    written = [
        server.handle_line(json.dumps(message).encode())
        for message in (
            request("initialize", {"protocolVersion": "2025-06-18"}),
            request("tools/list"),
            call("teams_list", {}),
            call("teams_get", {"team": TEAM}),
            call("teams_get", {"team": TEAM}),
            request("server/discover", {"_meta": META}),
        )
    ]
    everything = b"".join(line or b"" for line in written).decode()
    assert len(written) == 6
    assert key not in everything
    assert key[-8:] not in everything


# --- stdio ----------------------------------------------------------------------------------------


def run_lines(server: mcp.Server, *lines: bytes) -> list[dict[str, Any]]:
    out = io.BytesIO()
    mcp.serve(io.BytesIO(b"".join(lines)), out, server=server)
    written = out.getvalue()
    assert written.endswith(b"\n") or written == b""
    return [json.loads(line) for line in written.decode("utf-8").split("\n") if line]


def test_serve_answers_one_line_per_request_in_order_and_stops_at_end_of_input() -> None:
    server, _ = server_with(page([{"name": "Team A"}]))
    replies = run_lines(
        server,
        json.dumps(request("initialize", {"protocolVersion": "2025-06-18"}, id=0)).encode() + b"\n",
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
        json.dumps(request("tools/list", id=1)).encode() + b"\n",
        json.dumps(call("teams_list", {}) | {"id": 2}).encode() + b"\n",
    )
    assert [r["id"] for r in replies] == [0, 1, 2]
    assert json.loads(replies[2]["result"]["content"][0]["text"])["data"] == [{"name": "Team A"}]


def test_serve_skips_blank_lines_and_accepts_windows_line_endings() -> None:
    server = mcp.Server(client_factory=Client)
    replies = run_lines(server, b"\r\n", b"\n", b'{"jsonrpc":"2.0","id":9,"method":"ping"}\r\n')
    assert replies == [{"jsonrpc": "2.0", "id": 9, "result": {}}]


def test_serve_writes_each_message_on_exactly_one_line() -> None:
    server, _ = server_with(item({"bio": "line one\nline two"}))
    out = io.BytesIO()
    message = json.dumps(call("teams_get", {"team": TEAM})).encode() + b"\n"
    mcp.serve(io.BytesIO(message), out, server=server)
    assert out.getvalue().count(b"\n") == 1


def test_serve_keeps_going_after_a_line_it_cannot_parse() -> None:
    ping = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    replies = run_lines(mcp.Server(client_factory=Client), b"nonsense\n", ping)
    assert [r.get("error", {}).get("code") for r in replies] == [-32700, None]


def test_serve_sends_non_ascii_text_as_valid_json() -> None:
    server, _ = server_with(item({"name": "Équipe Ünïcode"}))
    line = json.dumps(call("teams_get", {"team": TEAM})).encode() + b"\n"
    (reply,) = run_lines(server, line)
    assert json.loads(reply["result"]["content"][0]["text"]) == {"name": "Équipe Ünïcode"}


def test_a_real_process_answers_each_line_before_the_next_arrives() -> None:
    # A client waits for each reply before it writes again: an unflushed reply hangs it for good.
    env = {k: v for k, v in os.environ.items() if k != "ESPORTSODDS_API_KEY"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "esportsodds.mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    try:
        for n in (1, 2):
            proc.stdin.write(json.dumps(request("ping", id=n)).encode() + b"\n")
            proc.stdin.flush()
            assert json.loads(proc.stdout.readline())["id"] == n  # the deadline fails a hang
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
        proc.stdout.close()


def test_main_prints_the_version() -> None:
    out = io.StringIO()
    assert mcp.main(["--version"], stdout=out) == 0
    assert out.getvalue().strip() == f"esportsodds-mcp {__version__}"


def test_the_console_scripts_are_declared() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    section = pyproject.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    assert 'esportsodds-mcp = "esportsodds.mcp:main"' in section
    # `uvx esportsodds mcp`: how a registry starts a server whose package names no such command.
    assert 'esportsodds = "esportsodds.mcp:cli"' in section


def test_the_esportsodds_command_prints_its_version() -> None:
    out = io.StringIO()
    assert mcp.cli(["--version"], stdout=out) == 0
    assert out.getvalue().strip() == f"esportsodds {__version__}"


@pytest.mark.parametrize("argv", [[], ["serve"], ["mcp", "extra"]])
def test_the_esportsodds_command_takes_only_mcp(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert mcp.cli(argv) == 2
    assert "esportsodds mcp" in capsys.readouterr().err


def test_esportsodds_mcp_serves_in_a_real_process() -> None:
    env = {k: v for k, v in os.environ.items() if k != "ESPORTSODDS_API_KEY"}
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from esportsodds.mcp import cli; sys.exit(cli(['mcp']))",
        ],
        input=json.dumps(request("ping", id=4)).encode() + b"\n",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"jsonrpc": "2.0", "id": 4, "result": {}}


def test_a_real_process_speaks_only_protocol_on_stdout_and_exits_at_end_of_input() -> None:
    env = {k: v for k, v in os.environ.items() if k != "ESPORTSODDS_API_KEY"}
    lines = [
        request("initialize", {"protocolVersion": "2025-06-18"}, id=0),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request("tools/list", id=1),
        call("teams_list", {}) | {"id": 2},
    ]
    done = subprocess.run(
        [sys.executable, "-W", "error", "-m", "esportsodds.mcp"],
        input="".join(json.dumps(line) + "\n" for line in lines).encode(),
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    replies = [json.loads(line) for line in done.stdout.decode().splitlines()]
    assert [r["id"] for r in replies] == [0, 1, 2]
    assert replies[2]["result"]["isError"] is True  # no key in the environment
