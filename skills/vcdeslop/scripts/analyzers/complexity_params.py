"""Per-language metric dispatch (Python AST, SQL, POSIX shell) and redline findings for complexity/parameters."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .lexers import find_matching, strip_comments_and_strings
from .metrics_brace import BRACE_EXTS, collect_brace_metrics, collect_ps1_metrics, is_trivial_setter, line_of
from .models import Finding, FunctionMetric, ScanOptions, make_finding

_SQL_TOKEN_RE = re.compile(r"\(\s*SELECT\b|\bCASE\b|\bEND\b|\bWHEN\b|\bAND\b|\bOR\b|[()]", re.IGNORECASE)
_SH_FN_RE = re.compile(r"(?m)^[ \t]*(?:function[ \t]+([A-Za-z_]\w*)(?:[ \t]*\(\s*\))?|([A-Za-z_]\w*)[ \t]*\(\s*\))[ \t]*\{")
_SH_TOKEN_RE = re.compile(r"\belif\b|\belse\b|\bif\b|\bfi\b|\bfor\b|\bwhile\b|\buntil\b|\bcase\b|\b(?:done|esac)\b|&&|\|\||;|\n")


@dataclass
class _SqlState:
    score: int = 0
    depth: int = 0
    case_depth: int = 0
    prev_bool: str = ""


def _py_score_node(node: ast.AST, depth: int) -> int:
    """Recursively compute Sonar-style Cognitive Complexity for a Python AST node."""
    if isinstance(node, ast.If):
        return _py_if_complexity(node, depth, is_elif=False)
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
        return 1 + depth + sum(_py_score_node(c, depth + 1) for c in ast.iter_child_nodes(node))
    if isinstance(node, ast.BoolOp):
        return 1 + sum(_py_score_node(c, depth) for c in ast.iter_child_nodes(node))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return sum(_py_score_node(c, depth + 1) for c in ast.iter_child_nodes(node))
    return sum(_py_score_node(c, depth) for c in ast.iter_child_nodes(node))


def _py_if_complexity(node: ast.If, depth: int, *, is_elif: bool) -> int:
    """Score Python if/elif/else chains without double-nesting elif branches."""
    score = (1 if is_elif else 1 + depth) + _py_score_node(node.test, depth)
    score += sum(_py_score_node(stmt, depth + 1) for stmt in node.body)
    if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        return score + _py_if_complexity(node.orelse[0], depth, is_elif=True)
    if node.orelse:
        score += 1 + sum(_py_score_node(stmt, depth + 1) for stmt in node.orelse)
    return score


def _is_py_bool_annotation(ann: ast.expr | None) -> bool:
    """Return True for `bool`, `bool | None`, `Optional[bool]`, or `Union[bool, ...]`."""
    if isinstance(ann, ast.Name):
        return ann.id == "bool"
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _is_py_bool_annotation(ann.left) or _is_py_bool_annotation(ann.right)
    if isinstance(ann, ast.Subscript):
        base = ann.value.attr if isinstance(ann.value, ast.Attribute) else getattr(ann.value, "id", "")
        if base == "Optional":
            return _is_py_bool_annotation(ann.slice)
        if base == "Union" and isinstance(ann.slice, ast.Tuple):
            return any(_is_py_bool_annotation(elt) for elt in ann.slice.elts)
    return False


def _py_function_metric(rel: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionMetric:
    """Build one FunctionMetric (keyword-only args are exempt from Boolean Traps)."""
    pos = list(node.args.posonlyargs) + list(node.args.args)
    if pos and pos[0].arg in {"self", "cls"}:
        pos = pos[1:]
    defaults: list[ast.expr | None] = [None] * (len(pos) - len(node.args.defaults)) + list(node.args.defaults)
    flags = tuple(
        a.arg for a, d in zip(pos, defaults)
        if _is_py_bool_annotation(a.annotation) or (isinstance(d, ast.Constant) and isinstance(d.value, bool))
    )
    if is_trivial_setter(node.name, len(pos)):
        flags = ()
    score = sum(_py_score_node(s, 0) for s in node.body)
    end_line = getattr(node, "end_lineno", None) or node.lineno
    return FunctionMetric(rel, node.name, node.lineno, end_line, score, len(pos), flags)


def _collect_python_metrics(rel: str, text: str) -> list[FunctionMetric]:
    """Extract function metrics from the Python AST."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [
        _py_function_metric(rel, node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _step_sql_token(tok: str, state: _SqlState, paren_stack: list[int]) -> None:
    """Advance SQL Cognitive Complexity state for one SQL structural token."""
    if tok in {"AND", "OR"}:
        if tok != state.prev_bool:
            state.score += 1
            state.prev_bool = tok
        return
    state.prev_bool = ""
    if tok.startswith("(") and "SELECT" in tok:
        state.score += 1 + state.depth
        state.depth += 1
        paren_stack.append(1)
    elif tok == "(":
        paren_stack.append(0)
    elif tok == ")" and paren_stack:
        state.depth = max(0, state.depth - paren_stack.pop())
    elif tok == "CASE":
        state.score += 1 + state.depth
        state.case_depth += 1
        state.depth += 1
    elif tok == "WHEN" and state.case_depth > 1:
        state.score += 1
    elif tok == "END" and state.case_depth > 0:
        state.case_depth -= 1
        state.depth = max(0, state.depth - 1)


def _collect_sql_metrics(rel: str, text: str) -> list[FunctionMetric]:
    """Compute statement-level Cognitive Complexity metrics for SQL scripts."""
    cleaned = strip_comments_and_strings(text, ".sql")
    metrics: list[FunctionMetric] = []
    offset = 0
    for idx, stmt in enumerate(cleaned.split(";"), start=1):
        if stmt.strip():
            state, paren_stack = _SqlState(), []
            for match in _SQL_TOKEN_RE.finditer(stmt):
                _step_sql_token(match.group(0).upper(), state, paren_stack)
            start, end = line_of(cleaned, offset), line_of(cleaned, offset + len(stmt))
            metrics.append(FunctionMetric(rel, f"sql_stmt_{idx}", start, end, state.score, 0, ()))
        offset += len(stmt) + 1
    return metrics


def _score_sh_body(body: str) -> int:
    """Compute Cognitive Complexity score for one shell function body."""
    score, depth, prev_bool = 0, 0, ""
    for tok in _SH_TOKEN_RE.findall(body):
        if tok in {"&&", "||"}:
            score += int(tok != prev_bool)
            prev_bool = tok
            continue
        prev_bool = ""
        if tok in {"elif", "else"}:
            score += 1
        elif tok in {"fi", "done", "esac"}:
            depth = max(0, depth - 1)
        elif tok in {"if", "for", "while", "until", "case"}:
            score += 1 + depth
            depth += 1
    return score


def _collect_sh_metrics(rel: str, text: str) -> list[FunctionMetric]:
    """Extract function Cognitive Complexity metrics from POSIX/Bash scripts."""
    cleaned = strip_comments_and_strings(text, ".sh")
    metrics: list[FunctionMetric] = []
    cursor = 0
    for match in _SH_FN_RE.finditer(cleaned):
        if match.start() < cursor:
            continue
        brace_o = match.end() - 1
        brace_c = find_matching(cleaned, brace_o, "{", "}")
        if brace_c < 0:
            continue
        name = match.group(1) or match.group(2)
        score = _score_sh_body(cleaned[brace_o + 1 : brace_c])
        metrics.append(FunctionMetric(rel, name, line_of(cleaned, match.start()), line_of(cleaned, brace_c), score, 0, ()))
        cursor = brace_c + 1
    return metrics


def _dispatch_metrics(ext: str, rel: str, raw_text: str) -> list[FunctionMetric]:
    """Dispatch per-language metric extraction."""
    if ext == ".py":
        return _collect_python_metrics(rel, raw_text)
    if ext == ".sql":
        return _collect_sql_metrics(rel, raw_text)
    if ext == ".ps1":
        return collect_ps1_metrics(rel, raw_text)
    if ext in {".sh", ""}:
        return _collect_sh_metrics(rel, raw_text)
    return collect_brace_metrics(rel, raw_text, ext) if ext in BRACE_EXTS else []


def _metric_findings(m: FunctionMetric, options: ScanOptions) -> list[Finding]:
    """Translate one function's metrics into redline findings."""
    at = (m.line, m.line)
    found: list[Finding] = []
    if m.cognitive_complexity > options.max_cognitive_complexity:
        msg = f"`{m.name}` has Cognitive Complexity {m.cognitive_complexity} (> {options.max_cognitive_complexity})."
        found.append(make_finding("REDLINE-COMPLEXITY", m.file, at, msg))
    if m.positional_params > options.max_positional_params:
        msg = f"`{m.name}` takes {m.positional_params} positional parameters (> {options.max_positional_params})."
        found.append(make_finding("REDLINE-PARAMS", m.file, at, msg))
    if m.bool_flag_params:
        msg = f"`{m.name}` uses Boolean flag parameter(s): {', '.join(m.bool_flag_params)}."
        found.append(make_finding("REDLINE-BOOL-TRAP", m.file, at, msg))
    return found


def analyze_file_complexity(
    path: Path,
    raw_text: str,
    rel: str,
    options: ScanOptions,
    *,
    changed_lines: set[int] | None = None,
) -> tuple[list[Finding], list[FunctionMetric]]:
    """Analyze advisory file size, function complexity, parameter counts, and Boolean Traps."""
    findings: list[Finding] = []
    loc = len(raw_text.splitlines())
    if loc > options.warn_file_lines:
        msg = f"File has {loc} lines (advisory threshold {options.warn_file_lines}; non-blocking)."
        findings.append(make_finding("SLOP-FILE-SIZE", rel, (1, loc), msg))
    metrics = _dispatch_metrics(path.suffix.lower(), rel, raw_text)
    for m in metrics:
        if changed_lines is None or any(ln in changed_lines for ln in range(m.line, m.end_line + 1)):
            findings.extend(_metric_findings(m, options))
    return findings, metrics
