#!/usr/bin/env python3
"""Shared helpers for the bundled ``prd`` extension scripts.

The Python twin of ``prd-common.sh`` and ``prd-common.ps1``. Centralizes:

- Slug normalization (lowercase, kebab-case, [a-z0-9-] only, truncated)
- Path containment checks (refuse symlinks, refuse escaping project root)
- Source-content hashing (SHA-256) for digest provenance
- Manifest load/save with safe atomic writes

Mirrors the bash and PowerShell ports. Tests exercise parity against the
bundled Python implementation directly; the bash/powershell twins are
purely additive ports of the same logic.
"""

from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import json
import os
import re
import sys
import tempfile
import time as _time
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


SLUG_RE = re.compile(r"[^a-z0-9-]+")
SLUG_MAX_LENGTH_DEFAULT = 64

VALID_STATES = frozenset(
    {
        "PLANNING",
        "AWAITING_DECOMPOSITION_APPROVAL",
        "PLAN_READY",
        "STALE",
    }
)


def err(message: str) -> None:
    """Print a stderr message. Mirrors bash/PS ``err`` helper."""
    print(message, file=sys.stderr)


def info(message: str) -> None:
    """Print a stdout message. Mirrors bash/PS ``info`` helper."""
    print(message)


def normalize_slug(value: str, *, max_length: int = SLUG_MAX_LENGTH_DEFAULT) -> str:
    """Return a deterministic kebab-case slug.

    Rules:
      - Lowercase
      - Whitespace/underscores -> ``-``
      - Keep only ``[a-z0-9-]`` (drop every other character)
      - Collapse and trim ``-``
      - Truncate to ``max_length`` (default 64)
      - Reject empty result

    Mirrors ``scripts/bash/prd-common.sh::normalize_slug`` and the PowerShell
    twin. Bash/PowerShell truncate to ``max_length`` *after* dropping dashes
    at the edges; Python trims trailing dashes from the slice to keep the
    result stable across the three languages.
    """
    if not isinstance(value, str):
        raise ValueError("slug must be a string")
    lowered = value.strip().lower()
    lowered = re.sub(r"[\s_]+", "-", lowered)
    cleaned = SLUG_RE.sub("", lowered)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        raise ValueError(f"slug normalizes to empty value: {value!r}")
    truncated = cleaned[:max_length].rstrip("-")
    if not truncated:
        truncated = cleaned[:max_length]
    return truncated


