"""Function parameter-count and Boolean-trap analysis across Python, brace languages, and PowerShell."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .lexers import find_matching, split_top_level_params, strip_comments_and_strings
from .models import Finding, ScanOptions, make_finding

_BRACE_EXTS = frozenset({".java", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs"})
_JS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_CONTROL_WORDS = frozenset(
    {
        "if", "for", "while", "switch", "catch", "else", "try", "return", "new", "throw",
        "typeof", "await", "sizeof", "match", "select", "when", "case", "default",
    }
)

_BRACE_FN_RE = re.compile(
    r"""(?x)
    \b(?:function|func|fn)\s+(?:\([^)]*\)\s*)?([A-Za-z_$][\w$]*)\s*(?:<[^>{}()\n]*>)?\s*\(
    | \b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\(
    | (?:^|\n)[ \t]*(?:(?:public|protected|private|static|final|synchronized|abstract|default|native|async|override|readonly)\s+)*
      (?:<[^;{}\n]+>\s+)?(?:[\w.$<>\[\]?|&\s]+\s+)?([A-Za-z_$][\w$]*)\s*\(
    """
)
_BOOL_RE_MAP = {
    ".java": re.compile(r"\b(?:boolean|Boolean)\b"),
    ".go": re.compile(r"\bbool\b"),
    ".rs": re.compile(r"\bbool\b"),
}
_JS_BOOL_RE = re.compile(
    r"^\s*(?:[A-Za-z_$][\w$]*\s+)*[A-Za-z_$][\w$]*\??\s*"
    r"(?::\s*boolean\b(?!\s*\[)|=\s*(?:true|false)\b)"
)

_PS1_FN_RE = re.compile(r"(?im)^\s*function\s+([A-Za-z_][\w-]*)\s*(?:\(([^)]*)\))?\s*\{")
_PS1_PARAM_RE = re.compile(r"(?is)^\s*(?:\[CmdletBinding\([^\)]*\)\]\s*)?param\s*\(")
_PS1_BOOL_RE = re.compile(r"(?i)\[bool\]|=\s*\$(?:true|false)\b")


@dataclass(frozen=True)
class _FunctionParams:
    """Extracted function span, positional parameter count, and Boolean flag parameters."""

    name: str
    line: int
    end_line: int
    positional_params: int
    bool_flag_params: tuple[str, ...] = ()


def _is_trivial_setter(name: str, param_count: int) -> bool:
    """Allow single-argument state setters like setVisible(true)."""
    return param_count == 1 and name.startswith("set") and len(name) > 3 and (name[3] == "_" or name[3].isupper())


def _line_of(text: str, index: int) -> int:
    """Return the 1-based line number of a character offset."""
    return text.count("\n", 0, index) + 1


def _match_start_line(text: str, match: re.Match[str]) -> int:
    """Return the 1-based start line of a match, skipping any consumed leading newlines/spaces."""
    raw = match.group(0)
    return _line_of(text, match.start() + len(raw) - len(raw.lstrip()))


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


def _py_function_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _FunctionParams:
    """Extract parameter metrics from one Python function (keyword-only args are exempt)."""
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
    end_line = getattr(node, "end_lineno", None) or node.lineno
    return _FunctionParams(node.name, node.lineno, end_line, len(pos), flags)


def _collect_python_params(text: str) -> list[_FunctionParams]:
    """Extract function parameter metrics from the Python AST."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [
        _py_function_params(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


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
    """Return True for a scalar Boolean flag; collections, option objects, and callback types are exempt."""
    if ext in _JS_EXTS:
        return bool(_JS_BOOL_RE.search(param))
    if "{" in param or "<" in param:
        return False
    if ext == ".go" and re.search(r"\bfunc\s*\(", param):
        return False
    if ext in _BOOL_RE_MAP:
        return "[" not in param and bool(_BOOL_RE_MAP[ext].search(param))
    return False


def _extract_fn_span(cleaned: str, match: re.Match[str], cursor: int) -> tuple[str, int, int, int] | None:
    """Validate and extract (name, open_paren, close_paren, brace_close)."""
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
    return (name, open_p, close_p, brace_c) if brace_c >= 0 else None


def _collect_brace_params(text: str, ext: str) -> list[_FunctionParams]:
    """Extract parameter metrics from Java, JS/TS, Go, or Rust source text."""
    cleaned = strip_comments_and_strings(text, ext)
    signatures: list[_FunctionParams] = []
    cursor = 0
    for match in _BRACE_FN_RE.finditer(cleaned):
        span = _extract_fn_span(cleaned, match, cursor)
        if span is None:
            continue
        name, open_p, close_p, brace_c = span
        params = split_top_level_params(cleaned[open_p + 1 : close_p])
        trivial = _is_trivial_setter(name, len(params))
        flags = () if trivial else tuple(p for p in params if _is_bool_trap_param(p, ext))
        start, end = _match_start_line(cleaned, match), _line_of(cleaned, brace_c)
        signatures.append(_FunctionParams(name, start, end, len(params), flags))
        cursor = brace_c + 1
    return signatures


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


def _collect_ps1_params(text: str) -> list[_FunctionParams]:
    """Extract parameter metrics from PowerShell scripts (exempting `[switch]` flags)."""
    cleaned = strip_comments_and_strings(text, ".ps1")
    signatures: list[_FunctionParams] = []
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
        start, end = _match_start_line(cleaned, match), _line_of(cleaned, brace_c)
        signatures.append(_FunctionParams(match.group(1), start, end, param_count, bool_flags))
        cursor = brace_c + 1
    return signatures


def _dispatch_params(ext: str, raw_text: str) -> list[_FunctionParams]:
    """Dispatch per-language function signature extraction."""
    if ext == ".py":
        return _collect_python_params(raw_text)
    if ext == ".ps1":
        return _collect_ps1_params(raw_text)
    return _collect_brace_params(raw_text, ext) if ext in _BRACE_EXTS else []


def _param_findings(rel: str, sig: _FunctionParams, options: ScanOptions) -> list[Finding]:
    """Translate one function's parameter signature into redline findings."""
    at = (sig.line, sig.line)
    found: list[Finding] = []
    if sig.positional_params > options.max_positional_params:
        msg = f"`{sig.name}` takes {sig.positional_params} positional parameters (> {options.max_positional_params})."
        found.append(make_finding("REDLINE-PARAMS", rel, at, msg))
    if sig.bool_flag_params:
        msg = f"`{sig.name}` uses Boolean flag parameter(s): {', '.join(sig.bool_flag_params)}."
        found.append(make_finding("REDLINE-BOOL-TRAP", rel, at, msg))
    return found


def analyze_file_params(
    path: Path,
    raw_text: str,
    rel: str,
    options: ScanOptions,
    *,
    changed_lines: set[int] | None = None,
) -> list[Finding]:
    """Analyze parameter counts and Boolean Traps for scoped functions."""
    findings: list[Finding] = []
    for sig in _dispatch_params(path.suffix.lower(), raw_text):
        if changed_lines is None or any(ln in changed_lines for ln in range(sig.line, sig.end_line + 1)):
            findings.extend(_param_findings(rel, sig, options))
    return findings
