"""The route table: the one place that says which operations exist and what each one takes.

Everything is derived from `ROUTES`: the client builds URLs and query strings from it, the tests
check every method against it, and the maintainers compare it with the API's OpenAPI document and
its router on every change. If an operation or a query parameter is added, removed or renamed
there, a check fails until this table (and the matching method) catches up.

Order matters in one place: `Route.params` is the order query parameters are sent in, which is the
order the spec lists them, so the URLs a client builds are stable and readable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

__all__ = ["CSV_PARAMS", "ROUTES", "Kind", "Route"]

# What a successful reply looks like (the rule the contract tests apply to the spec too):
#   "page" - `{"data": [...], "meta": {...}}`; `meta.next_cursor` says whether another page exists.
#   "item" - `{"data": {...}}`, one object.
#   "raw"  - a bare body with no `data` envelope (only `/health`).
Kind = Literal["page", "item", "raw"]

# Query parameters that take several values, sent comma-separated. A method accepts a string
# (passed through) or any iterable of strings/UUIDs for these, and for no other parameter.
CSV_PARAMS = frozenset({"has", "include", "ids"})


@dataclass(frozen=True)
class Route:
    """One API operation.

    Attributes:
        key: The SDK's name for it, ``<namespace>.<method>`` (or a bare client method name).
        operation_id: The `operationId` in the OpenAPI document.
        method: ``GET`` or ``POST``.
        path: The path template; ``{game}`` and ``{id}`` are filled in by the client.
        params: Query parameters, in the order they are sent.
        kind: The shape of a successful reply.
        page_max: The largest page size the endpoint serves (None when the route is not
            paginated). Walking helpers ask for this by default: every request counts against
            the monthly allowance, so fewer, bigger pages are cheaper.
        min_timeout: A floor, in seconds, on the request timeout. The bulk export builds large
            pages and can take longer than an ordinary call.
        authenticated: False only for `/health`, which takes no key.
        retry: False only for `/health`, a probe whose job is to report one honest answer.
    """

    key: str
    operation_id: str
    method: str
    path: str
    params: tuple[str, ...]
    kind: Kind
    page_max: int | None = None
    min_timeout: float = 0.0
    authenticated: bool = True
    retry: bool = True

    @property
    def paginated(self) -> bool:
        """True when the route takes a `cursor` and so can be walked page by page."""
        return "cursor" in self.params

    @property
    def takes_id(self) -> bool:
        """True when the path has an ``{id}`` segment (a UUID or a slug)."""
        return "{id}" in self.path


_G = "/v1/{game}"

# One route per row reads better than the formatter's one-argument-per-line, hence fmt: off.
# fmt: off
_TABLE: tuple[Route, ...] = (
    Route(
        "matches.list", "listMatches", "GET", f"{_G}/matches",
        (
            "status", "tournament", "team", "date_from", "date_to", "has", "changed_since", "sort",
            "limit", "cursor",
        ),
        "page", page_max=500,
    ),
    Route(
        "matches.bulk", "listMatchesBulk", "GET", f"{_G}/matches/bulk",
        (
            "include", "status", "tournament", "team", "date_from", "date_to", "has",
            "changed_since", "sort", "limit", "cursor",
        ),
        "page", page_max=25, min_timeout=120.0,
    ),
    Route("matches.get", "getMatch", "GET", f"{_G}/matches/{{id}}", ("include",), "item"),
    Route("matches.maps", "listMatchMaps", "GET", f"{_G}/matches/{{id}}/maps", (), "page"),
    Route("matches.stats", "listMatchStats", "GET", f"{_G}/matches/{{id}}/stats", (), "page"),
    Route("matches.vetoes", "listMatchVetoes", "GET", f"{_G}/matches/{{id}}/vetoes", (), "page"),
    Route("matches.rounds", "listMatchRounds", "GET", f"{_G}/matches/{{id}}/rounds", (), "page"),
    Route(
        "matches.history", "listMatchHistory", "GET", f"{_G}/matches/{{id}}/history",
        ("limit", "before"), "page",
    ),
    Route("matches.depth", "getMatchDepth", "GET", f"{_G}/matches/{{id}}/depth", (), "item"),
    Route("matches.lineups", "listMatchLineups", "GET", f"{_G}/matches/{{id}}/lineups", (), "page"),
    Route("matches.streams", "listMatchStreams", "GET", f"{_G}/matches/{{id}}/streams", (), "page"),
    Route(
        "odds.list", "listOdds", "GET", f"{_G}/odds",
        ("match", "source", "latest", "market", "limit", "cursor"), "page", page_max=1000,
    ),
    Route("ws_token", "mintWsToken", "POST", f"{_G}/ws-token", (), "item"),
    Route(
        "teams.list", "listTeams", "GET", f"{_G}/teams",
        ("slug", "q", "region", "sort", "limit", "cursor"), "page", page_max=500,
    ),
    Route("teams.get", "getTeam", "GET", f"{_G}/teams/{{id}}", ("include", "rank"), "item"),
    Route("teams.h2h", "getHeadToHead", "GET", f"{_G}/teams/{{id}}/h2h", ("opponent",), "item"),
    Route("teams.form", "listTeamForm", "GET", f"{_G}/teams/{{id}}/form", ("limit",), "page"),
    Route(
        "teams.ratings", "listTeamRatings", "GET", f"{_G}/teams/{{id}}/ratings",
        ("limit", "window"), "page",
    ),
    Route(
        "teams.maps", "listTeamMaps", "GET", f"{_G}/teams/{{id}}/maps",
        ("window", "era", "active", "include"), "page",
    ),
    Route(
        "teams.round_stats", "getTeamRoundStats", "GET", f"{_G}/teams/{{id}}/round-stats",
        ("window",), "item",
    ),
    Route(
        "teams.vetoes", "getTeamVetoes", "GET", f"{_G}/teams/{{id}}/vetoes",
        ("window", "era"), "item",
    ),
    Route(
        "teams.results", "listTeamResults", "GET", f"{_G}/teams/{{id}}/results",
        ("limit", "opponent"), "page",
    ),
    Route(
        "teams.rivals", "listTeamRivals", "GET", f"{_G}/teams/{{id}}/rivals",
        ("limit", "min_meetings", "window"), "page",
    ),
    Route(
        "teams.roster_history", "listTeamRosterHistory", "GET", f"{_G}/teams/{{id}}/roster-history",
        ("limit",), "page",
    ),
    Route("teams.players", "listTeamPlayers", "GET", f"{_G}/teams/{{id}}/players", (), "item"),
    Route(
        "tournaments.list", "listTournaments", "GET", f"{_G}/tournaments",
        ("slug", "q", "status", "tier", "event_type", "region", "year", "limit", "cursor"),
        "page", page_max=500,
    ),
    Route("tournaments.get", "getTournament", "GET", f"{_G}/tournaments/{{id}}", (), "item"),
    Route(
        "tournaments.teams", "listTournamentTeams", "GET", f"{_G}/tournaments/{{id}}/teams",
        (), "page",
    ),
    Route(
        "players.list", "listPlayers", "GET", f"{_G}/players",
        ("slug", "q", "team", "role", "ids", "sort", "limit", "cursor"), "page", page_max=500,
    ),
    Route("players.get", "getPlayer", "GET", f"{_G}/players/{{id}}", ("include", "rank"), "item"),
    Route("players.career", "getPlayerCareer", "GET", f"{_G}/players/{{id}}/stats", (), "item"),
    Route(
        "players.performance", "getPlayerPerformance", "GET", f"{_G}/players/{{id}}/performance",
        (), "item",
    ),
    Route("players.maps", "listPlayerMaps", "GET", f"{_G}/players/{{id}}/maps", (), "item"),
    Route(
        "players.matches", "listPlayerMatches", "GET", f"{_G}/players/{{id}}/matches",
        ("limit",), "page",
    ),
    Route("players.teams", "listPlayerTeams", "GET", f"{_G}/players/{{id}}/teams", (), "item"),
    Route(
        "players.weapons", "getPlayerWeapons", "GET", f"{_G}/players/{{id}}/weapons", (), "item",
    ),
    Route(
        "rankings.list", "listRankings", "GET", f"{_G}/rankings",
        ("type", "region", "role", "sort", "delta_days", "include", "limit"), "page",
    ),
    Route(
        "rankings.history", "listRankingsHistory", "GET", f"{_G}/rankings/history",
        ("weeks", "top"), "page",
    ),
    Route(
        "rankings.round_stats", "listRankingsRoundStats", "GET", f"{_G}/rankings/round-stats",
        ("window", "limit"), "page",
    ),
    Route("maps.list", "listMaps", "GET", f"{_G}/maps", ("window", "era"), "page"),
    Route(
        "maps.teams", "listMapTeams", "GET", f"{_G}/maps/{{id}}/teams",
        ("window", "era", "limit"), "page",
    ),
    Route("coverage.list", "getCoverage", "GET", f"{_G}/coverage", (), "page"),
    Route("model.metrics", "getModelMetrics", "GET", f"{_G}/model/metrics", (), "page"),
    Route("health", "health", "GET", "/health", (), "raw", authenticated=False, retry=False),
)
# fmt: on

if len({r.key for r in _TABLE}) != len(_TABLE):  # pragma: no cover - a typo in the table above
    raise RuntimeError("duplicate route key in _routes._TABLE")

ROUTES: Mapping[str, Route] = MappingProxyType({r.key: r for r in _TABLE})
