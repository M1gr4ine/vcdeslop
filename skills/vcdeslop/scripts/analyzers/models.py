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
class Rule:
    """Catalog entry: default severity and canonical remedy for one rule id."""

    severity: str
    remedy: str
    output_id: str = ""


RULES: dict[str, Rule] = {
    "REDLINE-COMPLEXITY": Rule(
        "BLOCKER", "Flatten nesting with guard clauses, extract cohesive helpers, or replace nested SQL with WITH CTEs."
    ),
    "REDLINE-PARAMS": Rule("BLOCKER", "Group parameters into a named Record/DTO/Options object or dataclass."),
    "REDLINE-BOOL-TRAP": Rule(
        "BLOCKER", "Split into separate explicit functions, use an Enum/literal mode, or pass named options."
    ),
    "REDLINE-DUPLICATION": Rule(
        "BLOCKER", "Extract and reuse a shared cohesive function/module unless business domains diverge independently."
    ),
    "REDLINE-CYCLE": Rule(
        "BLOCKER", "Extract shared contracts/types into a lower acyclic module or invert dependency."
    ),
    "REDLINE-BOUNDARY": Rule(
        "BLOCKER", "Import through the target module's public facade, or route via a service/port interface."
    ),
    "ADVISORY-LAYER": Rule(
        "WARNING", "Check whether the project architecture calls for a service or port between these layers."
    ),
    'SLOP-PY-IGNORE': Rule('WARNING', 'Fix the underlying type mismatch or scope to `# type: ignore[code]`.', 'SLOP-TYPE-EVASION'),
    'SLOP-PY-TERNARY': Rule('WARNING', 'Replace with explicit `if/elif/else` or lookup mapping.', 'SLOP-NESTED-TERNARY'),
    'SLOP-SQL-INJECTION': Rule('BLOCKER', 'Use parameterized binding (`#{...}`, `?`, or named parameters).', 'SLOP-SQL-INJECTION'),
    'SLOP-SQL-SELECT-STAR': Rule('WARNING', 'Project explicit column names.', 'SLOP-SQL-SMELL'),
    'SLOP-SQL-DISTINCT-JOIN': Rule('WARNING', 'Verify join cardinality or use `EXISTS` instead of `DISTINCT`.', 'SLOP-SQL-SMELL'),
    'SLOP-SQL-NONSARGABLE': Rule('WARNING', 'Rewrite as sargable range predicate (e.g., `col >= start AND col < end`).', 'SLOP-SQL-SMELL'),
    'SLOP-CATCH-EMPTY': Rule('WARNING', 'Propagate failure or handle explicitly at an I/O boundary.', 'SLOP-SILENT-CATCH'),
    'SLOP-CATCH-LOG': Rule('WARNING', 'Avoid defensive try/catch inside internal trusted flows.', 'SLOP-SILENT-CATCH'),
    'SLOP-BRACE-TERNARY': Rule('WARNING', 'Flatten into guard clauses or explicit `if/else` statements.', 'SLOP-NESTED-TERNARY'),
    'SLOP-TS-ANY': Rule('WARNING', 'Use concrete interfaces, generics, or `unknown` with narrowing.', 'SLOP-TYPE-EVASION'),
    'SLOP-TS-IGNORE': Rule('WARNING', 'Fix the type or replace with `@ts-expect-error` + rationale.', 'SLOP-TYPE-EVASION'),
    'SLOP-JAVA-SUPPRESS': Rule('WARNING', 'Use explicit generic types or bounded type tokens.', 'SLOP-TYPE-EVASION'),
    'SLOP-PY-SILENT': Rule('WARNING', 'Let the error propagate, re-raise with context, or handle the specific failure at an I/O boundary.', 'SLOP-SILENT-CATCH'),
    "SLOP-FILE-SIZE": Rule(
        "WARNING", "Verify responsibilities remain a cohesive deep module; do not split into shallow pass-through wrappers."
    ),
}


def make_finding(rule_id: str, file: str, lines: tuple[int, int], message: str) -> Finding:
    """Build a Finding whose severity and remedy come from the RULES catalog."""
    rule = RULES[rule_id]
    return Finding(rule.output_id or rule_id, rule.severity, file, lines[0], lines[1], message, rule.remedy)


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
