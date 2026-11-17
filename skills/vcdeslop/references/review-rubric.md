# VCDeslop & Code Taste Review Rubric

Use this rubric inside the isolated reviewer subagent (or cold-context review pass). Every finding must reference an exact `file:line` observed in the diff or read files.

---

## 1. Double-Blind Reviewer Prompt Template

Pass this prompt block when spawning the reviewer subagent:

```text
You are an independent, senior code-taste and maintainability reviewer.

CORE RULES:
1. DO NOT TRUST any prior implementation summary, commit claim, or "tests pass" assertion. Independently verify the actual `git diff`, full surrounding files, callers, and test code.
2. Read `references/review-rubric.md` and the relevant language sections in `references/language-playbooks.md`.
3. Evaluate every scoped change against:
   - The 4 Hard Quantitative Maintainability Redlines ([BLOCKER])
   - Cursor Deslop Hygiene (slop comments, abnormal defensive checks/catches, type evasion, deep nesting, style drift)
   - Architecture & Cognitive Load Taste (Karpathy Simplicity & Surgical Changes, Ousterhout Deep Modules, Grug 3 AM readability)
   - Tombstone & Dead Code Reaping
   - Mental Mutation Test / Anti-Verification-Theater Audit
4. Output ONLY the standardized Markdown Review Report (Verdict, 4-Redline Scorecard, Findings with `file:line` and concrete minimal fix patches, Tombstone/Mutation notes).
```

---

## 2. Five Audit Dimensions

### Dimension A: 4 Hard Quantitative Maintainability Redlines (`[BLOCKER]`)

Any violation in scoped additions or modifications blocks merge (`Verdict: BLOCK`) unless explicitly justified in writing by an architectural exception:

1. **Single-Function Cognitive Complexity <= 15**
   - `REDLINE-COMPLEXITY` (`[BLOCKER]`): Any added/modified function, method, or SQL query exceeding **Cognitive Complexity 15** (penalizing nesting depth, mixed boolean chains `&&`/`||`, breaks in linear flow, and nested subqueries/`CASE`).
   - *Advisory Large-File Check (`SLOP-FILE-SIZE`, `[WARNING]` only — never `[BLOCKER]`)*: Files exceeding 400 lines emit a non-blocking warning to inspect domain cohesion; never chop cohesive **Deep Modules** into shallow pass-through files solely to hit an arbitrary line count.
   - *Fix*: Guard clauses / early returns, cohesive domain sub-functions, lookup maps/dispatch tables, or SQL `WITH` CTEs.

2. **Zero Boolean Flag Parameters (Boolean Trap) & Positional Params <= 4**
   - `REDLINE-BOOL-TRAP` (`[BLOCKER]`): **Zero Boolean flag arguments** (`true`/`false` positional parameters that switch internal behavior branches, e.g., `deleteOrder(id, true)` or `sendMsg(user, txt, true, false)`).
     - *Why*: Call sites become unreadable guessing games and every extra boolean splits the function into $2^N$ tangled branches.
     - *3 Canonical Refactors*:
       1. Distinct actions -> **split into two explicit functions** (`hardDeleteOrder(id)` + `archiveOrder(id)`).
       2. Optional configuration -> **named options object / DTO / Record / Python `*` keyword-only params** (`sendMsg(user, txt, { urgent: true, saveHistory: false })`).
       3. Mode/state -> **Enum or string literal union** (`setVisibility(postId, "public" | "private" | "friends_only")`).
     - *Sole Exemption*: Trivial single-property setters whose method name states the toggle clearly (`button.setDisabled(true)`, `modal.setVisible(false)`).
   - `REDLINE-PARAMS` (`[BLOCKER]`): More than **4 positional parameters** (`> 4`). Consolidate into a domain Record / DTO / Options struct.

3. **Newly Added Duplicate Code Blocks = 0 (`新增重复代码块 = 0`)**
   - `REDLINE-DUPLICATION` (`[BLOCKER]`): Zero newly copy-pasted or ~80%-similar logic blocks (>= 10 normalized lines) across files or inside the same file. Search the repository for existing helpers before creating new ones.
   - *Balance against Wrong Abstraction*: If two blocks merely look structurally similar today while representing **independently evolving business domains**, do **NOT** merge them by introducing a Boolean Trap monstrosity—either share a pure domain-neutral primitive or keep them cleanly separate.

4. **Module Circular Dependencies = 0 & Zero Cross-Layer / Private Imports (`模块循环依赖 = 0，严禁跨层引用私有文件`)**
   - `REDLINE-CYCLE` (`[BLOCKER]`): Zero circular imports (`A -> B -> ... -> A`) across files, packages, or SQL views.
   - `REDLINE-BOUNDARY` (`[BLOCKER]`):
     - Zero cross-module imports into private/internal files (`/internal/`, `/_internal/`, `/private/`, `/impl/`, or Python cross-package `._module`). Always import from the target module's public facade/entrypoint.
     - Zero architectural layer bypasses: Presentation (`controller`/`web`/`ui`/`routes`) importing Persistence (`mapper`/`dao`/`repository`) directly; Domain/Core importing Presentation or Infrastructure.

---

### Dimension B: Cursor Deslop Core

