#!/usr/bin/env python3
"""pr-monitor store reader and token-free status refresher.

The pr-monitor skill keeps its state in a markdown memoir at
``/memories/pr-monitor-features.md``. This module parses that file, refreshes
each PR's status straight from the ``gh`` CLI (no model in the loop), writes the
Status column back, and exposes a JSON payload for the Team Portal.

    pm.py list                 # what is tracked right now
    pm.py refresh [-f ID]      # hit GitHub, update the Status column
    pm.py add-feature NAME [-d DESC] [-p REF ...]
    pm.py add-pr ID REF        # REF = PR URL or owner/repo#123
    pm.py rm-pr ID NUMBER      # stop tracking one PR
    pm.py drop-feature ID      # retire a feature from active tracking
    pm.py payload              # JSON for the portal
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# The memory tool owns this file; we only rewrite the Status cells in place.
STORE_CANDIDATES = [
    "~/.vscode-server/data/User/globalStorage/github.copilot-chat/memory-tool"
    "/memories/pr-monitor-features.md",
    "~/.copilot/memories/pr-monitor-features.md",
    "~/memories/pr-monitor-features.md",
]
CACHE = Path(os.path.expanduser("~/tasks/pr-monitor-cache.json"))

# Worst first — a feature inherits the worst status among its open PRs.
STATUS_RANK = ["ci-fail", "changes-requested", "needs-rebase", "closed",
               "pending", "approved", "merge-queue", "merged"]
OPEN_STATES = {"ci-fail", "changes-requested", "needs-rebase", "pending",
               "approved", "merge-queue"}
TERMINAL_PLDM = {"passed", "failed", "cancelled"}
PLDM_STATES = {"not-tested", "queued", "running"} | TERMINAL_PLDM


def store_path() -> Path | None:
    for c in STORE_CANDIDATES:
        p = Path(os.path.expanduser(c))
        if p.exists():
            return p
    return None


# --- parsing ----------------------------------------------------------------

FEAT_RE = re.compile(r"^##\s*\[(\d+)\]\s*(.+?)\s*$")
# The PR table sits under a "- **PRs:**" bullet, so rows carry a leading indent.
ROW_RE = re.compile(r"^(\s*)\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|"
                    r"\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")
FIELD_RE = re.compile(r"^\s*-\s*\*\*([^:*]+):\*\*\s*(.*)$")
NOTE_RE = re.compile(r"^\s*-\s*\*\*(\d{4}-\d{2}-\d{2})[^*]*\*\*[:.]?\s*(.*)$")
ARCHIVED_RE = re.compile(r"COMPLETE|DROPPED|ABANDONED", re.I)


def parse(text: str) -> list[dict]:
    """Extract features from the memoir. Unknown lines are ignored on purpose —
    the file is a human memoir first and a data store second."""
    feats: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = FEAT_RE.match(line)
        if m:
            name = m.group(2)
            cur = {"id": int(m.group(1)), "name": name, "logged": None,
                   "description": "", "pldm": "not-tested", "pldm_details": "",
                   "prs": [], "notes": [],
                   "complete": bool(ARCHIVED_RE.search(name))}
            feats.append(cur)
            continue
        if cur is None:
            continue
        row = ROW_RE.match(line)
        if row and row.group(2).lower() not in ("owner", "-------", "---"):
            owner, repo, num = row.group(2), row.group(3), int(row.group(4))
            cur["prs"].append({
                "owner": owner, "repo": repo,
                "number": num, "branch": row.group(5),
                "title": row.group(6), "status": row.group(7).lower(),
                # Derived, not probed: links must survive a cold/stale cache.
                "url": f"https://github.com/{owner}/{repo}/pull/{num}",
            })
            continue
        note = NOTE_RE.match(line)
        if note:
            cur["notes"].append({"date": note.group(1), "text": note.group(2)})
            continue
        fld = FIELD_RE.match(line)
        if fld:
            key, val = fld.group(1).strip().lower(), fld.group(2).strip()
            if key == "logged":
                cur["logged"] = val
            elif key == "description":
                cur["description"] = val
            elif key == "pldm test":
                cur["pldm"] = val.lower()
            elif key == "pldm details":
                cur["pldm_details"] = val
    for f in feats:
        f["notes"].sort(key=lambda n: n["date"], reverse=True)
        f["last_note"] = f["notes"][0] if f["notes"] else None
        f["notes"] = f["notes"][:6]
    return _dedupe(feats)


def _dedupe(feats: list[dict]) -> list[dict]:
    """Drop blocks that are the same feature written twice.

    A memoir edited by both this script and the memory tool can end up with the
    whole body appended a second time; without this every duplicated feature
    renders twice in the portal. Only exact ``(id, name)`` repeats are collapsed
    — two *different* features that collide on an id are both real, and
    ``_spans`` refuses to write to either until one is renumbered.
    """
    out, seen = [], {}
    for f in feats:
        key = (f["id"], f["name"])
        prev = seen.get(key)
        if prev is None:
            seen[key] = f
            out.append(f)
        elif len(f["prs"]) + len(f["notes"]) > len(prev["prs"]) + len(prev["notes"]):
            out[out.index(prev)] = f      # keep whichever copy carries more
            seen[key] = f
    return out


# --- live refresh via gh (no tokens, no model) -------------------------------

def _gh(args: list[str], timeout: int = 45) -> tuple[bool, str]:
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip()


def queue_states(prs: list[dict]) -> dict:
    """Merge-queue membership for many PRs in one GraphQL round trip. `gh pr view`
    has no isInMergeQueue field, and a call per PR would triple the scan."""
    if not prs:
        return {}
    parts = []
    for i, pr in enumerate(prs):
        parts.append(
            f'p{i}: repository(owner:"{pr["owner"]}", name:"{pr["repo"]}")'
            f'{{ pullRequest(number:{pr["number"]})'
            f'{{ isInMergeQueue autoMergeRequest{{ enabledAt }} }} }}')
    ok, raw = _gh(["api", "graphql", "-f", "query={" + " ".join(parts) + "}"],
                  timeout=60)
    if not ok:
        return {}
    try:
        data = json.loads(raw).get("data") or {}
    except json.JSONDecodeError:
        return {}
    out = {}
    for i, pr in enumerate(prs):
        node = (data.get(f"p{i}") or {}).get("pullRequest") or {}
        out[(pr["owner"], pr["repo"], pr["number"])] = {
            "in_queue": bool(node.get("isInMergeQueue")),
            "auto_merge": bool(node.get("autoMergeRequest")),
        }
    return out


def probe(pr: dict) -> dict:
    """Resolve one PR's true current state. Uses ``gh pr checks`` rather than
    statusCheckRollup because the rollup counts superseded runs from every /ci
    re-fire on the same SHA and reports phantom failures."""
    slug = f"{pr['owner']}/{pr['repo']}"
    out = dict(pr, checks_pass=0, checks_fail=0, checks_pending=0,
               review=None, merge_state=None, mergeable=None, draft=False,
               in_queue=pr.get("in_queue", False),
               auto_merge=pr.get("auto_merge", False),
               url=f"https://github.com/{slug}/pull/{pr['number']}", error=None)
    ok, raw = _gh(["pr", "view", str(pr["number"]), "--repo", slug, "--json",
                   "state,mergeable,mergeStateStatus,reviewDecision,isDraft,"
                   "updatedAt,title,url"])
    if not ok:
        out["error"] = raw.splitlines()[0][:160] if raw else "gh failed"
        return out
    try:
        v = json.loads(raw)
    except json.JSONDecodeError:
        out["error"] = "bad gh json"
        return out
    state = (v.get("state") or "").upper()
    out.update(review=v.get("reviewDecision") or None,
               merge_state=v.get("mergeStateStatus"), mergeable=v.get("mergeable"),
               draft=bool(v.get("isDraft")), updated=(v.get("updatedAt") or "")[:10],
               url=v.get("url") or out["url"], title=v.get("title") or pr["title"])
    if state == "MERGED":
        out["status"] = "merged"
        return out
    if state == "CLOSED":
        out["status"] = "closed"
        return out

    ok, raw = _gh(["pr", "checks", str(pr["number"]), "--repo", slug,
                   "--json", "name,state,bucket"])
    checks = []
    if ok and raw:
        try:
            checks = json.loads(raw)
        except json.JSONDecodeError:
            checks = []
    for c in checks:
        b = (c.get("bucket") or "").lower()
        if b == "fail":
            out["checks_fail"] += 1
        elif b == "pass":
            out["checks_pass"] += 1
        elif b in ("pending", "waiting"):
            out["checks_pending"] += 1
    out["failed_checks"] = [c.get("name") for c in checks
                            if (c.get("bucket") or "").lower() == "fail"][:4]

    # Already handed to the merge queue: it is landing on its own and nothing
    # else about it is actionable, so it outranks the review/CI cascade.
    if out["in_queue"]:
        out["status"] = "merge-queue"
    elif out["checks_fail"]:
        out["status"] = "ci-fail"
    elif out["review"] == "CHANGES_REQUESTED":
        out["status"] = "changes-requested"
    elif out["merge_state"] in ("BEHIND", "DIRTY"):
        out["status"] = "needs-rebase"
    elif out["review"] == "APPROVED":
        out["status"] = "approved"
    else:
        out["status"] = "pending"
    return out


def refresh(only: int | None = None, workers: int = 8,
            do_purge: bool = True, progress=None) -> dict:
    """Re-probe every tracked PR and persist the new Status cells.

    ``progress`` is an optional ``fn(done, total, feature, pr)`` called once per
    completed probe so a caller can stream the scan to a UI.
    """
    p = store_path()
    if p is None:
        return {"error": "pr-monitor store not found", "features": []}
    text = p.read_text(encoding="utf-8")
    feats = parse(text)
    targets = [f for f in feats
               if not f["complete"] and (only is None or f["id"] == only)]
    jobs = [(f, pr) for f in targets for pr in f["prs"]]
    if jobs:
        if progress:
            progress(0, len(jobs), None, None)
        queued = queue_states([pr for _, pr in jobs])
        for _, pr in jobs:
            q = queued.get((pr["owner"], pr["repo"], pr["number"]))
            if q:
                pr.update(q)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [(f, pr, ex.submit(probe, pr)) for f, pr in jobs]
            for i, (f, pr, fut) in enumerate(futures, 1):
                pr.update(fut.result())
                if progress:
                    progress(i, len(jobs), f, pr)

    text = _write_back(text, targets)
    done = []
    if do_purge:
        text, done = purge(text, targets)
    p.write_text(text, encoding="utf-8")
    payload = _payload(feats)
    payload["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload["scanned"] = len(jobs)
    # A PR gh could not reach keeps its previous status, so a silent failure
    # looks exactly like "nothing changed" — count them and say so.
    payload["failed"] = [{"repo": pr["repo"], "number": pr["number"],
                          "error": pr["error"]}
                         for _, pr in jobs if pr.get("error")]
    payload["purged"] = [{"id": f["id"], "name": f["name"], "total": len(f["prs"])}
                         for f in done]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def purge(text: str, feats: list[dict]) -> tuple[str, list[dict]]:
    """Retire features whose PRs are all merged by annotating their heading, the
    same shape a human writes by hand. Nothing is deleted — the memoir keeps the
    entry and ARCHIVED_RE drops it from the active payload."""
    done = [f for f in feats if not f["complete"] and f["prs"]
            and all(pr.get("status") == "merged" for pr in f["prs"])]
    if not done:
        return text, []
    by_id = {f["id"]: f for f in done}
    today = datetime.now().strftime("%Y-%m-%d")
    out = []
    for line in text.splitlines():
        m = FEAT_RE.match(line)
        if m and int(m.group(1)) in by_id:
            n = len(by_id[int(m.group(1))]["prs"])
            line = (f"## [{m.group(1)}] {m.group(2)} \u2014 \u2705 COMPLETE {n}/{n} "
                    f"MERGED ({today}, dropped from active tracking)")
        out.append(line)
    for f in done:
        f["complete"] = True
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), done


def _write_back(text: str, feats: list[dict]) -> str:
    """Rewrite only the Status cell of each tracked row, leaving the memoir alone."""
    fresh = {(pr["owner"], pr["repo"], pr["number"]): pr["status"]
             for f in feats for pr in f["prs"] if pr.get("status")}
    out = []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            key = (m.group(2), m.group(3), int(m.group(4)))
            if key in fresh and fresh[key] != m.group(7).lower():
                line = (f"{m.group(1)}| {m.group(2)} | {m.group(3)} | {m.group(4)} | "
                        f"{m.group(5)} | {m.group(6)} | {fresh[key]} |")
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _spans(lines: list[str], feat_id: int) -> list[tuple[int, int]]:
    """Every ``[start, end)`` block carrying ``feat_id`` — normally one."""
    bounds = [i for i, line in enumerate(lines) if FEAT_RE.match(line)]
    hits = []
    for n, i in enumerate(bounds):
        if int(FEAT_RE.match(lines[i]).group(1)) == feat_id:
            hits.append((i, bounds[n + 1] if n + 1 < len(bounds) else len(lines)))
    return hits


def _span(lines: list[str], feat_id: int) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of one feature block in the memoir.

    ``None`` when the id is missing *or* ambiguous: editing the first of two
    blocks that share an id silently corrupts the wrong feature, so callers
    report the collision instead.
    """
    hits = _spans(lines, feat_id)
    return hits[0] if len(hits) == 1 else None


