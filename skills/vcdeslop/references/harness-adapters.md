# Cross-Agent Harness Adapters (Same-Model `inherit` Subagent Dispatch)

This skill is portable across all `agentskills.io`-compatible coding agents. To preserve **equal reasoning intelligence** and **double-blind verification**, always follow the exact spawn rules for your active runtime.

---

## Non-Negotiable Dispatch Rules

1. **Same Model / Reasoning Tier (`inherit`)**: Never delegate code review to a cheaper or faster sub-model (`haiku`, `flash`, `flash_lite`, `mini`, `Explore`). Omit model parameters or set `inherit` so the reviewer runs with the parent session's full intelligence.
2. **Fresh Context (`spawn`, never `fork`)**: Do NOT copy the implementer's conversation history or persuasive summary into the reviewer. Give the reviewer only the task scope, repo path, preflight output, and rubric links so it independently verifies the actual code and `git diff`.
3. **Parallel module sharding**: For diffs, count additions plus deletions. Use one isolated `inherit` reviewer for up to 400 changed lines. For larger diffs, group whole files by directory or module into shards of roughly 400 changed lines each. For `--mode all` (no diff), split module-cohesive shards of roughly 3,000 scoped source lines each; run at most four reviewers concurrently and dispatch remaining shards in waves. In each wave, spawn up to four isolated `inherit` reviewers in a single turn, each with its file list and the full preflight output when available, to reduce review time without downgrading model intelligence.
4. **Overlap & Verify**: In implementation flows, once production and test files are complete and the Step 3 dispatch gate is satisfied, start the build/tests and spawn the reviewer in the same turn, falling back to serial execution without asynchronous subagents. For fix re-review, use a fresh `inherit` reviewer in `Verify` mode with only the prior reviewer's verbatim findings and the fix diff as evidence ([Review Rubric §3.3](./review-rubric.md#33-verify-report-fix-re-review)).

---

## 1. Claude Code

- **Recommended path**: Install `adapters/claude/agents/vcdeslop-reviewer.md` to `~/.claude/agents/vcdeslop-reviewer.md` (done automatically by `scripts/install.ps1` / `scripts/install.sh`), OR invoke the built-in `Agent` / `Task` tool directly.
- **How to invoke**:
  - Call `Agent` (or `Task`) with `subagent_type: "vcdeslop-reviewer"` (whose frontmatter pins `model: inherit`) or `subagent_type: "general-purpose"` and **omit `model`** (or pass `model: "inherit"`).
  - **Pitfall**: Never use `subagent_type: "Explore"` for code review—it forces a lightweight model (`haiku`) and read-only exploration bias.
- **Prompt payload**: Include `<repo_root>`, `<mode: Review | Clean>`, `<preflight_scan.py output>`, and paths to `skills/vcdeslop/references/review-rubric.md` + `language-playbooks.md`.

## 2. OpenAI Codex

- **Skill location**: `.agents/skills/vcdeslop` (project) or `~/.codex/skills/vcdeslop` (user global).
- **How to invoke**:
  - Call `spawn_agent` with the double-blind review prompt from `references/review-rubric.md`.
  - **Model rule**: **Omit** `model` and `reasoning_effort` in `spawn_agent` arguments so Codex automatically inherits the parent session's exact model and reasoning effort.
  - **Pitfall**: Do not pass a fast/mini worker profile override if one is configured in `config.toml`.
- **Completion**: Wait for the spawned thread result via `wait` / agent wakeup, then synthesize the standardized Markdown review report.

## 3. DeepSeek Harness (`dsh`)

- **Skill location**: `.dsh/skills/vcdeslop` or `.agents/skills/vcdeslop` (project), or `~/.dsh/skills/vcdeslop` / `~/.agents/skills/vcdeslop` (global).
- **How to invoke**:
  - Call the `subagent` tool (`packages/subagent/tool-subagent`) using **`spawn`** (fresh isolated child session).
  - **Model rule**: **Omit** explicit `provider`, `model`, and `reasoning_effort` overrides so the child subagent inherits the parent session's active `modelSelection` route.
  - **Pitfall**: **NEVER** use `subagent_fork` for code review—`fork` clones the parent's completed conversation turns, which exposes the implementer's claims and defeats double-blind verification.

## 4. Google Antigravity (AGY)

- **Plugin / Skill location**: `~/.gemini/config/plugins/vcdeslop/` (or project `.agents/skills/vcdeslop/`).
- **How to invoke**:
  - Call `invoke_subagent` with:
    - `TypeName: "self"` (always available; inherits full system intelligence and tools) or `"vcdeslop-reviewer"` (when installed via plugin `agents/vcdeslop-reviewer.md`)
    - `Model: "inherit"` (MUST be `"inherit"`; **never** `"flash"` or `"flash_lite"`)
    - `Role: "Double-Blind Code Taste Reviewer"`
    - `Workspace: "inherit"` (for Review Mode, keeping edits zero)
- **Wait rule**: Do not poll in a loop; end turn or proceed with read-only checks until the subagent message wakes you up.

## 5. Cursor & Single-Thread Agents (Fallback)

If the active environment has no subagent tool (or subagents are disabled by policy):
1. When Python is available, run `python <skill_dir>/scripts/preflight_scan.py --path <repo_root>` with the scope flags resolved in Step 1. Reuse those flags for the final cross-module scan.
2. Re-read the raw `git diff` and full target files from scratch—explicitly ignoring prior rationalizations in chat history.
3. Apply all five dimensions of `references/review-rubric.md` line by line using the current session model.

Review and tests cannot overlap on a single-thread harness. For implementation and Clean / Deslop flows (including `CleanAudit`), apply the Step 3 gate and pass suspected scanner false positives through for confirmation. In read-only Review, proceed immediately with the full preflight output when available. Use `Verify` for fix-scoped re-checks (§3.3).
