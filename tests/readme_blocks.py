"""Reading README.md as data: its sections, fenced code blocks and tables.

The README makes promises (a quickstart that runs, a table of every method, which errors are
retried). tests/test_readme.py holds each one to the code, and this is the small reader it uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


@dataclass(frozen=True)
class Block:
    lang: str
    code: str
    section: str  # the `##` heading the block sits under ("" before the first)
    line: int  # 1-based line of the opening fence


def read() -> str:
    return README.read_text(encoding="utf-8")


def headings(text: str) -> list[str]:
    """The `##` headings, in order (fenced code is skipped: a `#` comment is not a heading)."""
    found: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            found.append(line[3:].strip())
    return found


def sections(text: str) -> dict[str, str]:
    """Each `##` section's body text, by heading."""
    result: dict[str, list[str]] = {}
    current: str | None = None
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = line[3:].strip()
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return {name: "\n".join(lines) for name, lines in result.items()}


def blocks(text: str) -> list[Block]:
    found: list[Block] = []
    section = ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            lang = line[3:].strip()
            start = i + 1
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                i += 1
            found.append(Block(lang, "\n".join(lines[start:i]) + "\n", section, start))
        elif line.startswith("## "):
            section = line[3:].strip()
        i += 1
    return found


def tables(body: str) -> list[list[list[str]]]:
    """Every markdown table in `body`: rows of stripped cells, header and separator rows removed."""
    result: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in [*body.splitlines(), ""]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            current.append(cells)
        elif current:
            result.append(current[2:])  # the header row and the `| --- |` separator
            current = []
    return result


def code_spans(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)
