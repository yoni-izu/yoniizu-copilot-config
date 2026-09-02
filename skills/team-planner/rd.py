#!/usr/bin/env python3
"""RAID log reader/writer for ~/team-planner/raid.md.

The file stays the source of truth and stays hand-editable; this only rewrites
the specific field lines it is asked to change.

    rd.py list                      # open rows, worst first
    rd.py show R-001
    rd.py set R-001 --rag red --status "Open" --note "..."
    rd.py new --type risk --title "..." --owner Yoni --p 3 --i 4
    rd.py close R-001 --resolution "PRs merged ahead of trigger"
    rd.py payload                   # JSON for the Team Portal
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

RAID = Path(os.path.expanduser("~/team-planner/raid.md"))
TODAY = date.today().isoformat()

TYPES = {"R": "Risk", "A": "Assumption", "I": "Issue", "D": "Dependency"}
PREFIX = {v.lower(): k for k, v in TYPES.items()}
RAGS = ["green", "yellow", "orange", "red"]

# Order matters: `new` emits blocks in exactly this shape.
FIELDS = ["Title", "Description", "FW Owner", "External Owner",
          "Trigger", "Mitigation", "Contingency", "Due", "Status", "Link"]
EDITABLE = FIELDS + ["P", "I", "RAG", "Type"]

HEAD_RE = re.compile(r"^###\s+([A-Z]-\d+)\s*\|\s*(\w+)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*([^:*]+):\*\*\s*(.*)$")
SCORE_RE = re.compile(r"\*\*P:\*\*\s*(\d+)\s*\|\s*\*\*I:\*\*\s*(\d+)"
                      r"\s*\|\s*\*\*Score:\*\*\s*(\d+)\s*\|\s*\*\*RAG:\*\*\s*(\w+)")


def rag_for(score: int) -> str:
    if score >= 15:
        return "Red"
    if score >= 8:
        return "Orange"
    if score >= 4:
        return "Yellow"
    return "Green"


def _plain(s: str) -> str:
    """Strip markdown links and emphasis so the portal shows readable text."""
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    return re.sub(r"\*\*|__|`", "", s).strip()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse(text: str | None = None) -> list[dict]:
    if text is None:
        text = RAID.read_text(encoding="utf-8", errors="replace") if RAID.exists() else ""
    lines = text.splitlines()
    rows, cur = [], None

    for i, line in enumerate(lines):
        m = HEAD_RE.match(line)
        if m:
            cur = {"id": m.group(1), "type": m.group(2), "start": i, "end": len(lines),
                   "fields": {}, "p": 0, "i": 0, "score": 0, "rag": ""}
            rows.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("### ") or line.startswith("## "):
            cur["end"] = i
            cur = None
            continue
        sm = SCORE_RE.search(line)
        if sm:
            cur["p"], cur["i"] = int(sm.group(1)), int(sm.group(2))
            cur["score"], cur["rag"] = int(sm.group(3)), sm.group(4).lower()
            cur["score_line"] = i
            continue
        fm = FIELD_RE.match(line)
        if fm:
            cur["fields"].setdefault(fm.group(1).strip(), {"line": i, "value": fm.group(2).strip()})

    for r in rows:
        f = r.pop("fields")
        r["lines"] = {k: v["line"] for k, v in f.items()}
        status = f.get("Status", {}).get("value", "")
        r.update({
            "title": _plain(f.get("Title", {}).get("value", "")),
            # *_raw feed the portal's editor; the stripped values are for display
            # only and would silently drop markdown if written back.
            "title_raw": f.get("Title", {}).get("value", ""),
            "due_raw": f.get("Due", {}).get("value", ""),
            "description": _plain(f.get("Description", {}).get("value", ""))[:400],
            "owner": _plain(f.get("FW Owner", {}).get("value", "")),
            "external": _plain(f.get("External Owner", {}).get("value", "")),
            "trigger": _plain(f.get("Trigger", {}).get("value", ""))[:300],
            "mitigation": _plain(f.get("Mitigation", {}).get("value", ""))[:300],
            "contingency": _plain(f.get("Contingency", {}).get("value", ""))[:300],
            "due": _plain(f.get("Due", {}).get("value", ""))[:10],
            "status": _plain(status)[:300],
            "link": _plain(f.get("Link", {}).get("value", ""))[:300],
            "closed": _plain(status).lower().startswith("closed"),
        })
    return rows


def _field_span(lines: list[str], start: int) -> int:
    """Where a `- **Field:**` value ends — continuation lines are indented."""
    j = start + 1
    while j < len(lines):
        s = lines[j]
        if s.startswith("### ") or s.startswith("## ") or FIELD_RE.match(s):
            break
        if s.strip() and not s.startswith((" ", "\t")):
            break
        j += 1
    return j


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def apply_edit(rid: str, payload: dict) -> tuple[bool, str]:
    """Rewrite one row's fields. Untouched fields keep their exact text."""
    if not RAID.exists():
        return False, "raid.md not found"
    text = RAID.read_text(encoding="utf-8")
    lines = text.splitlines()
    rows = parse(text)
    row = next((r for r in rows if r["id"].upper() == rid.upper()), None)
    if row is None:
        return False, f"unknown RAID id {rid}"

    changed: list[str] = []
    edits: list[tuple[int, int, str]] = []   # (from, to, replacement)

    for name in FIELDS:
        key = name.lower().replace(" ", "_")
        if key not in payload:
            continue
        val = str(payload[key]).strip()
        if name in row["lines"]:
            a = row["lines"][name]
            if lines[a] == f"- **{name}:** {val}".rstrip():
                continue
            edits.append((a, _field_span(lines, a), f"- **{name}:** {val}"))
        else:
            edits.append((row["end"], row["end"], f"- **{name}:** {val}"))
        changed.append(name)

    if any(k in payload for k in ("p", "i", "rag")):
        p = int(payload.get("p", row["p"]) or 0)
        i = int(payload.get("i", row["i"]) or 0)
        score = p * i
        rag = str(payload.get("rag") or "").capitalize() or rag_for(score)
        new = (f"- **P:** {p} | **I:** {i} | **Score:** {score} | **RAG:** {rag}")
        if "score_line" in row:
            edits.append((row["score_line"], row["score_line"] + 1, new))
        else:
            edits.append((row["start"] + 1, row["start"] + 1, new))
        changed.append("scoring")

    if payload.get("type"):
        t = TYPES.get(PREFIX.get(str(payload["type"]).lower(), ""), "")
        if t and t != row["type"]:
            edits.append((row["start"], row["start"] + 1, f"### {row['id']} | {t}"))
            changed.append("Type")

    if not edits:
        return False, "nothing to change"

    # Apply back-to-front so earlier line numbers stay valid.
    for a, b, repl in sorted(edits, key=lambda e: -e[0]):
        lines[a:b] = [repl]

    RAID.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True, f"{row['id']}: " + ", ".join(changed)


