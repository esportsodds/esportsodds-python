"""An MCP (Model Context Protocol) server, so an AI assistant can read the API as tools.

Start ``esportsodds-mcp`` (or ``python -m esportsodds.mcp``) from an MCP client's configuration,
with ESPORTSODDS_API_KEY in its environment. It speaks newline-delimited JSON-RPC 2.0 on stdin
and stdout and serves both protocol eras: clients that open with an ``initialize`` handshake
(revision 2025-11-25 and earlier) and clients that send the protocol version in every request's
``_meta`` (2026-07-28). Standard library only, like the rest of the package.

The tools are the read routes listed in `TOOLS`. Each is built from its resource method: the
signature gives the input schema and the docstring the descriptions, so a tool cannot drift from
the client it calls. Only protocol messages go to stdout, and the key is never written anywhere.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

from . import _resources
from ._client import ENV_API_KEY, Client
from ._errors import AuthenticationError, EsportsOddsError
from ._pagination import Page
from ._routes import ROUTES
from ._version import __version__

__all__ = ["DEFAULT_LIST_LIMIT", "SUPPORTED_VERSIONS", "TOOLS", "Server", "cli", "main", "serve"]

# Protocol revisions, newest first. A request that carries a modern version in `_meta` is served
# statelessly; an `initialize` handshake selects the handshake era for the rest of the process.
MODERN_VERSIONS = ("2026-07-28",)
HANDSHAKE_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = MODERN_VERSIONS + HANDSHAKE_VERSIONS

# Rows a list tool asks for when the assistant names no `limit`. The API's own default (100) fills
# a model's context with rows it rarely needs; asking for more costs nothing extra in requests.
DEFAULT_LIST_LIMIT = 20

# The tools, in the order `tools/list` returns them: (route key, title). Read routes only. Left out
# on purpose: the bulk export (pages too large for a conversation), the WebSocket ticket, `/health`,
# and the narrower routes, because clients cap how many tools they will load across all servers.
TOOLS: tuple[tuple[str, str], ...] = (
    ("matches.list", "List matches"),
    ("matches.get", "Get a match"),
    ("matches.maps", "Match map scores"),
    ("matches.stats", "Match player stats"),
    ("matches.vetoes", "Match vetoes"),
    ("matches.rounds", "Match rounds"),
    ("matches.lineups", "Match lineups"),
    ("odds.list", "Market line"),
    ("teams.list", "List teams"),
    ("teams.get", "Get a team"),
    ("teams.h2h", "Head to head"),
    ("teams.form", "Team form"),
    ("teams.maps", "Team map record"),
    ("teams.results", "Team results"),
    ("teams.players", "Team players"),
    ("tournaments.list", "List tournaments"),
    ("tournaments.get", "Get a tournament"),
    ("tournaments.teams", "Tournament teams"),
    ("players.list", "List players"),
    ("players.get", "Get a player"),
    ("players.career", "Player career stats"),
    ("players.matches", "Player matches"),
    ("rankings.list", "Rankings"),
    ("maps.list", "Map figures"),
)

INSTRUCTIONS = (
    "esportsodds.gg CS2 (Counter-Strike 2) match data: matches with per-map scores, vetoes, rounds "
    "and player stats; teams, players, tournaments, rankings and league-wide map figures; and a "
    "de-vigged market line. Ids are UUIDs. Teams, players and tournaments also take a slug, and a "
    "list tool finds an id from a name (for example `teams_list` with `slug`). List tools return "
    f"{DEFAULT_LIST_LIMIT} rows unless you set `limit`. Every tool call is one API request against "
    "the key's allowance, so prefer one well-filtered call to many."
)

SERVER_INFO = {"name": "esportsodds", "title": "esportsodds.gg", "version": __version__}
CAPABILITIES = {"tools": {"listChanged": False}}

USAGE = (
    "usage: esportsodds-mcp [--version]\n\n"
    "An MCP server for the esportsodds.gg API. Start it from an MCP client's configuration with\n"
    f"{ENV_API_KEY} in its environment; it speaks MCP on stdin and stdout.\n"
)

CLI_USAGE = (
    "usage: esportsodds mcp | esportsodds --version\n\n"
    "`esportsodds mcp` starts the MCP server (the same as `esportsodds-mcp`).\n"
)

# JSON-RPC error codes, and the one MCP defines that this server sends.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNSUPPORTED_PROTOCOL_VERSION = -32022

_META_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_META_SERVER = "io.modelcontextprotocol/serverInfo"

_NAMESPACES: Mapping[str, type] = {
    "matches": _resources.Matches,
    "odds": _resources.Odds,
    "teams": _resources.Teams,
    "tournaments": _resources.Tournaments,
    "players": _resources.Players,
    "rankings": _resources.Rankings,
    "maps": _resources.Maps,
}

# The JSON type of each annotation the exposed methods use. The annotations are strings (the
# resources module defers them), read here without evaluating anything. A method that starts using
# a new one fails the tests until it is mapped.
_JSON_TYPES = {
    "IdLike": "string",
    "str": "string",
    "DateLike": "string",
    "Multi": "string",
    "Ids": "string",
    "int": "integer",
    "bool": "boolean",
}
_SEVERAL = frozenset({"Multi", "Ids"})  # a comma-separated string, or a list of strings

# Help for parameters a docstring leaves undescribed.
_ID_HELP = {
    "match": "The match id (a UUID). Matches have no slug: find one with `matches_list`.",
    "team": "The team's id (a UUID) or its slug.",
    "player": "The player's id (a UUID) or its slug.",
    "tournament": "The tournament's id (a UUID) or its slug.",
}
_PARAM_HELP = {"limit": "How many rows to return."}

_TYPE_WORDS = {"string": "a string", "integer": "an integer", "boolean": "true or false"}


@dataclass(frozen=True)
class _Tool:
    name: str
    namespace: str
    method: str
    id_param: str | None
    required: tuple[str, ...]
    types: Mapping[str, str]
    several: frozenset[str]
    paginated: bool
    definition: Mapping[str, Any]

    def problem(self, arguments: Mapping[str, Any]) -> str | None:
        """Why these arguments cannot be sent, or None when they can."""
        unknown = sorted(set(arguments) - set(self.types))
        if unknown:
            return (
                f"{self.name} takes no argument {', '.join(unknown)}. "
                f"It takes: {', '.join(self.types)}."
            )
        for param in self.required:
            if arguments.get(param) is None:
                return f"{self.name} needs `{param}`."
        for param, value in arguments.items():
            if value is None:
                continue
            expected = self.types[param]
            if not _is_type(value, expected, several=param in self.several):
                shown = json.dumps(value)[:40]
                return f"`{param}` must be {_TYPE_WORDS[expected]}, not {shown}."
            if param in self.required and not value.strip():
                return f"`{param}` must not be empty."
        return None

    def invoke(self, client: Client, arguments: Mapping[str, Any]) -> Any:
        kwargs = {k: v for k, v in arguments.items() if v is not None}
        if self.paginated:
            kwargs.setdefault("limit", DEFAULT_LIST_LIMIT)
        method = getattr(getattr(client, self.namespace), self.method)
        ident = (kwargs.pop(self.id_param),) if self.id_param else ()
        value = method(*ident, **kwargs)
        if isinstance(value, Page):
            rows: dict[str, Any] = {"data": value.data}
            if self.paginated:
                rows["next_cursor"] = value.next_cursor
            return rows
        return value


def _is_type(value: Any, expected: str, *, several: bool) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(value, str):
        return True
    return several and isinstance(value, list) and all(isinstance(v, str) for v in value)


def _one_line(text: str) -> str:
    """A docstring fragment as one line, with reStructuredText's double backticks made single."""
    return " ".join(text.split()).replace("``", "`")