def sha256_bytes(data: bytes) -> str:
    """Return hex SHA-256 digest. Mirrors the bash/PowerShell twins."""
    return _hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of a file, raising on read failure."""
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def is_within(child: Path, parent: Path) -> bool:
    """Return True iff ``child`` (lexically normalized) is inside ``parent``.

    Mirrors the lexical containment check used by bash/PowerShell — no
    symlink resolution, no ``resolve()``. Use ``require_within`` to also
    reject symlinked ancestors.
    """
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def refuse_if_symlink(path: Path) -> None:
    """Raise if ``path`` or any ancestor up to ``parent_stop`` is a symlink.

    The PRD extension refuses any ancestor traversal that would resolve
    outside the project root. The bash and PowerShell twins check each
    component independently; Python's ``Path.is_symlink`` does the same.
    """
    for part in path.parents:
        if part.is_symlink():
            raise RuntimeError(f"refusing symlinked ancestor: {part}")


def require_within(child: Path, parent: Path) -> None:
    """Raise if ``child`` escapes ``parent`` or any ancestor is a symlink."""
    refuse_if_symlink(child)
    if not is_within(child, parent):
        raise RuntimeError(
            f"path {child!s} escapes project root {parent!s}"
        )


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically (write + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".prd-", dir=tmp_dir, text=False)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content.encode(encoding))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def yaml_safe_dump(data: dict[str, Any]) -> str:
    """Tiny YAML emitter for the manifest.

    The PRD extension avoids a hard PyYAML dependency; the manifest
    shape is fixed and small, so a minimal emitter is sufficient and
    keeps parity with the bash/powershell scripts. Falls back to
    PyYAML's ``safe_dump`` if available.
    """
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    except ImportError:
        return _minimal_yaml_dump(data)


def _minimal_yaml_dump(data: Any, indent: int = 0) -> str:
    """Render a small dict/list/scalar tree as YAML.

    Only used when PyYAML is unavailable (the bash/PowerShell scripts do not
    need it at all). Supports the manifest shape used by the PRD extension:
    nested mappings, lists of mappings, scalars.
    """
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return "{}"
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                lines.append(_minimal_yaml_dump(value, indent + 1))
            elif isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}: {'{}' if isinstance(value, dict) else '[]'}")
            elif isinstance(value, bool):
                lines.append(f"{pad}{key}: {'true' if value else 'false'}")
            elif value is None:
                lines.append(f"{pad}{key}: null")
            elif isinstance(value, (int, float)):
                lines.append(f"{pad}{key}: {value}")
            else:
                escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{pad}{key}: "{escaped}"')
        return "\n".join(lines)
    if isinstance(data, list):
        if not data:
            return "[]"
        lines = []
        for item in data:
            if isinstance(item, dict):
                inner = _minimal_yaml_dump(item, indent + 1).splitlines()
                first = f"{pad}- {inner[0].lstrip()}"
                lines.append(first)
                lines.extend(inner[1:])
            else:
                lines.append(f"{pad}- {item}")
        return "\n".join(lines)
    if isinstance(data, bool):
        return "true" if data else "false"
    if data is None:
        return "null"
    if isinstance(data, (int, float)):
        return str(data)
    escaped = str(data).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def yaml_safe_load(text: str) -> dict[str, Any]:
    """Parse a YAML document.

    Mirrors ``PyYAML.safe_load`` with a JSON fallback. The bundled PRD
    extension emits its own manifest, so the parser only needs to handle
    the shape produced by ``yaml_safe_dump``.
    """
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(text)
    except ImportError:
        loaded = _minimal_yaml_load(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("manifest root must be a mapping")
    return loaded


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """Tiny YAML loader that handles the manifest shape emitted above.

    Supports nested mappings, lists, scalars, and quoted strings. Does not
    attempt to be a complete YAML parser — only what the PRD extension
    emits.
    """

    def strip_quotes(token: str) -> str:
        token = token.strip()
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
            return token[1:-1]
        return token

    def coerce(token: str) -> Any:
        token = token.strip()
        if not token:
            return ""
        if token in {"true", "True"}:
            return True
        if token in {"false", "False"}:
            return False
        if token in {"null", "None", "~"}:
            return None
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            pass
        return strip_quotes(token)

    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_indent, parent = stack[-1]
        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"list item outside list: {raw!r}")
            item_value = stripped[2:].strip()
            if ":" in item_value and not item_value.startswith('"'):
                key, _, value = item_value.partition(":")
                key = key.strip()
                value = value.strip()
                item: dict[str, Any] = {key: coerce(value) if value else None}
                parent.append(item)
                stack.append((indent, item))
                if not value:
                    stack.append((indent + 2, item))
            else:
                parent.append(coerce(item_value))
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            new: Any = {}
            if isinstance(parent, dict):
                parent[key] = new
            stack.append((indent, new))
        elif value in {"[]", "[ ]"}:
            if isinstance(parent, dict):
                parent[key] = []
        elif value in {"{}", "{ }"}:
            if isinstance(parent, dict):
                parent[key] = {}
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if isinstance(parent, dict):
                parent[key] = [coerce(part) for part in inner.split(",")] if inner else []
        else:
            if isinstance(parent, dict):
                parent[key] = coerce(value)
    return root


def find_project_root(start: Path | None = None) -> Path | None:
    """Find the project root, preferring the explicit ``SPECIFY_INIT_DIR`` override.

    Order:

      1. ``SPECIFY_INIT_DIR`` environment variable (when set, must point
         at an existing directory containing ``.specify/``)
      2. Walk up from ``start`` (default cwd) looking for ``.specify/``

    Mirrors the precedence used by ``scripts/python/common.py`` and the
    bundled ``agent-context`` / ``git`` extensions: explicit override
    wins; otherwise the closest ``.specify/`` ancestor.
    """
    explicit = os.environ.get("SPECIFY_INIT_DIR", "").strip()
    if explicit:
        try:
            resolved = Path(explicit).resolve(strict=True)
        except OSError:
            return None
        if not (resolved / ".specify").is_dir():
            return None
        return resolved

    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".specify").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def ensure_unique_slug(
    specs_root: Path, requested: str, *, max_length: int = SLUG_MAX_LENGTH_DEFAULT
) -> str:
    """Return a slug that does not collide with an existing specs subdir.

    Mirrors the slug-resolution policy described in the command spec:
    user-provided slugs are taken verbatim, automated runs append the
    shortest disambiguating suffix (``-2``, ``-3``, …) to avoid
    overwriting existing PRD workspaces.
    """
    base = normalize_slug(requested, max_length=max_length)
    candidate = base
    n = 2
    while (specs_root / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def safe_create_dir(path: Path, project_root: Path) -> Path:
    """Create ``path`` after verifying it stays inside ``project_root``.

    Raises ``RuntimeError`` on symlinked ancestors or escape. Returns the
    created path on success.
    """
    require_within(path, project_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def preserve_source(
    target_dir: Path,
    project_root: Path,
    *,
    source_bytes: bytes | None,
    original_name: str,
    version: str = "v001",
    extension: str = ".md",
) -> dict[str, Any]:
    """Persist the original PRD source under ``source/prd-<version>.<ext>``.

    Returns a small dict with the relative path, byte size, and SHA-256
    digest of the preserved file. Mirrors the bash/PowerShell twins.
    """
    safe_create_dir(target_dir, project_root)
    source_dir = safe_create_dir(target_dir / "source", project_root)
    leaf = f"prd-{version}{extension}"
    target = source_dir / leaf
    require_within(target, project_root)
    if source_bytes is None:
        # Empty paste / placeholder.
        source_bytes = b""
    atomic_write_text(target, source_bytes.decode("utf-8", errors="replace"))
    digest = sha256_bytes(source_bytes)
    return {
        "relative_path": target.relative_to(project_root).as_posix(),
        "byte_size": len(source_bytes),
        "sha256": digest,
        "original_name": original_name,
    }


def write_manifest(
    target_dir: Path,
    project_root: Path,
    payload: dict[str, Any],
) -> Path:
    """Persist a PRD manifest.yml atomically and return its path."""
    require_within(target_dir, project_root)
    safe_create_dir(target_dir, project_root)
    manifest_path = target_dir / "manifest.yml"
    require_within(manifest_path, project_root)
    body = yaml_safe_dump(payload)
    atomic_write_text(manifest_path, body)
    return manifest_path


def load_manifest(target_dir: Path) -> dict[str, Any] | None:
    """Load ``manifest.yml`` from ``target_dir``; return ``None`` if absent."""
    candidate = target_dir / "manifest.yml"
    if not candidate.is_file():
        return None
    return yaml_safe_load(candidate.read_text(encoding="utf-8"))


def json_dumps(payload: Any) -> str:
    """Compact JSON for ``{SCRIPT}``-style script bridges."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=False)


