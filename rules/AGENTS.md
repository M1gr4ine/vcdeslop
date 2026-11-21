# VCDeslop & Code Taste Guardrails

## Pre-Coding Hygiene (Always Active)
- **Simplicity First (Karpathy)**: Write the minimum clear code required. No speculative abstractions, single-use wrappers, or unrelated drive-by edits.
- **Trust Internal Types**: Never add redundant null-checks or `try/catch` swallowing inside internal trusted code paths; validate only at I/O boundaries.
- **3 Hard Quantitative Redlines**:
  1. Zero Boolean Flag Parameters (`Boolean Trap`) & <= 4 positional parameters (use split functions, named options/DTO/Record/`*`, or Enums; trivial single-arg `setXxx(bool)` setters exempt).
  2. Newly added duplicate code blocks = 0 (search & reuse existing helpers).
  3. Block import cycles, cross-module private imports, and domain dependencies on outer layers. Report direct presentation-to-persistence imports as warnings for architectural review.

## Mandatory Review Auto-Trigger
- Whenever the user asks to **review code, review a PR/branch/diff, audit maintainability, or deslop changes**:
  1. Activate the **`vcdeslop`** skill (`skills/vcdeslop/SKILL.md`).
  2. Run `preflight_scan.py` when Python is available.
  3. Delegate the audit to an **isolated (`spawn`, never `fork`), same-model (`inherit`) subagent**—never downgrade to `haiku`/`flash`/`flash_lite`/`mini`/`Explore`.

## Implementation Flow (Always Active)
- **Mid-task**: after writing or substantially changing a non-trivial file, run `preflight_scan.py --mode diff` (if Python is available) and fix redline hits immediately.
- **Code-complete, before commit**: activate `vcdeslop` and spawn an isolated `inherit` reviewer in the same turn as build/test; resolve both results, then commit. Never commit first and review afterwards.
- **Round closure**: once a review round is accepted (`PASS`, or `CONCERNS` under an agreed exit condition), commit/push/install/release requests do not trigger a new review; re-review only the delta since acceptance, in `Verify` mode.
