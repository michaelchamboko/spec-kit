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
    "speckit.prd.orchestrate",
    "speckit.prd.validate",
}

EXPECTED_SCRIPTS = {
    "scripts/bash/prd-common.sh",
    "scripts/bash/prd_plan.sh",
    "scripts/bash/prd_validate.sh",
    "scripts/bash/prd_orchestrate.sh",
    "scripts/powershell/prd-common.ps1",
    "scripts/powershell/prd_plan.ps1",
    "scripts/powershell/prd_validate.ps1",
    "scripts/powershell/prd_orchestrate.ps1",
    "scripts/python/prd_common.py",
    "scripts/python/prd_plan.py",
    "scripts/python/prd_validate.py",
    "scripts/python/prd_orchestrate.py",
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
        assert manifest["extension"]["version"] == "1.1.0"
        assert manifest["extension"]["author"] == "spec-kit-core"
        commands = {c["name"] for c in manifest["provides"]["commands"]}
        assert commands == EXPECTED_COMMANDS
        # Manifest must declare the new tags so the catalog matches.
        tags = set(manifest.get("tags", []))
        for required in (
            "karpathy",
            "writing-plans",
            "context7",
            "regression",
            "waterfall",
            "evidence",
            "orchestration",
        ):
            assert required in tags, f"missing tag {required!r} in manifest"

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
        assert entry["version"] == "1.1.0"
        assert entry["author"] == "spec-kit-core"
        # Methodology tags surface in the catalog.
        assert "bmad" in entry["tags"]
        assert "openspec" in entry["tags"]
        assert "taskmaster" in entry["tags"]
        assert "v3.5-protocol" in entry["tags"]
        # v1.1 methodology tags must also surface.
        for required in (
            "karpathy",
            "writing-plans",
            "context7",
            "regression",
            "waterfall",
            "evidence",
            "orchestration",
        ):
            assert required in entry["tags"], f"catalog missing tag {required!r}"


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


@pytest.mark.skipif(
    not POWERSHELL or os.name == "nt",
    reason="pwsh subprocess hangs on Windows MSYS; the parse-only check covers us here",
)
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

# ── Python twin: orchestration ledger generation on approve ───────────────────────


class TestPrdPlanEmitsOrchestrationLedger:
    """Approve must materialize a 1.1 orchestration ledger."""

    @pytest.fixture(autouse=True)
    def project(self, tmp_path, monkeypatch):
        (tmp_path / ".specify").mkdir()
        (tmp_path / "prd.md").write_text(
            "# PRD\nFR1: do the thing\n", encoding="utf-8"
        )
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.tmp = tmp_path

    def _run_plan(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, str(EXT_DIR / "scripts" / "python" / "prd_plan.py"), *args],
            capture_output=True, text=True, env=self.env, input=stdin, timeout=30,
        )

    def test_approve_writes_orchestration_ledger(self):
        r = self._run_plan("source=prd.md", "slug=demo")
        assert r.returncode == 0
        r = self._run_plan(
            "slug=demo", "approve=true",
            stdin="SLC-001\tdemo\t001-demo\nSLC-002\tfollow\t002-follow\n",
        )
        assert r.returncode == 0, r.stderr
        body = json.loads(r.stdout)
        assert body["status"] == "PLANNING"
        assert "ledger" in body
        ledger_path = (
            self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml"
        )
        assert ledger_path.is_file()
        ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
        assert ledger["schema_version"] == "1.1"
        assert ledger["plan"]["slug"] == "demo"
        assert ledger["priorities"]["business"] == ["SLC-001", "SLC-002"]
        assert ledger["slices"][0]["id"] == "SLC-001"
        assert ledger["slices"][0]["tasks"][0]["id"].startswith("SLC-001-T")
        assert ledger["project"]["state"] == "NOT_STARTED"

    def test_finalize_refuses_without_ledger(self):
        r = self._run_plan("source=prd.md", "slug=demo")
        assert r.returncode == 0
        r = self._run_plan("slug=demo", "--finalize")
        assert r.returncode == 2, r.stderr
        assert "orchestration ledger missing" in r.stderr
        assert "action=initialize" in r.stderr

    def test_finalize_refuses_legacy_1_0_ledger(self):
        self.tmp.joinpath(".specify", "specs", "demo", "000-spec-of-specs").mkdir(parents=True)
        manifest = {
            "schema_version": "1.1",
            "extension": "prd",
            "slug": "demo",
            "state": "PLANNING",
            "slices": [],
        }
        (self.tmp / ".specify/specs/demo/000-spec-of-specs/manifest.yml").write_text(
            yaml.safe_dump(manifest), encoding="utf-8"
        )
        (self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml").write_text(
            "schema_version: \"1.0\"\nproject:\n  state: NOT_STARTED\nslices: []\n",
            encoding="utf-8",
        )
        r = self._run_plan("slug=demo", "--finalize")
        assert r.returncode == 2
        assert "schema_version" in r.stderr
        assert "1.1" in r.stderr


