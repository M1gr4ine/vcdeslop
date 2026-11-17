"""Preflight analyzer package combining git scope, metrics, clones, and dependency boundaries."""

from __future__ import annotations

from dataclasses import replace

from .clones_slop import detect_ai_slop, detect_duplicates
from .complexity_params import analyze_file_complexity
from .git_scope import collect_source_files, resolve_scope, to_rel_posix
from .graph_boundaries import analyze_graph_and_boundaries
from .models import Finding, FunctionMetric, ScanOptions, ScanReport

__all__ = ["Finding", "FunctionMetric", "ScanOptions", "ScanReport", "run_scan"]

_SEVERITY_RANK = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}


def run_scan(options: ScanOptions) -> ScanReport:
    """Execute all deterministic preflight checks and return an immutable ScanReport."""
    root = options.root.resolve()
    normalized = replace(options, root=root)
    scope = resolve_scope(normalized)
    if not scope:
        return ScanReport(normalized.scan_mode, 0, (), ())

    all_files = sorted({*collect_source_files(root), *scope.keys()})
    findings: list[Finding] = []
    metrics: list[FunctionMetric] = []

    for path, changed_lines in sorted(scope.items()):
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = to_rel_posix(path, root)
        file_findings, file_metrics = analyze_file_complexity(path, raw_text, rel, normalized, changed_lines=changed_lines)
        findings.extend(file_findings)
        metrics.extend(file_metrics)
        findings.extend(detect_ai_slop(path, raw_text, changed_lines, root))

    findings.extend(detect_duplicates(scope, all_files, normalized))
    findings.extend(analyze_graph_and_boundaries(scope, all_files, normalized))

    findings.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.file, f.line, f.rule_id))
    metrics.sort(key=lambda m: (-m.cognitive_complexity, -m.positional_params, m.file, m.line))
    return ScanReport(normalized.scan_mode, len(scope), tuple(findings), tuple(metrics[:15]))
