#!/usr/bin/env python3
"""bug-monitor CLI + library — mechanics for the bug-monitor skill.

Owns ``~/tasks/bugs.json``: the local mirror of every HSI bug in Jira plus the
team-lead's own monitoring layer on top of it (triage verdict, duplicate
families, watch state, progress signals, notes).

Everything here is *mechanical*: Jira REST reads, store mutations, signal
computation, duplicate clustering by lexical similarity. The judgment calls —
"is this really a duplicate", "what is the root cause" — belong to the model
driving the ``bug-monitor`` / ``bug-triage`` skills, which record their
conclusions back through ``triage`` / ``family`` subcommands.

Jira **writes** (comment, link) exist but are hard-gated behind ``--yes`` and
must never be invoked without the user's explicit approval.

Also importable as a module: ``tm.py`` uses it to render the Bugs section of
the team portal dashboard.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

STORE = Path(os.path.expanduser("~/tasks/bugs.json"))
SITE = "https://elementlabs.atlassian.net"
BROWSE = f"{SITE}/browse/"

HSI_TEAM_ID = "705f36d3-e7b3-4b7e-a220-d28f31f013ab"
HSI_COMPONENTS = ["ip-pcie-firmware-ep", "ip-se-firmware", "ip-d2d-firmware"]

# Jira custom fields (see jira-hsi-planning skill for the full map).
CF_TEAM = "customfield_10001"
CF_MILESTONE = "customfield_10702"
CF_SPRINT = "customfield_10020"

JIRA_FIELDS = [
    "summary", "status", "statuscategorychangedate", "assignee", "reporter",
    "priority", "components", "labels", "created", "updated", "duedate",
    "resolution", "resolutiondate", "issuetype", "project", "parent",
    "issuelinks", "description", "timeoriginalestimate",
    CF_TEAM, CF_MILESTONE, CF_SPRINT,
]

TRIAGE_STATES = ["untriaged", "in-triage", "triaged", "deferred"]

# CI evidence the regression auto-filer leaves in comments.
CI_RUN_RE = re.compile(
    r"https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)(?:/job/(\d+))?")
STATION_RE = re.compile(r"pldm-edk\d+-z\d+[a-z]")
TRIAGE_VERDICTS = [
    "new",            # genuine, distinct, needs a fix
    "reproduction",   # same family, new surface/station
    "duplicate",      # already tracked by another ticket
    "known-error",    # understood, fix pending / documented limitation
    "not-a-bug",      # env / test / config issue
    "not-ours",       # real bug, another team owns it
]
SEVERITIES = ["critical", "high", "medium", "low"]
WATCH_STATES = ["active", "muted", "resolved"]

# Progress signals, worst-first. Computed, never stored.
SIGNALS = ["dormant", "stalled", "slow", "moving", "closed"]

DEFAULT_CONFIG = {
    "team_id": HSI_TEAM_ID,
    "components": HSI_COMPONENTS,
    "extra_jql": "",          # appended with AND when set
    "new_days": 3,            # first_seen within N days => "new"
    "slow_days": 7,           # no activity for N days => "slow"
    "stall_days": 14,         # no activity for N days => "stalled"
    "dormant_days": 30,       # no activity for N days => "dormant"
    "aging_days": 30,         # open longer than N days => "aging"
    "ping_flag_at": 2,        # Oded pinged N+ times => "pinged" flag
    "ping_author": "Oded Gabbay",  # Jira comments by this person count as pings
    "closed_retention_days": 90,   # drop closed bugs untouched this long
}

TODAY = date.today().isoformat()
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------
# Store I/O
# ----------------------------------------------------------------------------

def _empty() -> dict:
    return {
        "version": 1,
        "last_scan": None,
        "config": dict(DEFAULT_CONFIG),
        "bugs": [],
        "families": [],
        "next_family_id": 1,
    }


def load() -> dict:
    if not STORE.exists():
        return _empty()
    try:
        with STORE.open() as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty()
    base = _empty()
    for k, v in base.items():
        d.setdefault(k, v)
    cfg = d["config"]
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    for b in d["bugs"]:
        b.setdefault("oded_pings", 0)
        b.setdefault("last_ping", None)
        b.setdefault("manual_pings", 0)
        b.setdefault("manual_last_ping", None)
        b.setdefault("ci_runs", [])
    return d


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    tmp.replace(STORE)


def find_bug(d: dict, key: str) -> dict | None:
    k = (key or "").strip().upper()
    for b in d["bugs"]:
        if b["key"].upper() == k:
            return b
    return None


def find_family(d: dict, ident: str) -> dict | None:
    i = (ident or "").strip()
    for fam in d["families"]:
        if fam["id"].lower() == i.lower():
            return fam
    low = i.lower()
    hits = [f for f in d["families"] if low and low in f["title"].lower()]
    return hits[0] if len(hits) == 1 else None


def append_note(obj: dict, msg: str) -> None:
    obj.setdefault("notes", []).append(f"{NOW}: {msg}")


# ----------------------------------------------------------------------------
# Jira REST
# ----------------------------------------------------------------------------

class JiraError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    """Resolve (email, api_token) from env, ~/.netrc, or ~/.atlassian-token."""
    email = os.environ.get("ATLASSIAN_EMAIL")
    token = os.environ.get("ATLASSIAN_TOKEN")
    if email and token:
        return email, token
    try:
        import netrc
        auth = netrc.netrc().authenticators(urllib.parse.urlparse(SITE).hostname)
        if auth and auth[0] and auth[2]:
            return auth[0], auth[2]
    except Exception:
        pass
    tf = Path(os.path.expanduser("~/.atlassian-token"))
    if tf.exists():
        tok = tf.read_text().strip().splitlines()[0].strip()
        ef = Path(os.path.expanduser("~/.atlassian-email"))
        mail = ef.read_text().strip() if ef.exists() else email
        if tok and mail:
            return mail, tok
    raise JiraError(
        "no Atlassian credentials — add a ~/.netrc entry for "
        f"{urllib.parse.urlparse(SITE).hostname} (login=email, password=API token) "
        "or export ATLASSIAN_EMAIL/ATLASSIAN_TOKEN")


def _request(method: str, path: str, body: dict | None = None,
             params: dict | None = None) -> Any:
    email, token = _credentials()
    url = SITE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    basic = base64.b64encode(f"{email}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise JiraError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise JiraError(f"{method} {path} -> {e.reason}") from None


def jira_search(jql: str, fields: list[str], page_size: int = 100) -> list[dict]:
    out: list[dict] = []
    token: str | None = None
    while True:
        body = {"jql": jql, "maxResults": page_size, "fields": fields}
        if token:
            body["nextPageToken"] = token
        page = _request("POST", "/rest/api/3/search/jql", body)
        out.extend(page.get("issues") or [])
        token = page.get("nextPageToken")
        if page.get("isLast") or not token:
            break
    return out


def jira_activity(key: str, ping_author: str = "") -> dict:
    """Fetch changelog + comments for one issue.

    Also derives the two things the portal can't get anywhere else: how many
    times ``ping_author`` has chased the bug, and any CI run links the
    auto-filer dropped in the comments.
    """
    issue = _request("GET", f"/rest/api/3/issue/{key}",
                     params={"expand": "changelog", "fields": "comment"})
    hist = (issue.get("changelog") or {}).get("histories") or []
    status_changes = []
    for h in hist:
        for item in h.get("items") or []:
            if item.get("field") == "status":
                status_changes.append({
                    "at": h.get("created", "")[:10],
                    "by": (h.get("author") or {}).get("displayName"),
                    "from": item.get("fromString"),
                    "to": item.get("toString"),
                })
    comments = ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []

    pings, last_ping = 0, None
    runs: dict[str, dict] = {}
    want = (ping_author or "").strip().lower()
    for c in comments:
        when = (c.get("created") or "")[:10]
        who = ((c.get("author") or {}).get("displayName") or "").strip().lower()
        if want and who == want:
            pings += 1
            last_ping = when
        # Dump the raw ADF rather than the rendered text: run links usually
        # live in a link mark's href, which never appears in the text nodes.
        _collect_ci_runs(json.dumps(c.get("body")), when, runs)

    return {
        "status_changes": status_changes[-8:],
        "last_status_change": status_changes[-1]["at"] if status_changes else None,
        "last_comment": comments[-1]["created"][:10] if comments else None,
        "last_comment_by": ((comments[-1].get("author") or {}).get("displayName")
                            if comments else None),
        "comment_count": len(comments),
        "change_count": len(hist),
        "pings": pings,
        "last_ping": last_ping,
        "ci_runs": sorted(runs.values(), key=lambda r: r["at"], reverse=True)[:8],
    }


def _collect_ci_runs(text: str, when: str, seen: dict) -> None:
    """Pull distinct GitHub Actions run links out of one comment."""
    station = STATION_RE.search(text)
    for repo, run, job in CI_RUN_RE.findall(text):
        if run in seen:
            continue
        seen[run] = {
            "run_id": run,
            "repo": repo,
            "url": f"https://github.com/{repo}/actions/runs/{run}"
                   + (f"/job/{job}" if job else ""),
            "at": when,
            "station": station.group(0) if station else None,
        }


def _apply_activity(b: dict, act: dict) -> None:
    """Fold a fetched activity blob into a stored bug record.

    Pings and CI links are recomputed from Jira on every scan; ``manual_pings``
    carries chases that happened outside Jira and is preserved.
    """
    b["activity"] = {k: v for k, v in act.items()
                     if k not in ("pings", "last_ping", "ci_runs")}
    jira_pings = act.get("pings") or 0
    legacy = b.get("oded_pings") or 0
    if "jira_pings" not in b and legacy and legacy != jira_pings:
        # Pre-auto counts were hand-tallied proxies for these same comments.
        # Leave an audit line so the number is never silently rewritten.
        b.setdefault("changes", []).append({
            "at": TODAY,
            "diff": [f"oded_pings {legacy} -> {jira_pings} (auto-derived from Jira)"],
        })
    b["jira_pings"] = jira_pings
    b.setdefault("manual_pings", 0)
    b["oded_pings"] = jira_pings + (b.get("manual_pings") or 0)
    stamps = [s for s in (act.get("last_ping"), b.get("manual_last_ping")) if s]
    b["last_ping"] = max(stamps) if stamps else None
    b["ci_runs"] = act.get("ci_runs") or []


# ----------------------------------------------------------------------------
# Jira field flattening
# ----------------------------------------------------------------------------

def _adf_text(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node.get("text") or "")
        for child in node.get("content") or []:
            _adf_text(child, out)
        if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock"):
            out.append("\n")
    elif isinstance(node, list):
        for child in node:
            _adf_text(child, out)


def adf_to_text(adf: Any, limit: int = 600) -> str:
    if not adf:
        return ""
    if isinstance(adf, str):
        return adf[:limit]
    parts: list[str] = []
    _adf_text(adf, parts)
    text = re.sub(r"\n{2,}", "\n", "".join(parts)).strip()
    return text[:limit]


def _sprint_names(val: Any) -> list[str]:
    if not val:
        return []
    if isinstance(val, list):
        names = []
        for s in val:
            if isinstance(s, dict) and s.get("name"):
                names.append(s["name"])
            elif isinstance(s, str):
                m = re.search(r"name=([^,\]]+)", s)
                names.append(m.group(1) if m else s)
        return names
    return [str(val)]


def flatten_issue(issue: dict) -> dict:
    f = issue.get("fields") or {}
    status = f.get("status") or {}
    ms = f.get(CF_MILESTONE)
    if isinstance(ms, list):
        ms = ms[0] if ms else None
    return {
        "key": issue["key"],
        "summary": (f.get("summary") or "").strip(),
        "description": adf_to_text(f.get("description")),
        "project": (f.get("project") or {}).get("key"),
        "status": status.get("name"),
        "status_category": ((status.get("statusCategory") or {}).get("key") or ""),
        "status_since": (f.get("statuscategorychangedate") or "")[:10] or None,
        "priority": (f.get("priority") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "assignee_id": (f.get("assignee") or {}).get("accountId"),
        "reporter": (f.get("reporter") or {}).get("displayName"),
        "components": [c["name"] for c in f.get("components") or []],
        "labels": f.get("labels") or [],
        "team": (f.get(CF_TEAM) or {}).get("name") if isinstance(f.get(CF_TEAM), dict) else None,
        "milestone": ms.get("value") if isinstance(ms, dict) else ms,
        "sprints": _sprint_names(f.get(CF_SPRINT)),
        "created": (f.get("created") or "")[:10] or None,
        "updated": (f.get("updated") or "")[:10] or None,
        "duedate": f.get("duedate"),
        "resolution": (f.get("resolution") or {}).get("name"),
        "resolved": (f.get("resolutiondate") or "")[:10] or None,
        "estimate_h": round((f.get("timeoriginalestimate") or 0) / 3600, 1) or None,
        "parent": (f.get("parent") or {}).get("key"),
        "links": [
            {"type": (l.get("type") or {}).get("outward" if l.get("outwardIssue") else "inward"),
             "key": ((l.get("outwardIssue") or l.get("inwardIssue")) or {}).get("key")}
            for l in f.get("issuelinks") or []
            if (l.get("outwardIssue") or l.get("inwardIssue"))
        ],
        "url": BROWSE + issue["key"],
    }


# ----------------------------------------------------------------------------
# Scope / scan
# ----------------------------------------------------------------------------

def scope_jql(cfg: dict, extra: str | None = None) -> str:
    clauses = []
    if cfg.get("team_id"):
        clauses.append(f'"Team[Team]" = {cfg["team_id"]}')
    comps = cfg.get("components") or []
    if comps:
        joined = ", ".join(f'"{c}"' for c in comps)
        clauses.append(f"component in ({joined})")
    if not clauses:
        raise JiraError("scope config has neither team_id nor components")
    jql = f"issuetype = Bug AND ({' OR '.join(clauses)})"
    tail = extra if extra is not None else cfg.get("extra_jql") or ""
    if tail.strip():
        jql += f" AND ({tail.strip()})"
    return jql + " ORDER BY created DESC"


# Fields whose change is worth recording in the bug's change log.
WATCHED_FIELDS = ["status", "priority", "assignee", "resolution", "milestone", "duedate"]


def _diff_fields(old: dict, new: dict) -> list[str]:
    out = []
    for k in WATCHED_FIELDS:
        a, b = old.get(k), new.get(k)
        if a != b:
            out.append(f"{k}: {a or '—'} → {b or '—'}")
    if sorted(old.get("sprints") or []) != sorted(new.get("sprints") or []):
        out.append(f"sprint: {', '.join(old.get('sprints') or []) or '—'} → "
                   f"{', '.join(new.get('sprints') or []) or '—'}")
    return out


def _new_bug_record(flat: dict, bootstrap: bool = False) -> dict:
    # On a bootstrap scan the whole backlog arrives at once; dating it "today"
    # would flag every historical bug as new. Use the Jira creation date instead.
    return {
        **flat,
        "first_seen": (flat.get("created") or TODAY) if bootstrap else TODAY,
        "watch": "active",
        # How many times Oded has chased this bug for progress/status, and when
        # he last did. Auto-derived from Jira comments on every scan.
        "oded_pings": 0,
        "last_ping": None,
        "jira_pings": 0,
        "manual_pings": 0,        # chases that happened outside Jira
        "manual_last_ping": None,
        "ci_runs": [],
        "triage": {
            "state": "untriaged",
            "verdict": None,
            "severity_call": None,
            "family": None,
            "owner": None,
            "root_cause": None,
            "investigation": None,
            "next_step": None,
            "triaged_on": None,
        },
        "activity": {},
        "changes": [],
        "notes": [],
    }


def do_scan(d: dict, activity: str = "auto", extra_jql: str | None = None,
            workers: int = 8) -> dict:
    """Refresh the local mirror from Jira. Returns a report dict."""
    cfg = d["config"]
    bootstrap = not d["bugs"]
    jql = scope_jql(cfg, extra_jql)
    issues = jira_search(jql, JIRA_FIELDS)
    seen_keys = set()
    report = {"jql": jql, "fetched": len(issues), "bootstrap": bootstrap,
              "new": [], "changed": [], "closed": [], "gone": [],
              "activity_fetched": 0}

    for issue in issues:
        flat = flatten_issue(issue)
        seen_keys.add(flat["key"])
        cur = find_bug(d, flat["key"])
        if cur is None:
            rec = _new_bug_record(flat, bootstrap)
            d["bugs"].append(rec)
            report["new"].append(flat["key"])
            continue
        diffs = _diff_fields(cur, flat)
        was_open = cur.get("status_category") != "done"
        cur.update(flat)
        if diffs:
            cur["changes"].append({"at": TODAY, "diff": diffs})
            cur["changes"] = cur["changes"][-40:]
            report["changed"].append({"key": flat["key"], "diff": diffs})
        if was_open and flat["status_category"] == "done":
            report["closed"].append(flat["key"])
            if cur["watch"] == "active":
                cur["watch"] = "resolved"

    for b in d["bugs"]:
        if b["key"] not in seen_keys and b.get("in_scope", True):
            b["in_scope"] = False
            report["gone"].append(b["key"])
        elif b["key"] in seen_keys:
            b["in_scope"] = True

    targets = [b for b in d["bugs"]
               if b.get("in_scope", True)
               and (activity == "all"
                    or (activity == "auto" and b.get("status_category") != "done"))]
    if activity != "none" and targets:
        author = cfg.get("ping_author") or ""

        def fetch(b):
            try:
                return b["key"], jira_activity(b["key"], author)
            except JiraError:
                return b["key"], None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, act in pool.map(fetch, targets):
                if act:
                    _apply_activity(find_bug(d, key), act)
                    report["activity_fetched"] += 1

    d["last_scan"] = NOW
    return report


# ----------------------------------------------------------------------------
# Derived signals
# ----------------------------------------------------------------------------

def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (date.today() - date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def last_activity(b: dict) -> str | None:
    """Most recent evidence of anyone touching this bug (Jira or us)."""
    cands = [b.get("updated")]
    act = b.get("activity") or {}
    cands += [act.get("last_status_change"), act.get("last_comment")]
    cands += [c.get("at") for c in b.get("changes") or []]
    for n in b.get("notes") or []:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", n)
        if m:
            cands.append(m.group(1))
    cands = [c for c in cands if c]
    return max(cands) if cands else None


def signal_of(b: dict, cfg: dict) -> str:
    if b.get("status_category") == "done":
        return "closed"
    idle = _days_since(last_activity(b))
    if idle is None:
        return "dormant"
    if idle >= cfg["dormant_days"]:
        return "dormant"
    if idle >= cfg["stall_days"]:
        return "stalled"
    if idle >= cfg["slow_days"]:
        return "slow"
    return "moving"


def flags_of(b: dict, cfg: dict) -> list[str]:
    """Team-lead attention flags. Only meaningful for open bugs."""
    out = []
    if b.get("status_category") == "done":
        return out
    tri = b.get("triage") or {}
    if tri.get("state") in (None, "untriaged"):
        out.append("untriaged")
    if not b.get("assignee"):
        out.append("unassigned")
    if not b.get("milestone"):
        out.append("no-milestone")
    if not b.get("sprints"):
        out.append("no-sprint")
    if not b.get("estimate_h"):
        out.append("no-estimate")
    age = _days_since(b.get("created"))
    if age is not None and age >= cfg["aging_days"]:
        out.append("aging")
    due = b.get("duedate")
    if due and (_days_since(due) or -1) > 0:
        out.append("overdue")
    if (b.get("activity") or {}).get("comment_count") == 0:
        out.append("no-discussion")
    if (b.get("oded_pings") or 0) >= cfg["ping_flag_at"]:
        out.append("pinged")
    return out


def annotate(b: dict, d: dict) -> dict:
    """Return a display copy of the bug with all derived fields resolved."""
    cfg = d["config"]
    fam_id = (b.get("triage") or {}).get("family")
    fam = next((f for f in d["families"] if f["id"] == fam_id), None)
    la = last_activity(b)
    c = dict(b)
    c["signal"] = signal_of(b, cfg)
    c["flags"] = flags_of(b, cfg)
    c["age_days"] = _days_since(b.get("created"))
    c["idle_days"] = _days_since(la)
    c["last_activity"] = la
    c["is_new"] = (_days_since(b.get("first_seen")) or 99) <= cfg["new_days"]
    c["family_title"] = fam["title"] if fam else None
    c["family_primary"] = fam.get("primary") if fam else None
    c["open"] = b.get("status_category") != "done"
    c["oded_pings"] = b.get("oded_pings") or 0
    c["days_since_ping"] = _days_since(b.get("last_ping"))
    return c


def view(d: dict) -> list[dict]:
    return [annotate(b, d) for b in d["bugs"]]


# ----------------------------------------------------------------------------
# Duplicate clustering
# ----------------------------------------------------------------------------

_NOISE = re.compile(
    r"\[[^\]]*\]"                       # [Regression], [C2C][FW] …
    r"|pldm-\S+|edk\d+\S*|z\d[a-z]?"    # station ids
    r"|\b\d+(\.\d+)*\b"                 # bare numbers / versions
    r"|0x[0-9a-f]+", re.I)
_STOP = {
    "the", "a", "an", "on", "in", "of", "to", "for", "and", "or", "is", "not",
    "with", "did", "does", "was", "were", "from", "at", "by", "after", "when",
    "test", "issue", "bug", "error", "failure", "fail", "failed",
}


def _tokens(text: str) -> set[str]:
    t = _NOISE.sub(" ", (text or "").lower())
    words = re.findall(r"[a-z_]{3,}", t)
    return {w for w in words if w not in _STOP}


def similarity(a: dict, b: dict) -> float:
    ta, tb = _tokens(a.get("summary", "")), _tokens(b.get("summary", ""))
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    comps_a, comps_b = set(a.get("components") or []), set(b.get("components") or [])
    if comps_a and comps_b:
        jac += 0.10 if comps_a & comps_b else -0.15
    if a.get("project") == b.get("project"):
        jac += 0.05
    return max(0.0, min(1.0, jac))


def cluster_duplicates(d: dict, threshold: float = 0.55,
                       include_closed: bool = False) -> list[dict]:
    """Greedy single-link clustering of similar bugs. Proposals only."""
    pool = [b for b in d["bugs"]
            if b.get("in_scope", True)
            and (include_closed or b.get("status_category") != "done")]
    clusters: list[list[dict]] = []
    for b in pool:
        placed = False
        for cl in clusters:
            if any(similarity(b, o) >= threshold for o in cl):
                cl.append(b)
                placed = True
                break
        if not placed:
            clusters.append([b])
    out = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        cl.sort(key=lambda b: b.get("created") or "")
        fams = {(b.get("triage") or {}).get("family") for b in cl}
        fams.discard(None)
        out.append({
            "primary": cl[0]["key"],
            "members": [b["key"] for b in cl],
            "summaries": [b["summary"] for b in cl],
            "components": sorted({c for b in cl for c in b.get("components") or []}),
            "existing_families": sorted(fams),
            "score": round(min(similarity(cl[0], o) for o in cl[1:]), 2),
        })
    out.sort(key=lambda c: -len(c["members"]))
    return out


# ----------------------------------------------------------------------------
# Rendering helpers
# ----------------------------------------------------------------------------

SIGNAL_ICON = {"moving": "🟢", "slow": "🟡", "stalled": "🟠",
               "dormant": "🔴", "closed": "✅"}


def _table(rows: list[tuple], headers: tuple) -> None:
    if not rows:
        print("  (none)")
        return
    allr = [headers] + [tuple(str(c) for c in r) for r in rows]
    widths = [max(len(r[i]) for r in allr) for i in range(len(headers))]
    line = "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  " + "  ".join("-" * w for w in widths))
    for r in allr[1:]:
        print("  " + "  ".join(r[i].ljust(widths[i]) for i in range(len(headers))))


def _short(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


# ----------------------------------------------------------------------------
# Subcommands
# ----------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    d = load()
    try:
        rep = do_scan(d, activity=args.activity, extra_jql=args.jql,
                      workers=args.workers)
    except JiraError as e:
        print(f"[bm] jira error: {e}", file=sys.stderr)
        return 2
    save(d)
    if args.json:
        rep["bugs"] = view(d)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print(f"[bm] scanned {rep['fetched']} bugs "
          f"({rep['activity_fetched']} activity fetches) at {NOW}")
    if rep.get("bootstrap"):
        print(f"[bm] bootstrap: seeded {len(rep['new'])} bugs, first_seen backfilled "
              f"from Jira creation dates. Subsequent scans report real deltas.")
        rep["new"] = []
    if rep["new"]:
        print(f"\n🆕 NEW ({len(rep['new'])})")
        _table([(k, _short(find_bug(d, k)["summary"], 78),
                 find_bug(d, k).get("priority") or "—",
                 find_bug(d, k).get("assignee") or "unassigned")
                for k in rep["new"]],
               ("KEY", "SUMMARY", "PRI", "ASSIGNEE"))
    if rep["changed"]:
        print(f"\n🔁 CHANGED ({len(rep['changed'])})")
        for c in rep["changed"]:
            print(f"  {c['key']}: " + "; ".join(c["diff"]))
    if rep["closed"]:
        print(f"\n✅ CLOSED since last scan: {', '.join(rep['closed'])}")
    if rep["gone"]:
        print(f"\n👻 LEFT SCOPE (kept, marked out-of-scope): {', '.join(rep['gone'])}")
    if not (rep["new"] or rep["changed"] or rep["closed"] or rep["gone"]):
        print("\nNo changes since the last scan.")
    untriaged = [b for b in d["bugs"]
                 if b.get("in_scope", True) and b.get("status_category") != "done"
                 and (b.get("triage") or {}).get("state") == "untriaged"]
    if untriaged:
        print(f"\n⚠️  {len(untriaged)} open bug(s) still untriaged: "
              f"{', '.join(b['key'] for b in untriaged[:12])}")
    return 0


def _filtered(d: dict, args: argparse.Namespace) -> list[dict]:
    rows = view(d)
    f = getattr(args, "filter", "open") or "open"
    if f == "open":
        rows = [b for b in rows if b["open"]]
    elif f == "new":
        rows = [b for b in rows if b["is_new"] and b["open"]]
    elif f == "untriaged":
        rows = [b for b in rows if b["open"]
                and (b.get("triage") or {}).get("state") == "untriaged"]
    elif f == "stalled":
        rows = [b for b in rows if b["signal"] in ("stalled", "dormant")]
    elif f == "closed":
        rows = [b for b in rows if not b["open"]]
    if getattr(args, "assignee", None):
        a = args.assignee.lower()
        rows = [b for b in rows if a in (b.get("assignee") or "unassigned").lower()]
    if getattr(args, "family", None):
        rows = [b for b in rows
                if (b.get("triage") or {}).get("family") == args.family]
    if getattr(args, "priority", None):
        rows = [b for b in rows
                if (b.get("priority") or "").lower() == args.priority.lower()]
    if getattr(args, "component", None):
        rows = [b for b in rows if args.component in (b.get("components") or [])]
    if getattr(args, "watch", None):
        rows = [b for b in rows if b.get("watch") == args.watch]
    elif f != "closed":
        rows = [b for b in rows if b.get("watch") != "muted"]
    rows.sort(key=lambda b: (SIGNALS.index(b["signal"]), -(b["idle_days"] or 0)))
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    d = load()
    rows = _filtered(d, args)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"[bm] {len(rows)} bug(s) · filter={args.filter} · last scan {d['last_scan'] or 'never'}")
    _table([(SIGNAL_ICON.get(b["signal"], "?") + " " + b["key"],
             _short(b["summary"], 60),
             (b.get("priority") or "—")[:8],
             (b.get("status") or "—")[:12],
             _short(b.get("assignee") or "unassigned", 16),
             f"{b['idle_days']}d" if b["idle_days"] is not None else "—",
             ("📣" + str(b["oded_pings"])) if b["oded_pings"] else "—",
             (b.get("triage") or {}).get("state") or "—",
             (b.get("triage") or {}).get("family") or "—",
             ",".join(b["flags"][:3]) or "—")
            for b in rows],
           ("BUG", "SUMMARY", "PRI", "STATUS", "OWNER", "IDLE", "PINGS",
            "TRIAGE", "FAM", "FLAGS"))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    d = load()
    b = find_bug(d, args.key)
    if not b:
        print(f"[bm] unknown bug {args.key}", file=sys.stderr)
        return 1
    a = annotate(b, d)
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
        return 0
    tri = a.get("triage") or {}
    act = a.get("activity") or {}
    print(f"{a['key']}  {a['summary']}")
    print(f"  {a['url']}")
    print(f"  status     : {a.get('status')} ({a.get('status_category')}) "
          f"since {a.get('status_since') or '—'}")
    print(f"  priority   : {a.get('priority')}   severity call: {tri.get('severity_call') or '—'}")
    print(f"  assignee   : {a.get('assignee') or 'unassigned'}   reporter: {a.get('reporter')}")
    print(f"  components : {', '.join(a.get('components') or []) or '—'}   "
          f"labels: {', '.join(a.get('labels') or []) or '—'}")
    print(f"  plan       : milestone={a.get('milestone') or '—'} "
          f"sprint={', '.join(a.get('sprints') or []) or '—'} "
          f"est={a.get('estimate_h') or '—'}h due={a.get('duedate') or '—'}")
    print(f"  timeline   : created {a.get('created')} ({a['age_days']}d) · "
          f"last activity {a.get('last_activity') or '—'} ({a['idle_days']}d idle) · "
          f"signal {SIGNAL_ICON.get(a['signal'],'')} {a['signal']}")
    print(f"  activity   : {act.get('comment_count', 0)} comments "
          f"(last {act.get('last_comment') or '—'} by {act.get('last_comment_by') or '—'}) · "
          f"{act.get('change_count', 0)} field changes · "
          f"last status change {act.get('last_status_change') or '—'}")
    print(f"  triage     : {tri.get('state')} / verdict={tri.get('verdict') or '—'} "
          f"family={tri.get('family') or '—'} ({a.get('family_title') or '—'}) "
          f"owner={tri.get('owner') or '—'}")
    if tri.get("root_cause"):
        print(f"  root cause : {tri['root_cause']}")
    if tri.get("next_step"):
        print(f"  next step  : {tri['next_step']}")
    if tri.get("investigation"):
        print(f"  note file  : {tri['investigation']}")
    print(f"  flags      : {', '.join(a['flags']) or '—'}   watch: {a.get('watch')}")
    print(f"  oded pings : {a['oded_pings']}"
          + (f"   last {a.get('last_ping')} ({a['days_since_ping']}d ago)"
             if a.get("last_ping") else ""))
    if a.get("links"):
        print("  jira links : " + ", ".join(f"{l['type']} {l['key']}" for l in a["links"]))
    if a.get("description"):
        print("  description:")
        for line in a["description"].splitlines()[:8]:
            print(f"    {line}")
    if a.get("changes"):
        print("  change log :")
        for c in a["changes"][-6:]:
            print(f"    {c['at']}: " + "; ".join(c["diff"]))
    if a.get("notes"):
        print("  notes      :")
        for n in a["notes"][-8:]:
            print(f"    {n}")
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    d = load()
    b = find_bug(d, args.key)
    if not b:
        print(f"[bm] unknown bug {args.key}", file=sys.stderr)
        return 1
    tri = b.setdefault("triage", {})
    changed = []
    for attr, field in (("state", "state"), ("verdict", "verdict"),
                        ("severity", "severity_call"), ("owner", "owner"),
                        ("root_cause", "root_cause"), ("next_step", "next_step"),
                        ("investigation", "investigation")):
        val = getattr(args, attr, None)
        if val is not None:
            tri[field] = val
            changed.append(f"{field}={val}")
    if args.family:
        fam = find_family(d, args.family)
        if not fam:
            print(f"[bm] unknown family {args.family} — create it with "
                  f"`bm.py family new <title>`", file=sys.stderr)
            return 1
        tri["family"] = fam["id"]
        if b["key"] not in fam["members"]:
            fam["members"].append(b["key"])
        changed.append(f"family={fam['id']}")
    if args.verdict and not args.state:
        tri["state"] = "triaged"
        changed.append("state=triaged")
    if tri.get("state") in ("triaged", "deferred"):
        tri["triaged_on"] = TODAY
    if args.note:
        append_note(b, args.note)
    if changed:
        append_note(b, "triage " + ", ".join(changed))
    save(d)
    print(f"[bm] {b['key']} triage updated: {', '.join(changed) or 'note only'}")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    d = load()
    b = find_bug(d, args.key)
    if not b:
        print(f"[bm] unknown bug {args.key}", file=sys.stderr)
        return 1
    append_note(b, " ".join(args.text))
    save(d)
    print(f"[bm] noted on {b['key']}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    d = load()
    b = find_bug(d, args.key)
    if not b:
        print(f"[bm] unknown bug {args.key}", file=sys.stderr)
        return 1
    b["watch"] = args.state
    append_note(b, f"watch → {args.state}" + (f" ({args.reason})" if args.reason else ""))
    save(d)
    print(f"[bm] {b['key']} watch = {args.state}")
    return 0


def cmd_ping(args: argparse.Namespace) -> int:
    """Record that Oded chased this bug for progress/status."""
    d = load()
    b = find_bug(d, args.key)
    if not b:
        print(f"[bm] unknown bug {args.key}", file=sys.stderr)
        return 1
    if args.set is not None:
        b["manual_pings"] = 0
        b["oded_pings"] = max(0, args.set)
        b["last_ping"] = args.on or (TODAY if args.set else None)
        msg = f"oded pings set to {b['oded_pings']} (until next scan)"
    else:
        # Scans recompute oded_pings from Jira, so an out-of-band chase has to
        # live in manual_pings or it would vanish on the next refresh.
        b["manual_pings"] = (b.get("manual_pings") or 0) + args.n
        b["oded_pings"] = (b.get("oded_pings") or 0) + args.n
        b["manual_last_ping"] = args.on or TODAY
        b["last_ping"] = b["manual_last_ping"]
        msg = (f"pinged by Oded x{args.n}" if args.n > 1 else "pinged by Oded")
        msg += f" (total {b['oded_pings']})"
    append_note(b, msg + (f" — {args.note}" if args.note else ""))
    save(d)
    print(f"[bm] {b['key']}: {msg}, last {b['last_ping'] or '—'}")
    return 0


def cmd_family(args: argparse.Namespace) -> int:
    d = load()
    act = args.action

    if act == "list":
        if args.json:
            print(json.dumps(d["families"], ensure_ascii=False, indent=2))
            return 0
        rows = []
        for f in d["families"]:
            members = [find_bug(d, k) for k in f["members"]]
            members = [m for m in members if m]
            openn = sum(1 for m in members if m.get("status_category") != "done")
            rows.append((f["id"], _short(f["title"], 46), f.get("status", "open"),
                         f.get("primary") or "—", f"{openn}/{len(members)}",
                         _short(f.get("root_cause") or "—", 34)))
        _table(rows, ("ID", "TITLE", "STATE", "PRIMARY", "OPEN/ALL", "ROOT CAUSE"))
        return 0

    if act == "new":
        fid = f"F-{d['next_family_id']:03d}"
        d['next_family_id'] += 1
        fam = {"id": fid, "title": " ".join(args.title), "status": "open",
               "primary": args.primary, "members": list(args.members or []),
               "root_cause": args.root_cause, "created": TODAY, "notes": []}
        if args.primary and args.primary not in fam["members"]:
            fam["members"].insert(0, args.primary)
        d["families"].append(fam)
        for k in fam["members"]:
            b = find_bug(d, k)
            if b:
                b.setdefault("triage", {})["family"] = fid
        save(d)
        print(f"[bm] created {fid} · {fam['title']} · {len(fam['members'])} member(s)")
        return 0

    fam = find_family(d, args.id)
    if not fam:
        print(f"[bm] unknown family {args.id}", file=sys.stderr)
        return 1

    if act == "add":
        for k in args.members:
            b = find_bug(d, k)
            if not b:
                print(f"[bm] unknown bug {k} — skipped", file=sys.stderr)
                continue
            if b["key"] not in fam["members"]:
                fam["members"].append(b["key"])
            b.setdefault("triage", {})["family"] = fam["id"]
            append_note(b, f"joined family {fam['id']} ({fam['title']})")
    elif act == "drop":
        for k in args.members:
            fam["members"] = [m for m in fam["members"] if m.upper() != k.upper()]
            b = find_bug(d, k)
            if b and (b.get("triage") or {}).get("family") == fam["id"]:
                b["triage"]["family"] = None
    elif act == "primary":
        fam["primary"] = args.members[0].upper()
        if fam["primary"] not in fam["members"]:
            fam["members"].insert(0, fam["primary"])
    elif act == "set":
        if args.title:
            fam["title"] = " ".join(args.title)
    elif act == "close":
        fam["status"] = "closed"
        fam.setdefault("notes", []).append(f"{NOW}: closed" +
                                           (f" — {args.reason}" if args.reason else ""))
    elif act == "show":
        print(f"{fam['id']} · {fam['title']}  [{fam.get('status')}]")
        print(f"  primary   : {fam.get('primary') or '—'}")
        print(f"  root cause: {fam.get('root_cause') or '—'}")
        rows = []
        for k in fam["members"]:
            b = find_bug(d, k)
            if not b:
                rows.append((k, "(not in store)", "", "", ""))
                continue
            a = annotate(b, d)
            rows.append((("* " if k == fam.get("primary") else "  ") + k,
                         _short(a["summary"], 58), a.get("status") or "—",
                         SIGNAL_ICON.get(a["signal"], "") + a["signal"],
                         f"{a['idle_days']}d" if a["idle_days"] is not None else "—"))
        _table(rows, ("MEMBER", "SUMMARY", "STATUS", "SIGNAL", "IDLE"))
        for n in fam.get("notes") or []:
            print(f"  {n}")
        return 0

    if getattr(args, "root_cause", None):
        fam["root_cause"] = args.root_cause
    save(d)
    print(f"[bm] {fam['id']} updated ({len(fam['members'])} member(s))")
    return 0


def cmd_dups(args: argparse.Namespace) -> int:
    d = load()
    clusters = cluster_duplicates(d, args.threshold, args.include_closed)
    if args.json:
        print(json.dumps(clusters, ensure_ascii=False, indent=2))
        return 0
    if not clusters:
        print(f"[bm] no clusters above similarity {args.threshold}")
        return 0
    print(f"[bm] {len(clusters)} candidate duplicate cluster(s) "
          f"(similarity >= {args.threshold}) — PROPOSALS ONLY, confirm before linking\n")
    for i, c in enumerate(clusters, 1):
        fam = (", already in " + ", ".join(c["existing_families"])
               if c["existing_families"] else "")
        print(f"#{i}  {len(c['members'])} bugs · score {c['score']} · "
              f"{', '.join(c['components']) or 'no component'}{fam}")
        for k, s in zip(c["members"], c["summaries"]):
            b = annotate(find_bug(d, k), d)
            mark = "*" if k == c["primary"] else " "
            print(f"   {mark} {k:<20} {(b.get('status') or '—'):<12} {_short(s, 74)}")
        print()
    print("Legend: * = oldest (suggested primary). "
          "Record a confirmed cluster with `bm.py family new ...`.")
    return 0


def cmd_pulse(args: argparse.Namespace) -> int:
    """Team-lead monitoring view: where is progress, and where is it missing."""
    d = load()
    cfg = d["config"]
    rows = [b for b in view(d) if b["open"] and b.get("watch") != "muted"]
    buckets: dict[str, list[dict]] = {s: [] for s in SIGNALS}
    for b in rows:
        buckets[b["signal"]].append(b)
    if args.json:
        print(json.dumps({"buckets": {k: v for k, v in buckets.items() if v},
                          "config": cfg, "last_scan": d["last_scan"]},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"HSI Bug Pulse · {len(rows)} open · last scan {d['last_scan'] or 'never'}")
    print("─" * 72)
    for sig in ("dormant", "stalled", "slow", "moving"):
        bs = buckets[sig]
        label = {"dormant": f"no activity ≥{cfg['dormant_days']}d",
                 "stalled": f"no activity ≥{cfg['stall_days']}d",
                 "slow": f"no activity ≥{cfg['slow_days']}d",
                 "moving": "touched this week"}[sig]
        print(f"\n{SIGNAL_ICON[sig]} {sig.upper():<8} {len(bs):>3}  ({label})")
        if bs:
            _table([(b["key"], _short(b["summary"], 54),
                     _short(b.get("assignee") or "unassigned", 15),
                     (b.get("status") or "—")[:12],
                     f"{b['idle_days']}d", ",".join(b["flags"][:3]) or "—")
                    for b in bs],
                   ("BUG", "SUMMARY", "OWNER", "STATUS", "IDLE", "FLAGS"))

    by_owner: dict[str, list[dict]] = {}
    for b in rows:
        by_owner.setdefault(b.get("assignee") or "unassigned", []).append(b)
    print("\n👥 BY OWNER")
    _table([(o, str(len(bs)),
             str(sum(1 for b in bs if b["signal"] in ("stalled", "dormant"))),
             str(sum(1 for b in bs
                     if (b.get("triage") or {}).get("state") == "untriaged")))
            for o, bs in sorted(by_owner.items(), key=lambda kv: -len(kv[1]))],
           ("OWNER", "OPEN", "STALLED", "UNTRIAGED"))

    flagged: dict[str, list[str]] = {}
    for b in rows:
        for fl in b["flags"]:
            flagged.setdefault(fl, []).append(b["key"])
    if flagged:
        print("\n🚩 HYGIENE")
        _table([(fl, str(len(ks)), _short(", ".join(ks), 60))
                for fl, ks in sorted(flagged.items(), key=lambda kv: -len(kv[1]))],
               ("FLAG", "N", "BUGS"))

    fams = [f for f in d["families"] if f.get("status") != "closed"]
    if fams:
        print("\n🧬 OPEN FAMILIES")
        _table([(f["id"], _short(f["title"], 44), f.get("primary") or "—",
                 str(sum(1 for k in f["members"]
                         if (find_bug(d, k) or {}).get("status_category") != "done")),
                 str(len(f["members"])))
                for f in fams],
               ("ID", "TITLE", "PRIMARY", "OPEN", "MEMBERS"))

    pinged = sorted((b for b in rows if b["oded_pings"]),
                    key=lambda b: (-b["oded_pings"], b["days_since_ping"] or 999))
    if pinged:
        print("\n📣 PINGED BY ODED")
        _table([(b["key"], _short(b["summary"], 48),
                 str(b["oded_pings"]),
                 b.get("last_ping") or "—",
                 f"{b['idle_days']}d" if b["idle_days"] is not None else "—",
                 f"{SIGNAL_ICON[b['signal']]} {b['signal']}")
                for b in pinged],
               ("BUG", "SUMMARY", "PINGS", "LAST PING", "IDLE", "SIGNAL"))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Markdown digest, suitable for pasting into a weekly / manager brief."""
    d = load()
    cfg = d["config"]
    rows = [b for b in view(d) if b.get("watch") != "muted"]
    openb = [b for b in rows if b["open"]]
    new = [b for b in openb if b["is_new"]]
    untri = [b for b in openb if (b.get("triage") or {}).get("state") == "untriaged"]
    stuck = [b for b in openb if b["signal"] in ("stalled", "dormant")]
    closed_recent = [b for b in rows if not b["open"]
                     and (_days_since(b.get("resolved")) or 999) <= args.window]

    out = [f"## HSI Bug Status — {TODAY}", "",
           f"- **Open:** {len(openb)}  ·  **New (≤{cfg['new_days']}d):** {len(new)}"
           f"  ·  **Untriaged:** {len(untri)}  ·  **Stalled/dormant:** {len(stuck)}"
           f"  ·  **Closed (≤{args.window}d):** {len(closed_recent)}", ""]

    def tbl(title: str, bs: list[dict], extra: str = "idle") -> None:
        if not bs:
            return
        out.append(f"### {title}")
        out.append("| Bug | Summary | Owner | Status | " +
                   ("Idle" if extra == "idle" else "Resolved") + " |")
        out.append("|---|---|---|---|---|")
        for b in bs:
            tail = (f"{b['idle_days']}d" if extra == "idle" else (b.get("resolved") or "—"))
            out.append(f"| [{b['key']}]({b['url']}) | {_short(b['summary'], 70)} | "
                       f"{b.get('assignee') or '—'} | {b.get('status') or '—'} | {tail} |")
        out.append("")

    tbl("🆕 New", new)
    tbl("⚠️ Untriaged", untri)
    tbl("🟠 No progress", sorted(stuck, key=lambda b: -(b["idle_days"] or 0)))
    tbl("✅ Recently closed", closed_recent, extra="resolved")

    fams = [f for f in d["families"] if f.get("status") != "closed"]
    if fams:
        out.append("### 🧬 Duplicate families")
        out.append("| Family | Primary | Open/All | Root cause |")
        out.append("|---|---|---|---|")
        for f in fams:
            openn = sum(1 for k in f["members"]
                        if (find_bug(d, k) or {}).get("status_category") != "done")
            out.append(f"| {f['id']} — {f['title']} | {f.get('primary') or '—'} | "
                       f"{openn}/{len(f['members'])} | {f.get('root_cause') or '—'} |")
        out.append("")
    print("\n".join(out))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    d = load()
    if not args.set:
        print(json.dumps(d["config"], indent=2))
        return 0
    for kv in args.set:
        k, _, v = kv.partition("=")
        k = k.strip()
        if k not in DEFAULT_CONFIG:
            print(f"[bm] unknown config key {k}", file=sys.stderr)
            return 1
        cur = DEFAULT_CONFIG[k]
        if isinstance(cur, int):
            d["config"][k] = int(v)
        elif isinstance(cur, list):
            d["config"][k] = [x.strip() for x in v.split(",") if x.strip()]
        else:
            d["config"][k] = v
    save(d)
    print(json.dumps(d["config"], indent=2))
    return 0


