"""Preflight analyzer package combining git scope, parameter checks, clones, and dependency boundaries."""

from __future__ import annotations

from dataclasses import replace

from .clones_slop import detect_ai_slop, detect_duplicates
from .git_scope import collect_source_files, resolve_scope, to_rel_posix
from .graph_boundaries import analyze_graph_and_boundaries
from .models import Finding, ScanOptions, ScanReport
from .params import analyze_file_params

__all__ = ["Finding", "ScanOptions", "ScanReport", "run_scan"]

_SEVERITY_RANK = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}


def run_scan(options: ScanOptions) -> ScanReport:
    """Execute all deterministic preflight checks and return an immutable ScanReport."""
    root = options.root.resolve()
    normalized = replace(options, root=root)
    scope = resolve_scope(normalized)
    if not scope:
        return ScanReport(normalized.scan_mode, 0, ())

    all_files = sorted({*collect_source_files(root), *scope.keys()})
    findings: list[Finding] = []

    for path, changed_lines in sorted(scope.items()):
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = to_rel_posix(path, root)
        findings.extend(analyze_file_params(path, raw_text, rel, normalized, changed_lines=changed_lines))
        findings.extend(detect_ai_slop(path, raw_text, changed_lines, root))

    findings.extend(detect_duplicates(scope, all_files, normalized))
    findings.extend(analyze_graph_and_boundaries(scope, all_files, normalized))

    findings.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.file, f.line, f.rule_id))
    return ScanReport(normalized.scan_mode, len(scope), tuple(findings))
