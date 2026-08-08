"""Contract test: every bundled extension must ship inside the wheel.

Mirrors ``test_wheel_bundled_presets.py`` for the extensions force-include
list. Any extension marked ``bundled: true`` in ``extensions/catalog.json``
must have a corresponding entry under
``[tool.hatch.build.targets.wheel.force-include]`` so the wheel ships it
under ``specify_cli/core_pack/extensions/<id>/``.

Regression guard: this catches the case where a new bundled extension is
added to ``catalog.json`` (and registered in ``pyproject.toml``) but the
force-include line is forgotten, leaving the wheel build without the
extension's commands or scripts.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _force_include() -> dict[str, str]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)
    return pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def _bundled_extension_ids() -> list[str]:
    catalog = json.loads(
        (REPO_ROOT / "extensions" / "catalog.json").read_text(encoding="utf-8")
    )
    return sorted(
        ext_id
        for ext_id, entry in catalog["extensions"].items()
        if entry.get("bundled")
    )


def test_every_bundled_extension_is_force_included():
    force_include = _force_include()
    bundled = _bundled_extension_ids()

    assert bundled, "expected at least one bundled extension in extensions/catalog.json"
    for ext_id in bundled:
        assert force_include.get(f"extensions/{ext_id}") == (
            f"specify_cli/core_pack/extensions/{ext_id}"
        ), f"bundled extension '{ext_id}' is missing from the wheel force-include list"


def test_prd_is_bundled_and_shipped():
    """Regression guard for the PRD-to-Plans extension shipped at v0.16."""
    assert "prd" in _bundled_extension_ids()
    assert _force_include()["extensions/prd"] == (
        "specify_cli/core_pack/extensions/prd"
    )