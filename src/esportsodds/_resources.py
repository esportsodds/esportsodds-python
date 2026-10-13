"""The resource namespaces: `client.matches`, `client.teams` and the rest.

Every method is a thin, explicitly typed wrapper over one row of `_routes.ROUTES`. They are written
out by hand (not generated) so editors and type checkers see every parameter; the tests fail if a
method's parameters ever drift from the route table, and the contract tests fail if the table
drifts from the API's OpenAPI document.

Conventions:

- Anything the API calls an ``id`` accepts a UUID (a `str` or `uuid.UUID`) or the resource's
  slug.
- Filters are keyword-only and optional; ``None`` leaves a filter out.
- A parameter that takes several values (`has`, `include`, `ids`) accepts a comma-separated
  string or any iterable of strings.
- Dates are ``YYYY-MM-DD`` strings or `datetime.date`; instants are RFC 3339 strings or
  `datetime.datetime` (naive means UTC).
"""

from __future__ import annotations

from collections.abc import Generator, Iterable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ._errors import InvalidResponseError
from ._pagination import Page, iter_rows, walk_pages
from ._routes import ROUTES, Route
from ._time import format_rfc3339, parse_rfc3339

if TYPE_CHECKING:
    from ._client import Client

__all__ = [
    "DEFAULT_CHANGE_OVERLAP",
    "Coverage",
    "Maps",
    "Matches",
    "Model",
    "Odds",
    "Players",
    "Rankings",
    "Teams",
    "Tournaments",
]

IdLike = str | UUID
DateLike = str | date | datetime
Multi = str | Iterable[str]
Ids = str | Iterable[str | UUID]
Rows = Generator[dict[str, Any], None, None]
Pages = Generator[Page, None, None]

# Ingestion commits in batches, so a row can become visible a few minutes after the `changed_at`
# stamped on it. A change-feed consumer that resumes from the exact last stamp it saw can skip such
# a row for good; re-reading the last ten minutes costs a few duplicates (which `iter_changes`
# drops) and closes the gap.
DEFAULT_CHANGE_OVERLAP = timedelta(minutes=10)

_HISTORY_MAX = 200  # the history endpoint's page ceiling; it clamps anything larger


class _Namespace:
    __slots__ = ("_c",)

    def __init__(self, client: Client) -> None:
        self._c = client


