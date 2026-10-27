#!/usr/bin/env python3
"""Owner-side incremental recent-activity scanner for team-manager members.

Per member, on explicit request only, scans:
  * Jira  — issues assigned to / reported by the member's Jira account, updated
            since the Jira cursor (REST API v3 ``/search/jql``, basic auth).
  * GitHub — PRs authored by the member's handle and reviews the member
            submitted, updated since the GitHub cursor (``gh api``).

The first scan of a source looks back ``FIRST_SCAN_DAYS``; later scans start at
that source's last successful watermark (minus a small overlap — upserts are
idempotent).  A failing source records its error and keeps its cursor.  A
source cut short by page caps saves what it saw but keeps its cursor (``incomplete``).
Scans hold a per-member lease row so two scans of one member never overlap.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Iterable, Protocol
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from private_paths import load_private_json  # noqa: E402

JIRA_CONFIG = load_private_json("config/jira.json")
JIRA_SITE = os.environ.get(
    "TEAM_ACTIVITY_JIRA_SITE", JIRA_CONFIG.get("site", "")).rstrip("/")
FIRST_SCAN_DAYS = 7
OVERLAP = timedelta(minutes=10)
LEASE = timedelta(minutes=30)
SOURCE_DEADLINE_S = 600  # wall-clock budget per source scan
HTTP_TIMEOUT = 30
GH_TIMEOUT = 45
JIRA_PAGE_SIZE = 100
JIRA_MAX_PAGES = 20
GH_PAGE_SIZE = 100
GH_MAX_PAGES = 10  # GitHub search never returns more than 1000 results.
GH_MAX_REVIEW_PRS = 100
SOURCES = ("jira", "github")
JIRA_FIELDS = ["summary", "status", "issuetype", "assignee", "reporter", "updated", "project"]

ACTIVITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_scans (
  scan_id TEXT PRIMARY KEY,
  member_key TEXT NOT NULL REFERENCES members(member_key),
  status TEXT NOT NULL CHECK(status IN ('running','ok','partial','failed','abandoned')),
  started_at TEXT NOT NULL,
  lease_expires_at TEXT NOT NULL,
  finished_at TEXT,
  result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_scans_member ON activity_scans(member_key, started_at);
CREATE TABLE IF NOT EXISTS activity_source_state (
  member_key TEXT NOT NULL REFERENCES members(member_key),
  source TEXT NOT NULL CHECK(source IN ('jira','github')),
  cursor_at TEXT,
  last_success_at TEXT,
  last_attempt_at TEXT,
  last_status TEXT,
  last_error_kind TEXT,
  last_error TEXT,
  last_scan_id TEXT,
  last_item_count INTEGER,
  PRIMARY KEY(member_key, source)
);
CREATE TABLE IF NOT EXISTS activity_observations (
  observation_id TEXT PRIMARY KEY,
  member_key TEXT NOT NULL REFERENCES members(member_key),
  source TEXT NOT NULL CHECK(source IN ('jira','github')),
  kind TEXT NOT NULL,
  external_id TEXT NOT NULL,
  title TEXT,
  url TEXT,
  state TEXT,
  observed_at TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_scan_id TEXT NOT NULL,
  UNIQUE(member_key, source, kind, external_id)
);
CREATE INDEX IF NOT EXISTS idx_activity_obs_member ON activity_observations(member_key, observed_at);
"""
ACTIVITY_TABLES = ("activity_scans", "activity_source_state", "activity_observations")


