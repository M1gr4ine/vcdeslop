---
name: vcdeslop
description: >-
  Use whenever reviewing code, PRs, commits, or branch diffs, running post-coding
  quality checks, cleaning up AI-generated slop, or auditing maintainability
  (代码审查/Review/重构减负/去AI味自动触发). Orchestrates a deterministic polyglot
  preflight scan plus an isolated, same-model (`inherit`) subagent for double-blind
  review across Java, JS/TS, SQL, Python, Go, and Rust—enforcing Cognitive
  Complexity <= 15, zero Boolean Traps, positional params <= 4, zero duplicate
  blocks, zero circular or private cross-module imports, tombstone dead-code reaping, and
  mutation-test rigor.
license: MIT
metadata:
  version: "2.0.0"
  compatibility: "Claude Code, OpenAI Codex, DeepSeek Harness, Google Antigravity, Cursor; Python 3.10+ optional preflight"
---

# VCDeslop — Vibe Coding Deslop & Code Taste Reviewer

Eliminate AI-generated slop and enforce human-maintainable engineering taste. Combines deterministic diff metrics, Cursor-style `deslop` cleanup, Karpathy/Ousterhout/Grug taste principles, dead-code tombstone reaping, mental mutation auditing, and four quantitative `[BLOCKER]` redlines—executed by an **isolated subagent at the exact same intelligence tier as the main session**.

## When to Activate

- **Review Mode (default)**: User asks to review code, inspect a PR/branch/diff, verify changes after implementation, or check maintainability/architecture. Keep workspace read-only and report verdict + concrete minimal patches.
- **Clean / Deslop Mode**: User explicitly asks to deslop, simplify, remove dead code, or fix review findings. Audit first, then apply regression-safe surgical deletions.

## Execution Protocol

### Step 1: Resolve Scope & Target Branch

1. Determine the diff target (explicit files, staged changes, working tree vs `HEAD`, or current branch vs `main`/`master`).
2. Default strictly to **changed files and lines** (`git diff`) plus their immediate dependencies—never perform unrequested full-repo rewrites unless `mode=all` was requested.

### Step 2: Run Deterministic Preflight Scan (Optional / Opportunistic)

If `python` or `python3` is available on `PATH`, run the zero-dependency polyglot preflight scanner bundled with this skill (from the resolved `<skill_dir>`):

```bash
python <skill_dir>/scripts/preflight_scan.py --path <repo_root> --mode diff
```

Useful flags: `--base main`, `--mode staged`, `--mode all`, `--files a.java b.ts c.sql`, `--format json`, `--gate strict`. If Python is not installed, skip this script without error and rely on repo-native linters plus direct `git diff` analysis.

If the target repository already configures native linters or architecture tests, run those targeted checks too (`eslint`, `jscpd`, `dependency-cruiser`, `knip`, `tach`, `complexipy`, `sqlfluff`, or `mvn`/`gradle` ArchUnit checks) so findings reflect real repo gates.

### Step 3: Delegate to an Isolated, Same-Model (`inherit`) Subagent

> [!IMPORTANT]
> **Two non-negotiable subagent rules:**
> 1. **Equal Intelligence (`model: inherit`)**: The reviewer subagent MUST run on the **exact same model and reasoning tier** as the parent session. Omit model overrides or pass `inherit`. **Never** route review to lightweight/cheap presets (`Explore`, `haiku`, `flash`, `flash_lite`, `mini`).
> 2. **Double-Blind Context Isolation (`spawn`, never `fork`)**: Start a **fresh** subagent session without inheriting the implementer's conversation history or reassuring self-summary. The reviewer trusts only the actual `git diff`, source files, test outputs, and preflight report.

Dispatch using your active harness (see [Harness Adapters](./references/harness-adapters.md) for exact signatures):