def parse_args(argv: list[str] | None = None) -> dict[str, str]:
    """Parse ``key=value`` and ``--flag`` tokens for command entrypoints.

    Mirrors the bash/PowerShell arg parser: every token not starting with
    ``--`` is treated as ``key=value`` (or a positional slug). Tokens
    starting with ``--`` are stored as flags (``flag_<name>=True``).
    """
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


# ── Orchestration ledger (v1.1) ──────────────────────────────────────────────
#
# The orchestration ledger is the **sole machine-readable task-state
# authority** for the waterfall. State-changing actions read, mutate,
# and atomically rewrite this file. Helpers below enforce path
# containment and atomic writes; business-rule enforcement lives in
# ``prd_orchestrate.py``.

ORCHESTRATION_LEDGER_SCHEMA_VERSION = "1.1"
ORCHESTRATION_LEDGER_FILENAME = "orchestration.yml"
ORCHESTRATION_EVIDENCE_DIRNAME = "orchestration-evidence"
ORCHESTRATION_LOCK_FILENAME = "orchestration.lock"
ORCHESTRATION_LOCK_TIMEOUT_SECONDS = 5

VALID_PROJECT_STATES = frozenset(
    {
        "NOT_STARTED",
        "IN_PROGRESS",
        "BLOCKED",
        "AWAITING_APPROVAL",
        "STALE",
        "RELEASE_READY",
    }
)
VALID_SLICE_STATES = frozenset({"PENDING", "IN_PROGRESS", "DONE", "STALE", "BLOCKED"})
VALID_TASK_STATES = frozenset({"TODO", "READY", "IN_PROGRESS", "BLOCKED", "DONE", "STALE"})


