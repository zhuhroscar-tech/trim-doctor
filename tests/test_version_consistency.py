"""Guard against version drift between pyproject.toml and __init__.py.

This class of bug has been found and fixed twice already in sibling repos
(reboot-safety-check, usbsmart-doctor): pyproject.toml's [project] version
gets bumped for a release but __init__.py's __version__ (what `--version`
actually prints) is left stale, so the wheel's package metadata says one
version while the CLI's own --version output says another. This test makes
that drift fail CI immediately instead of depending on a manual post-release
smoke test to catch it.

Deliberately avoids tomllib/tomli (tomllib is 3.11+ only, and this CI matrix
still tests py3.9) by parsing the single `version = "..."` line under
[project] with a plain regex -- no extra dependency needed for a one-line,
well-known-format field.
"""
from __future__ import annotations

import re
from pathlib import Path

from trim_doctor import __version__

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def test_init_version_matches_pyproject_version():
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject_path.read_text()
    match = _VERSION_RE.search(text)
    assert match, 'Could not find `version = "..."` in pyproject.toml'
    pyproject_version = match.group(1)
    assert __version__ == pyproject_version, (
        f"__init__.py __version__ ({__version__!r}) does not match "
        f"pyproject.toml's [project].version ({pyproject_version!r}). "
        "These must be bumped together on every release."
    )
