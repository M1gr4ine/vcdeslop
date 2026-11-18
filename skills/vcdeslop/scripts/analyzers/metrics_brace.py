"""Cognitive Complexity and parameter metrics for brace languages (Java, JS/TS, Go, Rust) and PowerShell."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lexers import find_matching, split_top_level_params, strip_comments_and_strings
from .models import FunctionMetric

BRACE_EXTS = frozenset({".java", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs"})
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
_BRACE_TOKENS = (
    r"\belse\s+if\b|\belse\b|\bif\b|\bfor\b|\bwhile\b|\bcatch\b|\bswitch\b|\bmatch\b|\bloop\b"
    r"|&&|\|\||\?(?![.:])|[;{}()]"
)
# Lambda bodies nest like Sonar: `=>` (JS/TS) and `->` (Java) make the following `{` a nesting level.
_TOKEN_RE_BY_EXT = {
    **{ext: re.compile(r"=>|" + _BRACE_TOKENS) for ext in _JS_EXTS},
    ".java": re.compile(r"->|" + _BRACE_TOKENS),
}
_DEFAULT_TOKEN_RE = re.compile(_BRACE_TOKENS)
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
_PS1_TOKEN_RE = re.compile(r"(?i)(?<!-)\b(?:elseif|else|if|foreach|for|while|switch|catch)\b(?!-)|-and\b|-or\b|[;{}()]")
_PS1_BOOL_RE = re.compile(r"(?i)\[bool\]|=\s*\$(?:true|false)\b")
_PS1_TOKEN_ALIASES = {"elseif": "else if", "-and": "&&", "-or": "||", "foreach": "for"}


@dataclass
class _BraceState:
    score: int = 0
    depth: int = 0
    pending_nest: bool = False
    prev_bool: str = ""
    paren_depth: int = 0


def is_trivial_setter(name: str, param_count: int) -> bool:
    """Allow single-argument state setters like setVisible(true)."""
    return param_count == 1 and name.startswith("set") and len(name) > 3 and (name[3] == "_" or name[3].isupper())


def line_of(text: str, index: int) -> int:
    """Return the 1-based line number of a character offset."""
    return text.count("\n", 0, index) + 1


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
    elif tok in {"=>", "->"}:
        state.pending_nest = True
    elif tok == "{":
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


def _score_tokens(tokens: list[str]) -> int:
    """Run the brace-language state machine over normalized tokens."""
    state, nest_stack = _BraceState(), []
    for tok in tokens:
        _step_brace_token(tok, state, nest_stack)
    return state.score


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


def collect_brace_metrics(rel: str, text: str, ext: str) -> list[FunctionMetric]:
    """Extract function metrics from Java, JS/TS, Go, or Rust source text."""
    cleaned = strip_comments_and_strings(text, ext)
    token_re = _TOKEN_RE_BY_EXT.get(ext, _DEFAULT_TOKEN_RE)
    metrics: list[FunctionMetric] = []
    cursor = 0
    for match in _BRACE_FN_RE.finditer(cleaned):
        span = _extract_fn_span(cleaned, match, cursor)
        if span is None:
            continue
        name, open_p, close_p, brace_o, brace_c = span
        params = split_top_level_params(cleaned[open_p + 1 : close_p])
        trivial = is_trivial_setter(name, len(params))
        flags = () if trivial else tuple(p for p in params if _is_bool_trap_param(p, ext))
        score = _score_tokens([m.group(0) for m in token_re.finditer(cleaned, brace_o + 1, brace_c)])
        start, end = line_of(cleaned, match.start()), line_of(cleaned, brace_c)
        metrics.append(FunctionMetric(rel, name, start, end, score, len(params), flags))
        cursor = brace_c + 1
    return metrics


def _extract_ps1_params(match: re.Match[str], body: str) -> tuple[int, tuple[str, ...]]:
    """Extract PowerShell parameter count and scalar Boolean Traps (exempting `[switch]`)."""
    raw_params = match.group(2) or ""
    if not raw_params and (pm := _PS1_PARAM_RE.match(body)):
        p_close = find_matching(body, pm.end() - 1, "(", ")")
        raw_params = body[pm.end() : p_close] if p_close >= 0 else ""
    params = split_top_level_params(raw_params)
    if is_trivial_setter(match.group(1), len(params)):
        return len(params), ()
    flags = tuple(
        p for p in params
        if "[switch]" not in p.lower() and "[bool[]]" not in p.lower() and _PS1_BOOL_RE.search(p)
    )
    return len(params), flags


def collect_ps1_metrics(rel: str, text: str) -> list[FunctionMetric]:
    """Extract function metrics from PowerShell scripts (exempting `[switch]` flags)."""
    cleaned = strip_comments_and_strings(text, ".ps1")
    metrics: list[FunctionMetric] = []
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
        score = _score_tokens([_PS1_TOKEN_ALIASES.get(t.lower(), t.lower()) for t in _PS1_TOKEN_RE.findall(body)])
        start, end = line_of(cleaned, match.start()), line_of(cleaned, brace_c)
        metrics.append(FunctionMetric(rel, match.group(1), start, end, score, param_count, bool_flags))
        cursor = brace_c + 1
    return metrics
