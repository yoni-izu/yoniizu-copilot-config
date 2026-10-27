#!/usr/bin/env python3
"""bug-triage phenomenon registry — mechanics for ticketless, cross-platform triage.

Owns the phenomenon registry: every *observed failure* the team has seen,
regardless of whether anyone ever opened a Jira ticket for it. The registry
lives in the shared bug database ``~/tasks/bugs.db`` (``bug-monitor/bugdb.py``),
the single source of truth that ``bm.py`` and the Team Portal read and write
too. Set ``BUGS_DB`` to point at a different database (used for testing —
never share a test database with the live one).

The unit of record is the **sighting** — one observation of a failure, on one
platform (CI / sim / FPGA / Palladium / lab silicon / production), arriving
through any channel (Slack, chat, a customer mail, a CI run, a bench session,
a hallway remark). Sightings that share a **signature** are grouped into a
**phenomenon**, which is the thing that actually gets root-caused and fixed.

Everything here is mechanical: normalizing raw evidence into a fingerprint,
scoring a new sighting against the registry, bookkeeping the lifecycle. The
judgment calls — "is this really the same failure", "what is the root cause" —
belong to the model driving the ``bug-triage`` skill, which records its
conclusions back through ``set`` / ``link`` / ``merge``.

This store NEVER writes to Jira. ``promote`` only drafts a ticket for a human
to approve and file.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.expanduser("~/.copilot/skills/bug-monitor"))
import bugdb  # noqa: E402  — the shared bug database (single source of truth)

STORE = bugdb.DB_PATH

PLATFORMS = ["ci", "sim", "fpga", "palladium", "silicon-lab", "production", "unknown"]
SOURCES = ["ci", "jira", "slack", "chat", "email", "customer", "lab", "meeting",
           "verbal", "self", "other"]
STATUSES = ["open", "investigating", "root-caused", "fix-in-flight", "fixed",
            "monitoring", "closed", "not-a-bug", "not-ours", "merged"]
SEVERITIES = ["critical", "high", "medium", "low"]

# Platforms ordered by how close they are to a shipping customer. A phenomenon
# that moves rightwards has *escaped* a gate and is more urgent than its raw
# count suggests.
PLATFORM_RANK = {p: i for i, p in enumerate(
    ["sim", "fpga", "palladium", "ci", "silicon-lab", "production"])}

DEFAULT_CONFIG = {
    "match_threshold": 0.45,   # below this, not even a candidate
    "strong_threshold": 0.72,  # at/above this, --auto will attach without asking
    "hot_days": 3,             # sighting within N days => hot
    "active_days": 14,         # sighting within N days => active
    "quiet_days": 21,          # no sighting for N days => quiet
    "recurring_at": 3,         # N+ sightings => recurring
}

# ----------------------------------------------------------------------------
# Signature engine
# ----------------------------------------------------------------------------

# Applied in order, to lowercased text. Each rule removes a coordinate that
# varies run-to-run (time, address, station, run id) while keeping the tokens
# that identify the *failure* (error codes, asserts, symbols, small status hex).
SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\x1b\[[0-9;]*m"), " "),                               # ANSI
    (re.compile(r"\[\s*\d+\.\d+\]"), " "),                              # dmesg ts
    (re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}"
                r"(?:[.,]\d+)?(?:z|[+-]\d{2}:?\d{2})?"), " ts "),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), " ts "),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), " ts "),
    (re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+"
                r"(?:/job/\d+)?"), " cirun "),
    (re.compile(r"https?://\S+"), " url "),
    (re.compile(r"\bpldm-edk\d+-z\d+[a-z]?\b"), " station "),
    (re.compile(r"\b(?:dsk|srv|nuc)\d+\b"), " host "),
    (re.compile(r"\bbmc-[\w-]+\b"), " host "),
    (re.compile(r"\b[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d\b"), " bdf "),
    (re.compile(r"\b0x[0-9a-f]{5,}\b"), " addr "),                      # addresses
    (re.compile(r"\b[0-9a-f]{7,40}\b"), " sha "),                       # git sha
    (re.compile(r"(?<![\w.-])\d{3,}(?![\w])"), " n "),                  # big ints
    (re.compile(r"[^\w\s:.\-/]+"), " "),
    (re.compile(r"\s+"), " "),
]

TOKEN_RE = re.compile(r"-\d{1,4}\b|0x[0-9a-f]{1,4}\b|[a-z_][a-z0-9_]{2,}")

STOP = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "has", "have", "not", "but", "its", "it's", "you", "your", "our", "all",
    "any", "can", "did", "does", "got", "see", "seen", "when", "then", "than",
    "after", "before", "into", "out", "off", "over", "under", "again", "also",
    "only", "just", "now", "here", "there", "they", "them", "his", "her",
    "log", "logs", "line", "lines", "info", "warn", "warning", "debug",
    "test", "tests", "run", "runs", "ts", "url", "cirun", "host", "station",
    "sha", "addr", "bdf", "n",
}


def normalize(text: str) -> str:
    """Collapse a raw evidence blob into its run-invariant form."""
    out = (text or "").lower()
    for pat, rep in SUBS:
        out = pat.sub(rep, out)
    return out.strip()


def tokenize(text: str) -> list[str]:
    """Salient tokens of the normalized text, de-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for tok in TOKEN_RE.findall(normalize(text)):
        if tok in STOP or len(tok) < 2:
            continue
        seen.setdefault(tok, None)
    return list(seen)


def fingerprint(text: str) -> str:
    """Stable short hash of the token set — identical evidence, identical fp."""
    toks = sorted(set(tokenize(text)))
    if not toks:
        return "0" * 10
    return hashlib.sha1(" ".join(toks).encode()).hexdigest()[:10]


