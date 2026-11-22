"""Repository integrity tests for vcdeslop v3 (pure Review Agent prompt)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANTIGRAVITY_ALLOWED_KEYS = {
    "name",
    "displayName",
    "version",
    "description",
    "author",
    "suggestedPrompts",
}


def strip_frontmatter(text: str) -> str:
    match = re.match(r"^---\r?\n.*?\r?\n---\r?\n+", text, flags=re.DOTALL)
    assert match, "expected YAML frontmatter"
    return text[match.end() :].strip()


def test_manifests() -> None:
    agy = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert set(agy.keys()) <= ANTIGRAVITY_ALLOWED_KEYS, f"unexpected keys: {set(agy.keys()) - ANTIGRAVITY_ALLOWED_KEYS}"
    assert len(agy.get("suggestedPrompts", [])) <= 3

    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert claude["name"] == "vcdeslop"
    assert claude["version"] == agy["version"]


def test_prompt_single_source_of_truth() -> None:
    canonical = (ROOT / "prompts" / "review-agent.md").read_text(encoding="utf-8").strip()
    assert not canonical.startswith("---"), "prompts/review-agent.md must not contain YAML frontmatter"

    skill_copy = (ROOT / "skills" / "vcdeslop" / "references" / "review-agent.md").read_text(encoding="utf-8").strip()
    assert skill_copy == canonical, "skills/vcdeslop/references/review-agent.md drifted from prompts/review-agent.md"

    for preset in (
        ROOT / "adapters" / "claude" / "agents" / "vcdeslop-reviewer.md",
        ROOT / "adapters" / "antigravity" / "agents" / "vcdeslop-reviewer.md",
    ):
        body = strip_frontmatter(preset.read_text(encoding="utf-8"))
        assert body == canonical, f"{preset.relative_to(ROOT)} body drifted from prompts/review-agent.md"


def test_no_programmatic_gates() -> None:
    forbidden = (
        ROOT / "hooks.json",
        ROOT / "adapters" / "hooks",
        ROOT / "adapters" / "rules",
        ROOT / "rules",
        ROOT / "skills" / "vcdeslop" / "scripts",
    )
    for path in forbidden:
        assert not path.exists(), f"legacy programmatic gate still present: {path.relative_to(ROOT)}"

    agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert not agents_md.startswith("---"), "AGENTS.md must not have YAML frontmatter"
    assert len(agents_md.encode("utf-8")) < 24_000

    skill_md = (ROOT / "skills" / "vcdeslop" / "SKILL.md").read_text(encoding="utf-8")
    assert len(skill_md.splitlines()) < 100
    assert "preflight_scan.py" not in skill_md


def test_windows_installer_and_legacy_cleanup() -> None:
    if os.name != "nt":
        return

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        claude_dir = home / ".claude"
        codex_dir = home / ".codex"
        claude_dir.mkdir(parents=True)
        codex_dir.mkdir(parents=True)

        # Seed legacy v2 hook + rule block alongside user config to verify clean stripping.
        (claude_dir / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": 'powershell -NoProfile -ExecutionPolicy Bypass -File "F:\\vcdeslop\\adapters\\hooks\\vcdeslop-hook.ps1" claude',
                                        "timeout": 5,
                                    }
                                ],
                            }
                        ]
                    },
                    "enabledPlugins": {"keep-me": True},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (codex_dir / "AGENTS.md").write_text(
            "# User rules\nKeep this.\n\n<!-- VCDESLOP-AUTO-GATE:START -->\nold gate\n<!-- VCDESLOP-AUTO-GATE:END -->\n",
            encoding="utf-8",
        )

        script = str(ROOT / "scripts" / "install.ps1")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "all", "-HomeDir", str(home), "-Copy", "-Force"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"install failed:\n{res.stdout}\n{res.stderr}"

        # Installed ONLY skill + reviewer presets, zero hooks/rules.
        assert (claude_dir / "agents" / "vcdeslop-reviewer.md").is_file()
        assert (claude_dir / "skills" / "vcdeslop" / "SKILL.md").is_file()
        agy_dir = home / ".gemini" / "config" / "plugins" / "vcdeslop"
        assert (agy_dir / "plugin.json").is_file()
        assert (agy_dir / "agents" / "vcdeslop-reviewer.md").is_file()
        assert (agy_dir / "skills" / "vcdeslop" / "SKILL.md").is_file()
        assert not (agy_dir / "hooks.json").exists()
        assert not (agy_dir / "rules").exists()
        assert not (home / ".cursor" / "rules" / "vcdeslop-gate.mdc").exists()

        # Legacy hook and rule block were stripped while user content stayed intact.
        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        assert "hooks" not in settings
        assert settings["enabledPlugins"] == {"keep-me": True}
        codex_agents = (codex_dir / "AGENTS.md").read_text(encoding="utf-8")
        assert "Keep this." in codex_agents
        assert "VCDESLOP-AUTO-GATE" not in codex_agents

        # Re-running with junctions + -Force must never move files out of the source repo.
        res_junc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "codex", "-HomeDir", str(home), "-Force"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_junc.returncode == 0, f"junction reinstall failed:\n{res_junc.stdout}\n{res_junc.stderr}"
        res_junc2 = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "codex", "-HomeDir", str(home), "-Force"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_junc2.returncode == 0, f"second junction reinstall failed:\n{res_junc2.stdout}\n{res_junc2.stderr}"
        assert (ROOT / "skills" / "vcdeslop" / "SKILL.md").is_file(), "source SKILL.md must remain intact after -Force over junction"

        # Uninstall cleans everything back up cleanly.
        res_un = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "all", "-HomeDir", str(home), "-Uninstall"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_un.returncode == 0, f"uninstall failed:\n{res_un.stdout}\n{res_un.stderr}"
        assert not (claude_dir / "agents" / "vcdeslop-reviewer.md").exists()
        assert not (claude_dir / "skills" / "vcdeslop").exists()
        assert not agy_dir.exists()
        assert not (home / ".agents" / "skills" / "vcdeslop").exists()


if __name__ == "__main__":
    test_manifests()
    test_prompt_single_source_of_truth()
    test_no_programmatic_gates()
    test_windows_installer_and_legacy_cleanup()
    print("ALL VCDESLOP V3 CHECKS PASSED")
