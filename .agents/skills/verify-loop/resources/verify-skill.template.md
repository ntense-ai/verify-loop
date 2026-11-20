---
name: verify-<area>
description: >-
  Boot, drive, and inspect <area> (for example the web checkout or the sync
  service) to verify a change end to end. Use before committing any change under
  <path/to/area>.
---

# Verify <area>

<!--
Copy this file to a project skill directory as close to the code it covers as
your agent allows, for example `.claude/skills/verify-<area>/SKILL.md` or
`.agents/skills/verify-<area>/SKILL.md`. Add one skill per surface that needs a
different harness. This is the smoke-test harness that verify-loop's Step 4
runs, so keep it fast and reliable.
-->

## Boot

1. Start command: `<e.g. npm run dev -- --port 0>`. Record the port it prints.
2. Readiness: poll `<e.g. curl -sf http://127.0.0.1:$PORT/healthz>` until it succeeds or 60 s elapse. Do not use a fixed sleep.
3. Seed state: `<e.g. npm run seed -- --fixture smoke>` (use an isolated temp DB or profile).

## Drive

Use `<browser automation / CDP / iOS simulator / API harness / CLI>` to run the flows in `FEATURE_MAP.md` that the diff touches. At minimum:

- `<FM-001>`: `<steps in user terms>` → expect `<observable result>`.

## Inspect and capture evidence

- Screenshot or response body for each flow, saved under `<ignored evidence dir>`.
- Console errors, failed network requests, and server log lines at `warn` or above.
- Logs contain no tokens, emails, or request bodies.

## Tear down

Stop the processes you started and delete temporary state.

## Keeping this harness healthy

- Time the full run. If it exceeds `<budget, e.g. 2 min>`, reuse seeded snapshots, parallelise independent flows, or cache builds.
- Stress-test changes to this skill: run it 5 times back to back from a cold start. Every run must pass on an unchanged tree.
- When a run flakes, fix the readiness or isolation cause. Do not add sleeps or retries.
