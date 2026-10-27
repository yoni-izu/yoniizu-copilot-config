#!/usr/bin/env python3
"""Outgoing PR owner: SQLite state and token-free GitHub mirror refresh.

PRS_DB overrides ~/tasks/prs.db. Markdown is explicit import/export only;
GitHub remains authoritative for probed PR status.

    pm.py list                 # what is tracked right now
    pm.py refresh [-f ID]      # hit GitHub, update the Status column
    pm.py add-feature NAME [-P PRIO] [-s SUMMARY] [-j JIRA] [-d DESC] [-p REF ...]
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
import pr_store as db

# Legacy discovery is used only by the explicit migrate command.
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
NOTE_RE = re.compile(r"^\s*-\s*\*\*(\d{4}-\d{2}-\d{2})\s*(?:\(([^)]*)\))?[^*]*\*\*[:.]?\s*(.*)$")
# The block's opening note is written as a field: "- **Notes:** 2026-10-12 — text".
FIELD_NOTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*[\u2014\u2013-]+\s*(.*)$")
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15}-\d+)\b")
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[`*])")
ARCHIVED_RE = re.compile(r"COMPLETE|DROPPED|ABANDONED", re.I)
SUMMARY_MAX = 240
PRIORITIES = ("critical", "high", "medium", "low")
DEFAULT_PRIORITY = "medium"


def jira_keys(*texts: str) -> list[str]:
    """Distinct Jira keys in order of appearance. ``SW-0`` style placeholders
    (number 0) mean "no ticket yet" and are dropped."""
    out: list[str] = []
    for t in texts:
        for k in JIRA_KEY_RE.findall(t or ""):
            if int(k.rsplit("-", 1)[1]) and k not in out:
                out.append(k)
    return out


def derive_summary(description: str) -> str:
    """First sentence of the description, minus its leading ``[KEY]`` tags."""
    d = re.sub(r"^(\s*\[[^\]]*\]\s*)+", "", description or "").strip()
    if not d:
        return ""
    first = SENTENCE_END_RE.split(d, maxsplit=1)[0].strip()
    if len(first) > SUMMARY_MAX:
        first = first[:SUMMARY_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"
    return first


def parse(text: str) -> list[dict]:
    """Parse explicit markdown interchange, retaining complete field values.

    Unknown narrative is preserved in the canonical document and raw blocks
    exposed by records(), rather than being reconstructed from display fields.
    """
    feats: list[dict] = []
    cur: dict | None = None
    tail: dict | None = None    # last note, so wrapped lines join it
    for line in text.splitlines():
        m = FEAT_RE.match(line)
        if m:
            name = m.group(2)
            cur = {"id": int(m.group(1)), "name": name, "logged": None,
                   "summary": "", "jira": [], "description": "", "priority": "",
                   "pldm": "not-tested", "pldm_details": "",
                   "prs": [], "notes": [],
                   "complete": bool(ARCHIVED_RE.search(name))}
            feats.append(cur)
            tail = None
            continue
        if cur is None:
            continue
        if tail is not None and line[:1].isspace() and line.strip() \
                and not line.lstrip().startswith(("|", "-")):
            tail["text"] += " " + line.strip()
            continue
        tail = None
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
            tail = {"date": note.group(1), "label": (note.group(2) or "").strip(),
                    "text": note.group(3)}
            cur["notes"].append(tail)
            continue
        fld = FIELD_RE.match(line)
        if fld:
            key, val = fld.group(1).strip().lower(), fld.group(2).strip()
            if key == "logged":
                cur["logged"] = val
            elif key == "summary":
                cur["summary"] = val
            elif key == "jira":
                cur["jira"] = jira_keys(val)
            elif key == "priority":
                cur["priority"] = _norm_priority(val)
            elif key == "description":
                cur["description"] = val
            elif key == "notes" and val:
                fn = FIELD_NOTE_RE.match(val)
                tail = ({"date": fn.group(1), "label": "logged", "text": fn.group(2)} if fn
                        else {"date": cur["logged"] or "", "label": "logged", "text": val})
                cur["notes"].append(tail)
            elif key == "pldm test":
                cur["pldm"] = val.lower()
            elif key == "pldm details":
                cur["pldm_details"] = val
    for f in feats:
        # Keep full multiline fields/notes in the parser API as well as the
        # original document; display summaries alone are not migration data.
        block_lines = []
        active = False
        for line in text.splitlines():
            head = FEAT_RE.match(line)
            if head:
                active = int(head.group(1)) == f["id"] and head.group(2) == f["name"]
            if active:
                block_lines.append(line)
        raw_fields = {}
        for i, line in enumerate(block_lines):
            fld = FIELD_RE.match(line)
            if fld and not NOTE_RE.match(line):
                end = _value_end(block_lines, i)
                raw_fields[fld.group(1).lower()] = "\n".join(
                    [fld.group(2)] + block_lines[i + 1:end]).rstrip("\n")
        f["fields_raw"] = raw_fields
        for field in ("description", "summary", "pldm_details"):
            label = field.replace("_", " ")
            if label in raw_fields:
                f[field] = raw_fields[label]
        notes = []
        for i, line in enumerate(block_lines):
            note = NOTE_RE.match(line)
            fld = FIELD_RE.match(line)
            if note:
                end = _value_end(block_lines, i)
                notes.append({"date": note.group(1), "label": (note.group(2) or "").strip(),
                              "text": "\n".join([note.group(3)] + block_lines[i + 1:end]).rstrip("\n")})
            elif fld and fld.group(1).lower() == "notes" and fld.group(2):
                end = _value_end(block_lines, i)
                value = "\n".join([fld.group(2)] + block_lines[i + 1:end]).rstrip("\n")
                fn = FIELD_NOTE_RE.match(value)
                notes.append({"date": fn.group(1) if fn else f["logged"] or "",
                              "label": "logged",
                              "text": value[fn.start(2):] if fn else value})
        f["notes"] = notes
        f["priority_derived"] = not f["priority"]
        f["priority"] = f["priority"] or DEFAULT_PRIORITY
        f["summary_derived"] = not f["summary"]
        if not f["summary"]:
            f["summary"] = derive_summary(f["description"])
        f["jira_derived"] = not f["jira"]
        if not f["jira"]:
            f["jira"] = jira_keys(f["name"], f["description"])
        # Stable: same-day notes keep their memoir order.
        f["notes"].sort(key=lambda n: n["date"], reverse=True)
        f["last_note"] = f["notes"][0] if f["notes"] else None
    return _dedupe(feats)


def _value_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if FEAT_RE.match(line) or FIELD_RE.match(line) or line.lstrip().startswith("|"):
            break
        end += 1
    return end


def records() -> list[dict]:
    """Canonical records, including archived features and complete raw evidence."""
    text, revision, cached = db.snapshot()
    feats = parse(text)
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if FEAT_RE.match(line)]
    for f in feats:
        blocks = []
        for n, start in enumerate(starts):
            if int(FEAT_RE.match(lines[start]).group(1)) != f["id"]:
                continue
            blocks.append("".join(lines[start:starts[n + 1] if n + 1 < len(starts) else len(lines)]))
        f["raw_markdown"] = "".join(blocks)
        f["revision"] = revision
    observed = cached.get("_feature_history", []) + cached.get("features", [])
    previous = {f["id"]: f for f in observed if "id" in f}
    feats = [{**previous.get(f["id"], {}), **f} for f in feats]
    probed = {(pr["owner"], pr["repo"], pr["number"]): pr
              for f in observed for pr in f.get("prs", [])}
    for f in feats:
        for i, pr in enumerate(f["prs"]):
            old = probed.get((pr["owner"], pr["repo"], pr["number"]))
            if old:
                # The last GitHub observation, not a stale memoir status, wins.
                f["prs"][i] = {**pr, **old}
    return feats


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
                   "--json", "name,state,bucket,link"])
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
    # Run URLs (job suffix stripped) of the failing checks, for the portal's
    # "Why red" deep link into the pipeline explorer.
    runs = []
    for c in checks:
        if (c.get("bucket") or "").lower() != "fail":
            continue
        m = re.match(r"(https://github\.com/[^/]+/[^/]+/actions/runs/\d+)", c.get("link") or "")
        if m and m.group(1) not in runs:
            runs.append(m.group(1))
    out["failed_runs"] = runs[:4]

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


def refresh(only=None, workers: int = 8,
            do_purge: bool = True, progress=None) -> dict:
    """Re-probe every tracked PR and persist the new Status cells.

    ``only`` limits the scan to one feature id or an iterable of ids; the probe
    detail (checks, gate, merge state) of every other feature is carried over
    from the cache so a partial scan never blanks the rest of the portal.

    ``progress`` is an optional ``fn(done, total, feature, pr)`` called once per
    completed probe so a caller can stream the scan to a UI.
    """
    with db.edit():
        text, revision, _ = db.snapshot()
        feats = records()
    ids = None if only is None else ({only} if isinstance(only, int) else {int(i) for i in only})
    targets = [f for f in feats
               if not f["complete"] and (ids is None or f["id"] in ids)]
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
    # Network work happens outside the write lock. A simultaneous user edit
    # invalidates this snapshot instead of silently being overwritten.
    try:
        db.save(text, {**db.snapshot()[2], **payload, "_feature_history": feats},
                expected_revision=revision, action="refresh")
    except db.Conflict as exc:
        return {"error": str(exc), "features": [], "retry": True}
    return payload


def _carry_probe(feats: list[dict], skip: set[int]) -> None:
    """Merge cached probe detail into PRs of features not being re-probed."""
    cached = db.snapshot()[2]
    probed = {(pr["owner"], pr["repo"], pr["number"]): pr
              for f in cached.get("_feature_history", []) + cached.get("features", [])
              for pr in f.get("prs", [])}
    for f in feats:
        if f["id"] in skip:
            continue
        for i, pr in enumerate(f["prs"]):
            old = probed.get((pr["owner"], pr["repo"], pr["number"]))
            if old:
                f["prs"][i] = {**old, **{k: v for k, v in pr.items() if v}}


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


@db.atomic
def set_pldm(feat_id: int, state: str, details: str = "") -> tuple[bool, str]:
    """Atomically stamp a feature's local PLDM state and probe projection."""
    if state not in PLDM_STATES:
        return False, f"invalid PLDM state: {state}"
    lines = db.snapshot()[0].splitlines()

    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    start, end = span

    want = {"pldm test": f"- **PLDM Test:** {state}"}
    if details:
        want["pldm details"] = f"- **PLDM Details:** {details}"
    for key, replacement in want.items():
        start, end = _span(lines, feat_id)
        found = next((i for i in range(start + 1, end)
                      if (f := FIELD_RE.match(lines[i]))
                      and f.group(1).strip().lower() == key), None)
        if found is not None:
            lines[found:_value_end(lines, found)] = [replacement]
        else:
            anchor = next((i for i in range(start + 1, end)
                           if NOTE_RE.match(lines[i])
                           or lines[i].lstrip().startswith("|")
                           or (FIELD_RE.match(lines[i])
                               and FIELD_RE.match(lines[i]).group(1).lower() == "prs")),
                          end)
            lines.insert(anchor, replacement)

    db.save("\n".join(lines) + "\n")
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
    """Rebuild the SQLite portal projection without losing GitHub observations."""
    text, _, cached = db.snapshot()
    probed = {(pr["owner"], pr["repo"], pr["number"]): pr
              for f in cached.get("_feature_history", []) + cached.get("features", [])
              for pr in f.get("prs", [])}
    feats = parse(text)
    previous = {f["id"]: f
                for f in cached.get("_feature_history", []) + cached.get("features", [])
                if "id" in f}
    feats = [{**previous.get(f["id"], {}), **f} for f in feats]
    for f in feats:
        for i, pr in enumerate(f["prs"]):
            old = probed.get((pr["owner"], pr["repo"], pr["number"]))
            if old:
                # The memoir wins, but only where it actually says something.
                f["prs"][i] = {**pr, **old}
    out = _payload(feats)
    out["last_scan"] = cached.get("last_scan")
    db.save(text, {**cached, **out, "_feature_history": feats}, action="cache")


