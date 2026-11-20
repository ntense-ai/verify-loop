#!/usr/bin/env python3
"""Commit gate for Git commit-msg hooks and agent PreToolUse hooks (Claude Code, Antigravity).

Supports off, observe, and enforce modes.

Progressive commit gating behavior:
- Modes: `off` | `observe` (default) | `enforce`
- Reads `.verify-loop.json` from the base branch first so changes cannot weaken their own gate.
- Allows commits that carry an explicit explanation (`Verified-By: none (<reason>)` or `Unverified-Reason: <why>`).
- Circuit breaker in `enforce` mode: if commits were already blocked twice in the session, allows the 3rd attempt through so the agent never deadlocks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verify_guard import (  # noqa: E402
    compute_tree_fingerprint,
    find_repo_root,
    load_session,
    run_git,
    resolve_base_ref,
    save_session,
    state_dir,
)

EXPLICIT_BYPASS_RE = re.compile(
    r"(Verified-By:\s*none\s*\([^)\n]{3,}\)|Unverified-Reason:\s*\S+)",
    re.IGNORECASE,
)
VALID_MODES = {"off", "observe", "enforce"}
MAX_ENFORCE_BLOCKS_PER_SESSION = 2


def resolve_gate_mode(repo: Path) -> str:
    local = run_git(repo, ["config", "--local", "--get", "verifyloop.gateMode"])
    if local.returncode == 0:
        mode = local.stdout.strip().lower()
        if mode not in VALID_MODES:
            raise ValueError("Invalid local verifyloop.gateMode")
        return mode
    base_ref = resolve_base_ref(repo)
    if base_ref and base_ref != "HEAD":
        config = run_git(repo, ["show", f"{base_ref}:.verify-loop.json"])
        if config.returncode == 0:
            mode = json.loads(config.stdout).get("gateMode")
            if mode not in VALID_MODES:
                raise ValueError("Invalid gateMode in base configuration")
            return mode
    return "observe"


def session_detail(repo: Path, sid: str) -> tuple[bool, str]:
    session = load_session(repo, sid)
    if session.get("verified") and session.get("tree_fingerprint") == compute_tree_fingerprint(repo):
        return True, session["commit_trailer"]
    return False, "No current pass for this staged snapshot"


def git_message_gate(repo: Path, message_file: Path) -> int:
    mode = resolve_gate_mode(repo)
    if mode == "off":
        return 0
    trailers = run_git(repo, ["interpret-trailers", "--parse", str(message_file.resolve())], check=True)
    lines = trailers.stdout.splitlines()
    verified = [line for line in lines if line.lower().startswith("verified-by:")]
    exempt = len(verified) == 1 and EXPLICIT_BYPASS_RE.fullmatch(verified[0])
    sid = os.environ.get("VERIFY_SESSION_ID") or "default"
    ok, expected = session_detail(repo, sid)
    if exempt or (ok and verified == [expected]):
        append_gate_log(repo, {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "mode": mode, "conversationId": sid,
                               "outcome": "allow_explicit_explanation" if exempt else "allow_verified"})
        return 0
    append_gate_log(repo, {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "mode": mode, "outcome": "observe_would_block" if mode == "observe" else "enforce_blocked",
                           "conversationId": sid, "reason": "missing current verification or matching trailer"})
    print("verify-loop: commit needs a current verification and its matching trailer, or a documented Verified-By: none reason.", file=sys.stderr)
    return int(mode == "enforce")


def parse_git_commit(command: str, cwd: Path):
    # The Git commit-msg hook handles compound commands and commits from other clients.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens or tokens[0] != "git" or any(t in {";", "&&", "|", "||", "&"} for t in tokens):
        return None
    location = []
    i = 1
    while i < len(tokens) and tokens[i] != "commit":
        opt = tokens[i]
        if opt in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(tokens):
            if opt != "-c":
                location.extend(tokens[i:i + 2])
            i += 2
        elif opt.startswith(("-C", "--git-dir=", "--work-tree=")):
            location.append(opt)
            i += 1
        else:
            return None
    if i == len(tokens):
        return None
    args = tokens[i + 1:]
    result = run_git(cwd, [*location, "rev-parse", "--show-toplevel"])
    if result.returncode:
        raise ValueError("Unable to resolve commit repository")
    return Path(result.stdout.strip()), args


def append_gate_log(repo: Path, event: dict[str, Any]) -> None:
    log_path = state_dir(repo) / "gate-observe.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def detect_host(payload: dict[str, Any]) -> str:
    """Claude Code sends `tool_name`/`hook_event_name`; Antigravity sends `toolCall`."""
    return "claude" if "tool_name" in payload or "hook_event_name" in payload else "antigravity"


def shell_call(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (command, cwd, conversation id) for a shell tool call, else None."""
    if detect_host(payload) == "claude":
        if payload.get("tool_name") != "Bash":
            return None
        tool_input = payload.get("tool_input") or {}
        return (str(tool_input.get("command", "")), str(payload.get("cwd") or "."),
                str(payload.get("session_id") or "default"))
    tool_call = payload.get("toolCall") or {}
    if str(tool_call.get("name", "")).strip() != "run_command":
        return None
    args = tool_call.get("args") or {}
    workspace_paths = payload.get("workspacePaths") or []
    cwd = args.get("Cwd") or payload.get("cwd") or (workspace_paths[0] if workspace_paths else ".")
    return str(args.get("CommandLine", "")), str(cwd), str(payload.get("conversationId") or "default")


