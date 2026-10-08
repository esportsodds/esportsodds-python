"""Follow match updates over the WebSocket, reconnecting when the connection drops.

    pip install websockets
    ESPORTSODDS_API_KEY=... python examples/watch_matches.py MATCH_ID [MATCH_ID ...]

The WebSocket costs no requests. Frames are `snapshot` (a match's full current state) and `update`
(a change); `seq` counts up per match, and a gap means a frame was missed, so the match is
subscribed again to get a fresh snapshot. Every reconnect mints a new ticket and starts from the
snapshots, because there is no replay.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import sys
from collections.abc import Sequence
from typing import Any

from esportsodds import Client


def subscribe_frame(match_ids: Sequence[str]) -> str:
    return json.dumps({"type": "subscribe", "data": {"match_ids": list(match_ids)}})


def missed_a_frame(frame: dict[str, Any], last_seq: dict[str, int]) -> bool:
    """Record the frame's `seq` for its match; True when the previous one was skipped."""
    match_id, seq = frame.get("match_id"), frame.get("seq")
    if not isinstance(match_id, str) or not isinstance(seq, int):
        return False
    previous = last_seq.get(match_id)
    last_seq[match_id] = seq
    return frame.get("type") == "update" and previous is not None and seq != previous + 1


async def watch(
    client: Client, match_ids: Sequence[str], *, connections: int | None = None
) -> None:
    """Follow `match_ids`; `connections` bounds how many times to connect (None: for ever)."""
    import websockets  # pip install websockets

    delay = 1.0
    attempt = 0
    while connections is None or attempt < connections:
        attempt += 1
        try:
            ticket = await asyncio.to_thread(client.ws_token)
            async with websockets.connect(client.ws_url(ticket)) as socket:
                delay = 1.0
                last_seq: dict[str, int] = {}
                await socket.send(subscribe_frame(match_ids))
                async for raw in socket:
                    frame = json.loads(raw)
                    if frame["type"] == "error":
                        print("server error:", frame["data"])
                        break
                    if frame["type"] in ("snapshot", "update"):
                        print(frame["match_id"], frame["type"], frame["seq"])
                    if missed_a_frame(frame, last_seq):
                        await socket.send(subscribe_frame([frame["match_id"]]))
        except (OSError, websockets.ConnectionClosed) as error:
            print("connection lost:", error)
        if connections is not None and attempt >= connections:
            break
        await asyncio.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311 - jitter, not security
        delay = min(delay * 2, 60.0)


def main(argv: Sequence[str] | None = None) -> int:
    match_ids = list(sys.argv[1:] if argv is None else argv)
    if not match_ids:
        print("usage: watch_matches.py MATCH_ID [MATCH_ID ...]")
        return 2
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(watch(Client(), match_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
