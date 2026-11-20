# Bootstrapping, Gating & Closing the Verification Loop (`closing-the-loop`)

Treat your verification layer like production software: start new gates safely in observe mode, turn real misses into diff-triggered rules validated against false positives, evaluate check recall on real incidents, and prune stale rules routinely.

---

## 1. Bootstrapping Verification in a Repository

Copy and tailor the starter templates from [`../resources/`](../resources/) as close to the relevant code (or repo root) as appropriate:

1. **`REVIEW.md`** (from [`../resources/REVIEW.template.md`](../resources/REVIEW.template.md))
   - Written from the code owner's perspective: must-fix invariants, architectural boundaries, and domain hazards.
2. **`RELIABILITY_CHECKLIST.md`** (from [`../resources/RELIABILITY_CHECKLIST.template.md`](../resources/RELIABILITY_CHECKLIST.template.md))
   - Diff-triggered checks (`Trigger Paths`, `Trigger Diff Regex`, `Check`, `Provenance`) evaluated automatically by `verify_guard.py triage`.
3. **`FEATURE_MAP.md`** (from [`../resources/FEATURE_MAP.template.md`](../resources/FEATURE_MAP.template.md))
   - User-visible flows, selectors/shortcuts/commands, and expected outcomes for E2E smoke testing and rubric grading.
4. **Commit Gate Hook (`.agents/hooks.json` + `.verify-loop.json`)**
   - Copy [`../resources/verify-loop.config.json`](../resources/verify-loop.config.json) to `<repo>/.verify-loop.json` (starts in `"gateMode": "observe"`).
   - Merge [`../resources/hooks.template.json`](../resources/hooks.template.json) into `<repo>/.agents/hooks.json`.
   - Commit these files to the default branch so `verify_guard.py` and `commit_gate_hook.py` load them from an immutable base ref.

---

## 2. Progressive Commit Gate Rollout (`off` -> `observe` -> `enforce`)

Never turn on a blocking commit gate cold without measuring false positives first.

1. **Stage 1 — `observe` mode (Default)**:
   - Set `"gateMode": "observe"` in `.verify-loop.json`.
   - `commit_gate_hook.py` never blocks commits, but logs every commit that *would* have been blocked to the per-repository state log (`/tmp/verify-loop-state/<repo>-<hash>/gate-observe.log`, or `$VERIFY_LOOP_STATE_DIR/gate-observe.log`).
   - Inspect observed events periodically:
     ```bash
     python3 .agents/skills/verify-loop/scripts/commit_gate_hook.py --inspect-log
     python3 .agents/skills/verify-loop/scripts/verify_guard.py audit-commits -n 50
     ```
   - Tune workflows or bypass conventions until false positives drop near zero.
2. **Stage 2 — `enforce` mode**:
   - Update `"gateMode": "enforce"` on the base branch.
   - Unverified commits in a session without `verify_guard.py record --status pass` are blocked **unless**:
     - The commit message carries an explicit explanation (`Verified-By: none (<concrete reason>)`), **or**
     - Commits have already been blocked **twice** in the current session (`blocked_commits_count >= 2`), triggering the **circuit breaker** on the 3rd attempt so an imperfect agent session never deadlocks permanently.

---

## 3. Turning Missed Bugs into Rules (Two Formal Workflows)

When a bug escapes to code review, causes a PR revert, or triggers an incident, close the loop systematically rather than pasting vague advice into rules files.

### Method A: Pattern Extraction from Reviews & Reverts
A candidate rule qualifies **only** if:
1. The same issue pattern occurred in **at least 2 PRs or reverts**, AND
2. Its trigger is **observable directly from a `git diff`** (path glob or diff syntax/token pattern).

**3-Agent Disproof Trial** (use `run_workflow` `parallel(...)` or 3 parallel `research` subagents):
1. Draft the candidate rule (`Trigger` + `Check` + `Provenance`).
2. Spawn **3 independent agents** instructed to **disprove or break** the candidate rule against clean historical commits in the repo:
   - Agent 1 searches for clean, valid diffs where the rule triggers false positives.
   - Agent 2 tests whether the rule wording is ambiguous or unactionable.
   - Agent 3 checks redundancy/conflict against existing `REVIEW.md` and `RELIABILITY_CHECKLIST.md` rules.
3. Refine or discard if disproved; otherwise open a dedicated PR for human code-owner approval.

### Method B: Incident Replay (3 Bug-Finder Agents)
When analyzing a past production incident tied to a specific PR:
1. Check out / reconstruct the repository at the exact pre-incident PR commit and diff.
2. Spawn **3 independent bug-finder subagents** against that diff (without telling them the answer).
3. Evaluate detectability:
   - If **at least 2 of 3** identify the bug, the class of bug is reliably detectable by an agent.
   - If the regular verification/review pass missed it—especially on a small diff—add a targeted entry to `REVIEW.md` or `RELIABILITY_CHECKLIST.md` citing the incident/PR in `Provenance`.

---

## 4. Evaluating the Verification System ("Reviewing the Reviewers")

Measure check quality against real engineering outcomes rather than gut feel:

1. **Production Signals**:
   - Post-merge revert rate
   - Maintainer rejection rate prior to merge
   - False-positive rate on a **size-matched control set** of merged changes that remained clean/stable
2. **Historical Incident Eval Suite**:
   - Pair historical incident/revert diffs with a **one-sentence answer key** stating the exact defect.
   - Split examples strictly into a **tuning set** (used while iterating on `REVIEW.md` / prompts) and a **held-out test set** (used solely to score changes).
   - Award points **only** to findings that accurately describe the answer-key bug, while penalizing noisy flags on clean size-matched controls.
3. **Stage-Wise Recall Tracking**:
   - Measure recall separately at three stages:
     1. **Raw findings** (initial candidate issues surfaced)
     2. **Post-verification** (after self-check / repro filtering)
     3. **Final review output** (reported `Important` findings)
   - Comparing all three reveals whether valid bugs were never spotted or were mistakenly filtered out downstream.
   - Stop tuning recall as soon as marginal gains begin overfitting to the tuning set rather than generalizing to held-out incidents.

---

## 5. Routine Hygiene & Pruning

Test suites, smoke harnesses, `REVIEW.md`, and `RELIABILITY_CHECKLIST.md` grow monotonically if left unattended, slowing runs and diluting model attention.

Run a periodic pruning pass:
- **Delete or merge** checklist/review rules whose gated APIs, schemas, or subsystems no longer exist, or that have zero true-positive hits and high false-positive friction.
- **Profile test runtime**: optimize or split slow tests out of Tier 1; consolidate redundant E2E flows covered by faster property/integration invariants.
- **Purge flaky tests** whose root cause remains unresolved, leaving a concise commit note explaining why the test was removed.
