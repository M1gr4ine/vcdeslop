"""Advisory file-size warning, parameter design (Boolean Trap), and Cognitive Complexity analyzers."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .lexers import find_matching, split_top_level_params, strip_comments_and_strings
from .models import Finding, FunctionMetric, ScanOptions

_BRACE_EXTS = frozenset({".java", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs"})
_CONTROL_WORDS = frozenset(
    {"if", "for", "while", "switch", "catch", "else", "try", "return", "new", "throw", "typeof", "await", "sizeof", "match", "select", "when", "case", "default"}
)

_BRACE_FN_RE = re.compile(
    r"""(?x)
    \b(?:function|func|fn)\s+(?:\([^)]*\)\s*)?([A-Za-z_$][\w$]*)\s*(?:<[^>{}()\n]*>)?\s*\(
    | \b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\(
    | (?:^|\n)[ \t]*(?:(?:public|protected|private|static|final|synchronized|abstract|default|native|async|override|readonly)\s+)*
      (?:<[^;{}\n]+>\s+)?(?:[\w.$<>\[\]?|&\s]+\s+)?([A-Za-z_$][\w$]*)\s*\(
    """
)
_BRACE_TOKEN_RE = re.compile(r"\belse\s+if\b|\belse\b|\bif\b|\bfor\b|\bwhile\b|\bcatch\b|\bswitch\b|\bmatch\b|\bloop\b|&&|\|\||\?(?![.:])|[;{}()]")
_SQL_TOKEN_RE = re.compile(r"\(\s*SELECT\b|\bCASE\b|\bEND\b|\bWHEN\b|\bAND\b|\bOR\b|[()]", re.IGNORECASE)
_PS1_FN_RE = re.compile(r"(?im)^\s*function\s+([A-Za-z_][\w-]*)\s*(?:\(([^)]*)\))?\s*\{")
_PS1_PARAM_RE = re.compile(r"(?is)^\s*(?:\[CmdletBinding\([^\)]*\)\]\s*)?param\s*\(")
_PS1_TOKEN_RE = re.compile(r"(?i)(?<!-)\b(?:elseif|else|if|foreach|for|while|switch|catch)\b(?!-)|-and\b|-or\b|[;{}()]")
_PS1_BOOL_RE = re.compile(r"(?i)\[bool\]|=\s*\$(?:true|false)\b")
_SH_FN_RE = re.compile(r"(?m)^[ \t]*(?:function[ \t]+([A-Za-z_]\w*)(?:[ \t]*\(\s*\))?|([A-Za-z_]\w*)[ \t]*\(\s*\))[ \t]*\{")
_SH_TOKEN_RE = re.compile(r"\belif\b|\belse\b|\bif\b|\bfi\b|\bfor\b|\bwhile\b|\buntil\b|\bcase\b|\b(?:done|esac)\b|&&|\|\||;|\n")
_BOOL_RE_MAP = {".java": re.compile(r"\b(?:boolean|Boolean)\b"), ".go": re.compile(r"\bbool\b"), ".rs": re.compile(r"\bbool\b")}
_JS_BOOL_RE = re.compile(r":\s*boolean\b(?!\s*\[)|=\s*(?:true|false)\b")


@dataclass
class _BraceState:
    score: int = 0
    depth: int = 0
    pending_nest: bool = False
    prev_bool: str = ""
    paren_depth: int = 0


@dataclass
class _SqlState:
    score: int = 0
    depth: int = 0
    case_depth: int = 0
    prev_bool: str = ""


def _is_trivial_setter(name: str, param_count: int) -> bool:
    """Allow single-argument state setters like setVisible(true)."""
    return param_count == 1 and name.startswith("set") and len(name) > 3 and (name[3] == "_" or name[3].isupper())


def _find_body_brace(text: str, close_paren: int) -> int:
    """Return opening '{' index if signature transitions cleanly to a function body."""
    paren_depth = 0
    for idx in range(close_paren + 1, min(len(text), close_paren + 240)):
        ch = text[idx]
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            if paren_depth == 0:
                return -1
            paren_depth -= 1
        elif ch == ";" or (ch == "=" and text[idx : idx + 2] != "=>"):
            return -1
        elif ch == "{" and paren_depth == 0:
            return idx
    return -1


def _is_bool_trap_param(param: str, ext: str) -> bool:
    """Return True when a parameter represents a scalar Boolean flag rather than a collection/options DTO."""
    if "{" in param or "<" in param:
        return False
    if ext in _BOOL_RE_MAP:
        return "[" not in param and bool(_BOOL_RE_MAP[ext].search(param))
    return bool(_JS_BOOL_RE.search(param))


def _analyze_brace_params(name: str, raw_params: str, ext: str) -> tuple[int, tuple[str, ...]]:
    """Count positional parameters and detect Boolean Traps in brace languages."""
    params = split_top_level_params(raw_params)
    if _is_trivial_setter(name, len(params)):
        return len(params), ()
    return len(params), tuple(p for p in params if _is_bool_trap_param(p, ext))


def _step_brace_delim(tok: str, state: _BraceState) -> None:
    """Track parentheses and statement resets inside brace-language complexity."""
    if tok == "(":
        state.paren_depth += 1
    elif tok == ")":
        state.paren_depth = max(0, state.paren_depth - 1)
    elif state.paren_depth == 0:
        state.pending_nest = False


def _step_brace_token(tok: str, state: _BraceState, nest_stack: list[int]) -> None:
    """Advance brace-language cognitive complexity state for one lexical token."""
    if tok in {"&&", "||"}:
        if tok != state.prev_bool:
            state.score += 1
            state.prev_bool = tok
        return
    state.prev_bool = ""
    if tok in {"(", ")", ";"}:
        _step_brace_delim(tok, state)
        return
    if tok == "{":
        mark = int(state.pending_nest)
        nest_stack.append(mark)
        state.depth += mark
        state.pending_nest = False
    elif tok == "}":
        if nest_stack:
            state.depth -= nest_stack.pop()
    elif tok.startswith("else"):
        state.score += 1
        state.pending_nest = True
    else:
        state.score += 1 + state.depth
        state.pending_nest = tok != "?"


def _extract_fn_span(cleaned: str, match: re.Match[str], cursor: int) -> tuple[str, int, int, int, int] | None:
    """Validate and extract (name, open_paren, close_paren, brace_open, brace_close)."""
    if match.start() < cursor:
        return None
    name = next((g for g in match.groups() if g), "")
    if not name or name in _CONTROL_WORDS:
        return None
    if cleaned[max(0, match.start(0)) : match.end(0) - 1].rstrip().endswith((".", "new", "return")):
        return None
    open_p = match.end() - 1
    close_p = find_matching(cleaned, open_p, "(", ")")
    brace_o = _find_body_brace(cleaned, close_p) if close_p >= 0 else -1
    brace_c = find_matching(cleaned, brace_o, "{", "}") if brace_o >= 0 else -1
    return (name, open_p, close_p, brace_o, brace_c) if brace_c >= 0 else None


def _collect_brace_metrics(rel: str, text: str, ext: str) -> list[FunctionMetric]:
    """Extract function metrics from Java, JS/TS, Go, or Rust source text."""
    cleaned = strip_comments_and_strings(text, ext)
    metrics: list[FunctionMetric] = []
    cursor = 0
    for match in _BRACE_FN_RE.finditer(cleaned):
        span = _extract_fn_span(cleaned, match, cursor)
        if span is None:
            continue
        name, open_p, close_p, brace_o, brace_c = span
        line = cleaned.count("\n", 0, match.start()) + 1
        end_line = cleaned.count("\n", 0, brace_c) + 1
        param_count, bool_flags = _analyze_brace_params(name, cleaned[open_p + 1 : close_p], ext)
        state, nest_stack = _BraceState(), []
        for tok_m in _BRACE_TOKEN_RE.finditer(cleaned[brace_o + 1 : brace_c]):
            _step_brace_token(tok_m.group(0), state, nest_stack)
        metrics.append(FunctionMetric(rel, name, line, end_line, state.score, param_count, bool_flags))
        cursor = brace_c + 1
    return metrics


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


def _collect_python_metrics(rel: str, text: str) -> list[FunctionMetric]:
    """Extract function metrics from Python AST (keyword-only args exempt from traps)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    metrics: list[FunctionMetric] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        pos = list(node.args.posonlyargs) + list(node.args.args)
        if pos and pos[0].arg in {"self", "cls"}:
            pos = pos[1:]
        defaults: list[ast.expr | None] = [None] * (len(pos) - len(node.args.defaults)) + list(node.args.defaults)
        flags = tuple(
            a.arg for a, d in zip(pos, defaults)
            if _is_py_bool_annotation(a.annotation) or (isinstance(d, ast.Constant) and isinstance(d.value, bool))
        )
        if _is_trivial_setter(node.name, len(pos)):
            flags = ()
        end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
        metrics.append(FunctionMetric(rel, node.name, node.lineno, end_line, sum(_py_score_node(s, 0) for s in node.body), len(pos), flags))
    return metrics


def _step_sql_token(tok: str, state: _SqlState, paren_stack: list[int]) -> None:
    """Advance SQL Cognitive Complexity state for one SQL structural token."""
    if tok in {"AND", "OR"}:
        if tok != state.prev_bool:
            state.score, state.prev_bool = state.score + 1, tok
        return
    state.prev_bool = ""
    if tok.startswith("(") and "SELECT" in tok:
        state.score, state.depth = state.score + 1 + state.depth, state.depth + 1
        paren_stack.append(1)
    elif tok == "(":
        paren_stack.append(0)
    elif tok == ")" and paren_stack:
        state.depth = max(0, state.depth - paren_stack.pop())
    elif tok == "CASE":
        state.score, state.case_depth, state.depth = state.score + 1 + state.depth, state.case_depth + 1, state.depth + 1
    elif tok == "WHEN" and state.case_depth > 1:
        state.score += 1
    elif tok == "END" and state.case_depth > 0:
        state.case_depth, state.depth = state.case_depth - 1, max(0, state.depth - 1)


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
            start_ln = cleaned.count("\n", 0, offset) + 1
            end_ln = cleaned.count("\n", 0, offset + len(stmt)) + 1
            metrics.append(FunctionMetric(rel, f"sql_stmt_{idx}", start_ln, end_ln, state.score, 0, ()))
        offset += len(stmt) + 1
    return metrics


def _extract_ps1_params(match: re.Match[str], body: str) -> tuple[int, tuple[str, ...]]:
    """Extract PowerShell parameter count and scalar Boolean Traps (exempting `[switch]`)."""
    raw_params = match.group(2) or ""
    if not raw_params and (pm := _PS1_PARAM_RE.match(body)):
        p_close = find_matching(body, pm.end() - 1, "(", ")")
        raw_params = body[pm.end() : p_close] if p_close >= 0 else ""
    params = split_top_level_params(raw_params)
    if _is_trivial_setter(match.group(1), len(params)):
        return len(params), ()
    flags = tuple(
        p for p in params
        if "[switch]" not in p.lower() and "[bool[]]" not in p.lower() and _PS1_BOOL_RE.search(p)
    )
    return len(params), flags


def _collect_ps1_metrics(rel: str, text: str) -> list[FunctionMetric]:
    """Extract function metrics from PowerShell scripts (exempting `[switch]` flags)."""
    cleaned = strip_comments_and_strings(text, ".ps1")
    metrics: list[FunctionMetric] = []
    norm_tok = {"elseif": "else if", "-and": "&&", "-or": "||", "foreach": "for"}
    cursor = 0
    for match in _PS1_FN_RE.finditer(cleaned):
        if match.start() < cursor:
            continue
        brace_o = match.end() - 1
        brace_c = find_matching(cleaned, brace_o, "{", "}")
        if brace_c < 0:
            continue
        body = cleaned[brace_o + 1 : brace_c]
        param_count, bool_flags = _extract_ps1_params(match, body)
        state, nest_stack = _BraceState(), []
        for tm in _PS1_TOKEN_RE.finditer(body):
            _step_brace_token(norm_tok.get(tm.group(0).lower(), tm.group(0).lower()), state, nest_stack)
        metrics.append(FunctionMetric(rel, match.group(1), cleaned.count("\n", 0, match.start()) + 1, cleaned.count("\n", 0, brace_c) + 1, state.score, param_count, bool_flags))
        cursor = brace_c + 1
    return metrics


def _score_sh_body(body: str) -> int:
    """Compute Cognitive Complexity score for one shell function body."""
    score, depth, prev_bool = 0, 0, ""
    for tm in _SH_TOKEN_RE.finditer(body):
        tok = tm.group(0)
        if tok in {"&&", "||"}:
            if tok != prev_bool:
                score, prev_bool = score + 1, tok
            continue
        prev_bool = ""
        if tok in {"elif", "else"}:
            score += 1
        elif tok in {"fi", "done", "esac"}:
            depth = max(0, depth - 1)
        elif tok in {"if", "for", "while", "until", "case"}:
            score, depth = score + 1 + depth, depth + 1
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
        metrics.append(FunctionMetric(rel, name, cleaned.count("\n", 0, match.start()) + 1, cleaned.count("\n", 0, brace_c) + 1, score, 0, ()))
        cursor = brace_c + 1
    return metrics


def _dispatch_metrics(ext: str, rel: str, raw_text: str) -> list[FunctionMetric]:
    """Dispatch per-language metric extraction."""
    if ext == ".py":
        return _collect_python_metrics(rel, raw_text)
    if ext == ".sql":
        return _collect_sql_metrics(rel, raw_text)
    if ext == ".ps1":
        return _collect_ps1_metrics(rel, raw_text)
    if ext in {".sh", ""}:
        return _collect_sh_metrics(rel, raw_text)
    return _collect_brace_metrics(rel, raw_text, ext) if ext in _BRACE_EXTS else []


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
        findings.append(Finding("SLOP-FILE-SIZE", "WARNING", rel, 1, loc, f"File has {loc} lines (advisory threshold {options.warn_file_lines}; non-blocking).", "Verify responsibilities remain a cohesive deep module; do not split into shallow pass-through wrappers."))
    metrics = _dispatch_metrics(path.suffix.lower(), rel, raw_text)
    for m in metrics:
        if changed_lines is not None and not any(ln in changed_lines for ln in range(m.line, m.end_line + 1)):
            continue
        if m.cognitive_complexity > options.max_cognitive_complexity:
            findings.append(Finding("REDLINE-COMPLEXITY", "BLOCKER", rel, m.line, m.line, f"`{m.name}` has Cognitive Complexity {m.cognitive_complexity} (> {options.max_cognitive_complexity}).", "Flatten nesting with guard clauses, extract cohesive helpers, or replace nested SQL with WITH CTEs."))
        if m.positional_params > options.max_positional_params:
            findings.append(Finding("REDLINE-PARAMS", "BLOCKER", rel, m.line, m.line, f"`{m.name}` takes {m.positional_params} positional parameters (> {options.max_positional_params}).", "Group parameters into a named Record/DTO/Options object or dataclass."))
        if m.bool_flag_params:
            findings.append(Finding("REDLINE-BOOL-TRAP", "BLOCKER", rel, m.line, m.line, f"`{m.name}` uses Boolean flag parameter(s): {', '.join(m.bool_flag_params)}.", "Split into separate explicit functions, use an Enum/literal mode, or pass named options."))
    return findings, metrics
