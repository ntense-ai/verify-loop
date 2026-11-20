---
name: verify-loop
description: >-
  Run evidence-backed verification loops, end-to-end smoke tests, diff-triggered
  reliability checks, stateful rubric reviews, and pre-commit verification gates.
  Use this skill before committing or finishing code changes, when
  writing or upgrading E2E, property-based, or differential tests, when
  troubleshooting flaky tests, or when bootstrapping and evolving a repository's
  verification harness, REVIEW.md rules, and commit gates.
---

# Verification Loop (`verify-loop`)

Equip agent runs to verify changes **with concrete runtime evidence** so humans review evidence and definitions of "done" instead of auditing every line of every diff by hand.

## Using the skill in your agent

Ask “Use the verify-loop skill on these changes.” The workflow needs Git, Python 3.9 or later, and a terminal in the target repository. Use the interpreter available on your system (for example, `python3` or `py -3`). Use the host's available browser, simulator, or CLI harness for runtime evidence.

The examples assume a project installation in `.agents/skills/verify-loop`. For a personal installation or another discovery directory, resolve `scripts/`, `resources/`, and `references/` beside the loaded `SKILL.md`. Run the helpers by their resolved paths with the target repository as the working directory. An explicit `--repo` also selects the target for the guard.

Codex, Cursor, Copilot CLI, Gemini CLI, and Antigravity support the project directory above. Claude Code discovers the project skill through `.claude/skills/verify-loop`. This repository supplies a relative directory link there. For checkouts without symlink support, install the full skill directory at the Claude path. In Claude Code, invoke `/verify-loop`.

The optional Git commit-msg hook runs across Git clients. Claude Code and Antigravity also support an earlier tool-call check through a PreToolUse hook. Use a separate reviewer only when available and authorised. Otherwise review the evidence in a fresh pass and record that it was a self-review.

## Core Operating Invariants

1. **Verification Means Running the Change**: Passing unit tests or reading diffs is insufficient. Boot and exercise the real affected system (web browser/CDP, mobile simulator, desktop app, CLI, or local service/API harness) and capture observable evidence.
2. **Changes Cannot Modify Their Own Checks**: Never judge a change using gating files modified by that same change. Always load `REVIEW.md`, `RELIABILITY_CHECKLIST.md`, and gate configs from the **base branch** (`git show <base>:...`), and flag any diff that deletes, skips, or weakens assertions.
3. **Cheap Checks First**: Run fast local checks (`typecheck`, `lint`, `build`, fast unit/property suites) before expensive E2E suites, reviews, or commits.
4. **Three-Strike Escalation Rule**: Iterate to fix failing checks, carrying prior round context forward. If a check still fails after **3 fix rounds**, **STOP immediately** and escalate to a human with a structured summary of all 3 attempts.
5. **Zero Flake Workarounds**: Never add `sleep`/arbitrary delays, never mark failing tests `.skip`/`xfail`/`#[ignore]`, and never retry until green. Either fix the deterministic root cause or **delete the flaky test and document why**.
6. **Stamp Every Commit**: Record verification provenance on every commit with a `Verified-By:` trailer (including `Verified-By: none (<reason>)` when verification genuinely cannot apply) so unverified commits remain queryable.
7. **Close the Loop**: When a miss or a repeated agent failure recurs, turn it into a base-branch rule, a hook, or an `AGENTS.md`/`CLAUDE.md` instruction so it stops recurring. Prune rules that no longer catch anything.

---

## Mode Router

Select the workflow matching your current task:

| Goal | Mode | Reference / Tooling |
| :--- | :--- | :--- |
| Verify pending changes before finishing, committing, or opening a PR | **Mode 1: Pre-Commit Verification Loop** | Steps below + [`scripts/verify_guard.py`](./scripts/verify_guard.py) |
| Design or add E2E, property-based, or differential tests, or eliminate flaky tests | **Mode 2: High-Leverage Test Engineering** | [`references/test-strategy.md`](./references/test-strategy.md) + [`scripts/diff_oracle.py`](./scripts/diff_oracle.py) |
| Run a separate rubric grader or multi-round stateful code review | **Mode 3: Stateful Review & Rubric Grading** | [`references/review-and-grading.md`](./references/review-and-grading.md) |
| Bootstrap repo verification files and a project `verify` skill, roll out commit gates (`observe` -> `enforce`), turn missed bugs and repeated agent failures into rules, evaluate check recall, or prune rules | **Mode 4: Bootstrap, Gate & Close the Loop** | [`references/closing-the-loop.md`](./references/closing-the-loop.md) + [`resources/`](./resources/) |

---

## Mode 1: Pre-Commit Verification Loop (Default Runbook)

Execute these steps in order before committing or declaring an implementation task done.

### Step 1: Check Integrity & Diff-Triggered Triage

Run the verification guard to inspect the diff against the base branch, detect tampered checks or newly introduced test sleeps/skips, load base-branch rubrics, and match diff-triggered reliability items:

```bash
python3 .agents/skills/verify-loop/scripts/verify_guard.py triage
```

- **If `integrity_issues` is non-empty**: Inspect every flagged item immediately. Revert any unauthorized edits to gating files (`REVIEW.md`, `RELIABILITY_CHECKLIST.md`, hooks) or weakened/skipped tests unless a human explicitly requested that rubric/test change.
- **Keep `matched_checks` in scope**: Evaluate **only** the reliability checklist items whose path or diff triggers matched (typically 3–10 items), rather than scanning unrelated historical rules.

### Step 2: Tier 1 — Cheap Local Checks First

For commit verification, stage the intended files. From a worktree matching that staged snapshot, run:

```bash
python3 .agents/skills/verify-loop/scripts/verify_guard.py begin --new-task
```

Keep evidence outside the worktree or in ignored output directories. A separate worktree lets you verify a candidate alongside unfinished edits. Set a distinct `VERIFY_SESSION_ID` for each concurrent task. An inspection without a commit can use triage and runtime checks directly.

Run the project's fast deterministic checks before booting heavy environments:

1. **Static & Build**: Formatting, linter, typechecker, and build/compile check.
2. **Fast Automated Suites**: Targeted unit tests, **property-based tests** (invariant/round-trip/idempotency/equivalence), and **differential tests** (if refactoring or migrating behavior—see [`scripts/diff_oracle.py`](./scripts/diff_oracle.py)).

### Step 3: Tier 2 — Diff-Triggered Reliability Verification

For each item returned in `matched_checks` from Step 1:

1. Execute the specific verification action described in its **Check** field (e.g., loading legacy fixtures when session/message schemas change, verifying idempotent rollback when migrations change).
2. Record concrete pass/fail evidence referencing the item ID (e.g., `RC-001`).

### Step 4: Tier 3 — Run the Real System End-to-End

Static checks and isolated unit tests can pass while the user-facing feature is broken. Exercise the running change the way a human or QA engineer would:

- **Locate Repo Smoke Harness / Feature Map**: Prefer a project `verify` skill nearest the changed code (for example `.claude/skills/verify-*/SKILL.md` or `.agents/skills/verify-*/SKILL.md`), then `FEATURE_MAP.md` or a smoke script. If the area has none and you had to work out how to boot it, offer to codify those steps with [`resources/verify-skill.template.md`](./resources/verify-skill.template.md).
- **Drive & Inspect by Surface**:
  - **Web UI**: Boot local dev/preview server; drive real user flows via browser/CDP/Playwright; inspect rendered DOM, console errors, network statuses, and screenshots.
  - **Mobile / Desktop**: Boot simulator/emulator or desktop build; execute the end-to-end flow and inspect visual state and logs.
  - **Backend / API / CLI**: Start the local service or invoke the compiled binary against realistic inputs/fixtures; assert response payloads, side effects, and clean logs.
- If a critical user flow or invariant lacks automated coverage, add an E2E, property, or differential test following [`references/test-strategy.md`](./references/test-strategy.md).

