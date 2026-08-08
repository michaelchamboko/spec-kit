---
description: "Deterministic, one-task-at-a-time waterfall task manager for the PRD orchestration ledger; records evidence, blockers, reopens, and stage approvals without ever editing implementation source"
tools:
  - 'bash/prd_orchestrate.sh'
  - 'python/prd_orchestrate.py'
scripts:
  sh: ../../scripts/bash/prd_orchestrate.sh
  ps: ../../scripts/powershell/prd_orchestrate.ps1
  py: ../../scripts/python/prd_orchestrate.py
---

# PRD-to-Plans: Orchestrate Command

Drive a strict, one-task-at-a-time waterfall over
`.specify/specs/<slug>/000-spec-of-specs/orchestration.yml`.
This command **never edits implementation source code**; it only
reads and writes the ledger and its evidence directory.

## User Input

```text
$ARGUMENTS
```

**Parse arguments**: `slug=<slug>` (required) and one of:

| Action | Extra arguments | Behavior |
|--------|-----------------|----------|
| `initialize` | — | Validate every task/dependency in a legacy `1.0` plan, materialize the `1.1` ledger without renumbering existing IDs. Fails closed on ambiguous imports. |
| `status` | — | Render the complete prioritized checklist, current task, blockers, and pending approval |
| `next` | — | Return the one eligible task packet, its interfaces, documentation references, and completion checks |
| `start` | `task=<id> owner=<label>` | Claim the next eligible task; reject any second concurrent active task or non-next task |
| `evidence` | `task=<id> check=<id> result=pass\|fail path=<relative-path>` | Record manual or external evidence for one check |
| `complete` | `task=<id>` | Run declared automated non-rewriting checks; complete only when every required check has passing evidence |
| `block` | `task=<id> reason="<text>"` | Stop progression and record the blocker |
| `reopen` | `task=<id> reason="<text>"` | Invalidate the task and every transitive downstream task and approval |
| `approve` | `stage=<SLC-NNN\|FINAL> approved_by="<identity>"` | Unlock the next stage only after its exit gate passes |

Exactly one action per call. Reject anything else.

## Ancestor Path Safety

Same rules as `speckit.prd.plan`:

1. Verify `.specify`, `.specify/specs`, and the PRD directory are real
   directories resolving inside the project root.
2. Refuse symlinked components and any path escaping the root.
3. Resolve the slug with the same normalization rules.
4. Reject if `.specify/specs/<slug>/000-spec-of-specs/orchestration.yml`
   does not exist unless the action is `initialize`.

## Ledger Location

The ledger is the **only** source of truth:

```
.specify/specs/<slug>/000-spec-of-specs/orchestration.yml
```

The companion evidence directory lives next to it:

```
.specify/specs/<slug>/000-spec-of-specs/orchestration-evidence/<task-id>/
    unit/<check-id>.pass
    unit/<check-id>.fail
    integration/<check-id>.pass
    ...
```

Every state-changing action must update the ledger atomically
(`tmp-write` + `rename`) and write evidence files under the
orchestration-evidence tree.

## Mode: initialize

For a legacy `1.0` workspace (manifest `schema_version: "1.0"`):

1. Walk every slice's `tasks.md` and capture task IDs exactly as
   printed (`SLC-NNN-TMMM`). **Do not renumber.**
2. Walk the dependency graph; reject ambiguous or cyclic imports.
3. Compute global execution rank from the dependency topology,
   preserving the frozen slice order.
4. Emit the `1.1` ledger with every existing task and slice state set
   to `PENDING` (slice) / `TODO` (task) — ready for the orchestrator to
   pick up at the next eligible task.
5. Bump `manifest.yml` to `schema_version: "1.1"` **only** when the
   ledger is fully written and validated.

If any step fails, the ledger is **not** written and `manifest.yml`
**does not** change. Output the exact blocker and the single valid
recovery action.

## Mode: status

Render the complete checklist:

```text
project.state: <NOT_STARTED|IN_PROGRESS|BLOCKED|AWAITING_APPROVAL|STALE|RELEASE_READY>
current_task: <SLC-NNN-TMMM | null>
active_owner: <label | null>
blockers: [<task-id>: <reason>, ...]

business priority: [<slice-id>, ...]
execution rank:    [(<slice-id>, <task-id>), ...]

SLC-NNN [<state>]  directory=<dir>  rank=<N>
  ✓ DONE   SLC-NNN-T001 <title>
  ▶ ACTIVE SLC-NNN-T002 <title>          owner=<label>
  ○ READY  SLC-NNN-T003 <title>
  ○ TODO   SLC-NNN-T004 <title>
  ✗ BLOCKED SLC-NNN-T005 <title>        reason=<reason>
  ...
```

