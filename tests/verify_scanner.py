"""Verification suite for vcdeslop preflight scanner, language fixtures, and self-dogfooding."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "vcdeslop" / "scripts"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from analyzers import Finding, ScanOptions, run_scan  # noqa: E402
from preflight_scan import main as preflight_main  # noqa: E402
from tests.test_hooks_and_git import run_integration_tests  # noqa: E402


def _scan_temp_files(files: dict[str, str]) -> tuple[Finding, ...]:
    """Write fixture files to an isolated temp directory and run preflight scan."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return run_scan(ScanOptions(root=root, scan_mode="all")).findings


def _assert_rules(
    findings: tuple[Finding, ...],
    expected: tuple[tuple[str, str, str], ...],
    forbidden: tuple[tuple[str, str], ...],
) -> None:
    """Assert every expected rule/file/message match exists and no forbidden rule triggers."""
    for rule_id, file_sub, msg_sub in expected:
        if not any(f.rule_id == rule_id and file_sub in f.file and msg_sub in f.message for f in findings):
            raise AssertionError(f"Expected {rule_id} ({msg_sub}) in '{file_sub}', got: {findings}")
    for rule_id, msg_sub in forbidden:
        for f in findings:
            if f.rule_id == rule_id and msg_sub in f.message:
                raise AssertionError(f"Unexpected false positive {rule_id} ({msg_sub}): {f}")


def test_java_fixtures() -> None:
    """Verify Java Boolean Trap, >4 params, cross-layer boundary, type evasion, and exemptions."""
    mapper_src = """package com.acme.order.mapper;
public class OrderMapper {
  public void save() {}
}
"""
    ctrl_src = """package com.acme.order.controller;
import com.acme.order.mapper.OrderMapper;
public class OrderController {
  public void setVisible(boolean visible) {}
  public void processFlags(java.util.List<Boolean> flags) {}
  @SuppressWarnings("unchecked")
  public void deleteOrder(Long id, boolean isHardDelete) {
    try { new OrderMapper().save(); } catch (Exception e) {}
  }
  public void createInvoice(int a, int b, int c, int d, int e) {}
}
"""
    safe_src = """package com.acme.order.service;
public class OrderSafe {
  // @SuppressWarnings("unchecked")
  public void ok() {}
}
"""
    findings = _scan_temp_files(
        {
            "src/main/java/com/acme/order/mapper/OrderMapper.java": mapper_src,
            "src/main/java/com/acme/order/controller/OrderController.java": ctrl_src,
            "src/main/java/com/acme/order/service/OrderSafe.java": safe_src,
        }
    )
    _assert_rules(
        findings,
        (
            ("REDLINE-BOUNDARY", "OrderController.java", ""),
            ("REDLINE-BOOL-TRAP", "OrderController.java", ""),
            ("REDLINE-PARAMS", "OrderController.java", ""),
            ("SLOP-SILENT-CATCH", "OrderController.java", ""),
            ("SLOP-TYPE-EVASION", "OrderController.java", ""),
        ),
        (("REDLINE-BOOL-TRAP", "setVisible"), ("REDLINE-BOOL-TRAP", "processFlags")),
    )
    assert not any(f.file.endswith("OrderSafe.java") for f in findings)


def test_ts_fixtures() -> None:
    """Verify TS complexity > 15, cycles, private import, `as any`, and intra-module exemptions."""
    billing_index = """import { SECRET } from './internal/secret';
export function readFlags(flags: boolean[]): boolean[] { return flags; }
export const PUBLIC_SECRET = SECRET;
"""
    mod_a = """import { beta } from './modB';
import { SECRET } from '../billing/internal/secret';
export const alpha = 1;
export function sendSafe(msg: string, opts: { urgent?: boolean }): string { return msg; }
export function castIt(v: unknown): string { return (v as any).trim(); }
export function handle(a: number, b: number, c: number, d: number): number {
  if (a > 0 && b > 0) {
    for (let i = 0; i < a; i++) {
      if (i > b) {
        while (c > 0) {
          if (d > 1 || d < -1) {
            return 1;
          }
        }
      }
    }
  }
  return 0;
}
"""
    findings = _scan_temp_files(
        {
            "src/billing/internal/secret.ts": "export const SECRET = 1;\n",
            "src/billing/index.ts": billing_index,
            "src/orders/modB.ts": "import { alpha } from './modA';\nexport const beta = alpha;\n",
            "src/orders/modA.ts": mod_a,
        }
    )
    _assert_rules(
        findings,
        (
            ("REDLINE-COMPLEXITY", "modA.ts", ""),
            ("REDLINE-CYCLE", "mod", ""),
            ("REDLINE-BOUNDARY", "modA.ts", ""),
            ("SLOP-TYPE-EVASION", "modA.ts", ""),
        ),
        (("REDLINE-BOOL-TRAP", "sendSafe"),),
    )
    assert not any(f.file.endswith("billing/index.ts") for f in findings)


