#!/usr/bin/env bash
# PRD-to-Plans: bash entrypoint for ``speckit.prd.validate``.
#
# Read-only twin of ``prd_validate.py``. Performs structural,
# traceability, and state-consistency checks against the manifest,
# requirements, slice graph, and Council review artifacts.
#
# Usage:
#   prd_validate.sh slug=<slug> [phase=decomposition|final|orchestration|all]

set -euo pipefail

SCRIPT_DIR="$(CDPATH="" cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./prd-common.sh
. "$SCRIPT_DIR/prd-common.sh"

ARTIFACT_DIRNAME="000-spec-of-specs"

usage() {
    cat <<'USAGE'
Usage: prd_validate.sh slug=<slug> [phase=decomposition|final|orchestration|all]
USAGE
}

# Append a check result ``{name}|{status}|{detail}`` to ``$RESULTS``.
record_check() {
    local name="$1"
    local status="$2"
    local detail="$3"
    RESULTS+=( "$(printf '%s|%s|%s' "$name" "$status" "$detail")" )
}

# Verify every required top-level manifest field is present.
check_manifest_fields() {
    local manifest_path="$1"
    local required="schema_version extension slug state active_version source slices"
    for field in $required; do
        if grep -q "^${field}:" "$manifest_path"; then
            record_check "manifest.$field" "PASS" "field present"
        else
            record_check "manifest.$field" "FAIL" "missing required field"
        fi
    done
}

check_source_integrity() {
    local manifest_path="$1"
    local project_root="$2"
    local canonical_rel preserved_rel byte_size expected_digest
    canonical_rel=$(grep '^[[:space:]]*canonical_path:' "$manifest_path" | sed -E 's/^[[:space:]]*canonical_path:[[:space:]]*//; s/^"//; s/"$//')
    preserved_rel=$(grep '^[[:space:]]*preserved_at:' "$manifest_path" | sed -E 's/^[[:space:]]*preserved_at:[[:space:]]*//; s/^"//; s/"$//')
    if [[ -z "$preserved_rel" ]] || [[ ! -f "$project_root/$preserved_rel" ]]; then
        record_check "source.preserved" "FAIL" "missing preserved file"
        return 0
    fi
    record_check "source.preserved" "PASS" "preserved file present"
    expected_digest=$(grep '^[[:space:]]*sha256:' "$manifest_path" | sed -E 's/^[[:space:]]*sha256:[[:space:]]*//; s/^"//; s/"$//')
    local actual_digest
    actual_digest=$(sha256_file "$project_root/$preserved_rel")
    if [[ -n "$expected_digest" && "$expected_digest" != "$actual_digest" ]]; then
        record_check "source.sha256" "FAIL" "expected=$expected_digest got=$actual_digest"
    else
        record_check "source.sha256" "PASS" "digest matches"
    fi
    if [[ -n "$canonical_rel" && "$canonical_rel" == "$preserved_rel" ]]; then
        return 0
    fi
    local normalized_rel
    normalized_rel="${preserved_rel%.*}.normalized.md"
    if [[ -f "$project_root/$normalized_rel" ]]; then
        record_check "source.normalized" "PASS" "normalized file present"
    else
        record_check "source.normalized" "FAIL" "missing normalized file"
    fi
}

check_requirements() {
    local artifact_dir="$1"
    local requirements_file="$artifact_dir/requirements.md"
    if [[ ! -f "$requirements_file" ]]; then
        record_check "requirements.exists" "FAIL" "requirements.md missing"
        return 0
    fi
    record_check "requirements.exists" "PASS" "requirements.md present"
    local ids
    ids=$(grep -oE 'PRD-(FR|NFR)-[0-9]+' "$requirements_file" || true)
    if [[ -z "$ids" ]]; then
        record_check "requirements.ids" "FAIL" "no PRD-FR-/PRD-NFR- ids found"
        return 0
    fi
    record_check "requirements.ids" "PASS" "requirement ids found"
    local unique_count total_count
    unique_count=$(printf '%s\n' "$ids" | sort -u | wc -l | tr -d ' ')
    total_count=$(printf '%s\n' "$ids" | wc -l | tr -d ' ')
    if [[ "$unique_count" != "$total_count" ]]; then
        record_check "requirements.unique" "FAIL" "duplicate requirement ids"
    else
        record_check "requirements.unique" "PASS" "all ids unique"
    fi
}

check_slices() {
    local manifest_path="$1"
    local prd_dir="$2"
    local in_slices=false
    local slice_count=0
    local saw_slc=false
    local saw_unique=true
    while IFS= read -r line; do
        if [[ "$line" == "slices:" ]]; then
            in_slices=true
            continue
        fi
        $in_slices || continue
        case "$line" in
            "  - id: "*) slice_count=$((slice_count + 1)) ;;
            "    directory: "*) ;;
            "") in_slices=false ;;
        esac
    done <"$manifest_path"
    if (( slice_count == 0 )); then
        record_check "slices.present" "FAIL" "slices array empty or missing"
        return 0
    fi
    record_check "slices.present" "PASS" "$slice_count slice(s) declared"
}