def similarity(a: str, b: str) -> float:
    """0..1 similarity of two raw evidence blobs."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, normalize(a)[:4000],
                                  normalize(b)[:4000]).ratio()
    # Containment rewards a short Slack one-liner that is a subset of a long log.
    cont = len(ta & tb) / min(len(ta), len(tb))
    return round(0.45 * jac + 0.25 * seq + 0.30 * cont, 4)


# ----------------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------------

def load() -> dict[str, Any]:
    """The registry in its classic dict shape, read from the shared database.
    ``jira``/``investigations`` on each phenomenon are assembled from the link
    tables; ``save()`` writes changed rows and link diffs back."""
    data = bugdb.load_ph()
    data.setdefault("version", 1)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(data.get("config") or {})
    data["config"] = cfg
    data.setdefault("phenomena", [])
    data.setdefault("next_id", len(data["phenomena"]) + 1)
    data.setdefault("next_sighting", 1)
    return data


def save(store: dict[str, Any]) -> None:
    bugdb.save_ph(store)


def find(store: dict[str, Any], pid: str) -> dict[str, Any]:
    pid = pid.upper()
    if not pid.startswith("PH-"):
        pid = f"PH-{int(pid):03d}"
    for ph in store["phenomena"]:
        if ph["id"] == pid:
            return ph
    sys.exit(f"error: no such phenomenon: {pid}")


def today() -> str:
    return date.today().isoformat()


def days_since(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        d = datetime.fromisoformat(stamp[:10]).date()
    except ValueError:
        return None
    return (date.today() - d).days


def ver_tuple(v: str | None) -> tuple[int, ...] | None:
    """Leading dotted-numeric part of a version string, or None if unparseable."""
    if not v:
        return None
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(v))
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def at_or_after_fix(fw: str | None, fix_version: str | None) -> bool:
    """True only when both versions parse and the sighting's build carries the fix."""
    a, b = ver_tuple(fw), ver_tuple(fix_version)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) >= b + (0,) * (n - len(b))


# ----------------------------------------------------------------------------
# Derived view
# ----------------------------------------------------------------------------