def close_row(rid: str, resolution: str) -> tuple[bool, str]:
    return apply_edit(rid, {"status": f"**Closed {TODAY}** — {resolution}".rstrip(" —")})


def next_id(kind: str, rows: list[dict]) -> str:
    pre = PREFIX.get(kind.lower(), kind[:1].upper())
    used = [int(r["id"].split("-")[1]) for r in rows if r["id"].startswith(pre + "-")]
    return f"{pre}-{max(used, default=0) + 1:03d}"


def new_row(payload: dict) -> tuple[bool, str]:
    kind = str(payload.get("type") or "risk").lower()
    if kind not in PREFIX:
        return False, f"type must be one of {sorted(PREFIX)}"
    title = str(payload.get("title") or "").strip()
    if not title:
        return False, "title is required"

    rows = parse()
    rid = next_id(kind, rows)
    p = int(payload.get("p") or 3)
    i = int(payload.get("i") or 3)
    score = p * i
    rag = str(payload.get("rag") or "").capitalize() or rag_for(score)

    def f(key, default="—"):
        return str(payload.get(key) or default).strip()

    block = [
        f"### {rid} | {TYPES[PREFIX[kind]]}",
        f"- **Title:** {title}",
        f"- **Description:** {f('description')}",
        f"- **FW Owner:** {f('fw_owner', 'Yoni')}",
        f"- **External Owner:** {f('external_owner')}",
        f"- **P:** {p} | **I:** {i} | **Score:** {score} | **RAG:** {rag}",
        f"- **Trigger:** {f('trigger')}",
        f"- **Mitigation:** {f('mitigation')}",
        f"- **Contingency:** {f('contingency')}",
        f"- **Due:** {f('due')}",
        f"- **Status:** {f('status', 'Open')}",
        f"- **Link:** {f('link')}",
        "",
    ]

    if not RAID.exists():
        RAID.parent.mkdir(parents=True, exist_ok=True)
        RAID.write_text("# RAID Log — HSI Firmware\n\n## Active Items\n\n", encoding="utf-8")

    text = RAID.read_text(encoding="utf-8")
    lines = text.splitlines()
    # New rows go at the end of the last open block, before any trailing sections.
    at = rows[-1]["end"] if rows else len(lines)
    while at > 0 and not lines[at - 1].strip():
        at -= 1
    lines[at:at] = [""] + block[:-1]
    RAID.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return True, rid