def _ident(value: object, what: str) -> str:
    """A path segment: a UUID or, for teams, players and tournaments, a slug. Rejects what cannot
    name a resource, including the dot segments that a proxy would resolve into a different URL."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str) and value.strip() and value not in (".", ".."):
        return value
    shape = {
        "match": "id (UUID)",  # matches have no slug
        "map": "map name such as dust2",  # a map has no UUID
    }.get(what, "id (UUID) or slug")
    raise ValueError(f"{what} must be a non-empty {shape}, got {value!r}")


def _page_size(limit: int | None, route: Route) -> int | None:
    """Walking helpers ask for the biggest page unless told otherwise: every request counts
    against the monthly allowance."""
    return route.page_max if limit is None else limit


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def _has_values(value: Multi | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return any(True for _ in list(value))


def _dedupe(rows: Rows) -> Rows:
    """Drop a row the walk has already yielded: the same id at the same `changed_at`.

    Keyed on both, never on the id alone: a match that changes again while the walk is running
    comes back with a newer `changed_at` and must be yielded again, or a consumer that then moves
    its checkpoint forward would never see the newer state.
    """
    seen: set[tuple[str, str]] = set()
    try:
        for row in rows:
            ident, stamp = row.get("id"), row.get("changed_at")
            if isinstance(ident, str) and isinstance(stamp, str):
                key = (ident, stamp)
                if key in seen:
                    continue
                seen.add(key)
            yield row
    finally:
        rows.close()


def _window_start(since: str | date | datetime, overlap: timedelta) -> str:
    if not isinstance(overlap, timedelta) or overlap < timedelta(0):
        raise ValueError(f"overlap must be a non-negative timedelta, got {overlap!r}")
    if isinstance(since, datetime):
        moment = since
    elif isinstance(since, date):
        moment = datetime(since.year, since.month, since.day)
    elif isinstance(since, str):
        moment = parse_rfc3339(since)
    else:
        raise TypeError("since must be an RFC 3339 string, a datetime or a date")
    return format_rfc3339(moment - overlap)


# ---------------------------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------------------------


class Matches(_Namespace):
    """Matches: fixtures, results, and everything attached to a match.

    Three ways to read the list: `list` (one page), `pages` (page by page, with the cursor to
    checkpoint) and `iter` (row by row). `bulk` and its siblings return the same matches with
    their sub-resources embedded, one request per 25 matches. `iter_changes` follows the change
    feed. `iter_history` walks a match's recorded revisions.
    """

    def list(
        self,
        *,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        changed_since: DateLike | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """One page of matches, newest fixture first.

        That order starts a list of scheduled matches with the fixture furthest away. For the
        next matches to be played, pass ``status="scheduled", sort="scheduled_at"``.

        Args:
            status: ``scheduled``, ``live``, ``completed`` or ``cancelled``. With ``scheduled``,
                also pass ``sort="scheduled_at"`` to get the next fixtures, not the furthest away.
            tournament: Only this tournament's matches (id).
            team: Only this team's matches (id).
            date_from: Scheduled on or after this day (UTC, inclusive).
            date_to: Scheduled on or before this day (UTC, inclusive of the whole day).
            has: Only matches that carry all of these sub-resources, for example
                ``["rounds", "depth"]``.
            changed_since: Only matches whose ``changed_at`` is at or after this instant; the
                change feed (prefer `iter_changes`, which also handles the overlap).
            sort: ``-scheduled_at`` (default, newest first), ``scheduled_at`` (oldest first,
                which for scheduled matches is soonest first), ``changed_at`` or ``-changed_at``.
                A cursor is tied to the sort it came from.
            limit: Page size (default 100, maximum 500).
            cursor: ``next_cursor`` from the previous page; omit for the first.
        """
        return self._c._page(
            "matches.list",
            None,
            status=status,
            tournament=tournament,
            team=team,
            date_from=date_from,
            date_to=date_to,
            has=has,
            changed_since=changed_since,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )

    def pages(
        self,
        *,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        changed_since: DateLike | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Pages:
        """Pages of matches, following ``next_cursor`` until it is null.

        Each page costs one request. ``limit`` defaults to the largest page (500). The last page
        yielded carries its ``next_cursor``, so a job that stops at ``max_pages`` (or crashes)
        can resume with ``cursor=``. Never stops on a short page: only a null cursor ends a walk.
        """
        size = _page_size(limit, ROUTES["matches.list"])

        def fetch(after: str | None) -> Page:
            return self.list(
                status=status,
                tournament=tournament,
                team=team,
                date_from=date_from,
                date_to=date_to,
                has=has,
                changed_since=changed_since,
                sort=sort,
                limit=size,
                cursor=after,
            )

        return walk_pages(fetch, cursor=cursor, max_pages=max_pages)

    def iter(
        self,
        *,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        changed_since: DateLike | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every matching match, one row at a time. Lazy: a page is fetched only when the
        previous one is used up, so stopping early costs nothing more. See `pages`."""
        return iter_rows(
            self.pages(
                status=status,
                tournament=tournament,
                team=team,
                date_from=date_from,
                date_to=date_to,
                has=has,
                changed_since=changed_since,
                sort=sort,
                limit=limit,
                cursor=cursor,
                max_pages=max_pages,
            )
        )

    def bulk(
        self,
        *,
        include: Multi | None = None,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        changed_since: DateLike | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """One page of matches with their sub-resources embedded: 25 matches per request.

        Takes the filters, ordering and cursors of `list`. A bulk page counts as a single request
        against your allowance however much it carries, so it is the cheap way to pull history.

        Args:
            include: Sub-resources to embed on every match: ``maps``, ``stats``, ``vetoes``,
                ``rounds``, ``depth``, ``lineups``, ``streams``, ``players``. Ask only for what you
                need; an omitted one costs nothing to build.
            limit: Matches per page (at most 25, which is also the default).

        Pages are large (up to about 10 MB before compression) and at most two are built at once
        across the whole service, so a busy moment answers 503 with a short ``Retry-After``; the
        client waits and retries that for you. The timeout is at least 120 seconds.
        """
        return self._c._page(
            "matches.bulk",
            None,
            include=include,
            status=status,
            tournament=tournament,
            team=team,
            date_from=date_from,
            date_to=date_to,
            has=has,
            changed_since=changed_since,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )

    def pages_bulk(
        self,
        *,
        include: Multi | None = None,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        changed_since: DateLike | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Pages:
        """Pages of `bulk`, following ``next_cursor``. Use this for a resumable backfill: save
        each page's ``next_cursor`` and pass it as ``cursor=`` to carry on after an interruption."""
        size = _page_size(limit, ROUTES["matches.bulk"])

        def fetch(after: str | None) -> Page:
            return self.bulk(
                include=include,
                status=status,
                tournament=tournament,
                team=team,
                date_from=date_from,
                date_to=date_to,
                has=has,
                changed_since=changed_since,
                sort=sort,
                limit=size,
                cursor=after,
            )

        return walk_pages(fetch, cursor=cursor, max_pages=max_pages)

    def iter_bulk(
        self,
        *,
        include: Multi | None = None,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        changed_since: DateLike | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every matching match with its sub-resources, one row at a time. See `pages_bulk`."""
        return iter_rows(
            self.pages_bulk(
                include=include,
                status=status,
                tournament=tournament,
                team=team,
                date_from=date_from,
                date_to=date_to,
                has=has,
                changed_since=changed_since,
                sort=sort,
                limit=limit,
                cursor=cursor,
                max_pages=max_pages,
            )
        )

    def iter_changes(
        self,
        since: str | date | datetime,
        *,
        include: Multi | None = None,
        status: str | None = None,
        tournament: IdLike | None = None,
        team: IdLike | None = None,
        date_from: DateLike | None = None,
        date_to: DateLike | None = None,
        has: Multi | None = None,
        overlap: timedelta = DEFAULT_CHANGE_OVERLAP,
        max_pages: int | None = None,
    ) -> Rows:
        """Every match that changed since an instant, oldest change first: the change feed.

        To keep a local copy current, call this with the ``changed_at`` of the newest row you
        stored last time. Rows come back in ``changed_at`` order, so after a clean run the newest
        row's ``changed_at`` is your next ``since``.

        The window is widened by ``overlap`` (ten minutes by default) because a row can become
        visible shortly after the ``changed_at`` it carries; the repeats this causes are dropped
        (same id and same ``changed_at``). A match that changes *again* during the walk is
        yielded again with its newer state.

        Args:
            since: An RFC 3339 string, a `datetime` (naive means UTC) or a `date`.
            include: Embed sub-resources (as in `bulk`). When given, the walk uses the bulk
                route, 25 matches per request; otherwise the plain list, 500 per request.
            overlap: How far before ``since`` to start. Use ``timedelta(0)`` to turn it off.

        Raises:
            ValueError: ``since`` or ``overlap`` is invalid (raised at the call, before any
                request).
        """
        start = _window_start(since, overlap)
        feed: Pages
        if _has_values(include):
            feed = self.pages_bulk(
                include=include,
                status=status,
                tournament=tournament,
                team=team,
                date_from=date_from,
                date_to=date_to,
                has=has,
                changed_since=start,
                sort="changed_at",
                max_pages=max_pages,
            )
        else:
            feed = self.pages(
                status=status,
                tournament=tournament,
                team=team,
                date_from=date_from,
                date_to=date_to,
                has=has,
                changed_since=start,
                sort="changed_at",
                max_pages=max_pages,
            )
        return _dedupe(iter_rows(feed))

    def get(self, match: IdLike, *, include: Multi | None = None) -> dict[str, Any]:
        """One match. The ``data_available`` flags say which sub-resources exist for it.

        Args:
            match: The match's id, which is always a UUID.
            include: Embed related resources: ``teams`` (adds ``team_a``/``team_b``),
                ``tournament`` and ``odds`` (the latest line per outcome), or sub-resources.
        """
        return self._c._item("matches.get", _ident(match, "match"), include=include)

    def maps(self, match: IdLike) -> Page:
        """Per-map scores for one match."""
        return self._c._page("matches.maps", _ident(match, "match"))

    def stats(self, match: IdLike) -> Page:
        """Per-player performance stats for one match."""
        return self._c._page("matches.stats", _ident(match, "match"))

    def vetoes(self, match: IdLike) -> Page:
        """The map veto (pick and ban) sequence for one match."""
        return self._c._page("matches.vetoes", _ident(match, "match"))

    def rounds(self, match: IdLike) -> Page:
        """Round-by-round team stats for one match."""
        return self._c._page("matches.rounds", _ident(match, "match"))

    def depth(self, match: IdLike) -> dict[str, Any]:
        """Per-player depth breakdowns for one match: ``weapons``, ``grenades``, ``hitgroups``,
        ``duels`` and ``flashes``. A match without depth returns every group as an empty list."""
        return self._c._item("matches.depth", _ident(match, "match"))

    def lineups(self, match: IdLike) -> Page:
        """Pre-match roster snapshots. A snapshot is a listed roster (substitutes included), not
        a confirmed starting five; the players who played are in `stats`."""
        return self._c._page("matches.lineups", _ident(match, "match"))

    def streams(self, match: IdLike) -> Page:
        """Broadcast channels for a match in progress, official first. Empty before a match starts
        and after it ends, so do not keep these rows as an archive."""
        return self._c._page("matches.streams", _ident(match, "match"))

    def history(
        self,
        match: IdLike,
        *,
        limit: int | None = None,
        before: str | datetime | None = None,
    ) -> Page:
        """One page of a match's recorded revisions, newest first, each with the value before and
        after. Not cursor-paginated: page backwards with ``before`` (exclusive), or use
        `iter_history`.

        Args:
            limit: Entries to return (default 50, maximum 200).
            before: Only entries strictly earlier than this instant.
        """
        return self._c._page("matches.history", _ident(match, "match"), limit=limit, before=before)

    def iter_history(
        self,
        match: IdLike,
        *,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every recorded revision of a match, newest first, walking back through time.

        The history endpoint has no cursor, so this pages with ``before`` set to the last entry's
        ``changed_at`` (exactly as the API sent it) and stops when a page has fewer entries than
        asked for. ``limit`` is capped at 200, the endpoint's own ceiling, so a short page really
        does mean the end.

        Raises:
            ValueError: ``limit`` or ``max_pages`` is not a positive integer (raised at the call).
            InvalidResponseError: An entry lacks ``changed_at``, or the walk fails to move back.
        """
        ident = _ident(match, "match")
        size = _HISTORY_MAX if limit is None else min(_positive(limit, "limit"), _HISTORY_MAX)
        if max_pages is not None:
            _positive(max_pages, "max_pages")
        return self._walk_history(ident, size, max_pages)

    def _walk_history(self, ident: str, size: int, max_pages: int | None) -> Rows:
        before: str | None = None
        fetched = 0
        while True:
            page = self._c._page("matches.history", ident, limit=size, before=before)
            fetched += 1
            rows = page.data
            if len(rows) < size or (max_pages is not None and fetched >= max_pages):
                yield from rows  # a short page is the end here (the size was clamped to the
                return  # server's own ceiling), or the caller's cap was reached
            last = rows[-1].get("changed_at")
            if not isinstance(last, str):
                raise InvalidResponseError(
                    "a history entry has no changed_at, so the walk cannot continue past it"
                )
            if before is not None and (
                last == before or parse_rfc3339(last) > parse_rfc3339(before)
            ):
                raise InvalidResponseError(
                    "history did not move back in time (the same changed_at came back); "
                    "stopping instead of repeating a page"
                )
            yield from rows
            before = last


# ---------------------------------------------------------------------------------------------
# Odds
# ---------------------------------------------------------------------------------------------


class Odds(_Namespace):
    """Odds lines: the served market line (``eo_market``) and, once released, the model line
    (``eo_model``). Only these two derived sources are ever returned."""

    def list(
        self,
        *,
        match: IdLike | None = None,
        source: str | None = None,
        latest: bool = False,
        market: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """Odds lines, newest first.

        With ``match`` this is that match's line history, paginated by ``cursor``. Without it
        (or with ``match`` and ``latest=True``) it is a snapshot: the latest line per market and
        outcome, in a single page capped at ``limit``, whose ``next_cursor`` is always null.

        Args:
            match: One match's id.
            source: ``eo_market`` or ``eo_model``. ``eo_model`` rows are absent until the model
                line is released. Omit for both.
            latest: With ``match``, return the current board instead of the tick history.
            market: With ``match``, only this market type (for example ``match_winner``).
            limit: Page size (default 200, maximum 1000).
            cursor: ``next_cursor`` from the previous page (match history only).
        """
        return self._c._page(
            "odds.list",
            None,
            match=match,
            source=source,
            latest=latest,
            market=market,
            limit=limit,
            cursor=cursor,
        )

    def pages(
        self,
        *,
        match: IdLike | None = None,
        source: str | None = None,
        latest: bool = False,
        market: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Pages:
        """Pages of `list`, following ``next_cursor``; ``limit`` defaults to 1000. A snapshot has
        one page, so the walk ends after it."""
        size = _page_size(limit, ROUTES["odds.list"])

        def fetch(after: str | None) -> Page:
            return self.list(
                match=match, source=source, latest=latest, market=market, limit=size, cursor=after
            )

        return walk_pages(fetch, cursor=cursor, max_pages=max_pages)

    def iter(
        self,
        *,
        match: IdLike | None = None,
        source: str | None = None,
        latest: bool = False,
        market: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every odds line, one row at a time. See `pages`."""
        return iter_rows(
            self.pages(
                match=match,
                source=source,
                latest=latest,
                market=market,
                limit=limit,
                cursor=cursor,
                max_pages=max_pages,
            )
        )


# ---------------------------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------------------------


class Teams(_Namespace):
    """Teams, with their form, results, ratings, maps, vetoes, rivals, round stats and roster
    history."""

    def list(
        self,
        *,
        slug: str | None = None,
        region: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """One page of teams, by name.

        Args:
            slug: Exact slug match (returns the one team, or an empty page).
            region: One circuit region, by full name: ``Europe``, ``North America``,
                ``South America``, ``Asia``, ``CIS``, ``Oceania`` or ``Africa``.
            sort: ``name`` (default) or ``slug``; prefix ``-`` to reverse.
            limit: Page size (default 100, maximum 500).
            cursor: ``next_cursor`` from the previous page.
        """
        return self._c._page(
            "teams.list", None, slug=slug, region=region, sort=sort, limit=limit, cursor=cursor
        )

    def pages(
        self,
        *,
        slug: str | None = None,
        region: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Pages:
        """Pages of teams, following ``next_cursor``; ``limit`` defaults to 500."""
        size = _page_size(limit, ROUTES["teams.list"])

        def fetch(after: str | None) -> Page:
            return self.list(slug=slug, region=region, sort=sort, limit=size, cursor=after)

        return walk_pages(fetch, cursor=cursor, max_pages=max_pages)

    def iter(
        self,
        *,
        slug: str | None = None,
        region: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every team, one row at a time. See `pages`."""
        return iter_rows(
            self.pages(
                slug=slug,
                region=region,
                sort=sort,
                limit=limit,
                cursor=cursor,
                max_pages=max_pages,
            )
        )

    def get(
        self, team: IdLike, *, include: Multi | None = None, rank: bool = False
    ) -> dict[str, Any]:
        """One team.

        Args:
            team: The team's id or slug.
            include: ``rank`` fills ``own_rank``, the team's position on the 3-month leaderboard.
            rank: The same as ``include="rank"``. Off by default because it costs the API an
                extra query.
        """
        return self._c._item("teams.get", _ident(team, "team"), include=include, rank=rank)

    def h2h(self, team: IdLike, *, opponent: IdLike) -> dict[str, Any]:
        """The completed-match record between two teams.

        Args:
            team: The first team (id or slug).
            opponent: The other team (id or slug). Required.
        """
        return self._c._item(
            "teams.h2h", _ident(team, "team"), opponent=_ident(opponent, "opponent")
        )

    def form(self, team: IdLike, *, limit: int | None = None) -> Page:
        """A team's recent completed results, newest first (default 10, maximum 50)."""
        return self._c._page("teams.form", _ident(team, "team"), limit=limit)

    def ratings(self, team: IdLike, *, limit: int | None = None, window: str | None = None) -> Page:
        """A team's Glicko-2 rating history, one point per played match, oldest first, each with
        the ``match_id`` that moved it (the most recent ``limit`` points; default 30, maximum 1000).

        Args:
            team: The team's id or slug.
            limit: How many points to return.
            window: A trailing span such as ``"12m"`` (whole months, up to ``"60m"``) or ``"all"``.
                Naming one returns that whole span and raises the default ``limit`` to the maximum.
        """
        return self._c._page("teams.ratings", _ident(team, "team"), limit=limit, window=window)

    def maps(
        self,
        team: IdLike,
        *,
        window: str | None = None,
        era: str | None = None,
        active: bool = False,
        include: Multi | None = None,
    ) -> Page:
        """A team's win and loss record per map, best win rate first.

        With no arguments this covers the team's whole history, including maps that have left the
        pool. The arguments narrow it to what is current.

        Args:
            team: The team's id or slug.
            window: A trailing span such as ``"6m"`` (whole months, up to ``"60m"``) or ``"all"``
                (the default).
            era: ``cs2`` keeps Counter-Strike 2 matches only; ``all`` (the default) keeps every one.
            active: Only the maps in today's Active Duty pool.
            include: ``detail`` adds each map's last-played date, pool membership and the team's
                round record by side and in pistol rounds.
        """
        return self._c._page(
            "teams.maps",
            _ident(team, "team"),
            window=window,
            era=era,
            active="true" if active else None,  # this flag is spelled out, unlike `rank`
            include=include,
        )

    def round_stats(self, team: IdLike, *, window: str | None = None) -> dict[str, Any]:
        """A team's round-level playstyle profile (side win rates, pistol, opening duels, eco,
        clutch), each a percentage or null when there is no sample.

        Args:
            team: The team's id or slug.
            window: A trailing span such as ``"6m"`` (whole months, up to ``"60m"``) or ``"all"``
                (the default).
        """
        return self._c._item("teams.round_stats", _ident(team, "team"), window=window)

    def vetoes(
        self, team: IdLike, *, window: str | None = None, era: str | None = None
    ) -> dict[str, Any]:
        """How a team chooses maps: for each map, its own picks, bans, first bans and deciders, and
        whether it is a permaban. Read every share against ``veto_matches``, the matches with a
        recorded veto, not against ``matches``.

        Args:
            team: The team's id or slug.
            window: A trailing span such as ``"6m"`` (the default; whole months up to ``"60m"``) or
                ``"all"``.
            era: ``cs2`` or ``all`` (the default).
        """
        return self._c._item("teams.vetoes", _ident(team, "team"), window=window, era=era)

    def results(
        self, team: IdLike, *, limit: int | None = None, opponent: IdLike | None = None
    ) -> Page:
        """A team's completed, decided matches, newest first, with every played map's score and
        both teams' ratings going into the match (default 20, maximum 100).

        Args:
            team: The team's id or slug.
            limit: How many results to return.
            opponent: Only the matches against this team (id or slug).
        """
        return self._c._page(
            "teams.results",
            _ident(team, "team"),
            limit=limit,
            opponent=None if opponent is None else _ident(opponent, "opponent"),
        )

    def rivals(
        self,
        team: IdLike,
        *,
        limit: int | None = None,
        min_meetings: int | None = None,
        window: str | None = None,
    ) -> Page:
        """The opponents a team has met most often, most meetings first, with the head-to-head, the
        last ten meetings and the average rating gap (default 5, maximum 20).

        Args:
            team: The team's id or slug.
            limit: How many rivals to return.
            min_meetings: Fewest meetings that make an opponent a rival, 1 to 50 (default 2).
            window: A trailing span such as ``"36m"`` (the default; whole months up to
                ``"120m"``) or ``"all"``.
        """
        return self._c._page(
            "teams.rivals",
            _ident(team, "team"),
            limit=limit,
            min_meetings=min_meetings,
            window=window,
        )

    def roster_history(self, team: IdLike, *, limit: int | None = None) -> Page:
        """The five players a team fielded in each recent completed match, newest first, with the
        roster-change facts (default 20, maximum 100). Read ``continuity`` before interpreting
        ``changed_count``."""
        return self._c._page("teams.roster_history", _ident(team, "team"), limit=limit)

    def players(self, team: IdLike) -> dict[str, Any]:
        """The players in a team's newest completed match with stats, usually five. Each comes with
        their own figures over the last 12 months (the same as `players.performance`), whether they
        have a player page, and when their current run with the team began.

        ``as_of`` is that match's date; a team with no such match returns ``as_of: null`` and no
        players. A figure from fewer than 10 recorded maps is null. Players are identified by
        nickname, country and role only.

        Args:
            team: The team's id or slug.
        """
        return self._c._item("teams.players", _ident(team, "team"))


# ---------------------------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------------------------


class Tournaments(_Namespace):
    """Tournaments and the teams that took part."""

    def list(
        self,
        *,
        slug: str | None = None,
        status: str | None = None,
        tier: str | None = None,
        event_type: str | None = None,
        region: str | None = None,
        year: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """One page of tournaments.

        Args:
            slug: Exact slug match.
            status: ``upcoming``, ``ongoing`` or ``finished``.
            tier: One prestige tier, ``S`` (highest) down to ``C``.
            event_type: ``lan`` or ``online``.
            region: One circuit region, by full name (for example ``Europe``).
            year: Tournaments starting in this calendar year (2000 to 2100).
            limit: Page size (default 100, maximum 500).
            cursor: ``next_cursor`` from the previous page.
        """
        return self._c._page(
            "tournaments.list",
            None,
            slug=slug,
            status=status,
            tier=tier,
            event_type=event_type,
            region=region,
            year=year,
            limit=limit,
            cursor=cursor,
        )

    def pages(
        self,
        *,
        slug: str | None = None,
        status: str | None = None,
        tier: str | None = None,
        event_type: str | None = None,
        region: str | None = None,
        year: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Pages:
        """Pages of tournaments, following ``next_cursor``; ``limit`` defaults to 500."""
        size = _page_size(limit, ROUTES["tournaments.list"])

        def fetch(after: str | None) -> Page:
            return self.list(
                slug=slug,
                status=status,
                tier=tier,
                event_type=event_type,
                region=region,
                year=year,
                limit=size,
                cursor=after,
            )

        return walk_pages(fetch, cursor=cursor, max_pages=max_pages)

    def iter(
        self,
        *,
        slug: str | None = None,
        status: str | None = None,
        tier: str | None = None,
        event_type: str | None = None,
        region: str | None = None,
        year: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every tournament, one row at a time. See `pages`."""
        return iter_rows(
            self.pages(
                slug=slug,
                status=status,
                tier=tier,
                event_type=event_type,
                region=region,
                year=year,
                limit=limit,
                cursor=cursor,
                max_pages=max_pages,
            )
        )

    def get(self, tournament: IdLike) -> dict[str, Any]:
        """One tournament (id or slug)."""
        return self._c._item("tournaments.get", _ident(tournament, "tournament"))

    def teams(self, tournament: IdLike) -> Page:
        """The teams that took part in a tournament."""
        return self._c._page("tournaments.teams", _ident(tournament, "tournament"))


# ---------------------------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------------------------


class Players(_Namespace):
    """Players: career totals, last-12-month figures with percentiles, maps, recent matches, team
    history and weapons. A player is identified by nickname, country and role only."""

    def list(
        self,
        *,
        slug: str | None = None,
        team: IdLike | None = None,
        role: str | None = None,
        ids: Ids | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """One page of players, by nickname.

        Args:
            slug: Exact slug match.
            team: Only this team's roster (id).
            role: One in-game role: ``AWP``, ``IGL``, ``Lurker``, ``Rifler`` or ``Support``
                (exact, case-sensitive; only some players have one on file).
            ids: Look up specific players: up to 500 ids, as a list or comma-separated string.
            sort: ``nickname`` (default) or ``slug``; prefix ``-`` to reverse.
            limit: Page size (default 100, maximum 500).
            cursor: ``next_cursor`` from the previous page.
        """
        return self._c._page(
            "players.list",
            None,
            slug=slug,
            team=team,
            role=role,
            ids=ids,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )

    def pages(
        self,
        *,
        slug: str | None = None,
        team: IdLike | None = None,
        role: str | None = None,
        ids: Ids | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Pages:
        """Pages of players, following ``next_cursor``; ``limit`` defaults to 500."""
        size = _page_size(limit, ROUTES["players.list"])

        def fetch(after: str | None) -> Page:
            return self.list(
                slug=slug, team=team, role=role, ids=ids, sort=sort, limit=size, cursor=after
            )

        return walk_pages(fetch, cursor=cursor, max_pages=max_pages)

    def iter(
        self,
        *,
        slug: str | None = None,
        team: IdLike | None = None,
        role: str | None = None,
        ids: Ids | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        max_pages: int | None = None,
    ) -> Rows:
        """Every player, one row at a time. See `pages`."""
        return iter_rows(
            self.pages(
                slug=slug,
                team=team,
                role=role,
                ids=ids,
                sort=sort,
                limit=limit,
                cursor=cursor,
                max_pages=max_pages,
            )
        )

    def get(
        self, player: IdLike, *, include: Multi | None = None, rank: bool = False
    ) -> dict[str, Any]:
        """One player.

        Args:
            player: The player's id or slug.
            include: ``rank`` fills ``own_rank``, the player's position on the 3-month leaderboard.
            rank: The same as ``include="rank"``. Off by default because it costs the API an
                extra query.
        """
        return self._c._item("players.get", _ident(player, "player"), include=include, rank=rank)

    def career(self, player: IdLike) -> dict[str, Any]:
        """A player's all-time career totals and averages (averages are null with no rated maps)."""
        return self._c._item("players.career", _ident(player, "player"))

    def performance(self, player: IdLike) -> dict[str, Any]:
        """A player's figures over their recorded maps in the last 12 months, each with its
        percentile among every player who has a player page.

        A recorded map is one played to a result on a competitive map, in a completed
        Counter-Strike 2 match that was not a walkover, where the player's line is complete.
        Every rate is a ratio of sums over the maps that carry its inputs, and ``n`` is how many
        maps a figure uses. A figure from fewer than 10 maps is null, and so is its percentile.

        Read ``level`` (``established``, ``listed`` or ``none``) and each ``percentile`` as the API
        returns them: ``definition`` and ``population.level_definition`` quote every rule, so
        quote them rather than restate them.

        Args:
            player: The player's id or slug.
        """
        return self._c._item("players.performance", _ident(player, "player"))

    def maps(self, player: IdLike) -> dict[str, Any]:
        """A player's figures on each competitive map they have a recorded map on in the last
        12 months, most played first: maps played, ADR and KAST weighted by rounds, and kills per
        death. A figure from fewer than 10 maps on that map is null.

        Args:
            player: The player's id or slug.
        """
        return self._c._item("players.maps", _ident(player, "player"))

    def matches(self, player: IdLike, *, limit: int | None = None) -> Page:
        """A player's recent completed matches, newest first, with the team they played for, the
        opponent, the series score from their side, the tournament and their own kills, deaths and
        ADR. Walkovers and matches before Counter-Strike 2 are left out. Not paginated.

        Args:
            player: The player's id or slug.
            limit: How many matches to return (default 10, maximum 25).
        """
        return self._c._page("players.matches", _ident(player, "player"), limit=limit)

    def teams(self, player: IdLike) -> dict[str, Any]:
        """A player's team history as stints, newest first. A stint is a run of consecutive matches
        for one team; a run of fewer than three matches is left out unless it is the current one.
        The newest stint is ``current`` only while its last match is recent.

        Args:
            player: The player's id or slug.
        """
        return self._c._item("players.teams", _ident(player, "player"))

    def weapons(self, player: IdLike) -> dict[str, Any]:
        """A player's weapon and hit-group totals over their matches in the last 12 months. The
        eight weapons with the most kills are listed and the rest folded into one ``other`` row.
        Weapon data is recorded per match, never per map; a player with none gets empty lists.

        Args:
            player: The player's id or slug.
        """
        return self._c._item("players.weapons", _ident(player, "player"))


# ---------------------------------------------------------------------------------------------
# Rankings, coverage, model
# ---------------------------------------------------------------------------------------------


class Rankings(_Namespace):
    """The team and player leaderboards over a rolling three-month window, the team board's
    history, and the round-level figures of its top teams."""

    def list(
        self,
        *,
        type: str | None = None,
        region: str | None = None,
        role: str | None = None,
        sort: str | None = None,
        delta_days: int | None = None,
        include: Multi | None = None,
        limit: int | None = None,
    ) -> Page:
        """A leaderboard. Not paginated: ``limit`` truncates the board and ``next_cursor`` is
        always null.

        Args:
            type: ``team`` (default) or ``player``. They rank on different metrics, named in each
                row's ``metric``.
            region: One circuit region, by full name.
            role: One player role (with ``type="player"``).
            sort: ``rank`` (default), ``value`` or ``name``; prefix ``-`` to reverse. Re-sorting
                reorders the rows but never changes their ``rank``.
            delta_days: Days back that a team's ``rank_delta`` is measured against, 1 to 90
                (default 7).
            include: ``trend`` adds, to each team row, the board value at each of the last twelve
                weekly marks, oldest first.
            limit: Rows to return (default 100, maximum 500).
        """
        return self._c._page(
            "rankings.list",
            None,
            type=type,
            region=region,
            role=role,
            sort=sort,
            delta_days=delta_days,
            include=include,
            limit=limit,
        )

    def history(self, *, weeks: int | None = None, top: int | None = None) -> Page:
        """The top of the team board at each of the last ``weeks`` weekly marks, oldest first, the
        newest being now. Always exactly ``weeks`` snapshots; a mark with no eligible team has an
        empty ``entries`` list.

        Args:
            weeks: How many weekly snapshots, 1 to 52 (default 13).
            top: How many teams in each snapshot, 1 to 25 (default 10).
        """
        return self._c._page("rankings.history", None, weeks=weeks, top=top)

    def round_stats(self, *, window: str | None = None, limit: int | None = None) -> Page:
        """The round-level figures of ``teams.round_stats`` for the top teams of the board, in
        board order, each with its team and rank: one request in place of one per team (default 30,
        maximum 100).

        Args:
            window: A trailing span of whole months from ``"1m"`` to ``"12m"`` (default ``"6m"``);
                there is no ``"all"``.
            limit: How many top teams to return.
        """
        return self._c._page("rankings.round_stats", None, window=window, limit=limit)


class Maps(_Namespace):
    """League-wide figures for each competitive map, and how each team fares on one map."""

    def list(self, *, window: str | None = None, era: str | None = None) -> Page:
        """Each competitive map's league-wide figures: maps played, average rounds, rounds played
        and won on each side, and the bans and picks recorded in vetoes. Most played first. Read a
        map's bans and picks against its own ``veto_matches``.

        Args:
            window: A trailing span of whole months from ``"1m"`` to ``"12m"`` (default ``"6m"``);
                there is no ``"all"``.
            era: ``cs2`` or ``all`` (the default).
        """
        return self._c._page("maps.list", None, window=window, era=era)

    def teams(
        self,
        map: str,
        *,
        window: str | None = None,
        era: str | None = None,
        limit: int | None = None,
    ) -> Page:
        """How each team fares on one map: maps played and won, its own picks and bans of the
        map, and whether it is a permaban. A team is listed with at least 10 maps played on the
        map or at least 10 picks plus bans of it (default 100, maximum 200).

        Args:
            map: The map's name, such as ``"dust2"`` (the ``de_`` prefix is optional). An unknown
                name raises `NotFoundError`.
            window: A trailing span such as ``"6m"`` (the default; whole months up to ``"60m"``)
                or ``"all"``.
            era: ``cs2`` or ``all`` (the default).
            limit: How many teams to return.
        """
        return self._c._page("maps.teams", _ident(map, "map"), window=window, era=era, limit=limit)


class Coverage(_Namespace):
    """What the data covers, and how reliably."""

    def list(self) -> Page:
        """The coverage matrix: each capability with its status and update cadence."""
        return self._c._page("coverage.list")


class Model(_Namespace):
    """The model line's published track record. The model line is coming soon."""

    def metrics(self) -> Page:
        """Transparency metrics for the model line, per market.

        The model line is coming soon and is not served yet. This reports how it has scored on
        settled matches so far: an in-progress sample, not a claim of accuracy, with
        ``gate_passed`` saying whether it has cleared its release gate. The list may be empty.
        """
        return self._c._page("model.metrics")