def derived(ph: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    s = ph.get("sightings", [])
    dates = sorted(x.get("at", "") for x in s if x.get("at"))
    platforms = sorted({x.get("platform", "unknown") for x in s},
                       key=lambda p: PLATFORM_RANK.get(p, 99))
    sources = sorted({x.get("source", "other") for x in s})
    first, last = (dates[0] if dates else None), (dates[-1] if dates else None)
    idle = days_since(last)

    status = ph.get("status", "open")
    if status in ("closed", "not-a-bug", "not-ours", "merged"):
        signal = "closed"
    elif status == "fixed":
        signal = "fixed"
    elif idle is None:
        signal = "unknown"
    elif idle <= cfg["hot_days"]:
        signal = "hot"
    elif idle <= cfg["active_days"]:
        signal = "active"
    elif idle >= cfg["quiet_days"]:
        signal = "quiet"
    else:
        signal = "slow"

    flags = []
    if not ph.get("jira"):
        flags.append("no-jira")
    if not ph.get("owner"):
        flags.append("no-owner")
    if len(platforms) >= 2:
        flags.append("cross-platform")
    if "production" in platforms:
        flags.append("escaped")
    if "silicon-lab" in platforms and status not in ("fixed", "closed"):
        flags.append("on-silicon")
    if len(s) >= cfg["recurring_at"] and status not in ("fixed", "closed"):
        flags.append("recurring")
    if not ph.get("root_cause") and len(s) >= cfg["recurring_at"]:
        flags.append("unexplained-recurring")
    fixed_at = (ph.get("fix") or {}).get("at")
    if any(x.get("after_fix") for x in s) or (
            fixed_at and last and last > fixed_at):
        flags.append("regressed")
    origin, origin_why = bugdb.ph_origin(ph)
    if origin == "customer":
        flags.append("customer-reported")

    return {"platforms": platforms, "sources": sources, "first_seen": first,
            "last_seen": last, "count": len(s), "idle_days": idle,
            "signal": signal, "flags": flags,
            "origin": origin, "origin_why": origin_why}


def view(ph: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(ph)
    out.update(derived(ph, cfg))
    return out


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------

def rank(store: dict[str, Any], text: str, include_closed: bool = False,
         limit: int = 5) -> list[tuple[float, dict[str, Any], str]]:
    """Rank phenomena against free-text evidence. Returns (score, ph, why)."""
    fp = fingerprint(text)
    out: list[tuple[float, dict[str, Any], str]] = []
    for ph in store["phenomena"]:
        if ph.get("status") == "merged":
            continue
        if not include_closed and ph.get("status") in ("closed", "not-a-bug", "not-ours"):
            continue
        best, why = 0.0, ""
        for sig in ph.get("signatures", []):
            if sig.get("fp") == fp:
                best, why = 1.0, "exact fingerprint"
                break
            sc = similarity(text, sig.get("text", ""))
            if sc > best:
                best, why = sc, "signature similarity"
        for sg in ph.get("sightings", []):
            if best >= 1.0:
                break
            sc = similarity(text, sg.get("raw", ""))
            if sc > best:
                best, why = sc, f"sighting {sg.get('id')}"
        tsc = similarity(text, ph.get("title", ""))
        if tsc > best:
            best, why = tsc, "title"
        if best > 0:
            out.append((round(best, 4), ph, why))
    out.sort(key=lambda t: -t[0])
    return out[:limit]


# ----------------------------------------------------------------------------
# Printing
# ----------------------------------------------------------------------------

SIGNAL_ICON = {"hot": "🔥", "active": "🟢", "slow": "🟡", "quiet": "🟠",
               "fixed": "🔧", "closed": "✅", "unknown": "⚪"}


def table(rows: list[list[str]], headers: list[str]) -> str:
    if not rows:
        return "  (none)"
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    line = "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  " + "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r))
            for r in rows]
    return "\n".join([line, sep] + body)


def brief(v: dict[str, Any]) -> list[str]:
    return [
        v["id"],
        SIGNAL_ICON.get(v["signal"], "") + " " + v["signal"],
        (v.get("severity") or "-"),
        v.get("status", "open"),
        str(v["count"]),
        ",".join(v["platforms"]) or "-",
        v.get("owner") or "-",
        ",".join(v.get("jira") or []) or "-",
        (v.get("title") or "")[:58],
    ]


BRIEF_HEADERS = ["id", "signal", "sev", "status", "n", "platforms", "owner",
                 "jira", "title"]


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def read_evidence(args) -> str:
    parts = []
    if getattr(args, "text", None):
        parts.append(args.text)
    if getattr(args, "file", None):
        if args.file == "-":
            parts.append(sys.stdin.read())
        else:
            parts.append(Path(os.path.expanduser(args.file)).read_text(errors="replace"))
    return "\n".join(parts).strip()


def cmd_fingerprint(args) -> None:
    text = read_evidence(args)
    if not text:
        sys.exit("error: provide evidence with --text or --file")
    print(f"fingerprint : {fingerprint(text)}")
    print(f"tokens      : {' '.join(tokenize(text)[:40])}")
    print(f"normalized  : {normalize(text)[:600]}")


def cmd_match(args) -> None:
    store = load()
    text = read_evidence(args)
    if not text:
        sys.exit("error: provide evidence with --text or --file")
    hits = rank(store, text, include_closed=args.include_closed, limit=args.limit)
    cfg = store["config"]
    hits = [h for h in hits if h[0] >= cfg["match_threshold"]]
    if args.json:
        print(json.dumps([{"score": s, "id": p["id"], "title": p.get("title"),
                           "why": w} for s, p, w in hits], indent=2))
        return
    print(f"fingerprint: {fingerprint(text)}")
    if not hits:
        print("\nNo candidate above threshold — this looks like a NEW phenomenon.")
        print('Register it:  ph.py sight --new "<title>" --platform <p> --source <s> ...')
        return
    rows = [[f"{s:.2f}", p["id"], w, p.get("status", "open"),
             str(len(p.get("sightings", []))), (p.get("title") or "")[:52]]
            for s, p, w in hits]
    print("\n" + table(rows, ["score", "id", "matched on", "status", "n", "title"]))
    top = hits[0]
    if top[0] >= cfg["strong_threshold"]:
        print(f"\nStrong match → likely the same phenomenon as {top[1]['id']}.")
    else:
        print("\nWeak/partial match — confirm against the Correlate & Classify axes "
              "before attaching.")


def cmd_sight(args) -> None:
    store = load()
    cfg = store["config"]
    text = read_evidence(args)
    if not text:
        sys.exit("error: provide evidence with --text or --file")

    target = None
    if args.to:
        target = find(store, args.to)
    elif args.new:
        target = _new_phenomenon(store, args.new, text, args)
    else:
        hits = [h for h in rank(store, text, limit=5) if h[0] >= cfg["match_threshold"]]
        if args.auto:
            strong = [h for h in hits if h[0] >= cfg["strong_threshold"]]
            # Two strong, near-equal candidates is the dangerous case: attaching
            # to the wrong one buries a distinct bug inside another's history.
            tie = len(strong) > 1 and (strong[0][0] - strong[1][0]) < 0.08
            if strong and not tie:
                target = strong[0][1]
                print(f"auto-attached to {target['id']} (score {strong[0][0]:.2f})")
            elif hits:
                rows = [[f"{s:.2f}", p["id"], w, p.get("status", "open"),
                         (p.get("title") or "")[:52]] for s, p, w in hits]
                print(table(rows, ["score", "id", "matched on", "status", "title"]))
                reason = ("Two candidates match about equally well"
                          if tie else
                          "Candidates exist but none is a strong match")
                print(f"\nAmbiguous — {reason}. Nothing written.\n"
                      "Decide explicitly: --to <PH-id> (same "
                      'phenomenon) or --new "<title>" (genuinely distinct).')
                sys.exit(2)
            else:
                title = args.title or text.strip().splitlines()[0][:80]
                target = _new_phenomenon(store, title, text, args)
                print(f"auto-created {target['id']}")
        else:
            print(f"fingerprint: {fingerprint(text)}\n")
            if hits:
                rows = [[f"{s:.2f}", p["id"], w, p.get("status", "open"),
                         (p.get("title") or "")[:52]] for s, p, w in hits]
                print(table(rows, ["score", "id", "matched on", "status", "title"]))
                print("\nNothing written. Attach with  --to <PH-id>, or start a new "
                      'phenomenon with  --new "<title>"  (or --auto to decide '
                      "mechanically).")
            else:
                print("No candidate above threshold — nothing written.")
                print('Start a new phenomenon:  --new "<title>"')
            return

    sid = f"S-{store['next_sighting']:04d}"
    store["next_sighting"] += 1
    sighting = {
        "id": sid,
        "at": args.at or today(),
        "platform": args.platform,
        "source": args.source,
        "reporter": args.reporter,
        "where": args.where,
        "fw": args.fw,
        "image": args.image,
        "manifest": args.manifest,
        "run": args.run,
        "note": args.note,
        "fp": fingerprint(text),
        "raw": text[:8000],
        "logged": today(),
    }
    if target.get("status") in ("fixed", "closed", "monitoring"):
        sighting["after_fix"] = True
    elif at_or_after_fix(args.fw, (target.get("fix") or {}).get("version")):
        sighting["after_fix"] = True
    target.setdefault("sightings", []).append(sighting)
    target["updated"] = today()
    if args.jira:
        for k in args.jira:
            if k not in target.setdefault("jira", []):
                target["jira"].append(k)
    save(store)
    v = view(target, cfg)
    print(f"\n{sid} recorded on {target['id']} — {target.get('title')}")
    print(f"  platforms : {', '.join(v['platforms'])}")
    print(f"  sightings : {v['count']}   signal: {v['signal']}   "
          f"flags: {', '.join(v['flags']) or '-'}")
    if "escaped" in v["flags"]:
        print("  ⚠ seen in PRODUCTION — escalate regardless of count.")
    if "regressed" in v["flags"]:
        print("  ⚠ sighting recorded AFTER the fix was declared — the fix is "
              "incomplete, not deployed, or this is a second root cause.")
    if "cross-platform" in v["flags"]:
        print("  ⚠ now seen on more than one platform — rule out a platform-local "
              "confound before merging the evidence.")


def _new_phenomenon(store: dict[str, Any], title: str, text: str,
                    args) -> dict[str, Any]:
    pid = f"PH-{store['next_id']:03d}"
    store["next_id"] += 1
    ph = {
        "id": pid,
        "title": title,
        "status": "open",
        "severity": getattr(args, "severity", None),
        "owner": getattr(args, "owner", None),
        "component": getattr(args, "component", None),
        "signatures": [{"fp": fingerprint(text), "text": text[:4000],
                        "added": today(), "label": "initial"}],
        "root_cause": None,
        "fix": {},
        "jira": list(getattr(args, "jira", None) or []),
        "investigations": [],
        "sightings": [],
        "tags": [],
        "notes": [],
        "created": today(),
        "updated": today(),
    }
    store["phenomena"].append(ph)
    return ph


def cmd_new(args) -> None:
    store = load()
    text = read_evidence(args) or args.title
    ph = _new_phenomenon(store, args.title, text, args)
    save(store)
    print(f"{ph['id']} created — {ph['title']}")
    print(f"  fingerprint: {ph['signatures'][0]['fp']}")
    print("  Record the first sighting:  ph.py sight --to "
          f"{ph['id']} --platform <p> --source <s> --text '...'")


def cmd_list(args) -> None:
    store = load()
    cfg = store["config"]
    views = [view(p, cfg) for p in store["phenomena"]]
    if args.status != "merged":
        views = [v for v in views if v["status"] != "merged"]
    if args.open:
        views = [v for v in views if v["status"] not in
                 ("closed", "fixed", "not-a-bug", "not-ours", "merged")]
    if args.status:
        views = [v for v in views if v["status"] == args.status]
    if args.platform:
        views = [v for v in views if args.platform in v["platforms"]]
    if args.severity:
        views = [v for v in views if v.get("severity") == args.severity]
    if args.owner:
        views = [v for v in views if (v.get("owner") or "").lower() ==
                 args.owner.lower()]
    if args.flag:
        views = [v for v in views if args.flag in v["flags"]]
    if args.untracked:
        views = [v for v in views if "no-jira" in v["flags"]]
    if args.since:
        views = [v for v in views if (v["last_seen"] or "") >= args.since]
    order = {"hot": 0, "active": 1, "slow": 2, "quiet": 3, "unknown": 4,
             "fixed": 5, "closed": 6}
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}
    views.sort(key=lambda v: (order.get(v["signal"], 9),
                              sev.get(v.get("severity"), 4), -v["count"]))
    if args.json:
        print(json.dumps(views, indent=2))
        return
    print(table([brief(v) for v in views], BRIEF_HEADERS))
    print(f"\n  {len(views)} phenomena")


