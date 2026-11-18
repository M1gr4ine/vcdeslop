"""Polyglot comment/string masking and balanced-delimiter lexical helpers."""

from __future__ import annotations

import re

_BRACE_NOISE_RE = re.compile(
    r'/\*[\s\S]*?\*/|//[^\n]*|"""[\s\S]*?"""'
    r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\[\s\S]|[^`\\])*`'
)
_SQL_NOISE_RE = re.compile(r"/\*[\s\S]*?\*/|--[^\n]*|'(?:''|[^'])*'")
_PY_NOISE_RE = re.compile(r'#[^\n]*|"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
_SH_NOISE_RE = re.compile(r"#[^\n]*|'(?:[^'])*'|\"(?:\\.|[^\"\\])*\"")
_PS1_NOISE_RE = re.compile(r"<#[\s\S]*?#>|#[^\n]*|'(?:''|[^'])*'|\"(?:`[\s\S]|[^\"`])*\"")
_NOISE_MAP = {".sql": _SQL_NOISE_RE, ".py": _PY_NOISE_RE, ".sh": _SH_NOISE_RE, "": _SH_NOISE_RE, ".ps1": _PS1_NOISE_RE}


def _mask_tokens(text: str, ext: str, mode: str) -> str:
    """Mask comments and/or string literals preserving line numbers."""
    regex = _NOISE_MAP.get(ext, _BRACE_NOISE_RE)

    def _mask(match: re.Match[str]) -> str:
        tok = match.group(0)
        is_comment = tok.startswith(("/*", "//", "--", "#", "<#"))
        if (mode == "comments" and not is_comment) or (mode == "strings" and is_comment):
            return tok
        nl = "\n" * tok.count("\n")
        if is_comment:
            return nl
        return f"{tok[0]}{nl}{tok[0]}" if tok[0] in ("'", '"', "`") else nl

    return regex.sub(_mask, text)


def strip_comments_and_strings(text: str, ext: str) -> str:
    """Replace comments with newlines and strings with empty quotes preserving lines."""
    return _mask_tokens(text, ext, "both")


def strip_comments_only(text: str, ext: str) -> str:
    """Replace comments with newlines while keeping string literals intact."""
    return _mask_tokens(text, ext, "comments")


def strip_strings_only(text: str, ext: str) -> str:
    """Replace string literals with empty quotes while keeping comments intact."""
    return _mask_tokens(text, ext, "strings")


def find_matching(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    """Find matching closing delimiter index, or -1 if unbalanced."""
    depth = 0
    for idx in range(open_idx, len(text)):
        if text[idx] == open_ch:
            depth += 1
        elif text[idx] == close_ch:
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _is_generic_call(raw: str, index: int) -> bool:
    """Recognize common generic calls in default expressions, such as factory<A, B>()."""
    return bool(re.match(r"<[\w\s,.$<>\[\]|&?]+>\s*\(", raw[index:]))


def split_top_level_params(raw_params: str) -> list[str]:
    """Split comma-separated parameter list respecting nested delimiters."""
    if not raw_params.strip():
        return []
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    in_default = False
    for idx, ch in enumerate(raw_params):
        if ch == "=" and not stack and raw_params[idx:idx + 2] != "=>":
            in_default = True
        elif ch in "([{" or (ch == "<" and (not in_default or _is_generic_call(raw_params, idx))):
            stack.append(ch)
        elif stack and ch in ")]}>" and { ")": "(", "]": "[", "}": "{", ">": "<" }[ch] == stack[-1]:
            stack.pop()
        elif ch == "," and not stack:
            parts.append(raw_params[start:idx].strip())
            start = idx + 1
            in_default = False
    if tail := raw_params[start:].strip():
        parts.append(tail)
    return [p for p in parts if p not in {"self", "&self", "&mut self"} and not p.startswith("this:")]
