"""Duplicate code block detection and AI slop / SQL smell analyzers."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .git_scope import to_rel_posix
from .lexers import strip_comments_and_strings, strip_comments_only, strip_strings_only
from .models import Finding, ScanOptions, make_finding

_SKIP_NORM_LINES = frozenset({"{", "}", "};", ");", "else {", "end", "pass", "break", "continue"})
_IMPORT_PREFIXES = ("import ", "from ", "package ", "use ", "using ", "#include")

_EMPTY_CATCH_RE = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")
_SWALLOW_CATCH_RE = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?:console\.\w+|log(?:ger)?\.\w+|e\.printStackTrace"
    r"|System\.(?:out|err)\.print\w*)\s*\([^;{}]*\)\s*;?\s*(?:return(?:\s+(?:null|undefined|false))?\s*;?)?\s*\}"
)
_PS1_EMPTY_CATCH_RE = re.compile(r"\bcatch\s*(?:\[[^\]]*\])?\s*\{\s*\}", re.IGNORECASE)
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

    catalog_key: str
    target_mode: str
    pattern: re.Pattern[str]
    message: str


_PY_SPECS = (
    _SlopSpec('SLOP-PY-IGNORE', 'no_strings', _PY_IGNORE_RE,
              'Bare `# type: ignore` bypasses type checking without error code.'),
    _SlopSpec('SLOP-PY-TERNARY', 'code', _PY_TERNARY_RE,
              'Nested inline conditional expression harms readability.'),
)
_PY_SILENT_MSG = "Except block only passes/logs/returns a sentinel; the failure is swallowed."
_PY_LOG_ROOTS = frozenset({"print", "log", "logger", "logging", "_log", "_logger", "LOG", "LOGGER", "warnings"})

_SQL_SPECS = (
    _SlopSpec('SLOP-SQL-INJECTION', 'no_comments', _SQL_RAW_INTERP_RE,
              'Raw `${...}` SQL interpolation risks SQL injection.'),
    _SlopSpec('SLOP-SQL-SELECT-STAR', 'code', _SQL_SELECT_STAR_RE,
              '`SELECT *` couples callers to arbitrary table schema changes.'),
    _SlopSpec('SLOP-SQL-DISTINCT-JOIN', 'code', _SQL_DISTINCT_JOIN_RE,
              '`SELECT DISTINCT` with `JOIN` often masks missing join predicates.'),
    _SlopSpec('SLOP-SQL-NONSARGABLE', 'code', _SQL_NONSARGABLE_RE,
              'Function wrapped around column in `WHERE` prevents index usage.'),
)

_BRACE_BASE_SPECS = (
    _SlopSpec('SLOP-CATCH-EMPTY', 'code', _EMPTY_CATCH_RE,
              'Empty `catch` block silently swallows errors.'),
    _SlopSpec('SLOP-CATCH-LOG', 'code', _SWALLOW_CATCH_RE,
              '`catch` block only logs or returns null without real recovery.'),
    _SlopSpec('SLOP-BRACE-TERNARY', 'code', _BRACE_TERNARY_RE,
              'Nested ternary expression harms 3 AM readability.'),
)

_TS_EXTRA_SPECS = (
    _SlopSpec('SLOP-TS-ANY', 'code', _TS_ANY_RE,
              '`any` type annotation or cast bypasses type safety.'),
    _SlopSpec('SLOP-TS-IGNORE', 'no_strings', _TS_IGNORE_RE,
              '`@ts-ignore` suppresses compiler checks blindly.'),
)

_JAVA_EXTRA_SPECS = (
    _SlopSpec('SLOP-JAVA-SUPPRESS', 'no_comments', _JAVA_SUPPRESS_RE,
              '`@SuppressWarnings("unchecked")` hides raw-type safety issues.'),
)

_PS1_SPECS = (
    _SlopSpec('SLOP-CATCH-EMPTY', 'code', _PS1_EMPTY_CATCH_RE,
              'Empty `catch` block silently swallows errors.'),
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
        msg = f"Duplicate block ({ea - ia + 1} normalized lines) matches `{to_rel_posix(op, options.root)}:{os_line}-{oe_line}`."
        findings.append(make_finding("REDLINE-DUPLICATION", to_rel_posix(tp, options.root), (ts, te), msg))
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
        found.append(make_finding(spec.catalog_key, rel, (line, line), spec.message))
    return found


def _call_root(node: ast.expr) -> str:
    """Return the leftmost name of a call target (`logger.warning` -> `logger`)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _is_swallow_stmt(stmt: ast.stmt) -> bool:
    """Return True for statements that neither recover nor re-raise: pass, continue, log calls, sentinel returns."""
    if isinstance(stmt, (ast.Pass, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _call_root(stmt.value.func) in _PY_LOG_ROOTS
    if isinstance(stmt, ast.Return):
        return stmt.value is None or (isinstance(stmt.value, ast.Constant) and (stmt.value.value is None or stmt.value.value is False))
    return False


def _is_broad_exception(node: ast.expr | None) -> bool:
    """Recognize bare and explicitly broad exceptions, including tuples."""
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_broad_exception(item) for item in node.elts)
    return isinstance(node, ast.Name) and node.id in {"Exception", "BaseException"}


def _is_silent_handler(handler: ast.ExceptHandler) -> bool:
    """Swallowing body that is broad (`except:`/`Exception`), a bare `pass`, or log-and-forget.

    Narrow I/O-boundary skips such as `except OSError: continue` are intentional and stay exempt.
    """
    if not all(_is_swallow_stmt(s) for s in handler.body):
        return False
    broad = _is_broad_exception(handler.type)
    logs = any(isinstance(s, ast.Expr) for s in handler.body)
    return broad or logs or any(isinstance(s, ast.Pass) for s in handler.body)


def _detect_py_silent_excepts(rel: str, raw_text: str, changed: set[int] | None) -> list[Finding]:
    """Flag `except` handlers that swallow failures instead of propagating or recovering."""
    try:
        tree = ast.parse(raw_text)
    except SyntaxError:
        return []
    return [
        make_finding("SLOP-PY-SILENT", rel, (node.lineno, node.lineno), _PY_SILENT_MSG)
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and _is_silent_handler(node)
        and (changed is None or any(line in changed for line in range(node.lineno, node.end_lineno + 1)))
    ]


def detect_ai_slop(
    path: Path,
    raw_text: str,
    changed: set[int] | None,
    root: Path,
) -> list[Finding]:
    """Detect AI slop patterns: silent catches, type evasion, nested ternaries, SQL smells."""
    ext = path.suffix.lower()
    rel = to_rel_posix(path, root)
    findings = _detect_py_silent_excepts(rel, raw_text, changed) if ext == ".py" else []
    specs = _specs_for_ext(ext)
    if not specs:
        return findings
    views = {
        "code": strip_comments_and_strings(raw_text, ext),
        "no_comments": strip_comments_only(raw_text, ext),
        "no_strings": strip_strings_only(raw_text, ext),
    }
    for spec in specs:
        findings.extend(_run_slop_spec(spec, rel, views[spec.target_mode], changed))
    return findings
