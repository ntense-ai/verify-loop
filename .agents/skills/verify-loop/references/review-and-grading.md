# Stateful Review & Separate Rubric Grading (`review-and-grading`)

Verification improves substantially when an independent pass grades the output against an explicit rubric—especially on complex or structured tasks (up to +10 percentage points in internal evaluations, with the largest gains on hard tasks). Follow this guide to run high-signal, low-noise review and grading loops.

---

## 1. One Tuned Reviewer Across Stateful Rounds

Do **not** stack multiple overlapping reviewers on top of each other. Adding a second expensive reviewer catches less and creates more false-positive churn than tuning **one** primary reviewer guided by `REVIEW.md` and running it statefully across rounds.

### Stateful Multi-Round Protocol
1. **Round 1 (Initial Review)**:
   - Evaluate the committed/frozen diff against the base-branch `REVIEW.md`, `FEATURE_MAP.md`, and matched `RELIABILITY_CHECKLIST.md` items.
   - Produce findings tagged strictly by actionability (`Important`, `Nit`, `Pre-existing`).
2. **Round 2+ (Delta Verification)**:
   - Never re-review the entire codebase from scratch without memory.
   - Pass the **prior round's findings**, **the fixes applied**, and **the incremental diff since the prior round** (or message the same reviewer subagent via `send_message` so its conversation state persists).
   - Verify whether earlier `Important` items are genuinely resolved and inspect only the newly touched lines and their ripple effects.

### Using a Separate Grader Subagent in Antigravity
For complex features, structured file generation, or multi-file refactors:
1. Invoke a dedicated read-only subagent (`invoke_subagent` with `TypeName: "research"` or `TypeName: "self"`, `Role: "Rubric Grader"`).
2. Supply in its prompt:
   - Base ref and target commit/diff (`git diff <base>`)
   - Exact rubric loaded from the **base branch** (`git show <base>:REVIEW.md`, `git show <base>:FEATURE_MAP.md`)
   - Runtime verification evidence already collected (test outputs, screenshots, E2E results)
3. When fixes are needed, keep messaging the **same** subagent `conversationId` via `send_message` for Round 2 and Round 3 so rounds build on each other cleanly.

---

## 2. Check Integrity: Never Trust Self-Modified Gates

If a change can alter the rule or test that judges it, the verification loop is broken.

1. **Always read gating docs from the base branch**:
   - Run `python3 .agents/skills/verify-loop/scripts/verify_guard.py triage --include-review-text --json` or `git show <base>:REVIEW.md`.
   - Never rely on a working-tree `REVIEW.md` or `RELIABILITY_CHECKLIST.md` if the PR itself edited those files.
2. **Audit test modifications in the diff**:
   - Flag any deleted test assertions, broadened error catches, weakened thresholds, or newly added skips/sleeps.
   - Only accept changes to tests/checks when they **tighten** assertions, add coverage for new behavior, or intentionally update fixtures for human-approved spec changes.
3. **Pin reviews to immutable commits (or verified tree fingerprints)**:
   - Reserve formal review sign-off for a fixed commit or frozen working tree (`tree_fingerprint`).
   - If files change while or after a review runs, the prior review result is invalidated and a delta pass must run on the updated state.

---

## 3. Strict Finding Taxonomy

People care not just whether a comment is technically true, but **whether the change must be blocked for it**. Tag every finding with one of three labels:

| Tag | Meaning | Required Agent Action |
| :--- | :--- | :--- |
| **`Important`** | Must-fix defect: broken user flow, data corruption, security/privacy leak, race condition, crash, or violated `REVIEW.md` invariant. | **Block commit/PR.** Fix and re-verify (subject to the 3-round escalation cap). |
| **`Nit`** | Minor readability, naming, or non-critical polish with zero behavioral risk. | Fix inline only if trivial and zero-risk; never spin extra verification cycles on nits. |
| **`Pre-existing`** | Bug or tech debt already present on the base branch outside the author's causal change. | **Do not hold the current change hostage.** Note it briefly for follow-up without blocking or forcing unrelated churn. |

---

## 4. Escalating cleanly to Humans (After 3 Failed Rounds)

Code owners should never have to reverse-engineer why an automated check or agent escalated. When a check or `Important` review failure persists after **3 rounds**, produce a concise **Escalation Brief**:

1. **Why Escalated**: Which check/rule triggered and which code-owner invariant is at stake (include checklist `Provenance` if applicable).
2. **Evidence**: Exact command, failing assertion, log excerpt, or screenshot path.
3. **3-Round Ledger**:
   - *Round 1*: Hypothesis -> change -> outcome
   - *Round 2*: Hypothesis -> change -> outcome
   - *Round 3*: Hypothesis -> change -> outcome
4. **Decision Needed**: Concrete options or architectural clarification needed from the human owner.
