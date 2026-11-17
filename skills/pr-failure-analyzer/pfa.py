#!/usr/bin/env python3
"""PR failure analyzer: sightings store and lesson log.

Sightings (which PR/run failed, the verdict and a normalized signature) are
runtime state in ``~/tasks/pr_failures.db`` (override with ``PFA_DB``).
Lessons are shareable technical knowledge appended to
``references/pitfalls.md`` in this skill.

Module API (used by the Team Portal and other skills):
    record(...), match(signature), recent(repos), fingerprint(text),
    parse_verdict(answer), VERDICTS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import domain_db  # noqa: E402

DB_PATH = Path(os.environ.get("PFA_DB", "~/tasks/pr_failures.db")).expanduser()
PITFALLS = Path(__file__).resolve().parent / "references" / "pitfalls.md"
VERDICTS = ("fw-code", "fw-wiring", "ci-tool", "foreign-artifact", "runner-env",
            "flake", "platform", "unknown")
VERDICT_MARK = "PORTAL-TRIAGE:"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, repo TEXT NOT NULL, pr INTEGER, feature_id INTEGER,
    run_url TEXT NOT NULL DEFAULT '', check_name TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL, signature TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'cli'
);
CREATE INDEX IF NOT EXISTS sightings_fp ON sightings(fingerprint);
CREATE INDEX IF NOT EXISTS sightings_repo ON sightings(repo);
"""


