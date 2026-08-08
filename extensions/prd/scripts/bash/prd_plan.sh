#!/usr/bin/env bash
# PRD-to-Plans: bash entrypoint for ``speckit.prd.plan``.
#
# Pure deterministic I/O twin of ``prd_plan.py``. The command body drives
# the AI-assisted decomposition; this script materializes the workspace,
# preserves the source, and freezes the slice sequence on approval.
#
# Usage:
#   prd_plan.sh source="<path|pasted>" [slug=<slug>]
#   prd_plan.sh slug=<slug> approve=true
#   prd_plan.sh slug=<slug> --finalize

set -euo pipefail

SCRIPT_DIR="$(CDPATH="" cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./prd-common.sh
. "$SCRIPT_DIR/prd-common.sh"

PRD_VERSION_PREFIX="${PRD_VERSION_PREFIX:-prd-v}"
DEFAULT_VERSION="v001"
ARTIFACT_DIRNAME="000-spec-of-specs"

utc_now_iso() {
    # GNU/BSD-portable UTC timestamp using ``date -u +%FT%TZ``; ``-u`` is
    # accepted by both ``coreutils`` and BSD/macOS ``date``.
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

read_local_source() {
    local source_path="$1"
    cat -- "$source_path"
}

# Parse a ``source=...`` value into (bytes, original_name, extension).
# Mirrors ``_resolve_source_bytes`` in ``prd_plan.py``. Local file: read
# bytes; otherwise treat the value as already-fetched bytes supplied by the
# command body (e.g. after URL fetching and normalization upstream).
resolve_source_bytes() {
    local source_value="$1"
    if [[ "$source_value" == "-" ]]; then
        cat
        return
    fi
    if [[ -f "$source_value" ]]; then
        read_local_source "$source_value"
        return
    fi
    printf '%s' "$source_value"
}

next_slice_prefix() {
    local count="$1"
    printf '%03d' "$((count + 1))"
}

# Build the manifest body (YAML) as a string. Mirrors ``cmd_intake``.
cmd_intake() {
    local project_root="$1"
    local slug="$2"
    local source_value="$3"
    local preserved_rel="$4"
    local byte_size="$5"
    local sha256="$6"
    local original_name="$7"
    local extension="$8"
    local authority="pasted"
    if [[ "$source_value" != "-" ]] && [[ -f "$source_value" ]]; then
        authority="file"
    fi
    local now
    now=$(utc_now_iso)
    cat <<EOF
schema_version: "1.0"
extension: prd
slug: "$slug"
state: AWAITING_DECOMPOSITION_APPROVAL
created_at: "$now"
active_version: "$DEFAULT_VERSION"
source:
  authority: "$authority"
  fetched_at: "$now"
  original_name: "$original_name"
  byte_size: $byte_size
  sha256: "$sha256"
  preserved_at: "$preserved_rel"
slices: []
decomposition_version: "$DEFAULT_VERSION"
frozen_sequence: false
EOF
}

# Render slice entries appended to ``manifest.yml`` under the ``slices:`` key.
# Each slice is a small mapping. Mirrors ``cmd_approve`` in Python.
cmd_approve_body() {
    # Iterate slice IDs from stdin (one per line) and render a YAML list.
    local project_root="$1"
    local slug="$2"
    cat <<EOF
schema_version: "1.0"
extension: prd
slug: "$slug"
state: PLANNING
frozen_sequence: true
decomposition_approval_version: "$DEFAULT_VERSION"
slices:
EOF
    local index=0
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local slice_id slice_slug folder_name
        slice_id=$(printf '%s' "$line" | cut -f1)
        slice_slug=$(printf '%s' "$line" | cut -f2)
        folder_name=$(printf '%s' "$line" | cut -f3)
        index=$((index + 1))
        printf '  - id: "%s"\n' "$slice_id"
        printf '    slug: "%s"\n' "$slice_slug"
        printf '    directory: "%s"\n' "$folder_name"
        printf '    order: %d\n' "$index"
        printf '    state: PLANNING\n'
        printf '    dependencies: []\n'
        printf '    requirements: []\n'
    done
}

# Render ``state: PLAN_READY`` snapshot.
cmd_finalize_body() {
    local slug="$1"
    local now
    now=$(utc_now_iso)
    cat <<EOF
schema_version: "1.0"
extension: prd
slug: "$slug"
state: PLAN_READY
final_review_version: "$DEFAULT_VERSION"
finalized_at: "$now"
EOF
}

# Persist the original source under ``source/prd-<version><ext>``.
preserve_source() {
    local artifact_dir="$1"
    local project_root="$2"
    local source_value="$3"
    local version="$4"
    local extension="$5"

    safe_create_dir "$artifact_dir/source" "$project_root" >/dev/null
    local leaf="prd-${version}${extension}"
    local target="$artifact_dir/source/$leaf"
    require_within "$target" "$project_root" || return 1

    local tmp
    tmp=$(mktemp -- "$artifact_dir/source/.prd-XXXXXX")
    resolve_source_bytes "$source_value" >"$tmp"
    mv -f -- "$tmp" "$target"

    local byte_size
    byte_size=$(wc -c <"$target" | tr -d ' ')
    local digest
    digest=$(sha256_file "$target")
    local rel
    rel=$(printf '%s' "${target#$project_root/}")
    printf '%s\t%s\t%s\t%s' "$rel" "$byte_size" "$digest" "$source_value"
}

usage() {
    cat <<'USAGE'
Usage: prd_plan.sh source="<path|pasted>" [slug=<slug>]
       prd_plan.sh slug=<slug> approve=true
       prd_plan.sh slug=<slug> --finalize
USAGE
}

main() {
    if [[ $# -eq 0 ]]; then
        usage >&2
        return 2
    fi
    local project_root
    project_root=$(find_specify_root) || {
        err "ERROR: not inside a Spec Kit project (.specify/ not found)"
        return 1
    }

    local args=()
    local approve=false
    local finalize=false
    local source_value=""
    local raw_slug=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --approve)
                approve=true
                shift
                ;;
            --finalize)
                finalize=true
                shift
                ;;
            --help|-h)
                usage
                return 0
                ;;
            source=*)
                source_value="${1#source=}"
                source_value="${source_value%\"}"
                source_value="${source_value#\"}"
                shift
                ;;
            slug=*)
                raw_slug="${1#slug=}"
                shift
                ;;
            approve=*)
                if [[ "${1#approve=}" == "true" ]]; then
                    approve=true
                fi
                shift
                ;;
            *)
                args+=("$1")
                shift
                ;;
        esac
    done

    if [[ -z "$raw_slug" ]]; then
        raw_slug=$(printf '%s' "${args[*]:-}" | sed -E 's/[[:space:]]+//g')
    fi
    local slug=""
    if [[ -n "$raw_slug" ]]; then
        slug=$(normalize_slug "$raw_slug") || return 1
    fi

    if $approve; then
        if [[ -z "$slug" ]]; then
            err "ERROR: approve=true requires slug=<slug>"
            return 2
        fi
        # Bash twin: read slice metadata from stdin (one ``id<TAB>slug<TAB>folder``
        # per line, supplied by the command body).
        local specs_root="$project_root/.specify/specs"
        safe_create_dir "$specs_root" "$project_root" >/dev/null
        local prd_dir="$specs_root/$slug"
        safe_create_dir "$prd_dir/$ARTIFACT_DIRNAME" "$project_root" >/dev/null
        local manifest_body
        manifest_body=$(cmd_approve_body "$project_root" "$slug")
        PRD_MANIFEST_BODY="$manifest_body" \
            write_manifest "$prd_dir/$ARTIFACT_DIRNAME" "$project_root" >/dev/null
        info "{\"status\":\"PLANNING\",\"slug\":\"$slug\"}"
        return 0
    fi

    if $finalize; then
        if [[ -z "$slug" ]]; then
            err "ERROR: --finalize requires slug=<slug>"
            return 2
        fi
        local specs_root="$project_root/.specify/specs"
        local prd_dir="$specs_root/$slug"
        local manifest_body
        manifest_body=$(cmd_finalize_body "$slug")
        PRD_MANIFEST_BODY="$manifest_body" \
            write_manifest "$prd_dir/$ARTIFACT_DIRNAME" "$project_root" >/dev/null
        info "{\"status\":\"PLAN_READY\",\"slug\":\"$slug\"}"
        return 0
    fi

    if [[ -z "$source_value" ]]; then
        err "ERROR: intake mode requires source=\"<path|pasted>\""
        return 2
    fi
    if [[ -z "$slug" ]]; then
        slug=$(ensure_unique_slug "$project_root/.specify/specs" "prd") || return 1
    else
        slug=$(ensure_unique_slug "$project_root/.specify/specs" "$slug") || return 1
    fi

    local specs_root="$project_root/.specify/specs"
    local prd_dir="$specs_root/$slug"
    local artifact_dir="$prd_dir/$ARTIFACT_DIRNAME"
    safe_create_dir "$artifact_dir" "$project_root" >/dev/null

    local extension=".md"
    if [[ "$source_value" != "-" ]] && [[ -f "$source_value" ]]; then
        extension=".${source_value##*.}"
        [[ "$extension" == "$source_value" ]] && extension=".md"
    fi

    local preserved
    preserved=$(preserve_source "$artifact_dir" "$project_root" "$source_value" "$DEFAULT_VERSION" "$extension")
    local preserved_rel byte_size digest original_name
    preserved_rel=$(printf '%s' "$preserved" | cut -f1)
    byte_size=$(printf '%s' "$preserved" | cut -f2)
    digest=$(printf '%s' "$preserved" | cut -f3)
    original_name=$(basename -- "$source_value")
    [[ -z "$original_name" || "$original_name" == "-" ]] && original_name="pasted.md"

    local body
    body=$(cmd_intake "$project_root" "$slug" "$source_value" "$preserved_rel" "$byte_size" "$digest" "$original_name" "$extension")
    PRD_MANIFEST_BODY="$body" \
        write_manifest "$artifact_dir" "$project_root" >/dev/null

    info "{\"status\":\"AWAITING_DECOMPOSITION_APPROVAL\",\"slug\":\"$slug\",\"manifest\":\".specify/specs/$slug/$ARTIFACT_DIRNAME/manifest.yml\",\"source_digest\":\"$digest\"}"
    return 0
}

main "$@"