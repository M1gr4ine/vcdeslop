"""Regression tests for safe POSIX installer JSON updates."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if os.name == "nt":
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.exists():
            BASH = str(candidate)


def run_install(home: Path, *args: str, without_jq: bool = False) -> subprocess.CompletedProcess[str]:
    """Invoke the installer with an isolated home, optionally hiding jq."""
    command = [str(BASH), str(ROOT / "scripts/install.sh"), *args, "--home-dir", home.as_posix(), "--copy"]
    if without_jq:
        setup = 'command() { if [[ "$1" == -v && "$2" == jq ]]; then return 1; fi; builtin command "$@"; }; export -f command; exec "$@"'
        command = [str(BASH), "-c", setup, "test", *command]
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_missing_jq_stops_before_any_install() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        settings = home / ".claude/settings.json"
        write_json(settings, {"theme": "dark"})
        original = settings.read_bytes()
        result = run_install(home, "all", without_jq=True)
        assert result.returncode != 0 and "jq" in result.stderr
        assert settings.read_bytes() == original
        assert not (home / ".agents").exists()
        assert not (home / ".vcdeslop-backups").exists()


def test_no_jq_for_targets_that_do_not_need_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = run_install(home, "universal", "cursor", without_jq=True)
        assert result.returncode == 0, result.stderr
        assert (home / ".cursor/rules/vcdeslop-gate.mdc").exists()
        assert not (home / ".cursor/hooks.json").exists()


def test_antigravity_install_requires_jq() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        result = run_install(home, "antigravity", without_jq=True)
        assert result.returncode != 0 and "jq" in result.stderr
        assert not (home / ".gemini").exists()
        assert run_install(home, "antigravity", "--uninstall", without_jq=True).returncode == 0


def test_bad_json_fails_before_install() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = home / ".dsh/hooks.json"
        path.parent.mkdir()
        path.write_text("{broken", encoding="utf-8")
        result = run_install(home, "all")
        assert result.returncode != 0
        assert path.read_text(encoding="utf-8") == "{broken"
        assert not (home / ".agents").exists()


def test_cursor_migration_preserves_other_hooks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = home / ".cursor/hooks.json"
        other = {"type": "command", "command": "/tools/vcdeslop-hook-helper"}
        legacy = {"type": "command", "command": '"/old/vcdeslop-hook" cursor'}
        write_json(path, {"version": 1, "hooks": {"UserPromptSubmit": [{"matcher": "x", "hooks": [other, legacy]}], "otherEvent": [other]}})
        result = run_install(home, "cursor")
        assert result.returncode == 0, result.stderr
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"version": 1, "hooks": {"UserPromptSubmit": [{"matcher": "x", "hooks": [other]}], "otherEvent": [other]}}


def test_uninstall_preserves_other_hooks_in_same_group() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = home / ".claude/settings.json"
        other = {"type": "command", "command": "another-hook"}
        legacy = {"type": "command", "command": "/old/vcdeslop-hook claude"}
        write_json(path, {"theme": "dark", "hooks": {"UserPromptSubmit": [{"hooks": [legacy, other]}]}})
        result = run_install(home, "claude", "--uninstall")
        assert result.returncode == 0, result.stderr
        assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "dark", "hooks": {"UserPromptSubmit": [{"hooks": [other]}]}}


def test_install_and_repeat_are_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = home / ".claude/settings.json"
        write_json(path, {"theme": "dark", "hooks": {"UserPromptSubmit": [{"hooks": [{"command": "other"}]}]}})
        for _ in range(2):
            result = run_install(home, "claude")
            assert result.returncode == 0, result.stderr
        data = json.loads(path.read_text(encoding="utf-8"))
        groups = data["hooks"]["UserPromptSubmit"]
        assert data["theme"] == "dark" and len(groups) == 2
        assert groups[0]["hooks"] == [{"command": "other"}]
        assert 'vcdeslop-hook" claude' in groups[1]["hooks"][0]["command"]


def test_cursor_unchanged_without_owned_hook() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = home / ".cursor/hooks.json"
        write_json(path, {"hooks": {"UserPromptSubmit": []}})
        original = path.read_bytes()
        result = run_install(home, "cursor")
        assert result.returncode == 0, result.stderr
        assert path.read_bytes() == original


def test_only_actual_hook_invocations_are_removed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = home / ".cursor/hooks.json"
        keep = ["echo /old/vcdeslop-hook was skipped", "other /old/vcdeslop-hook cursor", "/old/vcdeslop-hook-helper cursor"]
        remove = ['/old/vcdeslop-hook cursor', '"/old path/vcdeslop-hook" cursor',
                  'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\\old path\\vcdeslop-hook.ps1" cursor']
        write_json(path, {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": cmd} for cmd in keep + remove]}]}})
        result = run_install(home, "cursor")
        assert result.returncode == 0, result.stderr
        hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"][0]["hooks"]
        assert [hook["command"] for hook in hooks] == keep


def test_json_symlink_and_mode_preserved() -> None:
    if os.name != "posix":
        print("POSIX symlink test skipped on non-POSIX host")
        return
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        target = home / "shared.json"
        link = home / ".claude/settings.json"
        write_json(target, {"theme": "dark"})
        target.chmod(0o640)
        link.parent.mkdir()
        link.symlink_to("../shared.json")
        result = run_install(home, "claude")
        assert result.returncode == 0, result.stderr
        assert link.is_symlink() and os.readlink(link) == "../shared.json"
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
        assert json.loads(target.read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"]


def run_posix_installer_tests() -> None:
    if not BASH:
        print("POSIX installer tests skipped: Bash unavailable")
        return
    test_missing_jq_stops_before_any_install()
    test_no_jq_for_targets_that_do_not_need_json()
    test_antigravity_install_requires_jq()
    if subprocess.run([str(BASH), "-c", "command -v jq"], capture_output=True).returncode:
        print("POSIX JSON merge tests skipped: jq unavailable")
        return
    test_bad_json_fails_before_install()
    test_cursor_migration_preserves_other_hooks()
    test_uninstall_preserves_other_hooks_in_same_group()
    test_install_and_repeat_are_idempotent()
    test_cursor_unchanged_without_owned_hook()
    test_only_actual_hook_invocations_are_removed()
    test_json_symlink_and_mode_preserved()


if __name__ == "__main__":
    run_posix_installer_tests()
