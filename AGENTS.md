# AGENTS.md — VCDeslop Maintenance Guide

This file governs maintenance inside the `vcdeslop` repository.

## 1. Purpose & Design Philosophy

`vcdeslop` is a **pure built-in prompt repository for Review Agents** (with thin native agent presets and an optional opt-in skill).

It intentionally contains **no programmatic scanners, no auto-trigger hooks, no always-on global coding rules, and no hard numeric quotas**. Let models code freely; apply senior engineering judgment at review time.

```text
vcdeslop/
├── prompts/
│   └── review-agent.md                      # Canonical standalone Review Agent system prompt
├── adapters/
│   ├── antigravity/agents/vcdeslop-reviewer.md  # Antigravity reviewer subagent (frontmatter + canonical prompt)
│   └── claude/agents/vcdeslop-reviewer.md       # Claude Code reviewer subagent (frontmatter + canonical prompt)
├── skills/vcdeslop/
│   ├── SKILL.md                             # Ultra-lean opt-in dispatch skill
│   └── references/
│       ├── review-agent.md                  # Self-contained skill copy of prompts/review-agent.md
│       └── language-tastes.md               # Optional non-dogmatic language smell notes
├── scripts/
│   ├── install.ps1                          # Optional Windows installer/uninstaller (agent & skill only)
│   └── install.sh                           # Optional POSIX installer/uninstaller (agent & skill only)
├── plugin.json                              # Antigravity plugin manifest (strict schema)
├── .claude-plugin/plugin.json               # Claude Code plugin manifest
└── tests/verify_repo.py                     # Manifest, prompt-sync, and installer smoke tests
```

## 2. Invariants

1. **Single Prompt Body**: `prompts/review-agent.md` is the canonical source of truth. Keep `skills/vcdeslop/references/review-agent.md` and the markdown body of both `adapters/*/agents/vcdeslop-reviewer.md` files identical to it (`tests/verify_repo.py` enforces sync).
2. **No Programmatic Enforcement or Global Hijacking**:
   - Do not reintroduce preflight scanners, `hooks.json`, `adapters/hooks/`, `rules/AGENTS.md`, or always-on global rule-gate injections (`CLAUDE.md`/`AGENTS.md`/Cursor `.mdc`).
   - Keep `scripts/install.ps1` and `scripts/install.sh` scoped to installing the optional review agent / skill, while keeping `-Uninstall` / `--uninstall` able to clean up legacy v2.x hooks and rule blocks safely.
3. **Strict Manifest Schemas**:
   - Root `plugin.json` is parsed by Antigravity and must only use supported top-level keys (`name`, `displayName`, `version`, `description`, `author`, `suggestedPrompts` up to 3 items).
   - Keep `skills/vcdeslop/SKILL.md` concise (`< 100` lines) with `description` under `900` characters (`agentskills.io` cap is `1024`).
   - Keep `AGENTS.md` free of YAML frontmatter and under `24,000` bytes.
4. **Equal Intelligence for Review**: Keep `model: inherit` (or omit model downgrades) in reviewer agent presets and dispatch guidance so reviews never downgrade to weaker models.

## 3. Verification

Run from the repository root before committing:

```bash
python tests/verify_repo.py
```

Validate Windows installer dry-run without side effects:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 all -DryRun
```
