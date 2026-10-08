"""The README quickstart: the next five scheduled matches.

ESPORTSODDS_API_KEY=... python examples/quickstart.py
"""

from esportsodds import Client

client = Client()  # reads ESPORTSODDS_API_KEY from the environment

for match in client.matches.list(status="scheduled", limit=5).data:
    print(match["scheduled_at"], match["team_a_name"], "vs", match["team_b_name"])
