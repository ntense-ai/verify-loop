#!/usr/bin/env python3
"""Verification loop guard: diff triage, base-branch check integrity, session ledger, and commit stamps."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

sys.dont_write_bytecode = True

PROTECTED_CHECK_PATTERNS = (
    "REVIEW.md",
    "**/REVIEW.md",
    "RELIABILITY_CHECKLIST.md",
    "**/RELIABILITY_CHECKLIST.md",
    "FEATURE_MAP.md",
    "**/FEATURE_MAP.md",
    ".verify-loop.json",
    ".agents/hooks.json",
    ".agents/skills/verify-loop/**",
    ".claude/skills/verify/**",
    ".github/workflows/**",
)

TEST_FILE_PATTERNS = (
    "test/**",
    "tests/**",
    "__tests__/**",
    "e2e/**",
    "spec/**",
    "specs/**",
    "*_test.*",
    "*.test.*",
    "*.spec.*",
    "test_*.*",
    "**/test/**",
    "**/tests/**",
    "**/__tests__/**",
    "**/e2e/**",
    "**/*_test.*",
    "**/*.test.*",
    "**/*.spec.*",
    "**/test_*.*",
)

SKIP_MARKERS_RE = re.compile(
    r"(\b(?:it|test|describe)\.skip\s*\("
    r"|\b(?:xit|xtest|xdescribe)\s*\("
    r"|@pytest\.mark\.skip(?:if)?\b"
    r"|@unittest\.skip(?:If|Unless)?\b"
    r"|\bpytest\.skip\s*\("
    r"|#\[\s*ignore\b"
    r"|\bt\.Skip(?:f|Now)?\s*\()"
)

SLEEP_MARKERS_RE = re.compile(
    r"(\btime\.sleep\s*\("
    r"|\basyncio\.sleep\s*\("
    r"|\bsetTimeout\s*\("
    r"|\bwaitForTimeout\s*\("
    r"|\bThread\.sleep\s*\("
    r"|\bstd::thread::sleep\s*\()"
)


def run_git(repo: Path, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def find_repo_root(start: Path) -> Path:
    proc = run_git(start, ["rev-parse", "--show-toplevel"])
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return start.resolve()


def resolve_base_ref(repo: Path, explicit_base: str | None = None) -> str | None:
    candidates = [explicit_base] if explicit_base else [
        "origin/main",
        "origin/master",
        "main",
        "master",
        "HEAD",
    ]
    for ref in candidates:
        if not ref:
            continue
        proc = run_git(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
        if proc.returncode == 0:
            return ref
    return None


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    norm = pattern.strip().replace("\\", "/")
    out: list[str] = ["^"]
    i = 0
    n = len(norm)
    while i < n:
        if norm[i : i + 3] == "**/":
            out.append("(?:.+/)?")
            i += 3
        elif norm[i : i + 3] == "/**":
            out.append("(?:/.*)?")
            i += 3
        elif norm[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif norm[i] == "*":
            out.append("[^/]*")
            i += 1
        elif norm[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(norm[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def normalize_rel_path(rel_path: str) -> str:
    posix = rel_path.strip().replace("\\", "/")
    while posix.startswith("./"):
        posix = posix[2:]
    return posix.lstrip("/")


def matches_any_glob(rel_path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    posix = normalize_rel_path(rel_path)
    return any(glob_to_regex(p).match(posix) for p in patterns if p.strip())


def read_from_base_or_disk(repo: Path, base_ref: str | None, rel_path: str) -> dict[str, Any]:
    posix = normalize_rel_path(rel_path)
    if base_ref:
        proc = run_git(repo, ["show", f"{base_ref}:{posix}"])
        if proc.returncode == 0:
            return {
                "exists": True,
                "source": f"base_branch ({base_ref})",
                "trusted": True,
                "content": proc.stdout,
            }
    disk_path = repo / posix
    if disk_path.is_file():
        return {
            "exists": True,
            "source": "working_tree_bootstrap",
            "trusted": base_ref is None,
            "warning": (
                f"{posix} does not exist on base ref '{base_ref}' yet; loaded from working tree "
                "for initial bootstrap. Commit it to the base branch before treating it as an immutable gate."
                if base_ref
                else f"Repository has no commits yet; loaded {posix} from working tree."
            ),
            "content": disk_path.read_text(encoding="utf-8"),
        }
    return {"exists": False, "source": "missing", "trusted": False, "content": ""}


def collect_diff_state(repo: Path, base_ref: str | None) -> tuple[list[str], dict[str, str], str]:
    changed_files: set[str] = set()
    per_file_diff: dict[str, str] = {}
    full_diff_parts: list[str] = []

    if base_ref:
        names_proc = run_git(repo, ["diff", "--name-only", base_ref])
        if names_proc.returncode == 0:
            for line in names_proc.stdout.splitlines():
                if line.strip():
                    changed_files.add(line.strip())
        diff_proc = run_git(repo, ["diff", "--unified=3", base_ref])
        if diff_proc.returncode == 0 and diff_proc.stdout:
            full_diff_parts.append(diff_proc.stdout)
            current_file: str | None = None
            buf: list[str] = []
            for line in diff_proc.stdout.splitlines(keepends=True):
                if line.startswith("diff --git "):
                    if current_file and buf:
                        per_file_diff[current_file] = "".join(buf)
                    buf = [line]
                    m = re.match(r"^diff --git a/(.+?) b/(.+?)\s*$", line)
                    current_file = m.group(2) if m else None
                else:
                    buf.append(line)
            if current_file and buf:
                per_file_diff[current_file] = "".join(buf)
    else:
        staged_proc = run_git(repo, ["diff", "--cached", "--name-only"])
        if staged_proc.returncode == 0:
            for line in staged_proc.stdout.splitlines():
                if line.strip():
                    changed_files.add(line.strip())
        staged_diff = run_git(repo, ["diff", "--cached", "--unified=3"])
        if staged_diff.returncode == 0 and staged_diff.stdout:
            full_diff_parts.append(staged_diff.stdout)

    untracked_proc = run_git(repo, ["ls-files", "--others", "--exclude-standard"])
    if untracked_proc.returncode == 0:
        for rel in untracked_proc.stdout.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            changed_files.add(rel)
            fpath = repo / rel
            if fpath.is_file():
                try:
                    text = fpath.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                synthetic = f"diff --git a/{rel} b/{rel}\n--- /dev/null\n+++ b/{rel}\n" + "".join(
                    f"+{ln}" for ln in text.splitlines(keepends=True)
                )
                per_file_diff[rel] = synthetic
                full_diff_parts.append(synthetic)

    return sorted(changed_files), per_file_diff, "\n".join(full_diff_parts)


def audit_check_integrity(changed_files: list[str], per_file_diff: dict[str, str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for rel in changed_files:
        if matches_any_glob(rel, PROTECTED_CHECK_PATTERNS):
            issues.append(
                {
                    "type": "gating_file_modified",
                    "severity": "high",
                    "file": rel,
                    "message": (
                        f"Change modifies gating/verification file '{rel}'. Changes must not weaken "
                        "their own checks; verify base-branch rules and ensure human code-owner intent."
                    ),
                }
            )
        if matches_any_glob(rel, TEST_FILE_PATTERNS):
            patch = per_file_diff.get(rel, "")
            added_lines = [
                ln[1:]
                for ln in patch.splitlines()
                if ln.startswith("+") and not ln.startswith("+++")
            ]
            for ln in added_lines:
                if SKIP_MARKERS_RE.search(ln):
                    issues.append(
                        {
                            "type": "test_skip_added",
                            "severity": "high",
                            "file": rel,
                            "line": ln.strip(),
                            "message": (
                                f"Test skip/ignore marker added in '{rel}'. Never skip flaky or failing "
                                "tests; fix root cause or delete with documented rationale."
                            ),
                        }
                    )
                if SLEEP_MARKERS_RE.search(ln):
                    issues.append(
                        {
                            "type": "test_sleep_added",
                            "severity": "medium",
                            "file": rel,
                            "line": ln.strip(),
                            "message": (
                                f"Arbitrary sleep/timeout call added in test '{rel}'. Replace fixed sleeps "
                                "with deterministic condition polling, fake timers, or explicit readiness signals."
                            ),
                        }
                    )
    return issues


def extract_code_spans_or_csv(raw: str) -> list[str]:
    ticks = re.findall(r"`([^`]+)`", raw)
    if ticks:
        return [t.strip() for t in ticks if t.strip()]
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_reliability_checklist(markdown: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    heading_re = re.compile(r"^#{2,3}\s+([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(markdown))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        body = markdown[start:end]
        item_id = match.group(1).strip()
        title = match.group(2).strip()

        trigger_paths: list[str] = []
        trigger_regexes: list[str] = []
        check_text = ""
        provenance = ""

        for line in body.splitlines():
            m_paths = re.match(r"^\s*-\s*\*\*Trigger Paths?\*\*\s*:\s*(.+)$", line, re.IGNORECASE)
            if m_paths:
                trigger_paths.extend(extract_code_spans_or_csv(m_paths.group(1)))
                continue
            m_re = re.match(r"^\s*-\s*\*\*Trigger Diff Regex\*\*\s*:\s*(.+)$", line, re.IGNORECASE)
            if m_re:
                trigger_regexes.extend(extract_code_spans_or_csv(m_re.group(1)))
                continue
            m_check = re.match(r"^\s*-\s*\*\*Check\*\*\s*:\s*(.+)$", line, re.IGNORECASE)
            if m_check:
                check_text = m_check.group(1).strip()
                continue
            m_prov = re.match(r"^\s*-\s*\*\*Provenance\*\*\s*:\s*(.+)$", line, re.IGNORECASE)
            if m_prov:
                provenance = m_prov.group(1).strip()
                continue

        items.append(
            {
                "id": item_id,
                "title": title,
                "trigger_paths": trigger_paths,
                "trigger_diff_regex": trigger_regexes,
                "check": check_text,
                "provenance": provenance,
            }
        )
    return items


def match_reliability_items(
    items: list[dict[str, Any]],
    changed_files: list[str],
    full_diff: str,
) -> list[dict[str, Any]]:
    changed_diff_lines = "\n".join(
        ln[1:]
        for ln in full_diff.splitlines()
        if (ln.startswith("+") and not ln.startswith("+++"))
        or (ln.startswith("-") and not ln.startswith("---"))
    )
    matched: list[dict[str, Any]] = []
    for item in items:
        reasons: list[str] = []
        if item["trigger_paths"]:
            hit_files = [f for f in changed_files if matches_any_glob(f, item["trigger_paths"])]
            if hit_files:
                reasons.append(f"paths matched: {', '.join(hit_files[:5])}")
        for pattern in item["trigger_diff_regex"]:
            try:
                if re.search(pattern, changed_diff_lines, re.MULTILINE):
                    reasons.append(f"diff regex matched: /{pattern}/")
            except re.error as exc:
                reasons.append(f"invalid regex /{pattern}/ ({exc}) - included for safety")
        if reasons:
            matched.append({**item, "match_reasons": reasons})
    return matched


def compute_tree_fingerprint(repo: Path) -> str:
    hasher = hashlib.sha256()
    head_proc = run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    head_sha = head_proc.stdout.strip() if head_proc.returncode == 0 else "NO_HEAD"
    hasher.update(f"HEAD:{head_sha}\n".encode("utf-8"))

    status_proc = run_git(repo, ["status", "--porcelain=v1", "-uall"])
    if status_proc.returncode != 0:
        return hasher.hexdigest()

    entries: list[tuple[str, str]] = []
    for raw in status_proc.stdout.splitlines():
        if len(raw) < 4:
            continue
        status_code = raw[:2]
        path_part = raw[3:]
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        rel = path_part.strip().strip('"')
        entries.append((rel, status_code))

    for rel, status_code in sorted(entries, key=lambda x: x[0]):
        fpath = repo / rel
        if fpath.is_symlink():
            hasher.update(f"LINK:{rel}:{os.readlink(fpath)}\n".encode("utf-8"))
        elif fpath.is_file():
            digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
            hasher.update(f"FILE:{rel}:{digest}\n".encode("utf-8"))
        else:
            hasher.update(f"GONE:{rel}:{status_code.strip()}\n".encode("utf-8"))

    return hasher.hexdigest()


def state_dir(repo: Path) -> Path:
    override = os.environ.get("VERIFY_LOOP_STATE_DIR", "").strip()
    if override:
        target = Path(override).expanduser().resolve()
    else:
        resolved = repo.resolve()
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved.name) or "repo"
        target = Path("/tmp") / "verify-loop-state" / f"{safe_name}-{digest}"
    (target / "sessions").mkdir(parents=True, exist_ok=True)
    return target


def sanitize_session_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw.strip())
    return cleaned or "default"


def load_session(repo: Path, session_id: str) -> dict[str, Any]:
    sfile = state_dir(repo) / "sessions" / f"{sanitize_session_id(session_id)}.json"
    if sfile.is_file():
        try:
            return json.loads(sfile.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "session_id": sanitize_session_id(session_id),
        "verified": False,
        "failed_rounds": 0,
        "blocked_commits_count": 0,
        "round_history": [],
    }


def save_session(repo: Path, session_id: str, data: dict[str, Any]) -> Path:
    sfile = state_dir(repo) / "sessions" / f"{sanitize_session_id(session_id)}.json"
    sfile.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sfile


def format_trailer(checks: list[str], none_reason: str | None = None) -> str:
    normalized = [c.strip() for c in checks if c.strip()]
    if not normalized or normalized == ["none"]:
        reason = (none_reason or "unspecified reason").strip()
        return f"Verified-By: none ({reason})"
    return f"Verified-By: {', '.join(normalized)}"


def cmd_triage(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path(args.repo))
    base_ref = resolve_base_ref(repo, args.base)
    changed_files, per_file_diff, full_diff = collect_diff_state(repo, base_ref)
    integrity_issues = audit_check_integrity(changed_files, per_file_diff)

    review_doc = read_from_base_or_disk(repo, base_ref, args.review)
    checklist_doc = read_from_base_or_disk(repo, base_ref, args.checklist)
    feature_map_doc = read_from_base_or_disk(repo, base_ref, args.feature_map)

    all_items = (
        parse_reliability_checklist(checklist_doc["content"])
        if checklist_doc["exists"]
        else []
    )
    matched_items = match_reliability_items(all_items, changed_files, full_diff)

    payload = {
        "repo": str(repo),
        "base_ref": base_ref,
        "changed_files_count": len(changed_files),
        "changed_files": changed_files,
        "tree_fingerprint": compute_tree_fingerprint(repo),
        "integrity_ok": len(integrity_issues) == 0,
        "integrity_issues": integrity_issues,
        "rubrics": {
            "review_md": {k: v for k, v in review_doc.items() if k != "content"},
            "reliability_checklist": {k: v for k, v in checklist_doc.items() if k != "content"},
            "feature_map": {k: v for k, v in feature_map_doc.items() if k != "content"},
        },
        "checklist_total_items": len(all_items),
        "matched_checks_count": len(matched_items),
        "matched_checks": matched_items,
    }
    if args.include_review_text and review_doc["exists"]:
        payload["review_md_content"] = review_doc["content"]

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"=== Verify-Loop Triage ({repo.name}) ===")
    print(f"Base ref:            {base_ref or '(none - uncommitted repository)'}")
    print(f"Changed files:       {len(changed_files)}")
    print(f"REVIEW.md source:    {review_doc['source']}")
    print(f"Checklist source:    {checklist_doc['source']} ({len(matched_items)}/{len(all_items)} triggered)")
    print(f"Feature map source:  {feature_map_doc['source']}")

    for doc in (review_doc, checklist_doc, feature_map_doc):
        if doc.get("warning"):
            print(f"  [BOOTSTRAP NOTICE] {doc['warning']}")

    if integrity_issues:
        print(f"\n[!] CHECK INTEGRITY ISSUES ({len(integrity_issues)}):")
        for issue in integrity_issues:
            loc = f"{issue['file']}" + (f" -> `{issue['line']}`" if "line" in issue else "")
            print(f"  - [{issue['severity'].upper()}] {issue['type']} ({loc}): {issue['message']}")
    else:
        print("\n[OK] Check integrity clean (no tampered gates, test skips, or added sleeps).")

    if matched_items:
        print(f"\n=== Triggered Reliability Checks ({len(matched_items)}/{len(all_items)}) ===")
        for item in matched_items:
            print(f"  * {item['id']}: {item['title']}")
            print(f"    Why triggered: {'; '.join(item['match_reasons'])}")
            print(f"    Check:         {item['check']}")
            if item.get("provenance"):
                print(f"    Provenance:    {item['provenance']}")
    elif checklist_doc["exists"]:
        print("\n[OK] 0 reliability checklist triggers matched this diff.")

    return 0


def cmd_record(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path(args.repo))
    sid = args.session_id or os.environ.get("VERIFY_SESSION_ID") or "default"
    session = load_session(repo, sid)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]

    if checks == ["none"] and args.status == "pass" and not args.none_reason:
        print("Error: --none-reason is required when recording --checks none with --status pass.", file=sys.stderr)
        return 2

    fingerprint = compute_tree_fingerprint(repo)
    if args.status == "fail":
        round_num = args.round if args.round is not None else int(session.get("failed_rounds", 0)) + 1
        session["failed_rounds"] = round_num
        session["verified"] = False
    elif args.status == "escalated":
        round_num = args.round if args.round is not None else int(session.get("failed_rounds", 0))
        session["verified"] = False
    else:
        round_num = args.round if args.round is not None else int(session.get("failed_rounds", 0)) + 1
        session["failed_rounds"] = 0
        session["verified"] = True
        session["verified_at"] = now
        session["tree_fingerprint"] = fingerprint
        session["checks"] = checks
        session["none_reason"] = args.none_reason
        session["commit_trailer"] = format_trailer(checks, args.none_reason)

    must_escalate = args.status == "fail" and int(session.get("failed_rounds", 0)) >= 3
    entry = {
        "timestamp": now,
        "round": round_num,
        "status": args.status,
        "checks": checks,
        "summary": args.summary or "",
        "evidence": args.evidence or [],
        "tree_fingerprint": fingerprint,
    }
    session.setdefault("round_history", []).append(entry)
    session["updated_at"] = now
    session["must_escalate"] = must_escalate

    sfile = save_session(repo, sid, session)
    if sid != "default":
        save_session(repo, "default", {**session, "mirrored_from": sid})

    result = {
        "session_file": str(sfile),
        "session_id": session["session_id"],
        "status": args.status,
        "round": round_num,
        "failed_rounds": session["failed_rounds"],
        "must_escalate": must_escalate,
        "verified": session["verified"],
        "tree_fingerprint": fingerprint,
        "commit_trailer": session.get("commit_trailer"),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Recorded verification status='{args.status}' (round {round_num}) -> {sfile}")
        if session.get("commit_trailer") and args.status == "pass":
            print(f"Commit trailer:\n  {session['commit_trailer']}")
        if must_escalate:
            print(
                "\n[STOP - THREE-STRIKE ESCALATION]\n"
                "Checks have failed across 3 rounds. Stop attempting automated fixes and escalate "
                "to a human reviewer with the history of all 3 rounds and concrete failure evidence."
            )
    return 3 if must_escalate else 0


def apply_trailer_to_text(message: str, trailer: str) -> str:
    lines = message.rstrip("\n").splitlines()
    filtered = [ln for ln in lines if not re.match(r"^Verified-By:\s*", ln, re.IGNORECASE)]
    if not filtered:
        return f"{trailer}\n"
    last_block_has_trailers = bool(re.match(r"^[A-Za-z0-9-]+:\s+.+$", filtered[-1]))
    sep = "\n" if last_block_has_trailers else "\n\n"
    return "\n".join(filtered) + f"{sep}{trailer}\n"


def cmd_stamp_message(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path(args.repo))
    if args.checks:
        checks = [c.strip() for c in args.checks.split(",") if c.strip()]
        trailer = format_trailer(checks, args.none_reason)
    else:
        sid = args.session_id or os.environ.get("VERIFY_SESSION_ID") or "default"
        session = load_session(repo, sid)
        if not session.get("commit_trailer"):
            print(
                f"Error: No recorded verification trailer found for session '{sid}'. "
                "Run `verify_guard.py record` first or pass `--checks` explicitly.",
                file=sys.stderr,
            )
            return 2
        trailer = str(session["commit_trailer"])

    if args.file:
        msg_path = Path(args.file)
        original = msg_path.read_text(encoding="utf-8")
        stamped = apply_trailer_to_text(original, trailer)
        if args.in_place:
            msg_path.write_text(stamped, encoding="utf-8")
        else:
            sys.stdout.write(stamped)
        return 0

    stamped = apply_trailer_to_text(args.message or "", trailer)
    sys.stdout.write(stamped)
    return 0


def cmd_audit_commits(args: argparse.Namespace) -> int:
    repo = find_repo_root(Path(args.repo))
    head_check = run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    if head_check.returncode != 0:
        if args.json:
            print(json.dumps({"total": 0, "verified": 0, "explicit_none": 0, "unstamped": 0, "commits": []}))
        else:
            print("Repository has no commits yet.")
        return 0

    git_args = ["log", f"-n{args.limit}", "--format=%H%x1f%h%x1f%s%x1f%b%x1e"]
    if args.since:
        git_args.append(f"{args.since}..HEAD")
    proc = run_git(repo, git_args, check=True)

    commits: list[dict[str, Any]] = []
    counts = {"verified": 0, "explicit_none": 0, "unstamped": 0}
    for record in proc.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 4:
            continue
        full_sha, short_sha, subject, body = parts[0], parts[1], parts[2], parts[3]
        full_msg = f"{subject}\n{body}"
        m = re.search(r"^Verified-By:\s*(.+)$", full_msg, re.IGNORECASE | re.MULTILINE)
        if not m:
            category = "unstamped"
            stamp = None
        else:
            stamp = m.group(1).strip()
            category = "explicit_none" if re.match(r"^none\b", stamp, re.IGNORECASE) else "verified"
        counts[category] += 1
        commits.append(
            {
                "sha": full_sha,
                "short_sha": short_sha,
                "subject": subject,
                "category": category,
                "verified_by": stamp,
            }
        )

    report = {"total": len(commits), **counts, "commits": commits}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Commit Verification Audit ({len(commits)} commits): "
            f"{counts['verified']} verified, {counts['explicit_none']} explicit 'none', {counts['unstamped']} unstamped"
        )
        for c in commits:
            badge = {
                "verified": "[VERIFIED]",
                "explicit_none": "[NONE]    ",
                "unstamped": "[MISSING] ",
            }[c["category"]]
            suffix = f" ({c['verified_by']})" if c["verified_by"] else ""
            print(f"  {badge} {c['short_sha']} {c['subject']}{suffix}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_triage = sub.add_parser("triage", help="Check diff integrity, load base rubrics, and match checklist triggers")
    p_triage.add_argument("--repo", default=".", help="Path inside git repository")
    p_triage.add_argument("--base", default=None, help="Explicit base git ref (defaults to origin/main, main, HEAD)")
    p_triage.add_argument("--review", default="REVIEW.md", help="Relative path to REVIEW.md")
    p_triage.add_argument("--checklist", default="RELIABILITY_CHECKLIST.md", help="Relative path to checklist")
    p_triage.add_argument("--feature-map", default="FEATURE_MAP.md", help="Relative path to FEATURE_MAP.md")
    p_triage.add_argument("--include-review-text", action="store_true", help="Include base-branch REVIEW.md content in JSON")
    p_triage.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_triage.set_defaults(func=cmd_triage)

    p_rec = sub.add_parser("record", help="Record a verification round or passing session stamp")
    p_rec.add_argument("--repo", default=".", help="Path inside git repository")
    p_rec.add_argument("--session-id", default=None, help="Session/conversation identifier")
    p_rec.add_argument("--status", choices=["pass", "fail", "escalated"], required=True)
    p_rec.add_argument("--checks", required=True, help="Comma-separated checks run (e.g. build,lint,e2e) or 'none'")
    p_rec.add_argument("--round", type=int, default=None, help="Optional explicit round number")
    p_rec.add_argument("--summary", default="", help="Brief summary of verification result or failure")
    p_rec.add_argument("--evidence", action="append", default=[], help="Evidence artifact/log path (repeatable)")
    p_rec.add_argument("--none-reason", default=None, help="Required explanation when --checks none --status pass")
    p_rec.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_rec.set_defaults(func=cmd_record)

    p_stamp = sub.add_parser("stamp-message", help="Add or update Verified-By trailer on a commit message")
    p_stamp.add_argument("--repo", default=".", help="Path inside git repository")
    p_stamp.add_argument("--session-id", default=None, help="Session ID to read trailer from")
    p_stamp.add_argument("--checks", default=None, help="Explicit checks list instead of reading session")
    p_stamp.add_argument("--none-reason", default=None, help="Reason when --checks none")
    grp = p_stamp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--message", help="Commit message text")
    grp.add_argument("--file", help="Path to commit message file")
    p_stamp.add_argument("--in-place", action="store_true", help="Modify --file in place")
    p_stamp.set_defaults(func=cmd_stamp_message)

    p_audit = sub.add_parser("audit-commits", help="Query git history for Verified-By stamps")
    p_audit.add_argument("--repo", default=".", help="Path inside git repository")
    p_audit.add_argument("-n", "--limit", type=int, default=20, help="Max commits to inspect")
    p_audit.add_argument("--since", default=None, help="Inspect commits since <ref>..HEAD")
    p_audit.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_audit.set_defaults(func=cmd_audit_commits)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
