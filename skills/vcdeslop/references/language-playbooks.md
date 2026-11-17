# Language Playbooks: High-Taste Patterns & Ecosystem Gates

Concrete `Slop -> High-Taste` refactors and deterministic repo tools organized by priority tier.

---

## Tier 1: Primary Languages (Java, JS/TS, SQL)

### 1. Java (Spring Boot / MyBatis / JPA)

#### Common AI Slop & Anti-Patterns
1. **Boolean Traps & Parameter Bloat**:
   ```java
   // ❌ Slop: Flag argument + 5 positional parameters
   public void deleteOrder(Long orderId, Long operatorId, boolean isHardDelete, boolean skipNotify, String reason) {
       if (isHardDelete) { /* ... */ } else { /* ... */ }
   }

   // ✅ High Taste: Split distinct behaviors + Java record/Command object
   public record DeleteOrderCommand(Long orderId, Long operatorId, String reason) {}
   public void archiveOrder(DeleteOrderCommand cmd) { /* ... */ }
   public void purgeOrder(DeleteOrderCommand cmd) { /* ... */ }
   ```
2. **Swallowed Exceptions & Defensive Null Theater**:
   ```java
   // ❌ Slop: Swallowing checked/runtime error and returning null collection
   public List<OrderDto> findActive(Long userId) {
       if (userId == null) return null; // caller NPE trap
       try {
           return mapper.selectActive(userId);
       } catch (Exception e) {
           log.error("failed", e);
           return Collections.emptyList(); // silently hides DB outage!
       }
   }

   // ✅ High Taste: Return empty collection by contract; let infrastructure errors propagate to ControllerAdvice
   public List<OrderDto> findActive(long userId) {
       return mapper.selectActive(userId);
   }
   ```
3. **Layer Bypasses & Shallow `IXxxService` Wrapper Bloat**:
   - `[BLOCKER]` `Controller` directly injecting `OrderMapper` / `OrderRepository` or returning JPA `@Entity` objects instead of response DTOs.
   - `[WARNING]` Creating `interface FooService` + `FooServiceImpl` with only 1 implementation that just forwards `return fooMapper.selectById(id)` without transaction boundaries or domain rules. Use concrete `@Service` classes unless multiple implementations or SPI boundaries exist.
4. **N+1 Queries & Missing Boundary Batching**: Calling `mapper.selectById(...)` inside a `for` loop over query results; batch with `IN (...)` or a single join query.
5. **Type Evasion**: `@SuppressWarnings("unchecked")` or raw `Map<String, Object>` bags passing through services -> replace with typed `record` DTOs.

#### Recommended Java Repo Gates
- **Architecture**: `ArchUnit` (`TNG/ArchUnit`) locking `Controller -> Service -> Repository` and forbidding cycles (`slices().matching(...).should().beFreeOfCycles()`).
- **Complexity & Static**: `PMD` (`CognitiveComplexity` rule <= 15), `SpotBugs`, `ErrorProne`.
- **Mutation Testing**: `Pitest` (`org.pitest:pitest-maven` / `gradle-pitest-plugin`) on core domain services.

---

### 2. JavaScript / TypeScript (Node, React, Frontend & Backend)

#### Common AI Slop & Anti-Patterns
1. **Boolean Traps & Positional Arg Creep**:
   ```ts
   // ❌ Slop: Unreadable positional booleans at call sites
   sendNotification(user, "Done", true, false, true);

   // ✅ High Taste: Named options object + union mode
   interface NotifyOptions {
     priority: "normal" | "urgent";
     channels: ("email" | "push")[];
     persistHistory?: boolean;
   }
   sendNotification(user, "Done", { priority: "urgent", channels: ["push"], persistHistory: false });
   ```
2. **Type Evasion (`as any`, `@ts-ignore`)**:
   - Replace `x as any` / `: any` with `unknown` + type-guard narrowing, discriminated unions, or generics.
   - Replace `@ts-ignore` with concrete type fixes (or `@ts-expect-error` with an inline ticket/reason when unavoidable).
3. **Silent Async Catches & Over-Defensive Optional Chaining**:
   - Flag `try { await save(); } catch (err) { console.error(err); }` that lets UI/API proceed as if `save()` succeeded.
   - Flag `user?.profile?.id` when `User` type already guarantees non-null `profile`.
4. **Shallow Modules, Circular Barrels & Cross-Feature Private Imports**:
   - Flag barrel `index.ts` re-export cycles (`a.ts -> ./index -> b.ts -> a.ts`).
   - Flag `import { calc } from "../billing/internal/math"` — import only from `../billing` public entrypoint.
5. **React / UI Slop**:
   - Flag `useEffect` synchronizing derived state from props/state (compute inline during render instead).
   - Flag nested JSX ternaries (`a ? (b ? <X/> : <Y/>) : <Z/>`) -> early returns or extracted sub-components.

