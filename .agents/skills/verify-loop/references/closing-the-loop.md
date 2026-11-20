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
4. **Project `verify` skills** (from [`../resources/verify-skill.template.md`](../resources/verify-skill.template.md))
   - One skill per surface that codifies how to boot, drive, inspect, and tear down the running app. Place it as close to the code it covers as the agent's skill discovery allows. Step 4 of the pre-commit loop runs it.
   - Treat it as the smoke-test harness: time it, run it several times from a cold start after each edit, and fix flakes at the root.
5. **Code-owner checks**
   - The owner of an area writes its `REVIEW.md` rules and checklist items, then refines them until they flag what the owner would flag. Each rule states the invariant it protects, so an escalation can explain why without the reviewer reconstructing it from the diff.
6. **Optional Git commit gate**
   - Install the commit-msg template in the repository's active Git hooks. Set the repository-local Git setting `verifyloop.runtime` to the absolute path of a separately installed copy of the reviewed scripts. Integrate the check into any existing commit-msg hook.
   - Claude Code and Antigravity also support an earlier PreToolUse check. For Claude Code, merge [`../resources/claude-settings.template.json`](../resources/claude-settings.template.json) into `.claude/settings.local.json` or `~/.claude/settings.json`. For Antigravity, use [`../resources/hooks.template.json`](../resources/hooks.template.json) in its user-controlled hook settings. In both cases set the command to the absolute trusted script path. Do not commit the hook to a settings file that the changes it judges can edit.

## 2. Progressive commit gate rollout

Start with `git config --local verifyloop.gateMode observe`. Inspect events with `commit_gate_hook.py --inspect-log` and compare them with `verify_guard.py audit-commits`. To enable enforcement, set the local mode to `enforce`. A named base branch's committed configuration supplies the default when the local setting is absent. A bootstrap checkout defaults to observation.

Git validates the final staged tree and the message's verification trailer. A documented `Verified-By: none (reason)` trailer records an explicit exception, so an agent is never stuck: it can always commit by stating why the change is unverified, and the audit still shows it. The PreToolUse preflight retains its two-block session circuit breaker. The Git hook evaluates the actual commit independently.

## 3. Turning Missed Bugs into Rules (Two Formal Workflows)

When a bug escapes to code review, causes a PR revert, or triggers an incident, close the loop systematically rather than pasting vague advice into rules files.

### Method A: Pattern Extraction from Reviews & Reverts
A candidate rule qualifies **only** if:
1. The same issue pattern occurred in **at least 2 PRs or reverts**, AND
2. Its trigger is **observable directly from a `git diff`** (path glob or diff syntax/token pattern).

**Independent review of candidate rules** (use three reviewers when available and authorised):
1. Draft the candidate rule (`Trigger` + `Check` + `Provenance`).
2. Ask independent reviewers to **disprove or break** the candidate rule against clean historical commits in the repo:
   - Agent 1 searches for clean, valid diffs where the rule triggers false positives.
   - Agent 2 tests whether the rule wording is ambiguous or unactionable.
   - Agent 3 checks redundancy/conflict against existing `REVIEW.md` and `RELIABILITY_CHECKLIST.md` rules.
3. If only one reviewer is available, examine all three questions and label the result as a single review. Refine or discard if disproved; otherwise open a dedicated PR for human code-owner approval.

### Method B: Incident Replay (3 Bug-Finder Agents)
When analyzing a past production incident tied to a specific PR:
1. Check out / reconstruct the repository at the exact pre-incident PR commit and diff.
2. When three independent reviewers are available, give each the same diff without the answer. Otherwise record the available results and leave the independent threshold unevaluated.
3. Evaluate detectability:
   - If **at least 2 of 3** identify the bug, the class of bug is reliably detectable by an agent.
   - If the regular verification/review pass missed it—especially on a small diff—add a targeted entry to `REVIEW.md` or `RELIABILITY_CHECKLIST.md` citing the incident/PR in `Provenance`.

---

## 4. Feeding Agent Failures Back into Agent Behaviour

Rules in `REVIEW.md` catch bugs in the code. Some failures come from the agent's habits instead, such as repeatedly failing the same check or forgetting the same step. When the same check fails for the same reason in two or more tasks, change the agent's environment so the mistake stops recurring:

1. **Prefer a hook when the trigger is mechanical.** For example, a check that scans logs for user data can run as a PreToolUse or PostToolUse hook, so the agent gets the failure while it is still working.
2. **Otherwise add one concrete instruction** to the agent's project instructions (`AGENTS.md`, `CLAUDE.md`, or `GEMINI.md`) next to the code it concerns. Name the check, the failure, and what to do instead. Cite the task or PR that prompted it.
3. **Make the final check run automatically.** In Claude Code, a `Stop` hook can run the fast test suite when the agent finishes and return failures to it, or a `/goal` can require a passing suite. Guard a `Stop` hook against loops by honouring the `stop_hook_active` input field.
4. **Confirm it worked.** Check that the failure stops appearing in later tasks' `record --status fail` entries. Remove the instruction or hook if it did not help.

Instructions and feedback hooks in this section guide the agent rather than decide whether a change passes, so they may live in the working tree. Anything that gates a change, including the commit gate, stays out of reach of that change.

---

## 5. Evaluating the Verification System ("Reviewing the Reviewers")

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

## 6. Routine Hygiene & Pruning

Test suites, smoke harnesses, `REVIEW.md`, and `RELIABILITY_CHECKLIST.md` grow monotonically if left unattended, slowing runs and diluting model attention.

Run a periodic pruning pass:
- **Delete or merge** checklist/review rules whose gated APIs, schemas, or subsystems no longer exist, or that have zero true-positive hits and high false-positive friction.
- **Prune agent instructions and hooks** from section 4 once the habit they corrected no longer appears.
- **Profile test runtime**: optimize or split slow tests out of Tier 1; consolidate redundant E2E flows covered by faster property/integration invariants.
- **Purge flaky tests** whose root cause remains unresolved, leaving a concise commit note explaining why the test was removed.
