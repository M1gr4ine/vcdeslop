---
name: vcdeslop
description: >-
  Use whenever reviewing code, PRs, commits, or branch diffs, running post-coding
  quality checks, cleaning up AI-generated slop, or auditing maintainability
  (代码审查/Review/重构减负/去AI味自动触发). Orchestrates a deterministic polyglot
  preflight scan plus an isolated, same-model (`inherit`) subagent for double-blind
  review across Java, JS/TS, SQL, Python, Go, and Rust—enforcing zero Boolean
  Traps, positional params <= 4, zero duplicate blocks, zero circular or private
  cross-module imports, tombstone dead-code reaping, and mutation-test rigor.
license: MIT
metadata:
  version: "2.0.0"
  compatibility: "Claude Code, OpenAI Codex, DeepSeek Harness, Google Antigravity, Cursor; Python 3.10+ optional preflight"
---

# VCDeslop — Vibe Coding Deslop & Code Taste Reviewer

Eliminate AI-generated slop and enforce human-maintainable engineering taste. Combines deterministic diff metrics, Cursor-style `deslop` cleanup, Karpathy/Ousterhout/Grug taste principles, dead-code tombstone reaping, mental mutation auditing, and three quantitative `[BLOCKER]` redlines—executed by an **isolated subagent at the exact same intelligence tier as the main session**.

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

If the target repository already configures native linters or architecture tests, run those targeted checks too (`eslint`, `jscpd`, `dependency-cruiser`, `knip`, `tach`, `sqlfluff`, or `mvn`/`gradle` ArchUnit checks) so findings reflect real repo gates.

**Mid-task preflight**: After writing or substantially changing a non-trivial file, rerun `preflight_scan.py --mode diff` and fix redline hits immediately.

### Step 3: Delegate to an Isolated, Same-Model (`inherit`) Subagent

**Reviewer dispatch and verification**
- **Dispatch gate (edit-bound flows only)**: Applies whenever this flow will edit code — implementation flows and Clean / Deslop mode (including its `CleanAudit` pass). When Python is available, fix preflight blockers before dispatching; any blocker judged a scanner false positive stays unfixed and is passed through for the reviewer to confirm. Read-only Review never waits on this gate: dispatch immediately with the full preflight output. If Python is unavailable, skip the gate. The reviewer confirms findings in source and audits all five dimensions.
- **Overlap with verification**: Once production code and tests are complete, start the project build/test run and spawn the final reviewer in the same turn. Wait for both results, then address test failures and review findings together. Use serial execution on harnesses without asynchronous subagents.
- **Fix re-review**: For `Verify`, spawn a fresh `inherit` reviewer with only the prior reviewer's verbatim findings and the fix diff as evidence. Use the [Verify Report](./references/review-rubric.md#33-verify-report-fix-re-review) (§3.3). If the diff materially exceeds those findings, run a full Review (§3.1). The initial Review and CleanAudit payloads are listed below.

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
5. **Scope scaling by diff size**: Count additions plus deletions. Use one isolated `inherit` reviewer for up to 400 changed lines. For larger diffs, group whole files by directory or module, aiming for 400 changed lines per reviewer with at most four reviewers active at once. Keep an oversized file in one shard. For `--mode all` (no diff), split module-cohesive shards of roughly 3,000 scoped source lines each; run at most four reviewers concurrently and dispatch remaining shards in waves.
6. **Frozen-module pre-review**: When the overall scope exceeds 15 files, a module qualifies once its call chain and tests are complete and no further edits to it are planned. Spawn a read-only background reviewer for that module while work continues elsewhere. If the module changes later, discard its review and return it to the final review scope. At final review, cover all files without a valid pre-review and the cross-module interfaces; pass pre-review Tombstone findings to the final reviewer as provisional and have it reconfirm them against the final code, since edits in other modules can add or remove callers; also rerun the Tombstone dimension for frozen-module symbols referenced on any added or deleted diff line (deleted lines included) since that pre-review, to catch symbols whose last caller was removed, then rerun `preflight_scan.py` with the same scope flags resolved in Step 1 to check for cross-module duplication and cycles. Never dispatch after a fixed number of files: later edits invalidate findings, unfinished call chains distort dead-code and mutation findings, and separate batches can hide duplicates or cycles.

**Orchestrator discipline (what keeps it double-blind and cheap):**
- **No leading hints**: pass facts (scope, preflight output), never suspicions ("check why X", "look at installers"). Hypotheses anchor the reviewer and defeat the blind pass.
- **No shadow review**: while the reviewer runs, do not read and audit the same files yourself; wait for its report, then verify only the items you will act on.
- **Preflight is a heuristic signal, not a verdict**: regex/AST metrics have false positives and blind spots (renamed clones, narrative comments, redundant null checks). The reviewer confirms each `[BLOCKER]` in source before it blocks, and hunts what the scanner cannot see.

### Step 4: Synthesize Report or Apply Regression-Safe Cleanup

1. **In Review Mode**:
   - Present the **Verdict** (`PASS` / `CONCERNS` / `BLOCK`) and the **3-Redline Scorecard**.
   - List every `[BLOCKER]` and `[WARNING]` with exact `file:line`, principle violated, and a concrete minimal refactor snippet.
   - Include **Tombstone & Dead Code** observations and **Mental Mutation Test** gaps.
   - If the repo lacks automated enforcement, suggest 1–2 matching ecosystem tools from the playbook.
2. **In Clean / Deslop Mode** (after review identifies targets):
   - **Lock with tests**: Verify existing tests pass before editing (or write focused behavior checks first).
   - **Deletion-first surgical edits**: Strip obvious comments, dead orphans, abnormal defensive checks, Boolean Traps, unnecessary nesting, clones, and shallow pass-through wrappers—touching only scoped code.
   - **Re-verify**: Re-run `preflight_scan.py` (confirm `0` blockers) and the project's test suite.
   - **Concise summary & Before/After delta**: Report scope cleaned and verification results in 1–3 crisp sentences, plus a descriptive delta line:
     `Blockers: M -> 0 | Warnings: W -> V`

## Reference Index

- **Audit Checklist, Subagent Prompt & Output Schema**: [review-rubric.md](./references/review-rubric.md)
- **Java, JS/TS, SQL & Secondary Language Playbooks**: [language-playbooks.md](./references/language-playbooks.md)
- **Cross-Agent Subagent Spawn Guide**: [harness-adapters.md](./references/harness-adapters.md)
- **Deterministic Preflight Scanner CLI**: [preflight_scan.py](./scripts/preflight_scan.py)