#### Recommended JS/TS Repo Gates
- **Complexity & Slop**: `eslint-plugin-sonarjs` (`sonarjs/cognitive-complexity: ["error", 15]`), `eslint-plugin-slop` / `eslint-plugin-no-comment-slop`.
- **Duplication**: `jscpd` (`--threshold 0` on new PRs).
- **Architecture & Dead Code**: `dependency-cruiser` (cycles + layer/internal guards) + `knip` (unused files/exports/deps).
- **Mutation Testing**: `Stryker` (`@stryker-mutator/core`).

---

### 3. SQL (MySQL / PostgreSQL / MyBatis / Migrations)

#### Common AI Slop & Anti-Patterns
1. **Cognitive Complexity > 15 (Subquery & Nested `CASE` Pyramids)**:
   ```sql
   -- ❌ Slop: Deeply nested correlated subqueries & nested CASE
   SELECT u.id, (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id AND o.status = 'PAID')
   FROM users u
   WHERE u.id IN (SELECT user_id FROM events WHERE CASE WHEN type = 'A' THEN ... END > 0);

   -- ✅ High Taste: Flat, named CTE pipeline
   WITH paid_order_counts AS (
     SELECT user_id, COUNT(*) AS paid_count
     FROM orders
     WHERE status = 'PAID'
     GROUP BY user_id
   ),
   active_users AS (
     SELECT DISTINCT user_id FROM events WHERE event_type = 'A'
   )
   SELECT u.id, COALESCE(p.paid_count, 0) AS paid_count
   FROM users u
   JOIN active_users a ON a.user_id = u.id
   LEFT JOIN paid_order_counts p ON p.user_id = u.id;
   ```
2. **Non-Sargable `WHERE` Index Killers**:
   - Flag `WHERE DATE(created_at) = '2026-01-01'` or `WHERE LOWER(email) = ...` or `WHERE COALESCE(status, 'NEW') = 'NEW'` -> rewrite to index-friendly range predicates (`created_at >= '2026-01-01' AND created_at < '2026-01-02'`).
3. **`SELECT *` & `SELECT DISTINCT` Band-Aids**:
   - Flag `SELECT *` in application queries and views (breaks column contract & prevents covering index scans).
   - Flag `SELECT DISTINCT ... JOIN ...` where AI masked a 1-to-N join explosion instead of using `EXISTS (...)` or aggregating first.
4. **Defensive `LEFT JOIN` + `COALESCE` Masking Corrupt Data**:
   - When an entity's foreign key is `NOT NULL` by schema, `LEFT JOIN` + `COALESCE(parent.name, 'Unknown')` hides orphan corruption instead of using `INNER JOIN`.
5. **Injection & View Cycles**:
   - `[BLOCKER]` Raw `${...}` string interpolation in SQL / MyBatis XML -> enforce `#{...}` / bind parameters.
   - `[BLOCKER]` Circular `CREATE VIEW` dependencies or 4-layer stacked views -> flatten into explicit CTEs or materialized summary tables.

#### Recommended SQL Repo Gates
- **Linter & Style**: `sqlfluff` (`sqlfluff lint` enforcing explicit columns, aliasing, CTE readability).

---

## Tier 2: AI-Favorite Secondary Languages

### 4. Python
- **Boolean Traps & Configs**: Enforce keyword-only booleans via `*` (`def sync_items(src: Path, *, dry_run: bool = False):`) or `@dataclass(frozen=True)` options objects. Never pass positional `bool` switches.
- **Type Evasion & Silent Catches**: Flag `dict[str, Any]` bags crossing module boundaries, bare `# type: ignore` without `[error-code]`, and `except Exception: pass` / `return None`.
- **Tools**: `rohaquinlop/complexipy` (Cognitive Complexity <= 15), `tach-org/tach` (module boundaries & zero cycles), `vulture` (dead code), `mutmut` (mutation tests).

### 5. Go
- **Error Discipline & Interfaces**: Flag `_ = err` or swallowed errors without `fmt.Errorf("...: %w", err)` context; define interfaces at the **consumer** package rather than Java-style producer `IService` interfaces.
- **Package Boundaries**: Avoid `utils/` or `common/` dumping grounds that cause import cycles; keep internal helpers in `internal/`.
- **Tools**: `golangci-lint` (enable `gocognit` <= 15, `wrapcheck`, `forbidigo`).

### 6. Rust
- **Safety & Abstraction Slop**: Flag `.unwrap()` / `.expect()` outside tests/CLI entrypoints, gratuitous `.clone()` to appease the borrow checker instead of borrowing `&T`, and unnecessary `Box<dyn Trait>` where a small `enum` suffices.
- **Tools**: `cargo clippy -- -W clippy::cognitive_complexity`.

### 7. Shell / PowerShell
- **Silent Failure Traps**: Flag `|| true`, `2>/dev/null` hiding real command errors, `-ErrorAction SilentlyContinue` on destructive/state-changing commands, missing `set -euo pipefail` (Bash) or `$ErrorActionPreference = 'Stop'` (PowerShell), and unquoted `$VAR` expansions.
