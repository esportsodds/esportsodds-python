# esportsodds

**The official Python client for the [esportsodds.gg](https://esportsodds.gg/cs2-api) CS2 data API.**

Counter-Strike 2 matches and results, team and player stats, tournaments, rankings and a derived
market line, returned as plain Python dicts. Standard library only: there is nothing else to install.

Get CS2 match data and stats without scraping HLTV. This package is a client for a paid API; it is
not an official HLTV/Valve API.

## Install

```bash
pip install esportsodds
```

Python 3.10 or newer. No dependencies.

## Quickstart

```python
from esportsodds import Client

client = Client()  # reads ESPORTSODDS_API_KEY from the environment

for match in client.matches.list(status="scheduled", limit=5).data:
    print(match["scheduled_at"], match["team_a_name"], "vs", match["team_b_name"])
```

```text
2026-10-08T14:00:00Z Team A vs Team B
2026-10-08T17:30:00Z Team C vs Team D
```

Every call returns what the API sent, as ordinary dicts and lists. A list call returns a `Page`
(`page.data`, `page.next_cursor`, `page.has_more`, `page.meta`); a call for one thing returns a dict.

## Your API key and the plan

There is one plan: **$49 a month for 50,000 requests**, per account, counted per calendar month
(UTC) and paid up front. There is no free tier and no trial: a key exists only for a paid account.
Past the 50,000, requests get a `429` with the code `quota_exceeded` until the month resets, and
this client raises `QuotaExceededError` without retrying (retrying cannot help).

Create a key in your account after you [subscribe](https://esportsodds.gg/pricing), then keep it
out of your code:

```bash
export ESPORTSODDS_API_KEY="eo_live_..."
```

- The client sends the key only in the `Authorization: Bearer` header, never in a URL. It shows
  only the last four characters in `repr()` and never puts the key in an error message or a log line.
- It talks HTTPS only. Plain `http://` is accepted for `localhost` and other loopback addresses
  (`127.0.0.1`, `::1`) alone, and redirects are never followed, so a key cannot be sent to a
  different host.
- Keep the key on your server. A browser or mobile app should call your backend, not the API.
- You can look at real responses before subscribing on the [public demo](https://demo.esportsodds.gg).

## Configuration

| Environment variable | Meaning |
| --- | --- |
| `ESPORTSODDS_API_KEY` | Your API key, used when `api_key` is not passed. |
| `ESPORTSODDS_BASE_URL` | The API address, used when `base_url` is not passed. |

| `Client(...)` argument | Default | Meaning |
| --- | --- | --- |
| `api_key` | `None` | Your key. When omitted, `ESPORTSODDS_API_KEY` is read; with neither, `ValueError`. |
| `base_url` | `https://api.esportsodds.gg` | The API address. HTTPS only, except `localhost` and other loopback addresses. |
| `game` | `"cs2"` | The title to query. CS2 is the only one with data today. |
| `timeout` | `30.0` | Seconds to wait for a reply. The bulk export always waits at least 120. |
| `max_retries` | `3` | Retries per call for the failures listed under [Errors](#errors). `0` turns retries off. |
| `transport` | `None` | Your own HTTP stack: any object with `send(TransportRequest) -> TransportResponse`. |

A `Client` is safe to share between threads. Use it as a context manager (`with Client() as client:`)
or call `client.close()` when you are finished: it closes a custom `transport` that has a `close()`
method (the default one keeps no connections open, so it has nothing to release).

## Methods

Every endpoint is one method. `{game}` is the client's `game`, and `{id}` accepts the UUID or, for
teams, players and tournaments, the slug.

| Call | Endpoint | Returns | What it gives you |
| --- | --- | --- | --- |
| `client.matches.list()` | `GET /v1/{game}/matches` | `Page` | Fixtures and results, newest first; filter by `status`, `tournament`, `team`, dates and `has`. |
| `client.matches.bulk()` | `GET /v1/{game}/matches/bulk` | `Page` | Matches with their maps, stats and more embedded (`include=`), 25 per request. |
| `client.matches.get(match)` | `GET /v1/{game}/matches/{id}` | `dict` | One match; `include=` embeds teams, tournament, odds or sub-resources. |
| `client.matches.maps(match)` | `GET /v1/{game}/matches/{id}/maps` | `Page` | Map-by-map results. |
| `client.matches.stats(match)` | `GET /v1/{game}/matches/{id}/stats` | `Page` | Per-player stat lines, whole match and per map. |
| `client.matches.vetoes(match)` | `GET /v1/{game}/matches/{id}/vetoes` | `Page` | The map pick and ban sequence. |
| `client.matches.rounds(match)` | `GET /v1/{game}/matches/{id}/rounds` | `Page` | Round-by-round team stats: economy, opening duels, clutches, bomb events. |
| `client.matches.history(match)` | `GET /v1/{game}/matches/{id}/history` | `Page` | What a match used to say: each change with its value before and after. |
| `client.matches.depth(match)` | `GET /v1/{game}/matches/{id}/depth` | `dict` | Per-player weapons, grenades, hit groups, duels and flashes. |
| `client.matches.lineups(match)` | `GET /v1/{game}/matches/{id}/lineups` | `Page` | Each team's listed roster before the match. |
| `client.matches.streams(match)` | `GET /v1/{game}/matches/{id}/streams` | `Page` | The channels broadcasting a match. |
| `client.odds.list()` | `GET /v1/{game}/odds` | `Page` | The derived market line, as history or as the current board. |
| `client.teams.list()` | `GET /v1/{game}/teams` | `Page` | Every team, alphabetically. |
| `client.teams.get(team)` | `GET /v1/{game}/teams/{id}` | `dict` | One team. |
| `client.teams.h2h(team, opponent=...)` | `GET /v1/{game}/teams/{id}/h2h` | `dict` | The head-to-head record between two teams. |
| `client.teams.form(team)` | `GET /v1/{game}/teams/{id}/form` | `Page` | Recent completed results, newest first. |
| `client.teams.ratings(team)` | `GET /v1/{game}/teams/{id}/ratings` | `Page` | The team's rating history, one point per match. |
| `client.teams.maps(team)` | `GET /v1/{game}/teams/{id}/maps` | `Page` | The team's record on each map. |
| `client.teams.round_stats(team)` | `GET /v1/{game}/teams/{id}/round-stats` | `dict` | Round-level playstyle rates: side win rates, pistols, opening duels. |
| `client.teams.vetoes(team)` | `GET /v1/{game}/teams/{id}/vetoes` | `dict` | A team's own picks, bans and deciders per map, with a permaban flag. |
| `client.teams.results(team)` | `GET /v1/{game}/teams/{id}/results` | `Page` | Recent results with every map's score and both teams' ratings going in. |
| `client.teams.rivals(team)` | `GET /v1/{game}/teams/{id}/rivals` | `Page` | The opponents a team meets most, with the head-to-head and rating gap. |
| `client.teams.roster_history(team)` | `GET /v1/{game}/teams/{id}/roster-history` | `Page` | The five players fielded in each recent match, with roster changes. |
| `client.teams.players(team)` | `GET /v1/{game}/teams/{id}/players` | `dict` | The players in the team's newest match with stats, with their last-12-month figures. |
| `client.tournaments.list()` | `GET /v1/{game}/tournaments` | `Page` | Every tournament, newest first. |
| `client.tournaments.get(tournament)` | `GET /v1/{game}/tournaments/{id}` | `dict` | One tournament. |
| `client.tournaments.teams(tournament)` | `GET /v1/{game}/tournaments/{id}/teams` | `Page` | The participating teams, by seed. |
| `client.players.list()` | `GET /v1/{game}/players` | `Page` | Every player, alphabetically by nickname. |
| `client.players.get(player)` | `GET /v1/{game}/players/{id}` | `dict` | One player. |
| `client.players.career(player)` | `GET /v1/{game}/players/{id}/stats` | `dict` | A player's career totals and averages. |
| `client.players.performance(player)` | `GET /v1/{game}/players/{id}/performance` | `dict` | Last-12-month figures over recorded maps, each with its percentile among players with a page. |
| `client.players.maps(player)` | `GET /v1/{game}/players/{id}/maps` | `dict` | The player's figures on each map they have a recorded map on. |
| `client.players.matches(player)` | `GET /v1/{game}/players/{id}/matches` | `Page` | Recent completed matches, newest first, with the player's own line. |
| `client.players.teams(player)` | `GET /v1/{game}/players/{id}/teams` | `dict` | The player's team history as stints. |
| `client.players.weapons(player)` | `GET /v1/{game}/players/{id}/weapons` | `dict` | Weapon and hit-group totals over the last 12 months. |
| `client.rankings.list()` | `GET /v1/{game}/rankings` | `Page` | A team or player leaderboard over a rolling three-month window. |
| `client.rankings.history()` | `GET /v1/{game}/rankings/history` | `Page` | The team board at each of the last weekly marks. |
| `client.rankings.round_stats()` | `GET /v1/{game}/rankings/round-stats` | `Page` | Round-level figures for the top teams, in board order. |
| `client.maps.list()` | `GET /v1/{game}/maps` | `Page` | League-wide figures for each competitive map. |
| `client.maps.teams(map)` | `GET /v1/{game}/maps/{id}/teams` | `Page` | How each team fares on, and chooses around, one map. |
| `client.coverage.list()` | `GET /v1/{game}/coverage` | `Page` | What data exists and how often it refreshes. |
| `client.model.metrics()` | `GET /v1/{game}/model/metrics` | `Page` | Transparency metrics for the model line, per market. The model line is coming soon; the list may be empty. |
| `client.ws_token()` | `POST /v1/{game}/ws-token` | `dict` | A 60-second ticket for the [WebSocket](#websocket-match-updates). Costs no requests. |
| `client.health()` | `GET /health` | `dict` | The service status. Needs no key and is never retried. |

Filters are keyword arguments named as in the [API reference](https://docs.esportsodds.gg/docs).
A list given for a comma-separated filter (`has=["rounds", "depth"]`, `include=["maps", "stats"]`)
is joined for you; a `datetime` or `date` is formatted for you (a naive `datetime` means UTC); an
unknown argument raises `TypeError` instead of being sent. Ids are checked and escaped before they
go into a URL.

The odds are our own derived lines (`source="eo_market"`), not any bookmaker's price. The model
line (`source="eo_model"`) is coming soon and is not returned yet.

## Pagination, the bulk export and the change feed

List calls return one page and a cursor. Walk them yourself:

```python
page = client.matches.list(status="completed", limit=100)
while True:
    for match in page.data:
        print(match["id"])
    if not page.has_more:
        break
    page = client.matches.list(status="completed", limit=100, cursor=page.next_cursor)
```

or let the client do it. `iter()` yields rows, `pages()` yields pages, and both fetch lazily, one
request per page, so stopping early costs nothing more. `max_pages` is a safety limit; the last page
yielded still carries its `next_cursor`, so a job that stops (or crashes) can resume with `cursor=`.

```python
for match in client.matches.iter(status="completed", date_from="2026-09-01"):
    print(match["id"])
```

`iter` and `pages` exist on `matches`, `odds`, `teams`, `tournaments` and `players`.

**The bulk export** is the cheap way to pull history: 25 matches per request, each with the
sub-resources you ask for embedded, and one request against your allowance per page.

```python
for match in client.matches.iter_bulk(status="completed", include=["maps", "stats"]):
    print(match["id"], len(match["maps"]))
```

A busy moment answers `503` with a short `Retry-After`; the client waits and retries for you.

**The change feed** keeps a local copy current. Give `iter_changes` the `changed_at` of the newest
row you stored last time and it yields every match that changed since, oldest change first:

```python
from datetime import datetime, timedelta, timezone

since = datetime.now(timezone.utc) - timedelta(hours=1)
newest = since.isoformat()
for match in client.matches.iter_changes(since):
    newest = match["changed_at"]  # rows arrive in changed_at order
    print(match["id"], match["status"])
# Store `newest`; it is the `since` for the next run.
```

It starts ten minutes before `since` (`overlap=`) because a row can become visible shortly after the
`changed_at` it carries, and drops the repeats this causes. A quiet hour costs one request.
`matches.history(match)` and `matches.iter_history(match)` show what each of those changes was.

## Errors

Every error is an `EsportsOddsError`. A reply with an error status raises an `ApiError` subclass;
branch on the class, or on `error.code`, never on `error.message`.

| Exception | API `code` | Status | Retried automatically |
| --- | --- | --- | --- |
| `AuthenticationError` | `missing_api_key`, `invalid_api_key`, `unauthorized` | 401 | No |
| `PermissionDeniedError` | `forbidden` | 403 | No |
| `NotFoundError` | `not_found`, `unknown_game` | 404 | No |
| `InvalidRequestError` | `invalid_parameter` | 400 | No |
| `InvalidCursorError` | `invalid_cursor` | 400 | No: start the walk again from the first page |
| `ConflictError` | `conflict` | 409 | No |
| `RateLimitError` | `rate_limited` | 429 | Yes, after the `Retry-After` the API sends |
| `QuotaExceededError` | `quota_exceeded` | 429 | Never: nothing changes until the month resets |
| `ServiceUnavailableError` | `unavailable` | 503 | Yes, after the `Retry-After` the API sends |
| `ServerError` | `internal_error` | 500 | Yes, but at most twice |
| `ApiError` | any other | other | Only 502 and 504 |
| `TransportError` | none: the request never got an answer | none | Yes, with backoff |
| `InvalidResponseError` | none: the reply was not the shape the API documents | 2xx | No |

An unknown path id is a `404` (`NotFoundError`), not a `400`, and an unknown id on a sub-resource
such as `matches.maps(...)` returns an empty page rather than an error.

An `ApiError` carries `status`, `code`, `message`, `request_id`, `retry_after`, `headers` and `body`.
Quote `request_id` when you report a problem; it is also in `client.last_response.request_id`.

```python
from esportsodds import QuotaExceededError, RateLimitError

try:
    client.matches.get("00000000-0000-7000-8000-000000000001")
except QuotaExceededError as error:
    print("out of requests until", error.reset_at)
except RateLimitError as error:
    print("slow down; retry in", error.retry_after, "seconds")
```

## Rate limits and retries

Two limits apply: a per-key token bucket (20 requests a second sustained, bursts of 100) and the
monthly allowance of 50,000 requests per account, shared by all of an account's keys. After every
call, `client.last_response.rate_limit` has the budget the API reported:

```python
info = client.last_response  # None until the first call has been made
if info is not None:
    budget = info.rate_limit
    print(budget.quota_remaining, "of", budget.quota_limit, "left; resets", budget.quota_reset)
```

The client retries only what is safe to retry, up to `max_retries` times (3 by default), and logs
each retry at `INFO` on the `esportsodds` logger:

- A `429 rate_limited` or `503` waits the `Retry-After` the API sent, plus up to half a second of
  jitter. A wait longer than a minute is raised instead of slept through.
- A `502` or `504` and a network failure back off from half a second, doubling, up to eight seconds,
  with jitter.
- A `500` is retried at most twice however high `max_retries` is: the API counts it against your
  allowance, so each retry spends a request you are paying for.
- A `429 quota_exceeded` is never retried, and nothing else is.

Polling is rarely the cheapest design: the [change feed](#pagination-the-bulk-export-and-the-change-feed)
and the WebSocket cost far fewer requests. The
[request budget guide](https://docs.esportsodds.gg/docs/how-to/request-budget) has the numbers.

## WebSocket (match updates)

The WebSocket pushes score and odds changes for the matches you subscribe to, and it costs no
requests: neither the ticket, nor the connection, nor the messages count against your allowance.
Updates arrive when our own collection sees a change, so the socket removes your polling delay but
cannot make the data newer than that collection; the guide linked below lists the typical ages.

This package does not open sockets itself (that would need a dependency). It mints the ticket and
builds the address; any WebSocket library connects, for example
[`websockets`](https://websockets.readthedocs.io/) (`pip install websockets`):

```python
import asyncio
import json

import websockets

from esportsodds import Client

client = Client()


async def watch(match_ids: list[str]) -> None:
    ticket = await asyncio.to_thread(client.ws_token)
    async with websockets.connect(client.ws_url(ticket)) as socket:
        await socket.send(json.dumps({"type": "subscribe", "data": {"match_ids": match_ids}}))
        async for raw in socket:
            frame = json.loads(raw)
            if frame["type"] in ("snapshot", "update"):
                print(frame["match_id"], frame["seq"], frame["type"])


asyncio.run(watch(["00000000-0000-7000-8000-000000000001"]))
```

- A ticket lasts 60 seconds and is not single-use, so a retry inside that window can reuse it; mint
  a new one once it has expired. Your key never appears in the socket address.
- A `snapshot` is the full current state of a match and an `update` is a change. `seq` counts up per
  match; if one is skipped, subscribe to that match again to get a fresh `snapshot`.
- Reconnect with exponential backoff and jitter, and re-send your `subscribe`. There is no replay.
- A key may hold five connections at once, so subscribe to many matches on one connection.
- The server pings every 54 seconds; `websockets` answers for you.

The [WebSocket guide](https://docs.esportsodds.gg/docs/live-data) lists every frame type.

## Examples

The [`examples/`](https://github.com/esportsodds/esportsodds-python/tree/main/examples) folder has
runnable scripts. Each reads `ESPORTSODDS_API_KEY`.

| Script | What it does |
| --- | --- |
| [`quickstart.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/quickstart.py) | The quickstart above. |
| [`upcoming_with_odds.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/upcoming_with_odds.py) | Upcoming matches with their current market line. |
| [`team_report.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/team_report.py) | A team's form, head-to-head record and ratings. |
| [`backfill_matches.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/backfill_matches.py) | A resumable bulk pull of completed matches to a JSON Lines file. |
| [`follow_changes.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/follow_changes.py) | Keeps a local file current from the change feed. |
| [`handle_errors.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/handle_errors.py) | Reading the budget and handling each kind of error. |
| [`watch_matches.py`](https://github.com/esportsodds/esportsodds-python/blob/main/examples/watch_matches.py) | Match updates over the WebSocket. |

## FAQ

**Is this an official API?** No. It is not an official HLTV/Valve API, and esportsodds.gg is not
affiliated with or endorsed by Valve or any stats site.

**Why not scrape?** Scraping breaks whenever a page changes and many sites forbid it. With this API
you build without scraping HLTV or anyone else: you call stable, documented endpoints instead.

**Is there a free tier?** No. There is no free tier and no trial; the plan is $49 a month for
50,000 requests, paid up front. The [public demo](https://demo.esportsodds.gg) shows real
responses without an account.

**What does a call cost?** One request against the 50,000, whatever it returns. A bulk page, which
carries up to 25 matches with their detail, is also one request. A walker (`iter`, `pages`,
`iter_bulk`, `iter_changes`) costs one request per page it fetches. The WebSocket costs none.

**Does it work with asyncio?** The client is synchronous and safe to share between threads. From
async code, run a call in a thread:

```python
matches = await asyncio.to_thread(client.matches.list, status="scheduled", limit=10)
```

**Can I use another HTTP library?** Yes. Pass `transport=` any object with a
`send(TransportRequest) -> TransportResponse` method. To go through a proxy with the default
transport, pass `UrllibTransport(proxies={"https": "http://proxy.example:3128"})`.

**Which games are supported?** CS2 (`game="cs2"`). The `game` argument exists for later titles.

**Is it typed?** Yes: the package ships `py.typed` and passes `mypy --strict`. Responses are plain
dicts, so they are `dict[str, Any]`.

**Where do I report a bug in this client?** In this repository's issues. Questions about your
account, billing or the data go through [esportsodds.gg](https://esportsodds.gg/contact).

## Licence

The code in this repository is released under the
[MIT licence](https://github.com/esportsodds/esportsodds-python/blob/main/LICENSE). The licence
covers this client, not the data it fetches: the data is licensed to you under sections 4 and 5 of
the [Terms of Service](https://esportsodds.gg/terms), which allow you to build on it for your own
purposes and do not allow redistributing it.

Counter-Strike and CS2 are trademarks of Valve Corporation. esportsodds.gg is independent and is
not affiliated with, endorsed by or sponsored by Valve.