| Harness | Required Invocation | Model Rule |
| :--- | :--- | :--- |
| **Claude Code** | `Agent` / `Task` (`vcdeslop-reviewer` or `general-purpose`) | `model: inherit` (or omit); never `Explore`/`haiku` |
| **OpenAI Codex** | `spawn_agent` | Omit `model` & `reasoning_effort` (inherits parent) |
| **DeepSeek Harness** | `subagent` (`spawn`, fresh session; never `subagent_fork`) | Omit `model`/`provider` overrides (inherits route) |
| **Google Antigravity** | `invoke_subagent` (`TypeName: "self"` or `"vcdeslop-reviewer"`) | `Model: "inherit"`; never `flash`/`flash_lite` |
| **Cursor / Single-Thread** | No subagent API: run preflight, then audit raw diff cold | Same active session model |

Provide the subagent with:
1. Repository root, target mode (`Review` vs `CleanAudit`), and exact scope (`git diff` base, explicit files, or assigned module shard).
2. The full output of `preflight_scan.py` (and any native linter outputs).
3. Instructions to read **only** [Review Rubric](./references/review-rubric.md), the matching section(s) of [Language Playbooks](./references/language-playbooks.md), and scoped files/callers/tests inside `<repo_root>` (never inspect external host configs, IDE/harness docs, or web search).
4. **Mode-aware output contract**: emit the full Review Report in `Review Mode`, or ONLY the Compact Action List (`file:line + tag -> surgical edit`, omitting tables and code patches) in `CleanAudit`.
5. **Scope scaling (`<= 8` vs `> 8` files)**: dispatch **1** isolated `inherit` subagent for `<= 8` scoped files; for `> 8` files or `--mode all`, run `preflight_scan.py` first and shard scoped files by directory/module across **2–3 parallel** isolated `inherit` subagents.

**Orchestrator discipline (what keeps it double-blind and cheap):**
- **No leading hints**: pass facts (scope, preflight output), never suspicions ("check why X", "look at installers"). Hypotheses anchor the reviewer and defeat the blind pass.
- **No shadow review**: while the reviewer runs, do not read and audit the same files yourself; wait for its report, then verify only the items you will act on.
- **Preflight is a heuristic signal, not a verdict**: regex/AST metrics have false positives and blind spots (renamed clones, narrative comments, redundant null checks). The reviewer confirms each `[BLOCKER]` in source before it blocks, and hunts what the scanner cannot see.

### Step 4: Synthesize Report or Apply Regression-Safe Cleanup

1. **In Review Mode**:
   - Present the **Verdict** (`PASS` / `CONCERNS` / `BLOCK`) and the **4-Redline Scorecard**.
   - List every `[BLOCKER]` and `[WARNING]` with exact `file:line`, principle violated, and a concrete minimal refactor snippet.
   - Include **Tombstone & Dead Code** observations and **Mental Mutation Test** gaps.
   - If the repo lacks automated enforcement, suggest 1–2 matching ecosystem tools from the playbook.
2. **In Clean / Deslop Mode** (after review identifies targets):
   - **Lock with tests**: Verify existing tests pass before editing (or write focused behavior checks first).
   - **Deletion-first surgical edits**: Strip obvious comments, dead orphans, abnormal defensive checks, Boolean Traps, deep nesting, clones, and shallow pass-through wrappers—touching only scoped code.
   - **Re-verify**: Re-run `preflight_scan.py` (confirm `0` blockers) and the project's test suite.
   - **Concise summary & Before/After delta**: Report scope cleaned and verification results in 1–3 crisp sentences, plus a descriptive delta line:
     `Net LOC: ±N (+A / -B) | Max Cognitive Complexity: X -> Y | Blockers: M -> 0`
     *(Treat `Net LOC` strictly as an observation—never cram control flow into dense one-liners/nested ternaries or skip named Options/DTO types just to chase negative line counts.)*

## Reference Index

- **Audit Checklist, Subagent Prompt & Output Schema**: [review-rubric.md](./references/review-rubric.md)
- **Java, JS/TS, SQL & Secondary Language Playbooks**: [language-playbooks.md](./references/language-playbooks.md)
- **Cross-Agent Subagent Spawn Guide**: [harness-adapters.md](./references/harness-adapters.md)
- **Deterministic Preflight Scanner CLI**: [preflight_scan.py](./scripts/preflight_scan.py)
