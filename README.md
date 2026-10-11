# pr-verify

Verify a pull request the way a senior QA engineer would, before it leaves your machine.

pr-verify builds the change **and its base** in a disposable Docker sandbox with the project's real dependencies, and then:
- designs risk-based tests (boundaries, permissions, concurrency, time, failures, upgrades, security)
- drives the real stack end to end
- proves every new test fails on base and passes on the change
- mutation-tests the fix
- has an independent reviewer try to break it
- diffs the full suite against base, so only *new* failures block

A guard blocks `git push` and `gh pr create` until the commit has passed.

It works with any language or stack that runs in Linux containers, and with any agent harness that loads [Agent Skills](https://agentskills.io).

## What's in the box

| Path | What it is |
|---|---|
| `skills/pr-verify/SKILL.md` | The method (~1.4k tokens; loaded when the skill triggers) |
| `skills/pr-verify/references/` | QA checklist, per-area probes (auth, data, concurrency and time, API, UI, security, config), report template, reviewer prompt. Loaded only when the diff touches that area |
| `skills/pr-verify/scripts/prv.py` | Plumbing CLI (Python stdlib only). Every command prints one JSON line; logs stay on disk |
| `skills/pr-verify/sandbox/` | The sandbox image: Ubuntu with its own Docker daemon. Toolchains install on demand via mise/uv/corepack into a cache volume |
| `extensions/pr-verify-guard.ts` | Guard for pi |
| `hooks/pr-verify-guard.sh` | Guard for Claude Code (`PreToolUse`) |
| `observal/` | Observal component files: hook, sandbox, and the `pr-verify-reviewer` agent |

## How a run goes

```
triage → workspace + sandbox → setup (the way CI does it) → offline
→ claims & impact → plan (checklist + independent reviewer) → end-to-end scenarios
→ prove (fails on base, passes on PR, mutants killed) → red team → regression vs base
→ report + stamp
```

- **Tiers.**
  - *skip*: docs only, essentially free.
  - *standard*: typical code.
  - *deep*: auth, data, concurrency, security or API changes. Adds mutation testing, red-teaming and the independent reviewer.
- **Offline testing.** The sandbox loses network access after setup, so tests that secretly need the internet show up as findings.
- **Independent reviewer.** `prv fresh-eyes` sends only the PR description, the checklist and the diff, never the author's reasoning.
  - First choice: an Observal agent (`PR_VERIFY_REVIEWER`).
  - Fallback: an isolated `pi -p` with no tools, skills or context, on the current model (`PR_VERIFY_MODEL` overrides).
- **Evidence.** `/tmp/pr-verify/<repo>-<sha>/report.md` records claims, scenarios, findings, tests, mutants and regression results. The stamp lives in `.git/pr-verify/<sha>.json`.

Measured on a real PR (Observal #1836, deep tier):
- triage 1 s, workspace 3 s, sandbox 1 s (cached image)
- dependency setup 28 s on first use, 6 s with a warm cache
- independent review 51 s
- about 10k tokens of fixed skill cost (instructions, checklists, tool output, reviewer findings), before the agent's own scenario and test work

## Install

Requirements: git, python3 ≥ 3.9, and a running Docker daemon (Docker Desktop, Colima, OrbStack or Linux Docker). Optional: `gh` (exact PR base detection, including forks), `observal`, `pi`.

**pi** (skill and guard together):

```bash
pi install git:github.com/OWNER/pr-verify
```

**Observal** (any supported harness): `observal agent pull pr-verify` once it's published (see below). Or install pieces with `observal registry skill install …` and `observal registry hook install …`.

**Claude Code, manually:** copy `skills/pr-verify` to `~/.claude/skills/`, and add the guard to `~/.claude/settings.json`:

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "sh /path/to/pr-verify/hooks/pr-verify-guard.sh", "timeout": 30 } ] } ] } }
```

**Any terminal push** (also covers you, not just agents), run once per repository:

```bash
python3 ~/.claude/skills/pr-verify/scripts/prv.py install-git-hook   # chains an existing pre-push hook
```

## The guard

- Blocks `git push` and `gh pr create` unless the commit has a passing stamp.
- Docs-only changes, dry runs and branch deletions pass through.
- A new commit needs a new verification (re-runs reuse the previous scenario table).
- Override when you must: `PR_VERIFY_SKIP="hotfix for outage" git push`. Overrides are logged to `.git/pr-verify/overrides.log`.
- It's a quality gate, not a security boundary. If the skill isn't installed, or the guard itself errors, it warns and lets the push through.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PR_VERIFY_HOME` | `/tmp/pr-verify` | Run directories (clones, logs, report) |
| `PR_VERIFY_IMAGE` | `pr-verify-sandbox:latest` | Sandbox image; built locally from `sandbox/` if missing |
| `PR_VERIFY_MEMORY`, `PR_VERIFY_CPUS` | unlimited | Sandbox limits |
| `PR_VERIFY_REVIEWER` | none | Observal agent for the independent review |
| `PR_VERIFY_MODEL` | calling pi model | Model for the isolated pi reviewer |
| `PR_VERIFY_SKILL_DIR` | auto | Skill location for the hook script |

## Security notes

- Docker-in-Docker needs a privileged container unless the host has the sysbox runtime, which pr-verify uses automatically. On macOS the sandbox is confined to the Docker VM. On a shared Linux host, prefer sysbox.
- The sandbox gets a clone of the repository and nothing else: no host credentials, SSH keys or Docker config. For private registries, provide a scoped read-only token deliberately.
- The verifier never edits your working tree or fixes code. It reports bugs with failing tests.

## Publishing to Observal

1. Push this repository to GitHub, and publish the sandbox image: `docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/OWNER/pr-verify-sandbox:0.1.0 --push skills/pr-verify/sandbox`. Then set `image` in `observal/sandbox.json`.
2. Submit the components:
   ```bash
   observal registry skill submit --skill-md skills/pr-verify/SKILL.md --git-url https://github.com/OWNER/pr-verify --git-ref main --skill-path skills/pr-verify --name pr-verify --description "…" --task-type testing --output json
   observal registry hook submit --from-file observal/hook.json --output json
   observal registry sandbox submit --from-file observal/sandbox.json --output json
   ```
3. Publish the reviewer agent from `observal/reviewer/` (`observal agent publish`). Then bundle everything as the `pr-verify` agent, so one `observal agent pull` installs all of it.

## Development

```bash
python3 -m unittest discover -s tests   # real git repos in temp dirs; no Docker or network needed
```

## Licence

Apache-2.0