def fingerprint(text: str) -> str:
    """Stable id for a failure signature: volatile tokens are masked."""
    t = (text or "").lower()
    t = re.sub(r"https?://\S+", "<url>", t)
    t = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", t)
    t = re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", t)
    t = re.sub(r"(/[\w.@+-]+)+", "<path>", t)
    t = re.sub(r"\d+", "<n>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha1(t.encode()).hexdigest()[:12] if t else ""


def _row(r) -> dict:
    return dict(r)


def record(*, repo: str, verdict: str, pr: int | None = None, feature_id: int | None = None,
           run_url: str = "", check_name: str = "", signature: str = "", summary: str = "",
           source: str = "cli", path: Path | None = None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not repo:
        raise ValueError("repo is required")
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "repo": repo,
           "pr": pr, "feature_id": feature_id, "run_url": run_url[:300],
           "check_name": check_name[:200], "verdict": verdict, "signature": signature[:500],
           "fingerprint": fingerprint(signature), "summary": summary[:500], "source": source}
    with domain_db.transaction(path or DB_PATH, SCHEMA) as conn:
        cur = conn.execute(
            "INSERT INTO sightings(at,repo,pr,feature_id,run_url,check_name,verdict,signature,"
            "fingerprint,summary,source) VALUES(:at,:repo,:pr,:feature_id,:run_url,:check_name,"
            ":verdict,:signature,:fingerprint,:summary,:source)", row)
        row["id"] = cur.lastrowid
        domain_db.audit(conn, "sighting", str(row["id"]), "record",
                        {"repo": repo, "verdict": verdict}, actor=source)
    return row


def match(signature: str, path: Path | None = None, limit: int = 20) -> list[dict]:
    fp = fingerprint(signature)
    if not fp:
        return []
    conn = domain_db.connect(path or DB_PATH, SCHEMA)
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM sightings WHERE fingerprint=? ORDER BY id DESC LIMIT ?", (fp, limit))]
    finally:
        conn.close()


def recent(repos: list[str] | None = None, path: Path | None = None,
           limit: int = 20) -> list[dict]:
    conn = domain_db.connect(path or DB_PATH, SCHEMA)
    try:
        if repos:
            marks = ",".join("?" * len(repos))
            rows = conn.execute(f"SELECT * FROM sightings WHERE repo IN ({marks}) "
                                "ORDER BY id DESC LIMIT ?", (*repos, limit))
        else:
            rows = conn.execute("SELECT * FROM sightings ORDER BY id DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]
    finally:
        conn.close()


def parse_verdict(answer: str) -> dict | None:
    """Read the machine line ``PORTAL-TRIAGE: {json}`` from an analysis.

    Returns ``{"verdict", "summary", "owner", "items": [...]}`` or None.
    Each item may carry repo/pr/check/signature/verdict/run_url.
    """
    for line in reversed((answer or "").splitlines()):
        line = line.strip().strip("`")
        if not line.startswith(VERDICT_MARK):
            continue
        try:
            data = json.loads(line[len(VERDICT_MARK):].strip())
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        verdict = str(data.get("verdict") or "unknown").lower()
        out = {"verdict": verdict if verdict in VERDICTS else "unknown",
               "summary": str(data.get("summary") or "")[:300],
               "owner": str(data.get("owner") or "")[:120], "items": []}
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            v = str(item.get("verdict") or out["verdict"]).lower()
            pr = item.get("pr")
            out["items"].append({
                "repo": str(item.get("repo") or "")[:120],
                "pr": pr if isinstance(pr, int) else None,
                "check": str(item.get("check") or "")[:200],
                "signature": str(item.get("signature") or "")[:500],
                "run_url": str(item.get("run_url") or "")[:300],
                "verdict": v if v in VERDICTS else out["verdict"]})
        return out
    return None


def add_lesson(title: str, verdict: str, symptom: str, cause: str, method: str,
               path: Path | None = None) -> str:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not title.strip() or not symptom.strip():
        raise ValueError("title and symptom are required")
    entry = (f"\n### {title.strip()}\n"
             f"- **Added**: {date.today().isoformat()} · **verdict**: `{verdict}`\n"
             f"- **Symptom**: {symptom.strip()}\n"
             f"- **Cause**: {cause.strip() or 'unknown'}\n"
             f"- **Method / fix**: {method.strip() or '-'}\n")
    target = path or PITFALLS
    with target.open("a", encoding="utf-8") as out:
        out.write(entry)
    return entry


def _print_rows(rows: list[dict]) -> None:
    for r in rows:
        print(f"#{r['id']} {r['at'][:16]} {r['repo']}#{r['pr'] or '-'} {r['verdict']:<16} "
              f"{r['check_name'][:40]} :: {r['summary'][:80]}")
    if not rows:
        print("(none)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pfa.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sight", help="record one analysed failure")
    s.add_argument("--repo", required=True)
    s.add_argument("--verdict", required=True, choices=VERDICTS)
    s.add_argument("--pr", type=int)
    s.add_argument("--feature", type=int)
    s.add_argument("--run", default="")
    s.add_argument("--check", default="")
    s.add_argument("--signature", default="")
    s.add_argument("--summary", default="")
    m = sub.add_parser("match", help="earlier sightings with the same signature")
    m.add_argument("signature")
    r = sub.add_parser("recent", help="latest sightings, optionally per repo")
    r.add_argument("--repo", action="append")
    r.add_argument("--limit", type=int, default=20)
    sub.add_parser("verdicts", help="list verdict classes")
    les = sub.add_parser("lesson", help="append a pitfall/debug lesson to references/pitfalls.md")
    les.add_argument("title")
    les.add_argument("--verdict", required=True, choices=VERDICTS)
    les.add_argument("--symptom", required=True)
    les.add_argument("--cause", default="")
    les.add_argument("--method", default="")
    a = ap.parse_args(argv)
    if a.cmd == "sight":
        row = record(repo=a.repo, verdict=a.verdict, pr=a.pr, feature_id=a.feature,
                     run_url=a.run, check_name=a.check, signature=a.signature,
                     summary=a.summary)
        print(json.dumps(row, indent=1))
    elif a.cmd == "match":
        _print_rows(match(a.signature))
    elif a.cmd == "recent":
        _print_rows(recent(a.repo, limit=a.limit))
    elif a.cmd == "verdicts":
        print("\n".join(VERDICTS))
    elif a.cmd == "lesson":
        print(add_lesson(a.title, a.verdict, a.symptom, a.cause, a.method))
    return 0


if __name__ == "__main__":
    sys.exit(main())
