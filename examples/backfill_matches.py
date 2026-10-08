"""Pull completed matches, with their maps and player stats, into a JSON Lines file. Resumable.

    ESPORTSODDS_API_KEY=... python examples/backfill_matches.py matches.jsonl --since 2026-01-01

Each request is one bulk page: 25 matches with the sub-resources embedded, and one request
against your allowance however much a page carries. After each page the file is flushed and the
page's `next_cursor` is saved beside it (matches.jsonl.cursor), so a run that stops, or crashes,
carries on from there. A crash between the two writes can repeat one page, so de-duplicate on `id`
when you load the file.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from esportsodds import Client


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("output", type=Path, help="the JSON Lines file to append to")
    parser.add_argument("--since", default="2026-01-01", help="oldest scheduled day, YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=None, help="stop after this many pages")
    args = parser.parse_args(argv)

    cursor_file = args.output.with_name(args.output.name + ".cursor")
    cursor = cursor_file.read_text(encoding="utf-8").strip() if cursor_file.exists() else None
    if cursor:
        print("resuming from the saved cursor")

    client = Client()
    written = 0
    pages = client.matches.pages_bulk(
        status="completed",
        date_from=args.since,
        include=["maps", "stats"],
        cursor=cursor,
        max_pages=args.max_pages,
    )
    with args.output.open("a", encoding="utf-8") as out:
        for page in pages:
            for match in page.data:
                out.write(json.dumps(match, separators=(",", ":")) + "\n")
                written += 1
            out.flush()
            if page.next_cursor:
                cursor_file.write_text(page.next_cursor, encoding="utf-8")
            else:
                cursor_file.unlink(missing_ok=True)  # the walk is complete
    print(f"wrote {written} matches to {args.output}")
    info = client.last_response
    if info is not None:
        print(info.rate_limit.quota_remaining, "requests left this month")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
