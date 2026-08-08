#!/usr/bin/env python3
"""PRD-to-Plans: deterministic plan orchestration entrypoint.

Invoked by ``speckit.prd.plan``. Performs the deterministic I/O the
command body delegates to a script:

- Resolve the project root, slug, and ``.specify/specs/<slug>/`` directory.
- Validate ancestor paths (refuse symlinks, refuse escape).
- Preserve the original PRD source bytes under ``source/prd-v001.<ext>``.
- Compute and persist the SHA-256 digest in ``manifest.yml``.
- Freeze the decomposition sequence: materialize slice directories
  ``001-<slice-slug>``, ``002-<slice-slug>``, … when state is
  ``AWAITING_DECOMPOSITION_APPROVAL`` and ``approve=true`` is passed.

This script is **not** a stand-in for the command body. The command body
drives the AI-assisted extraction, decomposition, and Council review. The
script only materializes the deterministic skeleton that those steps
fill in.

Usage::

    prd_plan.py source="<path or pasted>" [slug=<slug>] [approve=true]
    prd_plan.py slug=<slug> approve=true
    prd_plan.py --validate-only
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prd_common as common  # noqa: E402

PRD_VERSION_PREFIX = "prd-v"
DEFAULT_VERSION = "v001"
ARTIFACT_DIRNAME = "000-spec-of-specs"


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_local_source(source_path: Path) -> bytes:
    with open(source_path, "rb") as fh:
        return fh.read()


def _resolve_source_bytes(source_value: str) -> tuple[bytes, str, str]:
    """Return ``(bytes, original_name, extension)`` for a source spec.

    The command body supplies one of:

    - a local file path
    - a public URL (the command body downloads and passes bytes inline)
    - a sentinel ``-`` meaning ``stdin``-pasted content
    """
    if source_value == "-":
        return sys.stdin.buffer.read(), "pasted.md", ".md"
    candidate = Path(source_value)
    if candidate.is_file():
        return _read_local_source(candidate), candidate.name, candidate.suffix or ".md"
    # Otherwise treat the value as already-fetched bytes supplied by the
    # command body (after URL fetching and normalization upstream).
    return source_value.encode("utf-8"), "fetched.md", ".md"


def _next_slice_prefix(slice_count: int) -> str:
    return f"{slice_count + 1:03d}"


def cmd_intake(
    project_root: Path,
    slug: str,
    args: dict[str, str],
) -> dict[str, object]:
    """Create or reconcile a PRD workspace; stop for decomposition approval."""
    specs_root = common.safe_create_dir(
        project_root / ".specify" / "specs", project_root
    )
    prd_dir = common.safe_create_dir(specs_root / slug, project_root)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)

    source_value = args.get("source") or args.get("source=")
    if source_value is None:
        common.err(
            "ERROR: intake mode requires source=\"<path|URL|pasted>\""
        )
        raise SystemExit(2)
    source_value = source_value.strip('"').strip("'")

    source_bytes, original_name, extension = _resolve_source_bytes(source_value)
    preserved = common.preserve_source(
        artifact_dir,
        project_root,
        source_bytes=source_bytes,
        original_name=original_name,
        version=DEFAULT_VERSION,
        extension=extension or ".md",
    )

    manifest = {
        "schema_version": "1.0",
        "extension": "prd",
        "slug": slug,
        "state": "AWAITING_DECOMPOSITION_APPROVAL",
        "created_at": _utc_now_iso(),
        "active_version": DEFAULT_VERSION,
        "source": {
            "authority": (
                "file"
                if source_value != "-"
                and Path(source_value).is_file()
                else "pasted"
            ),
            "fetched_at": _utc_now_iso(),
            "original_name": preserved["original_name"],
            "byte_size": preserved["byte_size"],
            "sha256": preserved["sha256"],
            "preserved_at": preserved["relative_path"],
        },
        "slices": [],
        "decomposition_version": DEFAULT_VERSION,
        "frozen_sequence": False,
    }
    manifest_path = common.write_manifest(
        artifact_dir, project_root, manifest
    )

    return {
        "status": "AWAITING_DECOMPOSITION_APPROVAL",
        "slug": slug,
        "manifest": str(manifest_path.relative_to(project_root)),
        "decomposition_review": (
            f".specify/specs/{slug}/{ARTIFACT_DIRNAME}/reviews/"
            f"decomposition-{DEFAULT_VERSION}.md"
        ),
        "next_command": f"speckit.prd.plan slug={slug} approve=true",
        "source_digest": preserved["sha256"],
    }


def cmd_approve(
    project_root: Path,
    slug: str,
    slices: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Freeze the decomposition sequence and materialize slice directories.

    The command body is responsible for producing the list of slice
    metadata (id, slug, dependencies) after decomposition and Council
    review. When ``slices`` is ``None``, the script freezes whatever the
    manifest already records.
    """
    prd_dir = common.safe_create_dir(
        project_root / ".specify" / "specs", project_root
    ).joinpath(slug)
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    manifest = common.load_manifest(artifact_dir) or {}
    manifest.setdefault("schema_version", "1.0")
    manifest["extension"] = "prd"
    manifest["slug"] = slug
    manifest["state"] = "PLANNING"
    manifest["frozen_sequence"] = True
    manifest["decomposition_approval_version"] = DEFAULT_VERSION
    manifest.setdefault("active_version", DEFAULT_VERSION)

    if slices is None:
        # Read ``id<TAB>slug<TAB>folder`` records from stdin (one per line).
        # Mirrors the bash twin: the command body passes slice metadata
        # through stdin to keep argv clean.
        slices = []
        if not sys.stdin.isatty():
            for raw in sys.stdin:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                slice_meta = {
                    "id": parts[0].strip(),
                    "slug": parts[1].strip(),
                }
                if len(parts) >= 3 and parts[2].strip():
                    slice_meta["directory"] = parts[2].strip()
                if len(parts) >= 4 and parts[3].strip():
                    slice_meta["dependencies"] = [
                        d.strip() for d in parts[3].split(",") if d.strip()
                    ]
                slices.append(slice_meta)
        if not slices:
            slices = manifest.get("slices", []) or []
    materialized: list[dict[str, object]] = []
    for index, slice_meta in enumerate(slices, start=1):
        if not isinstance(slice_meta, dict):
            continue
        slice_slug = str(slice_meta.get("slug") or slice_meta.get("id") or "").strip()
        if not slice_slug:
            common.err(
                f"WARN: slice entry #{index} missing slug/id; skipping materialization"
            )
            continue
        prefix = _next_slice_prefix(len(materialized))
        directory = str(slice_meta.get("directory") or "").strip()
        if not directory:
            directory = f"{prefix}-{common.normalize_slug(slice_slug)}"
        slice_dir = common.safe_create_dir(
            prd_dir / directory, project_root
        )
        materialized.append(
            {
                "id": slice_meta.get("id") or f"SLC-{index:03d}",
                "slug": slice_slug,
                "directory": directory,
                "dependencies": slice_meta.get("dependencies", []),
                "order": len(materialized) + 1,
                "state": slice_meta.get("state", "PLANNING"),
                "requirements": slice_meta.get("requirements", []),
            }
        )
    manifest["slices"] = materialized
    manifest["state"] = "PLANNING"
    manifest["updated_at"] = _utc_now_iso()

    common.write_manifest(artifact_dir, project_root, manifest)

    return {
        "status": "PLANNING",
        "slug": slug,
        "manifest": str((artifact_dir / "manifest.yml").relative_to(project_root)),
        "materialized_slices": [m["directory"] for m in materialized],
    }


