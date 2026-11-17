"""Deterministic polyglot preflight scanner CLI for the cross-agent vcdeslop skill."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from analyzers import Finding, ScanOptions, ScanReport, run_scan


def _build_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(description="Run deterministic deslop & maintainability preflight checks.")
    parser.add_argument("--path", default=".", help="Repository or target root directory.")
    parser.add_argument("--base", default=None, help="Git base ref for diff mode (e.g., main, HEAD~1).")
    parser.add_argument("--mode", choices=("diff", "staged", "all"), default="diff", help="Scope resolution mode.")
    parser.add_argument("--files", nargs="*", default=(), help="Explicit file paths to scan.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Report output format.")
    parser.add_argument("--gate", choices=("advisory", "strict"), default="advisory", help="Exit non-zero on BLOCKER findings when strict.")
    return parser


def _count_rules(findings: tuple[Finding, ...], rule_ids: tuple[str, ...]) -> int:
    """Count findings whose rule_id belongs to rule_ids."""
    return sum(1 for f in findings if f.rule_id in rule_ids)


def _summarize_verdict(findings: tuple[Finding, ...]) -> tuple[str, int, int]:
    """Return (verdict, blocker_count, warning_count) for report findings."""
    blockers = sum(1 for f in findings if f.severity == "BLOCKER")
    warnings = sum(1 for f in findings if f.severity == "WARNING")
    if blockers > 0:
        return "BLOCK", blockers, warnings
    if warnings > 0:
        return "CONCERNS", blockers, warnings
    return "PASS", blockers, warnings


def _slop_gate_label(slop: list[Finding]) -> str:
    """Return scorecard gate label for SLOP-* findings."""
    if any(f.severity == "BLOCKER" for f in slop):
        return "FAIL"
    if slop:
        return "WARN"
    return "CLEAN"


def render_markdown(report: ScanReport) -> str:
    """Format ScanReport as a compact Markdown scorecard and findings list."""
    verdict, blockers, warnings = _summarize_verdict(report.findings)
    c1 = _count_rules(report.findings, ("REDLINE-COMPLEXITY",))
    c2 = _count_rules(report.findings, ("REDLINE-BOOL-TRAP", "REDLINE-PARAMS"))
    c3 = _count_rules(report.findings, ("REDLINE-DUPLICATION",))
    c4 = _count_rules(report.findings, ("REDLINE-CYCLE", "REDLINE-BOUNDARY"))
    slop = [f for f in report.findings if f.rule_id.startswith("SLOP-")]
    slop_gate = _slop_gate_label(slop)

    lines = [
        "# VCDeslop Preflight Report",
        "",
        f"- **Verdict**: `{verdict}` | **Mode**: `{report.mode}` | **Scoped Files**: {report.scanned_files} | **Blockers**: {blockers} | **Warnings**: {warnings}",
        "",
        "## 4 Hard Maintainability Redlines Scorecard",
        "| Redline | Gate | Findings |",
        "| :--- | :--- | :---: |",
        f"| 1. Single-Function Cognitive Complexity <= 15 | {'FAIL' if c1 else 'PASS'} | {c1} |",
        f"| 2. Zero Boolean Traps & Positional Params <= 4 | {'FAIL' if c2 else 'PASS'} | {c2} |",
        f"| 3. Newly Added Duplicate Code Blocks = 0 | {'FAIL' if c3 else 'PASS'} | {c3} |",
        f"| 4. Circular Deps = 0 & No Cross-Layer/Private Imports | {'FAIL' if c4 else 'PASS'} | {c4} |",
        f"| + AI Slop / SQL Smells / Advisory Warnings | {slop_gate} | {len(slop)} |",
        "",
    ]

    if report.findings:
        lines.append("## Findings")
        for f in report.findings:
            loc = f"{f.file}:{f.line}" if f.line == f.end_line else f"{f.file}:{f.line}-{f.end_line}"
            lines.append(f"- **[{f.severity}] `{f.rule_id}`** `{loc}` — {f.message} *Fix:* {f.remedy}")
        lines.append("")

    if report.hot_functions:
        lines.extend(
            [
                "## Top Functions by Cognitive Complexity",
                "| Function | Location | Cognitive Complexity | Positional Params |",
                "| :--- | :--- | :---: | :---: |",
            ]
        )
        for m in report.hot_functions[:10]:
            lines.append(f"| `{m.name}` | `{m.file}:{m.line}` | {m.cognitive_complexity} | {m.positional_params} |")
        lines.append("")

    return "\n".join(lines)


def render_json(report: ScanReport) -> str:
    """Serialize ScanReport to JSON."""
    verdict, blockers, warnings = _summarize_verdict(report.findings)
    payload = {
        "verdict": verdict,
        "mode": report.mode,
        "scanned_files": report.scanned_files,
        "blockers": blockers,
        "warnings": warnings,
        "findings": [asdict(f) for f in report.findings],
        "hot_functions": [asdict(m) for m in report.hot_functions],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = _build_parser().parse_args(argv)
    options = ScanOptions(
        root=Path(args.path),
        base_ref=args.base,
        scan_mode=args.mode,
        explicit_files=tuple(Path(p) for p in args.files),
    )
    report = run_scan(options)
    output = render_json(report) if args.format == "json" else render_markdown(report)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(output)
    has_blocker = any(f.severity == "BLOCKER" for f in report.findings)
    return 1 if (args.gate == "strict" and has_blocker) else 0


if __name__ == "__main__":
    raise SystemExit(main())
