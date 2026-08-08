---
description: "Re-runs deterministic structural, traceability, graph-freshness, and readiness checks without modifying source code"
tools:
  - 'bash/prd_validate.sh'
  - 'python/prd_validate.py'
scripts:
  sh: ../../scripts/bash/prd_validate.sh
  ps: ../../scripts/powershell/prd_validate.ps1
  py: ../../scripts/python/prd_validate.py
---

# PRD-to-Plans: Validate Command

Re-run deterministic structural, traceability, graph-freshness, and readiness checks without modifying source code.

## User Input

```text
$ARGUMENTS
```

**Parse arguments**: `slug=<slug>` required, optional `phase=decomposition|final`.

---

## Ancestor Path Safety

Before any filesystem lookup:

1. Verify `.specify` and `.specify/specs` are real directories (not symlinks) resolving inside the project root
2. Refuse and report if either exists as a symlink or escapes the root

Resolve slug:
- Explicit `slug=…` → use normalized value
- Conversation context → use confirmed slug
- Single existing directory under `.specify/specs/` → use it
- Otherwise stop and ask

Normalize slug (same rules as plan command).

Set `PRD_SLUG` and `PRD_DIR = .specify/specs/<PRD_SLUG>`.

---

## Path Safety

Resolve project root and real, symlink-resolved path of `PRD_DIR/` and every artifact.

**Refuse and report — never follow** — if any path component is a symlink or escapes project root.

---

## Prerequisites

- `PRD_DIR/000-spec-of-specs/manifest.yml` MUST exist
- If `phase=decomposition`: manifest state must be `AWAITING_DECOMPOSITION_APPROVAL` or later
- If `phase=final`: manifest state must be `PLANNING` or `PLAN_READY`
- If phase omitted: run all applicable checks for current state

---

## Validation Checks

### Phase: decomposition (or all if phase omitted and state >= AWAITING_DECOMPOSITION_APPROVAL)

1. **Manifest Structure**
   - `manifest.yml` conforms to schema (required fields, types, enum values)
   - `schema_version` is supported
   - PRD identity, slug, active_version, source_digest, source_authority present
   - Repository root, head, dirty_fingerprint present
   - Graph provider, indexed_state, fallback_mode, evidence_path present
   - Decomposition approval_version, frozen_sequence present
   - Slices array present with required fields per slice (id, directory, dependencies, order, requirements, state)
   - Artifact_state valid enum

2. **Source Integrity**
   - `source/prd-v<active_version>.normalized.md` exists
   - Source digest in manifest matches SHA-256 of normalized file
   - Original source preserved at `source/prd-v<active_version>.<ext>`

3. **Requirements Traceability**
   - `requirements.md` exists and parses
   - Every requirement has stable ID (`PRD-FR-*` or `PRD-NFR-*`)
   - Every requirement has source location in normalized PRD
   - No duplicate requirement IDs
   - Cross-cutting requirements properly declare additional affected slices

4. **Slice Decomposition**
   - `roadmap.md` exists and parses
   - Every requirement has exactly one primary owning slice
   - Slice IDs are stable (`SLC-001`, `SLC-002`, …) and sequential
   - Slice directories match manifest (`001-<slug>`, `002-<slug>`, …)
   - Dependency graph is acyclic
   - Critical path identified
   - Non-goals explicitly listed
   - Shared interfaces and acceptance boundaries defined
   - Acceptance criteria IDs follow pattern `AC-SLC-<NNN>-<MMM>`

5. **Codegraph Evidence (Brownfield)**
   - `codegraph.md` exists
   - If greenfield: evidence marked `Not applicable`, proposed architecture clearly labelled
   - If brownfield: provider/version, indexed commit, timestamp, exclusions recorded
   - Queries, returned symbols, dependency edges, inspected files recorded
   - Affected modules, callers, callees, routes, producers, consumers, contracts, persistence, tests, regression paths recorded
   - If GitNexus unavailable: fallback tracing evidence equivalent
   - No `NEEDS_CLARIFICATION` without explicit reason

6. **Council Review (Decomposition)**
   - `reviews/decomposition-v<version>.md` exists
   - Five lenses documented: Source Authority, Evidence, Traceability, Codegraph Controls, Simplicity
   - Independent answers, blind review, debate, verdict recorded
   - No unresolved Critical or Important findings

7. **State Consistency**
   - Manifest state matches actual artifact presence
   - `AWAITING_DECOMPOSITION_APPROVAL` → child slice directories must NOT exist
   - `PLANNING` or `PLAN_READY` → child slice directories must exist

---

### Phase: final (or all if phase omitted and state >= PLANNING)

1. **Child Artifact Completeness**
   For every slice in manifest (in frozen order):
   - `spec.md` exists with: requirement/slice/decision/evidence/acceptance IDs, vertical slice scope, acceptance criteria, non-goals
   - `plan.md` exists with: bounded responsibility, explicit dependencies, verified existing files/symbols or proposed paths, consumed/produced interfaces, ordered implementation instructions, allowed/non-scope/forbidden changes
   - `tasks.md` exists with: task packets with IDs `SLC-<NNN>-T<MMM>`, test-first instructions, edge cases, failure behavior, security, migration, observability, deployment, rollback, recovery trigger, enough context for fresh agent + reviewable commit
   - `code-impact.md` exists with: verified existing code references, proposed changes, regression paths, test coverage map

2. **Requirement Coverage (100%)**
   - Every mandatory PRD requirement covered by at least one task
   - No uncovered requirements
   - Every acceptance criterion mapped to tasks and decisive verification
   - Every task mapped back to requirements and code evidence

3. **Dependency Integrity**
   - No dependency cycles in slice graph
   - No unknown slice references in dependencies
   - No contradictory ownership of shared interfaces

4. **Code Evidence Freshness**
   - For brownfield: repository HEAD and dirty fingerprint match manifest
   - If changed: evidence refreshed or affected slices marked `STALE`
   - Every existing code reference directly verified (file exists, symbol exists)

5. **Council Review (Final)**
   - `reviews/final-v<version>.md` exists
   - Five lenses re-evaluated on complete artifact set
   - No unresolved Critical or Important findings

6. **State Consistency**
   - `PLAN_READY` → all child artifacts present and passing
   - `STALE` → reason recorded in manifest per slice

---

## Output

On success: exit 0, print summary:
```
slug: <PRD_SLUG>
manifest: <path to manifest.yml>
phase: <decomposition|final|all>
checks_passed: <count>
checks_failed: 0
```

On failure: exit 1, print:
```
slug: <PRD_SLUG>
manifest: <path to manifest.yml>
phase: <decomposition|final|all>
checks_passed: <count>
checks_failed: <count>
failures:
  - <check name>: <specific failure detail>
  - ...
```

Never modify source code or artifact files. This is a read-only validation.

---

## Guardrails

- Read-only — no writes to any artifact
- Path containment — all reads inside project root, symlink refusal
- Deterministic — same input always produces same result
- No AI model calls — purely structural and evidence-based checks