Check every diff line for classic AI-generated noise:
- **Obvious / Narrative Comments**: Comments restating the next line (`// Increment counter`, `// Return user`, `// Step 1: validate input`) or contradicting surrounding file comment density. Keep only *why* comments, invariant warnings, or required public API docstrings.
- **Abnormal Defensive Checks & Swallowed Errors**:
  - `try/catch` wrapping internal trusted code paths where errors should bubble up.
  - Empty `catch {}`, `except: pass`, or log-and-return-null `catch` that hides failures silently.
  - Redundant `if (x == null)` / `x?.y?.z` checks on non-nullable types or guaranteed internal contracts.
- **Type-Safety Evasion**: TypeScript `as any` / `: any` / `@ts-ignore`, Java `@SuppressWarnings("unchecked")` or raw types, Python bare `# type: ignore`.
- **Deep Nesting (> 3 levels)**: Pyramid-of-doom `if/for` blocks that flatten trivially via early returns or `continue`.
- **Style Drift**: Introducing a foreign naming convention, formatting style, or error-return pattern inconsistent with the surrounding module.

---

### Dimension C: Architecture & Cognitive Load Taste

- **Karpathy Simplicity First**: Reject speculative generalization, config flags nobody requested, dead future-proofing hooks, and abstractions with only a single caller unless a clear boundary invariant demands it.
- **Karpathy Surgical Changes**: Flag orthogonal edits—unsolicited reformatting, renaming untouched symbols, or rewriting unrelated helpers outside the task scope.
- **Ousterhout Deep Modules vs. Pass-Through Hell**:
  - Prefer **deep modules** (simple, narrow interface hiding non-trivial logic).
  - Flag **shallow wrapper chains** (`Controller -> Facade -> Manager -> Helper -> Mapper` where each layer only forwards arguments or renames DTO fields). Collapse trivial pass-through wrappers.
- **Grug-Brained 3 AM Readability**: Would an on-call engineer woken up at 3 AM understand this control flow in 10 seconds? Flag nested ternaries, clever bit/regex tricks, dense higher-order chains without intermediate names, and convoluted TypeScript type gymnastics.

---

### Dimension D: Tombstone & Dead Code Reaping (`墓碑代码与死代码收割`)

AI agents frequently solve bugs by writing a brand-new function while leaving the old one behind. Hunt down:
- Orphaned functions, methods, classes, types, exports, imports, feature flags, or SQL views/CTEs created or left behind in this diff/module with zero live callers.
- Commented-out "just in case" legacy code blocks.
- **Confidence rule**:
  - High confidence (no callers in repo, not a public framework/reflection entrypoint) -> delete directly (Clean Mode) or flag `[WARNING]`/`[BLOCKER]` (Review Mode).
  - Uncertain due to reflection, Spring DI, dynamic string routing, or runtime feature flags -> do NOT guess blindly; prescribe inserting a **Runtime Tombstone Probe** (lightweight deprecation metric/log hook) to verify zero production hits before removal.

---

### Dimension E: Anti-Verification-Theater / Mental Mutation Audit (`变异测试打假`)

When tests are added or modified in the diff, perform a **Mental Mutation Test** to catch hollow verification theater:
1. **Boundary Mutant**: Change `>` to `>=`, `<` to `<=`, or `+ 1` to `- 1` in the production diff. Would any test fail?
2. **Negation Mutant**: Invert a key `if` condition (`!` added/removed). Would tests still pass because mocks dictate outcomes?
3. **Deletion Mutant**: Delete a return statement, validation check, or persistence call. Do tests actually assert the observable side effect or just `expect(mock).toHaveBeenCalled()`?
4. **Test Smells**: Flag tests that assert only non-nullness (`toBeDefined()`, `assertNotNull()`), duplicate the implementation formula in the test expectation, swallow exceptions inside test helpers, or mock the unit under test.

---

## 3. Standardized Output Schema

The reviewer subagent MUST respond using this structure:

```markdown
# VCDeslop & Code Taste Review

- **Verdict**: `PASS` | `CONCERNS` | `BLOCK`
- **Scope**: `<branch/diff/files>` (`N` files reviewed)
- **Preflight**: `python preflight_scan.py` -> `<PASS/BLOCK, X blockers, Y warnings>`

## 4 Hard Redlines Scorecard
| Redline | Status | Count | Notes |
| :--- | :---: | :---: | :--- |
| 1. Single-Function Cognitive Complexity <= 15 | PASS/FAIL | N | ... |
| 2. Zero Boolean Traps & Positional Params <= 4 | PASS/FAIL | N | ... |
| 3. Newly Added Duplicate Code Blocks = 0 | PASS/FAIL | N | ... |
| 4. Circular Deps = 0 & No Cross-Layer/Private Imports | PASS/FAIL | N | ... |

## Findings (Ordered by Severity)
- **[BLOCKER] `<RULE-OR-TASTE-TAG>`** `path/to/File.ext:42` — What is wrong and why it hurts maintainability.
  - *Minimal Fix*:
    ```<lang>
    // Concise before -> after replacement
    ```
- **[WARNING] ...**

## Tombstone & Dead Code Audit
- <Unreferenced symbols/exports found, or "None observed in scope." Note if runtime tombstone probe is advised for dynamic paths.>

## Mental Mutation & Test Quality Audit
- <Weak/hollow assertions or surviving mutants found, or "N/A (no test changes)" / "Assertions verify observable behavior.">

## Optional Repo Gates to Wire In
- <1-2 targeted ecosystem linters/tools matching detected languages, only if not already configured.>
```
