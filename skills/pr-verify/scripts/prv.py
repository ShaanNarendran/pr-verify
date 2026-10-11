#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""pr-verify plumbing.

Deterministic work lives here so the model only reads one short JSON line per step.
Full logs stay on disk under the run directory. Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RUNS = Path(os.environ.get("PR_VERIFY_HOME", "/tmp/pr-verify"))
IMAGE = os.environ.get("PR_VERIFY_IMAGE", "pr-verify-sandbox:latest")
DIFF_LIMIT = 60_000  # characters of diff handed to the fresh-eyes reviewer

DOC_FILE = re.compile(
    r"(^|/)(docs?|documentation)/|\.(md|mdx|rst|txt|adoc)$|(^|/)(LICENSE|NOTICE|CHANGELOG|AUTHORS|CODEOWNERS)[^/]*$"
    r"|\.(png|jpe?g|gif|svg|webp|ico)$",
    re.IGNORECASE,
)

# area -> (path pattern, added-line pattern). Matching either marks the area.
AREAS: dict[str, tuple[str, str]] = {
    "auth": (
        r"auth|login|logout|oauth|saml|sso|jwt|credential|password|permission|rbac|acl|polic(y|ies)",
        r"\b(password|passwd|jwt|access_token|refresh_token|bearer|permission|is_admin|authorize|authenticate|"
        r"csrf|set_cookie|require_role|current_user)\b",
    ),
    "data": (
        r"migration|alembic|schema|models?/|\.sql$|repositor|dao/|database|prisma|knex",
        r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE TABLE|LIMIT|commit|rollback|transaction|migrate|bulk_create)\b"
        r"|\b(select|\.where|\.filter|\.query|\.execute|\.order_by|\.limit)\(",
    ),
    "concurrency": (
        r"worker|queue|job|cron|scheduler|celery|arq|sidekiq|lock",
        r"\b(thread|Thread|Lock|RLock|mutex|Mutex|semaphore|goroutine|race|retry|atomic|to_thread|run_in_executor)\b"
        r"|asyncio\.gather|Promise\.all|\bgo func|sync\.WaitGroup|subprocess\.run",
    ),
    "time": (
        r"time|clock|schedule|expir",
        r"\b(datetime|timezone|tzinfo|utcnow|expire[sd]?|expiry|ttl|TTL|Date\.now|time\.time|time_ns|monotonic)\b",
    ),
    "api": (
        r"routes?/|api/|endpoints?/|controllers?/|handlers?/|openapi|graphql|proto/|\.proto$",
        r"(@(app|router)\.(get|post|put|patch|delete)|app\.(get|post|put|patch|delete)\(|status_code=|HTTPException|response_model)",
    ),
    "ui": (
        r"\.(tsx|jsx|vue|svelte|css|scss|html)$|components?/|pages?/|views?/",
        r"(className=|onClick|useState|<button|aria-)",
    ),
    "security": (
        r"ssrf|sanitiz|escape|crypto|secret|upload",
        r"\b(subprocess|exec|eval|shell=True|os\.system|pickle|yaml\.load|innerHTML|dangerouslySetInnerHTML|redirect|urlopen|requests\.|httpx\.|fetch\(|open\()",
    ),
    "config": (
        r"(^|/)(settings|config)|\.env|Dockerfile|compose|helm/|k8s/|\.ya?ml$|\.toml$|package\.json$|lock",
        r"\b(os\.environ|getenv|process\.env|feature[_ ]?flag)\b",
    ),
}
DEEP_AREAS = {"auth", "data", "concurrency", "security", "api"}
AREA_REFS = {
    "auth": "areas/auth.md",
    "data": "areas/data.md",
    "concurrency": "areas/concurrency-time.md",
    "time": "areas/concurrency-time.md",
    "api": "areas/api.md",
    "ui": "areas/ui.md",
    "security": "areas/security.md",
    "config": "areas/config-deps.md",
}


# ---------------------------------------------------------------- helpers


def out(obj) -> None:
    print(json.dumps(obj, separators=(",", ":"), default=str))


def sh(args, cwd=None, check=True, timeout=None, input_text=None) -> str:
    res = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input_text)
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args))}: {res.stderr.strip()[:500]}")
    return res.stdout.strip()


