"""Static smoke test: every prd PowerShell script parses cleanly.

Mirrors the contract exercised by the bash ``bash -n`` check. Used
by the development loop on Windows where pwsh subprocess hangs. Lives
under ``tests/extensions/prd/`` so it is picked up by the standard
pytest collection.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = [
    "extensions/prd/scripts/powershell/prd_plan.ps1",
    "extensions/prd/scripts/powershell/prd_validate.ps1",
    "extensions/prd/scripts/powershell/prd_orchestrate.ps1",
    "extensions/prd/scripts/powershell/prd-common.ps1",
]

PWSH = (
    shutil.which("pwsh")
    or shutil.which("powershell.exe")
    or shutil.which("powershell")
)


@pytest.mark.skipif(
    not PWSH, reason="pwsh/PowerShell not available"
)
@pytest.mark.parametrize("rel", SCRIPTS)
def test_powershell_script_parses(rel: str) -> None:
    abs_path = (PROJECT_ROOT / rel).resolve()
    ps_script = (
        f"$c = Get-Content -LiteralPath '{abs_path}' -Raw;"
        "$null = [System.Management.Automation.Language.Parser]"
        "::ParseInput($c, [ref]$null, [ref]$null);"
        f"Write-Host '{rel} OK'"
    )
    r = subprocess.run(
        [PWSH, "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert f"{rel} OK" in r.stdout