class ActivitySourceError(RuntimeError):
    """A source failed; ``kind`` is a stable machine-readable category."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# --------------------------------------------------------------------------- time

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]  # Jira's +0300 offsets
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ------------------------------------------------------------------------ clients

class JiraClient(Protocol):
    def search_page(self, jql: str, fields: list[str], page_token: str | None,
                    page_size: int) -> dict[str, Any]: ...


class GitHubClient(Protocol):
    def search_issues(self, query: str, page: int, per_page: int) -> dict[str, Any]: ...
    def pr_reviews(self, repo: str, number: int, page: int, per_page: int) -> list[dict[str, Any]]: ...


def jira_credentials() -> tuple[str, str]:
    """Same resolution order as bug-monitor: env, ~/.netrc, ~/.atlassian-token(+email)."""
    if not JIRA_SITE:
        raise ActivitySourceError(
            "config",
            "Jira site is not configured; set TEAM_ACTIVITY_JIRA_SITE or add "
            "config/jira.json under COPILOT_PRIVATE_DIR")
    email = os.environ.get("ATLASSIAN_EMAIL")
    token = os.environ.get("ATLASSIAN_TOKEN")
    if email and token:
        return email, token
    host = urllib.parse.urlparse(JIRA_SITE).hostname
    try:
        import netrc
        auth = netrc.netrc().authenticators(host)
        if auth and auth[0] and auth[2]:
            return auth[0], auth[2]
    except (OSError, netrc.NetrcParseError):
        pass
    token_file = Path("~/.atlassian-token").expanduser()
    email_file = Path("~/.atlassian-email").expanduser()
    if token_file.exists():
        lines = token_file.read_text(encoding="utf-8").strip().splitlines()
        tok = lines[0].strip() if lines else ""
        mail = email_file.read_text(encoding="utf-8").strip() if email_file.exists() else email
        if tok and mail:
            return mail, tok
    raise ActivitySourceError(
        "auth",
        f"no Atlassian credentials: add a ~/.netrc entry for {host} (login=email, "
        "password=API token) or export ATLASSIAN_EMAIL/ATLASSIAN_TOKEN",
    )


class JiraRestClient:
    """Direct Jira Cloud REST client (no MCP); bounded per-request timeout."""

    def __init__(self, site: str = JIRA_SITE, timeout: float = HTTP_TIMEOUT) -> None:
        self.site = site.rstrip("/")
        self.timeout = timeout
        self._auth: str | None = None

    def _header(self) -> str:
        if self._auth is None:
            email, token = jira_credentials()
            self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        return self._auth

    def search_page(self, jql: str, fields: list[str], page_token: str | None,
                    page_size: int) -> dict[str, Any]:
        body: dict[str, Any] = {"jql": jql, "fields": fields, "maxResults": page_size}
        if page_token:
            body["nextPageToken"] = page_token
        req = urllib.request.Request(
            self.site + "/rest/api/3/search/jql", data=json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", self._header())
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            kind = "auth" if exc.code in (401, 403) else "http"
            raise ActivitySourceError(kind, f"Jira search HTTP {exc.code}: {detail}") from None
        except (TimeoutError, OSError) as exc:  # URLError is an OSError
            reason = getattr(exc, "reason", exc)
            kind = "timeout" if "timed out" in str(reason) else "transport"
            raise ActivitySourceError(kind, f"Jira search failed: {reason}") from None
        except json.JSONDecodeError:
            raise ActivitySourceError("invalid-response", "Jira returned non-JSON") from None


class GhCliClient:
    """GitHub via the authenticated ``gh`` CLI; bounded per-call timeout."""

    def __init__(self, timeout: float = GH_TIMEOUT) -> None:
        self.timeout = timeout

    def _api(self, args: list[str]) -> Any:
        try:
            proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except FileNotFoundError:
            raise ActivitySourceError("tool-missing", "gh CLI not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise ActivitySourceError("timeout", f"gh api timed out after {self.timeout}s") from None
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()[:300]
            low = err.lower()
            if "auth login" in low or "http 401" in low or "bad credentials" in low:
                raise ActivitySourceError("auth", f"gh not authenticated: {err}")
            if "rate limit" in low:
                raise ActivitySourceError("rate-limit", err)
            raise ActivitySourceError("http", f"gh api failed: {err}")
        try:
            return json.loads(proc.stdout or "null")
        except json.JSONDecodeError:
            raise ActivitySourceError("invalid-response", "gh returned non-JSON") from None

    def search_issues(self, query: str, page: int, per_page: int) -> dict[str, Any]:
        return self._api(["-X", "GET", "search/issues", "-f", f"q={query}", "-f", "sort=updated",
                          "-f", "order=asc", "-F", f"per_page={per_page}", "-F", f"page={page}"])

    def pr_reviews(self, repo: str, number: int, page: int, per_page: int) -> list[dict[str, Any]]:
        return self._api(["-X", "GET", f"repos/{repo}/pulls/{number}/reviews",
                          "-F", f"per_page={per_page}", "-F", f"page={page}"]) or []


# --------------------------------------------------------------------- collectors

class _Collected:
    def __init__(self, budget_s: float = SOURCE_DEADLINE_S) -> None:
        self.deadline = time.monotonic() + budget_s
        self.items: list[dict[str, Any]] = []
        self.pages = 0
        self.truncated = False
        self.truncation_reason: str | None = None

    def tick(self) -> None:
        if time.monotonic() > self.deadline:
            raise ActivitySourceError("timeout", f"source exceeded {SOURCE_DEADLINE_S}s budget")
        self.pages += 1

    def cap(self, reason: str) -> None:
        self.truncated = True
        self.truncation_reason = self.truncation_reason or reason


def _collect_jira(client: JiraClient, account_id: str, since: datetime, now: datetime) -> _Collected:
    out = _Collected()
    minutes = max(1, math.ceil((now - since).total_seconds() / 60))
    acc = account_id.replace('"', '')
    jql = (f'(assignee = "{acc}" OR reporter = "{acc}") '
           f'AND updated >= "-{minutes}m" ORDER BY updated ASC')
    token: str | None = None
    while True:
        if out.pages >= JIRA_MAX_PAGES:
            out.cap(f"Jira page cap ({JIRA_MAX_PAGES} pages) reached")
            break
        out.tick()
        page = client.search_page(jql, JIRA_FIELDS, token, JIRA_PAGE_SIZE)
        if not isinstance(page, dict):
            raise ActivitySourceError("invalid-response", "Jira page is not an object")
        for issue in page.get("issues") or []:
            fields = issue.get("fields") or {}
            updated = _parse_ts(fields.get("updated"))
            if updated is None or updated < since:
                continue
            roles = []
            if ((fields.get("assignee") or {}).get("accountId")) == account_id:
                roles.append("assigned")
            if ((fields.get("reporter") or {}).get("accountId")) == account_id:
                roles.append("reported")
            for role in roles:
                out.items.append({
                    "source": "jira", "kind": f"jira-{role}", "external_id": issue["key"],
                    "title": fields.get("summary"),
                    "url": f"{JIRA_SITE}/browse/{issue['key']}",
                    "state": (fields.get("status") or {}).get("name"),
                    "observed_at": _iso(updated),
                    "detail": {
                        "issue_type": (fields.get("issuetype") or {}).get("name"),
                        "project": (fields.get("project") or {}).get("key"),
                        "status_category": ((fields.get("status") or {}).get("statusCategory") or {}).get("key"),
                    },
                })
        token = page.get("nextPageToken")
        if page.get("isLast") or not token:
            break
    return out


def _search_all(client: GitHubClient, query: str, since: datetime, out: _Collected) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        if page > GH_MAX_PAGES:
            out.cap(f"GitHub search page cap ({GH_MAX_PAGES} pages) reached")
            break
        out.tick()
        data = client.search_issues(query, page, GH_PAGE_SIZE)
        if not isinstance(data, dict):
            raise ActivitySourceError("invalid-response", "GitHub search page is not an object")
        items = data.get("items") or []
        for item in items:
            updated = _parse_ts(item.get("updated_at"))
            if updated is None or updated < since:
                continue
            rows.append(item)
        if data.get("incomplete_results"):
            raise ActivitySourceError("incomplete", "GitHub search reported incomplete_results")
        if len(items) < GH_PAGE_SIZE:
            break
        page += 1
    return rows


def _repo_of(item: dict[str, Any]) -> str:
    return (item.get("repository_url") or "").split("/repos/", 1)[-1]


def _collect_github(client: GitHubClient, handle: str, since: datetime) -> _Collected:
    out = _Collected()
    orgs = [o.strip() for o in os.environ.get("TEAM_ACTIVITY_GITHUB_ORGS", "").split(",") if o.strip()]
    scope = "".join(f" org:{o}" for o in orgs)
    stamp = _iso(since)
    authored = _search_all(client, f"type:pr author:{handle} updated:>={stamp}{scope}", since, out)
    for item in authored:
        pr = item.get("pull_request") or {}
        repo = _repo_of(item)
        out.items.append({
            "source": "github", "kind": "github-pr-authored",
            "external_id": f"{repo}#{item['number']}", "title": item.get("title"),
            "url": item.get("html_url"),
            "state": "merged" if pr.get("merged_at") else item.get("state"),
            "observed_at": _iso(_parse_ts(item["updated_at"])),
            "detail": {"repo": repo, "number": item["number"], "draft": item.get("draft"),
                       "merged_at": pr.get("merged_at"), "created_at": item.get("created_at")},
        })
    reviewed = _search_all(client, f"type:pr reviewed-by:{handle} updated:>={stamp}{scope}", since, out)
    if len(reviewed) > GH_MAX_REVIEW_PRS:
        reviewed.sort(key=lambda i: i["updated_at"], reverse=True)
        out.cap(f"reviewed PR cap ({GH_MAX_REVIEW_PRS}) reached")
        reviewed = reviewed[:GH_MAX_REVIEW_PRS]
    for item in reviewed:
        repo = _repo_of(item)
        page = 1
        while page <= GH_MAX_PAGES:
            out.tick()
            reviews = client.pr_reviews(repo, int(item["number"]), page, GH_PAGE_SIZE)
            if not isinstance(reviews, list):
                raise ActivitySourceError("invalid-response", "GitHub reviews page is not a list")
            for review in reviews:
                login = ((review.get("user") or {}).get("login") or "").lower()
                submitted = _parse_ts(review.get("submitted_at"))
                if login != handle or submitted is None or submitted < since:
                    continue
                out.items.append({
                    "source": "github", "kind": "github-pr-review",
                    "external_id": f"{repo}#{item['number']}/review/{review.get('id')}",
                    "title": item.get("title"),
                    "url": review.get("html_url") or item.get("html_url"),
                    "state": review.get("state"), "observed_at": _iso(submitted),
                    "detail": {"repo": repo, "number": item["number"],
                               "pr_url": item.get("html_url")},
                })
            if len(reviews) < GH_PAGE_SIZE:
                break
            page += 1
        else:
            out.cap(f"review page cap ({GH_MAX_PAGES} pages) reached for {repo}#{item['number']}")
    return out


# ------------------------------------------------------------------------ storage

def initialize_activity(store: Any) -> None:
    """Create activity tables; idempotent. Called from the owner's init_db."""
    with closing(store.connect()) as conn:
        conn.executescript(ACTIVITY_SCHEMA)


