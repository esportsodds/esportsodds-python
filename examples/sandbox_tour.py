"""An event's latest results, then one match in depth: a first run for a sandbox key.

    ESPORTSODDS_API_KEY=... python examples/sandbox_tour.py TOURNAMENT_ID

A sandbox key reads a fixed sample of past matches. The sandbox guide lists the sample's events
and their ids: https://docs.esportsodds.gg/docs/sandbox. A paid key takes any event's id.
Two requests in all.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

from esportsodds import Client, PermissionDeniedError


def score(row: dict[str, Any], a: str, b: str) -> str:
    return f"{a} {row['score_a']}-{row['score_b']} {b}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("tournament", help="the event's tournament id")
    args = parser.parse_args(argv)

    client = Client()
    try:
        results = client.matches.list(tournament=args.tournament, status="completed", limit=5).data
    except PermissionDeniedError as error:
        print(error.message)  # outside a sandbox key's sample; the message names events that work
        return 1
    if not results:
        print("no completed matches for that event")
        return 1

    print(f"{results[0]['tournament_name']}, latest results:")
    for row in results:
        about = ", ".join(part for part in (row.get("stage"), row.get("format")) if part)
        line = f"  {row['scheduled_at'][:10]}  {score(row, row['team_a_name'], row['team_b_name'])}"
        print(f"{line}  {about}" if about else line)
    print()

    chosen = next((row for row in results if row["data_available"].get("maps")), None)
    if chosen is None:
        print("none of these has map data yet")
        return 0

    # One request brings the teams, the veto, the maps and the player lines.
    match = client.matches.get(
        chosen["id"], include=["teams", "vetoes", "maps", "stats", "players"]
    )
    names = {team["id"]: team["name"] for team in (match["team_a"], match["team_b"])}
    print(f"{score(match, match['team_a']['name'], match['team_b']['name'])} in depth:")
    steps = [
        " ".join(filter(None, (names.get(step["team_id"]), step["choice_type"], step["map_name"])))
        for step in match["vetoes"]
    ]
    if steps:
        print("  veto:", ", ".join(steps))
    for played in match["maps"]:
        print(
            f"  map {played['map_number']}: {played['map_name']} "
            f"{played['score_a']}-{played['score_b']}"
        )

    # Whole-match lines have no map_number; the per-map lines sit alongside them.
    nickname = {player["id"]: player["nickname"] for player in match["players"]}
    whole_match = [line for line in match["stats"] if line["map_number"] is None]
    top = sorted(whole_match, key=lambda line: line["kills"], reverse=True)[:3]
    if top:
        print(
            "  most kills:",
            ", ".join(f"{nickname.get(line['player_id'], '?')} {line['kills']}" for line in top),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
