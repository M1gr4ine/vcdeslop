# VCDeslop & Code Taste Guardrails

## Pre-Coding Hygiene (Always Active)
- **Simplicity First (Karpathy)**: Write the minimum clear code required. No speculative abstractions, single-use wrappers, or unrelated drive-by edits.
- **Trust Internal Types**: Never add redundant null-checks or `try/catch` swallowing inside internal trusted code paths; validate only at I/O boundaries.
- **4 Hard Quantitative Redlines**:
  1. Single-function Cognitive Complexity <= 15 (file size > 400 lines is advisory `[WARNING]` only; never block cohesive deep modules).
  2. Zero Boolean Flag Parameters (`Boolean Trap`) & <= 4 positional parameters (use split functions, named options/DTO/Record/`*`, or Enums; trivial single-arg `setXxx(bool)` setters exempt).
  3. Newly added duplicate code blocks = 0 (search & reuse existing helpers).
  4. Module circular dependencies = 0; zero cross-layer or cross-module private-file imports.

## Mandatory Review Auto-Trigger
- Whenever the user asks to **review code, review a PR/branch/diff, audit maintainability, or deslop changes**:
  1. Activate the **`vcdeslop`** skill (`skills/vcdeslop/SKILL.md`).
  2. Run `preflight_scan.py` when Python is available.
  3. Delegate the audit to an **isolated (`spawn`, never `fork`), same-model (`inherit`) subagent**—never downgrade to `haiku`/`flash`/`flash_lite`/`mini`/`Explore`.