def cmd_show(args) -> None:
    store = load()
    ph = find(store, args.id)
    v = view(ph, store["config"])
    if args.json:
        print(json.dumps(v, indent=2))
        return
    print(f"{v['id']}  {v.get('title')}")
    print(f"  status     : {v.get('status')}   severity: {v.get('severity') or '-'}"
          f"   owner: {v.get('owner') or '-'}   component: {v.get('component') or '-'}")
    print(f"  signal     : {SIGNAL_ICON.get(v['signal'],'')} {v['signal']}"
          f"   sightings: {v['count']}   idle: {v['idle_days']}d")
    print(f"  seen       : {v['first_seen']} → {v['last_seen']}")
    print(f"  platforms  : {', '.join(v['platforms']) or '-'}")
    print(f"  sources    : {', '.join(v['sources']) or '-'}")
    print(f"  flags      : {', '.join(v['flags']) or '-'}")
    print(f"  jira       : {', '.join(v.get('jira') or []) or '(none — ticketless)'}")
    if v.get("tags"):
        print(f"  tags       : {', '.join(v['tags'])}")
    if v.get("root_cause"):
        print(f"\n  root cause : {v['root_cause']}")
    if v.get("fix"):
        fx = {k: val for k, val in v["fix"].items() if val}
        if fx:
            print("  fix        : " + "  ".join(f"{k}={val}" for k, val in fx.items()))
        if fx.get("version"):
            print(f"               → a sighting on {fx['version']} or later is a "
                  "REGRESSION, not a reproduction.")
    if v.get("investigations"):
        print("  notes      : " + "\n               ".join(v["investigations"]))
    print("\n  Signatures")
    print(table([[s.get("fp", ""), s.get("label", ""), s.get("added", ""),
                  normalize(s.get("text", ""))[:60]]
                 for s in v.get("signatures", [])],
                ["fp", "label", "added", "normalized (head)"]))
    def blurb(s: dict[str, Any]) -> str:
        if s.get("note"):
            return s["note"]
        head = (s.get("raw") or "").strip().splitlines()
        return head[0] if head else "-"

    print("\n  Sightings   (! = recorded on a build that already carries the fix)")
    print(table([[("! " if s.get("after_fix") else "  ") + s.get("id", ""),
                  s.get("at", ""), s.get("platform", ""),
                  s.get("source", ""), (s.get("where") or "-")[:18],
                  (s.get("fw") or s.get("manifest") or "-")[:22],
                  (s.get("reporter") or "-")[:14], blurb(s)[:40]]
                 for s in v.get("sightings", [])],
                ["id", "at", "platform", "source", "where", "fw/manifest",
                 "reporter", "note"]))
    if v.get("notes"):
        print("\n  Log")
        for n in v["notes"]:
            print(f"    - {n}")


