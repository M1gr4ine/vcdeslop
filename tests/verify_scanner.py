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
from analyzers.clones_slop import detect_ai_slop  # noqa: E402
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
            ("ADVISORY-LAYER", "OrderController.java", ""),
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
    sql_smells = {f.message: f.remedy for f in findings if f.rule_id == "SLOP-SQL-SMELL"}
    assert any("column names" in remedy for remedy in sql_smells.values())
    assert any("cardinality" in remedy for remedy in sql_smells.values())
    assert any("range predicate" in remedy for remedy in sql_smells.values())
    assert all(f.severity == "WARNING" for f in findings if f.rule_id == "SLOP-SQL-SMELL")
    assert all(f.severity == "BLOCKER" for f in findings if f.rule_id == "SLOP-SQL-INJECTION")


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


def test_accuracy_regressions() -> None:
    """Lock callback-type bool exemptions, lazy-import cycles, lambda nesting, and AST silent-catch detection."""
    deep_ts = (
        "export function deep(xs: number[][]): number {\n  let n = 0;\n  xs.forEach((row) => {\n"
        "    row.forEach((v) => {\n      if (v > 0) {\n        if (v > 1) {\n          if (v > 2) {\n"
        "            if (v > 3) { n++; }\n          }\n        }\n      }\n    });\n  });\n  return n;\n}\n"
    )
    findings = _scan_temp_files(
        {
            "web/callback.ts": "export function onDone(cb: (ok: boolean) => void): void { cb(true); }\n",
            "web/flag.ts": "export function render(id: string, compact: boolean): string { return id; }\n",
            "web/deep.ts": deep_ts,
            "svc/run.go": "package svc\nfunc Run(fn func(bool) error) error { return fn(true) }\n",
            "tc/__init__.py": "",
            "tc/a.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from tc.b import B\n",
            "tc/b.py": "from tc.a import TYPE_CHECKING\nclass B: ...\n",
            "lazy/__init__.py": "",
            "lazy/a.py": "def f():\n    from lazy.b import g\n    return g()\n",
            "lazy/b.py": "from lazy.a import f\ndef g():\n    return 1\n",
            "eager/__init__.py": "",
            "eager/a.py": "from eager.b import g\n",
            "eager/b.py": "from eager.a import g as h\ng = 1\n",
            "errs/io.py": (
                "import os\ndef load(paths):\n    out = []\n    for p in paths:\n        try:\n            out.append(open(p).read())\n"
                "        except OSError:\n            continue\n    return out\n"
                "def risky(p):\n    try:\n        return open(p).read()\n    except Exception as e:\n        print(e)\n        return None\n"
            ),
        }
    )
    _assert_rules(
        findings,
        (
            ("REDLINE-BOOL-TRAP", "web/flag.ts", "compact"),
            ("REDLINE-COMPLEXITY", "web/deep.ts", "deep"),
            ("REDLINE-CYCLE", "eager/", ""),
            ("SLOP-SILENT-CATCH", "errs/io.py", ""),
        ),
        (("REDLINE-BOOL-TRAP", "onDone"), ("REDLINE-BOOL-TRAP", "Run"), ("REDLINE-CYCLE", "tc/"), ("REDLINE-CYCLE", "lazy/")),
    )
    silent = [f for f in findings if f.rule_id == "SLOP-SILENT-CATCH"]
    assert [f.line for f in silent] == [13], f"Only the broad log-and-return handler should fire, got {silent}"


def test_scanner_scope_edges() -> None:
    """Check eager else branches, callback defaults, and independent boundary severities."""
    findings = _scan_temp_files({
        "web/flags.ts": "export function render(id: string, compact: boolean = Boolean(id)) {}\n"
                        "export function onDone(cb: (ok: boolean) => void) {}\n"
                        "export function check(id: number, enabled: boolean = id < 5) {}\n"
                        "export function nested(cb: ((ok: boolean) => void)) {}\n"
                        "export function extra(id: number, active = id < 5, secure: boolean) {}\n"
                        "export function configure(a: number, b: number, cb = factory<A, B>(), secure: boolean) {}\n",
        "guard/a.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n    from guard.b import value\n",
        "guard/b.py": "from guard.a import TYPE_CHECKING\nvalue = 1\n",
        "fake/a.py": "class Settings:\n    TYPE_CHECKING = True\nsettings = Settings()\n"
                     "if settings.TYPE_CHECKING:\n    from fake.b import value\n",
        "fake/b.py": "from fake.a import settings\nvalue = 1\n",
        "web/view.ts": "import {x} from '../repository/store';\n",
        "repository/store.ts": "export const x = 1;\n",
        "web/secret.ts": "import {x} from '../other/internal/secret';\n",
        "other/internal/secret.ts": "export const x = 1;\n",
    })
    _assert_rules(findings, (
        ("REDLINE-BOOL-TRAP", "web/flags.ts", "compact"),
        ("REDLINE-BOOL-TRAP", "web/flags.ts", "enabled"),
        ("REDLINE-BOOL-TRAP", "web/flags.ts", "secure"),
        ("REDLINE-CYCLE", "guard/", ""),
        ("REDLINE-CYCLE", "fake/", ""),
        ("ADVISORY-LAYER", "web/view.ts", ""),
        ("REDLINE-BOUNDARY", "web/secret.ts", ""),
    ), (("REDLINE-BOOL-TRAP", "onDone"), ("REDLINE-BOOL-TRAP", "nested"), ("REDLINE-PARAMS", "configure")))
    severities = {f.rule_id: f.severity for f in findings}
    assert severities["ADVISORY-LAYER"] == "WARNING"
    assert severities["REDLINE-BOUNDARY"] == severities["REDLINE-CYCLE"] == "BLOCKER"


def test_silent_handler_sentinels() -> None:
    """Distinguish False from zero and retain explicit recovery and propagation."""
    findings = _scan_temp_files({"exceptions.py": (
        "def zero():\n    try: work()\n    except Exception: return 0\n"
        "def fail():\n    try: work()\n    except Exception: return False\n"
        "def recover():\n    try: work()\n    except ValueError: return fallback()\n"
        "def propagate():\n    try: work()\n    except Exception: raise\n"
        "def combined():\n    try: work()\n    except (ValueError, Exception): return None\n"
    )})
    lines = [f.line for f in findings if f.rule_id == "SLOP-SILENT-CATCH"]
    assert lines == [6, 15], lines
    raw = "try:\n    work()\nexcept Exception:\n    print('failed')\n    return_value = None\n"
    assert not detect_ai_slop(Path("exceptions.py"), raw, {4}, Path.cwd())
    raw = "def fail():\n    try: work()\n    except Exception:\n        print('failed')\n        return None\n"
    changed_findings = detect_ai_slop(Path("exceptions.py"), raw, {5}, Path.cwd())
    assert len(changed_findings) == 1 and changed_findings[0].line == 3


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
    test_accuracy_regressions()
    test_scanner_scope_edges()
    test_silent_handler_sentinels()
    run_integration_tests()
    test_self_dogfooding()
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