def _tables_ready(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({','.join('?' * len(ACTIVITY_TABLES))})",
        ACTIVITY_TABLES).fetchall()
    return len(rows) == len(ACTIVITY_TABLES)


def _obs_id(member_key: str, item: dict[str, Any]) -> str:
    raw = "\x1f".join([member_key, item["source"], item["kind"], item["external_id"]])
    return "act-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _acquire(conn: sqlite3.Connection, member_key: str, now: datetime) -> tuple[str | None, dict | None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE activity_scans SET status='abandoned', finished_at=? "
            "WHERE member_key=? AND status='running' AND lease_expires_at<?",
            (_iso(now), member_key, _iso(now)))
        busy = conn.execute(
            "SELECT scan_id, started_at, lease_expires_at FROM activity_scans "
            "WHERE member_key=? AND status='running'", (member_key,)).fetchone()
        if busy:
            conn.execute("COMMIT")
            return None, dict(busy)
        scan_id = f"scan-{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO activity_scans(scan_id, member_key, status, started_at, lease_expires_at) "
            "VALUES(?,?,?,?,?)", (scan_id, member_key, "running", _iso(now), _iso(now + LEASE)))
        conn.execute("COMMIT")
        return scan_id, None
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _source_state(conn: sqlite3.Connection, member_key: str, source: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM activity_source_state WHERE member_key=? AND source=?",
                       (member_key, source)).fetchone()
    return dict(row) if row else None


