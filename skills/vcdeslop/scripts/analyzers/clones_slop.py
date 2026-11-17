"""Duplicate code block detection and AI slop / SQL smell analyzers."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .git_scope import to_rel_posix
from .lexers import strip_comments_and_strings, strip_comments_only, strip_strings_only
from .models import Finding, ScanOptions

_SKIP_NORM_LINES = frozenset({"{", "}", "};", ");", "else {", "end", "pass", "break", "continue"})
_IMPORT_PREFIXES = ("import ", "from ", "package ", "use ", "using ", "#include")

_EMPTY_CATCH_RE = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")
_SWALLOW_CATCH_RE = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?:console\.\w+|log(?:ger)?\.\w+|e\.printStackTrace"
    r"|System\.(?:out|err)\.print\w*)\s*\([^;{}]*\)\s*;?\s*(?:return(?:\s+(?:null|undefined|false))?\s*;?)?\s*\}"
)
_PS1_EMPTY_CATCH_RE = re.compile(r"\bcatch\s*(?:\[[^\]]*\])?\s*\{\s*\}", re.IGNORECASE)
_PY_SWALLOW_RE = re.compile(
    r"^[ \t]*except\b[^\n:]*:\s*(?:\n[ \t]+(?:pass|return(?:\s+None)?)(?:[ \t]*#[^\n]*)?[ \t]*$)",
    re.MULTILINE,
)
_TS_ANY_RE = re.compile(r"\bas\s+any\b|:\s*any\b")
_TS_IGNORE_RE = re.compile(r"//[^\n]*@ts-ignore\b")
_JAVA_SUPPRESS_RE = re.compile(r"@SuppressWarnings\s*\([^)]*unchecked[^)]*\)")
_PY_IGNORE_RE = re.compile(r'(?:^|\s)#\s*type:\s*ignore(?![\[a-zA-Z0-9_-])(?=[ \t]*(?:#[^\n]*)?$)', re.MULTILINE)
_BRACE_TERNARY_RE = re.compile(r"\?[^:;\n]*\?[^;\n]*:")
_PY_TERNARY_RE = re.compile(r"\bif\b[^#\n]+\belse\b[^#\n]+\bif\b[^#\n]+\belse\b")

_SQL_SELECT_STAR_RE = re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?\*", re.IGNORECASE)
_SQL_DISTINCT_JOIN_RE = re.compile(r"\bSELECT\s+DISTINCT\b[^;]{0,400}\bJOIN\b", re.IGNORECASE)
_SQL_NONSARGABLE_RE = re.compile(
    r"\bWHERE\s+(?:(?:(?!\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION)\b)[^;]){0,300}?\b(?:AND|OR))?\s*\(*\s*"
    r"(?:DATE|YEAR|MONTH|LOWER|UPPER|SUBSTR|SUBSTRING|TRIM|COALESCE|IFNULL)\s*\(\s*[A-Za-z_][\w.]*(?:\s*,[^();]*)?\)"
    r"\s*(?:=|!=|<>|<=|>=|<|>|\b(?:IN|LIKE|BETWEEN)\b)",
    re.IGNORECASE,
)
_SQL_RAW_INTERP_RE = re.compile(r"\$\{[^}]+\}")


@dataclass(frozen=True)
class _SlopSpec:
    """Regex rule specification for AI slop and SQL smell checks."""

    rule_id: str
    severity: str
    target_mode: str
    pattern: re.Pattern[str]
    message: str
    remedy: str


_PY_SPECS = (
    _SlopSpec("SLOP-SILENT-CATCH", "WARNING", "code", _PY_SWALLOW_RE, "Except block silently passes or returns None.", "Let error propagate or handle/contextualize the specific failure."),
    _SlopSpec("SLOP-TYPE-EVASION", "WARNING", "no_strings", _PY_IGNORE_RE, "Bare `# type: ignore` bypasses type checking without error code.", "Fix the underlying type mismatch or scope to `# type: ignore[code]`."),
    _SlopSpec("SLOP-NESTED-TERNARY", "WARNING", "code", _PY_TERNARY_RE, "Nested inline conditional expression harms readability.", "Replace with explicit `if/elif/else` or lookup mapping."),
)

_SQL_SPECS = (
    _SlopSpec("SLOP-SQL-INJECTION", "BLOCKER", "no_comments", _SQL_RAW_INTERP_RE, "Raw `${...}` SQL interpolation risks SQL injection.", "Use parameterized binding (`#{...}`, `?`, or named parameters)."),
    _SlopSpec("SLOP-SQL-SMELL", "WARNING", "code", _SQL_SELECT_STAR_RE, "`SELECT *` couples callers to arbitrary table schema changes.", "Project explicit column names."),
    _SlopSpec("SLOP-SQL-SMELL", "WARNING", "code", _SQL_DISTINCT_JOIN_RE, "`SELECT DISTINCT` with `JOIN` often masks missing join predicates.", "Verify join cardinality or use `EXISTS` instead of `DISTINCT`."),
    _SlopSpec("SLOP-SQL-SMELL", "WARNING", "code", _SQL_NONSARGABLE_RE, "Function wrapped around column in `WHERE` prevents index usage.", "Rewrite as sargable range predicate (e.g., `col >= start AND col < end`)."),
)

_BRACE_BASE_SPECS = (
    _SlopSpec("SLOP-SILENT-CATCH", "WARNING", "code", _EMPTY_CATCH_RE, "Empty `catch` block silently swallows errors.", "Propagate failure or handle explicitly at an I/O boundary."),
    _SlopSpec("SLOP-SILENT-CATCH", "WARNING", "code", _SWALLOW_CATCH_RE, "`catch` block only logs or returns null without real recovery.", "Avoid defensive try/catch inside internal trusted flows."),
    _SlopSpec("SLOP-NESTED-TERNARY", "WARNING", "code", _BRACE_TERNARY_RE, "Nested ternary expression harms 3 AM readability.", "Flatten into guard clauses or explicit `if/else` statements."),
)

_TS_EXTRA_SPECS = (
    _SlopSpec("SLOP-TYPE-EVASION", "WARNING", "code", _TS_ANY_RE, "`any` type annotation or cast bypasses type safety.", "Use concrete interfaces, generics, or `unknown` with narrowing."),
    _SlopSpec("SLOP-TYPE-EVASION", "WARNING", "no_strings", _TS_IGNORE_RE, "`@ts-ignore` suppresses compiler checks blindly.", "Fix the type or replace with `@ts-expect-error` + rationale."),
)

_JAVA_EXTRA_SPECS = (
    _SlopSpec("SLOP-TYPE-EVASION", "WARNING", "no_comments", _JAVA_SUPPRESS_RE, "`@SuppressWarnings(\"unchecked\")` hides raw-type safety issues.", "Use explicit generic types or bounded type tokens."),
)

_PS1_SPECS = (
    _SlopSpec("SLOP-SILENT-CATCH", "WARNING", "code", _PS1_EMPTY_CATCH_RE, "Empty `catch` block silently swallows errors.", "Propagate failure or handle explicitly at an I/O boundary."),
)


def _normalize_file(path: Path) -> list[tuple[int, str]]:
    """Return (original_line_number, normalized_text) pairs for clone comparison."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    cleaned = strip_comments_and_strings(raw, path.suffix.lower())
    result: list[tuple[int, str]] = []
    for lineno, line in enumerate(cleaned.splitlines(), start=1):
        norm = " ".join(line.split())
        if norm and norm not in _SKIP_NORM_LINES and not norm.startswith(_IMPORT_PREFIXES):
            result.append((lineno, norm))
    return result


