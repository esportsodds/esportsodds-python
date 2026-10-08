"""Upcoming matches with their current market line.

    ESPORTSODDS_API_KEY=... python examples/upcoming_with_odds.py

One request for the fixtures, then one per match for its board: six requests for five matches.
The prices are our derived market line (source "eo_market"), not any bookmaker's price.
"""

from __future__ import annotations

from esportsodds import Client


def main() -> None:
    client = Client()
    for match in client.matches.list(status="scheduled", limit=5).data:
        print(match["scheduled_at"], match["team_a_name"], "vs", match["team_b_name"])
        board = client.odds.list(match=match["id"], latest=True, market="match_winner")
        if not board.data:
            print("    no market line yet")
        for line in board.data:
            print(f"    {line['label']}: {line['price']}")
    info = client.last_response
    if info is not None:
        print(info.rate_limit.quota_remaining, "requests left this month")


if __name__ == "__main__":
    main()