def ok(args, cwd=None) -> bool:
    return subprocess.run(args, cwd=cwd, capture_output=True).returncode == 0


def repo_root(cwd=None) -> Path:
    return Path(sh(["git", "rev-parse", "--show-toplevel"], cwd=cwd))


def head_sha(cwd=None) -> str:
    return sh(["git", "rev-parse", "HEAD"], cwd=cwd)


def run_dir(cwd=None, sha=None) -> Path:
    root = repo_root(cwd)
    return RUNS / f"{root.name}-{(sha or head_sha(cwd))[:12]}"


def load_run(cwd=None) -> dict:
    path = run_dir(cwd) / "run.json"
    if not path.exists():
        raise SystemExit("no workspace for HEAD: run `prv workspace` first")
    return json.loads(path.read_text())


def container_name(cwd=None) -> str:
    # shortcut: one sandbox per repo at a time (it shares the repo's Docker image cache volume); fine for one developer.
    return "prv-" + re.sub(r"[^a-z0-9-]", "-", repo_root(cwd).name.lower())


def stamp_dir(cwd=None) -> Path:
    common = Path(sh(["git", "rev-parse", "--git-common-dir"], cwd=cwd))
    if not common.is_absolute():
        common = Path(cwd or os.getcwd()) / common
    return common / "pr-verify"


# ---------------------------------------------------------------- base and triage


