# Changelog

All notable changes to this package are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0, a minor version may change
the API; the change will be listed here first.

## [Unreleased]

## [0.4.1] - 2026-10-14

### Fixed

- The MCP server's instructions, the text an AI assistant reads first, still told it to find a team,
  player or tournament from a name with the `slug` filter, which has to match exactly. They now send it
  to `q`, the name search added in 0.4.0, so one call finds a team from part of its name instead of
  guessing a spelling. No behaviour change: the client sends exactly what you ask for.

## [0.4.0] - 2026-10-14

### Added

- `q` on `teams.list`, `tournaments.list` and `players.list`, and on their `pages` and `iter`: search by
  name instead of guessing a slug, for example `client.teams.list(q="navi")`. It is case-insensitive and
  takes 2 to 100 characters. Teams match on name, short name, slug and alternate names, players on nickname
  and slug, tournaments on name and slug. The MCP tools `teams_list`, `players_list` and
  `tournaments_list` take it too, so an assistant can look a name up.

## [0.3.3] - 2026-10-13

### Added

- `examples/sandbox_tour.py`: an event's latest results, then one match's veto, maps and top players,
  in two requests. It works on a sandbox key, whose sample does not include the upcoming matches the
  quickstart asks for; the README now says so where it offers the sandbox key.

## [0.3.2] - 2026-10-13

### Fixed

- The quickstart, `examples/upcoming_with_odds.py` and the README asked for scheduled matches in the
  default order, newest first, which starts with the fixture furthest away. They now pass
  `sort="scheduled_at"`, so "the next five scheduled matches" are the next five to be played.
- `matches.list` says so in its documentation, and so does the `matches_list` tool an AI assistant
  reads through the MCP server: for upcoming matches, pass `status="scheduled"` with
  `sort="scheduled_at"`. No behaviour change: the client sends exactly what you ask for.

## [0.3.1] - 2026-10-12

### Changed

- README: there is now a free sandbox key, which reads a fixed sample of past matches (up to 2,000
  requests in total, no card). The README no longer says "no free tier", points at the sandbox guide,
  and says how this client raises the two sandbox errors (`sandbox_quota_exceeded` as
  `QuotaExceededError`, `sandbox_scope` as `PermissionDeniedError`). No code change.

## [0.3.0] - 2026-10-12

### Changed

- A match id retired by a merge keeps raising `NotFoundError` when the API answers it with `308`
  and code `match_merged` instead of `404`, and the surviving id is then in
  `err.headers.get("x-merged-into")`. The client still never follows redirects.

### Added

- An MCP (Model Context Protocol) server, so AI assistants can read the API as tools: run
  `esportsodds mcp` (for example `uvx esportsodds mcp`) or `esportsodds-mcp`. 24 read-only tools,
  one per read method, built from the methods themselves. List tools ask for 20 rows unless the
  assistant sets `limit`. It serves clients of every MCP revision from 2024-11-05 to 2026-07-28,
  uses only the standard library and reads `ESPORTSODDS_API_KEY`.
- Two error codes reserved for sandbox keys: `sandbox_scope` (403) raises `PermissionDeniedError`,
  and `sandbox_quota_exceeded` (429) raises `QuotaExceededError` at once, without retrying.
- `bulk_quota_exceeded` (429), for a plan's monthly allowance on `matches.bulk()`, raises
  `QuotaExceededError` at once, without retrying.
- `sample_quota_exceeded` (429), reserved for the public sample key's daily allowance, raises
  `QuotaExceededError` at once, without retrying.

## [0.2.0] - 2026-10-11

### Added

- Team analysis: `teams.results()` (recent results with every map's score and both teams' ratings going
  into the match), `teams.rivals()` (the opponents a team meets most, with the head-to-head and the
  average rating gap) and `teams.vetoes()` (a team's own picks, bans, first bans and deciders per map, with
  a permaban flag).
- Maps: `maps.list()` (league-wide figures for each competitive map) and `maps.teams(map)` (how each team
  fares on, and chooses around, one map).
- Rankings: `rankings.history()` (the team board at each of the last weekly marks) and
  `rankings.round_stats()` (round-level figures for the top teams, in board order).
- Players: `players.performance()` (last-12-month figures over recorded maps, each with its percentile
  among the players who have a player page), `players.maps()`, `players.matches()` (recent matches, newest
  first), `players.teams()` (team history as stints) and `players.weapons()` (weapon and hit-group
  totals), plus `teams.players()` (the players in a team's newest match with stats, with their
  last-12-month figures). A figure from fewer than 10 recorded maps is `null`, and so is its percentile.
- `window`, `era`, `active` and `include="detail"` on `teams.maps()`; `window` on `teams.ratings()` and
  `teams.round_stats()`; `delta_days` and `include="trend"` on `rankings.list()`.

### Changed

- `teams.ratings()` accepts a `limit` of up to 1000 (it was 200), and each point carries the `match_id`
  that moved it.

## [0.1.0] - 2026-10-08

### Added

- `Client`, with one method for each public endpoint of the esportsodds.gg CS2 API: matches (with
  maps, stats, vetoes, rounds, history, depth, lineups and streams), odds, teams, tournaments,
  players, rankings, coverage and model metrics, plus `health()`.
- Pagination: every list call returns a `Page`, and `pages()` / `iter()` walk a whole list lazily,
  one request per page. The bulk export has `pages_bulk()` / `iter_bulk()`; `matches.iter_changes()`
  follows the change feed with its overlap handled for you; `matches.iter_history()` walks a
  match's revisions.
- Errors: a typed exception for every API error code, all subclasses of `EsportsOddsError`, each
  carrying the status, code, request id and `Retry-After`.
- Retries for what is safe to retry (`429 rate_limited`, `503`, `502`, `504`, network failures and,
  at most twice, `500`), never for `quota_exceeded`. `client.last_response` holds the request id and
  the request budget of the latest response.
- `ws_token()` and `ws_url()` for the WebSocket; the package does not open the socket itself.
- Standard library only, Python 3.10 and newer, typed (`py.typed`, checked with `mypy --strict`).