def _in_line_scope(start_line: int, end_line: int, changed: set[int] | None) -> bool:
    """Return True if [start_line, end_line] overlaps changed lines (or whole file)."""
    if changed is None:
        return True
    return any(ln in changed for ln in range(start_line, end_line + 1))


def _index_clone_buckets(
    all_files: list[Path],
    win_size: int,
) -> tuple[dict[Path, list[tuple[int, str]]], dict[tuple[str, tuple[str, ...]], list[tuple[Path, int]]]]:
    """Index sliding windows of normalized lines across all source files."""
    norm_cache: dict[Path, list[tuple[int, str]]] = {}
    buckets: dict[tuple[str, tuple[str, ...]], list[tuple[Path, int]]] = defaultdict(list)
    for path in all_files:
        norm = _normalize_file(path)
        if len(norm) < win_size:
            continue
        norm_cache[path] = norm
        ext = path.suffix.lower()
        lines_only = [txt for _, txt in norm]
        for idx in range(len(lines_only) - win_size + 1):
            window = tuple(lines_only[idx : idx + win_size])
            if len(set(window)) >= 3:
                buckets[(ext, window)].append((path, idx))
    return norm_cache, buckets


def _collect_bucket_pairs(
    occs: list[tuple[Path, int]],
    scope: dict[Path, set[int] | None],
    win_size: int,
) -> list[tuple[Path, int, Path, int]]:
    """Collect valid non-overlapping clone pairs from one hash bucket."""
    if len(occs) < 2 or len(occs) > 12:
        return []
    pairs: list[tuple[Path, int, Path, int]] = []
    for i, (pa, ia) in enumerate(occs):
        for pb, ib in occs[i + 1 :]:
            if pa == pb and ia + win_size > ib:
                continue
            if pa in scope or pb in scope:
                pairs.append((pa, ia, pb, ib))
    return pairs


