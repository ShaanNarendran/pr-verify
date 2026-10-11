# SPDX-License-Identifier: Apache-2.0
"""Self-tests for prv.py: real git repositories in temp directories, no Docker, no network."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pr-verify" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import prv  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def git(*args, cwd, check=True, input_text=None):
    env = {**os.environ, **GIT_ENV}
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, input=input_text)
    if check and res.returncode != 0:
        raise AssertionError(res.stderr)
    return res


class RepoCase(unittest.TestCase):
    """A clone with origin/main as the base and a feature branch on top."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ.update(GIT_ENV)
        origin = self.tmp / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        self.repo = self.tmp / "repo"
        git("clone", "-q", str(origin), str(self.repo), cwd=self.tmp)
        git("checkout", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "app.py").write_text("def f():\n    return 1\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-q", "-m", "base", cwd=self.repo)
        git("push", "-q", "-u", "origin", "main", cwd=self.repo)
        git("remote", "set-head", "origin", "main", cwd=self.repo)
        git("checkout", "-q", "-b", "feature", cwd=self.repo)

    def commit(self, path, text, msg="change"):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        git("add", ".", cwd=self.repo)
        git("commit", "-q", "-m", msg, cwd=self.repo)
        return git("rev-parse", "HEAD", cwd=self.repo).stdout.strip()

    def decide(self, command, env_skip=None):
        return prv.guard_decision(command, str(self.repo), env_skip)