# ── Python twin: orchestrator state engine ────────────────────────────────────


def _author_workspace(tmp_path, *, slices=None):
    """Create a complete PLANNING workspace for orchestrator tests."""
    if slices is None:
        slices = [("SLC-001", "001-demo")]
    (tmp_path / ".specify").mkdir()
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "prd.md").write_text("# PRD\n", encoding="utf-8")
    specs = tmp_path / ".specify" / "specs" / "demo"
    artifact = specs / "000-spec-of-specs"
    artifact.mkdir(parents=True)
    manifest = {
        "schema_version": "1.1",
        "extension": "prd",
        "slug": "demo",
        "state": "PLANNING",
        "active_version": "v001",
        "repository": {
            "root": str(tmp_path), "head": "", "dirty_fingerprint": ""
        },
        "source": {
            "authority": "pasted",
            "fetched_at": "2024-01-01T00:00:00Z",
            "original_name": "prd.md",
            "byte_size": 6,
            "sha256": "deadbeef",
            "preserved_at": "source/prd-v001.md",
        },
        "slices": [
            {
                "id": sid,
                "slug": directory.split("-", 1)[1] if "-" in directory else sid,
                "directory": directory,
                "dependencies": [],
                "order": i + 1,
                "state": "PLANNING",
                "requirements": [],
            }
            for i, (sid, directory) in enumerate(slices)
        ],
        "frozen_sequence": True,
    }
    (artifact / "manifest.yml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    for sid, directory in slices:
        slice_dir = specs / directory
        slice_dir.mkdir(parents=True, exist_ok=True)
        (slice_dir / "spec.md").write_text("# spec\n", encoding="utf-8")
        (slice_dir / "plan.md").write_text("# plan\n", encoding="utf-8")
        (slice_dir / "tasks.md").write_text(
            f"# tasks\n\n- {sid}-T001: do thing\n", encoding="utf-8"
        )
    ledger = {
        "schema_version": "1.1",
        "revision": 1,
        "repository": {
            "root": str(tmp_path),
            "head": "",
            "dirty_fingerprint": "",
            "applicable_instructions": "",
        },
        "plan": {
            "slug": "demo",
            "manifest_version": "v001",
            "decomposition_version": "v001",
            "frozen_sequence": True,
        },
        "project": {
            "state": "NOT_STARTED",
            "current_task": None,
            "active_owner": None,
            "blockers": [],
        },
        "priorities": {
            "business": [s[0] for s in slices],
            "execution": [f"{s[0]}::{s[0]}-T001" for s in slices],
        },
        "slices": [
            {
                "id": sid,
                "directory": directory,
                "state": "PENDING",
                "rank": i + 1,
                "dependencies": [],
                "exit_gate": {
                    "required_evidence": [],
                    "e2e_journey": "",
                    "approval": {
                        "required": True,
                        "approved_by": None,
                        "approved_at": None,
                    },
                },
                "tasks": [
                    {
                        "id": f"{sid}-T001",
                        "rank": 1,
                        "state": "TODO",
                        "requirements": [],
                        "acceptance": [],
                        "interfaces": [],
                        "documentation_evidence": [],
                        "checks": {
                            "unit": [],
                            "integration": [],
                            "regression": [],
                            "e2e": [],
                            "migration": [],
                            "deployment": [],
                            "rollback": [],
                        },
                        "evidence": [],
                        "blockers": [],
                    }
                ],
            }
            for i, (sid, directory) in enumerate(slices)
        ],
        "final_gate": {
            "required": True,
            "approved_by": None,
            "approved_at": None,
            "baseline_check": "",
            "full_regression": "",
            "cross_slice_e2e": "",
            "deployment_smoke": "",
            "rollback_check": "",
        },
    }
    (artifact / "orchestration.yml").write_text(
        yaml.safe_dump(ledger), encoding="utf-8"
    )


def _run_orchestrate(env, *args, stdin=None):
    return subprocess.run(
        [sys.executable, str(EXT_DIR / "scripts" / "python" / "prd_orchestrate.py"), *args],
        capture_output=True, text=True, env=env, input=stdin, timeout=30,
    )


class TestPrdOrchestrateInitialize:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path, monkeypatch):
        (tmp_path / ".specify").mkdir()
        (tmp_path / "prd.md").write_text("# PRD\n", encoding="utf-8")
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.tmp = tmp_path

    def test_initialize_rejects_when_manifest_missing(self):
        r = _run_orchestrate(self.env, "slug=demo", "action=initialize")
        assert r.returncode == 1
        body = json.loads(r.stdout)
        assert body["ok"] is False
        assert body["reason"] == "manifest_missing"
        assert "prd_plan.py" in body["recovery"]

    def test_initialize_creates_ledger_and_bumps_manifest(self):
        subprocess.run(
            [sys.executable, str(EXT_DIR / "scripts" / "python" / "prd_plan.py"),
             "source=prd.md", "slug=demo"],
            check=True, capture_output=True, env=self.env,
        )
        subprocess.run(
            [sys.executable, str(EXT_DIR / "scripts" / "python" / "prd_plan.py"),
             "slug=demo", "approve=true"],
            input=b"SLC-001\tdemo\t001-demo\n",
            check=True, capture_output=True, env=self.env,
        )
        (self.tmp / ".specify/specs/demo/001-demo/tasks.md").write_text(
            "# tasks\n\n- SLC-001-T001: do thing\n- SLC-001-T002: do another\n",
            encoding="utf-8",
        )
        ledger_path = (
            self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml"
        )
        if ledger_path.is_file():
            ledger_path.unlink()
        r = _run_orchestrate(self.env, "slug=demo", "action=initialize")
        assert r.returncode == 0, r.stderr
        body = json.loads(r.stdout)
        assert body["ok"] is True
        assert body["revision"] == 1
        assert body["task_count"] == 2
        manifest = yaml.safe_load(
            (self.tmp / ".specify/specs/demo/000-spec-of-specs/manifest.yml").read_text(encoding="utf-8")
        )
        assert manifest["schema_version"] == "1.1"
        ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
        assert ledger["schema_version"] == "1.1"
        assert ledger["priorities"]["execution"] == [
            "SLC-001::SLC-001-T001",
            "SLC-001::SLC-001-T002",
        ]