def _default_branch(remote: str, cwd) -> str | None:
    with contextlib.suppress(RuntimeError):
        return sh(["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], cwd=cwd)
    for name in ("main", "master"):
        if ok(["git", "rev-parse", "--verify", "-q", f"{remote}/{name}"], cwd=cwd):
            return f"{remote}/{name}"
    return None


def _gh(args, cwd) -> str | None:
    if not shutil.which("gh"):
        return None
    with contextlib.suppress(Exception):
        return sh(["gh", *args], cwd=cwd, timeout=20)
    return None


def resolve_base(explicit: str | None, cwd=None, offline=False) -> tuple[str, str]:
    """Return (base description, merge-base sha). Prefers the PR's real base, then upstream, then the fork parent."""
    head = head_sha(cwd)
    if explicit:
        return explicit, sh(["git", "merge-base", explicit, head], cwd=cwd)
    if not offline:
        pr = _gh(["pr", "view", "--json", "baseRefName,baseRefOid,url"], cwd)
        if pr:
            info = json.loads(pr)
            oid = info["baseRefOid"]
            if not ok(["git", "cat-file", "-e", f"{oid}^{{commit}}"], cwd=cwd):
                repo_url = info["url"].split("/pull/")[0]
                sh(["git", "fetch", "-q", repo_url, oid], cwd=cwd, timeout=300)
            return f"PR base {info['baseRefName']}", sh(["git", "merge-base", oid, head], cwd=cwd)
    if not offline and ok(["git", "remote", "get-url", "upstream"], cwd=cwd):
        sh(["git", "fetch", "-q", "upstream"], cwd=cwd, timeout=300)
        ref = _default_branch("upstream", cwd)
        if ref:
            return ref, sh(["git", "merge-base", ref, head], cwd=cwd)
    if not offline:
        # A fork without an upstream remote: compare against the parent repository's default branch.
        parent = _gh(["repo", "view", "--json", "parent", "-q", '.parent.owner.login + "/" + .parent.name'], cwd)
        if parent and "/" in parent and not parent.startswith("/"):
            branch = _gh(["repo", "view", parent, "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"], cwd)
            if branch:
                sh(["git", "fetch", "-q", f"https://github.com/{parent}", branch], cwd=cwd, timeout=300)
                return f"{parent}@{branch}", sh(["git", "merge-base", "FETCH_HEAD", head], cwd=cwd)
    for remote in ("upstream", "origin"):
        if ok(["git", "remote", "get-url", remote], cwd=cwd):
            ref = _default_branch(remote, cwd)
            if ref:
                return ref, sh(["git", "merge-base", ref, head], cwd=cwd)
    for ref in ("main", "master"):
        if ok(["git", "rev-parse", "--verify", "-q", ref], cwd=cwd):
            return ref, sh(["git", "merge-base", ref, head], cwd=cwd)
    raise SystemExit("cannot find a base branch: pass --base REF")


def classify(files: list[str], added: str) -> dict:
    if not files:
        return {"tier": "skip", "areas": {}, "reason": "no changes against base"}
    if all(DOC_FILE.search(f) for f in files):
        return {"tier": "skip", "areas": {}, "reason": "documentation-only change"}
    areas: dict[str, list[str]] = {}
    for area, (path_re, line_re) in AREAS.items():
        hits = [f for f in files if re.search(path_re, f, re.IGNORECASE)]
        if hits or re.search(line_re, added):
            areas[area] = hits[:5]
    tier = "deep" if DEEP_AREAS & areas.keys() else "standard"
    return {"tier": tier, "areas": areas, "reason": "touches " + ", ".join(sorted(areas)) if areas else "code change"}


def cmd_triage(a) -> None:
    base_desc, base = resolve_base(a.base, offline=a.offline)
    head = head_sha()
    files = [f for f in sh(["git", "diff", "--name-only", base, head]).splitlines() if f]
    diff = sh(["git", "diff", "-U0", base, head])
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    result = classify(files, added)
    symbols = sorted(
        {
            m.group(1).strip()[:80]
            for m in re.finditer(r"^@@[^@]*@@\s*(.+)$", diff, re.MULTILINE)
            if not re.match(r"\s*([+\-#*]|//|/\*|<!--|SPDX)", m.group(1))
        }
    )
    stat = sh(["git", "diff", "--shortstat", base, head])
    refs = sorted({AREA_REFS[a_] for a_ in result["areas"]})
    out(
        {
            "head": head,
            "base": base,
            "base_ref": base_desc,
            "tier": result["tier"],
            "reason": result["reason"],
            "areas": result["areas"],
            "files": files[:60],
            "files_total": len(files),
            "stat": stat,
            "changed_symbols": symbols[:40],
            "read": (["qa-method.md"] + refs) if result["tier"] != "skip" else [],
            "git_hook": git_hook_installed(),
        }
    )


# ---------------------------------------------------------------- workspace


def _clone(src: Path, dest: Path, sha: str) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    # --local hardlinks objects: fast, and refs/config/hooks stay independent of the user's repository.
    sh(["git", "clone", "-q", "--local", "--no-checkout", str(src), str(dest)])
    sh(["git", "checkout", "-q", "--detach", sha], cwd=dest)
    sh(["git", "remote", "remove", "origin"], cwd=dest)


def cmd_workspace(a) -> None:
    root = repo_root()
    base_desc, base = resolve_base(a.base)
    head = head_sha()
    rd = run_dir()
    (rd / "logs").mkdir(parents=True, exist_ok=True)
    _clone(root, rd / "pr", head)
    _clone(root, rd / "base", base)
    report = rd / "report.md"
    if not report.exists():
        report.write_text((SKILL_DIR / "references" / "report.md").read_text())
    meta = {"repo": str(root), "head": head, "base": base, "base_ref": base_desc, "run_dir": str(rd)}
    (rd / "run.json").write_text(json.dumps(meta, indent=2))
    dirty = sh(["git", "status", "--porcelain"], cwd=root)
    out(
        {
            **meta,
            "pr": str(rd / "pr"),
            "base_tree": str(rd / "base"),
            "report": str(report),
            "uncommitted_ignored": bool(dirty),
        }
    )


# ---------------------------------------------------------------- sandbox


def _docker(*args, check=True, timeout=None) -> str:
    return sh(["docker", *args], check=check, timeout=timeout)


def _runtime_flags() -> list[str]:
    runtimes = _docker("info", "--format", "{{json .Runtimes}}", check=False)
    if "sysbox-runc" in runtimes:
        return ["--runtime=sysbox-runc"]  # Docker-in-Docker without a privileged container
    return ["--privileged"]


def cmd_sandbox(a) -> None:
    if not shutil.which("docker") or not ok(["docker", "info"]):
        raise SystemExit("Docker is not running: start Docker Desktop, Colima or OrbStack, then retry")
    name = container_name()
    if a.action == "down":
        _docker("rm", "-f", "-v", name, check=False)
        out({"sandbox": name, "state": "removed"})
        return
    if a.action in ("offline", "online"):
        want = a.action == "online"
        attached = _docker("inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", name)
        if want != ("bridge" in attached.split()):
            _docker("network", "connect" if want else "disconnect", "bridge", name)
        # Verify instead of trusting the call: a silent half-connected sandbox wastes a whole run.
        for _ in range(10):
            resolves = ok(["docker", "exec", name, "getent", "hosts", "github.com"])
            if resolves == want:
                out({"sandbox": name, "network": "on" if want else "off"})
                return
            time.sleep(1)
        raise SystemExit(f"sandbox network did not turn {'on' if want else 'off'}; see `docker inspect {name}`")
    if a.action == "status":
        state = _docker("inspect", "-f", "{{.State.Status}}", name, check=False) or "absent"
        out({"sandbox": name, "state": state, "image": IMAGE})
        return
    meta = load_run()
    if not ok(["docker", "image", "inspect", IMAGE]):
        log = Path(meta["run_dir"]) / "logs" / "image-build.log"
        res = subprocess.run(
            ["docker", "build", "-t", IMAGE, str(SKILL_DIR / "sandbox")], capture_output=True, text=True
        )
        log.write_text(res.stdout + res.stderr)
        if res.returncode != 0:
            raise SystemExit(f"sandbox image build failed, see {log}")
    _docker("rm", "-f", "-v", name, check=False)
    repo = re.sub(r"[^a-z0-9-]", "-", Path(meta["repo"]).name.lower())
    args = [
        "run",
        "-d",
        "--name",
        name,
        "--hostname",
        "pr-verify",
        *_runtime_flags(),
        "-v",
        f"{meta['run_dir']}:/work",
        "-v",
        "prv-cache:/cache",  # package and toolchain caches, shared across runs
        "-v",
        f"prv-docker-{repo}:/var/lib/docker",  # inner image cache, per repository
    ]
    if os.environ.get("PR_VERIFY_MEMORY"):
        args += ["--memory", os.environ["PR_VERIFY_MEMORY"]]
    if os.environ.get("PR_VERIFY_CPUS"):
        args += ["--cpus", os.environ["PR_VERIFY_CPUS"]]
    _docker(*args, IMAGE)
    for _ in range(60):
        if ok(["docker", "exec", name, "docker", "info"]):
            out({"sandbox": name, "state": "ready", "image": IMAGE, "work": "/work/pr and /work/base"})
            return
        time.sleep(2)
    raise SystemExit(f"inner Docker did not start; see `docker logs {name}`")


def _tail(text: str, n: int) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return [line[:300] for line in lines[-n:]]


def run_in_sandbox(label: str, tree: str, command: str, timeout: int, tail: int) -> dict:
    meta = load_run()
    log = Path(meta["run_dir"]) / "logs" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', label)}.log"
    started = time.monotonic()
    argv = ["docker", "exec", "-w", f"/work/{tree}", container_name(), "bash", "-lc", command]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        code, text = res.returncode, res.stdout + res.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        text = (exc.stdout or b"").decode(errors="replace") + (exc.stderr or b"").decode(errors="replace")
        text += f"\n[pr-verify] timed out after {timeout}s"
    log.write_text(f"$ {command}\n{text}")
    return {
        "label": label,
        "in": tree,
        "exit": code,
        "secs": round(time.monotonic() - started, 1),
        "log": str(log),
        "tail": _tail(text, tail if code != 0 else 2),
    }


def cmd_run(a) -> None:
    if not a.after:
        raise SystemExit("usage: prv run LABEL [--in pr|base] -- COMMAND")
    out(run_in_sandbox(a.label, a.tree, " ".join(a.after), a.timeout, a.tail))


# ---------------------------------------------------------------- how the repository sets itself up


def _run_blocks(text: str) -> list[str]:
    """Extract `run:` commands from a CI YAML file without a YAML parser."""
    cmds, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        m = re.match(r"^(\s*)(-\s+)?run:\s*(.*)$", lines[i])
        if m:
            indent, rest = len(m.group(1)) + (len(m.group(2)) if m.group(2) else 0), m.group(3).strip()
            if rest in ("|", ">", "|-", ">-", "|+"):
                block, i = "", i + 1
                while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                    line = lines[i].strip()
                    if line:
                        if block.endswith("\\"):
                            block = block[:-1].rstrip() + " " + line  # shell line continuation
                        else:
                            block = f"{block} && {line}" if block else line
                    i += 1
                cmds.append(block)
                continue
            cmds.append(rest.strip("'\""))
        i += 1
    keep = [c for c in cmds if c and "<<" not in c and not re.match(r"(echo|printf)\b", c)]
    return [c[:160] for c in keep]


def cmd_ci_steps(a) -> None:
    root = Path(load_run()["run_dir"]) / "pr" if a.from_workspace else repo_root()
    found: dict[str, object] = {}
    dev = root / ".devcontainer" / "devcontainer.json"
    if dev.exists():
        found["devcontainer"] = {
            k: v
            for k, v in json.loads(re.sub(r"^\s*//.*$", "", dev.read_text(), flags=re.MULTILINE)).items()
            if k
            in ("image", "dockerFile", "build", "features", "postCreateCommand", "postStartCommand", "onCreateCommand")
        }
    for wf in sorted(glob.glob(str(root / ".github" / "workflows" / "*.y*ml")))[:20]:
        text = Path(wf).read_text(errors="replace")
        if not re.search(r"^\s*(pull_request|push)\s*:|^on:.*\b(pull_request|push)\b", text, re.MULTILINE):
            continue  # release, deploy, scheduled and badge workflows don't say how to build and test
        uses = sorted(set(re.findall(r"uses:\s*([\w./-]+)@", text)))
        versions = sorted(set(re.findall(r"(\w+-version):\s*['\"]?([\w.\-*x]+)", text)))
        runs = _run_blocks(text)
        if not runs:
            continue
        found[f"workflow:{Path(wf).name}"] = {
            "run": runs[:12],
            "uses": uses[:15],
            "versions": [f"{k}={v}" for k, v in versions][:10],
        }
    gitlab = root / ".gitlab-ci.yml"
    if gitlab.exists():
        found["gitlab-ci"] = re.findall(r"^\s*-\s+(.+)$", gitlab.read_text())[:30]
    for f in ("Makefile", "justfile", "Taskfile.yml"):
        p = root / f
        if p.exists():
            found[f] = re.findall(r"^([A-Za-z][\w-]*):", p.read_text(errors="replace"), re.MULTILINE)[:40]
    pkg = root / "package.json"
    if pkg.exists():
        with contextlib.suppress(ValueError):
            found["package.json scripts"] = json.loads(pkg.read_text()).get("scripts", {})
    for f in (
        ".tool-versions",
        ".mise.toml",
        ".nvmrc",
        ".node-version",
        ".python-version",
        "go.mod",
        "rust-toolchain.toml",
    ):
        if (root / f).exists():
            found.setdefault("toolchain_files", []).append(f)
    for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        for p in [root / f, *root.glob(f"*/{f}")]:
            if p.exists():
                found.setdefault("compose_files", []).append(str(p.relative_to(root)))
    out(found)


# ---------------------------------------------------------------- results


def _junit(pattern: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for path in glob.glob(pattern, recursive=True):
        with contextlib.suppress(ET.ParseError):
            for case in ET.parse(path).getroot().iter("testcase"):
                key = "::".join(x for x in (case.get("file") or case.get("classname"), case.get("name")) if x)
                state = "pass"
                for child in case:
                    if child.tag in ("failure", "error"):
                        state = "fail"
                    elif child.tag == "skipped" and state == "pass":
                        state = "skip"
                results[key] = state
    return results


def compare_results(base: dict[str, str], pr: dict[str, str]) -> dict:
    new = sorted(k for k, v in pr.items() if v == "fail" and base.get(k) != "fail")
    fixed = sorted(k for k, v in pr.items() if v == "pass" and base.get(k) == "fail")
    still = sorted(k for k, v in pr.items() if v == "fail" and base.get(k) == "fail")
    gone = sorted(k for k in base if k not in pr)
    return {
        "verdict": "fail" if new else "pass",
        "new_failures": new[:50],
        "new_failures_total": len(new),
        "fixed": fixed[:20],
        "preexisting_failures": still[:20],
        "preexisting_total": len(still),
        "tests_removed": gone[:20],
        "counts": {"base": len(base), "pr": len(pr), "pr_failed": sum(v == "fail" for v in pr.values())},
    }


def cmd_compare(a) -> None:
    base, pr = _junit(a.base), _junit(a.pr)
    if not pr:
        raise SystemExit(f"no JUnit results matched {a.pr}")
    out(compare_results(base, pr))


@contextlib.contextmanager
def _exclusive(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another mutate/swap is running: steps that modify files must run one at a time") from None
        yield


def cmd_mutate(a) -> None:
    meta = load_run()
    target = Path(meta["run_dir"]) / "pr" / a.file
    command = a.after
    if not command:
        raise SystemExit("usage: prv mutate --file F --old S --new S --label L -- TEST_COMMAND")
    with _exclusive(Path(meta["run_dir"]) / ".mutate.lock"):
        original = target.read_text()
        if original.count(a.old) != 1:
            raise SystemExit(f"--old must match exactly once in {a.file} (matches: {original.count(a.old)})")
        try:
            target.write_text(original.replace(a.old, a.new))
            result = run_in_sandbox(f"mutant-{a.label}", "pr", " ".join(command), a.timeout, 4)
        finally:
            target.write_text(original)
    log_text = Path(result["log"]).read_text(errors="replace")
    reason = next((ln[:240] for ln in log_text.splitlines() if re.match(r"\s*(FAILED|FAIL|--- FAIL|✗|×)", ln)), None)
    out(
        {
            "mutant": a.label,
            "file": a.file,
            "killed": result["exit"] != 0,
            "killed_by": reason,  # confirm a real assertion killed it, not an import or syntax error
            "exit": result["exit"],
            "log": result["log"],
        }
    )


# ---------------------------------------------------------------- brief and fresh eyes


def cmd_brief(a) -> None:
    meta = load_run()
    rd = Path(meta["run_dir"])
    pr = _gh(["pr", "view", "--json", "title,body"], None)
    if pr:
        info = json.loads(pr)
        intent = f"# {info['title']}\n\n{info.get('body') or ''}"
    else:
        intent = sh(["git", "log", "--format=## %s%n%n%b", f"{meta['base']}..{meta['head']}"], cwd=meta["repo"])
    diff = sh(["git", "diff", meta["base"], meta["head"]], cwd=meta["repo"])
    if len(diff) > DIFF_LIMIT:
        diff = diff[:DIFF_LIMIT] + f"\n\n[diff truncated at {DIFF_LIMIT} characters]"
    checklist = (SKILL_DIR / "references" / "qa-method.md").read_text()
    brief = (
        "## What the change claims to do\n\n"
        + intent.strip()
        + "\n\n## Risk checklist\n\n"
        + checklist
        + "\n\n## Diff\n\n```diff\n"
        + diff
        + "\n```\n"
    )
    path = rd / "brief.md"
    path.write_text(brief)
    out({"brief": str(path), "chars": len(brief), "diff_truncated": len(diff) >= DIFF_LIMIT})


def cmd_fresh_eyes(a) -> None:
    meta = load_run()
    rd = Path(meta["run_dir"])
    brief = (rd / "brief.md").read_text()
    prompt = (SKILL_DIR / "references" / "reviewer-prompt.md").read_text()
    dest = rd / "fresh-eyes.md"
    reviewer = a.reviewer or os.environ.get("PR_VERIFY_REVIEWER")
    delegate_error = None
    if reviewer and shutil.which("observal"):
        res = subprocess.run(
            ["observal", "delegate", "run", reviewer, brief, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=a.timeout,
        )
        if res.returncode != 0:
            delegate_error = f"observal: {(res.stderr or res.stdout).strip()[-300:]}"
        if res.returncode == 0:
            with contextlib.suppress(ValueError, KeyError):
                task = json.loads(res.stdout)
                texts = [
                    p.get("text", "")
                    for art in task.get("artifacts", [])
                    for p in art.get("parts", [])
                    if isinstance(p, dict)
                ]
                if task.get("status", {}).get("state") == "TASK_STATE_COMPLETED" and any(texts):
                    dest.write_text("\n".join(texts))
                    out({"via": "observal", "reviewer": reviewer, "findings": str(dest)})
                    return
    errors = [delegate_error] if delegate_error else []
    if shutil.which("pi"):
        # Same model as the calling pi session unless PR_VERIFY_MODEL says otherwise; pi's own default may differ.
        model = os.environ.get("PR_VERIFY_MODEL") or (
            f"{os.environ['PI_PROVIDER']}/{os.environ['PI_MODEL']}"
            if os.environ.get("PI_PROVIDER") and os.environ.get("PI_MODEL")
            else None
        )
        argv = ["pi", "-p", "--no-tools", "--no-skills", "--no-extensions", "--no-context-files", "--no-session"]
        argv += ["--model", model] if model else []
        res = subprocess.run(
            [*argv, f"{prompt}\n\n---\n\n{brief}"], capture_output=True, text=True, timeout=a.timeout, cwd=rd
        )
        if res.returncode == 0 and res.stdout.strip():
            dest.write_text(res.stdout)
            out({"via": "pi", "model": model, "findings": str(dest)})
            return
        errors.append(f"pi: {(res.stderr or res.stdout).strip()[-300:]}")
    note = "no independent reviewer available; do the fresh-eyes pass yourself from brief.md only"
    out({"via": None, "findings": None, "note": note, "errors": errors})


# ---------------------------------------------------------------- stamp and guard


def cmd_stamp(a) -> None:
    sha = a.sha or head_sha()
    d = stamp_dir()
    path = d / f"{sha}.json"
    if a.action == "write":
        d.mkdir(parents=True, exist_ok=True)
        record = {
            "sha": sha,
            "verdict": a.verdict,
            "tier": a.tier,
            "report": a.report,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(record, indent=2))
        out(record)
        return
    record = json.loads(path.read_text()) if path.exists() else None
    out({"sha": sha, "stamp": record})
    sys.exit(0 if record and record.get("verdict") == "pass" else 1)


def guard_decision(cwd: str, shas: list[str], env_skip: str | None) -> tuple[bool, str]:
    """Return (allowed, message) for pushing these commits."""
    if env_skip:
        d = stamp_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "overrides.log", "a") as fh:
            for s in shas:
                fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {s} {env_skip}\n")
        return True, f"override logged: {env_skip}"
    missing = []
    for s in shas:
        path = stamp_dir(cwd) / f"{s}.json"
        record = json.loads(path.read_text()) if path.exists() else None
        if record and record.get("verdict") == "pass":
            continue
        with contextlib.suppress(Exception):
            _, base = resolve_base(None, cwd=cwd, offline=True)
            files = [f for f in sh(["git", "diff", "--name-only", base, s], cwd=cwd).splitlines() if f]
            if classify(files, "")["tier"] == "skip":
                continue
        missing.append((s, record))
    if not missing:
        return True, "verified"
    s, record = missing[0]
    why = "failed verification" if record else "has not been verified"
    return False, (
        f"pr-verify: commit {s[:10]} {why}. Run the pr-verify skill before pushing. "
        'To push anyway: PR_VERIFY_SKIP="<reason>" git push (the override is logged). Do not use --no-verify.'
    )


def cmd_guard(a) -> None:
    """git pre-push hook: stdin has one `<local ref> <local sha> <remote ref> <remote sha>` line per ref."""
    shas = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) == 4 and set(parts[1]) != {"0"}:  # an all-zero local sha is a branch deletion
            shas.append(parts[1])
    if not shas:
        sys.exit(0)
    allowed, msg = guard_decision(os.getcwd(), shas, os.environ.get("PR_VERIFY_SKIP"))
    if not allowed:
        print(msg, file=sys.stderr)
    sys.exit(0 if allowed else 1)


def git_hook_installed(cwd=None) -> bool:
    hook = Path(sh(["git", "rev-parse", "--git-path", "hooks"], cwd=cwd))
    if not hook.is_absolute():
        hook = Path(cwd or os.getcwd()) / hook
    hook = hook / "pre-push"
    return hook.exists() and str(Path(__file__).resolve()) in hook.read_text(errors="replace")


def cmd_install_git_hook(a) -> None:
    hooks = Path(sh(["git", "rev-parse", "--git-path", "hooks"]))
    hook = hooks / "pre-push"
    chained = hooks / "pre-push.before-pr-verify"
    if hook.exists() and "pr-verify" not in hook.read_text(errors="replace"):
        hook.rename(chained)
    script = Path(__file__).resolve()
    guard = f"python3 {shlex.quote(str(script))} guard"
    previous = shlex.quote(str(chained))
    hook.write_text(
        "#!/bin/sh\n# pr-verify guard (installed by `prv install-git-hook`)\n"
        "input=$(cat)\n"
        f'printf "%s\\n" "$input" | {guard} "$@" || exit 1\n'
        f'if [ -x {previous} ]; then printf "%s\\n" "$input" | {previous} "$@"; fi\n'
    )
    hook.chmod(0o755)
    out({"installed": str(hook), "chained_previous": chained.exists()})


# ---------------------------------------------------------------- entry


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="prv", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", help="tier, risk areas and references to read")
    t.add_argument("--base")
    t.add_argument("--offline", action="store_true", help="no gh or fetch; local refs only")
    t.set_defaults(fn=cmd_triage)

    w = sub.add_parser("workspace", help="clone PR and base into the run directory")
    w.add_argument("--base")
    w.set_defaults(fn=cmd_workspace)

    s = sub.add_parser("sandbox", help="up | down | offline | online | status")
    s.add_argument("action", choices=["up", "down", "offline", "online", "status"])
    s.set_defaults(fn=cmd_sandbox)

    r = sub.add_parser("run", help="run a command in the sandbox; prints a one-line summary")
    r.add_argument("label")
    r.add_argument("--in", dest="tree", choices=["pr", "base"], default="pr")
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--tail", type=int, default=12)
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("ci-steps", help="how the repository installs and tests itself")
    c.add_argument("--from-workspace", action="store_true")
    c.set_defaults(fn=cmd_ci_steps)

    cmp_ = sub.add_parser("compare", help="diff JUnit results: new failures vs pre-existing")
    cmp_.add_argument("--base", required=True, help="glob for base JUnit XML")
    cmp_.add_argument("--pr", required=True, help="glob for PR JUnit XML")
    cmp_.set_defaults(fn=cmd_compare)

    m = sub.add_parser("mutate", help="apply one mutation, run tests, restore")
    m.add_argument("--file", required=True)
    m.add_argument("--old", required=True)
    m.add_argument("--new", required=True)
    m.add_argument("--label", required=True)
    m.add_argument("--timeout", type=int, default=1200)
    m.set_defaults(fn=cmd_mutate)

    b = sub.add_parser("brief", help="write brief.md for the independent reviewer")
    b.set_defaults(fn=cmd_brief)

    f = sub.add_parser("fresh-eyes", help="independent review via Observal delegation or an isolated headless agent")
    f.add_argument("--reviewer", help="Observal agent identifier (or PR_VERIFY_REVIEWER)")
    f.add_argument("--timeout", type=int, default=900)
    f.set_defaults(fn=cmd_fresh_eyes)

    st = sub.add_parser("stamp", help="write | check the verification stamp for a commit")
    st.add_argument("action", choices=["write", "check"])
    st.add_argument("--sha")
    st.add_argument("--verdict", choices=["pass", "fail"])
    st.add_argument("--tier", default="standard")
    st.add_argument("--report")
    st.set_defaults(fn=cmd_stamp)

    g = sub.add_parser("guard", help="git pre-push hook entry: block pushes without a passing stamp")
    g.add_argument("git_args", nargs="*", help="remote name and URL, as git passes them")
    g.set_defaults(fn=cmd_guard)

    ih = sub.add_parser("install-git-hook", help="install a git pre-push hook in this repository")
    ih.set_defaults(fn=cmd_install_git_hook)

    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything after `--` is the command to run; split it off so options before it always parse.
    command = argv[argv.index("--") + 1 :] if "--" in argv else []
    argv = argv[: argv.index("--")] if "--" in argv else argv
    args = p.parse_args(argv)
    args.after = command
    if getattr(args, "action", None) == "write" and args.cmd == "stamp" and not args.verdict:
        p.error("stamp write needs --verdict")
    args.fn(args)


if __name__ == "__main__":
    main()
