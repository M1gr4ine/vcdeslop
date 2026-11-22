# Optional Language Taste Notes

Non-dogmatic smell radar for common AI-coding pitfalls across languages. Use judgment—every item below is a heuristic, not a hard rule.

## JavaScript / TypeScript
- **Fallback traps**: Watch `a || b` clobbering valid empty strings `""`, `0`, or `false` where `??` or explicit type checks are needed.
- **Opaque flags & sprawling args**: Prefer named options objects or split functions when call sites read like guessing games; keep simple local helpers direct.
- **Honest async & boundaries**: Flag `catch {}` or log-and-continue catches that hide real I/O/state failures; leave intentional sandbox capability probes (e.g., `sessionStorage` / `history.replaceState`) alone.
- **Type escapes & cycles**: Question `as any` / `@ts-ignore` without clear justification, circular barrel `index.ts` re-exports, and `useEffect` duplicating derived UI state.
- **HTML / UI scripts**: Remember inline `<script>` blocks and DOM `data-testid` contracts—check that tests verify real markup wiring rather than only hand-rolled mocks.

## Java
- **Shallow wrapper inflation**: Question single-implementation `IXxxService` + `Impl` pairs that merely forward to a mapper with no domain or transaction boundary.
- **Nulls & swallowed errors**: Prefer empty collections over `null` returns; let infrastructure exceptions propagate to boundary handlers instead of returning empty results on DB failure.
- **Data access**: Watch N+1 queries inside loops, leaking persistence `@Entity` models across API boundaries, and raw `Map<String, Object>` bags where a `record` fits better.

## SQL
- **Readability & correctness**: Flatten deeply nested correlated subqueries or `CASE` pyramids into named `WITH` CTEs when clearer; watch `SELECT DISTINCT` masking 1-to-N join explosions.
- **Indexes & safety**: Watch non-Sargable predicates (`WHERE DATE(col) = ...`, `LOWER(col) = ...`) on hot paths, `SELECT *` in application contracts, and raw `${...}` string interpolation instead of bind parameters.
- **Silent corruption masking**: Question `LEFT JOIN` + `COALESCE` when foreign keys are `NOT NULL` by schema and an `INNER JOIN` would surface broken invariants.

## Python
- **Readable switches**: Keyword-only parameters (`def sync(src, *, dry_run=False)`) or small `@dataclass(frozen=True)` configs beat positional booleans at public call sites.
- **Error & type honesty**: Question bare `except Exception: pass` and unreasoned `# type: ignore`.

## Go
- **Errors & interfaces**: Watch ignored errors (`_ = err`) and Java-style producer interfaces; define small interfaces at the consumer side and avoid `utils`/`common` dumping grounds that pull in import cycles.

## Rust
- **Safety & borrowing**: Question `.unwrap()` / `.expect()` on fallible runtime paths outside tests/CLI entrypoints, gratuitous `.clone()` around borrow friction, and trait-object indirection where a small `enum` is clearer.

## Shell / PowerShell
- **Silent failure traps**: Watch `|| true`, `2>/dev/null`, or `-ErrorAction SilentlyContinue` hiding real state-changing failures, missing `set -euo pipefail` / `$ErrorActionPreference = 'Stop'`, and unquoted variable expansions.
