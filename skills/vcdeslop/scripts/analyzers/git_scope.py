"""Git diff and repository file-scope resolution for preflight scanning."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .models import IGNORED_DIRS, SUPPORTED_EXTENSIONS, ScanOptions

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_SHEBANG_SH_RE = re.compile(rb"^#!\s*(?:/\S*/|/usr/bin/env\s+(?:-S\s+)?)(?:ba|da|k|z|a)?sh(?:\s|$)")


def _has_shell_shebang(path: Path) -> bool:
    """Return True if an extensionless file begins with a POSIX/bash shebang."""
    if path.suffix != "" or not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(96)
    except OSError:
        return False
    return bool(_SHEBANG_SH_RE.match(head.partition(b"\n")[0]))


def is_supported_file(path: Path, root: Path | None = None) -> bool:
    """Return True if path has a supported language suffix (or shell shebang) and is not ignored."""
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS and not _has_shell_shebang(path):
        return False
    if root is not None:
        try:
            parts = path.resolve().relative_to(root.resolve()).parts
        except ValueError:
            parts = path.parts
    else:
        parts = path.parts
    return not any(part in IGNORED_DIRS for part in parts)


def collect_source_files(root: Path) -> list[Path]:
    """Collect all supported non-ignored source files beneath root."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        base = Path(dirpath)
        for fname in sorted(filenames):
            candidate = base / fname
            if candidate.suffix.lower() in SUPPORTED_EXTENSIONS or _has_shell_shebang(candidate):
                files.append(candidate.resolve())
    return files


def to_rel_posix(path: Path, root: Path) -> str:
    """Format path relative to root using forward slashes."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _run_git(root: Path, args: list[str]) -> tuple[int, str]:
    """Execute a git command inside root and return (returncode, stdout)."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return 1, ""
    return proc.returncode, proc.stdout


def _add_hunk_lines(raw_line: str, target_lines: set[int] | None) -> None:
    """Parse one unified=0 @@ hunk header and add changed lines to target_lines."""
    if target_lines is None:
        return
    match = _HUNK_HEADER_RE.match(raw_line)
    if not match:
        return
    start = int(match.group(1))
    count = int(match.group(2)) if match.group(2) is not None else 1
    if count > 0:
        target_lines.update(range(start, start + count))


def _merge_diff_into_scope(
    root: Path,
    diff_text: str,
    scope: dict[Path, set[int] | None],
) -> None:
    """Parse unified=0 diff text and merge changed line numbers into scope."""
    current_path: Path | None = None
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            current_path = _resolve_diff_target(root, raw_line[4:].strip())
            if current_path is not None:
                scope.setdefault(current_path, set())
            continue
        if current_path is not None:
            _add_hunk_lines(raw_line, scope.get(current_path))


def _resolve_diff_target(root: Path, raw_target: str) -> Path | None:
    """Convert a +++ diff target line into an existing supported Path."""
    cleaned = raw_target.strip().strip('"')
    if cleaned == "/dev/null":
        return None
    rel = cleaned[2:] if cleaned.startswith("b/") else cleaned
    candidate = (root / rel).resolve()
    if not candidate.is_file() or not is_supported_file(candidate, root):
        return None
    return candidate


def _collect_branch_base_diff(root: Path) -> str:
    """Fall back to merge-base diff against main/master/HEAD~1 on clean trees."""
    for branch in ("main", "master", "origin/main", "origin/master"):
        rc_ref, _ = _run_git(root, ["rev-parse", "--verify", "--quiet", branch])
        if rc_ref != 0:
            continue
        rc_mb, mb = _run_git(root, ["merge-base", "HEAD", branch])
        base = mb.strip()
        if rc_mb == 0 and base:
            _, diff_out = _run_git(root, ["diff", f"{base}..HEAD", "--unified=0", "--no-color"])
            if diff_out.strip():
                return diff_out
    rc_prev, _ = _run_git(root, ["rev-parse", "--verify", "--quiet", "HEAD~1"])
    if rc_prev == 0:
        _, diff_out = _run_git(root, ["diff", "HEAD~1..HEAD", "--unified=0", "--no-color"])
        return diff_out
    return ""


def _resolve_git_diff_scope(options: ScanOptions) -> dict[Path, set[int] | None]:
    """Build changed-line scope from staged, working-tree, or branch diffs."""
    root = options.root.resolve()
    scope: dict[Path, set[int] | None] = {}
    if options.scan_mode == "staged":
        _, staged = _run_git(root, ["diff", "--cached", "--unified=0", "--no-color"])
        _merge_diff_into_scope(root, staged, scope)
        return scope

    if options.base_ref:
        _, base_diff = _run_git(root, ["diff", options.base_ref, "--unified=0", "--no-color"])
        _merge_diff_into_scope(root, base_diff, scope)
    else:
        _, unstaged = _run_git(root, ["diff", "--unified=0", "--no-color"])
        _, staged = _run_git(root, ["diff", "--cached", "--unified=0", "--no-color"])
        _merge_diff_into_scope(root, unstaged, scope)
        _merge_diff_into_scope(root, staged, scope)
        _add_untracked_files(root, scope)

    if not scope and not options.base_ref:
        _merge_diff_into_scope(root, _collect_branch_base_diff(root), scope)
    return scope


def _add_untracked_files(root: Path, scope: dict[Path, set[int] | None]) -> None:
    """Include newly created untracked supported files as full-file scope."""
    rc, out = _run_git(root, ["ls-files", "--others", "--exclude-standard"])
    if rc != 0:
        return
    for rel in out.splitlines():
        candidate = (root / rel.strip().strip('"')).resolve()
        if candidate.is_file() and is_supported_file(candidate, root):
            scope[candidate] = None


def resolve_scope(options: ScanOptions) -> dict[Path, set[int] | None]:
    """Resolve target files and in-scope changed line sets (None means whole file)."""
    root = options.root.resolve()
    if options.explicit_files:
        scope: dict[Path, set[int] | None] = {}
        for item in options.explicit_files:
            resolved = item if item.is_absolute() else (root / item)
            resolved = resolved.resolve()
            if resolved.is_file() and is_supported_file(resolved, root):
                scope[resolved] = None
        return scope

    rc, _ = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if options.scan_mode == "all" or rc != 0:
        return {path: None for path in collect_source_files(root)}

    return _resolve_git_diff_scope(options)
