"""Keep a local copy of changed matches current, using the change feed.

    ESPORTSODDS_API_KEY=... python examples/follow_changes.py state.json
    ESPORTSODDS_API_KEY=... python examples/follow_changes.py state.json --every 600

The state file holds the newest `changed_at` seen and the latest copy of every match that has
changed since you started, by id. A pass asks only for what changed since then, so a quiet hour
costs one request. With --every N it runs a pass every N seconds until you stop it.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from esportsodds import Client, QuotaExceededError


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return state
    start = datetime.now(timezone.utc) - timedelta(days=1)
    return {"since": start.isoformat(), "matches": {}}


def save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temporary, path)  # a reader never sees half a file


def one_pass(client: Client, state: dict[str, Any]) -> int:
    changed = 0
    for match in client.matches.iter_changes(state["since"]):
        state["matches"][match["id"]] = match
        state["since"] = match["changed_at"]  # rows arrive in changed_at order
        changed += 1
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("state", type=Path, help="the JSON state file")
    parser.add_argument("--every", type=float, default=None, help="seconds between passes")
    args = parser.parse_args(argv)

    client = Client()
    state = load(args.state)
    while True:
        try:
            changed = one_pass(client, state)
        except QuotaExceededError as error:
            print("out of requests until", error.reset_at)
            return 1
        save(args.state, state)
        print(f"{changed} changed; {len(state['matches'])} matches held; since {state['since']}")
        if args.every is None:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
