# Application Feature Map & E2E Rubric (`FEATURE_MAP.md`)

> Lists core user-facing flows from the user's point of view. Use this file both to drive E2E smoke runs when verifying changes and as the grading rubric for independent verification passes.

## How to Boot & Smoke-Test Locally

- **Start command**: `<e.g., npm run dev -- --port 3000>`
- **Readiness check**: `<e.g., curl -sf http://127.0.0.1:3000/healthz>`
- **Inspection tools**: `<e.g., Browser/CDP, Playwright, iOS Simulator, or curl/CLI harness>`

---

## Core User Flows

### FM-001: Primary Happy-Path Creation / Checkout Flow
- **Entry Path**: `/` -> `/checkout` (or CLI subcommand)
- **Selectors / Shortcuts**: `[data-testid="add-item"]`, `[data-testid="submit-order"]`, shortcut `Cmd+Enter`
- **Steps**:
  1. Open entry view with clean test account/fixture.
  2. Trigger primary action with valid input.
  3. Submit and navigate to confirmation state.
- **Expected Result & Evidence**:
  - Confirmation banner renders with generated ID; HTTP `200`/`201`; zero console/server errors.
  - Capture screenshot or response JSON snapshot.

### FM-002: Validation & Recoverable Error Handling
- **Entry Path**: `/checkout`
- **Selectors / Shortcuts**: `[data-testid="submit-order"]`, `[role="alert"]`
- **Steps**:
  1. Submit malformed or empty required input.
  2. Correct the highlighted field and resubmit.
- **Expected Result & Evidence**:
  - Inline actionable error appears without crashing or losing entered form state; subsequent valid submit succeeds cleanly.
