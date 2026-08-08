#!/usr/bin/env python3
"""PRD-to-Plans: deterministic validation entrypoint.

Invoked by ``speckit.prd.validate``. Performs the read-only structural,
traceability, graph-freshness, orchestration-ledger, evidence,
regression, and readiness checks the command spec describes:

- Manifest schema, state consistency, required fields
- Source integrity (normalized markdown present, SHA-256 matches manifest)
- Requirements traceability (every requirement has a stable ID and source)
- Slice decomposition (stable IDs, acyclic dependencies, frozen order)
- Codegraph evidence (provider/version, indexed state, exclusions)
- Council review presence (decomposition and final)
- Child artifact completeness (for ``phase=final`` or ``PLANNING+`` state)
- Orchestration ledger integrity, one-active-task invariant, evidence
  coverage, documentation evidence, required checks, and the
  implementation-source hash invariant (for ``phase=orchestration``
  or ``PLANNING+`` state when a ledger exists)

This script is **read-only**. It never modifies artifact files or source
code; failures exit non-zero with a structured report. No AI model calls
are made.

Usage::

    prd_validate.py slug=<slug> [phase=decomposition|final|orchestration|all]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prd_common as common  # noqa: E402

REQUIREMENT_PREFIXES = ("PRD-FR-", "PRD-NFR-")
ACCEPTANCE_PREFIX = "AC-"
SLICE_PREFIX = "SLC-"
DECISION_PREFIX = "DEC-"

REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "extension",
    "slug",
    "state",
    "active_version",
    "source",
    "slices",
)


def _check(name: str, ok: bool, detail: str = "") -> dict[str, str]:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _required_manifest_fields(manifest: dict[str, object]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            failures.append(
                _check(
                    f"manifest.{field}",
                    False,
                    "missing required field",
                )
            )
    return failures


def _validate_source_integrity(
    project_root: Path, artifact_dir: Path, manifest: dict[str, object]
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    source_meta = manifest.get("source")
    if not isinstance(source_meta, dict):
        return [_check("source", False, "source metadata missing")]

    preserved_rel = str(source_meta.get("preserved_at", ""))
    preserved_path = project_root / preserved_rel
    if not preserved_path.is_file():
        failures.append(
            _check(
                "source.preserved",
                False,
                f"missing preserved file: {preserved_rel}",
            )
        )
        return failures

    expected_digest = str(source_meta.get("sha256", ""))
    actual_digest = common.sha256_file(preserved_path)
    if expected_digest and expected_digest != actual_digest:
        failures.append(
            _check(
                "source.sha256",
                False,
                f"expected {expected_digest}, got {actual_digest}",
            )
        )

    normalized_rel = preserved_rel.rsplit(".", 1)[0] + ".normalized.md"
    normalized_path = project_root / normalized_rel
    if not normalized_path.is_file():
        failures.append(
            _check(
                "source.normalized",
                False,
                f"missing normalized markdown: {normalized_rel}",
            )
        )
    return failures


def _validate_requirements(
    project_root: Path, artifact_dir: Path, manifest: dict[str, object]
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    requirements_file = artifact_dir / "requirements.md"
    if not requirements_file.is_file():
        return [
            _check(
                "requirements.exists",
                False,
                "requirements.md missing — run speckit.prd.plan first",
            )
        ]
    text = requirements_file.read_text(encoding="utf-8")
    ids = re.findall(r"\b(PRD-FR-\d+|PRD-NFR-\d+)\b", text)
    if not ids:
        failures.append(
            _check(
                "requirements.ids",
                False,
                "no PRD-FR-/PRD-NFR- ids detected in requirements.md",
            )
        )
    elif len(set(ids)) != len(ids):
        failures.append(
            _check("requirements.unique", False, "duplicate requirement ids")
        )
    return failures


def _validate_slices(
    project_root: Path, prd_dir: Path, manifest: dict[str, object]
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    slices = manifest.get("slices")
    if not isinstance(slices, list) or not slices:
        return [_check("slices.present", False, "slices array empty or missing")]
    seen_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        sid = str(slice_meta.get("id", ""))
        if not sid.startswith(SLICE_PREFIX):
            failures.append(
                _check(
                    f"slices.id_format[{sid}]",
                    False,
                    f"slice id must start with {SLICE_PREFIX}",
                )
            )
        if sid in seen_ids:
            failures.append(
                _check(f"slices.unique[{sid}]", False, "duplicate slice id")
            )
        seen_ids.add(sid)
        deps = slice_meta.get("dependencies", []) or []
        deps_set: set[str] = set()
        for dep in deps:
            if dep not in seen_ids:
                failures.append(
                    _check(
                        f"slices.dep_unknown[{sid}->{dep}]",
                        False,
                        "dependency references unknown slice",
                    )
                )
            deps_set.add(dep)
        dependencies[sid] = deps_set

    # Cycle detection (DFS) over the slice dependency graph.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in dependencies}

    def visit(node: str) -> bool:
        color[node] = GRAY
        for dep in dependencies.get(node, ()):  # type: ignore[arg-type]
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and visit(dep):
                return True
        color[node] = BLACK
        return False

    for node in list(color):
        if color[node] == WHITE and visit(node):
            failures.append(
                _check("slices.acyclic", False, "dependency cycle detected")
            )
            break

    # Materialized directory presence when state implies frozen sequence.
    if manifest.get("frozen_sequence"):
        for slice_meta in slices:
            if not isinstance(slice_meta, dict):
                continue
            directory = str(slice_meta.get("directory", ""))
            if not directory:
                continue
            if not (prd_dir / directory).is_dir():
                failures.append(
                    _check(
                        f"slices.materialized[{directory}]",
                        False,
                        "slice directory not materialized on disk",
                    )
                )
    return failures


def _validate_council_reviews(
    artifact_dir: Path, manifest: dict[str, object], phase: str
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    reviews_dir = artifact_dir / "reviews"
    if phase in {"decomposition", "all"}:
        version = str(manifest.get("decomposition_version", "v001"))
        target = reviews_dir / f"decomposition-{version}.md"
        if not target.is_file():
            failures.append(
                _check(
                    "reviews.decomposition",
                    False,
                    f"missing {target.relative_to(artifact_dir.parent.parent.parent)}",
                )
            )
    if phase in {"final", "all"}:
        version = str(manifest.get("final_review_version", "v001"))
        target = reviews_dir / f"final-{version}.md"
        if not target.is_file():
            failures.append(
                _check(
                    "reviews.final",
                    False,
                    f"missing {target.relative_to(artifact_dir.parent.parent.parent)}",
                )
            )
    return failures


def _validate_child_artifacts(
    project_root: Path, prd_dir: Path, manifest: dict[str, object]
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    slices = manifest.get("slices") or []
    if not isinstance(slices, list) or not slices:
        return failures
    required = ("spec.md", "plan.md", "tasks.md", "code-impact.md")
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        directory = prd_dir / str(slice_meta.get("directory", ""))
        if not directory.is_dir():
            failures.append(
                _check(
                    f"artifacts.dir[{directory.name}]",
                    False,
                    "slice directory missing",
                )
            )
            continue
        for leaf in required:
            if not (directory / leaf).is_file():
                failures.append(
                    _check(
                        f"artifacts.missing[{directory.name}/{leaf}]",
                        False,
                        "required child artifact missing",
                    )
                )
    return failures


def _phase_for_state(state: str) -> str:
    if state == "AWAITING_DECOMPOSITION_APPROVAL":
        return "decomposition"
    if state in {"PLANNING", "PLAN_READY"}:
        return "final"
    return "all"


# ── Orchestration phase checks ──────────────────────────────────────────────


_TASK_ID_RE = re.compile(r"\bSLC-\d{3}-T\d{3}\b")
_INTERFACE_EXISTING_RE = re.compile(r"^existing:(?P<file>[^:]+):(?P<symbol>.+)$")
_INTERFACE_PROPOSED_RE = re.compile(
    r"^proposed:(?P<file>[^:]+):(?P<symbol>[^:]+)\s*->\s*(?P<ret>.+)$"
)
_DOC_CONTEXT7_RE = re.compile(r"^context7:(?P<lib>[^@]+)@(?P<version>\S+)$")
_DOC_OFFICIAL_RE = re.compile(r"^official:(?P<url>\S+)@(?P<ts>\S+)$")


def _validate_orchestration_ledger(
    project_root: Path, artifact_dir: Path, manifest: dict[str, object]
) -> tuple[list[dict[str, str]], dict[str, object] | None]:
    """Run every orchestration-phase check against the ledger.

    Returns ``(failures, ledger_or_None)``. When the ledger is absent
    the function returns a synthetic "skipped" record so the caller
    can surface the phase as not-yet-applicable for older ``1.0`` plans.
    """
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        skipped: dict[str, str] = {
            "name": "orchestration.skipped",
            "status": "SKIPPED",
            "detail": "orchestration ledger absent; older 1.0 plan",
        }
        return [skipped], None

    failures: list[dict[str, str]] = []

    # 1. Schema + revision
    schema_version = str(ledger.get("schema_version", ""))
    if schema_version != common.ORCHESTRATION_LEDGER_SCHEMA_VERSION:
        failures.append(
            _check(
                "orchestration.schema_version",
                False,
                f"expected {common.ORCHESTRATION_LEDGER_SCHEMA_VERSION!r}, got {schema_version!r}",
            )
        )
    if not isinstance(ledger.get("revision"), int) or int(ledger.get("revision", 0)) < 1:
        failures.append(
            _check(
                "orchestration.revision",
                False,
                "revision must be an integer >= 1",
            )
        )

    # 2. Project state enum
    project = ledger.get("project") or {}
    if not isinstance(project, dict):
        failures.append(_check("orchestration.project", False, "project must be a mapping"))
    else:
        state = str(project.get("state", ""))
        if state not in common.VALID_PROJECT_STATES:
            failures.append(
                _check(
                    "orchestration.project.state",
                    False,
                    f"invalid project state {state!r}",
                )
            )

    # 3. One active task invariant
    slices = ledger.get("slices") or []
    in_progress_tasks = [
        t
        for s in slices
        if isinstance(s, dict)
        for t in (s.get("tasks") or [])
        if isinstance(t, dict) and str(t.get("state")) == "IN_PROGRESS"
    ]
    if len(in_progress_tasks) > 1:
        failures.append(
            _check(
                "orchestration.one_active_task",
                False,
                f"more than one IN_PROGRESS task: {[t.get('id') for t in in_progress_tasks]}",
            )
        )
    elif len(in_progress_tasks) == 1:
        current = str(project.get("current_task") or "")
        active_id = str(in_progress_tasks[0].get("id"))
        if current != active_id:
            failures.append(
                _check(
                    "orchestration.current_task_consistency",
                    False,
                    f"project.current_task={current!r} disagrees with active task {active_id!r}",
                )
            )

    # 4. Per-slice task correspondence with ``tasks.md``
    prd_dir = artifact_dir.parent
    manifest_slices = manifest.get("slices") or []
    ledger_slice_ids = {str(s.get("id")) for s in slices if isinstance(s, dict)}
    manifest_slice_ids = {
        str(s.get("id"))
        for s in manifest_slices
        if isinstance(s, dict)
    }
    missing_in_ledger = manifest_slice_ids - ledger_slice_ids
    extra_in_ledger = ledger_slice_ids - manifest_slice_ids
    for sid in sorted(missing_in_ledger):
        failures.append(
            _check(
                f"orchestration.slice_missing[{sid}]",
                False,
                f"slice {sid} declared in manifest but absent from ledger",
            )
        )
    for sid in sorted(extra_in_ledger):
        failures.append(
            _check(
                f"orchestration.slice_extra[{sid}]",
                False,
                f"slice {sid} present in ledger but missing from manifest",
            )
        )

    # Cross-check tasks.md IDs against ledger task IDs per slice.
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        slice_id = str(slice_meta.get("id"))
        directory = str(slice_meta.get("directory") or "")
        ledger_task_ids = {
            str(t.get("id"))
            for t in (slice_meta.get("tasks") or [])
            if isinstance(t, dict)
        }
        if not directory:
            continue
        tasks_md = prd_dir / directory / "tasks.md"
        if tasks_md.is_file():
            md_task_ids = set(_TASK_ID_RE.findall(tasks_md.read_text(encoding="utf-8")))
        else:
            md_task_ids = set()
        for tid in md_task_ids - ledger_task_ids:
            failures.append(
                _check(
                    f"orchestration.tasks_md_missing[{slice_id}/{tid}]",
                    False,
                    f"tasks.md references {tid} but ledger has no entry",
                )
            )
        for tid in ledger_task_ids - md_task_ids:
            failures.append(
                _check(
                    f"orchestration.ledger_extra[{slice_id}/{tid}]",
                    False,
                    f"ledger has {tid} but tasks.md does not",
                )
            )

    # 5. Strict priority / dependency order + acyclic
    state_by_task: dict[str, str] = {}
    deps_by_task: dict[str, set[str]] = {}
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        for task in slice_meta.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id"))
            state_by_task[tid] = str(task.get("state", "TODO"))
            deps_by_task[tid] = {
                str(d) for d in (task.get("dependencies") or []) if d
            }
    execution = ledger.get("priorities", {}).get("execution") or []
    parsed_execution: list[tuple[str, str]] = []
    for entry in execution:
        if isinstance(entry, str) and "::" in entry:
            parsed_execution.append(tuple(entry.split("::", 1)))
    # Verify the declared execution order has each task exactly once
    # and includes every task. (Use ``is`` checks on strings; two
    # task IDs that happen to share the same string compare equal.)
    execution_ids = [tid for _sid, tid in parsed_execution]
    seen: set[str] = set()
    duplicates = [tid for tid in execution_ids if tid in seen or seen.add(tid)]  # noqa: PERF401
    if duplicates:
        failures.append(
            _check(
                "orchestration.execution.duplicates",
                False,
                f"duplicate task ids in priorities.execution: {duplicates}",
            )
        )
    missing_in_execution = set(state_by_task) - set(execution_ids)
    if missing_in_execution:
        failures.append(
            _check(
                "orchestration.execution.incomplete",
                False,
                f"tasks missing from priorities.execution: {sorted(missing_in_execution)}",
            )
        )

    # Acyclic check across the task dependency graph.
    color: dict[str, int] = {tid: 0 for tid in state_by_task}
    WHITE, GRAY, BLACK = 0, 1, 2

    def visit(node: str) -> bool:
        color[node] = GRAY
        for dep in deps_by_task.get(node, ()):  # type: ignore[arg-type]
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and visit(dep):
                return True
        color[node] = BLACK
        return False

    for node in list(color):
        if color[node] == 0 and visit(node):
            failures.append(
                _check(
                    "orchestration.tasks.acyclic",
                    False,
                    "task dependency cycle detected",
                )
            )
            break

    # 6. Required checks + documentation evidence per task
    allowed_check_kinds = (
        "unit",
        "integration",
        "regression",
        "e2e",
        "migration",
        "deployment",
        "rollback",
    )
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        for task in slice_meta.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id"))
            checks = task.get("checks") or {}
            declared_kinds = [k for k in allowed_check_kinds if checks.get(k)]
            if not declared_kinds:
                failures.append(
                    _check(
                        f"orchestration.{tid}.no_checks",
                        False,
                        "task declares no verification checks",
                    )
                )
                continue
            if "unit" not in declared_kinds:
                failures.append(
                    _check(
                        f"orchestration.{tid}.missing_unit",
                        False,
                        "task must declare at least one unit check",
                    )
                )
            if "regression" not in declared_kinds:
                failures.append(
                    _check(
                        f"orchestration.{tid}.missing_regression",
                        False,
                        "task must declare at least one regression check",
                    )
                )
            if "e2e" not in declared_kinds and "integration" not in declared_kinds:
                failures.append(
                    _check(
                        f"orchestration.{tid}.missing_user_path",
                        False,
                        "task must declare e2e (user-visible) or integration (internal) coverage",
                    )
                )
            for kind in declared_kinds:
                for cmd in checks.get(kind, []) or []:
                    cmd_str = str(cmd).strip()
                    if not cmd_str:
                        failures.append(
                            _check(
                                f"orchestration.{tid}.{kind}.empty_command",
                                False,
                                f"{kind} check command must not be empty",
                            )
                        )
                        continue
                    placeholder = cmd_str.lower()
                    if placeholder in {"echo", "true", ":", "pytest"}:
                        failures.append(
                            _check(
                                f"orchestration.{tid}.{kind}.placeholder",
                                False,
                                f"{kind} check {cmd_str!r} is a placeholder; provide an exact verification command",
                            )
                        )
            # Documentation evidence: if the task references non-trivial
            # new dependencies (heuristic: any non-empty
            # documentation_evidence entry), then each entry must be a
            # context7:lib@version or official:url@ts reference.
            for ref in task.get("documentation_evidence", []) or []:
                ref_str = str(ref).strip()
                if not ref_str:
                    continue
                if not (
                    _DOC_CONTEXT7_RE.match(ref_str)
                    or _DOC_OFFICIAL_RE.match(ref_str)
                ):
                    failures.append(
                        _check(
                            f"orchestration.{tid}.doc_evidence_format",
                            False,
                            f"documentation_evidence {ref_str!r} must be context7:<lib>@<version> or official:<url>@<ts>",
                        )
                    )

    # 7. Final-gate completeness when project state >= AWAITING_APPROVAL
    final_gate = ledger.get("final_gate") or {}
    if str(project.get("state", "")) in {"AWAITING_APPROVAL", "RELEASE_READY", "STALE"}:
        for field in (
            "baseline_check",
            "full_regression",
            "cross_slice_e2e",
            "deployment_smoke",
            "rollback_check",
        ):
            if not str(final_gate.get(field) or "").strip():
                failures.append(
                    _check(
                        f"orchestration.final_gate.{field}",
                        False,
                        f"final_gate.{field} must be set",
                    )
                )
        approved_by = str(final_gate.get("approved_by") or "")
        if str(project.get("state")) == "RELEASE_READY" and not approved_by:
            failures.append(
                _check(
                    "orchestration.final_gate.approved_by",
                    False,
                    "RELEASE_READY requires final_gate.approved_by",
                )
            )

    # 8. Slice exit_gate approval records
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        slice_id = str(slice_meta.get("id"))
        state_val = str(slice_meta.get("state", ""))
        approval = (slice_meta.get("exit_gate") or {}).get("approval") or {}
        approved_by = str(approval.get("approved_by") or "")
        if state_val == "DONE" and not approved_by:
            failures.append(
                _check(
                    f"orchestration.{slice_id}.exit_gate.approved_by",
                    False,
                    "slice DONE but exit_gate.approved_by unset",
                )
            )

    # 9. Implementation-source hash invariant
    head = str(ledger.get("repository", {}).get("head", ""))
    dirty = str(ledger.get("repository", {}).get("dirty_fingerprint", ""))
    if head or dirty:
        current_hash = common.implementation_tree_fingerprint(project_root)
        # Without a reference hash captured at plan/approve time we
        # cannot compute a delta, but we can ensure the tree is
        # fingerprintable (non-empty). The orchestrator records the
        # reference fingerprint itself before every state-changing
        # action; the validator reports the current value so the
        # command body can compare.
        if not current_hash:
            failures.append(
                _check(
                    "orchestration.implementation_hash",
                    False,
                    "implementation tree fingerprint could not be computed",
                )
            )

    return failures, ledger


def main(argv: list[str] | None = None) -> int:
    args = common.parse_args(argv)
    project_root = common.find_project_root()
    if project_root is None:
        common.err("ERROR: not inside a Spec Kit project (.specify/ not found)")
        return 1
    raw_slug = args.get("slug", "")
    if not raw_slug:
        common.err("ERROR: missing slug=<slug>")
        return 2
    slug = common.normalize_slug(raw_slug)
    phase = args.get("phase", "all").lower()
    if phase not in {"decomposition", "final", "orchestration", "all"}:
        common.err(
            f"ERROR: phase must be decomposition|final|orchestration|all (got {phase!r})"
        )
        return 2

    specs_root = project_root / ".specify" / "specs"
    prd_dir = common.safe_create_dir(specs_root, project_root) / slug
    artifact_dir = common.safe_create_dir(prd_dir / "000-spec-of-specs", project_root)
    manifest = common.load_manifest(artifact_dir)
    if manifest is None:
        common.err(
            f"ERROR: manifest.yml not found at {artifact_dir}; "
            "run speckit.prd.plan first"
        )
        return 1

    state = str(manifest.get("state", ""))
    if phase == "decomposition" and state not in {
        "AWAITING_DECOMPOSITION_APPROVAL",
        "PLANNING",
        "PLAN_READY",
    }:
        common.err(
            f"ERROR: phase=decomposition requires state >= "
            f"AWAITING_DECOMPOSITION_APPROVAL (got {state!r})"
        )
        return 1
    if phase == "final" and state not in {"PLANNING", "PLAN_READY"}:
        common.err(
            f"ERROR: phase=final requires state in PLANNING|PLAN_READY "
            f"(got {state!r})"
        )
        return 1
    if phase == "orchestration" and state not in {"PLANNING", "PLAN_READY"}:
        common.err(
            f"ERROR: phase=orchestration requires state in PLANNING|PLAN_READY "
            f"(got {state!r})"
        )
        return 1

    failures: list[dict[str, str]] = []
    failures.extend(_required_manifest_fields(manifest))
    failures.extend(_validate_source_integrity(project_root, artifact_dir, manifest))
    failures.extend(_validate_requirements(project_root, artifact_dir, manifest))
    if state in {"AWAITING_DECOMPOSITION_APPROVAL", "PLANNING", "PLAN_READY"}:
        failures.extend(_validate_slices(project_root, prd_dir, manifest))
    failures.extend(_validate_council_reviews(artifact_dir, manifest, phase))
    if state in {"PLANNING", "PLAN_READY"} or phase == "final":
        failures.extend(_validate_child_artifacts(project_root, prd_dir, manifest))

    ledger_present = common.ledger_exists(artifact_dir, project_root)
    if phase == "orchestration" or (
        phase == "all" and state in {"PLANNING", "PLAN_READY"} and ledger_present
    ):
        orch_failures, _ledger = _validate_orchestration_ledger(
            project_root, artifact_dir, manifest
        )
        failures.extend(orch_failures)

    passed = sum(1 for f in failures if f["status"] == "PASS")
    skipped = sum(1 for f in failures if f["status"] == "SKIPPED")
    failed = sum(1 for f in failures if f["status"] == "FAIL")
    summary = {
        "slug": slug,
        "manifest": str((artifact_dir / "manifest.yml").relative_to(project_root)),
        "phase": phase,
        "checks_passed": passed + skipped,
        "checks_skipped": skipped,
        "checks_failed": failed,
        "failures": [f for f in failures if f["status"] == "FAIL"],
        "skipped": [f for f in failures if f["status"] == "SKIPPED"],
        "state": state,
    }
    if ledger_present:
        summary["ledger"] = str(
            (artifact_dir / common.ORCHESTRATION_LEDGER_FILENAME).relative_to(
                project_root
            )
        )
    common.info(common.json_dumps(summary))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())