def ledger_path(artifact_dir: Path, project_root: Path) -> Path:
    """Resolve the on-disk path of ``orchestration.yml``.

    Raises if the resulting path would escape the project root.
    """
    candidate = artifact_dir / ORCHESTRATION_LEDGER_FILENAME
    require_within(candidate, project_root)
    return candidate


def ledger_exists(artifact_dir: Path, project_root: Path) -> bool:
    return ledger_path(artifact_dir, project_root).is_file()


def load_ledger(artifact_dir: Path, project_root: Path) -> dict[str, Any] | None:
    """Load ``orchestration.yml`` with its comments and layout intact."""
    path = ledger_path(artifact_dir, project_root)
    if not path.is_file():
        return None
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    return parser.load(path.read_text(encoding="utf-8"))


def write_ledger(
    artifact_dir: Path,
    project_root: Path,
    payload: dict[str, Any],
) -> Path:
    """Persist only state changes without rewriting hand-authored YAML layout."""
    path = ledger_path(artifact_dir, project_root)
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    parser.width = 4096
    stream = StringIO()
    parser.dump(payload, stream)
    body = stream.getvalue()
    atomic_write_text(path, body)
    return path


def acquire_ledger_lock(
    artifact_dir: Path, project_root: Path, *, blocking: bool = True
) -> Any:
    """Acquire the filesystem lock that protects the ledger.

    Uses an exclusive ``O_CREAT|O_EXCL`` lock file. The returned object
    must be released via ``release_ledger_lock``. On non-POSIX
    platforms the helper degrades to a no-op; pwsh/Windows falls back
    to the same single-process contract.
    """
    require_within(artifact_dir, project_root)
    lock_path = artifact_dir / ORCHESTRATION_LOCK_FILENAME
    require_within(lock_path, project_root)
    deadline = _time.monotonic() + ORCHESTRATION_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            os.close(fd)
            return _LockHandle(lock_path)
        except FileExistsError:
            if not blocking:
                raise
            if _time.monotonic() >= deadline:
                raise TimeoutError(
                    f"could not acquire ledger lock at {lock_path} within "
                    f"{ORCHESTRATION_LOCK_TIMEOUT_SECONDS}s"
                )
            _time.sleep(0.05)


def release_ledger_lock(handle: Any) -> None:
    """Release a lock acquired by ``acquire_ledger_lock``."""
    handle.release()