def _no_span(lines: list[str], feat_id: int) -> str:
    n = len(_spans(lines, feat_id))
    if n > 1:
        return (f"feature [{feat_id}] appears {n} times \u2014 renumber one "
                "before editing it")
    return f"feature [{feat_id}] not found"


def set_pldm(feat_id: int, state: str, details: str = "") -> tuple[bool, str]:
    """Stamp a feature's PLDM Test/Details fields in the memoir.

    Rewrites the two field lines in place, or inserts them after the last
    ``- **…:**`` field of the block if the feature never carried them.
    """
    p = store_path()
    if not p:
        return False, "pr-monitor store not found"
    if state not in PLDM_STATES:
        return False, f"invalid PLDM state: {state}"
    lines = p.read_text(encoding="utf-8").splitlines()

    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    start, end = span

    want = {"pldm test": f"- **PLDM Test:** {state}"}
    if details:
        want["pldm details"] = f"- **PLDM Details:** {details}"
    last_field = start
    for i in range(start + 1, end):
        f = FIELD_RE.match(lines[i])
        if not f:
            continue
        last_field = i
        key = f.group(1).strip().lower()
        if key in want:
            lines[i] = want.pop(key)
    # Leftovers never existed on this feature — add them after its last field.
    for i, line in enumerate(want.values()):
        lines.insert(last_field + 1 + i, line)

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _patch_cache_pldm(feat_id, state, details)
    return True, f"[{feat_id}] PLDM Test: {state}"


