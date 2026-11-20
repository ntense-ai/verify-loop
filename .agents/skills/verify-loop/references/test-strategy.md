# High-Leverage Test Engineering (`test-strategy`)

Generating tests, benchmarks, specs, and end-to-end runs is cheap for an agent—and once generated, they execute deterministically as software without burning repeated token budgets. However, **not all tests are equal**. Prioritize the three test types below and enforce strict speed and anti-flakiness discipline.

---

## 1. End-to-End (E2E) Tests — First Priority

If you can invest in only one test type, choose **end-to-end tests** that exercise complete user flows (e.g., add item to cart -> checkout -> confirmation screen).

### Why E2E Beats Isolated Unit Tests for Agent Work
- **Hard to fool**: Either the full feature works or it fails; there are no hand-crafted mocks hiding contract mismatches.
- **Shared blind-spot protection**: An agent can pass unit tests it wrote while breaking the real feature because both the implementation and the unit test share the same incomplete mental model.
- **Diagnostic evidence**: Instead of an opaque `"Timeout after 30s"`, a good E2E harness captures a screenshot, DOM/accessibility snapshot, HTTP response body, and server log slice pinpointing what broke.

### Giving the Agent Hands and Eyes on the Running System
Always boot and drive the real system matching the project surface:
- **Web applications**: Boot the dev/preview server, drive real flows via browser automation / Chrome DevTools Protocol (CDP) / Playwright, assert visible state, console cleanliness, network status codes, and capture screenshots on both failure and key milestones.
- **Mobile applications**: Boot iOS Simulator / Android Emulator (via CLI or mobile MCP tools), install the build, drive gestures/navigation, and inspect accessibility hierarchies and screenshots.
- **Desktop applications**: Launch the built binary in a test profile, drive UI interactions, and verify window state/logs.
- **Backend / APIs / CLIs**: Boot the service locally against ephemeral state (SQLite/containers/temp dirs), send real HTTP/gRPC/CLI requests, verify response payloads, persisted side effects, and absence of sensitive data or errors in logs.

### Feature Maps as Guide & Grading Rubric
Maintain a `FEATURE_MAP.md` (see [`../resources/FEATURE_MAP.template.md`](../resources/FEATURE_MAP.template.md)) listing user-visible features from the user's perspective:
- Entry URL, route, or CLI command
- Selectors, keyboard shortcuts, and test inputs
- Expected observable outcome and evidence artifact to capture

Use `FEATURE_MAP.md` both to **select which E2E flows to run** for a diff and as the **rubric** for a separate grader pass.

---

## 2. Property-Based Tests — Constraints That Cannot Break

Example-based tests check only what the author thought might go wrong. **Property-based tests** generate hundreds or thousands of adversarial inputs without the bias of the agent that wrote the implementation. They are especially valuable during rewrites when written specs have drifted from production reality.

### Recommended Libraries by Ecosystem
| Ecosystem | Library |
| :--- | :--- |
| TypeScript / JavaScript | `fast-check` |
| Python | `hypothesis` |
| Rust | `proptest` |
| Go | `pgregory.net/rapid` or `testing/quick` |
| Java / Kotlin | `jqwik` |

### Four Canonical Properties to Look For in Every Codebase
1. **Round-Trip Encoding (`decode(encode(x)) == x`)**
   - Serialization, parsers, wire protocols, URL/query builders, DTO mappers, compression.
2. **Invariants (`P(state)` always holds)**
   - Ledger balances never go negative; tree/graph invariants hold after mutations; output permissions never exceed input scope; logs never contain raw secrets/PII.
3. **Idempotency (`f(f(x)) == f(x)`)**
   - Database/config migrations, text/AST formatters, state normalization, webhook/message retry handlers, upsert endpoints.
4. **Equivalence / Reference Oracle (`optimized(x) == naive_correct(x)`)**
   - Fast batch/cached/vectorized algorithms compared against a trivial, obviously-correct in-memory reference model.

### Execution Discipline
- Configure fast default run counts (e.g., 100–250 examples locally) so property suites stay fast in Tier 1, with higher example counts in pre-merge/CI profiles.
- Always log the failing **shrunk counterexample** and **PRNG seed** so failures reproduce deterministically in one command.

---

## 3. Differential Tests — The Old System Is the Oracle

Use **differential testing** for large-scale migrations, parser/compiler rewrites, dependency upgrades, and performance refactors where external behavior must stay identical.

### Workflow
1. **Source realistic inputs**:
   - Property/fuzz generators covering edge-case syntax and payloads, **or**
   - Sanitized samples of real production traffic / historical fixtures.
2. **Run old and new side-by-side**:
   - Use [`../scripts/diff_oracle.py`](../scripts/diff_oracle.py) (or an in-process test harness comparing legacy vs new modules, or a `git worktree` of the base branch vs HEAD).
3. **Normalize nondeterministic noise**:
   - Strip or canonicalize volatile fields: timestamps, random UUIDs, ephemeral ports, unordered JSON keys (`--sort-json`), irrelevant whitespace (`--ignore-whitespace`).
4. **Track intentional changes explicitly**:
   - Maintain a version-controlled `approved-diffs.json` allowlist documenting every intentional behavioral difference (`case_id`, `difference_sha256`, `reason` from the reviewed JSON report). Any unapproved divergence fails the check.

---

## 4. Test Speed & Anti-Flakiness Protocol

### Failure Mode 1: Slow Suites
Slow tests cause agents to batch too many edits before testing, making root-cause isolation expensive.
- **Tier checks by cost**:
  1. **Sub-second / few-second loop** during active coding: single-file typecheck, lint, targeted unit + property test.
  2. **Pre-commit loop** (before `git commit` / PR): full affected test suite, diff-triggered reliability checks, and E2E smoke harness.
- Invest directly in making the repo's smoke harness faster (reuse seeded state snapshots, parallelize independent flows, avoid redundant cold builds).

### Failure Mode 2: Flaky Tests
Flaky tests train agents to re-run commands blindly instead of fixing defects.

**Hard Prohibitions**:
- Never add `sleep` / `waitForTimeout` / arbitrary delays to "stabilize" a test.
- Never mark a test `.skip`, `xit`, `@pytest.mark.skip`, `#[ignore]`, or retry-until-pass.

**Required Resolution**:
1. Reproduce deterministically (run 10–20 iterations or under CPU/network contention if race-suspected).
2. Fix the structural cause:
   - Replace fixed sleeps with explicit readiness condition polling or event hooks.
   - Isolate shared state (unique temp dirs, ephemeral DB schemas, dynamic ports `port=0`).
   - Freeze or inject clocks, timezones, and random seeds.
3. **If the root cause cannot be resolved within 3 focused attempts**: **Delete the flaky test** and record a clear written explanation in the commit message / PR description (and `REVIEW.md` / test notes if coverage needs a replacement design). A deleted test with a documented gap is strictly safer than a flaky test that corrupts agent incentives.
