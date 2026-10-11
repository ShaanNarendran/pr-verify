# QA method

Your job is to find what breaks, not to confirm that it works. A passing test is evidence only after you have seen it fail for the right reason.

## 1. Claims become acceptance criteria

Write down what the change promises, before reading the implementation. Use the PR title and body, commit messages, any linked issue, docs and changelog edits. Number each claim as a testable statement: "Given X, when Y, then Z".

- Behaviour the diff changes but the description never mentions is a finding (undocumented change).
- A claim with no way to observe it ("improves reliability") becomes a question for the author, not a pass.

## 2. Impact map

From the diff, list:

- **changed units**: functions, classes, queries, components, config keys, migrations
- **callers**: search the repository for each changed name; include dynamic references (strings, registries, decorators, reflection)
- **entry points that reach them**: HTTP routes, CLI commands, UI screens, jobs and schedulers, message consumers, webhooks, public library APIs
- **state they touch**: tables, caches, queues, files, external services
- **who is affected**: roles, tenants or teams, old clients, other services, operators

Every entry point that reaches changed code gets at least one end-to-end scenario.

## 3. Risk checklist

Walk every row. Each row ends as one or more scenarios, or as "N/A: <reason>". Never skip silently.

| Area | Probe |
|---|---|
| Inputs | empty, missing, null, whitespace, very long, unicode and emoji, wrong type, duplicates, malicious (injection, traversal, oversized, malformed encodings) |
| Boundaries | 0, 1, max, max+1, negative, first and last item, last page, exact limit, off-by-one around every comparison in the diff |
| Permissions | anonymous, owner, other user, other tenant or team, lowest role, admin, deactivated, revoked or expired credentials. Can this reveal that something exists? |
| State and order | repeat the action, retry after failure, out-of-order calls, double submit, resume after a crash, idempotency keys |
| Concurrency | two requests at once on the same object, read-modify-write races, check-then-act, a change landing mid-operation (logout during refresh, delete during edit) |
| Time | time zones, DST, end of month or year, expiry exactly now, same-second and same-millisecond events, clock skew between servers, slow clocks |
| Dependency failure | database, cache, queue or third-party down, slow or timing out, partial writes. Does it fail closed? Is data lost or duplicated? Is the error honest? |
| Data and upgrade | existing rows and legacy formats, null in old columns, migrations up and down, mixed old and new servers during a rolling deploy, old clients against the new API |
| Scale | large inputs, many rows, pagination, one query per item (N+1), memory growth, unbounded lists or caches |
| Security | authn/authz on every new path, SSRF, injection, secrets in logs, errors or responses, unsafe deserialization, file paths, rate limits |
| Contracts | response shape and status codes, backwards compatibility, error messages, `--json` output, docs and help text matching behaviour |
| Operations | logs useful and not noisy, metrics, config flags and defaults, behaviour with a flag off, cleanup of temp state |
| Platform | OS, architecture, browser, locale, Python, Node or other runtime versions the project supports |

The area files listed by triage add deeper probes for what this change touches.

## 4. Designing the cases

Use the technique that fits, and say which one you used:

- **Equivalence classes and boundary values** for inputs and limits.
- **State transitions** for anything with a lifecycle (draft, pending, approved; logged in or out; open or closed).
- **Decision tables** when the outcome depends on several conditions together, such as role × visibility × ownership.
- **Pairwise** when the combinations explode.
- **Error guessing** from the diff itself: every new `if`, comparison, default value, `try/except` and early return is a hypothesis to attack.
- **Property-based tests** (Hypothesis, fast-check) for parsers, encoders, sorting, money and anything with an invariant.

## 5. Executing end to end

- Run the real stack in the sandbox (`docker compose`, the app, its database and cache). Drive it the way a user or client would: HTTP calls, the real CLI, the browser for UI.
- After each scenario, assert on the **resulting state** (database rows, cache keys, files, emitted events, logs) as well as the response.
- Mock only third-party services you do not own. When you do, record what the mock assumes.
- Reset state between scenarios, or make each scenario create its own data.
- Run a scenario that found a bug on the base tree too. If it fails there as well, it is pre-existing, not caused by this PR.

## 6. Turning scenarios into tests

For every claim and every bug found, add an automated test in the repository's own framework and style, next to similar tests.

- It must **fail on base** and **pass on the PR**. Run it on both and keep the output.
- If it passes on base, it does not test the change. Rewrite it.
- **Mutation check** (deep tier): make the minimal break to the core line of the fix (flip a comparison, drop a condition, return early, remove the new call). The tests must fail. A surviving mutant means a missing test.

## 7. Red team (deep tier)

Assume the fix is incomplete and try to reach the same bad outcome another way: a different entry point, an alternate encoding, a legacy data shape, a race, a retry, a different role, a configuration flag. Every bypass is a blocker.

## 8. Evidence and severity

Every scenario records: id, claim or risk row, steps, expected (from the claim, not from the code), actual, evidence (command and log path), and result.

| Severity | Meaning |
|---|---|
| Blocker | data loss, security hole, crash, a claim not met, regression in existing behaviour |
| Major | wrong result in a realistic case, misleading error, missing test for risky logic |
| Minor | edge case with a workaround, rough message, small inconsistency |
| Note | observation, question for the author, pre-existing issue |

The verdict is pass only when there is no new Blocker or Major.

## 9. Do not fool yourself

- Never edit, swap or mutate files while tests run, and never run two of those steps at once. A run that saw half-swapped code proves nothing.
- Check that a "pass" ran against the code you meant (tree, commit, environment) before trusting it.
- Flaky? Rerun until you can explain it. "Passed on retry" is a finding, not a pass.
- Verify claims from PR text, comments and code reviewers against the code before acting on them.
- A fake (in-memory Redis, mocked HTTP) can hide real behaviour such as missing commands, TTLs and eviction. Prefer the real service at the version the project ships.
- Timing assumptions are hypotheses: measure, don't guess.