# --- store mutations (track / untrack PRs and features) ----------------------

TABLE_HEAD = ["  | Owner | Repo | PR# | Branch | Title | Status |",
              "  |-------|------|-----|--------|-------|--------|"]
PR_REF_RE = re.compile(r"(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)"
                       r"(?:/pull/|/|#)(\d+)")


def parse_pr_ref(ref: str) -> tuple[str, str, int] | None:
    """Accept a PR URL, ``owner/repo#123`` or ``owner/repo/123``."""
    m = PR_REF_RE.search((ref or "").strip())
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def _cell(s: str) -> str:
    """Rows are pipe-delimited, so a literal pipe would split the row in two."""
    return re.sub(r"\s+", " ", (s or "").replace("|", "/")).strip()


def _pr_row(owner: str, repo: str, number: int, branch: str, title: str,
            status: str) -> str:
    return (f"  | {_cell(owner)} | {_cell(repo)} | {number} | {_cell(branch)} | "
            f"{_cell(title)} | {_cell(status) or 'pending'} |")


def _sync_cache() -> None:
    """Rebuild the cache from the just-edited memoir, keeping probe detail.

    Writing the memoir makes it newer than the cache, which would make
    ``payload()`` fall back to a bare parse and drop every probed field until
    the next scan. Re-derive the features from the memoir and carry over the
    checks/gate data of every PR that is still tracked.
    """
    p = store_path()
    if p is None or not CACHE.exists():
        return
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        CACHE.unlink(missing_ok=True)
        return
    probed = {(pr["owner"], pr["repo"], pr["number"]): pr
              for f in cached.get("features", []) for pr in f.get("prs", [])}
    feats = parse(p.read_text(encoding="utf-8"))
    for f in feats:
        for i, pr in enumerate(f["prs"]):
            old = probed.get((pr["owner"], pr["repo"], pr["number"]))
            if old:
                # The memoir wins, but only where it actually says something.
                f["prs"][i] = {**old, **{k: v for k, v in pr.items() if v}}
    out = _payload(feats)
    out["last_scan"] = cached.get("last_scan")
    CACHE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def add_pr(feat_id: int, ref: str, branch: str = "", title: str = "",
           status: str = "pending", lookup: bool = True) -> tuple[bool, str]:
    """Add one PR row to a feature's table, opening the table if it has none.

    ``ref`` is a PR URL or ``owner/repo#123``. With ``lookup`` the branch, title
    and merged/closed state come from ``gh``, so the row lands carrying the same
    detail a scan would have written.
    """
    p = store_path()
    if p is None:
        return False, "pr-monitor store not found"
    parsed = parse_pr_ref(ref)
    if not parsed:
        return False, f"could not read a PR reference from {ref!r}"
    owner, repo, number = parsed
    lines = p.read_text(encoding="utf-8").splitlines()
    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    start, end = span

    rows = [i for i in range(start, end) if ROW_RE.match(lines[i])]
    for i in rows:
        m = ROW_RE.match(lines[i])
        if (m.group(2), m.group(3), int(m.group(4))) == (owner, repo, number):
            return False, f"{repo}#{number} is already on feature [{feat_id}]"

    if lookup:
        ok, raw = _gh(["pr", "view", str(number), "--repo", f"{owner}/{repo}",
                       "--json", "headRefName,title,state"])
        if not ok:
            return False, f"gh could not see {owner}/{repo}#{number}"
        try:
            v = json.loads(raw)
        except json.JSONDecodeError:
            return False, f"bad gh json for {owner}/{repo}#{number}"
        branch = branch or v.get("headRefName") or ""
        title = title or v.get("title") or ""
        if (v.get("state") or "").upper() in ("MERGED", "CLOSED"):
            status = v["state"].lower()

    row = _pr_row(owner, repo, number, branch, title, status)
    if rows:
        lines.insert(rows[-1] + 1, row)
    else:
        # Empty or absent table: find what is already there and add only the
        # missing scaffolding. NOTE_RE is checked first because a dated note
        # also matches FIELD_RE, and the table belongs with the fields.
        anchor, has_bullet, has_table = start, False, False
        for i in range(start + 1, end):
            if NOTE_RE.match(lines[i]):
                continue
            fld = FIELD_RE.match(lines[i])
            if lines[i].strip().startswith("|"):
                anchor, has_table = i, True
            elif fld:
                anchor = i
                has_bullet |= fld.group(1).strip().lower() == "prs"
        block = ([] if has_bullet else ["- **PRs:**"]) \
            + ([] if has_table else TABLE_HEAD) + [row]
        lines[anchor + 1:anchor + 1] = block

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sync_cache()
    return True, f"[{feat_id}] + {repo}#{number}"