def _coalesce_pairs(
    pairs: list[tuple[Path, int, Path, int]],
    win_size: int,
) -> list[tuple[Path, int, int, Path, int, int]]:
    """Merge consecutive sliding-window clone matches along identical diagonals."""
    if not pairs:
        return []
    pairs.sort(key=lambda p: (str(p[0]), str(p[2]), p[1] - p[3], p[1]))
    merged: list[tuple[Path, int, int, Path, int, int]] = []
    cur_pa, cur_ia, cur_pb, cur_ib = pairs[0]
    cur_ea, cur_eb = cur_ia + win_size - 1, cur_ib + win_size - 1
    for pa, ia, pb, ib in pairs[1:]:
        same_diag = pa == cur_pa and pb == cur_pb and (ia - ib) == (cur_ia - cur_ib)
        if same_diag and ia <= cur_ea + 1:
            cur_ea = max(cur_ea, ia + win_size - 1)
            cur_eb = max(cur_eb, ib + win_size - 1)
            continue
        merged.append((cur_pa, cur_ia, cur_ea, cur_pb, cur_ib, cur_eb))
        cur_pa, cur_ia, cur_pb, cur_ib = pa, ia, pb, ib
        cur_ea, cur_eb = ia + win_size - 1, ib + win_size - 1
    merged.append((cur_pa, cur_ia, cur_ea, cur_pb, cur_ib, cur_eb))
    return merged


def detect_duplicates(
    scope: dict[Path, set[int] | None],
    all_files: list[Path],
    options: ScanOptions,
) -> list[Finding]:
    """Detect duplicated normalized code blocks touching in-scope files."""
    win_size = options.min_clone_lines
    norm_cache, buckets = _index_clone_buckets(all_files, win_size)
    raw_pairs: list[tuple[Path, int, Path, int]] = []
    for occs in buckets.values():
        raw_pairs.extend(_collect_bucket_pairs(occs, scope, win_size))

    findings: list[Finding] = []
    for pa, ia, ea, pb, ib, eb in _coalesce_pairs(raw_pairs, win_size):
        sa_line, ea_line = norm_cache[pa][ia][0], norm_cache[pa][ea][0]
        sb_line, eb_line = norm_cache[pb][ib][0], norm_cache[pb][eb][0]
        hit_a = pa in scope and _in_line_scope(sa_line, ea_line, scope[pa])
        hit_b = pb in scope and _in_line_scope(sb_line, eb_line, scope[pb])
        if not (hit_a or hit_b):
            continue
        choose_b = hit_b and not hit_a
        tp, ts, te = (pb, sb_line, eb_line) if choose_b else (pa, sa_line, ea_line)
        op, os_line, oe_line = (pa, sa_line, ea_line) if choose_b else (pb, sb_line, eb_line)
        findings.append(
            Finding(
                "REDLINE-DUPLICATION",
                "BLOCKER",
                to_rel_posix(tp, options.root),
                ts,
                te,
                f"Duplicate block ({ea - ia + 1} normalized lines) matches `{to_rel_posix(op, options.root)}:{os_line}-{oe_line}`.",
                "Extract and reuse a shared cohesive function/module unless business domains diverge independently.",
            )
        )
    return findings


def _specs_for_ext(ext: str) -> tuple[_SlopSpec, ...]:
    """Select applicable slop regex specifications for a file extension."""
    if ext == ".py":
        return _PY_SPECS
    if ext == ".sql":
        return _SQL_SPECS
    if ext == ".ps1":
        return _PS1_SPECS
    if ext in {".sh", ""}:
        return ()
    if ext in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return _BRACE_BASE_SPECS + _TS_EXTRA_SPECS
    if ext == ".java":
        return _BRACE_BASE_SPECS + _JAVA_EXTRA_SPECS
    return _BRACE_BASE_SPECS


def _run_slop_spec(spec: _SlopSpec, rel: str, text: str, changed: set[int] | None) -> list[Finding]:
    """Evaluate one SlopSpec regex against text and return scoped Findings."""
    found: list[Finding] = []
    for match in spec.pattern.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if changed is not None and line not in changed:
            continue
        found.append(Finding(spec.rule_id, spec.severity, rel, line, line, spec.message, spec.remedy))
    return found


def detect_ai_slop(
    path: Path,
    raw_text: str,
    changed: set[int] | None,
    root: Path,
) -> list[Finding]:
    """Detect AI slop patterns: silent catches, type evasion, nested ternaries, SQL smells."""
    ext = path.suffix.lower()
    specs = _specs_for_ext(ext)
    if not specs:
        return []
    rel = to_rel_posix(path, root)
    views = {
        "code": strip_comments_and_strings(raw_text, ext),
        "no_comments": strip_comments_only(raw_text, ext),
        "no_strings": strip_strings_only(raw_text, ext),
    }
    findings: list[Finding] = []
    for spec in specs:
        findings.extend(_run_slop_spec(spec, rel, views[spec.target_mode], changed))
    return findings
