# PRD-to-Plans Translation Extension

A bundled Spec Kit extension that turns a **PRD** into a **codebase-aware
spec-of-specs**, **numbered vertical-slice specifications**, and
**implementation-ready task packets**, then drives a deterministic,
one-task-at-a-time waterfall orchestrator over the same artifacts —
all before any code is written.

The workflow is the missing handoff between an upstream product artifact
(PRD, design brief, RFC, or technical-discovery note) and the standard
Spec-Driven Development lifecycle (`/speckit.specify → /speckit.plan →
/speckit.tasks → /speckit.analyze → /speckit.implement`). It composes
the [Spec Kit](https://github.com/github/spec-kit) CLI with the BMAD-method,
OpenSpec, Taskmaster, **v3.5-protocol**, Karpathy simplicity rules, the
writing-plans task-packet discipline, architecture analysis, and
Context7-backed technical research plus complete regression planning.
The orchestrator emits evidence and approval records that the rest of
the spec-kit lifecycle (and human reviewers) can consume.

## Status

**Bundled**, but **opt-in**. Install only when a project needs PRD
decomposition. The extension registers no lifecycle hooks and never
modifies source code.

## Commands

| Command | Phase | Output |
|---------|-------|--------|
| `speckit.prd.plan` | Capture or reconcile a PRD workspace; decompose into vertical slices; produce the orchestration ledger on approve; refuse `PLAN_READY` without the ledger | `.specify/specs/<slug>/000-spec-of-specs/{manifest.yml, source/, requirements.md, decisions.md, codegraph.md, roadmap.md, reviews/, orchestration.yml}` |
| `speckit.prd.orchestrate` | Drive a strict, one-task-at-a-time waterfall over `orchestration.yml`; record evidence, blockers, reopens, and stage approvals; never edit implementation source | Updates to `.specify/specs/<slug>/000-spec-of-specs/orchestration.yml` and `orchestration-evidence/<task-id>/<kind>/<check-id>.{pass,fail}` |
| `speckit.prd.validate` | Re-run deterministic structural, traceability, graph-freshness, orchestration-ledger, evidence, regression, and readiness checks without modifying source code | JSON summary, exit `0`/`1` |

The plan command has two modes:

- **Intake** — `source="<path|URL|pasted>"` `[slug=<slug>]`. Creates the
  workspace, preserves the original PRD source, extracts stable IDs for
  requirements and decisions, runs the first Council review, and stops
  for human approval at `AWAITING_DECOMPOSITION_APPROVAL`.
- **Approve** — `slug=<slug> approve=true`. Freezes the slice sequence,
  materializes numbered slice directories, generates the per-slice
  `spec.md`, `plan.md`, `tasks.md`, and `code-impact.md`, emits the
  `1.1` orchestration ledger, and sets the state to `PLAN_READY` (after
  the final Council review passes).

The orchestrator accepts these actions:

| Action | Behavior |
|--------|----------|
| `initialize` | Validate every task ID in a legacy `1.0` workspace and materialize the `1.1` ledger without renumbering. |
| `status` | Render the complete prioritized checklist, current task, blockers, and pending approval. |
| `next` | Return the one eligible task packet, its interfaces, documentation references, and completion checks. |
| `start task=<id> owner=<label>` | Claim the next eligible task; reject any second concurrent active task. |
| `evidence task=<id> check=<id> result=pass\|fail path=<relative>` | Record manual or external evidence. |
| `complete task=<id>` | Run declared automated checks; complete only when every required check has passing evidence. |
| `block task=<id> reason="<text>"` | Stop progression and record the blocker. |
| `reopen task=<id> reason="<text>"` | Invalidate the task and every transitive downstream task and approval. |
| `approve stage=<SLC-NNN\|FINAL> approved_by="<identity>"` | Unlock the next stage only after its exit gate passes. |

## Layout

```text
.specify/specs/<slug>/
├── 000-spec-of-specs/
│   ├── manifest.yml                    # State, source digest, slices, reviews
│   ├── source/
│   │   ├── prd-v001.<ext>              # Original PRD bytes (preserved as-is)
│   │   └── prd-v001.normalized.md      # Markdown-normalized form
│   ├── requirements.md                 # PRD-FR-* / PRD-NFR-* with source locations
│   ├── decisions.md                    # DEC-* IDs with rationale
│   ├── codegraph.md                    # GitNexus index, queries, affected surface
│   ├── roadmap.md                      # Vertical-slice decomposition + critical path
│   ├── reviews/
│   │   ├── decomposition-v001.md       # Five-lens Council review (v3.5)
│   │   └── final-v001.md               # Five-lens Council review (final)
│   ├── orchestration.yml               # 1.1 waterfall task-state ledger
│   └── orchestration-evidence/
│       └── <SLC-NNN-TMMM>/
│           ├── unit/<check-id>.pass
│           ├── regression/<check-id>.pass
│           ├── e2e/<check-id>.pass
│           ├── integration/<check-id>.pass
│           ├── migration/<check-id>.pass
│           ├── deployment/<check-id>.pass
│           └── rollback/<check-id>.pass
├── 001-<slice-slug>/
│   ├── spec.md                         # Slice spec (vertical, end-to-end)
│   ├── plan.md                         # One bounded responsibility
│   ├── tasks.md                        # SLC-<NNN>-T<MMM> task packets
│   └── code-impact.md                  # Existing code references + proposed changes
├── 002-<slice-slug>/
│   └── …
└── …
```

## Methodology Provenance

The PRD extension composes the upstream methodologies below, each
chosen for a specific capability. The contribution to spec-kit is the
**wiring** — the deterministic I/O, manifest schema, lifecycle gates,
and tests — not any of the source methodologies themselves.

### Spec Kit (this repository)

- **Provides:** the existing `/speckit.plan`, `/speckit.tasks`, and
  `/speckit.analyze` commands that consume the per-slice artifacts this
  extension produces. Also provides the **extension manifest schema**,
  the install layout, the wheel `force-include` mechanism, and the
  catalog format.
- **Used by:** every generated `plan.md`, `tasks.md`, and `code-impact.md`.

### Karpathy Simplicity Rules

- State assumptions and unresolved decisions explicitly in every
  `plan.md` and `tasks.md`. Unstated premises become bugs.
- Choose the smallest working architecture. Forbid speculative
  abstractions, premature frameworks, or unrelated refactors.
- Add no team machinery, model routing, plugin system, or "future
  hook" the current requirements do not demand.
- Drop dead code paths, dead config, dead flags, dead branches.
- Prefer boring technology. New dependency = new documentation
  evidence requirement.

### Writing-Plans Methodology

Every task packet in `tasks.md` carries requirement IDs, acceptance
IDs, allowed scope, forbidden scope, ordered steps, decisive completion
evidence, test-first steps, edge cases, failure behavior, security
notes, migration notes, observability hooks, deployment constraints,
rollback steps, and a recovery trigger that reopens the task when the
evidence becomes stale. A task missing any of these fields is
rejected by `validate`.

### Architecture Analysis

Every slice `plan.md` must map, with evidence file:symbol references,
observed vs target architecture, affected files, data flow, persistence,
security boundaries, migrations, observability, deployment, and
rollback.

### Interfaces

Every consumed or produced interface is declared as either an
**existing symbol** (`existing:file:symbol`) with the resolved
signature, or a **proposed contract** (`proposed:file:symbol -> ret`)
with a single owning slice. No `// TBD` signatures.

### Context7 / Official Documentation Evidence

For every dependency used by the slice:

1. Detect the version from `pyproject.toml`, lockfiles, etc.
2. Prefer **Context7 version-specific** documentation
   (`context7:<lib/id>@<version>`).
3. Fall back to **official primary** documentation
   (`official:<url>@<access-ts>`) when Context7 is unavailable, and
   record the fallback explicitly.
4. Fail closed: if neither is available, the task must be
   `BLOCKED` with reason `missing_documentation_evidence`.
5. Never store Context7 credentials or secrets in generated artifacts.

### BMAD-method

- **Provides:** the **vertical-slice decomposition discipline**. Every
  slice is the smallest independently testable, reviewable, and
  committable unit of behavior; slices are ordered by business value.
- **Applied in:** `roadmap.md`. Each slice carries one primary owning
  requirement, its acceptance criteria (`AC-SLC-NNN-MMM`), and the
  cross-cutting requirements that also touch it.
- **Reference:** <https://github.com/bmad-code-org/BMAD-METHOD>

### OpenSpec

- **Provides:** the **spec-of-specs** pattern: an artifact above
  individual feature specs that records the dependency graph,
  critical path, non-goals, shared interfaces, and slice ordering.
- **Applied as:** `000-spec-of-specs/{manifest.yml, roadmap.md}` plus
  the five-lens Council review against the decomposition.
- **Reference:** <https://github.com/Fission-AI/OpenSpec>

### Taskmaster

- **Provides:** the **task-packet discipline** captured above
  (requirement IDs, acceptance IDs, allowed scope, forbidden scope,
  ordered steps, decisive completion evidence, edge cases, failure
  behavior, security, migration, observability, deployment, rollback,
  recovery trigger).
- **Applied in:** each `tasks.md` and reflected 1:1 in
  `orchestration.yml` task entries.
- **Reference:** <https://github.com/taskmaster-ai/taskmaster-ai>

### v3.5 protocol (Council review)

- **Provides:** the **five-lens Council review** that runs both at
  decomposition time and again at finalization time. The five lenses
  are Source Authority, Evidence, Traceability, Codegraph Controls,
  and Simplicity.
- **Applied in:** `reviews/decomposition-v001.md` and
  `reviews/final-v001.md`. Each lens records independent answers,
  blind review, debate, and verdict; the workflow refuses to advance
  state if Critical or Important findings remain unresolved.

## Determinism and safety guarantees

- **Read-only validate.** `speckit.prd.validate` never writes
  artifacts. It performs structural, traceability, graph-freshness,
  orchestration-ledger, evidence, and regression checks and exits
  non-zero with a structured failure list when any check fails.
- **Path containment.** Every script variant refuses any ancestor
  traversal that would resolve outside the project root, and refuses
  any symlinked component. Mirrors the existing `agent-context` and
  `git` extension safety posture.
- **Symlink refusal.** A crafted project that places `.specify/` or
  any ancestor behind a symlink cannot redirect reads or writes
  outside `.specify/`.
- **Source preservation.** The original PRD bytes are preserved at
  `source/prd-v<version>.<ext>` with a SHA-256 digest recorded in
  `manifest.yml`. A normalized Markdown derivative is required for generated
  workspaces; projects may instead declare the preserved root PRD as the
  canonical source by setting identical `source.canonical_path` and
  `source.preserved_at` values. That explicit form creates no derivative.
- **Orchestration ledger as sole source of truth.**
  `orchestration.yml` (schema `1.1`) is the only machine-readable
  record of waterfall state. Every state-changing action reads,
  mutates, and atomically rewrites it under a filesystem lock; the
  revision counter is monotonic.
- **One active task.** Across the entire ledger exactly zero or one
  task is `IN_PROGRESS`. Multiple `IN_PROGRESS` is rejected by the
  engine and by `validate`.
- **Implementation-source hash invariant.** `prd_orchestrate.py`
  hashes the implementation tree (everything outside `.specify/`,
  `.git/`, etc.) before and after every state-changing action; any
  implementation-file change is reported as a fatal integrity
  violation.
- **Stable IDs.** Requirement, decision, slice, acceptance-criterion,
  and task IDs follow a fixed schema and are validated by
  `speckit.prd.validate`. The orchestrator refuses to renumber any
  existing task ID.
- **Frozen sequence.** Once `approve=true` runs, the slice directory
  prefixes (`001-…`, `002-…`, …) are frozen. Re-prioritization
  reorders `roadmap.md` but never renames folders.
- **No AI model calls in scripts.** The scripts are pure I/O. All
  reasoning happens in the command bodies driven by the active agent.
- **Never store Context7 credentials.** Generated artifacts reference
  environment variable names only.

## Installation

```bash
# Inside an initialized Spec Kit project
specify extension add prd

# Or pin to a local checkout for development
specify extension add prd --from /path/to/spec-kit/extensions/prd
```

The extension is **bundled** with the CLI — there is no separate
download. After installation the three commands appear in the agent's
command list. Skills-based agents also auto-register
`speckit-prd-plan` and `speckit-prd-orchestrate` skill directories
through the existing skill-registration pipeline.

## Typical flow

```bash
# 1. Intake the PRD (pasted text, local file, or fetched URL)
/speckit.prd.plan source=./docs/my-prd.md slug=my-feature
# → workspace created, slices proposed, Council review recorded,
#   state = AWAITING_DECOMPOSITION_APPROVAL

# 2. After human review of the decomposition Council report
/speckit.prd.plan slug=my-feature approve=true
# → slice sequence frozen, per-slice spec.md / plan.md / tasks.md /
#   code-impact.md generated, orchestration.yml emitted,
#   final Council review recorded, state = PLAN_READY

# 3. Materialize the 1.1 ledger if upgrading from a legacy 1.0 plan
/speckit.prd.orchestrate slug=my-feature action=initialize

# 4. Work the waterfall: start the next eligible task, record evidence,
#    complete, request stage approval.
/speckit.prd.orchestrate slug=my-feature action=next
/speckit.prd.orchestrate slug=my-feature action=start task=SLC-001-T001 owner=alice
/speckit.prd.orchestrate slug=my-feature action=evidence task=SLC-001-T001 check=unit.main result=pass path=tests/unit/test_x.py
/speckit.prd.orchestrate slug=my-feature action=complete task=SLC-001-T001
/speckit.prd.orchestrate slug=my-feature action=approve stage=SLC-001 approved_by=alice

# 5. Re-run validation any time to check evidence freshness
/speckit.prd.validate slug=my-feature phase=orchestration

# 6. After every slice is approved, approve FINAL to reach RELEASE_READY
/speckit.prd.orchestrate slug=my-feature action=approve stage=FINAL approved_by=alice
```

## Configuration

The extension reads configuration from
`.specify/extensions/prd/prd-config.yml` (copied from `config-template.yml`
on install). The defaults shipped with the extension are:

| Key | Default | Meaning |
|-----|---------|---------|
| `graph_provider` | `gitnexus` | Preferred codebase index; `direct` for direct tracing fallback |
| `fallback_tracing` | `true` | Permit direct repository and dependency tracing when GitNexus is unavailable |
| `slug_max_length` | `64` | Truncation limit for PRD slug normalization |
| `approval_required` | `true` | Refuse to advance past `AWAITING_DECOMPOSITION_APPROVAL` without explicit `approve=true` |
| `safe_fetch.enabled` | `true` | Allow the plan command to fetch public PRD URLs |
| `safe_fetch.max_size_bytes` | `10485760` | Cap on URL-fetched PRD size (10 MiB) |
| `safe_fetch.allowed_schemes` | `[https]` | Refuse non-HTTPS PRD URLs |
| `stale_threshold_days` | `30` | Days after which `PLAN_READY` artifacts are flagged stale without evidence refresh |
| `manifest_version` | `1.1` | Current manifest schema. `1.0` is read-only; `1.1` is required after `approve=true` |
| `orchestrate.schema_version` | `1.1` | Current orchestration ledger schema |
| `orchestrate.one_active_task_invariant` | `true` | Reject any second `IN_PROGRESS` task |
| `orchestrate.require_regression_per_slice` | `true` | Every task must declare at least one regression check |
| `orchestrate.require_cross_slice_e2e_before_final` | `true` | `final_gate.cross_slice_e2e` is required before `RELEASE_READY` |
| `orchestrate.documentation_authority.preferred` | `context7` | Preferred documentation source |
| `orchestrate.documentation_authority.accepted_fallback` | `official` | Accepted fallback authority |
| `orchestrate.documentation_authority.fail_closed_when_missing` | `true` | Fail closed when neither is available |
| `plan.refuse_plan_ready_without_ledger` | `true` | `--finalize` refuses without `orchestration.yml` |
| `plan.emit_orchestration_ledger_on_approve` | `true` | Plan command writes the ledger atomically alongside the manifest on approve |

## Disabling

```bash
specify extension disable prd
specify extension enable prd
```

The extension registers no lifecycle hooks, so disabling simply removes
the three commands from the agent's command list.

## Testing

The bundled tests live under `tests/extensions/prd/` and exercise:

- Manifest layout, README, command files, script variants
- Catalog registration
- `ExtensionManifest` validation against the bundled `extension.yml`
- Slug normalization, SHA-256, YAML round-trip, ledger helpers
  (Python)
- Intake → approve → finalize flow (Python twin)
- Orchestrator actions: `initialize`, `status`, `next`, `start`,
  `evidence`, `complete`, `block`, `reopen`, `approve` (Python twin)
- One-active-task invariant, `not_next_eligible` and
  `another_task_in_progress` errors, evidence completeness,
  dependency cycle detection, reopen cascade
- Implementation-source hash invariant (orchestrator refuses to write
  when implementation files change)
- Refuse `PLAN_READY` without ledger
- Validation behavior across all phase/state combinations including
  the new `orchestration` phase
- Ledger integrity: schema_version, revision monotonicity,
  one-active-task invariant, evidence coverage, placeholder check
  detection, documentation-evidence format, final-gate completeness
- Symlink escape refusal
- Source digest mismatch detection

Bash and PowerShell parity tests run when the relevant interpreter is
available and skip cleanly when it is not. PowerShell script parsing
is verified via a dedicated `__smoke_pwsh_parse.py` static check that
runs on Windows hosts where pwsh subprocess hangs.

## See also

- [`extensions/EXTENSION-DEVELOPMENT-GUIDE.md`](../EXTENSION-DEVELOPMENT-GUIDE.md) —
  how extensions are built, registered, and bundled into the wheel
- [`extensions/EXTENSION-USER-GUIDE.md`](../EXTENSION-USER-GUIDE.md) —
  user-facing installation and command reference
- [`extensions/git`](../git) — the closest sibling extension (also a
  planning-oriented workflow with bash/PowerShell/Python script
  parity)
- [`extensions/assess`](../assess) — the upstream discovery workflow
  that often feeds a PRD into this extension
- [`speckit.prd.plan`](./commands/speckit.prd.plan.md) — the planning
  command spec (methodology, ledger generation, guardrails)
- [`speckit.prd.orchestrate`](./commands/speckit.prd.orchestrate.md) —
  the waterfall orchestrator command spec (actions, state diagram,
  recovery rules)
- [`speckit.prd.validate`](./commands/speckit.prd.validate.md) — the
  validation command spec (all phases)
- [`spec-kit` core](https://github.com/github/spec-kit) — Spec-Driven
  Development lifecycle that consumes the per-slice artifacts
- BMAD-method, OpenSpec, Taskmaster, v3.5 Council protocol, Karpathy
  simplicity rules, writing-plans methodology, and Context7 (see
  *Methodology Provenance* above)