def remove_pr(feat_id: int, number: int, repo: str = "") -> tuple[bool, str]:
    """Drop one PR row from a feature. Nothing else in the memoir moves."""
    p = store_path()
    if p is None:
        return False, "pr-monitor store not found"
    lines = p.read_text(encoding="utf-8").splitlines()
    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    hits = []
    for i in range(*span):
        m = ROW_RE.match(lines[i])
        if m and int(m.group(4)) == number and (not repo or m.group(3) == repo):
            hits.append(i)
    if not hits:
        return False, f"PR #{number} is not on feature [{feat_id}]"
    if len(hits) > 1:
        return False, f"#{number} is ambiguous on [{feat_id}] \u2014 name the repo"
    gone = ROW_RE.match(lines[hits[0]]).group(3)
    del lines[hits[0]]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sync_cache()
    return True, f"[{feat_id}] \u2212 {gone}#{number}"


def add_feature(name: str, description: str = "", refs: list[str] | None = None,
                lookup: bool = True) -> tuple[bool, str, int | None]:
    """Open a new feature block at the end of the memoir and fill its table."""
    p = store_path()
    if p is None:
        return False, "pr-monitor store not found", None
    name = _cell(name)
    if not name:
        return False, "a feature name is required", None
    text = p.read_text(encoding="utf-8")
    ids = [int(m.group(1)) for m in map(FEAT_RE.match, text.splitlines()) if m]
    fid = max(ids, default=0) + 1
    block = [f"## [{fid}] {name}",
             f"- **Logged:** {datetime.now().strftime('%Y-%m-%d')}"]
    if description:
        block.append(f"- **Description:** {_cell(description)}")
    block += ["- **PRs:**"] + TABLE_HEAD
    p.write_text(text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n",
                 encoding="utf-8")

    added, bad = 0, []
    for ref in refs or []:
        ok, msg = add_pr(fid, ref, lookup=lookup)
        added += ok
        if not ok:
            bad.append(msg)
    _sync_cache()
    note = f"[{fid}] {name} \u2014 {added} PR(s)"
    return True, (note + " \u00b7 " + "; ".join(bad) if bad else note), fid


