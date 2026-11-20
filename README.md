# Verify Loop

Verify Loop helps a coding agent exercise a change, collect evidence, and record which staged Git snapshot passed. It includes a differential test runner and an optional Git commit hook. The helpers require Git and Python 3.9 or later and use the Python standard library.

## Quick start with your coding agent

1. Install the skill in the Git project you want to verify using the instructions below. Open that project in your coding agent.
2. Make your change. When it is ready for verification, stage the intended files. For a verification stamp, the working files must match what you staged.
3. Tell the agent what a user should be able to do and what result you expect. For example:

   > Use verify-loop on my staged export change. Open the app, export March for a Sydney workspace, and check that the CSV includes the 31 March transaction and excludes the 1 April transaction. Run the relevant tests and show me the result before committing.

An example report for that request might read:

> March export passed for the Sydney fixture. The downloaded CSV contained the 31 March row exactly once and excluded the 1 April row. Evidence: the CSV and test log. Other time zones remain untested.

For your own change, replace the export scenario with one user action and an observable expected result. Ask for the actual evidence paths so you can inspect the output. If you are still editing, ask **“Use verify-loop to inspect my current changes and exercise the affected flow.”** You can run that inspection before staging.

## Install the skill

Copy the complete [.agents/skills/verify-loop](.agents/skills/verify-loop) directory into the same location in your project. Open that project in your agent and ask **“Use verify-loop to check my staged changes.”**

| Agent | Project skill directory | Discovery checked |
| --- | --- | --- |
| Codex | `.agents/skills/verify-loop` | Local CLI |
| Claude Code | `.claude/skills/verify-loop` | Local CLI |
| Gemini CLI | `.agents/skills/verify-loop` | Local CLI |
| GitHub Copilot CLI, Cursor, Antigravity | `.agents/skills/verify-loop` | Vendor documentation |

For Claude Code, copy the included [.claude/skills/verify-loop](.claude/skills/verify-loop) directory link alongside the shared skill. On a system without directory symlink support, copy the full skill directory to the Claude location. Invoke it with `/verify-loop`.

## Run verification manually

Stage the files intended for the commit. Run the commands from the target repository. The shell examples use POSIX syntax. On Windows, use Git Bash or invoke the Python helpers with `py -3` and equivalent paths.

```sh
skill=.agents/skills/verify-loop
guard="$skill/scripts/verify_guard.py"

python3 "$guard" triage
python3 "$guard" begin --new-task
```

Triage reports changed files, integrity findings, and reliability checks from the base branch. Use `triage --base <ref>` to select a particular comparison. The templates under [resources](.agents/skills/verify-loop/resources) provide a starting point for project-specific review rules and user flows.

After `begin`, run the relevant checks and exercise the user-visible change. For an exporter, for example, download a report from the running app and compare its rows with a known fixture. Record the checks that actually passed. Keep evidence in an ignored directory or outside the checkout.

```sh
python3 "$guard" record --status pass --checks "unit,export-smoke" \
  --summary "Downloaded March CSV and checked the month boundary" \
  --evidence /path/to/export-test.log
python3 "$guard" stamp-message --message "Fix monthly export"
```

The last command prints the commit message with its verification trailer. Use that text when committing. Git’s tree ID covers file contents, paths, and executable modes. The guard also records the parent commit. Both `begin` and `record --status pass` require a working tree matching the staged candidate. If you are partway through other edits, use a separate worktree for verification. Use a separate `VERIFY_SESSION_ID` per concurrent task and export it to the shell that runs Git.

### When a check fails

Record the first failure with `record --status fail --checks export-smoke`. After a fix, stage it, run `begin` in the same task, and rerun the check. Add `--after-fix` when recording an unsuccessful fix. Three unsuccessful fixes for the same check produce exit code 3 and an escalation summary. Use `record --status resolved --checks export-smoke` for a recovered check, then run the final candidate’s verification. Start an independent task with `begin --new-task`.

### Compare two implementations

```sh
python3 "$skill/scripts/diff_oracle.py" \
  --old-cmd "python3 legacy_export.py" --new-cmd "python3 new_export.py" \
  --inputs-dir fixtures/export --json
```

Targets receive each UTF-8 fixture on stdin. A target command can instead use `{file}` for a fixture path with `--inputs-dir`, or `{input}` for a literal argument. Commands use quoted argument syntax and execute directly. Exit status and exact stdout are compared by default. Opt into stderr comparison or normalisation when the output contract calls for it.

To approve an intentional difference, inspect the JSON report and save an array of objects with its exact `case_id`, `difference_sha256`, and a written `reason`. Pass that file with `--approved-diffs`. The digest identifies both normalised outputs and exit statuses. Existing regex approvals need a fresh review against the new report.

## Optional commit gate

Git's `commit-msg` hook checks the candidate Git is actually committing, including temporary indexes used by partial commits. It reads the final message after editor input. It works with local Git commits made by any of the agents above. Observe mode logs commits that would fail. Enforce mode rejects a missing or stale pass, or a mismatched trailer. An explicit `Verified-By: none (reason)` trailer records an exception.

Install from a reviewed checkout. The following example installs a private copy of the two gate scripts and registers it in the target repository. Run it from the target repository root after installing the reviewed skill. It expects Git’s default hooks directory. If your repository already manages hooks, integrate the [commit-msg handler](.agents/skills/verify-loop/resources/commit-msg) into that setup and preserve its existing checks.

```sh
(
  set -eu
  skill=.agents/skills/verify-loop
  git_dir=$(git rev-parse --path-format=absolute --git-common-dir)
  hooks=$(git rev-parse --git-path hooks)
  if git config --get core.hooksPath >/dev/null || [ -e "$hooks/commit-msg" ] || [ -L "$hooks/commit-msg" ]; then
    echo "Existing hooks configuration found. Integrate the commit-msg handler there."
    exit 1
  fi
  runtime=$(mktemp -d "$git_dir/verify-loop-runtime.XXXXXX")
  cp "$skill/scripts/verify_guard.py" "$skill/scripts/commit_gate_hook.py" "$runtime/"
  git config --local verifyloop.runtime "$runtime"
  git config --local verifyloop.gateMode observe
  mkdir -p "$hooks"
  cp "$skill/resources/commit-msg" "$hooks/commit-msg"
  chmod +x "$hooks/commit-msg"
)
```

Use the installed `verify_guard.py` when verifying changes to the skill itself. To upgrade the gate, install a newly reviewed copy and update `verifyloop.runtime`. To enforce it after observing real commits, run `git config --local verifyloop.gateMode enforce`. Use `off` to disable it. Without a local override, the hook reads the committed mode from a recognised base branch and otherwise defaults to observation. Local Git hooks are operator-controlled. Use independently managed CI when merge policy requires enforcement for every contributor.

Antigravity can also use [the optional PreToolUse template](.agents/skills/verify-loop/resources/hooks.template.json) to provide earlier feedback on direct Git commands. Set its placeholder to the absolute path of the independently installed hook script in a user-controlled hook configuration. The Git hook makes the final decision, including for compound shell commands.

## Audit and test

```sh
python3 "$guard" audit-commits -n 25
python3 "$skill/scripts/commit_gate_hook.py" --inspect-log
```

Runtime state lives under the worktree’s Git directory. `VERIFY_LOOP_STATE_DIR` can supply a different parent, with a subdirectory per worktree. After upgrading from the original fingerprint format, begin a new verification task and run the checks again.

Run the regression suite from this skill repository:

```sh
python3 -Bm unittest discover -s tests -v
```
