"""The package as an importable, typed, well-behaved library."""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from importlib import resources
from pathlib import Path

import esportsodds
from esportsodds import _version


def test_every_public_name_resolves() -> None:
    missing = [name for name in esportsodds.__all__ if not hasattr(esportsodds, name)]
    assert missing == []


def test_the_public_names_are_unique_and_sorted_for_review() -> None:
    names = esportsodds.__all__
    assert len(names) == len(set(names))
    assert names == sorted(names)


def test_the_version_is_one_well_formed_string() -> None:
    assert esportsodds.__version__ == _version.__version__
    parts = esportsodds.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_the_package_ships_its_type_marker() -> None:
    # Without py.typed, type checkers treat the package as untyped and ignore all its annotations.
    assert resources.files("esportsodds").joinpath("py.typed").is_file()


def test_every_exception_derives_from_one_base() -> None:
    errors = [
        getattr(esportsodds, name)
        for name in esportsodds.__all__
        if name.endswith("Error") and name != "EsportsOddsError"
    ]
    assert len(errors) >= 12
    for cls in errors:
        assert issubclass(cls, esportsodds.EsportsOddsError), cls


def test_the_library_adds_a_null_handler_and_nothing_else_to_logging() -> None:
    # Python prints a "no handlers" notice for a library that logs with none configured.
    logger = logging.getLogger("esportsodds")
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)
    assert logger.level == logging.NOTSET  # the application decides the level
    assert logger.propagate is True


def test_importing_the_package_prints_and_warns_about_nothing() -> None:
    # A fresh interpreter, with warnings turned into errors: a deprecated call in the package
    # (Python deprecates stdlib APIs every release) fails here instead of in a user's logs.
    done = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import esportsodds"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert done.stderr == ""


def test_there_are_no_runtime_dependencies() -> None:
    # The install pulls in nothing: every module the package imports is standard library.
    root = Path(esportsodds.__file__).parent
    third_party: set[str] = set()
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            third_party |= {n for n in names if n not in sys.stdlib_module_names}
    assert third_party == set()