def _persist_source(conn: sqlite3.Connection, member_key: str, scan_id: str, source: str,
                    report: dict[str, Any], items: list[dict[str, Any]], cursor_after: str | None,
                    now: datetime) -> bool:
    """Write observations + state atomically; False if the scan lost its lease."""
    stamp = _iso(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        owned = conn.execute("SELECT 1 FROM activity_scans WHERE scan_id=? AND status='running'",
                             (scan_id,)).fetchone()
        if not owned:
            conn.execute("ROLLBACK")
            return False
        conn.execute("UPDATE activity_scans SET lease_expires_at=? WHERE scan_id=?",
                     (_iso(now + LEASE), scan_id))
        created = updated = 0
        for item in items:
            oid = _obs_id(member_key, item)
            existed = conn.execute(
                "SELECT observed_at, state, title FROM activity_observations WHERE observation_id=?",
                (oid,)).fetchone()
            conn.execute(
                """
                INSERT INTO activity_observations(observation_id, member_key, source, kind,
                  external_id, title, url, state, observed_at, detail_json, first_seen_at,
                  last_seen_at, last_scan_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(observation_id) DO UPDATE SET
                  title=excluded.title, url=excluded.url, state=excluded.state,
                  observed_at=MAX(observed_at, excluded.observed_at),
                  detail_json=excluded.detail_json, last_seen_at=excluded.last_seen_at,
                  last_scan_id=excluded.last_scan_id
                """,
                (oid, member_key, item["source"], item["kind"], item["external_id"],
                 item.get("title"), item.get("url"), item.get("state"), item["observed_at"],
                 json.dumps(item.get("detail") or {}, ensure_ascii=False, sort_keys=True),
                 stamp, stamp, scan_id))
            if existed is None:
                created += 1
            elif (existed["observed_at"], existed["state"], existed["title"]) != (
                    max(existed["observed_at"], item["observed_at"]), item.get("state"), item.get("title")):
                updated += 1
        report["new"], report["changed"] = created, updated
        ok = report["status"] == "ok"
        conn.execute(
            """
            INSERT INTO activity_source_state(member_key, source, cursor_at, last_success_at,
              last_attempt_at, last_status, last_error_kind, last_error, last_scan_id, last_item_count)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(member_key, source) DO UPDATE SET
              cursor_at=COALESCE(excluded.cursor_at, activity_source_state.cursor_at),
              last_success_at=COALESCE(excluded.last_success_at, activity_source_state.last_success_at),
              last_attempt_at=excluded.last_attempt_at, last_status=excluded.last_status,
              last_error_kind=excluded.last_error_kind, last_error=excluded.last_error,
              last_scan_id=excluded.last_scan_id, last_item_count=excluded.last_item_count
            """,
            (member_key, source, cursor_after if ok else None, stamp if ok else None, stamp,
             report["status"], report.get("error_kind"), report.get("error"), scan_id,
             len(items) if report["status"] != "error" else None))
        conn.execute("COMMIT")
        return True
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def scan_activity(store: Any, member_key: str, *, jira_client: JiraClient | None = None,
                  github_client: GitHubClient | None = None, sources: Iterable[str] = SOURCES,
                  now: datetime | None = None) -> dict[str, Any]:
    """Explicitly scan one member. Returns a result dict; never raises for source failures.

    Raises ``LookupError`` for an unknown member and ``ValueError`` for unknown sources.
    ``status`` is ``ok`` | ``partial`` | ``failed`` | ``busy``.
    Per-source ``status`` is ``ok`` | ``incomplete`` (capped: observations saved, cursor
    unchanged, listed in ``errors``) | ``error`` (nothing saved, cursor unchanged).
    """
    wanted = list(dict.fromkeys(sources))
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown or not wanted:
        raise ValueError(f"unknown/empty sources: {unknown or wanted}; expected subset of {SOURCES}")
    start = (now or _utcnow()).astimezone(timezone.utc).replace(microsecond=0)
    initialize_activity(store)
    with closing(store.connect()) as conn:
        member = conn.execute(
            "SELECT member_key, jira_account_id, github_handle FROM members WHERE member_key=?",
            (member_key,)).fetchone()
        if member is None:
            raise LookupError(f"unknown member: {member_key}")
        scan_id, busy = _acquire(conn, member_key, start)
        if scan_id is None:
            return {"member_key": member_key, "scan_id": None, "status": "busy",
                    "started_at": _iso(start), "finished_at": _iso(start), "running_scan": busy,
                    "sources": {}, "errors": [{
                        "source": None, "kind": "busy",
                        "message": f"scan {busy['scan_id']} already running for {member_key}"}]}
        reports: dict[str, dict[str, Any]] = {}
        lost_lease = False
        try:
            for source in wanted:
                state = _source_state(conn, member_key, source) or {}
                cursor_before = state.get("cursor_at")
                first = cursor_before is None
                since = (start - timedelta(days=FIRST_SCAN_DAYS)) if first else (
                    _parse_ts(cursor_before) - OVERLAP)
                report: dict[str, Any] = {
                    "status": "ok", "mode": "first" if first else "incremental",
                    "since": _iso(since), "cursor_before": cursor_before, "cursor_after": cursor_before,
                    "fetched": 0, "new": 0, "changed": 0, "pages": 0, "truncated": False,
                    "error_kind": None, "error": None,
                }
                items: list[dict[str, Any]] = []
                cursor_after: str | None = None
                try:
                    if source == "jira":
                        ident = (member["jira_account_id"] or "").strip()
                        if not ident:
                            raise ActivitySourceError(
                                "missing-identity", f"member {member_key} has no jira_account_id")
                        collected = _collect_jira(jira_client or JiraRestClient(), ident, since, start)
                    else:
                        ident = (member["github_handle"] or "").strip().lower()
                        if not ident:
                            raise ActivitySourceError(
                                "missing-identity", f"member {member_key} has no github_handle")
                        collected = _collect_github(github_client or GhCliClient(), ident, since)
                    items = collected.items
                    report["fetched"], report["pages"] = len(items), collected.pages
                    if collected.truncated:
                        # Results may be missing anywhere in the window (GitHub/Jira order is
                        # not a safe watermark), so keep what was seen but never move the cursor.
                        report.update(truncated=True, status="incomplete", error_kind="incomplete",
                                      error=f"{collected.truncation_reason}; saved {len(items)} "
                                            "observations, cursor not advanced")
                    else:
                        cursor_after = _iso(start)
                except ActivitySourceError as exc:
                    items, cursor_after = [], None
                    report.update(status="error", error_kind=exc.kind, error=str(exc), fetched=0)
                except Exception as exc:  # noqa: BLE001 — any client bug must surface, not advance
                    items, cursor_after = [], None
                    report.update(status="error", error_kind="unexpected",
                                  error=f"{type(exc).__name__}: {exc}", fetched=0)
                if cursor_after:
                    report["cursor_after"] = cursor_after
                if not _persist_source(conn, member_key, scan_id, source, report, items,
                                       cursor_after, _utcnow()):
                    lost_lease = True
                    report.update(status="error", error_kind="lease-lost",
                                  error="scan lease expired; results discarded",
                                  cursor_after=cursor_before)
                reports[source] = report
                if lost_lease:
                    break
        finally:
            failed = [s for s, r in reports.items() if r["status"] == "error"]
            unsuccessful = [s for s, r in reports.items() if r["status"] != "ok"]
            if lost_lease or len(reports) < len(wanted):
                status = "failed" if not reports or len(failed) == len(reports) else "partial"
            elif not unsuccessful:
                status = "ok"
            elif len(failed) == len(reports):
                status = "failed"
            else:
                status = "partial"
            result = {"member_key": member_key, "scan_id": scan_id, "status": status,
                      "started_at": _iso(start), "finished_at": _iso(_utcnow()),
                      "sources": reports,
                      "errors": [{"source": s, "kind": r["error_kind"], "message": r["error"]}
                                 for s, r in reports.items() if r["status"] != "ok"]}
            conn.execute(
                "UPDATE activity_scans SET status=?, finished_at=?, result_json=? "
                "WHERE scan_id=? AND status='running'",
                (status, result["finished_at"], json.dumps(result, sort_keys=True), scan_id))
        return result


# ------------------------------------------------------------------------ reading

def _source_meta(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    return {
        "scanned": bool(state.get("last_attempt_at")),
        "cursor_at": state.get("cursor_at"),
        "last_success_at": state.get("last_success_at"),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_status": state.get("last_status"),
        "last_error_kind": state.get("last_error_kind"),
        "last_error": state.get("last_error"),
        "last_scan_id": state.get("last_scan_id"),
        "last_item_count": state.get("last_item_count"),
        "next_mode": "incremental" if state.get("cursor_at") else "first",
    }


def activity_payload(store: Any, member_keys: Iterable[str] | None = None,
                     limit: int = 100) -> dict[str, Any]:
    """Read-only saved observations + per-source scan metadata (no external calls, no DDL)."""
    keys = list(dict.fromkeys(member_keys or []))
    out: dict[str, Any] = {"generated_at": _iso(_utcnow()), "initialized": False,
                           "first_scan_days": FIRST_SCAN_DAYS, "members": {}}
    with closing(store.connect()) as conn:
        known = {r[0] for r in conn.execute("SELECT member_key FROM members")}
        if member_keys is None:
            keys = sorted(known)
        ready = _tables_ready(conn)
        out["initialized"] = ready
        now = _iso(_utcnow())
        for key in keys:
            entry: dict[str, Any] = {
                "known": key in known, "observations": [], "counts": {},
                "sources": {s: _source_meta(None) for s in SOURCES},
                "last_scan": None, "running_scan": None,
            }
            if ready and key in known:
                rows = conn.execute(
                    "SELECT source, kind, external_id, title, url, state, observed_at, detail_json, "
                    "first_seen_at, last_seen_at FROM activity_observations WHERE member_key=? "
                    "ORDER BY observed_at DESC, external_id LIMIT ?", (key, int(limit))).fetchall()
                entry["observations"] = [
                    {**{k: r[k] for k in r.keys() if k != "detail_json"},
                     "detail": json.loads(r["detail_json"] or "{}")} for r in rows]
                entry["counts"] = {r[0]: r[1] for r in conn.execute(
                    "SELECT kind, COUNT(*) FROM activity_observations WHERE member_key=? GROUP BY kind",
                    (key,))}
                for source in SOURCES:
                    entry["sources"][source] = _source_meta(_source_state(conn, key, source))
                last = conn.execute(
                    "SELECT scan_id, status, started_at, finished_at FROM activity_scans "
                    "WHERE member_key=? AND status!='running' ORDER BY started_at DESC, rowid DESC LIMIT 1",
                    (key,)).fetchone()
                entry["last_scan"] = dict(last) if last else None
                running = conn.execute(
                    "SELECT scan_id, started_at, lease_expires_at FROM activity_scans "
                    "WHERE member_key=? AND status='running' AND lease_expires_at>=?",
                    (key, now)).fetchone()
                entry["running_scan"] = dict(running) if running else None
            entry["errors"] = [
                {"source": src, "kind": meta["last_error_kind"], "message": meta["last_error"],
                 "at": meta["last_attempt_at"]}
                for src, meta in entry["sources"].items()
                if meta["last_status"] in ("error", "incomplete")]
            if not entry["known"]:
                entry["errors"].append({"source": None, "kind": "unknown-member",
                                        "message": f"unknown member: {key}", "at": None})
            out["members"][key] = entry
    return out
