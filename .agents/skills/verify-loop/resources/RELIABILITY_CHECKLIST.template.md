# Diff-Triggered Reliability Checklist (`RELIABILITY_CHECKLIST.md`)

> Each item defines observable diff triggers (`Trigger Paths` and/or `Trigger Diff Regex`), a concrete `Check`, and `Provenance` (the incident or PR that motivated it).
> Run `python3 .agents/skills/verify-loop/scripts/verify_guard.py triage` to evaluate **only** the items triggered by the current diff.

### RC-001: Backward-compatible session and transcript loading
- **Trigger Paths**: `**/session/**`, `**/transcript*`, `**/storage/**`, `**/messages/**`
- **Trigger Diff Regex**: `\b(interface|type|struct|class|schema)\b.*(Session|Transcript|Message|Checkpoint)`
- **Check**: Load a legacy persisted session/transcript fixture missing any newly added fields (e.g., `--resume` flow) and verify deserialization succeeds cleanly with safe defaults.
- **Provenance**: Historical regression — adding a required field to session payloads broke loading pre-existing user transcripts.

### RC-002: Sensitive data and PII excluded from logs and telemetry
- **Trigger Paths**: `**/auth/**`, `**/user/**`, `**/billing/**`, `**/logger*`, `**/telemetry/**`
- **Trigger Diff Regex**: `\b(logger|console|log|tracing)\.(info|warn|error|debug)\b`
- **Check**: Inspect emitted logs during an E2E/integration run and verify no raw tokens, Authorization headers, emails, or user payload bodies appear in stdout/log files.
- **Provenance**: Privacy guard — debug logging left in error paths leaked user tokens into structured logs.

### RC-003: Idempotent database and state migrations
- **Trigger Paths**: `**/migrations/**`, `**/db/**`, `**/schema.*`
- **Trigger Diff Regex**: `\b(CREATE|ALTER|DROP|MIGRATE|upgrade|downgrade)\b`
- **Check**: Run the migration twice back-to-back against a populated fixture DB and verify idempotency, zero data loss, and non-blocking lock behavior.
- **Provenance**: Deployment outage — non-idempotent migration retry failed halfway and left schema partially applied.

### RC-004: Public API & CLI flag backward compatibility
- **Trigger Paths**: `**/api/**`, `**/routes/**`, `**/cli/**`, `**/cmd/**`, `openapi*`, `*.proto`
- **Trigger Diff Regex**: `\b(router\.|app\.(get|post|put|delete)|add_argument|flag\.)`
- **Check**: Run differential or contract checks ensuring existing callers/flags keep identical status codes, exit codes, and required response keys (unless listed in approved breaking changes).
- **Provenance**: Client breakage — renamed CLI option / response field broke automation scripts silently.