# ---------------------------------------------------------------------------
# Portal payload
# ---------------------------------------------------------------------------

def payload() -> dict:
    rows = [{k: v for k, v in r.items() if k not in ("lines", "start", "end", "score_line")}
            for r in parse()]
    rows.sort(key=lambda r: (r["closed"], -RAGS.index(r["rag"]) if r["rag"] in RAGS else 0,
                             -r["score"], r["due"] or "9999"))
    open_rows = [r for r in rows if not r["closed"]]
    return {
        "rows": rows,
        "owners": sorted({r["owner"] for r in rows if r["owner"]}),
        "counts": {
            "open": len(open_rows),
            "closed": len(rows) - len(open_rows),
            "hot": len([r for r in open_rows if r["rag"] in ("red", "orange")]),
            "overdue": len([r for r in open_rows if r["due"] and r["due"] < TODAY]),
        },
        "path": str(RAID),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rd.py", description="RAID log editor")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list")
    s.add_argument("--all", action="store_true", help="include closed rows")

    s = sub.add_parser("show")
    s.add_argument("id")

    s = sub.add_parser("set")
    s.add_argument("id")
    for name in FIELDS:
        s.add_argument("--" + name.lower().replace(" ", "-"))
    s.add_argument("--p", type=int)
    s.add_argument("--i", type=int)
    s.add_argument("--rag", choices=RAGS)
    s.add_argument("--type", choices=sorted(PREFIX))

    s = sub.add_parser("new")
    s.add_argument("--type", choices=sorted(PREFIX), default="risk")
    for name in FIELDS:
        s.add_argument("--" + name.lower().replace(" ", "-"))
    s.add_argument("--p", type=int, default=3)
    s.add_argument("--i", type=int, default=3)
    s.add_argument("--rag", choices=RAGS)

    s = sub.add_parser("close")
    s.add_argument("id")
    s.add_argument("--resolution", required=True)

    sub.add_parser("payload")

    a = p.parse_args(argv)

    if a.cmd == "payload":
        print(json.dumps(payload(), indent=2, ensure_ascii=False))
        return 0

    if a.cmd == "list":
        rows = payload()["rows"]
        if not a.all:
            rows = [r for r in rows if not r["closed"]]
        for r in rows:
            mark = "x" if r["closed"] else " "
            print(f"[{mark}] {r['id']:<7} {r['rag'] or '-':<7} s{r['score']:<3} "
                  f"{(r['owner'] or '-'):<8} {r['due'] or '-':<11} {r['title'][:60]}")
        print(f"\n{len(rows)} rows · {payload()['counts']}")
        return 0

    if a.cmd == "show":
        row = next((r for r in payload()["rows"] if r["id"].upper() == a.id.upper()), None)
        if not row:
            print(f"unknown RAID id {a.id}", file=sys.stderr)
            return 1
        for k, v in row.items():
            if v not in ("", 0, [], False) or k == "closed":
                print(f"{k:>13}: {v}")
        return 0

    fields = {k: v for k, v in vars(a).items()
              if k not in ("cmd", "all", "resolution", "id") and v is not None}

    if a.cmd == "new":
        ok, msg = new_row(fields)
    elif a.cmd == "close":
        ok, msg = close_row(a.id, a.resolution)
    else:
        ok, msg = apply_edit(a.id, fields)

    print(msg if ok else f"error: {msg}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
