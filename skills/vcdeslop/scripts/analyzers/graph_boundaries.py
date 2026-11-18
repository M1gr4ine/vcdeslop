"""Dependency graph circularity (Tarjan SCC) and architecture boundary analyzers."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from .git_scope import to_rel_posix
from .lexers import strip_comments_and_strings, strip_comments_only
from .models import Finding, ScanOptions, make_finding

_JS_FROM_RE = re.compile(r"""(?:import|export)\s+[\s\S]{0,400}?\bfrom\s+['"]([^'"]+)['"]""")
_JS_BARE_RE = re.compile(r"""(?:^|\n)\s*import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)""")
_JAVA_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_JAVA_IMP_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE)
_RUST_USE_RE = re.compile(r"^\s*use\s+crate::([\w:]+)", re.MULTILINE)
_SQL_CREATE_VIEW_RE = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_SQL_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)

_PRIVATE_SEGMENTS = frozenset({"internal", "_internal", "private", "impl"})
_PRESENTATION = frozenset({"controller", "controllers", "web", "ui", "pages", "views", "routes", "api", "handler", "handlers"})
_PERSISTENCE = frozenset({"repository", "repositories", "dao", "daos", "mapper", "mappers", "persistence"})
_DOMAIN = frozenset({"domain", "model", "models", "entity", "entities", "core"})
_INFRA = frozenset({"infra", "infrastructure"})
_JS_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js")


@dataclass(frozen=True)
class _ImportRef:
    src: Path
    target: Path | None
    raw_spec: str
    line: int
    eager: bool = True  # False for TYPE_CHECKING / function-local imports: not a load-time cycle edge


@dataclass(frozen=True)
class _RepoIndex:
    root: Path
    file_set: frozenset[Path]
    py_mods: dict[str, Path]
    java_fqns: dict[str, Path]
    sql_views: dict[str, Path]


@dataclass
class _TarjanState:
    counter: int = 0
    indices: dict[Path, int] = field(default_factory=dict)
    lowlink: dict[Path, int] = field(default_factory=dict)
    stack: list[Path] = field(default_factory=list)
    on_stack: set[Path] = field(default_factory=set)
    sccs: list[list[Path]] = field(default_factory=list)


def _index_python_file(root: Path, path: Path, py_mods: dict[str, Path]) -> None:
    """Register module dotted paths relative to root and root/src."""
    for base in (root, root / "src"):
        try:
            rel = path.resolve().relative_to(base.resolve())
        except ValueError:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            py_mods[".".join(parts)] = path.resolve()


def _build_repo_index(root: Path, all_files: list[Path]) -> _RepoIndex:
    """Build cross-file symbol/module indexes for Python, Java, and SQL views."""
    py_mods: dict[str, Path] = {}
    java_fqns: dict[str, Path] = {}
    sql_views: dict[str, Path] = {}
    for path in all_files:
        ext = path.suffix.lower()
        if ext == ".py":
            _index_python_file(root, path, py_mods)
            continue
        if ext not in {".java", ".sql"}:
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ext == ".java" and (m := _JAVA_PKG_RE.search(raw)):
            java_fqns[f"{m.group(1)}.{path.stem}"] = path.resolve()
        elif ext == ".sql":
            for m in _SQL_CREATE_VIEW_RE.finditer(strip_comments_and_strings(raw, ".sql")):
                sql_views[m.group(1).lower()] = path.resolve()
    return _RepoIndex(root, frozenset(p.resolve() for p in all_files), py_mods, java_fqns, sql_views)


def _resolve_js_target(src: Path, spec: str, index: _RepoIndex) -> Path | None:
    """Resolve JS/TS relative or src-aliased import specifier to an indexed Path."""
    if spec.startswith((".", "/")):
        base = (src.parent / spec) if spec.startswith(".") else (index.root / spec.lstrip("/"))
    elif spec.startswith(("@/", "~/")):
        base = index.root / "src" / spec[2:]
    else:
        return None
    for suffix in _JS_SUFFIXES:
        cand = Path(f"{base}{suffix}").resolve()
        if cand in index.file_set:
            return cand
    return None


def _resolve_py_target(src: Path, spec: str, index: _RepoIndex) -> Path | None:
    """Resolve absolute or relative Python import module to an indexed Path."""
    if not spec.startswith("."):
        return index.py_mods.get(spec) or index.py_mods.get(spec.rsplit(".", 1)[0])
    dots = len(spec) - len(spec.lstrip("."))
    tail, anchor = spec.lstrip("."), src.parent
    for _ in range(dots - 1):
        anchor = anchor.parent
    if not tail:
        init_cand = (anchor / "__init__.py").resolve()
        return init_cand if init_cand in index.file_set else None
    rel_parts = tail.split(".")
    for cand in ((anchor / Path(*rel_parts)).with_suffix(".py"), anchor / Path(*rel_parts) / "__init__.py"):
        if cand.resolve() in index.file_set:
            return cand.resolve()
    return None


def _extract_py_from_import(src: Path, node: ast.ImportFrom, index: _RepoIndex) -> list[_ImportRef]:
    """Resolve Python `from X import Y` edges, distinguishing submodules from symbols."""
    base = "." * node.level + (node.module or "")
    refs: list[_ImportRef] = []
    for alias in node.names:
        if alias.name == "*":
            refs.append(_ImportRef(src, _resolve_py_target(src, base, index), base, node.lineno))
            continue
        sub = f"{base}.{alias.name}" if node.module else f"{base}{alias.name}"
        target = _resolve_py_target(src, sub, index)
        if target is not None or alias.name.startswith("_"):
            refs.append(_ImportRef(src, target, sub, node.lineno))
        else:
            refs.append(_ImportRef(src, _resolve_py_target(src, base, index), base, node.lineno))
    return refs


def _is_type_checking(test: ast.expr) -> bool:
    """Recognize conventional typing guards, without treating arbitrary attributes as guards."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name) and test.value.id == "typing")


