# Changelog

All notable changes to this package are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0, a minor version may change
the API; the change will be listed here first.

## [Unreleased]

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
