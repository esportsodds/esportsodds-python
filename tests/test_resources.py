"""Every method calls the route it names, with the arguments it was given.

One row per route. The contract tests (tests/contract) check the same table against the OpenAPI
spec; this file checks that each Python method is wired to the right row.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from esportsodds import Client, Page
from esportsodds._routes import ROUTES

from .conftest import item, make_rig, page, reply

MATCH = "00000000-0000-7000-8000-000000000001"
TEAM = "00000000-0000-7000-8000-000000000002"
OPPONENT = "00000000-0000-7000-8000-000000000003"
TOURNAMENT = "00000000-0000-7000-8000-000000000004"
PLAYER = "00000000-0000-7000-8000-000000000005"


@dataclass(frozen=True)
class Case:
    key: str
    call: Callable[[Client], Any]
    method: str
    path: str
    query: dict[str, str]
    returns: str  # "page", "item" or "raw"


CASES = [
    # matches
    Case(
        "matches.list",
        lambda c: c.matches.list(status="completed", limit=5),
        "GET",
        "/v1/cs2/matches",
        {"status": "completed", "limit": "5"},
        "page",
    ),
    Case(
        "matches.bulk",
        lambda c: c.matches.bulk(include=["maps"]),
        "GET",
        "/v1/cs2/matches/bulk",
        {"include": "maps"},
        "page",
    ),
    Case(
        "matches.get",
        lambda c: c.matches.get(MATCH, include=["teams", "odds"]),
        "GET",
        f"/v1/cs2/matches/{MATCH}",
        {"include": "teams,odds"},
        "item",
    ),
    Case(
        "matches.maps",
        lambda c: c.matches.maps(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/maps",
        {},
        "page",
    ),
    Case(
        "matches.stats",
        lambda c: c.matches.stats(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/stats",
        {},
        "page",
    ),
    Case(
        "matches.vetoes",
        lambda c: c.matches.vetoes(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/vetoes",
        {},
        "page",
    ),
    Case(
        "matches.rounds",
        lambda c: c.matches.rounds(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/rounds",
        {},
        "page",
    ),
    Case(
        "matches.history",
        lambda c: c.matches.history(MATCH, limit=20, before="2026-09-22T00:00:00Z"),
        "GET",
        f"/v1/cs2/matches/{MATCH}/history",
        {"limit": "20", "before": "2026-09-22T00:00:00Z"},
        "page",
    ),
    Case(
        "matches.depth",
        lambda c: c.matches.depth(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/depth",
        {},
        "item",
    ),
    Case(
        "matches.lineups",
        lambda c: c.matches.lineups(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/lineups",
        {},
        "page",
    ),
    Case(
        "matches.streams",
        lambda c: c.matches.streams(MATCH),
        "GET",
        f"/v1/cs2/matches/{MATCH}/streams",
        {},
        "page",
    ),
    # odds
    Case(
        "odds.list",
        lambda c: c.odds.list(
            match=MATCH, source="eo_market", latest=True, market="match_winner", limit=10
        ),
        "GET",
        "/v1/cs2/odds",
        {
            "match": MATCH,
            "source": "eo_market",
            "latest": "1",
            "market": "match_winner",
            "limit": "10",
        },
        "page",
    ),
    # websocket ticket
    Case("ws_token", lambda c: c.ws_token(), "POST", "/v1/cs2/ws-token", {}, "item"),
    # teams
    Case(
        "teams.list",
        lambda c: c.teams.list(slug="team-a", region="Europe", sort="-name", limit=3),
        "GET",
        "/v1/cs2/teams",
        {"slug": "team-a", "region": "Europe", "sort": "-name", "limit": "3"},
        "page",
    ),
    Case(
        "teams.get",
        lambda c: c.teams.get(TEAM, include="rank", rank=True),
        "GET",
        f"/v1/cs2/teams/{TEAM}",
        {"include": "rank", "rank": "1"},
        "item",
    ),
    Case(
        "teams.h2h",
        lambda c: c.teams.h2h(TEAM, opponent=OPPONENT),
        "GET",
        f"/v1/cs2/teams/{TEAM}/h2h",
        {"opponent": OPPONENT},
        "item",
    ),
    Case(
        "teams.form",
        lambda c: c.teams.form(TEAM, limit=5),
        "GET",
        f"/v1/cs2/teams/{TEAM}/form",
        {"limit": "5"},
        "page",
    ),
    Case(
        "teams.ratings",
        lambda c: c.teams.ratings(TEAM, limit=5, window="12m"),
        "GET",
        f"/v1/cs2/teams/{TEAM}/ratings",
        {"limit": "5", "window": "12m"},
        "page",
    ),
    Case(
        "teams.maps",
        lambda c: c.teams.maps(TEAM, window="6m", era="cs2", active=True, include=["detail"]),
        "GET",
        f"/v1/cs2/teams/{TEAM}/maps",
        {"window": "6m", "era": "cs2", "active": "true", "include": "detail"},
        "page",
    ),
    Case(
        "teams.round_stats",
        lambda c: c.teams.round_stats(TEAM, window="6m"),
        "GET",
        f"/v1/cs2/teams/{TEAM}/round-stats",
        {"window": "6m"},
        "item",
    ),
    Case(
        "teams.vetoes",
        lambda c: c.teams.vetoes(TEAM, window="all", era="cs2"),
        "GET",
        f"/v1/cs2/teams/{TEAM}/vetoes",
        {"window": "all", "era": "cs2"},
        "item",
    ),
    Case(
        "teams.results",
        lambda c: c.teams.results(TEAM, limit=10, opponent=OPPONENT),
        "GET",
        f"/v1/cs2/teams/{TEAM}/results",
        {"limit": "10", "opponent": OPPONENT},
        "page",
    ),
    Case(
        "teams.rivals",
        lambda c: c.teams.rivals(TEAM, limit=3, min_meetings=4, window="36m"),
        "GET",
        f"/v1/cs2/teams/{TEAM}/rivals",
        {"limit": "3", "min_meetings": "4", "window": "36m"},
        "page",
    ),
    Case(
        "teams.roster_history",
        lambda c: c.teams.roster_history(TEAM, limit=5),
        "GET",
        f"/v1/cs2/teams/{TEAM}/roster-history",
        {"limit": "5"},
        "page",
    ),
    Case(
        "teams.players",
        lambda c: c.teams.players(TEAM),
        "GET",
        f"/v1/cs2/teams/{TEAM}/players",
        {},
        "item",
    ),
    # tournaments
    Case(
        "tournaments.list",
        lambda c: c.tournaments.list(
            slug="event-a",
            status="finished",
            tier="S",
            event_type="lan",
            region="Europe",
            year=2026,
            limit=4,
        ),
        "GET",
        "/v1/cs2/tournaments",
        {
            "slug": "event-a",
            "status": "finished",
            "tier": "S",
            "event_type": "lan",
            "region": "Europe",
            "year": "2026",
            "limit": "4",
        },
        "page",
    ),
    Case(
        "tournaments.get",
        lambda c: c.tournaments.get(TOURNAMENT),
        "GET",
        f"/v1/cs2/tournaments/{TOURNAMENT}",
        {},
        "item",
    ),
    Case(
        "tournaments.teams",
        lambda c: c.tournaments.teams(TOURNAMENT),
        "GET",
        f"/v1/cs2/tournaments/{TOURNAMENT}/teams",
        {},
        "page",
    ),
    # players
    Case(
        "players.list",
        lambda c: c.players.list(
            slug="player-a", team=TEAM, role="AWP", ids=[PLAYER, MATCH], sort="nickname", limit=2
        ),
        "GET",
        "/v1/cs2/players",
        {
            "slug": "player-a",
            "team": TEAM,
            "role": "AWP",
            "ids": f"{PLAYER},{MATCH}",
            "sort": "nickname",
            "limit": "2",
        },
        "page",
    ),
    Case(
        "players.get",
        lambda c: c.players.get(PLAYER, rank=True),
        "GET",
        f"/v1/cs2/players/{PLAYER}",
        {"rank": "1"},
        "item",
    ),
    Case(
        "players.career",
        lambda c: c.players.career(PLAYER),
        "GET",
        f"/v1/cs2/players/{PLAYER}/stats",
        {},
        "item",
    ),
    Case(
        "players.performance",
        lambda c: c.players.performance(PLAYER),
        "GET",
        f"/v1/cs2/players/{PLAYER}/performance",
        {},
        "item",
    ),
    Case(
        "players.maps",
        lambda c: c.players.maps(PLAYER),
        "GET",
        f"/v1/cs2/players/{PLAYER}/maps",
        {},
        "item",
    ),
    Case(
        "players.matches",
        lambda c: c.players.matches(PLAYER, limit=5),
        "GET",
        f"/v1/cs2/players/{PLAYER}/matches",
        {"limit": "5"},
        "page",
    ),
    Case(
        "players.teams",
        lambda c: c.players.teams(PLAYER),
        "GET",
        f"/v1/cs2/players/{PLAYER}/teams",
        {},
        "item",
    ),
    Case(
        "players.weapons",
        lambda c: c.players.weapons(PLAYER),
        "GET",
        f"/v1/cs2/players/{PLAYER}/weapons",
        {},
        "item",
    ),
    # leaderboard and meta
    Case(
        "rankings.list",
        lambda c: c.rankings.list(
            type="team",
            region="Europe",
            role="AWP",
            sort="-value",
            delta_days=14,
            include=["trend"],
            limit=10,
        ),
        "GET",
        "/v1/cs2/rankings",
        {
            "type": "team",
            "region": "Europe",
            "role": "AWP",
            "sort": "-value",
            "delta_days": "14",
            "include": "trend",
            "limit": "10",
        },
        "page",
    ),
    Case(
        "rankings.history",
        lambda c: c.rankings.history(weeks=13, top=10),
        "GET",
        "/v1/cs2/rankings/history",
        {"weeks": "13", "top": "10"},
        "page",
    ),
    Case(
        "rankings.round_stats",
        lambda c: c.rankings.round_stats(window="6m", limit=30),
        "GET",
        "/v1/cs2/rankings/round-stats",
        {"window": "6m", "limit": "30"},
        "page",
    ),
    # maps
    Case(
        "maps.list",
        lambda c: c.maps.list(window="6m", era="cs2"),
        "GET",
        "/v1/cs2/maps",
        {"window": "6m", "era": "cs2"},
        "page",
    ),
    Case(
        "maps.teams",
        lambda c: c.maps.teams("dust2", window="12m", era="all", limit=50),
        "GET",
        "/v1/cs2/maps/dust2/teams",
        {"window": "12m", "era": "all", "limit": "50"},
        "page",
    ),
    Case("coverage.list", lambda c: c.coverage.list(), "GET", "/v1/cs2/coverage", {}, "page"),
    Case("model.metrics", lambda c: c.model.metrics(), "GET", "/v1/cs2/model/metrics", {}, "page"),
    # the probe
    Case("health", lambda c: c.health(), "GET", "/health", {}, "raw"),
]


def scripted(returns: str) -> Any:
    if returns == "page":
        return page([{"id": "x"}], None)
    if returns == "item":
        return item({"id": "x"})
    return reply({"status": "ok"})


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_each_method_calls_its_route(case: Case) -> None:
    rig = make_rig(scripted(case.returns))
    result = case.call(rig.client)
    assert len(rig.transport.requests) == 1
    request = rig.transport.requests[0]
    assert request.method == case.method
    assert rig.transport.path() == case.path
    assert rig.transport.query() == case.query
    if case.returns == "page":
        assert isinstance(result, Page)
        assert result.data == [{"id": "x"}]
    elif case.returns == "item":
        assert result == {"id": "x"}
    else:
        assert result == {"status": "ok"}


def test_every_route_in_the_table_has_a_case() -> None:
    assert {c.key for c in CASES} == set(ROUTES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_the_cases_agree_with_the_route_table(case: Case) -> None:
    # The cases are written by hand from the spec; the table is what the client really uses. If the
    # two ever disagree, one of them is wrong, and this says which route.
    route = ROUTES[case.key]
    assert route.method == case.method
    assert route.kind == case.returns
    template = re.escape(route.path).replace(re.escape("{game}"), "cs2")
    template = template.replace(re.escape("{id}"), "[^/]+")
    assert re.fullmatch(template, case.path), (route.path, case.path)
    assert set(case.query) <= set(route.params), set(case.query) - set(route.params)


# One sample per query parameter name: the Python value to pass and the text that must go on the
# wire. The forwarding test below drives every method with ALL of its parameters at once.
SAMPLES: dict[str, tuple[Any, str]] = {
    "status": ("completed", "completed"),
    "tournament": (UUID(TOURNAMENT), TOURNAMENT),
    "team": (UUID(TEAM), TEAM),
    "date_from": (date(2026, 9, 1), "2026-09-01"),
    "date_to": ("2026-09-30", "2026-09-30"),
    "has": (["rounds", "depth"], "rounds,depth"),
    "changed_since": ("2026-09-18T03:49:12Z", "2026-09-18T03:49:12Z"),
    "sort": ("changed_at", "changed_at"),
    "limit": (7, "7"),
    "cursor": ("opaque-cursor", "opaque-cursor"),
    "include": (["maps", "stats"], "maps,stats"),
    "before": ("2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    "match": (UUID(MATCH), MATCH),
    "source": ("eo_market", "eo_market"),
    "latest": (True, "1"),
    "market": ("match_winner", "match_winner"),
    "slug": ("some-slug", "some-slug"),
    "region": ("Europe", "Europe"),
    "rank": (True, "1"),
    "opponent": ("other-team", "other-team"),
    "tier": ("S", "S"),
    "event_type": ("lan", "lan"),
    "year": (2026, "2026"),
    "role": ("AWP", "AWP"),
    "ids": ([PLAYER, MATCH], f"{PLAYER},{MATCH}"),
    "type": ("player", "player"),
    "window": ("6m", "6m"),
    "era": ("cs2", "cs2"),
    "active": (True, "true"),  # spelled out, unlike `rank` and `latest`
    "delta_days": (14, "14"),
    "weeks": (13, "13"),
    "top": (10, "10"),
    "min_meetings": (4, "4"),
}


def _method_for(client: Client, key: str) -> Callable[..., Any]:
    target: Any = client
    for part in key.split("."):
        target = getattr(target, part)
    assert callable(target), key
    return target  # type: ignore[no-any-return]


@pytest.mark.parametrize("key", sorted(ROUTES))
def test_every_declared_parameter_is_accepted_and_reaches_the_wire(key: str) -> None:
    # Catches a method that accepts a parameter and then forgets to pass it on: the quiet kind of
    # bug where a filter is ignored and the caller gets unfiltered data.
    route = ROUTES[key]
    missing = [name for name in route.params if name not in SAMPLES]
    assert not missing, f"add a sample for {missing}"
    rig = make_rig(scripted(route.kind))
    method = _method_for(rig.client, key)
    kwargs = {name: SAMPLES[name][0] for name in route.params}
    args = ["some-id"] if "{id}" in route.path else []
    method(*args, **kwargs)
    assert rig.transport.query() == {name: SAMPLES[name][1] for name in route.params}


@pytest.mark.parametrize("key", sorted(ROUTES))
def test_a_method_takes_exactly_the_documented_keyword_parameters(key: str) -> None:
    route = ROUTES[key]
    client = Client(api_key="k" * 8, base_url="https://x.invalid")
    sig = inspect.signature(_method_for(client, key))
    keyword_only = {
        name for name, p in sig.parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert keyword_only == set(route.params)
    positional = [
        name
        for name, p in sig.parameters.items()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert len(positional) == (1 if "{id}" in route.path else 0)
    # Everything is optional except the one parameter the API itself requires.
    required = {
        name
        for name, p in sig.parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    }
    assert required == ({"opponent"} if key == "teams.h2h" else set())


def test_the_namespaces_exist() -> None:
    client = Client(api_key="eo_live_" + "0" * 48, base_url="https://api.test.invalid")
    for name in (
        "matches",
        "odds",
        "teams",
        "tournaments",
        "players",
        "rankings",
        "maps",
        "coverage",
        "model",
    ):
        assert hasattr(client, name), name


def test_the_game_is_part_of_every_path() -> None:
    rig = make_rig(page([]), game="dota2")
    rig.client.matches.list()
    assert rig.transport.path() == "/v1/dota2/matches"


def test_ids_may_be_uuid_objects() -> None:
    rig = make_rig(item({"id": "x"}))
    rig.client.matches.get(UUID(MATCH))
    assert rig.transport.path() == f"/v1/cs2/matches/{MATCH}"


def test_h2h_requires_an_opponent() -> None:
    sig = inspect.signature(Client(api_key="k" * 8, base_url="https://x.invalid").teams.h2h)
    assert sig.parameters["opponent"].default is inspect.Parameter.empty
    assert sig.parameters["opponent"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_model_namespace_says_the_model_line_is_coming_soon_and_claims_nothing() -> None:
    # The model line is not released: the client says so and claims nothing about it.
    model = Client(api_key="k" * 8, base_url="https://x.invalid").model
    docs = [inspect.getdoc(type(model)), inspect.getdoc(type(model).metrics)]
    assert all(docs)
    text = " ".join(d for d in docs if d).lower()
    assert "coming soon" in text
    for claim in ("accurate", "accuracy of", "beats", "outperform", "edge", "profit", "predict"):
        assert claim not in text, claim


# Every walking helper: the namespace, the method and the list route it walks.
WALKERS = [
    ("matches", "pages", "matches.list"),
    ("matches", "iter", "matches.list"),
    ("matches", "pages_bulk", "matches.bulk"),
    ("matches", "iter_bulk", "matches.bulk"),
    ("odds", "pages", "odds.list"),
    ("odds", "iter", "odds.list"),
    ("teams", "pages", "teams.list"),
    ("teams", "iter", "teams.list"),
    ("tournaments", "pages", "tournaments.list"),
    ("tournaments", "iter", "tournaments.list"),
    ("players", "pages", "players.list"),
    ("players", "iter", "players.list"),
]


@pytest.mark.parametrize(("namespace", "method", "key"), WALKERS)
def test_a_walker_forwards_every_parameter_and_honours_max_pages(
    namespace: str, method: str, key: str
) -> None:
    # `list` is checked parameter by parameter above; this is the same for the helpers built on it,
    # which each repeat the parameter list by hand. A filter dropped here would quietly return
    # unfiltered data from page two onward.
    route = ROUTES[key]
    rig = make_rig(page([{"id": "a"}], "c1"), page([{"id": "b"}], None))
    kwargs = {name: SAMPLES[name][0] for name in route.params}
    walker = getattr(getattr(rig.client, namespace), method)
    got = list(walker(max_pages=1, **kwargs))
    assert len(got) == 1  # one page for pages*, one row for iter*
    assert len(rig.transport.requests) == 1  # the second scripted page was never asked for
    assert rig.transport.query(0) == {name: SAMPLES[name][1] for name in route.params}


@pytest.mark.parametrize(("namespace", "method", "key"), WALKERS)
def test_a_walker_takes_the_routes_parameters_plus_max_pages(
    namespace: str, method: str, key: str
) -> None:
    client = Client(api_key="k" * 8, base_url="https://x.invalid")
    sig = inspect.signature(getattr(getattr(client, namespace), method))
    keyword_only = {
        name for name, p in sig.parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert keyword_only == set(ROUTES[key].params) | {"max_pages"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())
    assert all(p.default is not inspect.Parameter.empty for p in sig.parameters.values())


def test_no_docstring_gives_a_match_a_slug() -> None:
    # Teams, players and tournaments answer to a slug as well as a UUID; matches do not ("Matches
    # have no slug, so a match id must be a UUID", says the API reference). A docstring that says
    # otherwise sends users to a 400.
    client = Client(api_key="k" * 8, base_url="https://x.invalid")
    checked = 0
    for method_name in dir(client.matches):
        method = getattr(client.matches, method_name)
        if method_name.startswith("_") or not callable(method):
            continue
        doc = inspect.getdoc(method) or ""
        for line in doc.splitlines():
            if re.match(r"\s*match:", line):
                checked += 1
                assert "slug" not in line.lower(), (method_name, line)
    assert checked >= 1, "no `match:` argument was documented, so this test checked nothing"


def test_a_team_maps_flag_is_spelled_out_not_sent_as_a_one() -> None:
    # `rank` and `latest` are "1" on the wire; `active` is "true" or "false" and a "1" is a 400.
    rig = make_rig(page([]))
    rig.client.teams.maps(TEAM, active=True)
    assert rig.transport.query() == {"active": "true"}
    rig = make_rig(page([]))
    rig.client.teams.maps(TEAM, active=False)  # off is simply left out
    assert rig.transport.query() == {}


def test_results_resolves_an_opponent_like_any_other_team_reference() -> None:
    rig = make_rig(page([]))
    rig.client.teams.results(TEAM, opponent="team-b")
    assert rig.transport.query() == {"opponent": "team-b"}
    with pytest.raises(ValueError, match="opponent"):
        rig.client.teams.results(TEAM, opponent=" ")


def test_a_map_name_is_checked_before_it_reaches_the_url() -> None:
    rig = make_rig(page([]))
    for bad in ("", "  ", ".", ".."):
        with pytest.raises(ValueError, match="map name"):
            rig.client.maps.teams(bad)
    assert rig.transport.requests == []
    rig.client.maps.teams("de_mirage")
    assert rig.transport.path() == "/v1/cs2/maps/de_mirage/teams"