def _iter_py_import_nodes(tree: ast.Module) -> list[tuple[ast.Import | ast.ImportFrom, bool]]:
    """Mark function bodies and the true branch of typing guards as non-eager."""
    found: list[tuple[ast.Import | ast.ImportFrom, bool]] = []
    stack: list[tuple[ast.AST, bool]] = [(n, True) for n in tree.body]
    while stack:
        node, eager = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.append((node, eager))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend((c, False) for c in ast.iter_child_nodes(node))
        elif isinstance(node, ast.If) and _is_type_checking(node.test):
            stack.extend((c, False) for c in node.body)
            stack.extend((c, eager) for c in node.orelse)
        else:
            stack.extend((c, eager) for c in ast.iter_child_nodes(node))
    return found


def _extract_py_imports(src: Path, raw: str, index: _RepoIndex) -> list[_ImportRef]:
    """Extract Python import edges via AST, tagging lazy imports as non-eager."""
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return []
    refs: list[_ImportRef] = []
    for node, eager in _iter_py_import_nodes(tree):
        if isinstance(node, ast.Import):
            node_refs = [_ImportRef(src, _resolve_py_target(src, a.name, index), a.name, node.lineno) for a in node.names]
        else:
            node_refs = _extract_py_from_import(src, node, index)
        refs.extend(r if eager else replace(r, eager=False) for r in node_refs)
    return refs


def _extract_js_imports(src: Path, cleaned: str, index: _RepoIndex) -> list[_ImportRef]:
    """Extract JS/TS import/export/require edges."""
    refs = [
        _ImportRef(src, _resolve_js_target(src, m.group(1), index), m.group(1), cleaned.count("\n", 0, m.start()) + 1)
        for m in _JS_FROM_RE.finditer(cleaned)
    ]
    for m in _JS_BARE_RE.finditer(cleaned):
        spec = m.group(1) or m.group(2)
        refs.append(_ImportRef(src, _resolve_js_target(src, spec, index), spec, cleaned.count("\n", 0, m.start()) + 1))
    return refs