def _read_docstring(func: Callable[..., Any]) -> tuple[str, dict[str, str]]:
    """The text before ``Args:``, and each argument's description from the ``Args:`` block."""
    doc = inspect.getdoc(func) or ""
    summary, _, rest = doc.partition("\nArgs:\n")
    described: dict[str, str] = {}
    current: str | None = None
    for line in rest.splitlines():
        entry = re.match(r" {4}(\w+): (.*)", line)
        if entry:
            current = entry.group(1)
            described[current] = entry.group(2)
        elif current and line.startswith(" " * 8):
            described[current] += " " + line.strip()
        elif line.strip():
            current = None  # another section, or prose after the arguments
    return _one_line(summary), {k: _one_line(v) for k, v in described.items()}


def _build_tool(key: str, title: str) -> _Tool:
    namespace, method = key.split(".")
    func = getattr(_NAMESPACES[namespace], method)
    route = ROUTES[key]
    summary, described = _read_docstring(func)
    properties: dict[str, Any] = {}
    types: dict[str, str] = {}
    several: set[str] = set()
    required: list[str] = []
    id_param: str | None = None
    for param in list(inspect.signature(func).parameters.values())[1:]:
        annotation = str(param.annotation).removesuffix(" | None")
        json_type = _JSON_TYPES[annotation]
        if param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            id_param = param.name  # the one positional argument is the path's {id}
            help_text = described.get(param.name) or _ID_HELP[param.name]
        else:
            help_text = described.get(param.name) or _PARAM_HELP[param.name]
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
        if annotation in _SEVERAL:
            several.add(param.name)
            help_text += " Several values: comma-separated."
        types[param.name] = json_type
        properties[param.name] = {"type": json_type, "description": help_text}
    description = summary
    if route.paginated:
        description += (
            f" Returns {DEFAULT_LIST_LIMIT} rows unless you set `limit`; when `next_cursor` is "
            "not null, pass it as `cursor` for the next page."
        )
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    name = key.replace(".", "_")
    definition = {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": schema,
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }
    return _Tool(
        name=name,
        namespace=namespace,
        method=method,
        id_param=id_param,
        required=tuple(required),
        types=types,
        several=frozenset(several),
        paginated=route.paginated,
        definition=definition,
    )


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _error(id_: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": body}


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _valid_id(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool)


class Server:
    """One MCP server: answers JSON-RPC messages, calling the API through one `Client`.

    Args:
        client_factory: Builds the client on the first tool call (the default reads
            ESPORTSODDS_API_KEY). Listing the tools needs no key.
    """

    def __init__(self, client_factory: Callable[[], Client] = Client) -> None:
        self._client_factory = client_factory
        self._client: Client | None = None
        self._tools = {tool.name: tool for tool in (_build_tool(k, t) for k, t in TOOLS)}

    def handle(self, message: Any) -> Any:
        """The reply to one decoded message (or batch), or None when nothing is owed."""
        if isinstance(message, list):
            if not message:
                return _error(None, INVALID_REQUEST, "Invalid request: an empty batch")
            replies = [r for r in (self._handle_one(m) for m in message) if r is not None]
            return replies or None
        return self._handle_one(message)

    def handle_line(self, line: bytes | str) -> bytes | None:
        """The reply to one line of stdio input, encoded for one line of output (no newline)."""
        reply: Any
        try:
            text = line.decode("utf-8") if isinstance(line, bytes) else line
            if not text.strip():
                return None
            message = json.loads(text)
        except ValueError as exc:  # also UnicodeDecodeError
            reply = _error(None, PARSE_ERROR, f"Parse error: {exc}")
        else:
            reply = self.handle(message)
        if reply is None:
            return None
        return json.dumps(reply, separators=(",", ":")).encode("ascii")

    def _handle_one(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Invalid request: not a JSON object")
        if "method" not in message and ("result" in message or "error" in message):
            return None  # a response; this server never sends requests, so nothing awaits it
        notification = "id" not in message
        id_ = message.get("id")
        if not notification and not _valid_id(id_):
            return _error(None, INVALID_REQUEST, "Invalid request: id must be a string or integer")
        method = message.get("method")
        params = message.get("params", {})
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return None if notification else _error(id_, INVALID_REQUEST, "Invalid request")
        if not isinstance(params, dict):
            return (
                None if notification else _error(id_, INVALID_REQUEST, "params must be an object")
            )
        if notification:
            return None  # notifications/initialized, notifications/cancelled and the rest
        try:
            result = self._dispatch(method, params)
        except _RpcError as exc:
            return _error(id_, exc.code, exc.message, exc.data)
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        meta = params.get("_meta")
        version = meta.get(_META_VERSION) if isinstance(meta, dict) else None
        if version is not None and version not in SUPPORTED_VERSIONS:
            raise _RpcError(
                UNSUPPORTED_PROTOCOL_VERSION,
                "Unsupported protocol version",
                {"supported": list(SUPPORTED_VERSIONS), "requested": version},
            )
        modern = version in MODERN_VERSIONS  # so `meta` is a dict
        if modern and not (
            isinstance(meta, dict) and isinstance(meta.get(_META_CAPABILITIES), dict)
        ):
            raise _RpcError(INVALID_PARAMS, f"Invalid params: _meta needs {_META_CAPABILITIES}")
        if method == "initialize":
            result = self._initialize(params)
        elif method == "server/discover":
            result = {
                "supportedVersions": list(SUPPORTED_VERSIONS),
                "capabilities": CAPABILITIES,
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [dict(tool.definition) for tool in self._tools.values()]}
        elif method == "tools/call":
            result = self._call_tool(params)
        else:
            raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        if modern or method == "server/discover":
            result = {"resultType": "complete", **result, "_meta": {_META_SERVER: SERVER_INFO}}
        return result

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in HANDSHAKE_VERSIONS else HANDSHAKE_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(name, str):
            raise _RpcError(INVALID_PARAMS, "Invalid params: tools/call needs a tool name")
        if not isinstance(arguments, dict):
            raise _RpcError(INVALID_PARAMS, "Invalid params: arguments must be an object")
        tool = self._tools.get(name)
        if tool is None:
            raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        problem = tool.problem(arguments)
        if problem:
            return _tool_error(problem)
        try:
            client = self._get_client()
            value = tool.invoke(client, arguments)
        except AuthenticationError as exc:
            return _tool_error(
                f"{exc}. Check {ENV_API_KEY} in this server's environment (the env block of "
                "the MCP client's configuration)."
            )
        except (EsportsOddsError, ValueError, TypeError) as exc:
            return _tool_error(str(exc))
        except Exception as exc:  # a tool call must never take the server down
            return _tool_error(f"{type(exc).__name__}: {exc}")
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def _get_client(self) -> Client:
        if self._client is None:
            try:
                self._client = self._client_factory()
            except ValueError as exc:
                if str(exc).startswith("no API key"):
                    raise ValueError(
                        f"No API key: set {ENV_API_KEY} in this server's environment (the env "
                        "block of the MCP client's configuration), then restart the server."
                    ) from None
                raise
        return self._client


def serve(
    stdin: IO[bytes] | None = None, stdout: IO[bytes] | None = None, *, server: Server | None = None
) -> None:
    """Answer MCP messages, one JSON-RPC message per line, until stdin ends."""
    source = stdin if stdin is not None else sys.stdin.buffer
    sink = stdout if stdout is not None else sys.stdout.buffer
    server = server if server is not None else Server()
    for line in iter(source.readline, b""):
        reply = server.handle_line(line)
        if reply is not None:
            sink.write(reply + b"\n")
            sink.flush()


def main(argv: Sequence[str] | None = None, stdout: IO[str] | None = None) -> int:
    """The ``esportsodds-mcp`` command."""
    args = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout
    if args in (["--version"], ["-V"]):
        out.write(f"esportsodds-mcp {__version__}\n")
        return 0
    if args in (["--help"], ["-h"]):
        out.write(USAGE)
        return 0
    if args:
        sys.stderr.write(USAGE)
        return 2
    protocol = sys.stdout.buffer
    sys.stdout = sys.stderr  # anything printed by accident goes to the log, never into the protocol
    if sys.stdin.isatty():
        sys.stderr.write(
            "esportsodds-mcp: waiting for MCP messages on stdin. Start it from an MCP client.\n"
        )
    with contextlib.suppress(KeyboardInterrupt, BrokenPipeError):
        serve(sys.stdin.buffer, protocol)
    return 0


def cli(argv: Sequence[str] | None = None, stdout: IO[str] | None = None) -> int:
    """The ``esportsodds`` command. Its one subcommand, ``mcp``, is how an MCP registry starts the
    server: registries run ``uvx <package> <arguments>``, and the package is ``esportsodds``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["-V"]):
        (stdout if stdout is not None else sys.stdout).write(f"esportsodds {__version__}\n")
        return 0
    if args == ["mcp"]:
        return main([])
    sys.stderr.write(CLI_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
