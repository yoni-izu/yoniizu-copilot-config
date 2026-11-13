#!/usr/bin/env python3
"""Portable, guarded workspace operations. Also sent to mctl targets verbatim."""
from __future__ import annotations

import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
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


def repo_worker(payload):
    """Run once per repository through wip forall or for the manifest checkout."""
    data = json.loads(payload)
    path = str(Path.cwd().resolve())

    def git(*args, optional=False):
        result = subprocess.run(["git", *args], text=True, capture_output=True, timeout=180)
        if result.returncode and not optional:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return result.stdout.strip() if not result.returncode else None

    def git_bytes(*args):
        result = subprocess.run(["git", *args], capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(os.fsdecode(result.stderr or result.stdout).strip())
        return result.stdout

    def working_digest(status):
        digest = hashlib.sha256(status.encode())
        if not status:
            return digest.hexdigest()
        digest.update(git_bytes("diff", "--cached", "--raw", "-z", "--no-ext-diff", "--no-abbrev"))
        changed = set(git_bytes("diff", "--name-only", "-z", "--no-ext-diff").split(b"\0")) - {b""}
        untracked = set(git_bytes("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")) - {b""}
        for name in sorted(changed | untracked):
            digest.update(name + b"\0")
            filename = os.fsdecode(name)
            try:
                before = os.lstat(filename)
            except FileNotFoundError:
                if name in untracked:
                    raise RuntimeError("untracked file changed during inspection: " + filename)
                digest.update(b"missing")
                continue
            digest.update(str(before.st_mode).encode() + b"\0")
            file_hash = hashlib.sha256()
            if stat.S_ISLNK(before.st_mode):
                file_hash.update(os.fsencode(os.readlink(filename)))
                after = os.lstat(filename)
            elif stat.S_ISREG(before.st_mode):
                fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as file:
                    opened = os.fstat(file.fileno())
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise RuntimeError("file changed during inspection: " + filename)
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        file_hash.update(chunk)
                    after = os.fstat(file.fileno())
            else:
                raise RuntimeError("cannot safely stash a nested repository or special file: " + filename)
            final = os.lstat(filename)
            def identity(item):
                return (item.st_dev, item.st_ino, item.st_mode, item.st_size,
                        item.st_mtime_ns, item.st_ctime_ns)
            if identity(before) != identity(after) or identity(before) != identity(final):
                raise RuntimeError("file changed during inspection: " + filename)
            digest.update(file_hash.digest())
        return digest.hexdigest()

    stash = None
    try:
        spec = data["repos"].get(path)
        if spec is None:
            raise RuntimeError("repository not in the resolved manifest: " + path)
        if git("rev-parse", "--show-toplevel") != path:
            raise RuntimeError("missing repository checkout: " + path)
        rev = spec["revision"]
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD", optional=True)
        head = git("rev-parse", "HEAD")
        status = git("status", "--porcelain", "--untracked-files=all")
        dirty = bool(status)
        status_digest = working_digest(status)
        ref = git("rev-parse", "--verify", "refs/remotes/origin/" + rev + "^{commit}", optional=True)
        ref = ref or git("rev-parse", "--verify", rev + "^{commit}", optional=True)
        baseline = (branch == rev) if branch else bool(ref and head == ref)
        current = (git("rev-parse", "--verify", "refs/remotes/origin/" + branch + "^{commit}",
                       optional=True) if branch else ref)
        ahead = git("rev-list", current + "..HEAD", "--count") if current else None
        target = spec.get("target") or rev
        target_ref = git("rev-parse", "--verify", "refs/heads/" + target + "^{commit}", optional=True)
        target_remote = git("rev-parse", "--verify", "refs/remotes/origin/" + target + "^{commit}",
                            optional=True)
        target_ahead = (git("rev-list", target_remote + ".." + target_ref, "--count")
                        if target_ref and target_remote else None)
        safe = not dirty and ahead == "0"
        reasons = []
        if dirty:
            reasons.append("uncommitted or untracked changes")
        if ahead is None:
            reasons.append("cannot verify local commits against origin")
        elif ahead != "0":
            reasons.append(ahead + " local-only commit(s)")
        if not baseline:
            reasons.append("on " + (branch or head[:12]) + ", expected " + rev)
        action = data["action"]
        state = {"revision": rev, "branch": branch, "head": head, "status_digest": status_digest,
                 "current_ref": current, "target_ref": target_ref, "target_remote": target_remote}
        if action == "stash":
            if spec.get("expected") != state:
                raise RuntimeError("repository changed after approval; review it again")
            if dirty:
                previous = git("rev-parse", "--verify", "refs/stash", optional=True)
                git("stash", "push", "--include-untracked", "--message",
                    "team-portal return " + (branch or "detached") + " " + head[:12])
                saved = git("rev-parse", "--verify", "refs/stash", optional=True)
                if not saved or saved == previous:
                    raise RuntimeError("git did not create a stash for the uncommitted changes")
                stash = {"path": path, "branch": branch, "head": head, "oid": saved}
                if git("status", "--porcelain", "--untracked-files=all"):
                    raise RuntimeError("working tree remains dirty after stashing; check submodules")
        elif action != "status":
            if action == "force-update":
                if dirty:
                    raise RuntimeError("working tree changed after stashing")
                if ahead != "0" and (not branch or branch == target):
                    raise RuntimeError("local commits on the target or detached HEAD cannot be safely returned")
            elif not safe:
                raise RuntimeError("; ".join(reasons))
            git("fetch", "origin")
            remote = "refs/remotes/origin/" + target
            if git("rev-parse", "--verify", remote + "^{commit}", optional=True):
                local_target = git("rev-parse", "--verify", "refs/heads/" + target + "^{commit}",
                                   optional=True)
                if local_target and git("rev-list", remote + ".." + local_target, "--count") != "0":
                    raise RuntimeError("target branch " + target + " has local-only commits")
                if not local_target:
                    git("checkout", "--no-overwrite-ignore", "-b", target, "--track", "origin/" + target)
                else:
                    git("checkout", "--no-overwrite-ignore", target)
                git("merge", "--ff-only", "--no-overwrite-ignore", remote)
            else:
                git("fetch", "origin", target)
                git("checkout", "--no-overwrite-ignore", "--detach", "FETCH_HEAD")
            if git("status", "--porcelain", "--untracked-files=all"):
                raise RuntimeError("working tree changed during checkout; refusing to sync")
            branch = git("symbolic-ref", "--quiet", "--short", "HEAD", optional=True)
            head = git("rev-parse", "HEAD")
        result = {"path": path, **state, "revision": rev, "branch": branch, "head": head, "dirty": dirty,
                  "remote": git("remote", "get-url", "origin", optional=True),
                  "baseline": baseline, "safe": safe, "clean": safe and baseline, "reasons": reasons,
                  "ahead": ahead, "target_ahead": target_ahead}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {"path": path, "error": str(exc), "clean": False, "safe": False, "reasons": [str(exc)]}
    if stash:
        result["stash"] = stash
    print("WS_RESULT:" + json.dumps(result))


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


def repo_batch(root, repos, action="status", step=None, label=None, on_result=None):
    wip = shutil.which("wip") or str(Path.home() / "bin/wip")
    try:
        capabilities = run([wip, "capabilities"], root.parent, 30).splitlines()
    except RuntimeError as exc:
        raise ValueError(f"WIP lacks direct-argument support; update WIP on this machine: {exc}") from exc
    if "forall-argv-v1" not in capabilities:
        raise ValueError("WIP lacks direct-argument support; update WIP on this machine")
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
            output = run([wip, "forall", "ws", "--argv", sys.executable,
                          str(Path(__file__).resolve()), "--repo-worker", payload], root, 900,
                         repo_progress(root, len(repos), step, label, on_result)
                         if step or on_result else None)
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


def repo_progress(root, total, step, label, on_result=None):
    """Per-repository progress from wip forall's '== ws:path ==' headers and WS_RESULT lines."""
    done = [0]

    def on_line(line):
        if line.startswith("== ") and line.endswith(" =="):
            progress(step, label, done=done[0], total=total,
                     current=line[3:-3].split(":", 1)[-1])
        elif line.startswith("WS_RESULT:"):
            row = json.loads(line[len("WS_RESULT:"):])
            if on_result:
                on_result(row)
            done[0] += 1
            progress(step, label, done=done[0], total=total,
                     current=os.path.relpath(row.get("path", ""), root),
                     error=row.get("error"),
                     detail=("Saved stash " + row["stash"]["oid"] if row.get("stash") else ""))
    return on_line


def repo_single(path, spec, action="status"):
    payload = json.dumps({"repos": {str(path.resolve()): spec}, "action": action})
    output = run([sys.executable, str(Path(__file__).resolve()), "--repo-worker", payload], path)
    lines = [line for line in output.splitlines() if line.startswith("WS_RESULT:")]
    if len(lines) != 1:
        raise ValueError(f"invalid repository response: {path}")
    return json.loads(lines[0][len("WS_RESULT:"):])


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
            rows.append(repo_single(manifests, {"revision": "main"}))
    return {"exists": True, "checked": now(), "repos": rows,
            "clean": all(r["clean"] for r in rows),
            "safe": all(r["safe"] for r in rows),
            "reasons": [f"{r['path']}: {why}" for r in rows for why in r["reasons"]]}


RETURN_STATE_FIELDS = ("revision", "branch", "head", "status_digest",
                       "current_ref", "target_ref", "target_remote")


def return_state(row):
    return {key: row.get(key) for key in RETURN_STATE_FIELDS}


def return_token(wid, path, revision, reading, mirror):
    state = {"id": wid, "path": str(path), "revision": revision, "mirror": mirror,
             "repos": [{"path": row["path"], **return_state(row)}
                       for row in sorted(reading["repos"], key=lambda item: item["path"])]}
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def return_blockers(reading):
    blockers = []
    for row in reading["repos"]:
        prefix = row["path"] + ": "
        if row.get("error"):
            blockers.append(prefix + row["error"])
        elif row.get("ahead") != "0" and (not row.get("branch")
                                           or row["branch"] == row["revision"]):
            blockers.append(prefix + "local commits on the target branch or detached HEAD "
                            "cannot be safely returned")
        elif row.get("target_ref") and row.get("target_ahead") != "0":
            blockers.append(prefix + "target branch has local-only or unverifiable commits")
    return blockers


def stash_summary(stashes):
    return "; ".join(f"{s['path']}: stash {s['oid']} from {s['branch'] or 'detached HEAD'} "
                     f"at {s['head']}" for s in stashes)


def prepare_forced_return(path, revision, reading, stashes):
    blockers = return_blockers(reading)
    if blockers:
        raise ValueError("; ".join(blockers))
    all_roots = roots(path)
    manifests = {root: manifest_repos(root, revision) for root in all_roots}
    before = {row["path"]: row for row in reading["repos"]}
    expected_paths = set()
    for root, specs in manifests.items():
        expected_paths.update(specs)
        expected_paths.add(str((root / ".repo/manifests").resolve()))
        if any(repo not in before or before[repo].get("revision") != spec["revision"]
               for repo, spec in specs.items()):
            raise ValueError("manifest changed after approval; review the workspace again")
    if expected_paths != set(before):
        raise ValueError("repository coverage changed after approval; review the workspace again")

    def save_stash(row):
        if row.get("stash"):
            stashes.append(row["stash"])

    def collect(rows, streamed=False):
        if not streamed:
            for row in rows:
                save_stash(row)
        failures = [f"{row['path']}: {row['error']}" for row in rows if row.get("error")]
        if failures:
            raise RuntimeError("; ".join(failures))

    for root in all_roots:
        where = f" ({root.name})" if len(all_roots) > 1 else ""
        specs = {repo: dict(spec, expected=return_state(before[repo]))
                 for repo, spec in manifests[root].items()}
        collect(repo_batch(root, specs, "stash", "stash",
                           "Stashing uncommitted changes" + where, on_result=save_stash),
                streamed=True)
        manifest_path = (root / ".repo/manifests").resolve()
        if str(manifest_path) not in specs:
            progress("stash", "Stashing uncommitted manifest changes" + where)
            collect([repo_single(manifest_path, {
                "revision": "main", "expected": return_state(before[str(manifest_path)])
            }, "stash")])

    for root in all_roots:
        if manifest_repos(root, revision) != manifests[root]:
            raise ValueError("manifest changed after stashing; review its saved changes before retrying")
        manifest_path = (root / ".repo/manifests").resolve()
        if str(manifest_path) not in manifests[root] and not before[str(manifest_path)]["baseline"]:
            progress("return", "Returning the manifest repository to its revision")
            collect([repo_single(manifest_path, {"revision": "main"}, "force-update")])
            if manifest_repos(root, revision) != manifests[root]:
                raise ValueError("manifest changed after checkout; review the workspace before retrying")
    return manifests


def execute(request):
    wid = request["id"]
    if not re.fullmatch(r"WS-[LR]-[0-9a-f]{32}", wid):
        raise ValueError("invalid workspace ID")
    action = request["action"]
    if action not in {"status", "sync", "free", "mirror", "align"}:
        raise ValueError(f"unsupported workspace action: {action}")
    portal_approval = None
    for option, required_action in (("portal_free", "free"), ("portal_sync", "sync")):
        if option not in request:
            continue
        value = request[option]
        if action != required_action or not isinstance(value, dict) or set(value) != {"approved"}:
            raise ValueError(f"{option} requires a {required_action} action and an approved snapshot")
        approved = value["approved"]
        if approved is not None and (not isinstance(approved, str)
                                     or not re.fullmatch(r"[0-9a-f]{64}", approved)):
            raise ValueError("invalid workspace approval snapshot")
        portal_approval = value
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
        stashes = []
        approval = None
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
                    if action == "sync" and mirror and portal_approval is not None:
                        if portal_approval["approved"] is not None:
                            raise ValueError("Force sync is only available for free independent workspaces")
                        portal_approval = None
                    forced = False
                    if portal_approval is not None:
                        failures = [r for r in result["repos"] if r.get("error")]
                        if failures:
                            raise ValueError("; ".join(f"{r['path']}: {r['error']}" for r in failures))
                        token = return_token(wid, path, revision, result, mirror)
                        if approved and approved != token and result["clean"]:
                            raise ValueError("workspace changed after approval; retry the return")
                        if not result["clean"] and approved != token:
                            approval = {
                                "token": token,
                                "revision": revision,
                                "repos": [{key: r.get(key) for key in
                                           ("path", "revision", "branch", "head", "dirty", "reasons")}
                                          for r in result["repos"] if not r["clean"]],
                                "blockers": return_blockers(result),
                            }
                        elif not result["clean"]:
                            forced = True
                    if approval is None:
                        approved_manifests = None
                        if forced:
                            blockers = return_blockers(result)
                            if blockers:
                                raise ValueError("; ".join(blockers))
                            changing_checkout = True
                            approved_manifests = prepare_forced_return(path, revision, result, stashes)
                        elif not result["safe"]:
                            raise ValueError("; ".join(result["reasons"]))
                        if action == "sync" and not mirror and not result["clean"] and not forced:
                            raise ValueError("; ".join(result["reasons"]))
                        if action == "align" and not result["clean"]:
                            raise ValueError("; ".join(result["reasons"]))
                        matched = set()
                        changing_checkout = True
                        all_roots = roots(path)
                        for root in all_roots:
                            where = f" ({root.name})" if len(all_roots) > 1 else ""
                            repos = manifest_repos(root, revision)
                            if forced and repos != approved_manifests[root]:
                                raise ValueError("manifest changed after approval; review the workspace again")
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
                            baseline_rows = repo_batch(root, repos if forced else manifest_repos(root, revision),
                                                       "force-update" if forced else "update",
                                                       "return", "Returning repositories to manifest revision" + where)
                            failures = [f"{r['path']}: {r['error']}" for r in baseline_rows if r.get("error")]
                            if failures:
                                raise RuntimeError("; ".join(failures))
                            if action != "align":
                                octl = shutil.which("octl") or str(Path.home() / ".local/bin/octl")
                                label = "Running octl ws sync" + where
                                progress("sync", label)
                                run([octl, "ws", "sync", "--no-plugins"], root, 1200, octl_progress(label))
                                if forced and manifest_repos(root, revision) != approved_manifests[root]:
                                    raise ValueError("manifest changed during sync; review the workspace before retrying")
                            after_rows = repo_batch(root, repos, "update", "update",
                                                    "Checking out target branches" + where)
                            failures = [f"{r['path']}: {r['error']}" for r in after_rows if r.get("error")]
                            if failures:
                                raise RuntimeError("; ".join(failures))
                        if action == "align":
                            expected = {f"{p['owner']}/{p['repo']}".lower()
                                        for p in request.get("prs", [])}
                            if not expected or matched != expected:
                                raise ValueError("review repositories missing: " + ", ".join(expected - matched))
                        result = inspect(path, revision, "verify", "Verifying clean baseline")
                        if action == "sync" and portal_approval is not None and not result["clean"]:
                            raise RuntimeError("sync did not reach a clean manifest baseline")
                        if action == "free":
                            if not result["clean"]:
                                raise RuntimeError("return/sync did not reach a clean manifest baseline")
                            state["mirror"] = None
                            atomic(marker, state)
            if stashes:
                result["stashes"] = stashes
            result.update(id=wid, path=str(path), mirror=state.get("mirror"))
            response = {"ok": True, "status": result}
            if approval is not None:
                response["approval_required"] = approval
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as e:
            if changing_checkout and action in {"sync", "free"} and state.get("mirror"):
                state["mirror"].update(state="failed", updated=now(), error=str(e))
                atomic(marker, state)
            error = str(e)
            if stashes:
                error += "; Saved work: " + stash_summary(stashes)
            response = {"ok": False, "error": error, "mirror": state.get("mirror")}
        with history.open("a") as f:
            f.write(json.dumps({"at": now(), "workspace_id": wid, "action": action,
                                "outcome": ("approval-required" if approval is not None else
                                            "ok" if response["ok"] else "error"),
                                "detail": response.get("error", "") or stash_summary(stashes),
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
    if len(sys.argv) == 3 and sys.argv[1] == "--repo-worker":
        repo_worker(sys.argv[2])
    else:
        main()
