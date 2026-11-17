"""Shared immutable data structures and constants for the deslop preflight scanner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".java",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".sql",
        ".py",
        ".go",
        ".rs",
        ".sh",
        ".ps1",
    }
)

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "target",
        "build",
        "dist",
        ".venv",
        "venv",
        "__pycache__",
        "vendor",
        ".next",
        ".idea",
        ".gradle",
        "out",
    }
)


@dataclass(frozen=True)
class ScanOptions:
    """Configuration bundle controlling scope and redline thresholds."""

    root: Path
    base_ref: str | None = None
    scan_mode: str = "diff"
    explicit_files: tuple[Path, ...] = ()
    max_cognitive_complexity: int = 15
    max_positional_params: int = 4
    warn_file_lines: int = 400
    min_clone_lines: int = 10


@dataclass(frozen=True)
class Finding:
    """Single deterministic violation or maintainability warning."""

    rule_id: str
    severity: str
    file: str
    line: int
    end_line: int
    message: str
    remedy: str


@dataclass(frozen=True)
class FunctionMetric:
    """Per-function complexity and parameter metrics for hot-spot reporting."""

    file: str
    name: str
    line: int
    end_line: int
    cognitive_complexity: int
    positional_params: int
    bool_flag_params: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanReport:
    """Aggregated preflight report across all scoped files."""

    mode: str
    scanned_files: int
    findings: tuple[Finding, ...]
    hot_functions: tuple[FunctionMetric, ...]