def render_decision(host: str, decision: dict[str, Any]) -> str:
    """Format a decision for the calling agent host."""
    if host != "claude":
        return json.dumps(decision)
    if decision["decision"] != "deny":
        # Print nothing: an explicit Claude Code "allow" would skip the user's permission prompt.
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": decision["reason"]}})


def evaluate_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    call = shell_call(payload)
    if call is None:
        return {"decision": "allow"}
    command_line, cwd, conversation_id = call
    target = parse_git_commit(command_line, Path(cwd))
    if target is None:
        return {"decision": "allow"}
    repo, commit_args = target
    if any(flag in commit_args for flag in ("--help", "--dry-run", "-h")):
        return {"decision": "allow"}
    mode = resolve_gate_mode(repo)
    if mode == "off":
        return {"decision": "allow"}

    now = dt.datetime.now(dt.timezone.utc).isoformat()

    # The final message (including -F and editor input) is validated by Git.
    if any(EXPLICIT_BYPASS_RE.fullmatch(line) for arg in commit_args for line in arg.splitlines()):
        return {"decision": "allow"}

    sid = os.environ.get("VERIFY_SESSION_ID") or conversation_id
    if not load_session(repo, sid).get("round_history") and "VERIFY_SESSION_ID" not in os.environ:
        sid = "default"
    ok, detail = session_detail(repo, sid)
    if ok:
        append_gate_log(
            repo,
            {
                "timestamp": now,
                "mode": mode,
                "conversationId": conversation_id,
                "outcome": "allow_verified",
                "detail": detail,
            },
        )
        return {"decision": "allow"}

    if mode == "observe":
        append_gate_log(
            repo,
            {
                "timestamp": now,
                "mode": "observe",
                "conversationId": conversation_id,
                "wouldBlock": True,
                "outcome": "observe_would_block",
                "reason": detail,
                "command": command_line,
            },
        )
        return {"decision": "allow"}

    # mode == "enforce"
    session = load_session(repo, conversation_id)
    prior_blocks = int(session.get("blocked_commits_count", 0))
    if prior_blocks >= MAX_ENFORCE_BLOCKS_PER_SESSION:
        append_gate_log(
            repo,
            {
                "timestamp": now,
                "mode": "enforce",
                "conversationId": conversation_id,
                "circuitBreakerTriggered": True,
                "priorBlocks": prior_blocks,
                "outcome": "allow_circuit_breaker",
                "reason": detail,
                "command": command_line,
            },
        )
        return {"decision": "allow"}

    new_blocks = prior_blocks + 1
    session["blocked_commits_count"] = new_blocks
    session["updated_at"] = now
    save_session(repo, conversation_id, session)

    append_gate_log(
        repo,
        {
            "timestamp": now,
            "mode": "enforce",
            "conversationId": conversation_id,
            "blockAttempt": new_blocks,
            "outcome": "enforce_blocked",
            "reason": detail,
            "command": command_line,
        },
    )
    return {
        "decision": "deny",
        "reason": (
            f"Commit blocked by verify-loop gate (attempt {new_blocks}/{MAX_ENFORCE_BLOCKS_PER_SESSION}): "
            f"{detail}. Run the verify-loop skill and record passing evidence via "
            "`python3 .agents/skills/verify-loop/scripts/verify_guard.py record --status pass ...`, "
            "or include `Verified-By: none (<concrete reason>)` in your commit message."
        ),
    }


def inspect_log(repo_arg: str, as_json: bool) -> int:
    repo = find_repo_root(Path(repo_arg))
    log_path = state_dir(repo) / "gate-observe.log"
    events: list[dict[str, Any]] = []
    if log_path.is_file():
        for ln in log_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue

    summary = {
        "repo": str(repo),
        "active_mode": resolve_gate_mode(repo),
        "log_file": str(log_path),
        "total_events": len(events),
        "observe_would_block": sum(1 for e in events if e.get("outcome") == "observe_would_block"),
        "enforce_blocked": sum(1 for e in events if e.get("outcome") == "enforce_blocked"),
        "allow_circuit_breaker": sum(1 for e in events if e.get("outcome") == "allow_circuit_breaker"),
        "allow_verified": sum(1 for e in events if e.get("outcome") == "allow_verified"),
        "allow_explicit_explanation": sum(1 for e in events if e.get("outcome") == "allow_explicit_explanation"),
        "recent_events": events[-10:],
    }
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Verify-Loop Commit Gate Status (active mode: {summary['active_mode']})")
        print(f"Log file:                   {summary['log_file']}")
        print(f"Verified commits allowed:   {summary['allow_verified']}")
        print(f"Explicit bypass allowed:    {summary['allow_explicit_explanation']}")
        print(f"Observe-mode would-blocks:  {summary['observe_would_block']}")
        print(f"Enforce-mode blocks:        {summary['enforce_blocked']}")
        print(f"Circuit-breaker overrides:  {summary['allow_circuit_breaker']}")
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--git-message", type=Path, help="Validate a Git commit-msg hook message file")
        parser.add_argument("--inspect-log", action="store_true", help="Summarize gate observe/enforce log")
        parser.add_argument("--repo", default=".", help="Path inside git repository")
        parser.add_argument("--json", action="store_true", help="Emit JSON output for --inspect-log")
        args = parser.parse_args()
        if args.git_message:
            return git_message_gate(find_repo_root(Path(args.repo)), args.git_message)
        if args.inspect_log:
            return inspect_log(args.repo, args.json)
        parser.print_help()
        return 0

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    host = detect_host(payload)
    try:
        decision = evaluate_pre_tool_use(payload)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        decision = {"decision": "deny", "reason": f"verify-loop gate error: {exc}"}
    output = render_decision(host, decision)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"verify-loop: {exc}")