def _extract_rust_imports(src: Path, cleaned: str, index: _RepoIndex) -> list[_ImportRef]:
    """Extract crate-internal Rust `use crate::...` dependencies."""
    refs: list[_ImportRef] = []
    for m in _RUST_USE_RE.finditer(cleaned):
        parts = m.group(1).split("::")
        target: Path | None = None
        for end in range(len(parts), 0, -1):
            cands = [(b / Path(*parts[:end])).with_suffix(".rs").resolve() for b in (index.root / "src", index.root)]
            if hit := next((c for c in cands if c in index.file_set), None):
                target = hit
                break
        refs.append(_ImportRef(src, target, m.group(1), cleaned.count("\n", 0, m.start()) + 1))
    return refs


def _extract_file_imports(src: Path, index: _RepoIndex) -> list[_ImportRef]:
    """Parse internal imports for a single source file."""
    try:
        raw = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ext = src.suffix.lower()
    if ext == ".py":
        return _extract_py_imports(src, raw, index)
    cleaned = strip_comments_and_strings(raw, ".sql") if ext == ".sql" else strip_comments_only(raw, ext)
    if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        return _extract_js_imports(src, cleaned, index)
    if ext == ".java":
        return [_ImportRef(src, index.java_fqns.get(m.group(1)), m.group(1), cleaned.count("\n", 0, m.start()) + 1) for m in _JAVA_IMP_RE.finditer(cleaned)]
    if ext == ".rs":
        return _extract_rust_imports(src, cleaned, index)
    if ext == ".sql":
        return [_ImportRef(src, t, m.group(1), cleaned.count("\n", 0, m.start()) + 1) for m in _SQL_REF_RE.finditer(cleaned) if (t := index.sql_views.get(m.group(1).lower())) and t != src.resolve()]
    return []


def _pop_scc(root_node: Path, state: _TarjanState) -> None:
    """Pop one strongly connected component from Tarjan stack."""
    comp: list[Path] = []
    while state.stack:
        member = state.stack.pop()
        state.on_stack.discard(member)
        comp.append(member)
        if member == root_node:
            break
    if len(comp) > 1:
        state.sccs.append(comp)


def _tarjan_visit(node: Path, adj: dict[Path, set[Path]], state: _TarjanState) -> None:
    """Recursive Tarjan DFS step with flat complexity."""
    state.indices[node] = state.lowlink[node] = state.counter
    state.counter += 1
    state.stack.append(node)
    state.on_stack.add(node)
    for nbr in sorted(adj.get(node, ())):
        if nbr not in state.indices:
            _tarjan_visit(nbr, adj, state)
            state.lowlink[node] = min(state.lowlink[node], state.lowlink[nbr])
        elif nbr in state.on_stack:
            state.lowlink[node] = min(state.lowlink[node], state.indices[nbr])
    if state.lowlink[node] == state.indices[node]:
        _pop_scc(node, state)


def _classify_layer(parts: tuple[str, ...]) -> str:
    """Return architectural layer tag from normalized path or package segments."""
    lowered = {p.lower() for p in parts}
    if lowered & _PRESENTATION:
        return "presentation"
    if lowered & _PERSISTENCE:
        return "persistence"
    if lowered & _DOMAIN:
        return "domain"
    return "infra" if (lowered & _INFRA) else ""


def _path_segments(path: Path, root: Path) -> tuple[str, ...]:
    """Return path segments relative to root when possible."""
    try:
        return path.resolve().relative_to(root.resolve()).with_suffix("").parts
    except ValueError:
        return path.with_suffix("").parts


def _dir_segments(path: Path, root: Path) -> tuple[str, ...]:
    """Return directory segments relative to root when possible."""
    try:
        rel = path.resolve().parent.relative_to(root.resolve())
        return () if rel == Path(".") else rel.parts
    except ValueError:
        return path.parent.parts


