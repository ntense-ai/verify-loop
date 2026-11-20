#!/usr/bin/env python3
"""Antigravity PreToolUse commit gate hook supporting off, observe, and enforce modes.

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
    read_from_base_or_disk,
    resolve_base_ref,
    save_session,
    state_dir,
)

GIT_COMMIT_RE = re.compile(
    r"(?:^|&&|\|\||[;&|\n])\s*git\s+(?:(?:-[Cc]\s+\S+|--[A-Za-z0-9-]+(?:=\S+)?|-[A-Za-z]+)\s+)*commit\b"
)
DRY_OR_HELP_RE = re.compile(r"\s(--dry-run|--help|-h)\b")
EXPLICIT_BYPASS_RE = re.compile(
    r"(Verified-By:\s*none\s*\([^)\n]{3,}\)|Unverified-Reason:\s*\S+)",
    re.IGNORECASE,
)
VALID_MODES = {"off", "observe", "enforce"}
MAX_ENFORCE_BLOCKS_PER_SESSION = 2


def resolve_gate_mode(repo: Path) -> str:
    base_ref = resolve_base_ref(repo)
    cfg_doc = read_from_base_or_disk(repo, base_ref, ".verify-loop.json")
    if cfg_doc["exists"]:
        try:
            parsed = json.loads(cfg_doc["content"])
            mode = str(parsed.get("gateMode", "")).strip().lower()
            if mode in VALID_MODES:
                return mode
        except json.JSONDecodeError:
            pass
    env_mode = os.environ.get("VERIFY_GATE_MODE", "").strip().lower()
    if env_mode in VALID_MODES:
        return env_mode
    return "observe"


def extract_commit_message_text(repo: Path, command_line: str) -> str:
    parts: list[str] = [command_line]
    for file_match in re.finditer(r"(?:-F|--file(?:=|\s+))(['\"]?)([^\s'\"]+)\1", command_line):
        candidate = Path(file_match.group(2))
        if not candidate.is_absolute():
            candidate = repo / candidate
        if candidate.is_file():
            try:
                parts.append(candidate.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(parts)


def check_session_verification(repo: Path, conversation_id: str) -> tuple[bool, str]:
    current_fp = compute_tree_fingerprint(repo)
    candidate_ids = [conversation_id] if conversation_id == "default" else [conversation_id, "default"]

    stale_reason: str | None = None
    for sid in candidate_ids:
        session = load_session(repo, sid)
        if not session.get("verified"):
            continue
        recorded_fp = session.get("tree_fingerprint")
        if recorded_fp == current_fp:
            return True, f"verified in session '{sid}' ({session.get('commit_trailer', 'Verified-By recorded')})"
        stale_reason = (
            f"session '{sid}' had a passing verification stamp, but files changed afterward "
            "(working tree fingerprint mismatch)"
        )

    if stale_reason:
        return False, stale_reason
    return False, "session has no recorded verification pass (`verify_guard.py record --status pass`)"


def append_gate_log(repo: Path, event: dict[str, Any]) -> None:
    log_path = state_dir(repo) / "gate-observe.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def evaluate_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name", "")).strip()
    args = tool_call.get("args") or {}
    command_line = str(args.get("CommandLine", ""))

    if tool_name != "run_command" or not GIT_COMMIT_RE.search(command_line):
        return {"decision": "allow"}
    if DRY_OR_HELP_RE.search(command_line):
        return {"decision": "allow"}

    workspace_paths = payload.get("workspacePaths") or []
    raw_cwd = payload.get("cwd") or (workspace_paths[0] if workspace_paths else ".")
    repo = find_repo_root(Path(raw_cwd))
    mode = resolve_gate_mode(repo)
    if mode == "off":
        return {"decision": "allow"}

    conversation_id = str(payload.get("conversationId") or "default")
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    msg_probe = extract_commit_message_text(repo, command_line)
    if EXPLICIT_BYPASS_RE.search(msg_probe):
        append_gate_log(
            repo,
            {
                "timestamp": now,
                "mode": mode,
                "conversationId": conversation_id,
                "outcome": "allow_explicit_explanation",
                "command": command_line,
            },
        )
        return {"decision": "allow"}

    ok, detail = check_session_verification(repo, conversation_id)
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
        parser.add_argument("--inspect-log", action="store_true", help="Summarize gate observe/enforce log")
        parser.add_argument("--repo", default=".", help="Path inside git repository")
        parser.add_argument("--json", action="store_true", help="Emit JSON output for --inspect-log")
        args = parser.parse_args()
        if args.inspect_log:
            return inspect_log(args.repo, args.json)
        parser.print_help()
        return 0

    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"decision": "allow"}))
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "allow"}))
        return 0

    decision = evaluate_pre_tool_use(payload)
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
