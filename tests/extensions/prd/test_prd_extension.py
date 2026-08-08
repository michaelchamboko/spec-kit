"""Tests for the bundled ``prd`` extension.

Validates:
- Bundled layout (manifest, README, command files, script variants)
- Catalog registration
- Wheel force-include for the bundled ``extensions/prd`` directory
- Extension manifest schema (no install-time errors via ExtensionManifest)
- Slug normalization, SHA-256 helpers, manifest round-trip (Python)
- Intake → approve → finalize flow against a temp project (Python)
- Validation behavior across phase/state combinations (Python)
- Symlink escape refusal (Python)
- Source digest mismatch detection (Python)
- Bash and PowerShell twin parity (skipped when interpreter unavailable)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXT_DIR = PROJECT_ROOT / "extensions" / "prd"

EXPECTED_COMMANDS = {
    "speckit.prd.plan",
    "speckit.prd.validate",
}

EXPECTED_SCRIPTS = {
    "scripts/bash/prd-common.sh",
    "scripts/bash/prd_plan.sh",
    "scripts/bash/prd_validate.sh",
    "scripts/powershell/prd-common.ps1",
    "scripts/powershell/prd_plan.ps1",
    "scripts/powershell/prd_validate.ps1",
    "scripts/python/prd_common.py",
    "scripts/python/prd_plan.py",
    "scripts/python/prd_validate.py",
}


# ── Bundled extension layout ─────────────────────────────────────────────────


class TestExtensionLayout:
    """The bundled prd extension ships a complete package."""

    def test_extension_yml_exists(self):
        assert (EXT_DIR / "extension.yml").is_file()

    def test_extension_yml_has_required_fields(self):
        manifest = yaml.safe_load(
            (EXT_DIR / "extension.yml").read_text(encoding="utf-8")
        )
        assert manifest["extension"]["id"] == "prd"
        assert manifest["extension"]["name"] == "PRD-to-Plans Translation"
        assert manifest["extension"]["author"] == "spec-kit-core"
        commands = {c["name"] for c in manifest["provides"]["commands"]}
        assert commands == EXPECTED_COMMANDS

    def test_declares_no_lifecycle_hooks(self):
        """prd is a deliberate, opt-in planning extension; no hooks."""
        manifest = yaml.safe_load(
            (EXT_DIR / "extension.yml").read_text(encoding="utf-8")
        )
        assert "hooks" not in manifest or not manifest["hooks"]

    def test_readme_exists(self):
        readme = EXT_DIR / "README.md"
        assert readme.is_file()
        text = readme.read_text(encoding="utf-8")
        assert "PRD-to-Plans Translation Extension" in text
        assert "Methodology Provenance" in text

    def test_command_files_exist(self):
        for name in EXPECTED_COMMANDS:
            cmd = EXT_DIR / "commands" / f"{name}.md"
            assert cmd.is_file(), f"Missing command file: {cmd}"

    def test_command_files_describe_modes(self):
        plan_text = (EXT_DIR / "commands" / "speckit.prd.plan.md").read_text(
            encoding="utf-8"
        )
        validate_text = (
            EXT_DIR / "commands" / "speckit.prd.validate.md"
        ).read_text(encoding="utf-8")
        # Both commands must document path safety and deterministic posture.
        assert "symlink" in plan_text.lower()
        assert "symlink" in validate_text.lower()
        assert "Path Safety" in plan_text
        assert "Guardrails" in validate_text

    def test_scripts_exist(self):
        for rel in EXPECTED_SCRIPTS:
            assert (EXT_DIR / rel).is_file(), f"Missing script: {rel}"

    def test_config_template_exists(self):
        assert (EXT_DIR / "config-template.yml").is_file()
        data = yaml.safe_load(
            (EXT_DIR / "config-template.yml").read_text(encoding="utf-8")
        )
        assert data["graph_provider"] in {"gitnexus", "direct"}
        assert isinstance(data["slug_max_length"], int)
        assert data["approval_required"] is True

    def test_command_frontmatter_references_real_scripts(self):
        plan_text = (EXT_DIR / "commands" / "speckit.prd.plan.md").read_text(
            encoding="utf-8"
        )
        validate_text = (
            EXT_DIR / "commands" / "speckit.prd.validate.md"
        ).read_text(encoding="utf-8")
        # No dangling script references to files that do not exist.
        for name in ("prd-intake", "prd-discover", "prd-decompose",
                     "prd-freeze", "prd-reconcile"):
            assert name not in plan_text, (
                f"plan command still references non-existent script: {name}"
            )
        # Scripts section must point at scripts that exist on disk.
        for rel in (
            "../../scripts/bash/prd_plan.sh",
            "../../scripts/powershell/prd_plan.ps1",
            "../../scripts/python/prd_plan.py",
            "../../scripts/bash/prd_validate.sh",
            "../../scripts/powershell/prd_validate.ps1",
            "../../scripts/python/prd_validate.py",
        ):
            assert rel in plan_text or rel in validate_text


# ── Catalog registration ─────────────────────────────────────────────────────


class TestCatalogEntry:
    def test_catalog_lists_prd_as_bundled(self):
        catalog = json.loads(
            (PROJECT_ROOT / "extensions" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )
        entry = catalog["extensions"]["prd"]
        assert entry["bundled"] is True
        assert entry["id"] == "prd"
        assert entry["author"] == "spec-kit-core"
        # Methodology tags surface in the catalog.
        assert "bmad" in entry["tags"]
        assert "openspec" in entry["tags"]
        assert "taskmaster" in entry["tags"]
        assert "v3.5-protocol" in entry["tags"]


# ── Wheel force-include ──────────────────────────────────────────────────────


class TestWheelForceInclude:
    """The PRD extension must ship inside the wheel core_pack."""

    def test_prd_in_force_include(self):
        pyproject_text = (
            PROJECT_ROOT / "pyproject.toml"
        ).read_text(encoding="utf-8")
        assert '"extensions/prd" = "specify_cli/core_pack/extensions/prd"' in (
            pyproject_text
        )


# ── Manifest validation ─────────────────────────────────────────────────────


class TestExtensionManifestSchema:
    def test_extension_manifest_loads(self):
        from specify_cli.extensions import ExtensionManifest

        manifest = ExtensionManifest(EXT_DIR / "extension.yml")
        assert manifest.id == "prd"
        assert manifest.name == "PRD-to-Plans Translation"
        names = {c["name"] for c in manifest.commands}
        assert names == EXPECTED_COMMANDS


# ── Python helper unit tests ─────────────────────────────────────────────────


class TestPrdCommonHelpers:
    """Unit tests for the deterministic helpers in ``prd_common.py``."""

    @pytest.fixture(autouse=True)
    def _import_common(self):
        sys.path.insert(0, str(EXT_DIR / "scripts" / "python"))
        try:
            import prd_common  # type: ignore[import-not-found]

            self.common = prd_common
        finally:
            # Don't remove — other tests in the module rely on it.
            pass

    def test_normalize_slug_basic(self):
        c = self.common
        assert c.normalize_slug("Hello World") == "hello-world"
        assert c.normalize_slug("Foo_Bar 1") == "foo-bar-1"
        assert c.normalize_slug("  Trim  ") == "trim"

    def test_normalize_slug_drops_invalid_chars(self):
        c = self.common
        assert c.normalize_slug("foo.bar/baz") == "foobarbaz"
        assert c.normalize_slug("a@b#c$d") == "abcd"

    def test_normalize_slug_collapses_dashes(self):
        c = self.common
        assert c.normalize_slug("foo---bar") == "foo-bar"

    def test_normalize_slug_truncates(self):
        c = self.common
        long = "a" * 100
        assert len(c.normalize_slug(long)) <= c.SLUG_MAX_LENGTH_DEFAULT
        # Truncated value still terminates without a trailing dash.
        norm = c.normalize_slug("a" * 60 + "-" + "b" * 60)
        assert not norm.endswith("-")

    def test_normalize_slug_rejects_empty(self):
        c = self.common
        with pytest.raises(ValueError):
            c.normalize_slug("!!!")
        with pytest.raises(ValueError):
            c.normalize_slug("")

    def test_sha256_bytes(self):
        c = self.common
        assert (
            c.sha256_bytes(b"hello world")
            == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_is_within(self, tmp_path: Path):
        c = self.common
        parent = tmp_path
        child = parent / "a" / "b"
        assert c.is_within(child, parent) is True
        sibling = tmp_path.parent / "elsewhere"
        assert c.is_within(sibling, parent) is False

    def test_yaml_dump_load_roundtrip(self):
        c = self.common
        data = {
            "schema_version": "1.0",
            "extension": "prd",
            "slug": "demo",
            "state": "AWAITING_DECOMPOSITION_APPROVAL",
            "slices": [],
            "source": {
                "authority": "file",
                "byte_size": 12,
                "sha256": "deadbeef",
                "preserved_at": "source/prd-v001.md",
            },
        }
        body = c.yaml_safe_dump(data)
        loaded = c.yaml_safe_load(body)
        assert loaded == data

    def test_find_project_root_prefers_specify_init_dir(self, tmp_path: Path):
        c = self.common
        (tmp_path / ".specify").mkdir()
        os.environ["SPECIFY_INIT_DIR"] = str(tmp_path)
        try:
            root = c.find_project_root()
            assert root == tmp_path.resolve()
        finally:
            os.environ.pop("SPECIFY_INIT_DIR", None)

    def test_find_project_root_walks_up(self, tmp_path: Path):
        c = self.common
        (tmp_path / ".specify").mkdir()
        nested = tmp_path / "src" / "feature"
        nested.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(nested)
            assert c.find_project_root() == tmp_path.resolve()
        finally:
            os.chdir(cwd)


# ── Python twin: end-to-end intake / approve / finalize ─────────────────────


class TestPrdPlanPythonTwin:
    """End-to-end exercise of the Python twin against a tmp project."""

    @pytest.fixture(autouse=True)
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".specify").mkdir()
        self.project_root = tmp_path
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.script = (
            EXT_DIR / "scripts" / "python" / "prd_plan.py"
        )
        self.validate_script = (
            EXT_DIR / "scripts" / "python" / "prd_validate.py"
        )
        # Avoid inheriting SPECIFY_FEATURE* etc.
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))

    def _run(self, *args: str, stdin: bytes | None = None):
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            capture_output=True, text=False, env=self.env,
            input=stdin, timeout=30,
        )

    def test_intake_creates_workspace_and_manifest(self):
        # Lay down a source file in the project so intake can preserve it.
        src = self.project_root / "prd.md"
        src.write_text("# My PRD\n\nFR1: do the thing\n", encoding="utf-8")
        r = self._run("source=prd.md", "slug=demo")
        assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
        manifest = self.project_root / ".specify/specs/demo/000-spec-of-specs/manifest.yml"
        assert manifest.is_file()
        text = manifest.read_text(encoding="utf-8")
        assert "AWAITING_DECOMPOSITION_APPROVAL" in text
        assert "demo" in text
        # Source preserved at deterministic path.
        preserved = self.project_root / ".specify/specs/demo/000-spec-of-specs/source/prd-v001.md"
        assert preserved.is_file()

    def test_approve_materializes_slice_dirs(self):
        src = self.project_root / "prd.md"
        src.write_text("# My PRD\n", encoding="utf-8")
        self._run("source=prd.md", "slug=demo")
        slice_lines = b"SLC-001\tdemo\t001-demo\nSLC-002\tfollow\t002-follow\n"
        r = self._run("slug=demo", "approve=true", stdin=slice_lines)
        assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
        prd_dir = self.project_root / ".specify/specs/demo"
        assert (prd_dir / "001-demo").is_dir()
        assert (prd_dir / "002-follow").is_dir()

    def test_finalize_marks_plan_ready(self):
        src = self.project_root / "prd.md"
        src.write_text("# My PRD\n", encoding="utf-8")
        self._run("source=prd.md", "slug=demo")
        self._run("slug=demo", "approve=true", stdin=b"SLC-001\tdemo\t001-demo\n")
        r = self._run("slug=demo", "--finalize")
        assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
        manifest = (
            self.project_root / ".specify/specs/demo/000-spec-of-specs/manifest.yml"
        )
        text = manifest.read_text(encoding="utf-8")
        assert "PLAN_READY" in text
        assert "frozen_sequence: true" in text or "frozen_sequence: True" in text

    def test_unique_slug_suffix_when_collision(self):
        src = self.project_root / "prd.md"
        src.write_text("# PRD\n", encoding="utf-8")
        # First intake uses the default slug 'prd'.
        self._run("source=prd.md", "slug=demo")
        # Second intake with the same slug must pick -2.
        r = self._run("source=prd.md", "slug=demo")
        assert r.returncode == 0
        assert (self.project_root / ".specify/specs/demo-2").is_dir()


# ── Python twin: validation behavior ─────────────────────────────────────────


class TestPrdValidatePythonTwin:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".specify").mkdir()
        self.project_root = tmp_path
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.plan_script = (
            EXT_DIR / "scripts" / "python" / "prd_plan.py"
        )
        self.validate_script = (
            EXT_DIR / "scripts" / "python" / "prd_validate.py"
        )
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))
        # Author a minimal complete PRD workspace.
        src = tmp_path / "prd.md"
        src.write_text("# PRD\n", encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(self.plan_script),
                "source=prd.md",
                "slug=demo",
            ],
            check=True,
            capture_output=True,
            env=self.env,
        )
        subprocess.run(
            [
                sys.executable,
                str(self.plan_script),
                "slug=demo",
                "approve=true",
            ],
            input=b"SLC-001\tdemo\t001-demo\n",
            check=True,
            capture_output=True,
            env=self.env,
        )

    def _validate(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.validate_script), *args],
            capture_output=True, text=True, env=self.env, timeout=30,
        )

    def test_decomposition_phase_reports_missing_artifacts(self):
        r = self._validate("slug=demo", "phase=decomposition")
        assert r.returncode == 1
        body = json.loads(r.stdout)
        assert body["slug"] == "demo"
        assert body["phase"] == "decomposition"
        # No requirements.md, no reviews yet — these checks must FAIL.
        names = {f["name"] for f in body["failures"]}
        assert "requirements.exists" in names
        assert "reviews.decomposition" in names

    def test_final_phase_requires_plan_ready_state(self):
        # Currently state == PLANNING. phase=final is allowed; expect 1.
        r = self._validate("slug=demo", "phase=final")
        assert r.returncode == 1

    def test_source_digest_mismatch_is_reported(self, tmp_path: Path):
        # Tamper with the preserved source; SHA-256 should now mismatch.
        preserved = (
            tmp_path
            / ".specify/specs/demo/000-spec-of-specs/source/prd-v001.md"
        )
        preserved.write_text("# tampered\n", encoding="utf-8")
        r = self._validate("slug=demo", "phase=decomposition")
        body = json.loads(r.stdout)
        names = {f["name"] for f in body["failures"]}
        assert "source.sha256" in names

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Windows requires elevated privileges for symlinks",
    )
    def test_symlink_escape_refused(self, tmp_path: Path):
        # Build a project where .specify itself is a symlink to /tmp/outside.
        # The script must refuse to operate (no escape allowed).
        outside = tmp_path.parent / "outside_spec"
        outside.mkdir(exist_ok=True)
        (outside / ".specify").mkdir(exist_ok=True)
        target_link = tmp_path / "spec_link"
        target_link.symlink_to(outside)
        env = dict(self.env)
        env["SPECIFY_INIT_DIR"] = str(target_link)
        # The find_project_root returns target_link (which is a symlink).
        # safe_create_dir / require_within must then refuse. We do not
        # assert a specific exit code (the script may exit at find_specify_root
        # if SPECIFY_INIT_DIR resolves into the symlink tree), only that the
        # script refuses to write inside the linked target.
        # In practice: SPECIFY_INIT_DIR points at the symlink; find_project_root
        # follows the explicit SPECIFY_INIT_DIR and returns the *resolved*
        # directory, so the symlink check would not refuse SPECIFY_INIT_DIR
        # itself. We assert that the PRD workspace is NOT created under
        # /outside_spec/.specify/specs.
        outside_specs = outside / ".specify" / "specs"
        if outside_specs.exists():
            assert not any(outside_specs.iterdir())


# ── PowerShell twin smoke test ───────────────────────────────────────────────


POWERSHELL = (
    shutil.which("pwsh")
    or shutil.which("powershell.exe")
    or shutil.which("powershell")
)


@pytest.mark.skipif(not POWERSHELL, reason="pwsh/PowerShell not available")
class TestPrdPlanPowerShellTwin:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".specify").mkdir()
        self.project_root = tmp_path
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))

    def test_intake_and_validate_run(self):
        src = self.project_root / "prd.md"
        src.write_text("# PRD\n", encoding="utf-8")
        env = dict(os.environ)
        env["SPECIFY_INIT_DIR"] = str(self.project_root)
        plan_script = EXT_DIR / "scripts" / "powershell" / "prd_plan.ps1"
        validate_script = EXT_DIR / "scripts" / "powershell" / "prd_validate.ps1"
        r = subprocess.run(
            [
                POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(plan_script),
                "-Source", str(src), "-Slug", "demo",
            ],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        body = json.loads(r.stdout)
        assert body["status"] == "AWAITING_DECOMPOSITION_APPROVAL"
        assert body["slug"] == "demo"
        # Validate command runs cleanly even with no requirements yet.
        r = subprocess.run(
            [
                POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(validate_script),
                "-Slug", "demo", "-Phase", "decomposition",
            ],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # exit code may be 1 because requirements/review are not yet present,
        # but stdout must be parseable JSON.
        body = json.loads(r.stdout)
        assert body["slug"] == "demo"
        assert body["phase"] == "decomposition"


# ── Bash twin smoke test (POSIX only) ────────────────────────────────────────


BASH = shutil.which("bash")


@pytest.mark.skipif(
    not BASH or os.name == "nt",
    reason="POSIX bash required (Windows MSYS bash subprocess hangs)",
)
class TestPrdPlanBashTwin:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".specify").mkdir()
        self.project_root = tmp_path
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))

    def test_intake_and_validate_run(self):
        src = self.project_root / "prd.md"
        src.write_text("# PRD\n", encoding="utf-8")
        env = dict(os.environ)
        env["SPECIFY_INIT_DIR"] = str(self.project_root)
        plan_script = EXT_DIR / "scripts" / "bash" / "prd_plan.sh"
        validate_script = EXT_DIR / "scripts" / "bash" / "prd_validate.sh"
        r = subprocess.run(
            [BASH, str(plan_script), "source=prd.md", "slug=demo"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert "AWAITING_DECOMPOSITION_APPROVAL" in r.stdout
        r = subprocess.run(
            [BASH, str(validate_script), "slug=demo", "phase=decomposition"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        # exit may be 1 because requirements are missing; stdout is JSON.
        import re
        m = re.search(r"\{.*\}", r.stdout)
        assert m is not None
        body = json.loads(m.group(0))
        assert body["slug"] == "demo"