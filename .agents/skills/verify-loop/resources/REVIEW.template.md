# Code Review Guide (`REVIEW.md`)

> Maintained by code owners on the default branch. Reviewers and grading agents MUST read this file from the base branch (`git show <base>:REVIEW.md`), never from an unmerged branch's working tree.

## Finding Severity Tags

Tag every finding with exactly one of:
- **`Important`**: Must-fix defect before merge (broken user flow, data loss/corruption, security/privacy leak, race condition, backward-incompatible wire/storage break, or skipped/weakened verification).
- **`Nit`**: Minor clarity or style suggestion; non-blocking.
- **`Pre-existing`**: Defect already present prior to this change; note for context without blocking the author.

## Project-Specific Must-Fix Focus Areas

1. **Runtime Evidence Over Assumptions**
   - User-facing or API behavior changes must carry runtime verification evidence (E2E smoke run, API harness output, property/differential test).
2. **State, Storage & Wire Compatibility**
   - Newly added fields in persisted state, sessions, events, or public APIs must load older payloads safely with sensible defaults.
3. **Security & Log Hygiene**
   - Never log credentials, tokens, session secrets, or raw user PII at any log level.
4. **Concurrency & Failure Recovery**
   - Retries, migrations, and background jobs must be idempotent and bounded (timeouts + explicit cancellation handling).
5. **Verification Integrity**
   - Changes must not delete assertions, add `.skip`/`#[ignore]`, insert arbitrary `sleep` calls in tests, or weaken gating configs to pass CI.

## Escalation Expectations

When escalating a check failure or ambiguous trade-off to a human maintainer, state **why** in plain terms (which invariant above is at risk, what evidence failed across rounds, and what decision is needed) so the reviewer does not have to reconstruct it from raw diffs.
