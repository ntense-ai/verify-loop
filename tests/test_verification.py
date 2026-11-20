"""Regression tests for the public CLI and hook entry points."""
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[1] / ".agents/skills/verify-loop"


class VerificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "sample repo"
        self.repo.mkdir()
        self.env = dict(os.environ, VERIFY_LOOP_STATE_DIR=str(self.root / "ledger"),
                        VERIFY_SESSION_ID="review-tests", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Tester")
        self.git("config", "user.email", "tester@example.invalid")
        self.file = self.repo / "sample.txt"
        self.file.write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")

    def invoke(self, *args, code=0, data=None):
        result = subprocess.run(args, cwd=self.repo, env=self.env, input=data,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout

    def git(self, *args, **kw):
        return self.invoke("git", *args, **kw)

    def script(self, name, *args, **kw):
        return self.invoke(sys.executable, "-I", str(SKILL / "scripts" / name), *args, **kw)

    def guard(self, *args, **kw):
        return self.script("verify_guard.py", *args, **kw)

    def stage(self, content):
        self.file.write_text(content)
        self.git("add", "sample.txt")

    def passed(self):
        self.guard("begin")
        self.guard("record", "--status", "pass", "--checks", "smoke")

    def test_candidate_requires_same_head_index_and_tested_files(self):
        self.stage("candidate\n")
        self.file.write_text("unstaged fix\n")
        self.guard("begin", code=1)
        self.stage("fixed\n")
        self.guard("begin")
        self.stage("different\n")
        self.guard("record", "--status", "pass", "--checks", "smoke", code=1)
        self.passed()
        self.file.write_text("another edit\n")
        self.guard("stamp-message", "--message", "Update", code=1)
        self.stage("another edit\n")
        self.guard("stamp-message", "--message", "Update", code=2)

    def test_mode_change_expires_stamp(self):
        self.passed()
        self.git("update-index", "--chmod=+x", "sample.txt")
        result = subprocess.run([sys.executable, str(SKILL / "scripts/verify_guard.py"),
                                 "stamp-message", "--message", "Mode change"],
                                cwd=self.repo, env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_failure_invalidates_stamp(self):
        self.passed()
        self.guard("record", "--status", "fail", "--checks", "smoke")
        self.guard("stamp-message", "--message", "Update", code=2)

    def test_git_gate_real_commits_and_audit(self):
        trusted = self.root / "trusted scripts"
        shutil.copytree(SKILL / "scripts", trusted)
        hooks = Path(self.git("rev-parse", "--absolute-git-dir").strip()) / "hooks"
        hook = hooks / "commit-msg"
        shutil.copy(SKILL / "resources/commit-msg", hook)
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        self.git("config", "verifyloop.runtime", str(trusted))
        self.git("config", "verifyloop.gateMode", "enforce")
        self.stage("one\n")
        self.git("commit", "-qm", "Unverified", code=1)
        self.passed()
        self.git("commit", "-qm", "Missing trailer", code=1)
        msg = self.guard("stamp-message", "--message", "feat: one")
        self.stage("two\n")
        self.git("commit", "-qm", msg, code=1)
        self.passed()
        self.git("commit", "-qm", msg)
        self.git("commit", "--allow-empty", "-qm", "Docs\n\nVerified-By: none (example notes)")
        audit = json.loads(self.guard("audit-commits", "--json"))
        self.assertEqual([audit[k] for k in ("total", "verified", "unstamped", "explicit_none")], [3, 1, 1, 1])

    def test_git_partial_commit_uses_its_temporary_index(self):
        second = self.repo / "second.txt"
        second.write_text("initial")
        self.git("add", ".")
        self.git("commit", "-qm", "Two files")
        hooks = Path(self.git("rev-parse", "--absolute-git-dir").strip()) / "hooks"
        shutil.copy(SKILL / "resources/commit-msg", hooks)
        self.git("config", "verifyloop.runtime", str(SKILL / "scripts"))
        self.git("config", "verifyloop.gateMode", "enforce")
        self.stage("new first")
        second.write_text("new second")
        self.git("add", "second.txt")
        self.passed()
        message = self.guard("stamp-message", "--message", "Both changes")
        self.git("commit", "-qm", message, "--only", "--", "sample.txt", code=1)
        self.git("commit", "-qm", message)

    def test_untrusted_bootstrap_and_multi_workspace(self):
        (self.repo / ".verify-loop.json").write_text('{"gateMode":"off"}')
        config = json.loads(self.script("commit_gate_hook.py", "--inspect-log", "--json"))
        self.assertEqual(config["active_mode"], "observe")
        other = self.root / "other"
        other.mkdir()
        self.git("-C", str(other), "init", "-q")
        self.git("-C", str(other), "config", "verifyloop.gateMode", "off")
        self.git("config", "verifyloop.gateMode", "enforce")
        for cwd, command in [(self.repo, "git commit"), (other, "git -C '../sample repo' commit")]:
            payload = {"toolCall": {"name": "run_command", "args": {"Cwd": str(cwd), "CommandLine": command}},
                       "workspacePaths": [str(other), str(self.repo)], "conversationId": str(cwd)}
            decision = json.loads(self.script("commit_gate_hook.py", data=json.dumps(payload)))
            self.assertEqual(decision["decision"], "deny")

    def test_deleted_assertion_and_unborn_staged_test(self):
        self.repo = self.root / "new"
        self.repo.mkdir()
        self.git("init", "-q")
        f = self.repo / "test_sample.py"
        f.write_text("import time\ntime.sleep(1)\nassert True\n")
        self.git("add", ".")
        def issues():
            return {i["type"] for i in json.loads(self.guard("triage", "--json"))["integrity_issues"]}
        self.assertIn("test_sleep_added", issues())
        self.git("-c", "user.name=A", "-c", "user.email=a@example.invalid", "commit", "-qm", "Base")
        f.write_text("import time\ntime.sleep(1)\n")
        self.assertIn("test_check_removed", issues())

    def test_failed_fix_budget_per_check_and_task(self):
        self.guard("begin", "--new-task")
        def failure(check, *args, code=0):
            return json.loads(self.guard("record", "--status", "fail", "--checks", check,
                                         "--json", *args, code=code))
        self.assertEqual(failure("a")["failed_rounds"], 0)
        for check in ("a", "b", "c"):
            self.assertFalse(failure(check, "--after-fix")["must_escalate"])
        self.guard("record", "--status", "resolved", "--checks", "a")
        self.assertEqual(failure("a", "--after-fix")["round"], 1)
        failure("b", "--after-fix")
        self.assertTrue(failure("b", "--after-fix", code=3)["must_escalate"])
        self.guard("begin", "--new-task")
        self.assertEqual(failure("b")["round"], 0)

    def compare(self, old, new, *args, code=0):
        return self.script("diff_oracle.py", "--old-cmd", old, "--new-cmd", new,
                           "--inputs-dir", str(self.corpus), "--json", *args, code=code)

    def test_literal_arguments_and_exact_approvals(self):
        self.corpus = self.root / "inputs"
        self.corpus.mkdir()
        case = self.corpus / "customer's report.txt"
        case.write_bytes(b"two words\n'quoted' {file}\r\n")
        def target(program, *args):
            return shlex.join([sys.executable, "-c", program, *args])
        by_file = target("import sys;sys.stdout.buffer.write(open(sys.argv[1],'rb').read())", "{file}")
        by_arg = target("import sys;sys.stdout.buffer.write(sys.argv[1].encode())", "{input}")
        self.assertEqual(json.loads(self.compare(by_file, by_arg))["identical"], 1)
        a = target("print(' old',end='')")
        b = target("print('old')")
        first = json.loads(self.compare(a, b, code=1))["regressions"][0]
        approved = self.root / "approved.json"
        approved.write_text(json.dumps([{"case_id": case.name, "reason": "output format",
                                        "difference_sha256": first["difference_sha256"]}]))
        opts = ["--approved-diffs", str(approved)]
        self.assertEqual(json.loads(self.compare(a, b, *opts))["approved_differences"], 1)
        self.compare(a, target("print('old count=0')"), *opts, code=1)
        self.compare(target("print('old',end='')"), b, code=1)
        approved.write_text("[{}]")
        self.compare(a, b, *opts, code=1)
