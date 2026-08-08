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

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


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
    return hashlib.sha256(data).hexdigest()


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


__all__ = [
    "SLUG_MAX_LENGTH_DEFAULT",
    "VALID_STATES",
    "atomic_write_text",
    "ensure_unique_slug",
    "err",
    "find_project_root",
    "info",
    "is_within",
    "json_dumps",
    "load_manifest",
    "normalize_slug",
    "parse_args",
    "preserve_source",
    "refuse_if_symlink",
    "require_within",
    "safe_create_dir",
    "sha256_bytes",
    "sha256_file",
    "write_manifest",
    "yaml_safe_dump",
    "yaml_safe_load",
]