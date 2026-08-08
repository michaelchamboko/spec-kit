# PRD-to-Plans Translation Extension

A bundled Spec Kit extension that turns a **PRD** into a **codebase-aware
spec-of-specs**, **numbered vertical-slice specifications**, and
**implementation-ready task packets** before any code is written.

The workflow is the missing handoff between an upstream product artifact
(PRD, design brief, RFC, or technical-discovery note) and the standard
Spec-Driven Development lifecycle (`/speckit.specify → /speckit.plan →
/speckit.tasks → /speckit.analyze → /speckit.implement`). It pairs the
[Spec Kit](https://github.com/github/spec-kit) CLI with the BMAD-method,
OpenSpec, Taskmaster, and **v3.5-protocol** methodologies, and emits
artifacts the existing `/speckit.plan` and `/speckit.tasks` workflows can
consume directly.

## Status

**Bundled**, but **opt-in**. Install only when a project needs PRD
decomposition. The extension registers no lifecycle hooks and never
modifies source code.

## Commands

| Command | Phase | Output |
|---------|-------|--------|
| `speckit.prd.plan` | Capture or reconcile a PRD workspace; decompose into vertical slices; stop for approval before generating child artifacts | `.specify/specs/<slug>/000-spec-of-specs/{manifest.yml, source/, requirements.md, decisions.md, codegraph.md, roadmap.md, reviews/}` |
| `speckit.prd.validate` | Re-run deterministic structural, traceability, graph-freshness, and readiness checks without modifying source code | JSON summary, exit `0`/`1` |

The plan command has two modes:

- **Intake** — `source="<path|URL|pasted>"` `[slug=<slug>]`. Creates the
  workspace, preserves the original PRD source, extracts stable IDs for
  requirements and decisions, runs the first Council review, and stops
  for human approval at `AWAITING_DECOMPOSITION_APPROVAL`.
- **Approve** — `slug=<slug> approve=true`. Freezes the slice sequence,
  materializes numbered slice directories, generates the per-slice
  `spec.md`, `plan.md`, `tasks.md`, and `code-impact.md`, and sets the
  state to `PLAN_READY` after a final Council review.

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
│   └── reviews/
│       ├── decomposition-v001.md       # Five-lens Council review (v3.5)
│       └── final-v001.md               # Five-lens Council review (final)
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

The PRD extension composes four upstream methodologies, each chosen for a
specific capability. The contribution to spec-kit is the **wiring** —
the deterministic I/O, manifest schema, lifecycle gates, and tests —
not any of the source methodologies themselves.

### Spec Kit (this repository)

- **Provides:** the existing `/speckit.plan`, `/speckit.tasks`, and
  `/speckit.analyze` commands that consume the per-slice artifacts this
  extension produces. Also provides the **extension manifest schema**,
  the install layout, the wheel `force-include` mechanism, and the
  catalog format.
- **Used by:** every generated `plan.md`, `tasks.md`, and `code-impact.md`.

### BMAD-method

- **What it gives us:** the **vertical-slice decomposition discipline**.
  BMAD insists that every slice is the smallest independently testable,
  reviewable, and committable unit of behavior. Slices are ordered by
  business value, not by technical layer.
- **How we apply it:** in `roadmap.md`. Each slice carries one primary
  owning requirement, its acceptance criteria (`AC-SLC-NNN-MMM`), and
  the cross-cutting requirements that also touch it.
- **Reference:** <https://github.com/bmad-code-org/BMAD-METHOD>

### OpenSpec

- **What it gives us:** the **spec-of-specs** pattern: an artifact
  above individual feature specs that records the dependency graph,
  critical path, non-goals, and shared interfaces across all slices.
  OpenSpec emphasizes that the spec-of-specs is itself reviewable
  before any per-slice artifacts are generated.
- **How we apply it:** as `000-spec-of-specs/{manifest.yml, roadmap.md}`
  plus the five-lens Council review against the decomposition.
- **Reference:** <https://github.com/Fission-AI/OpenSpec>

### Taskmaster

- **What it gives us:** the **task-packet discipline**. A task packet is
  a self-contained unit of work that one fresh coding-agent session can
  pick up, execute, and produce one reviewable commit. Taskmaster's
  contract is that the packet must include the verification recipe,
  edge cases, failure behavior, security considerations, migration
  notes, observability hooks, deployment constraints, rollback, and a
  recovery trigger for invalidating stale evidence.
- **How we apply it:** in each `tasks.md`. Every task has an
  `SLC-NNN-TMMM` ID and the packet shape above.
- **Reference:** <https://github.com/taskmaster-ai/taskmaster-ai>

### v3.5 protocol (Council review)

- **What it gives us:** the **five-lens Council review** that runs both
  at decomposition time and again at finalization time. The five
  lenses are Source Authority, Evidence, Traceability, Codegraph
  Controls, and Simplicity.
- **How we apply it:** the `reviews/decomposition-v001.md` and
  `reviews/final-v001.md` artifacts record **independent answers,
  blind review, debate, and verdict** for each lens, and the workflow
  refuses to advance state if Critical or Important findings remain
  unresolved.
- **Reference:** v3.5 protocol as documented in the PRD methodology
  notes; this extension implements the deterministic envelope (file
  presence, format) and delegates the actual reasoning to the active
  agent.

## Determinism and safety guarantees

- **Read-only validate.** `speckit.prd.validate` never writes
  artifacts. It performs structural, traceability, graph-freshness, and
  state-consistency checks and exits non-zero with a structured failure
  list when any check fails.
- **Path containment.** Every script variant refuses any ancestor
  traversal that would resolve outside the project root, and refuses
  any symlinked component. Mirrors the existing
  `agent-context` and `git` extension safety posture.
- **Symlink refusal.** `tests/symlinks/outside-target` and similar
  crafted projects cannot redirect reads or writes outside `.specify/`.
- **Source preservation.** The original PRD bytes are preserved at
  `source/prd-v<version>.<ext>` with a SHA-256 digest recorded in
  `manifest.yml`. The normalized form is the only derivative.
- **Stable IDs.** Requirement IDs (`PRD-FR-001`, `PRD-NFR-001`),
  decision IDs (`DEC-001`), slice IDs (`SLC-001`), acceptance
  criteria IDs (`AC-SLC-NNN-MMM`), and task IDs (`SLC-NNN-TMMM`)
  follow a fixed schema and are validated by `speckit.prd.validate`.
- **Frozen sequence.** Once `approve=true` runs, the slice directory
  prefixes (`001-…`, `002-…`, …) are frozen. Re-prioritization
  reorders `roadmap.md` but never renames folders.
- **No AI model calls in scripts.** The scripts are pure I/O. All
  reasoning happens in the command body driven by the active agent.

## Installation

```bash
# Inside an initialized Spec Kit project
specify extension add prd

# Or pin to a local checkout for development
specify extension add prd --from /path/to/spec-kit/extensions/prd
```

The extension is **bundled** with the CLI — there is no separate
download. After installation the two commands appear in the agent's
command list.

## Typical flow

```bash
# 1. Intake the PRD (pasted text, local file, or fetched URL)
/speckit.prd.plan source=./docs/my-prd.md slug=my-feature
# → workspace created, slices proposed, Council review recorded,
#   state = AWAITING_DECOMPOSITION_APPROVAL

# 2. After human review of the decomposition Council report
/speckit.prd.plan slug=my-feature approve=true
# → slice sequence frozen, per-slice spec.md / plan.md / tasks.md /
#   code-impact.md generated, final Council review recorded,
#   state = PLAN_READY

# 3. Hand each numbered slice to the existing SDD lifecycle
/speckit.plan 001-my-feature-slug
/speckit.tasks 001-my-feature-slug
# (and so on for each slice)

# 4. Re-run validation any time to check evidence freshness
/speckit.prd.validate slug=my-feature phase=final
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

## Disabling

```bash
specify extension disable prd
specify extension enable prd
```

The extension registers no lifecycle hooks, so disabling simply removes
the two commands from the agent's command list.

## Testing

The bundled tests live under `tests/extensions/` and exercise:

- Manifest layout and command file presence
- Catalog registration
- `ExtensionManifest` validation against the bundled `extension.yml`
- Slug normalization, SHA-256 helpers, manifest round-trip (Python
  helpers, parity with bash and PowerShell twins)
- Intake → approve → finalize flow (Python twin)
- Validation behavior across all phase/state combinations
- Symlink escape refusal
- Source digest mismatch detection

Bash and PowerShell parity tests run when the relevant interpreter is
available and skip cleanly when it is not.

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
- [`spec-kit` core](https://github.com/github/spec-kit) — Spec-Driven
  Development lifecycle that consumes the per-slice artifacts
- BMAD-method, OpenSpec, Taskmaster, and the v3.5 Council protocol
  (see *Methodology Provenance* above)