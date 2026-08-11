#!/usr/bin/env python3
"""PRD-to-Plans: deterministic orchestration state engine.

Invoked by ``speckit.prd.orchestrate``. Reads, mutates, and atomically
rewrites the ``orchestration.yml`` ledger; never edits implementation
source code.

Implementation-drift policy: implementation files may change freely while
a task is IN_PROGRESS. Every pass-evidence entry binds the
implementation-tree fingerprint current when it was recorded; ``complete``
requires a pass bound to the *current* tree for every declared check kind,
so post-evidence source changes block completion until evidence is
re-recorded. Transitions outside implementation (``start``/``block``/
``approve``) remain fail-closed against unrecorded drift relative to
the last evidenced tree. ``reopen`` is the sanctioned recovery path: it
clears stale evidence, marks the task STALE, and re-baselines the
reference to the current tree, so drifted work restarts with a fresh
start/evidence/complete cycle instead of manual ledger edits. Reopen
invalidates explicit transitive dependents plus any later tasks in the
frozen execution order that already hold a result (IN_PROGRESS/DONE/
BLOCKED); READY/TODO tasks stay planned and nothing earlier is touched.

Action surface:

    initialize            validate a 1.0 plan and materialize 1.1 ledger
    status                render the prioritized checklist
    next                  return the one eligible task packet
    start task=<id>       claim the next eligible task
    evidence task=<id>    record a manual/external evidence entry
    complete task=<id>    run automated checks and mark DONE
    block task=<id>       stop progression and record blocker
    reopen task=<id>      invalidate task and transitive downstream
    approve stage=<...>  record stage approval

Usage::

    prd_orchestrate.py slug=<slug> action=initialize
    prd_orchestrate.py slug=<slug> action=status
    prd_orchestrate.py slug=<slug> action=next
    prd_orchestrate.py slug=<slug> action=start task=<id> owner=<label>
    prd_orchestrate.py slug=<slug> action=evidence task=<id> check=<id> result=pass path=<p>
    prd_orchestrate.py slug=<slug> action=complete task=<id>
    prd_orchestrate.py slug=<slug> action=block task=<id> reason="<text>"
    prd_orchestrate.py slug=<slug> action=reopen task=<id> reason="<text>"
    prd_orchestrate.py slug=<slug> action=approve stage=<SLC-NNN|FINAL> approved_by="<id>"

Every action returns a single-line JSON summary on stdout and exits
with code 0 on success, 1 on rejected failure with a single valid
recovery action. The script never invokes a coding agent, formatter
write mode, migration application, deployment command, or
implementation editor; it only reads and writes the ledger and its
evidence directory.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prd_common as common  # noqa: E402

ARTIFACT_DIRNAME = "000-spec-of-specs"

VALID_ACTIONS = frozenset(
    {"initialize", "status", "next", "start", "evidence", "complete", "block", "reopen", "approve"}
)

ALLOWED_VERIFICATION_COMMANDS = re.compile(
    r"^\s*(pytest|python\s+-m\s+pytest|bash\s+|sh\s+|cargo\s+test|"
    r"go\s+test|node\s+|npm\s+test|npm\s+run\s+|make\s+|invoke-?tests?)",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_args(argv: list[str] | None = None) -> dict[str, str]:
    """Parse ``key=value`` and ``--flag`` tokens (mirrors ``prd_common.parse_args``)."""
    args = argv if argv is not None else sys.argv[1:]
    parsed: dict[str, str] = {}
    for token in args:
        if token.startswith("--"):
            parsed[f"flag_{token[2:].replace('-', '_')}"] = "true"
            continue
        if "=" in token:
            key, _, value = token.partition("=")
            parsed[key.strip()] = value.strip()
            continue
        parsed.setdefault("slug", token.strip())
    return parsed


def _slug_spec_dirs(project_root: Path) -> Path:
    return project_root / ".specify" / "specs"


def _fail(reason: str, recovery: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": reason,
        "recovery": recovery,
        **extra,
    }


def _ok(**extra: Any) -> dict[str, Any]:
    return {"ok": True, **extra}


def _next_eligible_task(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first task whose state is TODO/READY/STALE and whose
    dependencies are all DONE (transitively).

    STALE tasks are eligible so ``reopen`` (the sanctioned recovery path)
    can be restarted with a fresh start/evidence/complete cycle against
    the re-baselined tree.

    Walks the frozen execution order; returns the first match or
    ``None`` if nothing is eligible.
    """
    slices_by_id = {s["id"]: s for s in ledger.get("slices", []) if isinstance(s, dict)}
    state_by_task: dict[str, str] = {}
    for slice_meta in slices_by_id.values():
        for task in slice_meta.get("tasks", []) or []:
            state_by_task[str(task.get("id"))] = str(task.get("state", "TODO"))
    for entry in ledger.get("priorities", {}).get("execution", []) or []:
        if not isinstance(entry, str) or "::" not in entry:
            continue
        _slice_id, task_id = entry.split("::", 1)
        state = state_by_task.get(task_id)
        if state not in {"TODO", "READY", "STALE"}:
            continue
        task = next(
            (
                t
                for s in slices_by_id.values()
                for t in s.get("tasks", [])
                if str(t.get("id")) == task_id
            ),
            None,
        )
        if task is None:
            continue
        deps = task.get("dependencies") or []
        if any(state_by_task.get(d) != "DONE" for d in deps):
            continue
        return task
    return None