def test_sql_fixtures() -> None:
    """Verify SQL complexity, all 3 SQL smells, view cycles, and comment/RHS exemptions."""
    bad_sql = """SELECT DISTINCT * FROM orders o JOIN users u ON u.id = o.user_id
WHERE DATE(o.created_at) = '2026-01-01' AND o.tenant = ${tenantId}
  AND o.id IN (SELECT id FROM items WHERE qty > 0 AND (flag = 1 OR flag = 2)
    AND status IN (SELECT code FROM states WHERE CASE WHEN active = 1 THEN
      CASE WHEN tier = 2 THEN 1 ELSE 0 END WHEN active = 2 THEN 2 ELSE 0 END = 1
      AND region_id IN (SELECT region_id FROM regions WHERE deleted = 0)));
"""
    clean_sql = "-- safe comment ${tenantId}\nSELECT id, email FROM users WHERE email = LOWER('A@B.COM');\n"
    findings = _scan_temp_files(
        {
            "queries/bad.sql": bad_sql,
            "queries/clean.sql": clean_sql,
            "queries/v_a.sql": "CREATE VIEW v_a AS SELECT id FROM v_b;\n",
            "queries/v_b.sql": "CREATE VIEW v_b AS SELECT id FROM v_a;\n",
        }
    )
    _assert_rules(
        findings,
        (
            ("SLOP-SQL-INJECTION", "bad.sql", ""),
            ("SLOP-SQL-SMELL", "bad.sql", "SELECT *"),
            ("SLOP-SQL-SMELL", "bad.sql", "SELECT DISTINCT"),
            ("SLOP-SQL-SMELL", "bad.sql", "Function wrapped"),
            ("REDLINE-COMPLEXITY", "bad.sql", ""),
            ("REDLINE-CYCLE", "queries/v_", ""),
        ),
        (),
    )
    assert not any(f.file.endswith("clean.sql") for f in findings)


def test_python_and_shell_rules() -> None:
    """Verify Python AST complexity boundary (15 vs 16), cycles, boundaries, slop, and PowerShell metrics."""
    blocks_15 = "".join(f"    if a > {i}:\n        if b > {i}:\n            return {i + 1}\n" for i in range(5))
    blocks_16 = "".join(f"    for x_{i} in range(a):\n        if x_{i} > b:\n            return {i + 1}\n" for i in range(5))
    dup_body = "\n".join(f"    step_{i} = items[{i}] + {i + 1}" for i in range(12))
    ps1_complex = """function Invoke-Deep {
  param([switch]$DryRun)
  if ($DryRun) { if ($true) { if ($true) { if ($true) { if ($true) { if ($true) { return } } } } } }
}
"""
    worker_b = f"""def run_b(items):
{dup_body}
    return step_11
def sync_bad(src: str, dry_run: bool | None = None) -> None:
    x = 1  # type: ignore
    try:
        print(src, dry_run)
    except ValueError:
        pass
def sync_good(src: str, flags: list[bool], *, dry_run: bool = False) -> None:
    y = 1  # type: ignore[arg-type]
    print(src, flags, dry_run, y)
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        files = {
            "pkg_a/mod_a.py": (
                "from pkg_b import mod_b\nfrom pkg_b import _secret\n\n"
                f"def score_15(a: int, b: int) -> int:\n{blocks_15}    return 0\n\n"
                f"def score_16(a: int, b: int, c: int) -> int:\n{blocks_16}    if c:\n        return 9\n    return 0\n"
            ),
            "pkg_b/mod_b.py": "from pkg_a import mod_a\n",
            "pkg_b/_secret.py": "TOKEN = 'x'\n",
            "pkg_dup/worker_a.py": f"def run_a(items):\n{dup_body}\n    return step_11\n",
            "pkg_dup/worker_b.py": worker_b,
            "pkg_dup/giant.py": "\n".join(f"VALUE_{i} = {i}" for i in range(410)) + "\n",
            "tools/deep.ps1": ps1_complex,
        }
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        findings = run_scan(ScanOptions(root=root, scan_mode="all")).findings
        _assert_rules(
            findings,
            (
                ("REDLINE-COMPLEXITY", "mod_a.py", "score_16"),
                ("REDLINE-CYCLE", "pkg_", ""),
                ("REDLINE-BOUNDARY", "mod_a.py", ""),
                ("REDLINE-DUPLICATION", "worker_", ""),
                ("REDLINE-BOOL-TRAP", "worker_b.py", "dry_run"),
                ("SLOP-SILENT-CATCH", "worker_b.py", ""),
                ("SLOP-TYPE-EVASION", "worker_b.py", ""),
                ("REDLINE-COMPLEXITY", "deep.ps1", "Invoke-Deep"),
            ),
            (("REDLINE-COMPLEXITY", "score_15"), ("REDLINE-BOOL-TRAP", "sync_good"), ("REDLINE-BOOL-TRAP", "Invoke-Deep")),
        )
        assert sum(1 for f in findings if f.rule_id == "SLOP-TYPE-EVASION" and "worker_b.py" in f.file) == 1

        giant_findings = [f for f in findings if "giant.py" in f.file]
        assert len(giant_findings) == 1 and giant_findings[0].severity == "WARNING"
        with contextlib.redirect_stdout(io.StringIO()):
            assert preflight_main(["--path", str(root), "--files", "pkg_dup/giant.py", "--gate", "strict"]) == 0


def test_self_dogfooding() -> None:
    """Assert the entire deslop repository passes its own preflight redlines with 0 findings."""
    report = run_scan(ScanOptions(root=REPO_ROOT, scan_mode="all"))
    if report.findings:
        details = "\n".join(f"  - [{f.severity}] {f.rule_id} {f.file}:{f.line} {f.message}" for f in report.findings)
        raise AssertionError(f"Self-dogfooding check failed with findings:\n{details}")


def main() -> int:
    """Run all scanner test suites."""
    test_java_fixtures()
    test_ts_fixtures()
    test_sql_fixtures()
    test_python_and_shell_rules()
    run_integration_tests()
    test_self_dogfooding()
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