# --- Jira writes: approval-gated -------------------------------------------

def cmd_jira_comment(args: argparse.Namespace) -> int:
    if not args.yes:
        print("[bm] refusing to post — this writes to Jira. Re-run with --yes "
              "ONLY after the user has approved the exact text.", file=sys.stderr)
        return 1
    body = Path(args.body_file).read_text() if args.body_file else " ".join(args.body)
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": body}]}]}
    try:
        _request("POST", f"/rest/api/3/issue/{args.key}/comment", {"body": adf})
    except JiraError as e:
        print(f"[bm] {e}", file=sys.stderr)
        return 2
    d = load()
    b = find_bug(d, args.key)
    if b:
        append_note(b, f"posted Jira comment: {_short(body, 90)}")
        save(d)
    print(f"[bm] commented on {args.key}")
    return 0


def cmd_jira_link(args: argparse.Namespace) -> int:
    if not args.yes:
        print("[bm] refusing to link — this writes to Jira. Re-run with --yes "
              "ONLY after the user has approved.", file=sys.stderr)
        return 1
    payload = {"type": {"name": args.type},
               "inwardIssue": {"key": args.dup},
               "outwardIssue": {"key": args.primary}}
    try:
        _request("POST", "/rest/api/3/issueLink", payload)
    except JiraError as e:
        print(f"[bm] {e}", file=sys.stderr)
        return 2
    d = load()
    b = find_bug(d, args.dup)
    if b:
        append_note(b, f"linked as '{args.type}' to {args.primary}")
        save(d)
    print(f"[bm] linked {args.dup} --{args.type}--> {args.primary}")
    return 0