def _all_tasks_done(ledger: dict[str, Any]) -> bool:
    for slice_meta in ledger.get("slices", []) or []:
        for task in slice_meta.get("tasks", []) or []:
            if str(task.get("state")) != "DONE":
                return False
    return True


def _active_task(ledger: dict[str, Any]) -> dict[str, Any] | None:
    for slice_meta in ledger.get("slices", []) or []:
        for task in slice_meta.get("tasks", []) or []:
            if str(task.get("state")) == "IN_PROGRESS":
                return task
    return None


def _slice_by_id(ledger: dict[str, Any], slice_id: str) -> dict[str, Any] | None:
    for slice_meta in ledger.get("slices", []) or []:
        if str(slice_meta.get("id")) == slice_id:
            return slice_meta
    return None


def _task_by_id(ledger: dict[str, Any], task_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(slice, task)`` matching ``task_id`` or ``(None, None)``."""
    for slice_meta in ledger.get("slices", []) or []:
        for task in slice_meta.get("tasks", []) or []:
            if str(task.get("id")) == task_id:
                return slice_meta, task
    return None, None


def _implementation_fingerprint(project_root: Path) -> str:
    """Hash the implementation tree (everything outside excluded dirs)."""
    return common.implementation_tree_fingerprint(project_root)


def _ledger_reference_fingerprint(ledger: dict[str, Any]) -> str | None:
    """Return the ledger's stored implementation-tree reference hash, or
    ``None`` if the ledger predates the invariant.

    The reference is captured by ``start`` and refreshed by every
    ``evidence`` action, so it tracks the last tree the ledger has seen
    as stable. ``block``/``approve`` reject on any difference from this
    reference (unrecorded drift); ``complete`` instead requires evidence
    bound to the *current* tree. ``reopen`` re-baselines the reference
    to the current tree as its recovery action.
    """
    repo = ledger.get("repository") or {}
    value = repo.get("implementation_fingerprint")
    return value if isinstance(value, str) and value else None


def _set_ledger_reference_fingerprint(
    ledger: dict[str, Any], project_root: Path
) -> None:
    """Record the current implementation-tree fingerprint on the ledger."""
    repo = ledger.setdefault("repository", {})
    repo["implementation_fingerprint"] = _implementation_fingerprint(project_root)


def _enforce_implementation_invariant(
    project_root: Path, ledger: dict[str, Any], action: str
) -> dict[str, Any] | None:
    """Return a failure dict if implementation files changed since the
    ledger's recorded reference, else ``None``.

    On the first call (no reference recorded yet), the current
    fingerprint is captured and stored so subsequent calls have a
    stable baseline. The reference is refreshed whenever ``evidence``
    records a new proof, so this check only fires on *unrecorded* drift
    for the transitions that still use it (``start``/``block``/
    ``approve``). ``evidence`` and ``complete`` do not use this check:
    evidence snapshots the tree, and completion compares evidence to the
    current tree directly. ``reopen`` never rejects on drift; it clears
    stale evidence and re-baselines the reference instead.
    """
    reference = _ledger_reference_fingerprint(ledger)
    current = _implementation_fingerprint(project_root)
    if reference is None:
        _set_ledger_reference_fingerprint(ledger, project_root)
        return None
    if reference != current:
        return _fail(
            "implementation_hash_changed",
            "Halt and audit the implementation tree; do NOT auto-correct. "
            "Run `prd_orchestrate.py status` to inspect the ledger.",
            action=action,
            reference=reference,
            current=current,
        )
    return None


# ── Action implementations ───────────────────────────────────────────────────


def action_initialize(
    project_root: Path,
    slug: str,
    args: dict[str, str],
) -> dict[str, Any]:
    """Validate a legacy ``1.0`` workspace and materialize a ``1.1`` ledger.

    Refuses to renumber or rewrite any existing task IDs.
    """
    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    manifest = common.load_manifest(artifact_dir)
    if manifest is None:
        return _fail(
            "manifest_missing",
            "Run `prd_plan.py source=<p> slug=<slug>` first to create the workspace.",
        )
    manifest_schema = str(manifest.get("schema_version", ""))
    if manifest_schema not in {"1.0", "1.1"}:
        return _fail(
            "unsupported_manifest_schema_version",
            f"Manifest schema_version={manifest_schema!r} is not supported by this orchestrator.",
        )

    # Discover existing task IDs across all slice directories.
    discovered: dict[str, list[str]] = {}
    duplicates: list[str] = []
    seen_task_ids: set[str] = set()
    for slice_meta in manifest.get("slices", []) or []:
        if not isinstance(slice_meta, dict):
            continue
        slice_id = str(slice_meta.get("id") or "")
        directory = str(slice_meta.get("directory") or "")
        if not slice_id or not directory:
            continue
        slice_dir = prd_dir / directory
        ids = common._harvest_task_ids(slice_dir)
        for tid in ids:
            if tid in seen_task_ids:
                duplicates.append(tid)
            seen_task_ids.add(tid)
        discovered[slice_id] = ids
    if duplicates:
        return _fail(
            "ambiguous_task_ids",
            f"Duplicate task IDs across slices: {duplicates!r}. "
            "Re-author tasks.md before initializing the ledger.",
            duplicates=duplicates,
        )

    # Verify dependency edges refer to known task IDs.
    unknown: list[str] = []
    for slice_meta in manifest.get("slices", []) or []:
        if not isinstance(slice_meta, dict):
            continue
        for dep in slice_meta.get("dependencies") or []:
            if dep and dep not in {s.get("id") for s in manifest.get("slices", []) if isinstance(s, dict)}:
                unknown.append(dep)
    if unknown:
        return _fail(
            "ambiguous_slice_dependencies",
            f"Unknown slice dependencies: {unknown!r}. "
            "Fix the manifest's `dependencies` fields.",
            unknown=unknown,
        )

    ledger = common.build_ledger_from_manifest(project_root, slug, manifest)
    common.bump_revision(ledger)

    # Replay discovered task IDs without renumbering. If a slice has
    # tasks.md content, prefer those; otherwise keep the placeholder.
    for slice_meta in ledger.get("slices", []) or []:
        slice_id = str(slice_meta.get("id"))
        ids = discovered.get(slice_id) or []
        if ids:
            tasks = []
            for rank, tid in enumerate(ids, start=1):
                tasks.append(
                    {
                        "id": tid,
                        "rank": rank,
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
                )
            slice_meta["tasks"] = tasks
    # Recompute priorities.execution from the live task list.
    execution = []
    for slice_meta in ledger.get("slices", []) or []:
        for task in slice_meta.get("tasks", []) or []:
            execution.append(f"{slice_meta.get('id')}::{task.get('id')}")
    ledger.setdefault("priorities", {})["execution"] = execution

    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        common.write_ledger(artifact_dir, project_root, ledger)
        # Bump the manifest to ``1.1`` only after the ledger is written
        # and validated.
        manifest["schema_version"] = common.ORCHESTRATION_LEDGER_SCHEMA_VERSION
        manifest["orchestration_initialized_at"] = _utc_now_iso()
        common.write_manifest(artifact_dir, project_root, manifest)
    finally:
        common.release_ledger_lock(lock)
    return _ok(
        action="initialize",
        slug=slug,
        revision=int(ledger.get("revision", 1)),
        task_count=sum(
            len(s.get("tasks", []) or []) for s in ledger.get("slices", []) or []
        ),
        ledger=str((artifact_dir / common.ORCHESTRATION_LEDGER_FILENAME).relative_to(project_root)),
    )


def action_status(project_root: Path, slug: str, args: dict[str, str]) -> dict[str, Any]:
    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail(
            "ledger_missing",
            "Run `prd_orchestrate.py slug=<slug> action=initialize` first.",
        )
    return _ok(
        action="status",
        slug=slug,
        project=ledger.get("project", {}),
        priorities=ledger.get("priorities", {}),
        slices=[
            {
                "id": s.get("id"),
                "state": s.get("state"),
                "directory": s.get("directory"),
                "tasks": [
                    {
                        "id": t.get("id"),
                        "state": t.get("state"),
                        "rank": t.get("rank"),
                        "blockers": t.get("blockers", []),
                    }
                    for t in s.get("tasks", []) or []
                ],
            }
            for s in ledger.get("slices", []) or []
            if isinstance(s, dict)
        ],
        final_gate=ledger.get("final_gate", {}),
        revision=ledger.get("revision", 0),
    )


def action_next(project_root: Path, slug: str, args: dict[str, str]) -> dict[str, Any]:
    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail(
            "ledger_missing",
            "Run `prd_orchestrate.py slug=<slug> action=initialize` first.",
        )
    task = _next_eligible_task(ledger)
    if task is None:
        return _ok(action="next", task=None, reason="no_eligible_task")
    return _ok(action="next", task=task)


def action_start(
    project_root: Path, slug: str, args: dict[str, str]
) -> dict[str, Any]:
    task_id = args.get("task", "")
    owner = args.get("owner", "").strip()
    if not task_id or not owner:
        return _fail(
            "missing_arguments",
            "Pass task=<id> owner=<label>.",
        )
    try:
        common.parse_task_id(task_id)
    except ValueError as exc:
        return _fail("invalid_task_id", str(exc))

    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail("ledger_missing", "Run action=initialize first.")
    active = _active_task(ledger)
    if active is not None and str(active.get("id")) != task_id:
        return _fail(
            "another_task_in_progress",
            f"Complete (action=complete) or block (action=block) the active task first: {active.get('id')!r}.",
            active_task=active.get("id"),
        )
    eligible = _next_eligible_task(ledger)
    if eligible is None or str(eligible.get("id")) != task_id:
        return _fail(
            "not_next_eligible",
            "Use `action=next` to inspect the eligible task and pass its id.",
            eligible=eligible.get("id") if eligible else None,
        )
    slice_meta, task = _task_by_id(ledger, task_id)
    if task is None or slice_meta is None:
        return _fail("unknown_task_id", f"No ledger task with id {task_id!r}.")
    if str(task.get("state")) in {"DONE", "BLOCKED"}:
        return _fail(
            "task_not_startable",
            f"Task state is {task.get('state')!r}; reopen first (action=reopen) or pick a different task.",
        )
    violation = _enforce_implementation_invariant(project_root, ledger, "start")
    if violation:
        return violation
    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        task["state"] = "IN_PROGRESS"
        task["active_owner"] = owner
        ledger.setdefault("project", {})
        ledger["project"]["state"] = "IN_PROGRESS"
        ledger["project"]["current_task"] = task_id
        ledger["project"]["active_owner"] = owner
        common.bump_revision(ledger)
        common.write_ledger(artifact_dir, project_root, ledger)
    finally:
        common.release_ledger_lock(lock)
    return _ok(action="start", task=task_id, owner=owner)


def action_evidence(
    project_root: Path, slug: str, args: dict[str, str]
) -> dict[str, Any]:
    task_id = args.get("task", "")
    check = args.get("check", "")
    result = args.get("result", "")
    path = args.get("path", "")
    if not task_id or not check or result not in {"pass", "fail"}:
        return _fail(
            "missing_arguments",
            "Pass task=<id> check=<id> result=pass|fail path=<relative>.",
        )
    try:
        common.parse_task_id(task_id)
    except ValueError as exc:
        return _fail("invalid_task_id", str(exc))

    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail("ledger_missing", "Run action=initialize first.")
    slice_meta, task = _task_by_id(ledger, task_id)
    if task is None or slice_meta is None:
        return _fail("unknown_task_id", f"No ledger task with id {task_id!r}.")
    if str(task.get("state")) != "IN_PROGRESS":
        return _fail(
            "task_not_in_progress",
            "Evidence can only be recorded for the current IN_PROGRESS task. "
            "Use action=start first.",
            task_state=str(task.get("state")),
        )

    check_kind, _, check_id = check.partition(".")
    if not check_kind or not check_id:
        check_kind, check_id = "evidence", check
    source_path: Path | None = None
    if path:
        candidate = (project_root / path).resolve() if not os.path.isabs(path) else Path(path)
        try:
            common.require_within(candidate, project_root)
        except RuntimeError as exc:
            return _fail(
                "evidence_path_escapes_project",
                "Provide a path inside the project root (relative preferred).",
                detail=str(exc),
            )
        if not candidate.is_file():
            return _fail(
                "evidence_path_missing",
                f"Path {path!r} does not exist or is not a file.",
            )
        source_path = candidate

    current_fp = _implementation_fingerprint(project_root)

    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        evidence_path = common.write_task_evidence(
            artifact_dir,
            project_root,
            task_id=task_id,
            check_kind=check_kind,
            check_id=check_id,
            result=result,
            source_path=source_path,
        )
        # Append an evidence entry on the task, bound to the exact
        # implementation tree it proves.
        entry: dict[str, Any] = {
            "check_kind": check_kind,
            "check_id": check_id,
            "result": result,
            "recorded_at": _utc_now_iso(),
            "evidence_path": str(evidence_path.relative_to(project_root)),
            "implementation_fingerprint": current_fp,
        }
        if source_path is not None:
            entry["source_path"] = str(source_path.relative_to(project_root))
        task.setdefault("evidence", []).append(entry)
        # Refresh the ledger's reference so non-implementation
        # transitions (block/reopen/approve) treat this tree as stable.
        ledger.setdefault("repository", {})["implementation_fingerprint"] = current_fp
        common.bump_revision(ledger)
        common.write_ledger(artifact_dir, project_root, ledger)
    finally:
        common.release_ledger_lock(lock)
    return _ok(
        action="evidence",
        task=task_id,
        check=check,
        result=result,
        evidence_path=str(evidence_path.relative_to(project_root)),
    )


def action_complete(
    project_root: Path, slug: str, args: dict[str, str]
) -> dict[str, Any]:
    task_id = args.get("task", "")
    if not task_id:
        return _fail("missing_arguments", "Pass task=<id>.")
    try:
        common.parse_task_id(task_id)
    except ValueError as exc:
        return _fail("invalid_task_id", str(exc))

    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail("ledger_missing", "Run action=initialize first.")
    slice_meta, task = _task_by_id(ledger, task_id)
    if task is None or slice_meta is None:
        return _fail("unknown_task_id", f"No ledger task with id {task_id!r}.")
    if str(task.get("state")) != "IN_PROGRESS":
        return _fail(
            "task_not_in_progress",
            "Only the currently IN_PROGRESS task can be completed. Use action=start first if no task is active.",
        )
    # Verify every declared check kind has a pass entry bound to the
    # CURRENT implementation tree. Evidence recorded against an older
    # tree (any post-evidence source change) is stale and blocks
    # completion until re-recorded.
    current_fp = _implementation_fingerprint(project_root)
    declared_kinds = [
        kind
        for kind, cmds in (task.get("checks") or {}).items()
        if cmds
    ]
    declared_kinds = [k for k in declared_kinds if k in {"unit", "integration", "regression", "e2e", "migration", "deployment", "rollback"}]
    if not declared_kinds:
        return _fail(
            "no_declared_checks",
            "Add at least one unit/regression/integration/e2e check to the task before completing.",
        )
    pass_by_kind: dict[str, str] = {}
    fresh_by_kind: dict[str, str] = {}
    for entry in task.get("evidence", []) or []:
        kind = str(entry.get("check_kind"))
        cid = str(entry.get("check_id"))
        result = str(entry.get("result"))
        if kind not in declared_kinds or result != "pass":
            continue
        pass_by_kind.setdefault(kind, cid)
        if str(entry.get("implementation_fingerprint") or "") == current_fp:
            fresh_by_kind.setdefault(kind, cid)
    unsatisfied: list[tuple[str, str]] = []
    for kind in declared_kinds:
        if kind not in fresh_by_kind:
            unsatisfied.append(
                (kind, "evidence_stale" if kind in pass_by_kind else "no_pass_evidence")
            )
    if unsatisfied:
        stale_kinds = [k for k, r in unsatisfied if r == "evidence_stale"]
        if stale_kinds:
            return _fail(
                "evidence_stale",
                "Implementation files changed after the recorded pass evidence. "
                "Re-run the declared checks and re-record pass evidence against "
                "the current tree before completing.",
                stale=stale_kinds,
                unsatisfied=unsatisfied,
            )
        return _fail(
            "missing_evidence",
            "Record a pass evidence entry for each declared check kind: "
            f"{[k for k, _ in unsatisfied]!r}.",
            missing=unsatisfied,
        )

    # Refuse any verification command that looks like a coding agent or
    # implementation editor. ``complete`` only runs declared non-rewriting
    # verification commands.
    for kind in declared_kinds:
        for cmd in task.get("checks", {}).get(kind, []) or []:
            if not ALLOWED_VERIFICATION_COMMANDS.match(str(cmd)):
                return _fail(
                    "disallowed_verification_command",
                    f"Check command {cmd!r} does not match the allowed non-rewriting set "
                    "(pytest, cargo test, go test, npm test, make, bash/sh, node). "
                    "Replace it with a read-only verification command before completing.",
                    offending=cmd,
                )

    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        task["state"] = "DONE"
        task["completed_at"] = _utc_now_iso()
        task["active_owner"] = None
        ledger.setdefault("project", {})
        ledger["project"]["current_task"] = None
        ledger["project"]["active_owner"] = None
        slice_done = all(
            str(t.get("state")) == "DONE"
            for t in slice_meta.get("tasks", []) or []
        )
        if slice_done:
            slice_meta["state"] = "DONE"
            ledger["project"]["state"] = "AWAITING_APPROVAL"
        else:
            ledger["project"]["state"] = "IN_PROGRESS"
        common.bump_revision(ledger)
        common.write_ledger(artifact_dir, project_root, ledger)
    finally:
        common.release_ledger_lock(lock)
    return _ok(
        action="complete",
        task=task_id,
        slice_done=slice_done,
        project_state=ledger.get("project", {}).get("state"),
    )


def action_block(
    project_root: Path, slug: str, args: dict[str, str]
) -> dict[str, Any]:
    task_id = args.get("task", "")
    reason = args.get("reason", "")
    if not task_id or not reason:
        return _fail(
            "missing_arguments",
            "Pass task=<id> reason=\"<text>\".",
        )
    try:
        common.parse_task_id(task_id)
    except ValueError as exc:
        return _fail("invalid_task_id", str(exc))

    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail("ledger_missing", "Run action=initialize first.")
    slice_meta, task = _task_by_id(ledger, task_id)
    if task is None or slice_meta is None:
        return _fail("unknown_task_id", f"No ledger task with id {task_id!r}.")
    if str(task.get("state")) in {"DONE", "STALE"}:
        return _fail(
            "task_not_blockable",
            f"Task state is {task.get('state')!r}; reopen first (action=reopen) to invalidate.",
        )

    violation = _enforce_implementation_invariant(project_root, ledger, "block")
    if violation:
        return violation

    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        task["state"] = "BLOCKED"
        task.setdefault("blockers", []).append(
            {"reason": reason, "recorded_at": _utc_now_iso()}
        )
        if str(task.get("state")) == "BLOCKED":
            ledger["project"]["state"] = "BLOCKED"
            ledger["project"]["current_task"] = None
            ledger["project"]["active_owner"] = None
        common.bump_revision(ledger)
        common.write_ledger(artifact_dir, project_root, ledger)
    finally:
        common.release_ledger_lock(lock)
    return _ok(action="block", task=task_id, reason=reason)


def action_reopen(
    project_root: Path, slug: str, args: dict[str, str]
) -> dict[str, Any]:
    task_id = args.get("task", "")
    reason = args.get("reason", "")
    if not task_id or not reason:
        return _fail(
            "missing_arguments",
            "Pass task=<id> reason=\"<text>\".",
        )
    try:
        common.parse_task_id(task_id)
    except ValueError as exc:
        return _fail("invalid_task_id", str(exc))

    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail("ledger_missing", "Run action=initialize first.")
    slice_meta, task = _task_by_id(ledger, task_id)
    if task is None or slice_meta is None:
        return _fail("unknown_task_id", f"No ledger task with id {task_id!r}.")

    # Build the dependency closure: any task that depends (transitively)
    # on the target task becomes STALE.
    state_by_task: dict[str, str] = {}
    for slice_meta_inner in ledger.get("slices", []) or []:
        for t in slice_meta_inner.get("tasks", []) or []:
            state_by_task[str(t.get("id"))] = str(t.get("state", "TODO"))
    dependents: dict[str, list[str]] = {tid: [] for tid in state_by_task}
    for slice_meta_inner in ledger.get("slices", []) or []:
        for t in slice_meta_inner.get("tasks", []) or []:
            for dep in t.get("dependencies") or []:
                dependents.setdefault(dep, []).append(str(t.get("id")))
    closure: set[str] = set()
    stack = [task_id]
    while stack:
        current = stack.pop()
        if current in closure:
            continue
        closure.add(current)
        stack.extend(dependents.get(current, []))

    # Forward execution-order fallback: when explicit task dependencies
    # are absent (or incomplete), any later task in the frozen plan
    # order that already holds a result -- IN_PROGRESS, DONE, or BLOCKED
    # -- is also invalidated, so reopening the target cannot strand an
    # active downstream task. READY/TODO tasks carry no result and stay
    # planned; nothing earlier in the order is touched.
    execution = ledger.get("priorities", {}).get("execution", []) or []
    seen_target = False
    for entry in execution:
        if not isinstance(entry, str) or "::" not in entry:
            continue
        _slice_id, later_id = entry.split("::", 1)
        if not seen_target:
            if later_id == task_id:
                seen_target = True
            continue
        if later_id in closure:
            continue
        if state_by_task.get(later_id) in {"IN_PROGRESS", "DONE", "BLOCKED"}:
            closure.add(later_id)

    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        # Reopen is the sanctioned recovery path for drifted trees; it
        # never rejects on drift. Re-baseline the reference to the
        # current (unverified) tree so a fresh start/evidence/complete
        # cycle can run without manual ledger edits.
        _set_ledger_reference_fingerprint(ledger, project_root)
        # Invalidate the target and all downstream work: explicit
        # dependents plus later tasks materialized in the frozen order.
        for tid in closure:
            slice_inner, task_inner = _task_by_id(ledger, tid)
            if task_inner is None:
                continue
            task_inner["state"] = "STALE"
            task_inner["evidence"] = []
            task_inner["active_owner"] = None
            task_inner.pop("completed_at", None)
            task_inner.setdefault("reopens", []).append(
                {"reason": reason, "by": task_id, "recorded_at": _utc_now_iso()}
            )
        # Invalidate downstream approvals and any slice whose tasks
        # became STALE.
        for slice_meta_inner in ledger.get("slices", []) or []:
            contains_stale = any(
                str(t.get("state")) == "STALE"
                for t in slice_meta_inner.get("tasks", []) or []
            )
            if contains_stale:
                # Demote the slice state to STALE so downstream phases
                # cannot proceed without re-authoring the affected work.
                if str(slice_meta_inner.get("state")) in {"DONE", "PENDING"}:
                    slice_meta_inner["state"] = "STALE"
                approval = (
                    slice_meta_inner.setdefault("exit_gate", {}).setdefault(
                        "approval", {}
                    )
                )
                if approval.get("approved_by"):
                    approval["approved_by"] = None
                    approval["approved_at"] = None
        ledger["project"]["state"] = "STALE"
        ledger["project"]["current_task"] = None
        ledger["project"]["active_owner"] = None
        # Invalidate final approval if present.
        final_gate = ledger.setdefault("final_gate", {})
        if final_gate.get("approved_by"):
            final_gate["approved_by"] = None
            final_gate["approved_at"] = None
        common.bump_revision(ledger)
        common.write_ledger(artifact_dir, project_root, ledger)
    finally:
        common.release_ledger_lock(lock)
    return _ok(action="reopen", task=task_id, invalidated=sorted(closure))


def action_approve(
    project_root: Path, slug: str, args: dict[str, str]
) -> dict[str, Any]:
    stage = args.get("stage", "")
    approved_by = args.get("approved_by", "").strip()
    if not stage or not approved_by:
        return _fail(
            "missing_arguments",
            "Pass stage=<SLC-NNN|FINAL> approved_by=\"<id>\".",
        )
    try:
        kind, key = common.stage_key(stage)
    except ValueError as exc:
        return _fail("invalid_stage", str(exc))

    specs_root = _slug_spec_dirs(project_root)
    prd_dir = common.safe_create_dir(specs_root, project_root).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    ledger = common.load_ledger(artifact_dir, project_root)
    if ledger is None:
        return _fail("ledger_missing", "Run action=initialize first.")

    violation = _enforce_implementation_invariant(project_root, ledger, "approve")
    if violation:
        return violation

    lock = common.acquire_ledger_lock(artifact_dir, project_root)
    try:
        if kind == "final":
            if not _all_tasks_done(ledger):
                return _fail(
                    "final_gate_incomplete",
                    "Every task must be DONE before approving FINAL.",
                )
            final_gate = ledger.setdefault("final_gate", {})
            for key_name in (
                "baseline_check",
                "full_regression",
                "cross_slice_e2e",
                "deployment_smoke",
                "rollback_check",
            ):
                if not str(final_gate.get(key_name) or "").strip():
                    return _fail(
                        "final_gate_check_unset",
                        f"final_gate.{key_name} must be set before approving FINAL.",
                        field=key_name,
                    )
            final_gate["approved_by"] = approved_by
            final_gate["approved_at"] = _utc_now_iso()
            ledger["project"]["state"] = "RELEASE_READY"
            common.bump_revision(ledger)
            common.write_ledger(artifact_dir, project_root, ledger)
            return _ok(action="approve", stage="FINAL", approved_by=approved_by)
        # Slice approval.
        slice_meta = _slice_by_id(ledger, key)
        if slice_meta is None:
            return _fail(
                "unknown_slice",
                f"No ledger slice with id {key!r}.",
            )
        if not all(
            str(t.get("state")) == "DONE"
            for t in slice_meta.get("tasks", []) or []
        ):
            return _fail(
                "slice_incomplete",
                "Every task in the slice must be DONE before approval.",
            )
        if not str(slice_meta.get("exit_gate", {}).get("e2e_journey") or "").strip():
            return _fail(
                "slice_e2e_journey_unset",
                f"slice {key} exit_gate.e2e_journey must be set before approval.",
            )
        approval = slice_meta.setdefault("exit_gate", {}).setdefault("approval", {})
        approval["approved_by"] = approved_by
        approval["approved_at"] = _utc_now_iso()
        # Unlock the next slice (if any).
        slices = ledger.get("slices", []) or []
        idx = next(
            (i for i, s in enumerate(slices) if str(s.get("id")) == key),
            -1,
        )
        if idx >= 0 and idx + 1 < len(slices):
            next_slice = slices[idx + 1]
            if str(next_slice.get("state")) == "PENDING":
                next_slice["state"] = "PENDING"  # remain PENDING; tasks become TODO/READY on next
                # Promote tasks to TODO so they can be started after approval.
                for t in next_slice.get("tasks", []) or []:
                    if str(t.get("state")) == "TODO":
                        t["state"] = "TODO"
        ledger["project"]["state"] = "IN_PROGRESS"
        common.bump_revision(ledger)
        common.write_ledger(artifact_dir, project_root, ledger)
        return _ok(action="approve", stage=key, approved_by=approved_by)
    finally:
        common.release_ledger_lock(lock)


# ── Entry point ─────────────────────────────────────────────────────────────


def _route(project_root: Path, slug: str, args: dict[str, str]) -> dict[str, Any]:
    action = args.get("action", "").strip()
    if action not in VALID_ACTIONS:
        return _fail(
            "unknown_action",
            f"action must be one of {sorted(VALID_ACTIONS)!r}",
        )
    router = {
        "initialize": action_initialize,
        "status": action_status,
        "next": action_next,
        "start": action_start,
        "evidence": action_evidence,
        "complete": action_complete,
        "block": action_block,
        "reopen": action_reopen,
        "approve": action_approve,
    }
    return router[action](project_root, slug, args)


def main(argv: list[str] | None = None) -> int:
    args = _resolve_args(argv)
    project_root = common.find_project_root()
    if project_root is None:
        common.err("ERROR: not inside a Spec Kit project (.specify/ not found)")
        return 1
    raw_slug = args.get("slug", "")
    if not raw_slug:
        common.err("ERROR: missing slug=<slug>")
        return 2
    try:
        slug = common.normalize_slug(raw_slug)
    except ValueError as exc:
        common.err(f"ERROR: {exc}")
        return 1

    result = _route(project_root, slug, args)
    print(common.json_dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