def drop_feature(feat_id: int) -> tuple[bool, str]:
    """Retire a feature by annotating its heading, the same shape ``purge``
    writes. Nothing is deleted — ARCHIVED_RE just stops it reaching the payload."""
    p = store_path()
    if p is None:
        return False, "pr-monitor store not found"
    lines = p.read_text(encoding="utf-8").splitlines()
    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    name = FEAT_RE.match(lines[span[0]]).group(2)
    if ARCHIVED_RE.search(name):
        return False, f"feature [{feat_id}] is already retired"
    lines[span[0]] = (f"## [{feat_id}] {name} \u2014 \U0001f6ab DROPPED "
                      f"({datetime.now().strftime('%Y-%m-%d')}, "
                      "dropped from active tracking)")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sync_cache()
    return True, f"[{feat_id}] {name} \u2014 dropped from tracking"



def post_ci(owner: str, repo: str, number: int) -> tuple[bool, str]:
    """Post a ``/ci`` comment on one PR to (re-)trigger its CI pipeline.

    CI here only fires on a ``/ci`` comment — never on push or reopen — so this
    is the single supported way to (re-)run it.
    """
    return _gh(["pr", "comment", str(number), "--repo", f"{owner}/{repo}",
                "--body", "/ci"])


def trigger_ci(feat_id: int, scope: str = "all",
               number: int | None = None) -> dict:
    """(Re-)run CI on a feature's PRs by posting ``/ci`` comments.

    scope:
      ``all``    — every open PR (skips merged/closed);
      ``failed`` — only PRs whose status is ``ci-fail``;
      ``one``    — just the PR matching ``number``.

    Returns a per-PR result list plus posted/failed tallies, or ``{"error": …}``.
    """
    pl = payload()
    if pl.get("error"):
        return {"error": pl["error"]}
    feat = next((f for f in pl.get("features", []) if f["id"] == feat_id), None)
    if feat is None:
        return {"error": f"feature [{feat_id}] not found"}
    prs = feat["prs"]
    if scope == "one":
        prs = [p for p in prs if p["number"] == number]
        if not prs:
            return {"error": f"PR #{number} not in feature [{feat_id}]"}
    elif scope == "failed":
        prs = [p for p in prs if p.get("status") == "ci-fail"]
    else:
        prs = [p for p in prs if p.get("status") not in ("merged", "closed")]

    results, posted, failed = [], 0, 0
    for p in prs:
        ok, out = post_ci(p["owner"], p["repo"], p["number"])
        posted += ok
        failed += not ok
        results.append({"repo": p["repo"], "number": p["number"], "ok": ok,
                        "detail": (out.splitlines()[-1][:160] if out else "")})
    return {"feature_id": feat_id, "scope": scope, "posted": posted,
            "failed": failed, "results": results}


