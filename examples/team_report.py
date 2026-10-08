"""A team's recent form, head-to-head record and latest rating.

    ESPORTSODDS_API_KEY=... python examples/team_report.py team-a --opponent team-b

Teams are addressed by slug here; an id works too. Five requests in all.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from esportsodds import Client, NotFoundError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("team", help="the team's slug or id")
    parser.add_argument("--opponent", required=True, help="the opponent's slug or id")
    args = parser.parse_args(argv)

    client = Client()
    try:
        team = client.teams.get(args.team)
        opponent = client.teams.get(args.opponent)
    except NotFoundError:
        print("no such team; check the slug")
        return 1

    print(team["name"])
    results = client.teams.form(args.team, limit=5).data
    print(
        "  form, newest first:", " ".join("W" if row["won"] else "L" for row in results) or "none"
    )

    record = client.teams.h2h(args.team, opponent=args.opponent)
    print(f"  against {opponent['name']}: {record['team_wins']}-{record['opponent_wins']}")

    points = client.teams.ratings(args.team, limit=1).data
    if points:
        print(f"  latest rating: {points[-1]['rating']:.0f} (as of {points[-1]['as_of']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