No duplicate persisted dashboard is maintained.

## Mode: next

Return exactly one eligible task packet as JSON:

```json
{
  "action": "next",
  "task": {
    "id": "SLC-001-T001",
    "slice": "SLC-001",
    "title": "<task title>",
    "state": "READY",
    "rank": 1,
    "requirements": ["PRD-FR-001"],
    "acceptance": ["AC-SLC-001-001"],
    "interfaces": [
      "existing:src/foo/bar.py:func",
      "proposed:src/foo/baz.py:new_func -> int"
    ],
    "documentation_evidence": [
      "context7:some/library@1.2.3"
    ],
    "checks": {
      "unit": ["pytest tests/unit/test_x.py -k foo"],
      "integration": ["pytest tests/integration/test_y.py"],
      "regression": ["pytest tests/regression/test_z.py"],
      "e2e": ["bash scripts/e2e/journey.sh"],
      "migration": [],
      "deployment": ["bash scripts/deploy/smoke.sh"],
      "rollback": ["bash scripts/rollback/x.sh"]
    }
  },
  "context": {
    "interfaces_summary": "<human readable>",
    "documentation_summary": "<human readable>",
    "completion_summary": "<human readable>"
  }
}
```

If no eligible task (all blocked, or all done), output
`{"action":"next","task":null,"reason":"<why>"}`.

## Mode: start

Claim a task. **Hard rules**:

1. The requested `task=<id>` must be the **next eligible** task in the
   global execution rank. Reject any other task with
   `reason="not_next_eligible"`.
2. Across the entire ledger exactly one task may be `IN_PROGRESS`.
   If another task is `IN_PROGRESS`, reject with
   `reason="another_task_in_progress"` and identify it.
3. The task's `state` must be `READY` (or `TODO` for a fresh plan).
   Reject `BLOCKED` and `DONE` tasks with the recorded reason.
4. Record `owner=<label>` in the task's `active_owner` field.

## Mode: evidence

Record a manual or external evidence entry:

```
speckit.prd.orchestrate slug=my slug=… action=evidence task=SLC-001-T001 check=unit.result.result=pass path=tests/unit/test_x.py
```

The path must resolve inside the project root and must be a real file
(for `pass`) or a real failing report (for `fail` — a `.fail` file
under the evidence directory is created and the original report is
referenced). The script writes the evidence file under
`.specify/specs/<slug>/000-spec-of-specs/orchestration-evidence/<task-id>/<kind>/<check-id>.{pass,fail}`.

`result=fail` does **not** change the task state; it only records
evidence. Use `block` or `reopen` to react.

## Mode: complete

Mark a task `DONE`:

1. Verify `task=<id>` is currently `IN_PROGRESS`. Reject otherwise.
2. Verify every check kind declared on the task (`unit`,
   `integration`, `regression`, `e2e`, `migration`, `deployment`,
   `rollback`) has at least one `pass` evidence entry.
3. The script may run declared **non-rewriting verification
   commands** (e.g. `pytest`, `cargo test`, `go test`,
   `bash scripts/check.sh`) to gather automated evidence.
4. **The script never invokes** a coding agent, formatter write
   mode, migration application, deployment command, or
   implementation editor. Verified via the
   implementation-source-hash invariant (see Guardrails).
5. On success, mark task `DONE`. If every task in the slice is
   `DONE`, mark the slice `DONE` and transition project state to
   `AWAITING_APPROVAL`.
6. On failure, output the exact blocker and the single valid
   recovery action.

## Mode: block

Stop progression on a task:

1. Verify the task is not already `DONE` or `STALE`.
2. Record `reason="<text>"` in the task's `blockers` list.
3. Set the task state to `BLOCKED`.
4. If the blocked task is the currently active one, transition
   project state to `BLOCKED` and clear `current_task` /
   `active_owner`.

## Mode: reopen

Invalidate a task and everything transitively downstream:

1. Verify a `reason` is supplied.
2. The target task must currently be `DONE`, `STALE`, or `IN_PROGRESS`.
3. Set the target task state to `STALE` and clear its evidence.
4. Walk the dependency graph; every task that depends on the target
   (transitively) is set to `STALE`.
5. Any prior stage approval that depended only on now-stale tasks
   is invalidated (its `approved_by` is cleared and the stage returns
   to `AWAITING_APPROVAL` if it was `DONE`).
6. If the project state was `RELEASE_READY`, drop it to `STALE`.

## Mode: approve

Record a human stage approval:

1. `stage=FINAL`: requires every slice `DONE`, every task `DONE`, and
   the final gate's five checks (`baseline_check`,
   `full_regression`, `cross_slice_e2e`, `deployment_smoke`,
   `rollback_check`) to have at least one `pass` evidence entry.
2. `stage=SLC-NNN`: requires every task in that slice to be `DONE`
   and the slice's `exit_gate.e2e_journey` to have at least one
   `pass` evidence entry.
3. Record `approved_by="<identity>"` and the current UTC timestamp.
4. On `FINAL`, transition project state to `RELEASE_READY`.
5. On `SLC-NNN`, unlock the next slice (its tasks move from
   `PENDING` to `TODO`/`READY`).

## Prerequisites

- `.specify/specs/<slug>/000-spec-of-specs/manifest.yml` exists.
- For actions other than `initialize`: the ledger must exist and
  parse.
- The PRD workspace is inside the project root, with no symlinked
  ancestors.

## Guardrails

- **Never edit implementation source code**. Verified by hashing the
  implementation tree before and after every state-changing action;
  any change is reported as a fatal integrity violation.
- **Path containment** — all reads/writes inside project root,
  symlink refusal, deterministic relative paths.
- **Atomic ledger writes** — every change goes through
  `tmp-write` + `rename` so a partial write cannot be observed.
- **Schema validation** — every load runs the ledger through the
  embedded schema validator; unknown fields are rejected.
- **Revision counter** — every successful write increments a
  monotonic counter. Concurrent writes are detected via
  filesystem-level lock files.
- **One active task invariant** — exactly zero or one task is
  `IN_PROGRESS` across the entire ledger at any time.
- **No AI agent calls** — the script runs only declared,
  non-rewriting verification commands (e.g. `pytest`, `cargo test`,
  `go test`, `bash scripts/check.sh`); never invokes a coding agent.
- **Every failure returns** the exact blocker and the single valid
  recovery action.

## Recovery Rules

| Symptom | Recovery action |
|---------|-----------------|
| Ledger missing on a non-initialize action | Run `action=initialize` first |
| Schema parse fails | Delete ledger, rerun `action=initialize` |
| `not_next_eligible` on `start` | Use `action=next` to see the eligible task |
| `another_task_in_progress` | `action=complete` (or `block`) the active task first |
| Missing evidence on `complete` | `action=evidence task=<id> check=<id> result=pass path=<p>` for each missing check |
| `BLOCKED` task on `start` | `action=reopen task=<id> reason="<why>"` after unblocking |
| Implementation hash changed | **Fatal** — halt and audit. Recovery requires manual intervention; do not auto-correct. |

## Output

Every action returns a single-line JSON summary on stdout. State-
changing actions that succeed exit 0; failures exit 1 with the exact
`reason` and the single valid recovery action.

---

## State Diagram

```
NOT_STARTED
    │  action=next → 1st task
    ▼
IN_PROGRESS  ──action=complete──► AWAITING_APPROVAL (slice done)
    │  action=block                   │
    ▼                                  │ action=approve stage=SLC-NNN
BLOCKED                                ▼
    │  action=reopen + start          next slice unlocked
    ▼                                  │
IN_PROGRESS                            │ (last slice done)
                                       ▼
                                AWAITING_APPROVAL
                                       │ action=approve stage=FINAL
                                       ▼
                                RELEASE_READY
                                       │ action=reopen (any task)
                                       ▼
                                STALE
```

`STALE` is reachable from any non-terminal state via repository or
plan drift detection (see `prd_plan.py` and `prd_validate.py`).

## See also

- [`speckit.prd.plan`](./speckit.prd.plan.md) — produces the ledger on
  approve
- [`speckit.prd.validate`](./speckit.prd.validate.md) — re-runs the
  full structural, traceability, and orchestration checks
- `extensions/prd/README.md` — extension overview, methodology
  provenance, and recovery rules