def _patch_cache_pldm(feat_id: int, state: str, details: str) -> None:
    """Mirror a PLDM stamp into the cache and re-stamp its mtime.

    Writing the memoir makes it newer than the cache, which would make
    ``payload()`` fall back to a bare parse and drop every probed field until
    the next scan. Patching keeps the check counts and gates alive.
    """
    if not CACHE.exists():
        return
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        CACHE.unlink(missing_ok=True)
        return
    for f in data.get("features", []):
        if f.get("id") == feat_id:
            f["pldm"] = state
            f["pldm_stale"] = state in ("queued", "running")
            if details:
                f["pldm_details"] = details
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# --- payload ----------------------------------------------------------------

def _rank(s: str) -> int:
    try:
        return STATUS_RANK.index(s)
    except ValueError:
        return len(STATUS_RANK)


def _payload(feats: list[dict]) -> dict:
    active = []
    for f in feats:
        if f["complete"]:
            continue
        prs = f["prs"]
        merged = [p for p in prs if p.get("status") == "merged"]
        openish = [p for p in prs if p.get("status") in OPEN_STATES]
        worst = min((p.get("status", "pending") for p in openish),
                    key=_rank, default="merged" if prs and not openish else "pending")
        f = dict(f)
        f.update(merged=len(merged), total=len(prs), open=len(openish), worst=worst,
                 blocked=[p for p in prs
                          if p.get("status") in ("ci-fail", "changes-requested",
                                                 "needs-rebase")],
                 pldm_stale=f["pldm"] in ("queued", "running"))
        active.append(f)
    active.sort(key=lambda f: (_rank(f["worst"]), -f["id"]))
    return {"features": active,
            "counts": _counts(active),
            "last_scan": None}


