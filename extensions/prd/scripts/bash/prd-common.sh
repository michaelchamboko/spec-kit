#!/usr/bin/env bash
# PRD-to-Plans extension: shared helpers (bash)
#
# Pure POSIX-portable bash (no bashisms beyond `[[ ]]`, arrays, and `printf %q`).
# Twin of ``prd-common.ps1`` and ``prd_common.py``.

set -euo pipefail

SLUG_MAX_LENGTH_DEFAULT=${SLUG_MAX_LENGTH_DEFAULT:-64}

# Print a stderr message. Mirrors ``err()`` in the Python and PowerShell twins.
err() {
    printf '%s\n' "$*" >&2
}

# Print a stdout message. Mirrors ``info()`` in the Python and PowerShell twins.
info() {
    printf '%s\n' "$*"
}

# Normalize a value into a kebab-case slug.
# Rules:
#   - Lowercase
#   - Whitespace / underscores -> ``-``
#   - Keep only ``[a-z0-9-]``
#   - Collapse and trim ``-``
#   - Truncate to ``SLUG_MAX_LENGTH_DEFAULT``
#   - Reject empty result
#
# Mirrors the Python and PowerShell twins. The truncated result has its trailing
# dashes stripped so the three implementations stay in lock-step.
normalize_slug() {
    local value="${1:-}"
    if [[ -z "$value" ]]; then
        err "ERROR: slug must be a non-empty string"
        return 1
    fi
    local lowered
    lowered=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    local replaced
    replaced=$(printf '%s' "$lowered" | sed -E 's/[[:space:]_]+/-/g')
    local cleaned
    cleaned=$(printf '%s' "$replaced" | sed -E 's/[^a-z0-9-]+//g')
    cleaned=$(printf '%s' "$cleaned" | sed -E 's/-+/-/g; s/^-+//; s/-+$//')
    if [[ -z "$cleaned" ]]; then
        err "ERROR: slug normalizes to empty value: $value"
        return 1
    fi
    if (( ${#cleaned} > SLUG_MAX_LENGTH_DEFAULT )); then
        cleaned="${cleaned:0:SLUG_MAX_LENGTH_DEFAULT}"
        cleaned=$(printf '%s' "$cleaned" | sed -E 's/-+$//')
        if [[ -z "$cleaned" ]]; then
            cleaned="${cleaned:0:SLUG_MAX_LENGTH_DEFAULT}"
        fi
    fi
    printf '%s' "$cleaned"
}

# Print the hex SHA-256 digest of a file. Mirrors Python's ``hashlib.sha256``.
sha256_file() {
    local path="${1:-}"
    if [[ ! -f "$path" ]]; then
        err "ERROR: sha256_file: not a file: $path"
        return 1
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$path" | awk '{print $1}'
    else
        err "ERROR: no SHA-256 utility found (install coreutils)"
        return 1
    fi
}

# Return success if ``$child`` is lexically contained within ``$parent``.
is_within() {
    local child="$1"
    local parent="$2"
    case "$child" in
        "$parent"/*) return 0 ;;
        *) return 1 ;;
    esac
}

# Refuse if any ancestor of ``$path`` is a symlink.
refuse_if_symlink() {
    local target="$1"
    local current="$target"
    while [[ "$current" != "/" && "$current" != "." ]]; do
        if [[ -L "$current" ]]; then
            err "ERROR: refusing symlinked ancestor: $current"
            return 1
        fi
        current=$(dirname -- "$current")
    done
    return 0
}

# Refuse if ``$child`` escapes ``$parent`` or any ancestor is a symlink.
require_within() {
    local child="$1"
    local parent="$2"
    refuse_if_symlink "$child" || return 1
    if ! is_within "$child" "$parent"; then
        err "ERROR: path $child escapes project root $parent"
        return 1
    fi
    return 0
}

# Find the project root, preferring the explicit ``SPECIFY_INIT_DIR`` override.
# Mirrors ``scripts/bash/common.sh::get_repo_root`` and the equivalent in the
# Python and PowerShell twins: explicit project override wins; otherwise the
# closest ``.specify/`` ancestor.
find_specify_root() {
    if [[ -n "${SPECIFY_INIT_DIR:-}" ]]; then
        if [[ ! -d "$SPECIFY_INIT_DIR" ]]; then
            err "ERROR: SPECIFY_INIT_DIR does not point to an existing directory: $SPECIFY_INIT_DIR"
            return 1
        fi
        if [[ ! -d "$SPECIFY_INIT_DIR/.specify" ]]; then
            err "ERROR: SPECIFY_INIT_DIR is not a Spec Kit project (no .specify/ directory): $SPECIFY_INIT_DIR"
            return 1
        fi
        printf '%s\n' "$SPECIFY_INIT_DIR"
        return 0
    fi
    local dir="${1:-$(pwd)}"
    if ! dir="$(cd -- "$dir" 2>/dev/null && pwd)"; then
        return 1
    fi
    local prev=""
    while true; do
        if [[ -d "$dir/.specify" ]]; then
            printf '%s\n' "$dir"
            return 0
        fi
        if [[ "$dir" = "/" ]] || [[ "$dir" = "$prev" ]]; then
            break
        fi
        prev="$dir"
        dir=$(dirname -- "$dir")
    done
    return 1
}

# Create ``$path`` and all parents after verifying containment.
# Mirrors ``safe_create_dir`` in Python.
safe_create_dir() {
    local path="$1"
    local parent="$2"
    require_within "$path" "$parent" || return 1
    mkdir -p -- "$path"
    printf '%s\n' "$path"
}

# Append a disambiguating suffix (``-2``, ``-3`` …) until ``$base`` no longer
# collides with an existing sibling inside ``$specs_root``.
ensure_unique_slug() {
    local specs_root="$1"
    local requested="$2"
    local base
    base=$(normalize_slug "$requested") || return 1
    local candidate="$base"
    local n=2
    while [[ -e "$specs_root/$candidate" ]]; do
        candidate="${base}-${n}"
        n=$((n + 1))
    done
    printf '%s' "$candidate"
}

# Persist the original PRD source bytes atomically (write to tempfile + rename).
# Mirrors ``atomic_write_text`` in Python.
atomic_write_text() {
    local target="$1"
    local parent
    parent=$(dirname -- "$target")
    mkdir -p -- "$parent"
    local tmp
    tmp=$(mktemp -- "$parent/.prd-XXXXXX")
    cat >"$tmp"
    mv -f -- "$tmp" "$target"
}

# Write a minimal YAML manifest using ``cat <<EOF`` heredocs. The PRD manifest
# shape is fixed and small, so we avoid a hard PyYAML dependency at the bash
# layer.
write_manifest() {
    local artifact_dir="$1"
    local project_root="$2"
    local manifest_path="$artifact_dir/manifest.yml"
    require_within "$manifest_path" "$project_root" || return 1
    mkdir -p -- "$artifact_dir"
    # The caller is expected to have written the manifest body to the
    # ``PRD_MANIFEST_BODY`` variable; we just persist it.
    if [[ -z "${PRD_MANIFEST_BODY:-}" ]]; then
        err "ERROR: write_manifest requires PRD_MANIFEST_BODY to be set"
        return 1
    fi
    printf '%s\n' "$PRD_MANIFEST_BODY" >"$manifest_path"
    printf '%s\n' "$manifest_path"
}

# Render a small dict mapping as a YAML block (very limited). Mirrors the
# Python and PowerShell fallbacks; supports the manifest shape emitted by
# ``prd_plan.sh``.
yaml_dump() {
    local body="$1"
    printf '%s\n' "$body"
}