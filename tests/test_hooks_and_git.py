"""Integration tests for shell LF line endings, git diff scoping, native hooks, and installer lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "vcdeslop" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyzers import ScanOptions, run_scan  # noqa: E402
from tests.test_posix_install import BASH, run_posix_installer_tests  # noqa: E402


def _run_git(cwd: Path, *args: str) -> None:
    """Run a git command inside cwd expecting zero exit code."""
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _run_hook_cmd(cmd: list[str] | str, payload: dict[str, object]) -> dict[str, object]:
    """Run native OS hook command with JSON stdin and decode stdout JSON."""
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        input=json.dumps(payload, ensure_ascii=True),
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


def _assert_antigravity_hook(cmd: list[str] | str) -> None:
    """Run the manifest command with actual Antigravity payloads."""
    assert _run_hook_cmd(cmd, {"cwd": str(REPO_ROOT), "lastUserInput": "implement user login"}) == {}
    assert _run_hook_cmd(cmd, {"lastUserInput": "preview the component"}) == {}
    assert "injectSteps" in _run_hook_cmd(cmd, {"lastUserInput": "代码审查", "invocationNum": 0})
    assert "injectSteps" in _run_hook_cmd(cmd, {"lastUserInput": "对这个项目做一下deslop"})
    assert "injectSteps" in _run_hook_cmd(cmd, {"lastUserInput": "帮我review这个PR"})
    assert "injectSteps" in _run_hook_cmd(cmd, {"lastUserInput": "review my diff", "invocationNum": 0})
    assert _run_hook_cmd(cmd, {"lastUserInput": "review my diff", "invocationNum": 1}) == {}
    assert _run_hook_cmd(cmd, {"lastUserInput": "review my diff", "invocationNum": 7}) == {}
    nested_prompt = {"extra": {"lastUserInput": "review my diff"}, "lastUserInput": "implement login", "invocationNum": 0}
    nested_invocation = {"extra": {"invocationNum": 7}, "lastUserInput": "review my diff", "invocationNum": 0}
    assert _run_hook_cmd(cmd, nested_prompt) == {}
    assert "injectSteps" in _run_hook_cmd(cmd, nested_invocation)


def test_native_hooks() -> None:
    """Exercise the manifest command via the native platform and an available POSIX shell."""
    manifest = json.loads((REPO_ROOT / "hooks.json").read_text(encoding="utf-8"))
    command = manifest["vcdeslop-review-trigger"]["PreInvocation"][0]["command"]
    if sys.platform == "win32":
        _assert_antigravity_hook(command)
        ps_cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "adapters/hooks/vcdeslop-hook.ps1", "claude"]
        cc_res = _run_hook_cmd(ps_cmd, {"prompt": "please review my branch diff"})
        assert cc_res["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "hookSpecificOutput" in _run_hook_cmd(ps_cmd, {"prompt": "review my diff", "invocationNum": 7})
    if BASH:
        _assert_antigravity_hook([BASH, "-c", command])
        posix_cc = [BASH, "adapters/hooks/vcdeslop-hook", "claude"]
        assert "hookSpecificOutput" in _run_hook_cmd(posix_cc, {"prompt": "review my diff", "invocationNum": 7})


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
        )
        for p in removed_paths + hook_jsons:
            assert p.exists(), f"Missing installed path: {p}"
        assert not (home / ".cursor" / "hooks.json").exists(), "Cursor hooks cannot inject context; rule gate only"

        _run_installer(home, "-Uninstall")
        for p in removed_paths:
            assert not p.exists(), f"Path remained after uninstall: {p}"
        for hp in hook_jsons:
            assert "hooks" not in json.loads(hp.read_text(encoding="utf-8"))
        claude_md = (claude_dir / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Keep this line." in claude_md and "VCDESLOP-AUTO-GATE" not in claude_md
        assert (REPO_ROOT / "skills" / "vcdeslop" / "SKILL.md").is_file()


def test_installer_hook_upgrade() -> None:
    """An upgrade must preserve other commands sharing a legacy matcher group."""
    keep = {"type": "command", "command": "vcdeslop-hook-helper", "timeout": 15}
    log = {"type": "command", "command": "echo /old/vcdeslop-hook cursor"}
    group = {"matcher": "custom", "hooks": [keep, log]}
    cursor_events = {"beforeSubmitPrompt": [{"command": "my-prompt-filter"}]}
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        for harness, filename in (("claude", "settings.json"), ("cursor", "hooks.json")):
            conf = home / f".{harness}" / filename
            conf.parent.mkdir()
            old = {"type": "command", "command": f'powershell -File "old/vcdeslop-hook.ps1" {harness}'}
            events = {"UserPromptSubmit": [dict(group, hooks=[old, keep, log])]}
            if harness == "cursor":
                events.update(cursor_events)
            conf.write_text(json.dumps({"hooks": events}), encoding="utf-8")

        _run_installer(home, "-Force", "-Copy")
        cursor = home / ".cursor" / "hooks.json"
        assert json.loads(cursor.read_bytes())["hooks"] == dict(cursor_events, UserPromptSubmit=[group])
        claude = home / ".claude" / "settings.json"
        groups = json.loads(claude.read_bytes())["hooks"]["UserPromptSubmit"]
        assert groups[0] == group and len(groups) == 2
        assert len(groups[1]["hooks"]) == 1 and groups[1]["hooks"][0]["command"].endswith(" claude")

        # No owned entry is left in Cursor. Preserve the user's formatting on the next cleanup.
        cursor_original = json.dumps(json.loads(cursor.read_bytes()), indent=4).encode()
        cursor.write_bytes(cursor_original)
        rule = home / ".cursor" / "rules" / "vcdeslop-gate.mdc"
        with rule.open("a", encoding="utf-8") as file:
            file.write("\nUse our shared library for date parsing.\n")
        _run_installer(home, "-Uninstall")
        assert "Use our shared library for date parsing." in rule.read_text(encoding="utf-8")
        assert cursor.read_bytes() == cursor_original
        assert json.loads(claude.read_bytes())["hooks"]["UserPromptSubmit"] == [group]


def test_installer_upgrades_dangling_antigravity_junction() -> None:
    """A stale plugin junction must be backed up before a working installation replaces it."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        old_target = home / "old-plugin"
        old_target.mkdir()
        plugin = home / ".gemini" / "config" / "plugins" / "vcdeslop"
        plugin.parent.mkdir(parents=True)
        env = dict(os.environ, VCDESLOP_TEST_LINK=str(plugin), VCDESLOP_TEST_TARGET=str(old_target))
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "New-Item -ItemType Junction -Path $env:VCDESLOP_TEST_LINK -Target $env:VCDESLOP_TEST_TARGET | Out-Null"],
            check=True, capture_output=True, text=True, env=env,
        )
        old_target.rmdir()
        assert os.path.lexists(plugin) and not plugin.exists()
        _run_installer(home, "-Force")
        backups = list((home / ".vcdeslop-backups").glob("*/gemini_config_plugins_vcdeslop"))
        assert len(backups) == 1 and os.path.lexists(backups[0])
        assert Path(os.readlink(backups[0]).removeprefix('\\\\?\\')) == old_target
        assert not old_target.exists()
        for rel in ("plugin.json", "hooks.json", "agents/vcdeslop-reviewer.md", "skills/vcdeslop/SKILL.md"):
            assert (plugin / rel).read_text(encoding="utf-8")


def run_integration_tests() -> None:
    """Execute all hook, git-scope, line-ending, and installer integration tests."""
    test_shell_lf_line_endings()
    test_git_diff_scope()
    test_native_hooks()
    if sys.platform == "win32":
        test_installer_idempotency_and_uninstall()
        test_installer_hook_upgrade()
        test_installer_upgrades_dangling_antigravity_junction()
    run_posix_installer_tests()