def cmd_set(args) -> None:
    store = load()
    ph = find(store, args.id)
    changed = []
    for field in ("title", "status", "severity", "owner", "component",
                  "root_cause"):
        val = getattr(args, field.replace("-", "_"), None)
        if val is not None:
            ph[field] = val
            changed.append(f"{field}={val}")
    if args.tag:
        for t in args.tag:
            if t not in ph.setdefault("tags", []):
                ph["tags"].append(t)
                changed.append(f"+tag:{t}")
    if args.status == "fixed" and not (ph.get("fix") or {}).get("at"):
        ph.setdefault("fix", {})["at"] = today()
    if not changed:
        sys.exit("error: nothing to set")
    ph["updated"] = today()
    ph.setdefault("notes", []).append(f"{today()}: set {', '.join(changed)}")
    save(store)
    print(f"{ph['id']} updated: {', '.join(changed)}")


def cmd_link(args) -> None:
    store = load()
    ph = find(store, args.id)
    added = []
    for key in args.jira or []:
        if key not in ph.setdefault("jira", []):
            ph["jira"].append(key)
            added.append(f"jira:{key}")
    for path in args.investigation or []:
        if path not in ph.setdefault("investigations", []):
            ph["investigations"].append(path)
            added.append(f"note:{path}")
    fix = ph.setdefault("fix", {})
    for field in ("pr", "commit", "version", "repo"):
        val = getattr(args, field, None)
        if val:
            fix[field] = val
            added.append(f"fix.{field}={val}")
    if args.signature or args.signature_file:
        text = args.signature or Path(
            os.path.expanduser(args.signature_file)).read_text(errors="replace")
        ph.setdefault("signatures", []).append(
            {"fp": fingerprint(text), "text": text[:4000], "added": today(),
             "label": args.label or "variant"})
        added.append(f"signature:{fingerprint(text)}")
    if not added:
        sys.exit("error: nothing to link")
    if args.version:
        late = [s["id"] for s in ph.get("sightings", [])
                if at_or_after_fix(s.get("fw"), args.version)
                and not s.get("after_fix")]
        for s in ph.get("sightings", []):
            if s["id"] in late:
                s["after_fix"] = True
        if late:
            added.append(f"re-classified {', '.join(late)} as after-fix")
    ph["updated"] = today()
    save(store)
    print(f"{ph['id']} linked: {', '.join(added)}")


def cmd_unlink(args) -> None:
    store = load()
    ph = find(store, args.id)
    gone = []
    for key in args.jira or []:
        key = key.strip().upper()
        if key in ph.get("jira", []):
            ph["jira"].remove(key)
            gone.append(f"jira:{key}")
    names = {bugdb.note_name(p): p for p in ph.get("investigations", [])}
    for path in args.investigation or []:
        n = bugdb.note_name(path)
        if n in names:
            ph["investigations"].remove(names[n])
            gone.append(f"note:{n}")
    if not gone:
        sys.exit("error: nothing to unlink")
    ph["updated"] = today()
    ph.setdefault("notes", []).append(f"{today()}: unlinked {', '.join(gone)}")
    save(store)
    print(f"{ph['id']} unlinked: {', '.join(gone)}")