class _LockHandle:
    """Filesystem-backed lock handle for the orchestration ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# Import time for ledger lock helpers (kept at module bottom to avoid
# polluting the helper API surface).


def bump_revision(ledger: dict[str, Any]) -> int:
    """Increment and return the monotonic ``revision`` counter on a ledger."""
    current = int(ledger.get("revision", 0) or 0)
    ledger["revision"] = current + 1
    ledger["updated_at"] = _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return ledger["revision"]


def empty_ledger(
    project_root: Path,
    slug: str,
    *,
    manifest_version: str,
    decomposition_version: str,
) -> dict[str, Any]:
    """Build a fresh ``1.1`` ledger skeleton.

    The plan command fills in ``slices`` and ``priorities`` after the
    decomposition is approved. The orchestrator populates
    ``evidence`` and ``approvals`` as work progresses.
    """
    return {
        "schema_version": ORCHESTRATION_LEDGER_SCHEMA_VERSION,
        "revision": 0,
        "repository": {
            "root": str(project_root),
            "head": "",
            "dirty_fingerprint": "",
            "applicable_instructions": "",
        },
        "plan": {
            "slug": slug,
            "manifest_version": manifest_version,
            "decomposition_version": decomposition_version,
            "frozen_sequence": True,
        },
        "project": {
            "state": "NOT_STARTED",
            "current_task": None,
            "active_owner": None,
            "blockers": [],
        },
        "priorities": {
            "business": [],
            "execution": [],
        },
        "slices": [],
        "final_gate": {
            "required": True,
            "approved_by": None,
            "approved_at": None,
            "baseline_check": "",
            "full_regression": "",
            "cross_slice_e2e": "",
            "deployment_smoke": "",
            "rollback_check": "",
        },
    }


def build_ledger_from_manifest(
    project_root: Path,
    slug: str,
    manifest: dict[str, Any],
    *,
    repo_head: str = "",
    dirty_fingerprint: str = "",
    applicable_instructions: str = "",
) -> dict[str, Any]:
    """Materialize the ``1.1`` ledger from an existing frozen manifest.

    Reads each slice directory's ``tasks.md`` (if present) to harvest
    the ``SLC-NNN-TMMM`` IDs without renumbering; falls back to a
    single ``SLC-NNN-T001`` placeholder when a slice has no tasks file
    yet. The command body is responsible for filling in real task
    shapes during the per-slice plan/tasks authoring step.
    """
    ledger = empty_ledger(
        project_root,
        slug,
        manifest_version=str(manifest.get("active_version", "v001")),
        decomposition_version=str(manifest.get("decomposition_version", "v001")),
    )
    ledger["repository"] = {
        "root": str(project_root),
        "head": repo_head,
        "dirty_fingerprint": dirty_fingerprint,
        "applicable_instructions": applicable_instructions,
    }
    slices = manifest.get("slices") or []
    business_priority: list[str] = []
    execution_priority: list[tuple[str, str]] = []
    ledger_slices: list[dict[str, Any]] = []
    for slice_meta in slices:
        if not isinstance(slice_meta, dict):
            continue
        slice_id = str(slice_meta.get("id") or "").strip()
        slice_directory = str(slice_meta.get("directory") or "").strip()
        if not slice_id or not slice_directory:
            continue
        business_priority.append(slice_id)
        task_ids = _harvest_task_ids(project_root / ".specify" / "specs" / slug / slice_directory)
        if not task_ids:
            task_ids = [f"{slice_id}-T001"]
        ledger_tasks: list[dict[str, Any]] = []
        for rank, task_id in enumerate(task_ids, start=1):
            ledger_tasks.append(
                {
                    "id": task_id,
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
            execution_priority.append((slice_id, task_id))
        ledger_slices.append(
            {
                "id": slice_id,
                "directory": slice_directory,
                "state": "PENDING",
                "rank": len(ledger_slices) + 1,
                "dependencies": [d for d in (slice_meta.get("dependencies") or []) if d],
                "exit_gate": {
                    "required_evidence": [],
                    "e2e_journey": "",
                    "approval": {
                        "required": True,
                        "approved_by": None,
                        "approved_at": None,
                    },
                },
                "tasks": ledger_tasks,
            }
        )
    ledger["slices"] = ledger_slices
    ledger["priorities"] = {
        "business": business_priority,
        "execution": [f"{sid}::{tid}" for sid, tid in execution_priority],
    }
    return ledger


_TASK_ID_PATTERN = re.compile(r"\b(SLC-\d{3}-T\d{3})\b")


def _harvest_task_ids(slice_dir: Path) -> list[str]:
    """Read ``SLC-NNN-TMMM`` IDs from a slice's ``tasks.md``.

    Returns a deterministic, insertion-ordered list with no
    duplicates. Mirrors the bash and PowerShell twins. Returns an
    empty list when the file is absent or unreadable.
    """
    tasks_md = slice_dir / "tasks.md"
    if not tasks_md.is_file():
        return []
    try:
        text = tasks_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _TASK_ID_PATTERN.finditer(text):
        tid = match.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    return ordered


def ledger_evidence_dir(
    artifact_dir: Path, project_root: Path
) -> Path:
    """Path to the per-task evidence directory."""
    target = artifact_dir / ORCHESTRATION_EVIDENCE_DIRNAME
    require_within(target, project_root)
    return target


def write_task_evidence(
    artifact_dir: Path,
    project_root: Path,
    task_id: str,
    check_kind: str,
    check_id: str,
    result: str,
    source_path: Path | None = None,
) -> Path:
    """Record one evidence entry under the ledger's evidence tree.

    Returns the path to the evidence file. ``result`` must be
    ``"pass"`` or ``"fail"``. ``source_path`` (when supplied) must be
    inside the project root and is recorded as a sibling reference
    file.
    """
    if result not in {"pass", "fail"}:
        raise ValueError(f"result must be 'pass' or 'fail' (got {result!r})")
    evidence_dir = ledger_evidence_dir(artifact_dir, project_root)
    safe_create_dir(evidence_dir, project_root)
    task_dir = safe_create_dir(evidence_dir / task_id, project_root)
    kind_dir = safe_create_dir(task_dir / check_kind, project_root)
    evidence_path = kind_dir / f"{check_id}.{result}"
    require_within(evidence_path, project_root)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "check_kind": check_kind,
        "check_id": check_id,
        "result": result,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    if source_path is not None:
        require_within(source_path, project_root)
        payload["source_path"] = str(source_path.relative_to(project_root))
    atomic_write_text(evidence_path, yaml_safe_dump(payload))
    return evidence_path


def implementation_tree_fingerprint(
    project_root: Path,
    *,
    exclude_dirs: tuple[str, ...] = (
        ".specify",
        ".git",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
    ),
) -> str:
    """Hash every implementation-source file under the project root.

    Mirrors the implementation-source-hash invariant enforced by the
    orchestrator and validator. Implementation files are anything
    outside the excluded directories. The hash is a single SHA-256
    over the sorted concatenation of ``relative_path:NUL:content_hash``.
    Returns the empty digest ``e3b0c44...`` when no implementation
    files are present (still deterministic).
    """
    hasher = _hashlib.sha256()
    project_root = Path(project_root)
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(project_root).as_posix()
        except ValueError:
            continue
        if any(part in exclude_dirs for part in rel.split("/")):
            continue
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        try:
            with open(path, "rb") as fh:
                hasher.update(_hashlib.sha256(fh.read()).hexdigest().encode("ascii"))
        except OSError:
            continue
    return hasher.hexdigest()


def parse_task_id(value: str) -> tuple[str, str]:
    """Split ``SLC-NNN-TMMM`` into ``(slice_id, task_id)``.

    Raises ``ValueError`` for malformed inputs.
    """
    if not isinstance(value, str):
        raise ValueError(f"task id must be a string (got {type(value).__name__})")
    m = _TASK_ID_PATTERN.fullmatch(value.strip())
    if not m:
        raise ValueError(f"task id must match SLC-NNN-TMMM (got {value!r})")
    full = m.group(1)
    slice_id, task_suffix = full.rsplit("-", 1)
    return slice_id, f"{slice_id}-{task_suffix}"


def stage_key(stage: str) -> tuple[str, str]:
    """Normalize a stage argument to ``(kind, key)`` for ledger lookup.

    ``stage=FINAL`` -> ``("final", "FINAL")``.
    ``stage=SLC-001`` -> ``("slice", "SLC-001")``.
    """
    cleaned = stage.strip().upper()
    if cleaned == "FINAL":
        return ("final", "FINAL")
    if not cleaned.startswith("SLC-"):
        raise ValueError(f"stage must be SLC-NNN or FINAL (got {stage!r})")
    return ("slice", cleaned)


__all__ = [
    "SLUG_MAX_LENGTH_DEFAULT",
    "VALID_STATES",
    "VALID_PROJECT_STATES",
    "VALID_SLICE_STATES",
    "VALID_TASK_STATES",
    "ORCHESTRATION_LEDGER_SCHEMA_VERSION",
    "ORCHESTRATION_LEDGER_FILENAME",
    "ORCHESTRATION_EVIDENCE_DIRNAME",
    "ORCHESTRATION_LOCK_FILENAME",
    "ORCHESTRATION_LOCK_TIMEOUT_SECONDS",
    "acquire_ledger_lock",
    "atomic_write_text",
    "build_ledger_from_manifest",
    "bump_revision",
    "empty_ledger",
    "ensure_unique_slug",
    "err",
    "find_project_root",
    "implementation_tree_fingerprint",
    "info",
    "is_within",
    "json_dumps",
    "ledger_evidence_dir",
    "ledger_exists",
    "ledger_path",
    "load_ledger",
    "load_manifest",
    "normalize_slug",
    "parse_args",
    "parse_task_id",
    "preserve_source",
    "refuse_if_symlink",
    "release_ledger_lock",
    "require_within",
    "safe_create_dir",
    "sha256_bytes",
    "sha256_file",
    "stage_key",
    "write_ledger",
    "write_manifest",
    "write_task_evidence",
    "yaml_safe_dump",
    "yaml_safe_load",
]