### Step 5: Bounded Fix Loop (Max 3 Rounds)

When any check in Steps 2–4 fails:

1. Record the initial failure with `record --status fail --checks <name> --summary "<symptom>"`.
2. Diagnose and apply the fix, stage it, then run `begin` again in the same session. Use `--new-task` only for an independent task.
3. Rerun the check. On failure use `record --status fail --after-fix --checks <name> --summary "<attempt and result>"`. On success use `record --status resolved --checks <name>`. Exercise all checks claimed in the final trailer against the final candidate.
4. When the same check fails after three completed fixes, stop and escalate with:
   - Exact failing check and error/screenshot evidence
   - What Rounds 1, 2, and 3 each attempted and why each fell short
   - The specific code-owner decision or domain context needed to unblock

### Step 6: Focused Rubric / Review Pass

For non-trivial changes, run **one** focused reviewer pass against the base-branch `REVIEW.md` and `FEATURE_MAP.md` (read [`references/review-and-grading.md`](./references/review-and-grading.md) when delegating to a separate grader subagent or running multi-round reviews):

- Build on earlier verification rounds instead of re-checking from scratch.
- Tag every finding strictly as:
  - **`Important`**: Must-fix correctness, security, data-loss, or UX regression blocking commit/merge.
  - **`Nit`**: Minor polish; fix if trivial, otherwise do not churn.
  - **`Pre-existing`**: Bug already present on the base branch; document separately without blocking or blaming the current change.
- Resolve all `Important` findings and re-verify affected tiers before stamping.

### Step 7: Record Verification Evidence & Stamp Commit

Once verification passes on the exact current working tree state, record the session ledger entry and generate the `Verified-By:` commit trailer:

```bash
python3 .agents/skills/verify-loop/scripts/verify_guard.py record \
  --status pass \
  --checks "build,lint,unit,diff-checklist,e2e,review" \
  --summary "Booted local app and verified checkout flow + RC-001 legacy transcript load" \
  --evidence "path/to/evidence-or-test-log"
```

> [!IMPORTANT]
> `verify_guard.py record` checks the staged snapshot captured by `begin`. Stage further changes and verify the new candidate before committing.

When creating the commit, include the `Verified-By:` trailer (or stamp a commit message file via `verify_guard.py stamp-message`):

```text
feat(cart): validate discount recalculation on quantity update

Verified-By: build, lint, property, e2e, review
```

If verification genuinely cannot run (e.g., docs-only typo or unavailable external hardware), record an explicit reason so commit audits and enforce-mode gates stay transparent:

```text
Verified-By: none (markdown documentation update only; no executable code paths changed)
```

---

## Quick Script Reference

All helper scripts require only Python 3 standard library:

- [`scripts/verify_guard.py`](./scripts/verify_guard.py)
  - `triage`: Base-branch rubric loader, check-tampering detector, and diff-triggered checklist matcher.
  - `record`: Session verification ledger, 3-strike counter, tree-freshness fingerprint, and trailer generator.
  - `stamp-message`: Appends/updates `Verified-By:` trailer on a commit message string or file.
  - `audit-commits`: Queries `git log` to report verified, `Verified-By: none`, and unstamped commits.
- [`scripts/commit_gate_hook.py`](./scripts/commit_gate_hook.py)
  - `--git-message`: Git `commit-msg` handler that validates the commit Git is actually making (`off`, `observe` default, or `enforce`).
  - stdin mode: Claude Code or Antigravity `PreToolUse` hook that gives earlier feedback on direct `git commit` calls, with a two-block session circuit breaker in `enforce` mode.
  - `--inspect-log`: Summarises observe and enforce events so false positives can be reviewed before enforcing.
- [`scripts/diff_oracle.py`](./scripts/diff_oracle.py)
  - Runs `<old-cmd>` vs `<new-cmd>` across input corpora or generators with output normalizers and an approved-differences allowlist.
