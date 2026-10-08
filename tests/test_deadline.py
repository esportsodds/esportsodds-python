"""The per-test deadline in conftest.py really stops an endless loop.

A guard that never fires would be worse than none: it would read as protection. So this runs a
throwaway test file that spins forever, in a fresh pytest, with the deadline set to one second, and
checks that pytest reports it failed on the deadline well before any outer timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGALRM"), reason="the deadline needs SIGALRM (not available on Windows)"
)

SPINNER = """
def test_spins_forever():
    while True:
        pass
"""


def run_pytest(tmp_path: Path, deadline: str) -> tuple[subprocess.CompletedProcess[str], float]:
    # The spinner gets the same conftest as the real suite, so it runs under the real fixture.
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text((Path(__file__).parent / "conftest.py").read_text())
    (tests / "test_spin.py").write_text(SPINNER)
    env = dict(os.environ, EO_TEST_DEADLINE=deadline, PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return done, time.monotonic() - started


def test_an_endless_loop_fails_on_the_deadline_instead_of_hanging(tmp_path: Path) -> None:
    done, took = run_pytest(tmp_path, "1")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "deadline" in done.stdout
    assert "1 failed" in done.stdout
    assert took < 30  # a few seconds of interpreter start-up, not a hang


def test_the_default_deadline_is_generous_enough_for_the_real_suite() -> None:
    from .conftest import DEADLINE_SECONDS

    assert DEADLINE_SECONDS >= 5  # the slowest real test is well under a second