@db.atomic
def add_pr(feat_id: int, ref: str, branch: str = "", title: str = "",
           status: str = "pending", lookup: bool = True) -> tuple[bool, str]:
    """Track one PR on a canonical feature, opening its table if necessary.

    ``ref`` is a PR URL or ``owner/repo#123``. With ``lookup`` the branch, title
    and merged/closed state come from ``gh``, so the row lands carrying the same
    detail a scan would have written.
    """
    parsed = parse_pr_ref(ref)
    if not parsed:
        return False, f"could not read a PR reference from {ref!r}"
    owner, repo, number = parsed
    lines = db.snapshot()[0].splitlines()
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

    db.save("\n".join(lines) + "\n")
    _sync_cache()
    return True, f"[{feat_id}] + {repo}#{number}"


@db.atomic
def remove_pr(feat_id: int, number: int, repo: str = "") -> tuple[bool, str]:
    """Stop tracking one PR without disturbing other feature state."""
    lines = db.snapshot()[0].splitlines()
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
    db.save("\n".join(lines) + "\n")
    _sync_cache()
    return True, f"[{feat_id}] \u2212 {gone}#{number}"


@db.atomic
def add_feature(name: str, description: str = "", refs: list[str] | None = None,
                lookup: bool = True, summary: str = "",
                jira: str = "", priority: str = "") -> tuple[bool, str, int | None]:
    """Atomically allocate a feature ID, including archived IDs in the sequence."""
    name = _cell(name)
    if not name:
        return False, "a feature name is required", None
    text = db.snapshot()[0]
    ids = [int(m.group(1)) for m in map(FEAT_RE.match, text.splitlines()) if m]
    fid = max(ids, default=0) + 1
    block = [f"## [{fid}] {name}",
             f"- **Logged:** {datetime.now().strftime('%Y-%m-%d')}"]
    if _norm_priority(priority):
        block.append(f"- **Priority:** {_norm_priority(priority)}")
    if summary and _cell(summary):
        block.append(f"- **Summary:** {_cell(summary)}")
    if jira and _norm_jira(jira):
        block.append(f"- **Jira:** {_norm_jira(jira)}")
    if description:
        block.append(f"- **Description:** {description}")
    block += ["- **PRs:**"] + TABLE_HEAD
    db.save(text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n", action="new")

    added, bad = 0, []
    for ref in refs or []:
        ok, msg = add_pr(fid, ref, lookup=lookup)
        added += ok
        if not ok:
            bad.append(msg)
    _sync_cache()
    note = f"[{fid}] {name} \u2014 {added} PR(s)"
    return True, (note + " \u00b7 " + "; ".join(bad) if bad else note), fid


@db.atomic
def drop_feature(feat_id: int) -> tuple[bool, str]:
    """Retire a feature without deleting any history."""
    lines = db.snapshot()[0].splitlines()
    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    name = FEAT_RE.match(lines[span[0]]).group(2)
    if ARCHIVED_RE.search(name):
        return False, f"feature [{feat_id}] is already retired"
    lines[span[0]] = (f"## [{feat_id}] {name} \u2014 \U0001f6ab DROPPED "
                      f"({datetime.now().strftime('%Y-%m-%d')}, "
                      "dropped from active tracking)")
    db.save("\n".join(lines) + "\n", action="retire")
    _sync_cache()
    return True, f"[{feat_id}] {name} \u2014 dropped from tracking"


FIELD_ORDER = ["logged", "priority", "summary", "jira", "description"]


def _set_field(lines: list[str], start: int, end: int, label: str, value: str) -> None:
    """Upsert or (empty value) delete one ``- **Label:** value`` field in a block,
    keeping new fields in ``FIELD_ORDER`` position."""
    key = label.lower()
    rank = FIELD_ORDER.index(key)
    idx, insert_at = None, start + 1
    for i in range(start + 1, end):
        f = FIELD_RE.match(lines[i])
        if not f or NOTE_RE.match(lines[i]):
            continue
        k = f.group(1).strip().lower()
        if k == key:
            idx = i
            break
        if k in FIELD_ORDER and FIELD_ORDER.index(k) < rank:
            insert_at = _value_end(lines, i)
    row = f"- **{label}:** {value}"
    if value:
        if idx is not None:
            lines[idx:_value_end(lines, idx)] = [row]
        else:
            lines.insert(insert_at, row)
    elif idx is not None:
        del lines[idx:_value_end(lines, idx)]


def _norm_priority(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in PRIORITIES else ""


def _norm_jira(value: str) -> str:
    keys = jira_keys((value or "").upper())
    return ", ".join(keys)


@db.atomic
def edit_feature(feat_id: int, name: str | None = None,
                 description: str | None = None, summary: str | None = None,
                 jira: str | None = None,
                 priority: str | None = None,
                 expected_revision: int | None = None) -> tuple[bool, str]:
    """Edit selected local fields, optionally checking an optimistic revision.

    ``None`` leaves a field alone; an empty string removes it (summary and jira
    then fall back to being derived from the description).
    """
    text, revision, _ = db.snapshot()
    if expected_revision is not None and expected_revision != revision:
        return False, f"PR revision conflict: expected {expected_revision}, current {revision}"
    lines = text.splitlines()
    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    start = span[0]

    if name is not None:
        clean_name = _cell(name)
        if not clean_name:
            return False, "feature name cannot be empty"
        current = FEAT_RE.match(lines[start]).group(2)
        suffix = ""
        if " \u2014 " in current:
            _, tail = current.split(" \u2014 ", 1)
            if ARCHIVED_RE.search(tail):
                suffix = " \u2014 " + tail
        lines[start] = f"## [{feat_id}] {clean_name}{suffix}"

    if jira is not None and jira.strip() and not _norm_jira(jira):
        return False, f"no Jira key found in {jira!r} (expected e.g. IPPCIESW-684)"
    if priority is not None and priority.strip() and not _norm_priority(priority):
        return False, f"priority must be one of {', '.join(PRIORITIES)}"
    changed = []
    for label, val in (("Priority", priority), ("Summary", summary), ("Jira", jira), ("Description", description)):
        if val is None:
            continue
        clean = (_norm_jira(val) if label == "Jira"
                 else _norm_priority(val) if label == "Priority" else val)
        # The span shifts as fields are inserted/removed — re-resolve each time.
        s, e = _span(lines, feat_id)
        _set_field(lines, s, e, label, clean)
        changed.append(label.lower())
    if name is not None:
        changed.insert(0, "name")

    db.save("\n".join(lines) + "\n", expected_revision=revision)
    _sync_cache()
    return True, f"[{feat_id}] updated " + (", ".join(changed) or "nothing")


@db.atomic
def add_note(feat_id: int, text: str, label: str = "") -> tuple[bool, str]:
    """Append one dated activity note to a feature's history."""
    clean = text or ""
    if not clean.strip():
        return False, "note text is empty"
    lines = db.snapshot()[0].splitlines()
    span = _span(lines, feat_id)
    if span is None:
        return False, _no_span(lines, feat_id)
    start, end = span
    while end - 1 > start and not lines[end - 1].strip():
        end -= 1
    lbl = re.sub(r"[()*]", "", label or "").strip()
    head = datetime.now().strftime("%Y-%m-%d") + (f" ({lbl})" if lbl else "")
    lines.insert(end, f"- **{head}:** {clean}")
    db.save("\n".join(lines) + "\n", action="note")
    _sync_cache()
    return True, f"[{feat_id}] note added"


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
    """Mirror local PLDM state into the transactional SQLite projection."""
    text, _, data = db.snapshot()
    for f in data.get("features", []):
        if f.get("id") == feat_id:
            f["pldm"] = state
            f["pldm_stale"] = state in ("queued", "running")
            if details:
                f["pldm_details"] = details
    db.save(text, data, action="pldm")


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
    """Portal projection of canonical state with the latest GitHub observations."""
    with db.edit():
        _, _, cached = db.snapshot()
        base = _payload(records())
        return {**{k: v for k, v in cached.items() if not k.startswith("_")},
                **base, "last_scan": cached.get("last_scan")}


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
    r.add_argument("-f", "--feature", type=int, action="append",
                   help="only this feature id (repeatable)")
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
    af.add_argument("-s", "--summary", default="", help="one-line summary for the portal")
    af.add_argument("-j", "--jira", default="", help="Jira key(s), comma-separated")
    af.add_argument("-P", "--priority", default="", choices=("",) + PRIORITIES)
    af.add_argument("-p", "--pr", action="append", default=[],
                    help="PR URL or owner/repo#123 (repeatable)")
    ef = sub.add_parser("edit-feature", help="edit name/priority/summary/jira/description "
                        "(pass \"\" to clear a field)")
    ef.add_argument("feature", type=int)
    ef.add_argument("--name")
    ef.add_argument("-s", "--summary")
    ef.add_argument("-j", "--jira")
    ef.add_argument("-P", "--priority", choices=("",) + PRIORITIES)
    ef.add_argument("-d", "--description")
    ef.add_argument("--expected-revision", type=int)
    nt = sub.add_parser("note", help="append a dated activity note to a feature")
    nt.add_argument("feature", type=int)
    nt.add_argument("text")
    nt.add_argument("-l", "--label", default="", help="short tag, e.g. 'rebase + /ci'")
    df = sub.add_parser("drop-feature", help="retire a feature from tracking")
    df.add_argument("feature", type=int)
    sub.add_parser("payload", help="emit the portal JSON")
    sub.add_parser("paths", help="show canonical and legacy store paths")
    sh = sub.add_parser("show", help="show one feature, including archived history")
    sh.add_argument("feature", type=int)
    mg = sub.add_parser("migrate", help="one-time backed-up legacy import (local only)")
    mg.add_argument("--source", type=Path)
    mg.add_argument("--cache", type=Path, default=CACHE)
    ex = sub.add_parser("export", help="explicit markdown/cache interchange")
    ex.add_argument("destination", type=Path)
    ex.add_argument("--cache", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "paths":
        print(json.dumps({"database": str(db.path()), "legacy": str(store_path()),
                          "legacy_cache": str(CACHE)}))
    elif a.cmd == "migrate":
        source = a.source or store_path()
        if source is None:
            ap.error("legacy memoir not found; specify --source")
        print("imported" if db.migrate(source, a.cache) else "already imported")
    elif a.cmd == "export":
        db.export(a.destination, a.cache)
    elif a.cmd == "show":
        matches = [f for f in records() if f["id"] == a.feature]
        print(json.dumps(matches, indent=2, ensure_ascii=False))
        return 0 if matches else 1
    elif a.cmd == "list":
        _print(payload())
    elif a.cmd in ("add-pr", "rm-pr", "add-feature", "drop-feature", "edit-feature", "note"):
        if a.cmd == "add-pr":
            ok, msg = add_pr(a.feature, a.ref, lookup=not a.no_lookup)
        elif a.cmd == "rm-pr":
            ok, msg = remove_pr(a.feature, a.number, a.repo)
        elif a.cmd == "drop-feature":
            ok, msg = drop_feature(a.feature)
        elif a.cmd == "edit-feature":
            ok, msg = edit_feature(a.feature, name=a.name, description=a.description,
                                   summary=a.summary, jira=a.jira,
                                   priority=a.priority, expected_revision=a.expected_revision)
        elif a.cmd == "note":
            ok, msg = add_note(a.feature, a.text, a.label)
        else:
            ok, msg, _ = add_feature(a.name, a.description, a.pr,
                                     summary=a.summary, jira=a.jira,
                                     priority=a.priority)
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
        with db.edit():
            text = db.snapshot()[0]
            new, done = purge(text, records())
            for f in done:
                print(f"[pm] [{f['id']}] {f['name']} \u2014 {len(f['prs'])}/{len(f['prs'])} merged")
            if not done:
                print("[pm] nothing to retire")
            elif a.dry_run:
                print(f"[pm] dry run \u2014 {len(done)} feature(s) would be retired")
            else:
                db.save(new, action="purge")
                _sync_cache()
                print(f"[pm] retired {len(done)} feature(s)")
    else:
        print(json.dumps(payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