def _spec_segments(ref: _ImportRef, root: Path) -> tuple[str, ...]:
    """Extract normalized module/path segments for a target import."""
    if ref.target is not None:
        return _path_segments(ref.target, root)
    raw = ref.raw_spec.replace("\\", "/")
    if raw.startswith(".") and "/" in raw:
        return _path_segments(ref.src.parent / raw, root)
    if "/" in raw:
        return tuple(p for p in raw.strip("/").split("/") if p not in {".", ".."})
    return tuple(p for p in raw.split(".") if p and p != "*")


def _violates_private_encapsulation(src_dir: tuple[str, ...], dst_segs: tuple[str, ...]) -> bool:
    """Return True if dst_segs enters an internal/private path outside its owning package."""
    src_lower = tuple(s.lower() for s in src_dir)
    dst_lower = tuple(s.lower() for s in dst_segs)
    for idx, seg in enumerate(dst_segs):
        if seg.lower() not in _PRIVATE_SEGMENTS and not (seg.startswith("_") and not seg.startswith("__")):
            continue
        if src_lower != dst_lower[:idx] and src_lower[: idx + 1] != dst_lower[: idx + 1]:
            return True
    return False


def _check_boundary(ref: _ImportRef, root: Path) -> Finding | None:
    """Check one import edge for private-file access or architectural layer violation."""
    rel_src, at = to_rel_posix(ref.src, root), (ref.line, ref.line)
    src_dir, target_segs = _dir_segments(ref.src, root), _spec_segments(ref, root)
    if _violates_private_encapsulation(src_dir, target_segs):
        return make_finding("REDLINE-BOUNDARY", rel_src, at, f"Cross-module import of private/internal module `{ref.raw_spec}`.")

    src_layer, dst_layer = _classify_layer(_path_segments(ref.src, root)), _classify_layer(target_segs)
    if src_layer == "presentation" and dst_layer == "persistence":
        msg = f"Presentation layer imports persistence `{ref.raw_spec}` directly."
        return make_finding("ADVISORY-LAYER", rel_src, at, msg)
    if src_layer == "domain" and dst_layer in {"presentation", "infra"}:
        msg = f"Domain layer imports outer `{dst_layer}` module `{ref.raw_spec}` (invert via a domain port)."
        return make_finding("REDLINE-BOUNDARY", rel_src, at, msg)
    return None


def _collect_file_edges(
    src: Path,
    index: _RepoIndex,
    resolved_scope: dict[Path, set[int] | None],
    adj: dict[Path, set[Path]],
) -> list[Finding]:
    """Populate adjacency edges for src and return scoped boundary violations."""
    findings: list[Finding] = []
    in_scope = src in resolved_scope
    changed = resolved_scope.get(src)
    for ref in _extract_file_imports(src, index):
        if ref.eager and ref.target is not None and ref.target != src:
            adj[src].add(ref.target)
        if not in_scope or (changed is not None and ref.line not in changed):
            continue
        if boundary := _check_boundary(ref, index.root):
            findings.append(boundary)
    return findings


def analyze_graph_and_boundaries(
    scope: dict[Path, set[int] | None],
    all_files: list[Path],
    options: ScanOptions,
) -> list[Finding]:
    """Detect circular module dependencies and cross-layer / private-file imports."""
    index = _build_repo_index(options.root, all_files)
    adj: dict[Path, set[Path]] = defaultdict(set)
    findings: list[Finding] = []
    resolved_scope = {p.resolve(): lines for p, lines in scope.items()}

    for path in all_files:
        findings.extend(_collect_file_edges(path.resolve(), index, resolved_scope, adj))

    state = _TarjanState()
    for node in sorted(index.file_set):
        if node not in state.indices:
            _tarjan_visit(node, adj, state)

    for scc in state.sccs:
        if scoped_nodes := [n for n in scc if n in resolved_scope]:
            chain = " -> ".join(to_rel_posix(n, options.root) for n in sorted(scc))
            anchor = to_rel_posix(sorted(scoped_nodes)[0], options.root)
            findings.append(make_finding("REDLINE-CYCLE", anchor, (1, 1), f"Circular module dependency detected: `{chain} -> ...`."))
    return findings