def cmd_export(args) -> None:
    store = load()
    text = json.dumps(dict(store), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(os.path.expanduser(args.output)).write_text(text)
        print(f"exported {len(store['phenomena'])} phenomena → {args.output}")
    else:
        sys.stdout.write(text)


def cmd_note(args) -> None:
    store = load()
    ph = find(store, args.id)
    ph.setdefault("notes", []).append(f"{today()}: {args.text}")
    ph["updated"] = today()
    save(store)
    print(f"{ph['id']} note added")


def cmd_merge(args) -> None:
    store = load()
    dst = find(store, args.into)
    moved = 0
    for sid in args.ids:
        src = find(store, sid)
        if src["id"] == dst["id"]:
            continue
        for s in src.get("sightings", []):
            dst.setdefault("sightings", []).append(s)
            moved += 1
        for sig in src.get("signatures", []):
            if sig.get("fp") not in {x.get("fp") for x in dst.get("signatures", [])}:
                sig = dict(sig)
                sig["label"] = f"from {src['id']}"
                dst.setdefault("signatures", []).append(sig)
        for k in src.get("jira", []):
            if k not in dst.setdefault("jira", []):
                dst["jira"].append(k)
        for p in src.get("investigations", []):
            if p not in dst.setdefault("investigations", []):
                dst["investigations"].append(p)
        src["sightings"] = []
        src["status"] = "merged"
        src["merged_into"] = dst["id"]
        src["updated"] = today()
        src.setdefault("notes", []).append(f"{today()}: merged into {dst['id']}")
        dst.setdefault("notes", []).append(f"{today()}: absorbed {src['id']}")
    dst["updated"] = today()
    save(store)
    v = view(dst, store["config"])
    print(f"merged {', '.join(args.ids)} → {dst['id']} ({moved} sightings moved)")
    print(f"  {dst['id']} now: {v['count']} sightings across "
          f"{', '.join(v['platforms'])}")


def cmd_move(args) -> None:
    store = load()
    dst = find(store, args.to)
    sid = args.sighting.upper()
    for ph in store["phenomena"]:
        for s in list(ph.get("sightings", [])):
            if s.get("id") == sid:
                ph["sightings"].remove(s)
                dst.setdefault("sightings", []).append(s)
                ph["updated"] = dst["updated"] = today()
                ph.setdefault("notes", []).append(
                    f"{today()}: {sid} re-homed to {dst['id']}")
                dst.setdefault("notes", []).append(
                    f"{today()}: {sid} re-homed from {ph['id']}")
                save(store)
                print(f"{sid}: {ph['id']} → {dst['id']}")
                return
    sys.exit(f"error: no such sighting: {sid}")


def cmd_dups(args) -> None:
    store = load()
    cfg = store["config"]
    live = [p for p in store["phenomena"] if p.get("status") != "merged"]
    pairs = []
    for i, a in enumerate(live):
        a_text = "\n".join([a.get("title", "")] +
                           [s.get("text", "") for s in a.get("signatures", [])])
        for b in live[i + 1:]:
            b_text = "\n".join([b.get("title", "")] +
                               [s.get("text", "") for s in b.get("signatures", [])])
            sc = similarity(a_text, b_text)
            if sc >= (args.threshold or cfg["match_threshold"]):
                pairs.append((sc, a, b))
    pairs.sort(key=lambda t: -t[0])
    if args.json:
        print(json.dumps([{"score": s, "a": a["id"], "b": b["id"]}
                          for s, a, b in pairs], indent=2))
        return
    if not pairs:
        print("No merge candidates above threshold.")
        return
    rows = [[f"{s:.2f}", a["id"], b["id"],
             ",".join(derived(a, cfg)["platforms"]),
             ",".join(derived(b, cfg)["platforms"]),
             (a.get("title") or "")[:34], (b.get("title") or "")[:34]]
            for s, a, b in pairs]
    print(table(rows, ["score", "a", "b", "a-platforms", "b-platforms",
                       "a-title", "b-title"]))
    print("\nProposals only. Confirm against the Correlate & Classify axes "
          "(component, failure stage, FW version) before  ph.py merge.")


def cmd_pulse(args) -> None:
    store = load()
    cfg = store["config"]
    views = [view(p, cfg) for p in store["phenomena"]
             if p.get("status") != "merged"]
    live = [v for v in views if v["signal"] not in ("closed",)]
    if args.json:
        print(json.dumps(views, indent=2))
        return

    def section(title: str, sel) -> None:
        rows = [brief(v) for v in live if sel(v)]
        if rows:
            print(f"\n{title}")
            print(table(rows, BRIEF_HEADERS))

    print(f"Phenomenon pulse — {today()}   "
          f"({len(live)} live / {len(views)} total)")
    section("⚠ ESCAPED TO PRODUCTION", lambda v: "escaped" in v["flags"])
    section("⚠ REGRESSED AFTER FIX", lambda v: "regressed" in v["flags"])
    section("🔥 HOT (seen in the last few days)", lambda v: v["signal"] == "hot")
    section("🔁 RECURRING, STILL UNEXPLAINED",
            lambda v: "unexplained-recurring" in v["flags"] and v["signal"] != "hot")
    section("🧭 CROSS-PLATFORM",
            lambda v: "cross-platform" in v["flags"] and v["signal"] in ("active", "slow"))
    section("🎫 UNTRACKED IN JIRA (ticketless)",
            lambda v: "no-jira" in v["flags"] and v.get("severity") in
            ("critical", "high"))
    section("🟠 QUIET — close it or chase it", lambda v: v["signal"] == "quiet")

    # Platform matrix.
    print("\nPlatform spread (live phenomena)")
    rows = []
    for p in ["sim", "fpga", "palladium", "ci", "silicon-lab", "production"]:
        hits = [v for v in live if p in v["platforms"]]
        if hits:
            rows.append([p, str(len(hits)),
                         ", ".join(v["id"] for v in hits[:10])])
    print(table(rows, ["platform", "n", "phenomena"]))


def cmd_report(args) -> None:
    store = load()
    cfg = store["config"]
    cutoff = (date.today() - timedelta(days=args.window)).isoformat()
    views = [view(p, cfg) for p in store["phenomena"]
             if p.get("status") != "merged"]
    recent = [v for v in views if (v["last_seen"] or "") >= cutoff]
    newp = [v for v in views if (v.get("created") or "") >= cutoff]
    live = [v for v in views if v["signal"] != "closed"]

    out = [f"# Phenomenon report — last {args.window} days ({today()})", "",
           f"- live phenomena: **{len(live)}**",
           f"- new this window: **{len(newp)}**",
           f"- sighted this window: **{len(recent)}**",
           f"- escaped to production: **{len([v for v in live if 'escaped' in v['flags']])}**",
           f"- untracked in Jira: **{len([v for v in live if 'no-jira' in v['flags']])}**",
           ""]

    def md(title: str, sel) -> None:
        rows = [v for v in views if sel(v)]
        if not rows:
            return
        out.append(f"## {title}")
        out.append("| id | sev | status | n | platforms | last seen | jira | title |")
        out.append("|---|---|---|---|---|---|---|---|")
        for v in rows:
            out.append(f"| {v['id']} | {v.get('severity') or '-'} | {v['status']} "
                       f"| {v['count']} | {','.join(v['platforms'])} "
                       f"| {v['last_seen'] or '-'} "
                       f"| {','.join(v.get('jira') or []) or '-'} "
                       f"| {v.get('title','')} |")
        out.append("")

    md("Escaped / regressed", lambda v: {"escaped", "regressed"} & set(v["flags"])
       and v["signal"] != "closed")
    md("New this window", lambda v: (v.get("created") or "") >= cutoff)
    md("Recurring, unexplained",
       lambda v: "unexplained-recurring" in v["flags"] and v["signal"] != "closed")
    md("Sighted this window", lambda v: (v["last_seen"] or "") >= cutoff)
    print("\n".join(out))


def cmd_promote(args) -> None:
    """Draft (never file) a Jira ticket for a ticketless phenomenon."""
    store = load()
    ph = find(store, args.id)
    v = view(ph, store["config"])
    sightings = v.get("sightings", [])
    project = args.project or "<choose: IPPCIESW / IPSESW / IPD2DSW / PRJIRDMSW>"
    seen = (f"{v['count']} sighting(s) between {v['first_seen']} and "
            f"{v['last_seen']} on: {', '.join(v['platforms'])}."
            if sightings else "No sightings recorded yet — add them before filing.")
    lines = [
        f"SUMMARY: {v.get('title')}",
        "",
        f"PROJECT (suggested): {project}",
        f"COMPONENT: {v.get('component') or '<set>'}",
        f"PRIORITY (suggested): {v.get('severity') or '<set>'}",
        "",
        "DESCRIPTION",
        "-----------",
        f"Tracked locally as {v['id']} since {v.get('created')}.",
        seen,
        "",
        "Signature (run-invariant):",
        "  " + (normalize(v["signatures"][0]["text"])[:400] if v.get("signatures")
                else "(none)"),
        "",
        "Sightings:",
    ]
    for s in sightings:
        lines.append(f"  - {s.get('at')} | {s.get('platform')} | via {s.get('source')}"
                     f" | {s.get('where') or '-'} | fw/manifest: "
                     f"{s.get('fw') or s.get('manifest') or '-'}"
                     f" | reported by {s.get('reporter') or '-'}"
                     + (f" | {s.get('run')}" if s.get("run") else ""))
    lines += ["", f"Root cause: {v.get('root_cause') or '(not yet established)'}"]
    if v.get("investigations"):
        lines += ["", "Investigation notes:"] + \
                 [f"  - {p}" for p in v["investigations"]]
    if v.get("jira"):
        lines += ["", f"Existing related tickets: {', '.join(v['jira'])}"]
    lines += ["", "-- DRAFT ONLY. Nothing has been filed. Approve before creating. --"]
    print("\n".join(lines))


def cmd_config(args) -> None:
    store = load()
    if args.set:
        for kv in args.set:
            k, _, val = kv.partition("=")
            if k not in DEFAULT_CONFIG:
                sys.exit(f"error: unknown config key: {k}")
            try:
                store["config"][k] = float(val) if "." in val else int(val)
            except ValueError:
                store["config"][k] = val
        save(store)
    print(json.dumps(store["config"], indent=2))
    print(f"\nstore: {STORE}")


# ----------------------------------------------------------------------------
# Portal API (task-manager/tm.py) — same database, same rules as the CLI
# ----------------------------------------------------------------------------

JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}-\d{1,7}$")
EDITABLE = ("title", "status", "severity", "owner", "component", "root_cause")
FIX_FIELDS = ("version", "pr", "commit", "repo")


