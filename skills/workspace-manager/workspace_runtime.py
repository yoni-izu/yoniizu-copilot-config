#!/usr/bin/env python3
"""Portable, guarded workspace operations. Also sent to mctl targets verbatim."""
from __future__ import annotations

import base64
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from collections import deque


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, data):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@functools.lru_cache(maxsize=None)
def github_https_config():
    """Route GitHub SSH remotes through HTTPS with the gh token when gh is logged in.

    Services (the portal) do not inherit the interactive SSH agent and keys are
    passphrase-protected, so batch-mode SSH fails; the gh token does not need an agent.
    """
    gh = shutil.which("gh")
    if not gh:
        return {}
    try:
        if subprocess.run([gh, "auth", "token", "--hostname", "github.com"],
                          capture_output=True, timeout=15).returncode:
            return {}
    except (OSError, subprocess.SubprocessError):
        return {}
    pairs = [("url.https://github.com/.insteadOf", "git@github.com:"),
             ("url.https://github.com/.insteadOf", "ssh://git@github.com/"),
             ("credential.https://github.com.helper", ""),
             ("credential.https://github.com.helper", f"!{shlex.quote(gh)} auth git-credential")]
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def progress(step, label, **fields):
    """Emit a live step event; stdout stays reserved for the final JSON response."""
    try:
        sys.stderr.write("WS_PROGRESS:" + json.dumps(dict(step=step, label=label, **fields)) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def run(args, cwd, timeout=180, on_line=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10")
    env.update(github_https_config())
    if on_line is not None:
        return run_streaming(args, cwd, env, timeout, on_line)
    result = subprocess.run(args, cwd=cwd, env=env, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{shlex.join(args[:4])}: "
                           f"{(result.stderr or result.stdout).strip()[-3000:]}")
    return result.stdout


def run_streaming(args, cwd, env, timeout, on_line):
    """Like run(), but feeds each output line (stdout+stderr) to on_line as it arrives."""
    proc = subprocess.Popen(args, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    expired = []
    timer = threading.Timer(timeout, lambda: (expired.append(True), proc.kill()))
    timer.start()
    lines = []
    try:
        for line in proc.stdout:
            lines.append(line)
            try:
                on_line(line.rstrip("\n"))
            except (ValueError, KeyError, TypeError, OSError):
                pass  # progress reporting must never change the operation's outcome
        code = proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    output = "".join(lines)
    if expired:
        raise subprocess.TimeoutExpired(args, timeout, output)
    if code:
        raise RuntimeError(f"{shlex.join(args[:4])}: {output.strip()[-3000:]}")
    return output


def octl_progress(label):
    """Throttled live view of the latest octl output line."""
    last = [0.0]

    def on_line(line):
        text = ANSI.sub("", line).strip()
        if text and time.monotonic() - last[0] >= 0.5:
            last[0] = time.monotonic()
            progress("sync", label, detail=text[:200])
    return on_line


# Run once per repository through wip forall, never through a second repo loop.
REPO_WORKER = r'''
import json, os, pathlib, subprocess, sys
data=json.loads(sys.argv[1])
p=str(pathlib.Path.cwd().resolve())
def git(*args, optional=False):
    r=subprocess.run(["git",*args],text=True,capture_output=True,timeout=180)
    if r.returncode and not optional:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip() if not r.returncode else None
try:
    spec=data["repos"].get(p)
    if spec is None:
        raise RuntimeError("repository not in the resolved manifest: "+p)
    if git("rev-parse","--show-toplevel") != p:
        raise RuntimeError("missing repository checkout: "+p)
    rev=spec["revision"]
    branch=git("symbolic-ref","--quiet","--short","HEAD",optional=True)
    head=git("rev-parse","HEAD")
    dirty=bool(git("status","--porcelain","--untracked-files=normal"))
    ref=git("rev-parse","--verify","refs/remotes/origin/"+rev+"^{commit}",optional=True)
    ref=ref or git("rev-parse","--verify",rev+"^{commit}",optional=True)
    baseline=(branch==rev) if branch else bool(ref and head==ref)
    current=git("rev-parse","--verify","refs/remotes/origin/"+branch+"^{commit}",optional=True) if branch else ref
    ahead=git("rev-list",current+"..HEAD","--count") if current else None
    safe=not dirty and ahead=="0"
    reasons=[]
    if dirty: reasons.append("uncommitted or untracked changes")
    if ahead is None: reasons.append("cannot verify local commits against origin")
    elif ahead!="0": reasons.append(ahead+" local-only commit(s)")
    if not baseline: reasons.append("on "+(branch or head[:12])+", expected "+rev)
    action=data["action"]
    if action!="status":
        if not safe: raise RuntimeError("; ".join(reasons))
        target=spec.get("target") or rev
        git("fetch","origin")
        remote="refs/remotes/origin/"+target
        if git("rev-parse","--verify",remote+"^{commit}",optional=True):
            if git("show-ref","--verify","refs/heads/"+target,optional=True) is None:
                git("checkout","-b",target,"--track","origin/"+target)
            else: git("checkout",target)
            git("merge","--ff-only",remote)
        else:
            git("fetch","origin",target)
            git("checkout","--detach","FETCH_HEAD")
        branch=git("symbolic-ref","--quiet","--short","HEAD",optional=True)
        head=git("rev-parse","HEAD")
    result={"path":p,"revision":rev,"branch":branch,"head":head,"dirty":dirty,
            "remote":git("remote","get-url","origin",optional=True),
            "baseline":baseline,"safe":safe,"clean":safe and baseline,"reasons":reasons}
except Exception as e:
    result={"path":p,"error":str(e),"clean":False,"safe":False,"reasons":[str(e)]}
print("WS_RESULT:"+json.dumps(result))
'''


def roots(path):
    if (path / ".repo/metadata.json").is_file():
        return [path]
    found = sorted(p for p in path.iterdir()
                   if p.is_dir() and (p / ".repo/metadata.json").is_file())
    if not found:
        raise ValueError(f"no .repo/metadata.json checkout in {path}")
    return found


def manifest_repos(root, revision):
    meta = json.loads((root / ".repo/metadata.json").read_text())
    base = root / ".repo/manifests"
    filename = meta["manifest"]
    manifest = base / filename
    if not manifest.is_file():
        manifest = (base / "projects" / meta["project"] /
                    meta["manifest_revision"] / Path(filename).name)
    if not manifest.is_file():
        raise ValueError(f"manifest not found: {manifest}")
    out = {}
    active = set()

    def read(path, default=None):
        path = path.resolve()
        if not path.is_relative_to(base.resolve()) or path in active:
            raise ValueError(f"invalid manifest include: {path}")
        active.add(path)
        doc = ET.parse(path).getroot()
        entry = doc.find("default")
        default = entry.get("revision") if entry is not None else default
        if not default:
            raise ValueError(f"no default revision: {path}")
        if path == manifest.resolve() and default != revision:
            raise ValueError(f"configured revision {revision!r} differs from manifest "
                             f"default {default!r}; correct the workspace revision first")
        for node in doc:
            if node.tag == "include":
                read(path.parent / node.attrib["name"], default)
            elif node.tag == "project":
                rel = node.get("path") or node.attrib["name"]
                repo = (root / rel).resolve()
                if not repo.is_relative_to(root.resolve()):
                    raise ValueError(f"repository outside workspace: {rel}")
                out[str(repo)] = {"revision": node.get("revision") or default}
            elif node.tag in {"extend-project", "remove-project", "submanifest"}:
                raise ValueError(f"unsupported manifest element {node.tag}; refusing partial scan")
        active.remove(path)
    read(manifest)
    if not out:
        raise ValueError(f"manifest has no repositories: {manifest}")
    return out


def repo_batch(root, repos, action="status", step=None, label=None):
    wip = shutil.which("wip") or str(Path.home() / "bin/wip")
    # wip scan writes a shared state file in the slot; serialize sibling roots.
    lockdir = root.parent / ".wip"
    lockdir.mkdir(exist_ok=True)
    with (lockdir / "portal.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        statefile = lockdir / "state"
        if statefile.is_symlink():
            raise ValueError("refusing to overwrite a symlinked WIP state")
        previous = statefile.read_bytes() if statefile.exists() else None
        mode = statefile.stat().st_mode & 0o777 if previous is not None else 0o600
        scanned = None
        try:
            run([wip, "scan"], root.parent, 180)
            scanned = statefile.read_bytes()
            payload = json.dumps({"repos": repos, "action": action})
            code = "import base64;exec(base64.b64decode(" + repr(
                base64.b64encode(REPO_WORKER.encode()).decode()) + "))"
            command = shlex.join(["python3", "-c", code, payload])
            output = run([wip, "forall", "ws", command], root, 900,
                         repo_progress(root, len(repos), step, label) if step else None)
        finally:
            if scanned is not None and statefile.read_bytes() != scanned:
                raise ValueError("WIP state changed concurrently; newer state preserved")
            if previous is None:
                statefile.unlink(missing_ok=True)
            else:
                fd, temporary = tempfile.mkstemp(dir=lockdir, prefix="portal-state.")
                try:
                    with os.fdopen(fd, "wb") as backup:
                        os.fchmod(backup.fileno(), mode)
                        backup.write(previous)
                    os.replace(temporary, statefile)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
    results = [json.loads(line[len("WS_RESULT:"):]) for line in output.splitlines()
               if line.startswith("WS_RESULT:")]
    seen = {row["path"] for row in results}
    if seen != set(repos):
        raise ValueError(f"wip/manifest coverage mismatch; missing={sorted(set(repos)-seen)}, "
                         f"unexpected={sorted(seen-set(repos))}")
    return results


def repo_progress(root, total, step, label):
    """Per-repository progress from wip forall's '== ws:path ==' headers and WS_RESULT lines."""
    done = [0]

    def on_line(line):
        if line.startswith("== ") and line.endswith(" =="):
            progress(step, label, done=done[0], total=total,
                     current=line[3:-3].split(":", 1)[-1])
        elif line.startswith("WS_RESULT:"):
            row = json.loads(line[len("WS_RESULT:"):])
            done[0] += 1
            progress(step, label, done=done[0], total=total,
                     current=os.path.relpath(row.get("path", ""), root),
                     error=row.get("error"))
    return on_line


def inspect(path, revision, step=None, label=None):
    rows = []
    for root in roots(path):
        repos = manifest_repos(root, revision)
        ignored = {".repo", ".west", ".cache", ".venv", "node_modules", "build", "build_artifacts"}
        for directory, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name not in ignored]
            if ".git" in dirs or ".git" in files:
                found = str(Path(directory).resolve())
                if found not in repos:
                    raise ValueError(f"repository is not declared in the selected manifest: {found}")
                dirs[:] = []
        rows.extend(repo_batch(root, repos, "status", step, label))
        manifests = root / ".repo/manifests"
        if str(manifests.resolve()) not in repos:
            payload = json.dumps({"repos": {str(manifests.resolve()): {"revision": "main"}},
                                  "action": "status"})
            output = run(["python3", "-c", REPO_WORKER, payload], manifests)
            rows.append(json.loads(output.split("WS_RESULT:", 1)[1]))
    return {"exists": True, "checked": now(), "repos": rows,
            "clean": all(r["clean"] for r in rows),
            "safe": all(r["safe"] for r in rows),
            "reasons": [f"{r['path']}: {why}" for r in rows for why in r["reasons"]]}


def execute(request):
    wid = request["id"]
    if not re.fullmatch(r"WS-[LR]-[0-9a-f]{32}", wid):
        raise ValueError("invalid workspace ID")
    action = request["action"]
    if action not in {"status", "sync", "free", "mirror", "align"}:
        raise ValueError(f"unsupported workspace action: {action}")
    raw = request["path"]
    if not (raw.startswith("/") or raw.startswith("~/")):
        raise ValueError("workspace path must be absolute or home-relative")
    path = Path(raw).expanduser().resolve()
    revision = request.get("rev") or "main"
    directory = Path.home() / ".mctl/workspaces"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(path).encode()).hexdigest()
    marker = directory / (key + ".json")
    history = directory / (key + ".jsonl")
    with (directory / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(marker.read_text()) if marker.exists() else {
            "id": wid, "path": str(path), "mirror": None}
        if state["id"] != wid:
            return {"ok": False, "error": "workspace identity mismatch",
                    "workspace_id": state["id"], "path": str(path)}
        atomic(marker, state)
        changing_checkout = False
        try:
            if not path.is_dir():
                result = {"exists": False, "checked": now(), "clean": False,
                          "safe": False, "reasons": ["workspace directory missing"], "repos": []}
                if action != "status":
                    raise ValueError("workspace directory missing")
            elif action == "mirror":
                incoming = request.get("mirror")
                current = state.get("mirror")
                if current and (not incoming or current.get("source_id") != incoming.get("source_id")):
                    raise ValueError("workspace already mirrors another source; free it successfully first")
                if incoming and (not re.fullmatch(r"WS-[LR]-[0-9a-f]{32}", incoming.get("source_id", ""))
                                 or incoming.get("state") not in {"syncing", "active", "failed"}):
                    raise ValueError("mirror requires a valid source workspace ID and lifecycle state")
                state["mirror"] = incoming
                atomic(marker, state)
                result = {"exists": True, "checked": now()}
            else:
                result = inspect(path, revision, "inspect", "Checking repositories")
                mirror = state.get("mirror")
                if action in {"sync", "free", "align"}:
                    if action == "align" and mirror:
                        raise ValueError("cannot independently align a mirror")
                    if mirror and mirror.get("state") != "active" and action == "sync":
                        raise ValueError("mirror is not verified active; re-mirror or free it first")
                    if not result["safe"]:
                        raise ValueError("; ".join(result["reasons"]))
                    if action == "sync" and not mirror and not result["clean"]:
                        raise ValueError("; ".join(result["reasons"]))
                    if action == "align" and not result["clean"]:
                        raise ValueError("; ".join(result["reasons"]))
                    matched = set()
                    changing_checkout = True
                    all_roots = roots(path)
                    for root in all_roots:
                        where = f" ({root.name})" if len(all_roots) > 1 else ""
                        repos = manifest_repos(root, revision)
                        before = {r["path"]: r for r in result["repos"]}
                        if action == "sync" and mirror:
                            for repo, spec in repos.items():
                                branch = before[repo]["branch"]
                                if branch and branch not in (spec["revision"], "main"):
                                    spec["target"] = branch
                        if action == "align":
                            for repo, spec in repos.items():
                                url = before[repo].get("remote") or ""
                                keymatch = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
                                key = "/".join(keymatch.groups()).lower() if keymatch else ""
                                for pr in request.get("prs", []):
                                    prkey = f"{pr['owner']}/{pr['repo']}".lower()
                                    if prkey == key:
                                        branch = pr.get("branch") or ""
                                        if not branch or branch.startswith("-"):
                                            raise ValueError(f"invalid review branch: {branch!r}")
                                        spec["target"] = branch
                                        matched.add(prkey)
                        # Return first; octl must never rebase a development branch.
                        baseline_rows = repo_batch(root, manifest_repos(root, revision), "update",
                                                   "return", "Returning repositories to manifest revision" + where)
                        failures = [r["error"] for r in baseline_rows if r.get("error")]
                        if failures:
                            raise RuntimeError("; ".join(failures))
                        if action != "align":
                            octl = shutil.which("octl") or str(Path.home() / ".local/bin/octl")
                            label = "Running octl ws sync" + where
                            progress("sync", label)
                            run([octl, "ws", "sync", "--no-plugins"], root, 1200, octl_progress(label))
                        after_rows = repo_batch(root, repos, "update", "update",
                                                "Checking out target branches" + where)
                        failures = [r["error"] for r in after_rows if r.get("error")]
                        if failures:
                            raise RuntimeError("; ".join(failures))
                    if action == "align":
                        expected = {f"{p['owner']}/{p['repo']}".lower()
                                    for p in request.get("prs", [])}
                        if not expected or matched != expected:
                            raise ValueError("review repositories missing: " + ", ".join(expected - matched))
                    result = inspect(path, revision, "verify", "Verifying clean baseline")
                    if action == "free":
                        if not result["clean"]:
                            raise RuntimeError("return/sync did not reach a clean manifest baseline")
                        state["mirror"] = None
                        atomic(marker, state)
            result.update(id=wid, path=str(path), mirror=state.get("mirror"))
            response = {"ok": True, "status": result}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as e:
            if changing_checkout and action in {"sync", "free"} and state.get("mirror"):
                state["mirror"].update(state="failed", updated=now(), error=str(e))
                atomic(marker, state)
            response = {"ok": False, "error": str(e), "mirror": state.get("mirror")}
        with history.open("a") as f:
            f.write(json.dumps({"at": now(), "workspace_id": wid, "action": action,
                                "outcome": "ok" if response["ok"] else "error",
                                "detail": response.get("error", ""),
                                "mirror": state.get("mirror")}) + "\n")
        with history.open() as f:
            response["log"] = [json.loads(line) for line in deque(f, maxlen=200)]
        return response


def main():
    try:
        response = execute(json.loads(sys.argv[1]))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as e:
        response = {"ok": False, "error": str(e)}
    print(json.dumps(response))


if __name__ == "__main__":
    main()