class TestClassify(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(prv.classify([], "")["tier"], "skip")
        self.assertEqual(prv.classify(["README.md", "docs/guide.md", "img/a.png"], "")["tier"], "skip")
        self.assertEqual(prv.classify(["web/src/Button.css"], "")["tier"], "standard")
        deep = prv.classify(["server/api/routes/auth.py"], "token = make_jwt(user)")
        self.assertEqual(deep["tier"], "deep")
        self.assertIn("auth", deep["areas"])
        self.assertIn("api", deep["areas"])
        # content alone can raise the tier: a "harmless" util that starts shelling out
        self.assertEqual(prv.classify(["lib/util.py"], "subprocess.run(cmd, shell=True)")["tier"], "deep")

    def test_common_words_do_not_inflate_the_tier(self):
        # ordinary async code, telemetry "sessions" and LLM "tokens" are not auth or concurrency changes
        plain = prv.classify(
            ["services/session_ingest.py"], "result = await client.get(url)\ntokens = count_tokens(text)"
        )
        self.assertEqual(plain["tier"], "standard", plain)
        # but a changed query is a data change
        query = prv.classify(["services/session_ingest.py"], "stmt = select(Agent.id).where(Agent.name == n).limit(2)")
        self.assertEqual(query["tier"], "deep")
        self.assertIn("data", query["areas"])


class TestCompare(unittest.TestCase):
    def test_new_vs_preexisting(self):
        base = {"a": "pass", "b": "fail", "c": "pass"}
        pr = {"a": "fail", "b": "fail", "c": "pass", "d": "fail"}
        r = prv.compare_results(base, pr)
        self.assertEqual(r["verdict"], "fail")
        self.assertEqual(r["new_failures"], ["a", "d"])  # d is a brand-new failing test
        self.assertEqual(r["preexisting_failures"], ["b"])
        self.assertEqual(prv.compare_results({"b": "fail"}, {"b": "fail"})["verdict"], "pass")

    def test_junit_parsing(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "r.xml").write_text(
                '<testsuite><testcase classname="m" name="ok"/>'
                '<testcase classname="m" name="bad"><failure message="x"/></testcase>'
                '<testcase classname="m" name="skipped"><skipped/></testcase></testsuite>'
            )
            res = prv._junit(f"{d}/*.xml")
        self.assertEqual(res, {"m::ok": "pass", "m::bad": "fail", "m::skipped": "skip"})


class TestArgs(unittest.TestCase):
    def test_options_before_the_command_separator_are_parsed(self):
        seen = {}
        orig = prv.run_in_sandbox
        prv.run_in_sandbox = lambda label, tree, command, timeout, tail: seen.update(locals()) or {}
        try:
            with redirect_stdout(io.StringIO()):
                prv.main(["run", "setup-base", "--in", "base", "--timeout", "60", "--", "cd x && make", "--in", "y"])
        finally:
            prv.run_in_sandbox = orig
        self.assertEqual((seen["tree"], seen["timeout"]), ("base", 60))
        self.assertEqual(seen["command"], "cd x && make --in y")  # flags after -- belong to the command


class TestCiSteps(unittest.TestCase):
    def test_run_blocks(self):
        yaml = (
            "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n"
            "      - run: uv sync\n      - name: tests\n        run: |\n          make lint\n          make test\n"
            "      - run: 'echo done'\n"
            "      - run: |\n          pytest tests \\\n            -n auto\n"
        )
        # echo-only steps are noise; backslash continuations join into one command
        self.assertEqual(prv._run_blocks(yaml), ["uv sync", "make lint && make test", "pytest tests -n auto"])


class TestGuard(RepoCase):
    def test_non_push_and_dry_runs_pass(self):
        self.commit("app.py", "def f():\n    return 2\n")
        self.assertTrue(self.decide("git status")[0])
        self.assertTrue(self.decide("git push --dry-run origin feature")[0])
        self.assertTrue(self.decide("git push origin --delete old-branch")[0])

    def test_unverified_code_is_blocked_and_stamp_unblocks(self):
        sha = self.commit("app.py", "def f():\n    return 2\n")
        for cmd in ("git push origin feature", "cd x && git push", "gh pr create --fill", f"git -C {self.repo} push"):
            allowed, msg = self.decide(cmd)
            self.assertFalse(allowed, cmd)
            self.assertIn(sha[:10], msg)
        d = prv.stamp_dir(str(self.repo))
        d.mkdir(parents=True)
        (d / f"{sha}.json").write_text(json.dumps({"sha": sha, "verdict": "fail"}))
        self.assertIn("failed verification", self.decide("git push")[1])
        (d / f"{sha}.json").write_text(json.dumps({"sha": sha, "verdict": "pass"}))
        self.assertTrue(self.decide("git push")[0])
        # a new commit needs a new verification
        self.commit("app.py", "def f():\n    return 3\n")
        self.assertFalse(self.decide("git push")[0])

    def test_docs_only_change_is_not_blocked(self):
        self.commit("docs/guide.md", "# guide\n")
        self.assertTrue(self.decide("git push origin feature")[0])

    def test_guard_cli_entry_points(self):
        """Drive `prv guard` the way the hooks do: --command (pi) and hook JSON on stdin (Claude Code)."""
        self.commit("app.py", "def f():\n    return 2\n")
        script = str(SCRIPTS / "prv.py")
        by_flag = subprocess.run(
            [sys.executable, script, "guard", "--command", "git push origin feature", "--cwd", str(self.repo)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(by_flag.returncode, 2, by_flag.stderr)
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(self.repo)})
        by_stdin = subprocess.run([sys.executable, script, "guard"], input=payload, capture_output=True, text=True)
        self.assertEqual(by_stdin.returncode, 2, by_stdin.stderr)
        self.assertIn("pr-verify", by_stdin.stderr)
        harmless = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(self.repo)})
        allowed = subprocess.run([sys.executable, script, "guard"], input=harmless, capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0)

    def test_override_is_logged(self):
        sha = self.commit("app.py", "def f():\n    return 2\n")
        allowed, msg = self.decide('PR_VERIFY_SKIP="hotfix for outage" git push')
        self.assertTrue(allowed)
        log = (prv.stamp_dir(str(self.repo)) / "overrides.log").read_text()
        self.assertIn(sha, log)
        self.assertIn("hotfix for outage", log)
        self.assertTrue(self.decide("git push", env_skip="ci bot")[0])

    def test_git_pre_push_hook_blocks_real_push_and_chains_existing_hook(self):
        hooks = self.repo / ".git" / "hooks"
        marker = self.tmp / "previous-hook-ran"
        (hooks / "pre-push").write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / "pre-push").chmod(0o755)
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with redirect_stdout(io.StringIO()):
                prv.cmd_install_git_hook(None)
        finally:
            os.chdir(cwd)
        sha = self.commit("app.py", "def f():\n    return 2\n")
        blocked = git("push", "-q", "origin", "feature", cwd=self.repo, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("has not been verified", blocked.stderr)
        self.assertFalse(marker.exists())
        d = prv.stamp_dir(str(self.repo))
        d.mkdir(parents=True)
        (d / f"{sha}.json").write_text(json.dumps({"sha": sha, "verdict": "pass"}))
        git("push", "-q", "origin", "feature", cwd=self.repo)
        self.assertTrue(marker.exists(), "the pre-existing pre-push hook must still run")


if __name__ == "__main__":
    unittest.main()