def _as_list(val: Any) -> list[str]:
    if not val:
        return []
    if isinstance(val, str):
        val = re.split(r"[,\s]+", val)
    return [str(v).strip() for v in val if str(v).strip()]


def apply_ph_edit(pid: str, payload: dict[str, Any],
                  who: str = "portal") -> tuple[bool, str]:
    """Apply one portal edit to a phenomenon. Mirrors set/link/unlink/note."""
    with bugdb.tx(who):
        store = load()
        ph = next((p for p in store["phenomena"] if p["id"] == (pid or "").upper()), None)
        if ph is None:
            return False, f"unknown phenomenon {pid}"
        changed: list[str] = []
        for field in EDITABLE:
            if field not in payload:
                continue
            val = payload[field]
            val = val.strip() if isinstance(val, str) else val
            if field == "title" and not val:
                return False, "title cannot be empty"
            if field == "status" and val not in STATUSES:
                return False, f"invalid status {val!r}"
            if field == "severity" and val and val not in SEVERITIES:
                return False, f"invalid severity {val!r}"
            val = val or None
            if ph.get(field) != val:
                ph[field] = val
                changed.append(f"{field}={val if field != 'root_cause' else '…'}")
        if ph.get("status") == "fixed" and not (ph.get("fix") or {}).get("at"):
            ph.setdefault("fix", {})["at"] = today()
        fix = ph.setdefault("fix", {})
        for field in FIX_FIELDS:
            key = f"fix_{field}"
            if key not in payload:
                continue
            val = (payload[key] or "").strip() or None
            if fix.get(field) != val:
                if val:
                    fix[field] = val
                else:
                    fix.pop(field, None)
                changed.append(f"fix.{field}={val or '—'}")
                if field == "version" and val:
                    late = [s for s in ph.get("sightings", [])
                            if at_or_after_fix(s.get("fw"), val) and not s.get("after_fix")]
                    for s in late:
                        s["after_fix"] = True
                    if late:
                        changed.append("re-classified " + ", ".join(s["id"] for s in late)
                                       + " as after-fix")
        for t in _as_list(payload.get("tag_add")):
            if t not in ph.setdefault("tags", []):
                ph["tags"].append(t)
                changed.append(f"+tag:{t}")
        for t in _as_list(payload.get("tag_remove")):
            if t in ph.get("tags", []):
                ph["tags"].remove(t)
                changed.append(f"-tag:{t}")
        for key in _as_list(payload.get("link_jira")):
            key = key.upper()
            if not JIRA_KEY_RE.match(key):
                return False, f"not a Jira key: {key}"
            if key not in ph.setdefault("jira", []):
                ph["jira"].append(key)
                changed.append(f"+jira:{key}")
        for key in _as_list(payload.get("unlink_jira")):
            key = key.upper()
            if key in ph.get("jira", []):
                ph["jira"].remove(key)
                changed.append(f"-jira:{key}")
        for name in _as_list(payload.get("link_note")):
            if bugdb.read_investigation(name) is None:
                return False, f"no such investigation note: {name}"
            if name not in {bugdb.note_name(p) for p in ph.setdefault("investigations", [])}:
                ph["investigations"].append(bugdb.note_path(name))
                changed.append(f"+note:{name}")
        for name in _as_list(payload.get("unlink_note")):
            for p in list(ph.get("investigations", [])):
                if bugdb.note_name(p) == name:
                    ph["investigations"].remove(p)
                    changed.append(f"-note:{name}")
        note = (payload.get("note") or "").strip()
        if not changed and not note:
            return False, "nothing to change"
        ph["updated"] = today()
        if changed:
            ph.setdefault("notes", []).append(f"{today()}: {who}: {', '.join(changed)}")
        if note:
            ph.setdefault("notes", []).append(f"{today()}: {note}")
            changed.append("note")
        save(store)
        return True, f"{ph['id']}: " + ", ".join(changed)


