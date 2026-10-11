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


class TestPassMustBeEarned(RepoCase):
    """A passing stamp unblocks a push, so it requires the evidence the method promises."""

    FULL = "\n".join(f"| {row} | scenario or N/A: reason |" for row in prv.RISK_ROWS)

    def run_dir_with(self, report=None, compare=None, fresh=False, mutants=()):
        run = self.tmp / "run"
        run.mkdir(exist_ok=True)
        (run / "run.json").write_text("{}")
        if report is not None:
            (run / "report.md").write_text(report)
        if compare is not None:
            (run / "compare.json").write_text(json.dumps({"verdict": compare}))
        if fresh:
            (run / "fresh-eyes.md").write_text("## Scenarios")
        if mutants:
            (run / "mutants.jsonl").write_text("".join(json.dumps(m) + "\n" for m in mutants))
        return run, str(run / "report.md")

    def test_evidence_rules(self):
        self.assertEqual(prv.missing_evidence("skip", None, None), [])
        self.assertIn("no workspace", prv.missing_evidence("standard", None, None)[0])

        run, rep = self.run_dir_with(report="<!-- HEAD sha -->\n" + self.FULL, compare="pass")
        self.assertTrue(any("placeholders" in g for g in prv.missing_evidence("standard", run, rep)))

        run, rep = self.run_dir_with(report="| Inputs | x |", compare="pass")
        gaps = prv.missing_evidence("standard", run, rep)
        self.assertTrue(any("Boundaries" in g and "Permissions" in g for g in gaps), gaps)

        run, rep = self.run_dir_with(report=self.FULL, compare="fail")
        self.assertTrue(any("new failures" in g for g in prv.missing_evidence("standard", run, rep)))

        run, rep = self.run_dir_with(report=self.FULL, compare="pass")
        self.assertEqual(prv.missing_evidence("standard", run, rep), [])
        deep = prv.missing_evidence("deep", run, rep)
        self.assertTrue(any("fresh-eyes" in g for g in deep) and any("mutate" in g for g in deep), deep)

        run, rep = self.run_dir_with(
            report=self.FULL, compare="pass", fresh=True, mutants=[{"label": "m1", "killed": False}]
        )
        self.assertTrue(any("surviving mutants" in g and "m1" in g for g in prv.missing_evidence("deep", run, rep)))

        run, rep = self.run_dir_with(
            report=self.FULL, compare="pass", fresh=True, mutants=[{"label": "m1", "killed": True}]
        )
        self.assertEqual(prv.missing_evidence("deep", run, rep), [])

    def test_stamp_cli_refuses_an_unearned_pass_but_records_a_fail(self):
        sha = self.commit("app.py", "def f():\n    return 2\n")
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with redirect_stdout(io.StringIO()) as buf, self.assertRaises(SystemExit) as exc:
                prv.main(["stamp", "write", "--verdict", "pass"])  # never triaged: treated as deep, no evidence
            self.assertEqual(exc.exception.code, 1)
            self.assertIn('"stamped":false', buf.getvalue())
            self.assertFalse((prv.stamp_dir() / f"{sha}.json").exists())
            with redirect_stdout(io.StringIO()):
                prv.main(["stamp", "write", "--verdict", "fail"])
            self.assertEqual(json.loads((prv.stamp_dir() / f"{sha}.json").read_text())["verdict"], "fail")
        finally:
            os.chdir(cwd)


class TestGitHookGuard(RepoCase):
    """The guard as it ships: a git pre-push hook, exercised with real pushes to a bare remote."""

    def setUp(self):
        super().setUp()
        hooks = self.repo / ".git" / "hooks"
        self.marker = self.tmp / "previous-hook-ran"
        (hooks / "pre-push").write_text(f"#!/bin/sh\ntouch {self.marker}\n")
        (hooks / "pre-push").chmod(0o755)
        self.install()

    def install(self):
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with redirect_stdout(io.StringIO()):
                prv.cmd_install_git_hook(None)
        finally:
            os.chdir(cwd)

    def push(self, *args, env=None):
        if env:
            os.environ.update(env)
        try:
            return git("push", "-q", "origin", *(args or ("feature",)), cwd=self.repo, check=False)
        finally:
            for k in env or {}:
                os.environ.pop(k, None)

    def stamp(self, sha, verdict="pass"):
        d = prv.stamp_dir(str(self.repo))
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sha}.json").write_text(json.dumps({"sha": sha, "verdict": verdict}))

    def remote_has(self, branch):
        return git("ls-remote", "origin", f"refs/heads/{branch}", cwd=self.repo).stdout.strip() != ""

    def test_unverified_push_is_blocked_until_stamped_and_each_commit_needs_its_own_stamp(self):
        sha = self.commit("app.py", "def f():\n    return 2\n")
        blocked = self.push()
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(f"{sha[:10]} has not been verified", blocked.stderr)
        self.assertFalse(self.remote_has("feature"))
        self.assertFalse(self.marker.exists(), "chained hook must not run when the guard blocks")

        self.stamp(sha)
        self.assertEqual(self.push().returncode, 0)
        self.assertTrue(self.remote_has("feature"))
        self.assertTrue(self.marker.exists(), "the pre-existing pre-push hook must still run")

        self.commit("app.py", "def f():\n    return 3\n")
        self.assertNotEqual(self.push().returncode, 0)

    def test_failed_verdict_blocks(self):
        sha = self.commit("app.py", "def f():\n    return 2\n")
        self.stamp(sha, verdict="fail")
        self.assertIn("failed verification", self.push().stderr)

    def test_docs_only_change_and_branch_deletion_pass(self):
        self.commit("docs/guide.md", "# guide\n")
        self.assertEqual(self.push().returncode, 0)
        self.commit("app.py", "def f():\n    return 2\n")  # unverified code on the branch now
        self.assertEqual(self.push("--delete", "feature").returncode, 0)
        self.assertFalse(self.remote_has("feature"))

    def test_override_is_logged(self):
        sha = self.commit("app.py", "def f():\n    return 2\n")
        self.assertEqual(self.push(env={"PR_VERIFY_SKIP": "hotfix for outage"}).returncode, 0)
        log = (prv.stamp_dir(str(self.repo)) / "overrides.log").read_text()
        self.assertIn(f"{sha} hotfix for outage", log)

    def test_install_is_idempotent_and_detected(self):
        self.assertTrue(prv.git_hook_installed(str(self.repo)))
        self.install()  # reinstalling must not chain the guard to itself
        chained = (self.repo / ".git" / "hooks" / "pre-push.before-pr-verify").read_text()
        self.assertNotIn("pr-verify", chained)
        self.commit("app.py", "def f():\n    return 2\n")
        self.assertNotEqual(self.push().returncode, 0)


if __name__ == "__main__":
    unittest.main()
