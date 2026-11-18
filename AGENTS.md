# AGENTS.md — VCDeslop Maintenance Guide

This file governs all human and AI maintenance inside the `vcdeslop` repository.

## 1. Purpose & Architecture

`vcdeslop` is a cross-agent code review and AI-slop cleanup plugin/skill compatible with **Claude Code**, **OpenAI Codex**, **DeepSeek Harness (`dsh`)**, **Google Antigravity (AGY)**, and **Cursor** (`agentskills.io` open standard).

```text
vcdeslop/
├── AGENTS.md                        # This maintainer handbook (no YAML frontmatter, < 24 KB)
├── CLAUDE.md                        # `@AGENTS.md` pointer for Claude Code
├── plugin.json                      # Antigravity plugin manifest (strict schema)
├── hooks.json                       # Antigravity plugin root PreInvocation hook
├── .claude-plugin/plugin.json       # Claude Code plugin manifest
├── rules/AGENTS.md                  # Always-on plugin rule (auto-triggers review gate)
├── skills/vcdeslop/
│   ├── SKILL.md                     # Core progressive-disclosure skill (< 200 lines)
│   ├── references/
│   │   ├── review-rubric.md         # Double-blind prompt, 5 dimensions, output schema
│   │   ├── language-playbooks.md    # Java, JS/TS, SQL + Python, Go, Rust, Shell
│   │   └── harness-adapters.md      # Same-model (`inherit`) subagent spawn rules
│   └── scripts/
│       ├── preflight_scan.py        # CLI entrypoint (zero external dependencies)
│       └── analyzers/               # Modular DAG: models / lexers -> metrics_brace / git_scope -> complexity_params / clones_slop / graph_boundaries
├── adapters/
│   ├── hooks/                       # Native OS lifecycle hooks (`vcdeslop-hook.ps1`/`.cmd` & `vcdeslop-hook`)
│   ├── claude/ & antigravity/       # Harness subagent presets
│   └── rules/global-review-gate.md  # Idempotent global rule gate snippet
├── scripts/
│   ├── install.ps1                  # Native Windows PowerShell installer/uninstaller (no Python)
│   └── install.sh                   # Native POSIX/Bash installer/uninstaller (no Python)
└── tests/verify_scanner.py          # Synthetic fixture suite, native hook/installer tests, & dogfooding gate
```

## 2. Non-Negotiable Invariants

1. **Progressive Disclosure Budget (`skills/vcdeslop/SKILL.md`)**:
   - Keep `SKILL.md` concise (`< 200` lines). Put detailed checklists, language examples, and harness syntax in `skills/vcdeslop/references/`.
   - Keep `SKILL.md` frontmatter `description` under `900` characters (`agentskills.io` hard cap is `1024`) and include clear EN + ZH trigger phrases.
   - Keep `AGENTS.md` and `rules/AGENTS.md` free of YAML frontmatter and under `24,000` bytes.
2. **Strict Manifest Schemas**:
   - Root `plugin.json` is parsed by Antigravity and MUST only use supported top-level keys (`name`, `displayName`, `version`, `description`, `author`, `suggestedPrompts` up to 3 items). Do not add unknown fields like `license`.
   - `.claude-plugin/plugin.json` carries Claude Code manifest fields (`name`, `version`, `description`, `author`).
3. **Equal-Intelligence & Double-Blind Subagent Rule**:
   - Never modify `SKILL.md`, `rules/AGENTS.md`, `harness-adapters.md`, or `adapters/` to route reviews to weaker models (`haiku`, `flash`, `flash_lite`, `mini`, `Explore`).
   - Always preserve **fresh isolated spawn** (`spawn`, never `fork`) with `model: inherit` (or omitted model override) so the reviewer inherits the parent session's full reasoning capacity without author confirmation bias.
4. **Zero-Dependency Preflight Scanner (Opportunistic at Runtime)**:
   - `skills/vcdeslop/scripts/` MUST use Python 3.10+ standard library only—no third-party pip dependencies—while installers (`scripts/install.ps1`, `scripts/install.sh`) and hooks (`adapters/hooks/`) MUST NOT require Python at all.
5. **Dogfooding Self-Governance (Eat Our Own Dogfood)**:
   - Every code file in this repository MUST pass `python skills/vcdeslop/scripts/preflight_scan.py --path . --mode all --gate strict` with **0 blockers and 0 warnings**:
     - File LOC `<= 400` (advisory warning; never a hard blocker)
     - Function Cognitive Complexity `<= 15` (target `<= 10`)
     - Positional parameters `<= 4` and **0 Boolean Trap parameters** (use `@dataclass(frozen=True)` options or Python `*` keyword-only booleans)
     - Duplicate normalized code blocks `= 0`
     - Module circular dependencies `= 0` and zero private cross-module imports
   - Keep `skills/vcdeslop/scripts/analyzers/` strictly acyclic (DAG):
     `models` / `lexers` <- `metrics_brace` / `git_scope` <- `complexity_params` / `clones_slop` / `graph_boundaries` <- `__init__`.
   - Rule severities and remedies live in one catalog (`models.RULES`); build findings with `make_finding(...)` instead of repeating severity/remedy strings.

## 3. Extending Rules & Language Support

- **Adding a new complexity or parameter rule**: Brace languages + PowerShell live in `analyzers/metrics_brace.py`; Python/SQL/shell metrics and the finding translation live in `analyzers/complexity_params.py`. Keep language-specific patterns table-driven.
- **Adding a new slop/SQL check**: Add a `_SlopSpec` entry in `skills/vcdeslop/scripts/analyzers/clones_slop.py` and document the refactor pattern in `skills/vcdeslop/references/language-playbooks.md`.
- **Adding boundary rules**: Extend layer sets or import parsers in `skills/vcdeslop/scripts/analyzers/graph_boundaries.py`.
- **Every new rule MUST include a positive violation fixture AND a false-positive exemption case** in `tests/verify_scanner.py`.

## 4. Mandatory Verification Checklist

Run these commands from the repository root before considering any change complete:

```bash
python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('plugin.json', '.claude-plugin/plugin.json', 'hooks.json')]; print('JSON OK')"
python tests/verify_scanner.py
python skills/vcdeslop/scripts/preflight_scan.py --path . --mode all --gate strict
```

Validate the native Windows installer without side effects:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 all -Force -DryRun
```