# ----------------------------------------------------------------------------
# Dashboard hook (used by tm.py)
# ----------------------------------------------------------------------------

def apply_bug_edit(key: str, payload: dict) -> tuple[bool, str]:
    """Apply a dashboard edit to one bug. Local monitoring fields only —
    never touches Jira (Jira state is refreshed by `scan`)."""
    d = load()
    b = find_bug(d, key)
    if not b:
        return False, f"unknown bug {key}"
    tri = b.setdefault("triage", {})
    changed = []
    if payload.get("triage_state"):
        if payload["triage_state"] not in TRIAGE_STATES:
            return False, "invalid triage state"
        tri["state"] = payload["triage_state"]
        if tri["state"] in ("triaged", "deferred"):
            tri["triaged_on"] = TODAY
        changed.append(f"state={tri['state']}")
    if "verdict" in payload and payload["verdict"] != "":
        if payload["verdict"] not in TRIAGE_VERDICTS:
            return False, "invalid verdict"
        tri["verdict"] = payload["verdict"]
        changed.append(f"verdict={tri['verdict']}")
    if "severity" in payload and payload["severity"] != "":
        if payload["severity"] not in SEVERITIES:
            return False, "invalid severity"
        tri["severity_call"] = payload["severity"]
        changed.append(f"severity={tri['severity_call']}")
    if "family" in payload:
        fid = payload["family"] or None
        if fid and not any(f["id"] == fid for f in d["families"]):
            return False, f"unknown family {fid}"
        old = tri.get("family")
        if old and old != fid:
            fam = next((f for f in d["families"] if f["id"] == old), None)
            if fam:
                fam["members"] = [m for m in fam["members"] if m != b["key"]]
        tri["family"] = fid
        if fid:
            fam = next(f for f in d["families"] if f["id"] == fid)
            if b["key"] not in fam["members"]:
                fam["members"].append(b["key"])
        changed.append(f"family={fid or '—'}")
    for fld in ("root_cause", "next_step", "owner"):
        if fld in payload:
            tri[fld] = payload[fld] or None
            changed.append(fld)
    if payload.get("watch"):
        if payload["watch"] not in WATCH_STATES:
            return False, "invalid watch state"
        b["watch"] = payload["watch"]
        changed.append(f"watch={b['watch']}")
    if payload.get("ping"):
        b["manual_pings"] = (b.get("manual_pings") or 0) + 1
        b["oded_pings"] = (b.get("oded_pings") or 0) + 1
        b["manual_last_ping"] = TODAY
        b["last_ping"] = TODAY
        changed.append(f"pinged by Oded (total {b['oded_pings']})")
    elif "oded_pings" in payload and payload["oded_pings"] != "":
        try:
            n = max(0, int(payload["oded_pings"]))
        except (TypeError, ValueError):
            return False, "invalid ping count"
        b["manual_pings"] = 0
        b["oded_pings"] = n
        b["last_ping"] = TODAY if n else None
        changed.append(f"oded_pings={n} (until next scan)")
    if payload.get("note"):
        append_note(b, payload["note"].strip())
        changed.append("note")
    if not changed:
        return False, "nothing to change"
    if "note" not in changed:
        append_note(b, "dashboard: " + ", ".join(changed))
    save(d)
    return True, f"{b['key']}: " + ", ".join(changed)


