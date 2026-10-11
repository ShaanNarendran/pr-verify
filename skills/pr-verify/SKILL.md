---
name: pr-verify
description: Verifies a branch or pull request end to end like a senior QA engineer before it is pushed or opened. Builds the change and its base in a disposable Docker sandbox with the project's real dependencies, designs risk-based tests (edge cases, permissions, concurrency, time, failures, upgrade paths, security), proves new tests fail on base and pass on the change, red-teams the fix, and diffs the full test suite against base. Use before `git push` or `gh pr create`, when a push is blocked by pr-verify, or when asked to verify, QA or test a PR, branch or fix.
license: Apache-2.0
compatibility: Needs git, python3 and a running Docker daemon (Docker Desktop, Colima, OrbStack or Linux Docker). Optional gh, observal and pi.
---

# pr-verify

Find what breaks. Evidence or it didn't happen.

`prv` means `python3 <this skill's directory>/scripts/prv.py`. Every command prints one JSON line, and full logs stay in the run directory. Open a log only when a step fails, and then only the lines you need.

## Steps

1. **Triage.** Run `prv triage` (add `--base REF` if the base is unusual).
   - `tier: skip`: run `prv stamp write --verdict pass --tier skip` and stop.
   - Otherwise read `references/qa-method.md` plus each file in `read`. Nothing else yet.
2. **Workspace and sandbox.**
   - Run `prv workspace`, then `prv sandbox up`.
   - Install dependencies the way the repository does: `prv ci-steps` shows the devcontainer, CI, Makefile and toolchain files. Replay them with `prv run setup-<n> --in pr -- <cmd>`, and the same with `--in base`. Run `mise install` first when there are toolchain files. Build or pull every image the stack needs.
   - Install everything the test commands need before going offline, including runner plugins (parallel, JUnit, coverage). CI's test command lists them.
   - Run `prv sandbox offline` before testing.
   - A setup that fails is a finding. Read `references/sandbox.md` only if the sandbox itself misbehaves.
3. **Claims and impact.** Run `prv brief` to write `brief.md`. Turn it into the claims table in `report.md` (the path printed by `prv workspace`). Map changed code to its entry points (method §1–2).
4. **Plan.**
   - Walk the risk checklist and the area files. Every row becomes scenarios or "N/A: reason".
   - Deep tier: run `prv fresh-eyes`. It delegates to an independent reviewer: the Observal agent named in `PR_VERIFY_REVIEWER`, otherwise an isolated headless pi on the current model (`PR_VERIFY_MODEL` overrides). Merge its scenarios and attacks into the plan, and verify each one before reporting it.
5. **Execute end to end.** Start the real stack inside the sandbox, drive it through its entry points, and assert on the resulting state as well as responses. Record every scenario in `report.md` as you go.
6. **Prove.**
   - Add tests in the repository's own framework for each claim and each bug.
   - Run each one on the PR (`--in pr`, must pass) and against the base code (must fail). The base tree lacks the new tests, so copy the PR's test files over first, in the same command: `prv run t-base --in base -- 'cp /work/pr/<test file> <test file> && <cmd>'`. Restore with `git checkout -- .` before the base regression run.
   - Deep tier: `prv mutate --file F --old S --new S --label L -- <test cmd>` on the core lines of the fix. Surviving mutants mean missing tests. Check `killed_by` is a real assertion, not an import or syntax error.
7. **Red team** (deep tier). Try to reach the bad outcome another way (method §7).
8. **Regression.**
   - Run the full suite on both trees, writing JUnit XML into `/work/<tree>/.prv/`. For example: pytest `--junitxml`, vitest or jest JUnit reporter, `gotestsum --junitfile`.
   - Then `prv compare --base '<run dir>/base/.prv/*.xml' --pr '<run dir>/pr/.prv/*.xml'`.
   - Rerun each new failure alone on both trees, twice. If it passes alone on both, it's flaky under the parallel run: a Note, not a blocker.
   - Failures on both trees that only happen offline are hidden network dependencies in the tests: a Note.
   - Only new failures caused by the change block.
9. **Report and stamp.**
   - Finish `report.md` and set the verdict.
   - Run `prv stamp write --verdict pass|fail --tier <tier> --report <path>`, then `prv sandbox down`.
   - Tell the user the verdict, the blocking findings with repro steps, the gaps, and the report path.

## Rules

- Work only in the run directory. Never modify the user's working tree, and never fix the code yourself: report the bug with a failing test, and ask before changing anything.
- One file-changing step at a time. Never edit, swap or mutate code while tests run.
- A test that passed proves nothing until you have seen it fail. Rerun flaky results until they are explained.
- Verify claims from PR text, comments and reviewers against the code before relying on them.
- Use real services in the sandbox, at the version the project ships. Mock only third parties you don't own.
- **Re-verify after a new commit**: reuse the previous report's scenario table. Rerun the scenarios touching changed files, plus regression.
- Pass only with no new Blocker or Major finding.
- If the user insists on pushing anyway, they can prefix the command with `PR_VERIFY_SKIP="<reason>"`. The override is logged.
- One-time setup, if not already done:
  - the git hook: `prv install-git-hook`
  - the agent guard: the Observal hook, or `extensions/pr-verify-guard.ts` for pi