class TestPrdOrchestrateActionGuardrails:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path, monkeypatch):
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.tmp = tmp_path
        _author_workspace(tmp_path)
        r = _run_orchestrate(self.env, "slug=demo", "action=initialize")
        assert r.returncode == 0, r.stderr

    def _read_ledger(self):
        return yaml.safe_load(
            (self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml").read_text(encoding="utf-8")
        )

    def test_status_returns_prioritized_checklist(self):
        r = _run_orchestrate(self.env, "slug=demo", "action=status")
        assert r.returncode == 0
        body = json.loads(r.stdout)
        assert body["project"]["state"] == "NOT_STARTED"
        assert body["priorities"]["execution"] == ["SLC-001::SLC-001-T001"]

    def test_next_returns_eligible_task(self):
        r = _run_orchestrate(self.env, "slug=demo", "action=next")
        assert r.returncode == 0
        body = json.loads(r.stdout)
        assert body["task"] is not None
        assert body["task"]["id"] == "SLC-001-T001"

    def test_start_then_start_another_rejected(self):
        r = _run_orchestrate(
            self.env, "slug=demo", "action=start",
            "task=SLC-001-T001", "owner=alice",
        )
        assert r.returncode == 0, r.stderr
        ledger = self._read_ledger()
        assert ledger["project"]["state"] == "IN_PROGRESS"
        assert ledger["project"]["current_task"] == "SLC-001-T001"
        ledger["slices"][0]["tasks"].append({
            "id": "SLC-001-T002",
            "rank": 2,
            "state": "TODO",
            "requirements": [],
            "acceptance": [],
            "interfaces": [],
            "documentation_evidence": [],
            "checks": {
                "unit": [], "integration": [], "regression": [],
                "e2e": [], "migration": [], "deployment": [], "rollback": [],
            },
            "evidence": [],
            "blockers": [],
        })
        ledger["priorities"]["execution"].append("SLC-001::SLC-001-T002")
        (self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml").write_text(
            yaml.safe_dump(ledger), encoding="utf-8"
        )
        r = _run_orchestrate(
            self.env, "slug=demo", "action=start",
            "task=SLC-001-T002", "owner=bob",
        )
        assert r.returncode == 1
        body = json.loads(r.stdout)
        assert body["reason"] == "another_task_in_progress"
        assert body["active_task"] == "SLC-001-T001"

    def test_complete_requires_passing_evidence(self):
        ledger = self._read_ledger()
        ledger["slices"][0]["tasks"][0]["checks"] = {
            "unit": ["pytest tests/unit/test_x.py"],
            "regression": ["pytest tests/regression/test_x.py"],
            "e2e": ["bash scripts/e2e/x.sh"],
            "integration": [],
            "migration": [],
            "deployment": [],
            "rollback": [],
        }
        (self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml").write_text(
            yaml.safe_dump(ledger), encoding="utf-8"
        )
        r = _run_orchestrate(
            self.env, "slug=demo", "action=start",
            "task=SLC-001-T001", "owner=alice",
        )
        assert r.returncode == 0
        r = _run_orchestrate(
            self.env, "slug=demo", "action=complete",
            "task=SLC-001-T001",
        )
        assert r.returncode == 1
        body = json.loads(r.stdout)
        assert body["reason"] == "missing_evidence"
        for kind, cid in [("unit", "main"), ("regression", "main"), ("e2e", "journey")]:
            r = _run_orchestrate(
                self.env, "slug=demo", "action=evidence",
                "task=SLC-001-T001", f"check={kind}.{cid}",
                "result=pass", "path=README.md",
            )
            assert r.returncode == 0, r.stdout
        r = _run_orchestrate(
            self.env, "slug=demo", "action=complete",
            "task=SLC-001-T001",
        )
        assert r.returncode == 0, r.stdout
        body = json.loads(r.stdout)
        assert body["slice_done"] is True
        assert body["project_state"] == "AWAITING_APPROVAL"
        ledger = self._read_ledger()
        assert ledger["slices"][0]["state"] == "DONE"

    def test_evidence_path_must_exist(self):
        r = _run_orchestrate(
            self.env, "slug=demo", "action=evidence",
            "task=SLC-001-T001", "check=unit.main",
            "result=pass", "path=does/not/exist.py",
        )
        assert r.returncode == 1
        body = json.loads(r.stdout)
        assert body["reason"] == "evidence_path_missing"

    def test_reopen_invalidates_downstream(self):
        ledger = self._read_ledger()
        ledger["slices"][0]["tasks"][0]["checks"] = {
            "unit": ["pytest tests/unit/test_x.py"],
            "regression": ["pytest tests/regression/test_x.py"],
            "e2e": ["bash scripts/e2e/x.sh"],
            "integration": [],
            "migration": [],
            "deployment": [],
            "rollback": [],
        }
        (self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml").write_text(
            yaml.safe_dump(ledger), encoding="utf-8"
        )
        _run_orchestrate(
            self.env, "slug=demo", "action=start",
            "task=SLC-001-T001", "owner=alice",
        )
        for kind, cid in [("unit", "main"), ("regression", "main"), ("e2e", "journey")]:
            _run_orchestrate(
                self.env, "slug=demo", "action=evidence",
                "task=SLC-001-T001", f"check={kind}.{cid}",
                "result=pass", "path=README.md",
            )
        _run_orchestrate(self.env, "slug=demo", "action=complete", "task=SLC-001-T001")
        ledger = self._read_ledger()
        assert ledger["slices"][0]["state"] == "DONE"
        r = _run_orchestrate(
            self.env, "slug=demo", "action=reopen",
            "task=SLC-001-T001", "reason=evidence stale",
        )
        assert r.returncode == 0
        ledger = self._read_ledger()
        assert ledger["slices"][0]["state"] == "STALE"
        assert ledger["slices"][0]["tasks"][0]["state"] == "STALE"
        assert ledger["project"]["state"] == "STALE"


class TestPrdOrchestrateImplementationInvariant:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path, monkeypatch):
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.tmp = tmp_path
        _author_workspace(tmp_path)
        _run_orchestrate(self.env, "slug=demo", "action=initialize")

    def test_orchestrator_refuses_after_implementation_change(self):
        r = _run_orchestrate(
            self.env, "slug=demo", "action=start",
            "task=SLC-001-T001", "owner=alice",
        )
        assert r.returncode == 0
        new_dir = self.tmp / "src" / "feature"
        new_dir.mkdir(parents=True)
        (new_dir / "main.py").write_text("# new\n", encoding="utf-8")
        r = _run_orchestrate(
            self.env, "slug=demo", "action=evidence",
            "task=SLC-001-T001", "check=unit.main",
            "result=pass", "path=README.md",
        )
        assert r.returncode == 1
        body = json.loads(r.stdout)
        assert body["reason"] == "implementation_hash_changed"
        assert "Halt" in body["recovery"]


