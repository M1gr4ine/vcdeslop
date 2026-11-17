"""Integration tests for shell LF line endings, git diff scoping, native hooks, and installer lifecycle."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "vcdeslop" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyzers import ScanOptions, run_scan  # noqa: E402


def _run_git(cwd: Path, *args: str) -> None:
    """Run a git command inside cwd expecting zero exit code."""
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _run_hook_cmd(cmd: list[str] | str, payload: dict[str, object]) -> dict[str, object]:
    """Run native OS hook command with JSON stdin and decode stdout JSON."""
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=isinstance(cmd, str),
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_shell_lf_line_endings() -> None:
    """Verify POSIX shell scripts use LF-only line endings without CRLF corruption."""
    for rel in ("adapters/hooks/vcdeslop-hook", "scripts/install.sh"):
        raw = (REPO_ROOT / rel).read_bytes()
        assert b"\r\n" not in raw, f"CRLF line ending detected in {rel}"


def test_git_diff_scope() -> None:
    """Verify diff mode works under a `build/` ancestor dir and scopes strictly to changed lines."""
    legacy_complex = (
        "def legacy_fn(a: int, b: int, c: int) -> int:\n"
        "    if a > 0:\n"
        "        for i in range(a):\n"
        "            if i > b:\n"
        "                while c > 0:\n"
        "                    if c > 1:\n"
        "                        return 1\n"
        "    return 0\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "build" / "repo"
        repo.mkdir(parents=True)
        _run_git(repo, "init")
        _run_git(repo, "config", "user.email", "test@acme.io")
        _run_git(repo, "config", "user.name", "VCDeslop Test")
        app = repo / "app.py"
        app.write_text(legacy_complex, encoding="utf-8")
        _run_git(repo, "add", "app.py")
        _run_git(repo, "commit", "-m", "initial")

        app.write_text(
            legacy_complex + "\ndef bad_flag(x: bool = True) -> bool:\n    return x\n",
            encoding="utf-8",
        )
        report = run_scan(ScanOptions(root=repo, scan_mode="diff"))
        assert report.scanned_files == 1, f"Expected 1 scanned file under build/repo, got {report.scanned_files}"
        assert any(f.rule_id == "REDLINE-BOOL-TRAP" and "bad_flag" in f.message for f in report.findings)
        assert not any(f.rule_id == "REDLINE-COMPLEXITY" and "legacy_fn" in f.message for f in report.findings)


def test_native_hooks() -> None:
    """Verify PowerShell/cmd hooks trigger on EN+ZH intents and ignore workspace-path or false matches."""
    agy_cmd = '"./adapters/hooks/vcdeslop-hook" antigravity'
    ps_cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "adapters/hooks/vcdeslop-hook.ps1", "claude"]

    assert _run_hook_cmd(agy_cmd, {"cwd": str(REPO_ROOT), "lastUserInput": "implement user login"}) == {}
    assert _run_hook_cmd(agy_cmd, {"lastUserInput": "preview the component"}) == {}
    assert "injectSteps" in _run_hook_cmd(agy_cmd, {"lastUserInput": "对这个项目做一下deslop"})
    assert "injectSteps" in _run_hook_cmd(agy_cmd, {"lastUserInput": "帮我review这个PR"})

    cc_res = _run_hook_cmd(ps_cmd, {"prompt": "please review my branch diff"})
    assert cc_res["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def _run_installer(home: Path, *extra_args: str) -> None:
    """Invoke native PowerShell installer against an isolated fake home directory."""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/install.ps1", "all", "-HomeDir", str(home), *extra_args],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )


def test_installer_idempotency_and_uninstall() -> None:
    """Verify repeated `-Force` installs never collide and `-Uninstall` preserves user config & repo files."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        (claude_dir / "CLAUDE.md").write_text("# Custom Notes\nKeep this line.\n", encoding="utf-8")

        _run_installer(home, "-Force")
        _run_installer(home, "-Force")

        raw_settings = (claude_dir / "settings.json").read_bytes()
        assert not raw_settings.startswith(b"\xef\xbb\xbf"), "PowerShell JSON output must be BOM-free"
        settings = json.loads(raw_settings.decode("utf-8"))
        assert settings["theme"] == "dark" and len(settings["hooks"]["UserPromptSubmit"]) == 1
        assert "Optional Global Review" not in (claude_dir / "CLAUDE.md").read_text(encoding="utf-8")

        removed_paths = (
            home / ".agents" / "skills" / "vcdeslop" / "SKILL.md",
            claude_dir / "skills" / "vcdeslop" / "SKILL.md",
            claude_dir / "agents" / "vcdeslop-reviewer.md",
            home / ".codex" / "skills" / "vcdeslop" / "SKILL.md",
            home / ".codex" / "AGENTS.md",
            home / ".dsh" / "skills" / "vcdeslop" / "SKILL.md",
            home / ".dsh" / "AGENTS.md",
            home / ".gemini" / "config" / "plugins" / "vcdeslop" / "hooks.json",
            home / ".gemini" / "config" / "plugins" / "vcdeslop" / "agents" / "vcdeslop-reviewer.md",
            home / ".cursor" / "skills" / "vcdeslop" / "SKILL.md",
            home / ".cursor" / "rules" / "vcdeslop-gate.mdc",
        )
        hook_jsons = (
            claude_dir / "settings.json",
            home / ".codex" / "hooks.json",
            home / ".dsh" / "hooks.json",
            home / ".cursor" / "hooks.json",
        )
        for p in removed_paths + hook_jsons:
            assert p.exists(), f"Missing installed path: {p}"

        _run_installer(home, "-Uninstall")
        for p in removed_paths:
            assert not p.exists(), f"Path remained after uninstall: {p}"
        for hp in hook_jsons:
            assert "hooks" not in json.loads(hp.read_text(encoding="utf-8"))
        claude_md = (claude_dir / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Keep this line." in claude_md and "VCDESLOP-AUTO-GATE" not in claude_md
        assert (REPO_ROOT / "skills" / "vcdeslop" / "SKILL.md").is_file()


def run_integration_tests() -> None:
    """Execute all hook, git-scope, line-ending, and installer integration tests."""
    test_shell_lf_line_endings()
    test_git_diff_scope()
    test_native_hooks()
    test_installer_idempotency_and_uninstall()