def portal_payload() -> dict[str, Any]:
    """Everything the portal's Phenomena tab and per-bug triage block need."""
    store = load()
    cfg = store["config"]
    views = []
    for p in store["phenomena"]:
        v = view(p, cfg)
        v["sightings"] = [dict(s, raw=(s.get("raw") or "")[:1500])
                          for s in v.get("sightings", [])]
        v["signatures"] = [dict(sg, text=(sg.get("text") or "")[:600])
                           for sg in v.get("signatures", [])]
        views.append(v)
    return {
        "phenomena": views,
        "evidence": bugdb.evidence(),
        "investigations": bugdb.investigation_index(),
        "statuses": STATUSES, "severities": SEVERITIES, "platforms": PLATFORMS,
        "signal_icon": SIGNAL_ICON,
        "db": str(bugdb.DB_PATH),
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ph.py", description="bug-triage phenomenon registry "
                                  "(ticketless, cross-platform)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def evidence(sp):
        sp.add_argument("--text", "-t", help="evidence text (paste the symptom)")
        sp.add_argument("--file", "-f", help="read evidence from a file ('-' = stdin)")

    sp = sub.add_parser("fingerprint", help="normalize evidence and show its fingerprint")
    evidence(sp)
    sp.set_defaults(func=cmd_fingerprint)

    sp = sub.add_parser("match", help="rank the registry against evidence (no write)")
    evidence(sp)
    sp.add_argument("--include-closed", action="store_true")
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_match)

    sp = sub.add_parser("sight", help="record a sighting (matches first; writes only "
                                      "with --to/--new/--auto)")
    evidence(sp)
    sp.add_argument("--to", help="attach to this phenomenon id")
    sp.add_argument("--new", metavar="TITLE", help="start a new phenomenon")
    sp.add_argument("--auto", action="store_true",
                    help="attach on a strong match, else create a new phenomenon")
    sp.add_argument("--title", help="title to use with --auto when creating")
    sp.add_argument("--platform", choices=PLATFORMS, default="unknown")
    sp.add_argument("--source", choices=SOURCES, default="other")
    sp.add_argument("--reporter", help="who reported it (person / customer)")
    sp.add_argument("--where", help="station / host / board / OAM / tray")
    sp.add_argument("--fw", help="firmware version or build id")
    sp.add_argument("--image", help="image / FC (RTL) tag")
    sp.add_argument("--manifest", help="manifest version")
    sp.add_argument("--run", help="CI run URL or lab session id")
    sp.add_argument("--at", help="date of the observation (YYYY-MM-DD)")
    sp.add_argument("--note", help="one-line human context")
    sp.add_argument("--jira", nargs="*", help="related Jira key(s), if any")
    sp.add_argument("--severity", choices=SEVERITIES)
    sp.add_argument("--owner")
    sp.add_argument("--component")
    sp.set_defaults(func=cmd_sight)

    sp = sub.add_parser("new", help="create a phenomenon explicitly")
    sp.add_argument("title")
    evidence(sp)
    sp.add_argument("--severity", choices=SEVERITIES)
    sp.add_argument("--owner")
    sp.add_argument("--component")
    sp.add_argument("--jira", nargs="*")
    sp.set_defaults(func=cmd_new)

    sp = sub.add_parser("list", help="the working list")
    sp.add_argument("--open", action="store_true")
    sp.add_argument("--status", choices=STATUSES)
    sp.add_argument("--platform", choices=PLATFORMS)
    sp.add_argument("--severity", choices=SEVERITIES)
    sp.add_argument("--owner")
    sp.add_argument("--flag", help="e.g. escaped, regressed, cross-platform, no-jira")
    sp.add_argument("--untracked", action="store_true", help="no Jira ticket")
    sp.add_argument("--since", help="last seen on/after YYYY-MM-DD")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="everything about one phenomenon")
    sp.add_argument("id")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("set", help="update fields")
    sp.add_argument("id")
    sp.add_argument("--title")
    sp.add_argument("--status", choices=STATUSES)
    sp.add_argument("--severity", choices=SEVERITIES)
    sp.add_argument("--owner")
    sp.add_argument("--component")
    sp.add_argument("--root-cause", dest="root_cause")
    sp.add_argument("--tag", nargs="*")
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("link", help="attach a Jira key, PR/commit, note or signature")
    sp.add_argument("id")
    sp.add_argument("--jira", nargs="*")
    sp.add_argument("--investigation", nargs="*")
    sp.add_argument("--pr")
    sp.add_argument("--commit")
    sp.add_argument("--repo")
    sp.add_argument("--version", help="version/manifest the fix first ships in")
    sp.add_argument("--signature", help="add a variant signature (text)")
    sp.add_argument("--signature-file")
    sp.add_argument("--label", help="label for the added signature")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("unlink", help="detach a Jira key or an investigation note")
    sp.add_argument("id")
    sp.add_argument("--jira", nargs="*")
    sp.add_argument("--investigation", nargs="*")
    sp.set_defaults(func=cmd_unlink)

    sp = sub.add_parser("export", help="dump the registry as phenomena.json-shaped JSON")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("note", help="append a note")
    sp.add_argument("id")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_note)

    sp = sub.add_parser("merge", help="consolidate phenomena into one")
    sp.add_argument("ids", nargs="+")
    sp.add_argument("--into", required=True)
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("move", help="re-home a misfiled sighting")
    sp.add_argument("sighting")
    sp.add_argument("--to", required=True)
    sp.set_defaults(func=cmd_move)

    sp = sub.add_parser("dups", help="propose phenomena that should be merged")
    sp.add_argument("--threshold", type=float)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_dups)

    sp = sub.add_parser("pulse", help="cross-platform monitoring view")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_pulse)

    sp = sub.add_parser("report", help="Markdown digest")
    sp.add_argument("--window", type=int, default=7)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("promote", help="DRAFT a Jira ticket from a phenomenon "
                                        "(never files it)")
    sp.add_argument("id")
    sp.add_argument("--project")
    sp.set_defaults(func=cmd_promote)

    sp = sub.add_parser("config", help="show/tune thresholds")
    sp.add_argument("--set", nargs="*")
    sp.set_defaults(func=cmd_config)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # One write lock per command: a portal edit can never interleave with it.
    with bugdb.tx("cli"):
        args.func(args)


if __name__ == "__main__":
    main()