def _counts(active: list[dict]) -> dict:
    c: dict[str, int] = {}
    for f in active:
        for p in f["prs"]:
            s = p.get("status") or "pending"
            c[s] = c.get(s, 0) + 1
    return c


def payload() -> dict:
    """Portal payload. Prefers the cache (it carries the live probe detail) and
    falls back to a plain parse of the memoir when no scan has run yet."""
    p = store_path()
    if p is None:
        return {"features": [], "counts": {}, "last_scan": None,
                "error": "pr-monitor store not found"}
    base = _payload(parse(p.read_text(encoding="utf-8")))
    if CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return base
        if p.stat().st_mtime <= CACHE.stat().st_mtime + 2:
            return cached
        # The memoir was edited behind our back (the memory tool also writes
        # it). Fold the hand-edit in rather than serving a cache that can never
        # catch up, and keep the probe detail the bare parse does not have.
        _sync_cache()
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            base["last_scan"] = cached.get("last_scan")
    return base


# --- CLI --------------------------------------------------------------------

ICON = {"merged": "\u2705", "approved": "\U0001f7e2", "pending": "\u26aa",
        "needs-rebase": "\U0001f7e1", "changes-requested": "\U0001f7e0",
        "ci-fail": "\U0001f534", "closed": "\u26ab", "merge-queue": "\U0001f680"}