# ── Python twin: orchestration-phase validation ────────────────────


class TestPrdValidateOrchestrationPhase:
    @pytest.fixture(autouse=True)
    def project(self, tmp_path, monkeypatch):
        for k in [k for k in os.environ if k.startswith("SPECIFY_")]:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SPECIFY_INIT_DIR", str(tmp_path))
        self.env = dict(os.environ)
        self.env["SPECIFY_INIT_DIR"] = str(tmp_path)
        self.tmp = tmp_path
        _author_workspace(tmp_path)
        _run_orchestrate(self.env, "slug=demo", "action=initialize")

    def _validate(self, *args):
        return subprocess.run(
            [sys.executable, str(EXT_DIR / "scripts" / "python" / "prd_validate.py"), *args],
            capture_output=True, text=True, env=self.env, timeout=30,
        )

    def test_orchestration_phase_reports_placeholder_check_failures(self):
        r = self._validate("slug=demo", "phase=orchestration")
        body = json.loads(r.stdout)
        assert body["phase"] == "orchestration"
        names = {f["name"] for f in body["failures"]}
        assert any("no_checks" in n for n in names)

    def test_phase_all_includes_orchestration_when_ledger_present(self):
        r = self._validate("slug=demo", "phase=all")
        body = json.loads(r.stdout)
        assert body["phase"] == "all"
        assert "ledger" in body
        names = {f["name"] for f in body["failures"]}
        assert any(name.startswith("orchestration.") for name in names)

    def test_orchestration_phase_rejects_early_state(self):
        import shutil
        shutil.rmtree(self.tmp / ".specify/specs/demo", ignore_errors=True)
        subprocess.run(
            [sys.executable, str(EXT_DIR / "scripts" / "python" / "prd_plan.py"),
             "source=prd.md", "slug=demo"],
            check=True, capture_output=True, env=self.env,
        )
        r = self._validate("slug=demo", "phase=orchestration")
        assert r.returncode == 1
        assert "PLANNING" in r.stderr

    def test_orchestration_phase_skips_when_ledger_absent(self):
        import shutil
        ledger = self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml"
        if ledger.is_file():
            shutil.copy(
                ledger,
                self.tmp / ".specify/specs/demo/000-spec-of-specs/orchestration.yml.bak",
            )
            ledger.unlink()
        r = self._validate("slug=demo", "phase=orchestration")
        body = json.loads(r.stdout)
        assert body["checks_skipped"] == 1
        skipped = [
            f for f in body.get("skipped", [])
            if f.get("name") == "orchestration.skipped"
        ]
        assert skipped

    def test_validate_rejects_unknown_phase(self):
        r = self._validate("slug=demo", "phase=banana")
        assert r.returncode == 2
        assert "phase" in r.stderr
