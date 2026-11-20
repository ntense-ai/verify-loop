#!/usr/bin/env python3
"""Differential test runner: compares an old system (oracle) against a new system on identical inputs.

Supports:
- Input corpora from line-delimited files, input directories, or a random generator command.
- Normalization of volatile outputs (whitespace, canonical JSON key ordering, ISO timestamps, UUIDs, custom regexes).
- Approved-differences allowlist so intentional migration changes are tracked cleanly without masking unexpected regressions.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

sys.dont_write_bytecode = True

ISO_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def load_inputs(args: argparse.Namespace) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    if args.inputs_file:
        p = Path(args.inputs_file)
        for idx, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if line or args.keep_empty_lines:
                cases.append({"id": f"{p.name}:{idx}", "payload": line, "file": ""})
    if args.inputs_dir:
        d = Path(args.inputs_dir)
        for fp in sorted(x for x in d.iterdir() if x.is_file()):
            cases.append({"id": fp.name, "payload": fp.read_text(encoding="utf-8"), "file": str(fp)})
    if args.generator_cmd:
        for idx in range(1, args.generate_count + 1):
            proc = subprocess.run(args.generator_cmd, shell=True, capture_output=True, text=True, check=True)
            cases.append({"id": f"generated-{idx}", "payload": proc.stdout, "file": ""})
    return cases


def run_target(cmd_template: str, case: dict[str, str], timeout: float) -> dict[str, Any]:
    uses_placeholder = "{input}" in cmd_template or "{file}" in cmd_template
    rendered = cmd_template.replace("{input}", case["payload"]).replace("{file}", case["file"])
    proc = subprocess.run(
        rendered,
        shell=True,
        input=None if uses_placeholder else case["payload"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def normalize_text(
    text: str,
    *,
    sort_json: bool,
    ignore_whitespace: bool,
    strip_timestamps: bool,
    strip_uuids: bool,
    regex_rules: list[tuple[re.Pattern[str], str]],
) -> str:
    out = text.replace("\r\n", "\n")
    if sort_json:
        try:
            parsed = json.loads(out)
            out = json.dumps(parsed, indent=2, sort_keys=True)
        except json.JSONDecodeError:
            pass
    if strip_timestamps:
        out = ISO_TS_RE.sub("<TIMESTAMP>", out)
    if strip_uuids:
        out = UUID_RE.sub("<UUID>", out)
    for pattern, repl in regex_rules:
        out = pattern.sub(repl, out)
    if ignore_whitespace:
        out = "\n".join(" ".join(ln.split()) for ln in out.splitlines() if ln.strip())
    return out.strip()


def load_approved_diffs(path_str: str | None) -> list[dict[str, str]]:
    if not path_str:
        return []
    raw = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else raw.get("approved_diffs", [])


def match_approved_diff(
    case: dict[str, str],
    unified_diff: str,
    rules: list[dict[str, str]],
) -> str | None:
    for rule in rules:
        input_re = rule.get("input_regex")
        diff_re = rule.get("diff_regex")
        case_id = rule.get("case_id")
        if case_id and case_id != case["id"]:
            continue
        if input_re and not re.search(input_re, case["payload"]):
            continue
        if diff_re and not re.search(diff_re, unified_diff, re.MULTILINE):
            continue
        return rule.get("reason", "approved intentional difference")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-cmd", required=True, help="Oracle command (stdin fed unless {input}/{file} used)")
    parser.add_argument("--new-cmd", required=True, help="Candidate command (stdin fed unless {input}/{file} used)")
    parser.add_argument("--inputs-file", help="Line-delimited inputs file")
    parser.add_argument("--inputs-dir", help="Directory of input files")
    parser.add_argument("--generator-cmd", help="Shell command generating one random input on stdout per invocation")
    parser.add_argument("--generate-count", type=int, default=50, help="Inputs to generate with --generator-cmd")
    parser.add_argument("--keep-empty-lines", action="store_true", help="Include blank lines from --inputs-file")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-invocation timeout in seconds")
    parser.add_argument("--include-stderr", action="store_true", help="Compare normalized stderr as well as stdout")
    parser.add_argument("--sort-json", action="store_true", help="Canonicalize JSON outputs before comparing")
    parser.add_argument("--ignore-whitespace", action="store_true", help="Normalize whitespace differences")
    parser.add_argument("--strip-timestamps", action="store_true", help="Replace ISO-8601 timestamps with <TIMESTAMP>")
    parser.add_argument("--strip-uuids", action="store_true", help="Replace UUIDs with <UUID>")
    parser.add_argument(
        "--normalize-regex",
        action="append",
        default=[],
        metavar="PATTERN=REPL",
        help="Custom regex replacement applied before comparison (repeatable)",
    )
    parser.add_argument("--approved-diffs", help="Path to JSON file listing approved intentional differences")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    cases = load_inputs(args)
    if not cases:
        print("Error: No inputs provided. Pass --inputs-file, --inputs-dir, or --generator-cmd.", file=sys.stderr)
        return 2

    regex_rules: list[tuple[re.Pattern[str], str]] = []
    for spec in args.normalize_regex:
        if "=" not in spec:
            print(f"Error: Invalid --normalize-regex '{spec}' (expected PATTERN=REPL).", file=sys.stderr)
            return 2
        pat, repl = spec.split("=", 1)
        regex_rules.append((re.compile(pat), repl))

    approved_rules = load_approved_diffs(args.approved_diffs)
    norm_kwargs = {
        "sort_json": args.sort_json,
        "ignore_whitespace": args.ignore_whitespace,
        "strip_timestamps": args.strip_timestamps,
        "strip_uuids": args.strip_uuids,
        "regex_rules": regex_rules,
    }

    matches = 0
    approved: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []

    for case in cases:
        old_res = run_target(args.old_cmd, case, args.timeout)
        new_res = run_target(args.new_cmd, case, args.timeout)

        old_norm_out = normalize_text(old_res["stdout"], **norm_kwargs)
        new_norm_out = normalize_text(new_res["stdout"], **norm_kwargs)
        old_norm_err = normalize_text(old_res["stderr"], **norm_kwargs) if args.include_stderr else ""
        new_norm_err = normalize_text(new_res["stderr"], **norm_kwargs) if args.include_stderr else ""

        same = (
            old_res["returncode"] == new_res["returncode"]
            and old_norm_out == new_norm_out
            and old_norm_err == new_norm_err
        )
        if same:
            matches += 1
            continue

        old_view = f"exit={old_res['returncode']}\n{old_norm_out}" + (
            f"\n[stderr]\n{old_norm_err}" if args.include_stderr else ""
        )
        new_view = f"exit={new_res['returncode']}\n{new_norm_out}" + (
            f"\n[stderr]\n{new_norm_err}" if args.include_stderr else ""
        )
        udiff = "".join(
            difflib.unified_diff(
                old_view.splitlines(keepends=True),
                new_view.splitlines(keepends=True),
                fromfile=f"old ({case['id']})",
                tofile=f"new ({case['id']})",
            )
        )
        approved_reason = match_approved_diff(case, udiff, approved_rules)
        record = {
            "case_id": case["id"],
            "old_exit": old_res["returncode"],
            "new_exit": new_res["returncode"],
            "diff": udiff,
        }
        if approved_reason:
            approved.append({**record, "reason": approved_reason})
        else:
            regressions.append(record)

    report = {
        "total_cases": len(cases),
        "identical": matches,
        "approved_differences": len(approved),
        "unexpected_divergences": len(regressions),
        "approved": approved,
        "regressions": regressions,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Differential Oracle Summary ({len(cases)} cases): "
            f"{matches} identical, {len(approved)} approved diffs, {len(regressions)} unexpected divergences"
        )
        for item in approved:
            print(f"  [APPROVED] {item['case_id']}: {item['reason']}")
        for reg in regressions[:10]:
            print(f"\n  [DIVERGENCE] {reg['case_id']} (old exit {reg['old_exit']} vs new exit {reg['new_exit']}):")
            for ln in reg["diff"].splitlines():
                print(f"    {ln}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