def _print(pl: dict) -> None:
    if pl.get("error"):
        print(f"[pm] {pl['error']}")
        return
    print(f"[pm] {len(pl['features'])} active feature(s)"
          + (f" \u00b7 last scan {pl['last_scan']}" if pl.get("last_scan") else ""))
    for f in pl["features"]:
        print(f"\n  [{f['id']}] {f['name']}  ({f['merged']}/{f['total']} merged"
              f" \u00b7 {ICON.get(f['worst'], '')} {f['worst']})")
        for p in f["prs"]:
            s = p.get("status", "?")
            extra = ""
            if p.get("checks_fail"):
                extra = f"  {p['checks_fail']}F/{p.get('checks_pass', 0)}P"
            elif p.get("merge_state") in ("BEHIND", "DIRTY"):
                extra = f"  {p['merge_state'].lower()}"
            print(f"      {ICON.get(s, ' ')} {s:<18} {p['repo']}#{p['number']}{extra}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pm", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show tracked features from the store")
    r = sub.add_parser("refresh", help="re-probe every PR via gh and save")
    r.add_argument("-f", "--feature", type=int, help="only this feature id")
    r.add_argument("--no-purge", action="store_true",
                   help="keep fully-merged features in active tracking")
    g = sub.add_parser("purge", help="retire features whose PRs are all merged")
    g.add_argument("-n", "--dry-run", action="store_true")
    ap_ = sub.add_parser("add-pr", help="track one more PR on a feature")
    ap_.add_argument("feature", type=int)
    ap_.add_argument("ref", help="PR URL or owner/repo#123")
    ap_.add_argument("--no-lookup", action="store_true",
                     help="don't ask gh for the branch and title")
    rp = sub.add_parser("rm-pr", help="stop tracking one PR of a feature")
    rp.add_argument("feature", type=int)
    rp.add_argument("number", type=int)
    rp.add_argument("--repo", default="", help="disambiguate a repeated PR number")
    af = sub.add_parser("add-feature", help="open a new tracked feature")
    af.add_argument("name")
    af.add_argument("-d", "--description", default="")
    af.add_argument("-p", "--pr", action="append", default=[],
                    help="PR URL or owner/repo#123 (repeatable)")
    df = sub.add_parser("drop-feature", help="retire a feature from tracking")
    df.add_argument("feature", type=int)
    sub.add_parser("payload", help="emit the portal JSON")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        _print(payload())
    elif a.cmd in ("add-pr", "rm-pr", "add-feature", "drop-feature"):
        if a.cmd == "add-pr":
            ok, msg = add_pr(a.feature, a.ref, lookup=not a.no_lookup)
        elif a.cmd == "rm-pr":
            ok, msg = remove_pr(a.feature, a.number, a.repo)
        elif a.cmd == "drop-feature":
            ok, msg = drop_feature(a.feature)
        else:
            ok, msg, _ = add_feature(a.name, a.description, a.pr)
        print(f"[pm] {msg}")
        return 0 if ok else 1
    elif a.cmd == "refresh":
        pl = refresh(a.feature, do_purge=not a.no_purge)
        _print(pl)
        if not pl.get("error"):
            print(f"\n[pm] probed {pl.get('scanned', 0)} PR(s) at {pl['last_scan']}")
            for f in pl.get("purged", []):
                print(f"[pm] all {f['total']} PRs merged for [{f['id']}] "
                      f"{f['name']} \u2014 dropped from active tracking")
    elif a.cmd == "purge":
        p = store_path()
        if p is None:
            print("[pm] pr-monitor store not found")
            return 1
        text = p.read_text(encoding="utf-8")
        new, done = purge(text, parse(text))
        for f in done:
            print(f"[pm] [{f['id']}] {f['name']} \u2014 {len(f['prs'])}/{len(f['prs'])} merged")
        if not done:
            print("[pm] nothing to retire")
        elif a.dry_run:
            print(f"[pm] dry run \u2014 {len(done)} feature(s) would be retired")
        else:
            p.write_text(new, encoding="utf-8")
            CACHE.unlink(missing_ok=True)
            print(f"[pm] retired {len(done)} feature(s)")
    else:
        print(json.dumps(payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