def cmd_finalize(
    project_root: Path,
    slug: str,
) -> dict[str, object]:
    """Mark the workspace as ``PLAN_READY`` after the final Council review."""
    prd_dir = (
        common.safe_create_dir(project_root / ".specify" / "specs", project_root)
        / slug
    )
    artifact_dir = common.safe_create_dir(prd_dir / ARTIFACT_DIRNAME, project_root)
    manifest = common.load_manifest(artifact_dir) or {}
    manifest["state"] = "PLAN_READY"
    manifest["final_review_version"] = DEFAULT_VERSION
    manifest["finalized_at"] = _utc_now_iso()
    common.write_manifest(artifact_dir, project_root, manifest)
    return {
        "status": "PLAN_READY",
        "slug": slug,
        "manifest": str((artifact_dir / "manifest.yml").relative_to(project_root)),
    }


def main(argv: list[str] | None = None) -> int:
    args = common.parse_args(argv)
    project_root = common.find_project_root()
    if project_root is None:
        common.err("ERROR: not inside a Spec Kit project (.specify/ not found)")
        return 1

    raw_slug = args.get("slug", "")
    try:
        slug = common.normalize_slug(raw_slug) if raw_slug else ""
    except ValueError as exc:
        common.err(f"ERROR: {exc}")
        return 1

    approve = args.get("approve", "").lower() in {"true", "1", "yes"}
    finalize = args.get("flag_finalize", "false") == "true"

    if not slug and "source" not in args:
        common.err(
            "ERROR: missing slug=... and source=...; pass one of "
            "source=\"...\" (intake) or slug=<slug> [approve=true]"
        )
        return 2

    if approve:
        if not slug:
            common.err("ERROR: approve=true requires slug=<slug>")
            return 2
        result = cmd_approve(project_root, slug)
    elif finalize:
        if not slug:
            common.err("ERROR: --finalize requires slug=<slug>")
            return 2
        result = cmd_finalize(project_root, slug)
    else:
        if not slug:
            specs_root = project_root / ".specify" / "specs"
            slug = common.ensure_unique_slug(specs_root, "prd")
        else:
            slug = common.ensure_unique_slug(
                project_root / ".specify" / "specs", slug
            )
        result = cmd_intake(project_root, slug, args)

    common.info(common.json_dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())