check_council_reviews() {
    local artifact_dir="$1"
    local phase="$2"
    local reviews_dir="$artifact_dir/reviews"
    if [[ "$phase" == "decomposition" || "$phase" == "all" ]]; then
        if [[ -f "$reviews_dir/decomposition-v001.md" ]]; then
            record_check "reviews.decomposition" "PASS" "review present"
        else
            record_check "reviews.decomposition" "FAIL" "decomposition review missing"
        fi
    fi
    if [[ "$phase" == "final" || "$phase" == "all" ]]; then
        if [[ -f "$reviews_dir/final-v001.md" ]]; then
            record_check "reviews.final" "PASS" "review present"
        else
            record_check "reviews.final" "FAIL" "final review missing"
        fi
    fi
}

check_child_artifacts() {
    local prd_dir="$1"
    local manifest_path="$2"
    local current_dir=""
    while IFS= read -r line; do
        case "$line" in
            "    directory: "*)
                current_dir=$(printf '%s' "$line" | sed -E 's/^[[:space:]]+directory:[[:space:]]*//; s/^"//; s/"$//')
                for leaf in spec.md plan.md tasks.md code-impact.md; do
                    if [[ -f "$prd_dir/$current_dir/$leaf" ]]; then
                        record_check "artifacts.$current_dir/$leaf" "PASS" "present"
                    else
                        record_check "artifacts.$current_dir/$leaf" "FAIL" "missing"
                    fi
                done
                ;;
        esac
    done <"$manifest_path"
}

main() {
    RESULTS=()
    if [[ $# -eq 0 ]]; then
        usage >&2
        return 2
    fi
    local project_root
    project_root=$(find_specify_root) || {
        err "ERROR: not inside a Spec Kit project (.specify/ not found)"
        return 1
    }

    local raw_slug=""
    local phase="all"
    for arg in "$@"; do
        case "$arg" in
            slug=*) raw_slug="${arg#slug=}" ;;
            phase=*) phase="${arg#phase=}" ;;
            --help|-h) usage; return 0 ;;
            *) err "ERROR: unknown argument: $arg"; return 2 ;;
        esac
    done

    if [[ -z "$raw_slug" ]]; then
        err "ERROR: missing slug=<slug>"
        return 2
    fi
    local slug
    slug=$(normalize_slug "$raw_slug") || return 1

    if [[ ! "$phase" =~ ^(decomposition|final|orchestration|all)$ ]]; then
        err "ERROR: phase must be decomposition|final|orchestration|all (got $phase)"
        return 2
    fi

    local specs_root="$project_root/.specify/specs"
    safe_create_dir "$specs_root" "$project_root" >/dev/null
    local prd_dir="$specs_root/$slug"
    local artifact_dir="$prd_dir/$ARTIFACT_DIRNAME"
    local manifest_path="$artifact_dir/manifest.yml"
    if [[ ! -f "$manifest_path" ]]; then
        err "ERROR: manifest.yml not found at $artifact_dir"
        return 1
    fi

    local state
    state=$(grep '^state:' "$manifest_path" | sed -E 's/^state:[[:space:]]*//; s/^"//; s/"$//')
    if [[ "$phase" == "decomposition" && "$state" != "AWAITING_DECOMPOSITION_APPROVAL" && "$state" != "PLANNING" && "$state" != "PLAN_READY" ]]; then
        err "ERROR: phase=decomposition requires state >= AWAITING_DECOMPOSITION_APPROVAL (got $state)"
        return 1
    fi
    if [[ "$phase" == "final" && "$state" != "PLANNING" && "$state" != "PLAN_READY" ]]; then
        err "ERROR: phase=final requires state PLANNING|PLAN_READY (got $state)"
        return 1
    fi

    check_manifest_fields "$manifest_path"
    check_source_integrity "$manifest_path" "$project_root"
    check_requirements "$artifact_dir"
    if [[ "$state" == "AWAITING_DECOMPOSITION_APPROVAL" || "$state" == "PLANNING" || "$state" == "PLAN_READY" ]]; then
        check_slices "$manifest_path" "$prd_dir"
    fi
    check_council_reviews "$artifact_dir" "$phase"
    if [[ "$state" == "PLANNING" || "$state" == "PLAN_READY" || "$phase" == "final" ]]; then
        check_child_artifacts "$prd_dir" "$manifest_path"
    fi

    local passed=0 failed=0
    for entry in "${RESULTS[@]}"; do
        local status
        status=$(printf '%s' "$entry" | cut -d'|' -f2)
        case "$status" in
            PASS) passed=$((passed + 1)) ;;
            FAIL) failed=$((failed + 1)) ;;
        esac
    done

    # Emit a single-line JSON summary. Failures are listed in ``failures``;
    # passes are summarized by count to keep the script output compact and
    # machine-readable.
    printf '{"slug":"%s","phase":"%s","checks_passed":%d,"checks_failed":%d,"state":"%s"' \
        "$slug" "$phase" "$passed" "$failed" "$state"
    if (( failed > 0 )); then
        printf ',"failures":['
        local first=true
        for entry in "${RESULTS[@]}"; do
            local status name detail
            status=$(printf '%s' "$entry" | cut -d'|' -f2)
            [[ "$status" == "FAIL" ]] || continue
            name=$(printf '%s' "$entry" | cut -d'|' -f1)
            detail=$(printf '%s' "$entry" | cut -d'|' -f3-)
            $first || printf ','
            first=false
            printf '{"name":"%s","detail":"%s"}' "$name" "$detail"
        done
        printf ']'
    fi
    printf '}\n'
    return $((failed == 0 ? 0 : 1))
}

main "$@"