def dashboard_payload() -> dict:
    """Everything the portal's Bugs section needs, in one blob."""
    d = load()
    return {
        "bugs": view(d),
        "families": d["families"],
        "config": d["config"],
        "last_scan": d["last_scan"],
    }


def portal_scan() -> dict:
    """Jira refresh driven from the portal's scan button. Talks to Jira only."""
    d = load()
    report = do_scan(d)
    save(d)
    payload = dashboard_payload()
    payload["scan"] = {
        "fetched": report["fetched"],
        "new": report["new"],
        "changed": len(report["changed"]),
        "closed": report["closed"],
        "gone": report["gone"],
        "bootstrap": report["bootstrap"],
        "activity": report["activity_fetched"],
        "pings": sum(b.get("oded_pings") or 0 for b in d["bugs"]),
        "ci_runs": sum(len(b.get("ci_runs") or []) for b in d["bugs"]),
    }
    return payload


# ----------------------------------------------------------------------------
# Argparse
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bm.py", description="HSI bug monitor")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="refresh the local mirror from Jira")
    s.add_argument("--activity", choices=["auto", "all", "none"], default="auto",
                   help="fetch changelog/comments: auto=open bugs (default)")
    s.add_argument("--jql", help="extra JQL ANDed onto the scope query")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)

    def add_filters(sp):
        sp.add_argument("--filter", choices=["open", "new", "untriaged", "stalled",
                                             "closed", "all"], default="open")
        sp.add_argument("--assignee")
        sp.add_argument("--family")
        sp.add_argument("--priority")
        sp.add_argument("--component")
        sp.add_argument("--watch", choices=WATCH_STATES)
        sp.add_argument("--json", action="store_true")

    s = sub.add_parser("list", help="list monitored bugs")
    add_filters(s)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="full detail for one bug")
    s.add_argument("key")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("triage", help="record a triage outcome (no Jira write)")
    s.add_argument("key")
    s.add_argument("--state", choices=TRIAGE_STATES)
    s.add_argument("--verdict", choices=TRIAGE_VERDICTS)
    s.add_argument("--severity", choices=SEVERITIES)
    s.add_argument("--family", help="family id (F-00N) or unique title substring")
    s.add_argument("--owner", help="who should drive it")
    s.add_argument("--root-cause", dest="root_cause")
    s.add_argument("--next-step", dest="next_step")
    s.add_argument("--investigation", help="path to the bug-triage investigation note")
    s.add_argument("--note")
    s.set_defaults(func=cmd_triage)

    s = sub.add_parser("note", help="append a monitoring note")
    s.add_argument("key")
    s.add_argument("text", nargs="+")
    s.set_defaults(func=cmd_note)

    s = sub.add_parser("watch", help="set watch state")
    s.add_argument("key")
    s.add_argument("state", choices=WATCH_STATES)
    s.add_argument("--reason")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("ping", help="record a ping from Oded chasing progress/status")
    s.add_argument("key")
    s.add_argument("-n", type=int, default=1, help="how many pings to add (default 1)")
    s.add_argument("--set", type=int, help="set the total instead of incrementing")
    s.add_argument("--on", help="date of the ping (YYYY-MM-DD, default today)")
    s.add_argument("--note", help="what he asked for")
    s.set_defaults(func=cmd_ping)

    s = sub.add_parser("family", help="manage duplicate families")
    fsub = s.add_subparsers(dest="action", required=True)
    f = fsub.add_parser("list", help="list families")
    f.add_argument("--json", action="store_true")
    f = fsub.add_parser("new", help="create a family from confirmed duplicates")
    f.add_argument("title", nargs="+")
    f.add_argument("--primary", help="canonical bug key")
    f.add_argument("--members", nargs="*", default=[], help="bug keys")
    f.add_argument("--root-cause", dest="root_cause")
    f = fsub.add_parser("show", help="show one family")
    f.add_argument("id")
    f = fsub.add_parser("add", help="add bugs to a family")
    f.add_argument("id")
    f.add_argument("members", nargs="+")
    f = fsub.add_parser("drop", help="remove bugs from a family")
    f.add_argument("id")
    f.add_argument("members", nargs="+")
    f = fsub.add_parser("primary", help="set the canonical bug")
    f.add_argument("id")
    f.add_argument("members", nargs=1, metavar="KEY")
    f = fsub.add_parser("set", help="edit family fields")
    f.add_argument("id")
    f.add_argument("--title", nargs="+")
    f.add_argument("--root-cause", dest="root_cause")
    f = fsub.add_parser("close", help="close a family")
    f.add_argument("id")
    f.add_argument("--reason")
    s.set_defaults(func=cmd_family)

    s = sub.add_parser("dups", help="propose duplicate clusters (read-only)")
    s.add_argument("--threshold", type=float, default=0.55)
    s.add_argument("--include-closed", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_dups)

    s = sub.add_parser("pulse", help="progress / lack-of-progress monitoring view")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_pulse)

    s = sub.add_parser("report", help="markdown digest for weekly / manager brief")
    s.add_argument("--window", type=int, default=7, help="recently-closed window (days)")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("config", help="show/set monitor config")
    s.add_argument("--set", action="append", metavar="KEY=VALUE")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("jira-comment", help="post a comment (APPROVAL REQUIRED)")
    s.add_argument("key")
    s.add_argument("body", nargs="*")
    s.add_argument("--body-file")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_jira_comment)

    s = sub.add_parser("jira-link", help="link two issues (APPROVAL REQUIRED)")
    s.add_argument("dup", help="the duplicate / dependent issue")
    s.add_argument("primary", help="the canonical issue")
    s.add_argument("--type", default="Duplicate")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_jira_link)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
