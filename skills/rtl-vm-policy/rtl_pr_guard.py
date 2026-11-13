"""Source-free GitHub PR metadata for local RTL safety gates."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


_RTL_NAME = re.compile(r"(^|[._/-])(rtl|hdl)(?=$|[._/-])", re.IGNORECASE)
_HDL_SUFFIXES = {".v", ".vh", ".vp", ".sv", ".svh", ".svp", ".sva", ".vhd", ".vhdl", ".vams"}
_FILES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid baseRefOid
      files(first: 100, after: $cursor) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { path changeType }
      }
    }
  }
}
"""
_REVIEWS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state merged
      reviews(first: 100, after: $cursor) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { author { login } state submittedAt }
      }
    }
  }
}
"""
_ACTIVITY_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state mergedAt mergeable headRefOid baseRefOid
      commits(first: 100, after: $cursor) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { commit { oid committedDate } }
      }
    }
  }
}
"""
_SOFTWARE_ACTIVITY_QUERY = _ACTIVITY_QUERY.replace(
    "nodes { commit { oid committedDate } }",
    "nodes { commit { oid committedDate messageHeadline } }",
)
_THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid baseRefOid
      reviewThreads(first: 100, after: $cursor) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes {
          id path line originalLine isResolved isOutdated diffSide startLine
          startDiffSide originalStartLine resolvedBy { login }
        }
      }
    }
  }
}
"""
_THREAD_COMMENTS_QUERY = """
query($id: ID!, $cursor: String) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      id path line originalLine
      comments(first: 100, after: $cursor) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { id databaseId author { login } createdAt updatedAt body }
      }
    }
  }
}
"""


class MetadataError(RuntimeError):
    """Source-free metadata was unavailable or incomplete."""


def is_rtl_repo(repo: str) -> bool:
    return bool(_RTL_NAME.search(repo))


def is_rtl_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        any(part.lower() in {"rtl", "hdl"} for part in parts)
        or PurePosixPath(path).suffix.lower() in _HDL_SUFFIXES
    )


def valid_metadata_path(path: Any) -> bool:
    return (
        isinstance(path, str) and bool(path)
        and not path.startswith("/") and "\\" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
        and all(character not in path for character in ("\0", "\n", "\r"))
    )


def _query(owner: str, repo: str, number: int, query: str, cursor: str | None) -> dict:
    args = [
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-f", f"owner={owner}",
        "-f", f"repo={repo}",
        "-F", f"number={number}",
    ]
    if cursor is not None:
        args.extend(["-f", f"cursor={cursor}"])
    try:
        result = subprocess.run(args, cwd=Path.home(), capture_output=True, text=True,
                                check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MetadataError("GitHub metadata client unavailable") from exc
    if result.returncode:
        raise MetadataError("GitHub metadata request failed")
    try:
        payload = json.loads(result.stdout)
        if payload.get("errors"):
            raise MetadataError("GitHub returned metadata errors")
        pr = payload["data"]["repository"]["pullRequest"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise MetadataError("invalid GitHub metadata response") from exc
    if not isinstance(pr, dict):
        raise MetadataError("PR metadata unavailable")
    return pr


def _connection_pages(
    owner: str, repo: str, number: int, query: str, field: str
) -> tuple[dict, list[dict]]:
    cursor: str | None = None
    cursors: set[str] = set()
    expected: int | None = None
    nodes: list[dict] = []
    initial: dict | None = None
    for _ in range(100):
        pr = _query(owner, repo, number, query, cursor)
        if initial is None:
            initial = pr
        elif field == "reviews" and any(
            pr.get(k) != initial.get(k) for k in ("state", "merged")
        ):
            raise MetadataError("PR state changed during metadata pagination")
        elif field in {"files", "reviewThreads", "commits"} and any(
            pr.get(k) != initial.get(k) for k in ("headRefOid", "baseRefOid")
        ):
            raise MetadataError("PR revisions changed during metadata pagination")
        connection = pr.get(field)
        if not isinstance(connection, dict):
            raise MetadataError("metadata connection unavailable")
        count, page, entries = (
            connection.get("totalCount"), connection.get("pageInfo"), connection.get("nodes")
        )
        if (
            type(count) is not int or count < 0
            or not isinstance(page, dict) or not isinstance(entries, list)
        ):
            raise MetadataError("incomplete metadata connection")
        if expected is None:
            expected = count
        elif count != expected:
            raise MetadataError("metadata count changed during pagination")
        if any(not isinstance(node, dict) for node in entries):
            raise MetadataError("invalid metadata node")
        nodes.extend(entries)
        has_next = page.get("hasNextPage")
        if not isinstance(has_next, bool):
            raise MetadataError("invalid metadata pagination")
        if not has_next:
            if len(nodes) != expected:
                raise MetadataError("metadata pagination was incomplete")
            return initial, nodes
        next_cursor = page.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors:
            raise MetadataError("invalid metadata cursor")
        cursors.add(next_cursor)
        cursor = next_cursor
    raise MetadataError("metadata exceeded pagination limit")


def classify_pr_details(
    owner: str, repo: str, number: int
) -> tuple[str, str, str | None, str | None, list[dict]]:
    """Return the classification and verified, source-free revision/file metadata."""
    if not owner or not repo or number <= 0:
        return "unknown", "invalid PR identity", None, None, []
    if is_rtl_repo(repo):
        return "rtl", "RTL repository", None, None, []
    try:
        pr, files = _connection_pages(owner, repo, number, _FILES_QUERY, "files")
        head, base = pr.get("headRefOid"), pr.get("baseRefOid")
        if any(
            not isinstance(oid, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", oid)
            for oid in (head, base)
        ):
            raise MetadataError("PR revision metadata is incomplete")
        paths = set()
        for file in files:
            path, change = file.get("path"), file.get("changeType")
            if (
                not valid_metadata_path(path)
                or not isinstance(change, str)
                or change not in {"ADDED", "DELETED", "MODIFIED", "CHANGED", "RENAMED", "COPIED"}
                or path in paths
            ):
                raise MetadataError("changed-file metadata is incomplete")
            paths.add(path)
            if is_rtl_path(path):
                return "rtl", "HDL or RTL path in changed-file metadata", head, base, files
        # The GraphQL path is the new name; the previous name is not exposed.
        if any(file["changeType"] in {"CHANGED", "RENAMED", "COPIED"} for file in files):
            return ("unknown", "source path or type of changed/renamed/copied file is unavailable",
                    head, base, files)
    except MetadataError as exc:
        return "unknown", str(exc), None, None, []
    return "software", "complete changed-file metadata", head, base, files


def classify_pr(owner: str, repo: str, number: int) -> tuple[str, str, str | None]:
    """Return classification, reason, and verified head without source or patches."""
    kind, reason, head, _, _ = classify_pr_details(owner, repo, number)
    return kind, reason, head


def review_state(owner: str, repo: str, number: int) -> tuple[dict, list[dict]]:
    """Review decisions and PR state only; no bodies, patches, or diff hunks."""
    pr, nodes = _connection_pages(owner, repo, number, _REVIEWS_QUERY, "reviews")
    state, merged = pr.get("state"), pr.get("merged")
    if state not in {"OPEN", "CLOSED", "MERGED"} or not isinstance(merged, bool):
        raise MetadataError("PR state metadata unavailable")
    reviews: list[dict[str, Any]] = []
    for node in nodes:
        author, decision = node.get("author"), node.get("state")
        if author is not None and not isinstance(author, dict):
            raise MetadataError("invalid review author metadata")
        if not isinstance(decision, str):
            raise MetadataError("invalid review state metadata")
        reviews.append({
            "user": {"login": (author or {}).get("login")},
            "state": decision,
            "submitted_at": node.get("submittedAt"),
        })
    return {"state": state.lower(), "merged": merged}, reviews


def activity_state(
    owner: str, repo: str, number: int, *, include_headline: bool = False,
    expected_head: str | None = None,
) -> dict:
    """PR activity without commit bodies, source, or patches."""
    query = _SOFTWARE_ACTIVITY_QUERY if include_headline else _ACTIVITY_QUERY
    pr, nodes = _connection_pages(owner, repo, number, query, "commits")
    head, base = pr.get("headRefOid"), pr.get("baseRefOid")
    if any(
        not isinstance(oid, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", oid)
        for oid in (head, base)
    ):
        raise MetadataError("PR revision metadata is incomplete")
    if expected_head is not None and head != expected_head:
        raise MetadataError("PR head changed after changed-file classification")
    if pr.get("state") not in {"OPEN", "CLOSED", "MERGED"}:
        raise MetadataError("PR state metadata unavailable")
    commits = []
    for node in nodes:
        commit = node.get("commit")
        if not isinstance(commit, dict):
            raise MetadataError("commit activity metadata unavailable")
        oid, date = commit.get("oid"), commit.get("committedDate")
        if (
            not isinstance(oid, str)
            or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", oid)
            or not isinstance(date, str)
            or not date
        ):
            raise MetadataError("commit activity metadata incomplete")
        item = {"oid": oid, "committedDate": date}
        if include_headline:
            headline = commit.get("messageHeadline")
            if not isinstance(headline, str):
                raise MetadataError("commit headline metadata unavailable")
            item["messageHeadline"] = headline
        commits.append(item)
    return {
        "state": pr["state"],
        "mergedAt": pr.get("mergedAt"),
        "mergeable": pr.get("mergeable"),
        "headRefOid": head,
        "commits": commits,
    }


def _thread_page(thread_id: str, cursor: str | None) -> dict:
    args = [
        "gh", "api", "graphql",
        "-f", f"query={_THREAD_COMMENTS_QUERY}",
        "-f", f"id={thread_id}",
    ]
    if cursor is not None:
        args.extend(["-f", f"cursor={cursor}"])
    try:
        result = subprocess.run(args, cwd=Path.home(), capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MetadataError("GitHub metadata client unavailable") from exc
    if result.returncode:
        raise MetadataError("GitHub review-thread request failed")
    try:
        payload = json.loads(result.stdout)
        if payload.get("errors"):
            raise MetadataError("GitHub returned review-thread errors")
        node = payload["data"]["node"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise MetadataError("invalid review-thread response") from exc
    if not isinstance(node, dict):
        raise MetadataError("review thread unavailable")
    return node


def review_thread_metadata(owner: str, repo: str, number: int, expected_head: str) -> list[dict]:
    """Validate all thread paths before requesting any review text."""
    pr, threads = _connection_pages(owner, repo, number, _THREADS_QUERY, "reviewThreads")
    if pr.get("headRefOid") != expected_head:
        raise MetadataError("PR head changed after changed-file classification")
    for thread in threads:
        if not isinstance(thread.get("id"), str) or not thread["id"] or not isinstance(thread.get("path"), str):
            raise MetadataError("review-thread path metadata unavailable")
        if not thread["path"] or is_rtl_path(thread["path"]):
            raise MetadataError("RTL or unknown path in review-thread metadata")
    return threads


def review_threads(owner: str, repo: str, number: int, expected_head: str) -> list[dict]:
    """Fetch software-thread bodies without ever requesting diff hunks."""
    threads = review_thread_metadata(owner, repo, number, expected_head)
    results = []
    for thread in threads:
        cursor: str | None = None
        cursors: set[str] = set()
        comments: list[dict] = []
        expected: int | None = None
        for _ in range(100):
            page = _thread_page(thread["id"], cursor)
            if page.get("id") != thread["id"] or page.get("path") != thread["path"]:
                raise MetadataError("review-thread identity changed")
            connection = page.get("comments")
            if not isinstance(connection, dict):
                raise MetadataError("review comments unavailable")
            count = connection.get("totalCount")
            info, nodes = connection.get("pageInfo"), connection.get("nodes")
            if (
                type(count) is not int or count < 0
                or not isinstance(info, dict) or not isinstance(nodes, list)
                or any(not isinstance(node, dict) for node in nodes)
            ):
                raise MetadataError("incomplete review comments")
            for node in nodes:
                if (
                    (node.get("author") is not None and not isinstance(node["author"], dict))
                    or (node.get("body") is not None and not isinstance(node["body"], str))
                    or (node.get("createdAt") is not None and not isinstance(node["createdAt"], str))
                ):
                    raise MetadataError("invalid review-comment fields")
            if expected is None:
                expected = count
            elif count != expected:
                raise MetadataError("review comments changed during pagination")
            comments.extend(nodes)
            has_next = info.get("hasNextPage")
            if not isinstance(has_next, bool):
                raise MetadataError("invalid review-comment pagination")
            if not has_next:
                if len(comments) != expected:
                    raise MetadataError("review-comment pagination was incomplete")
                results.append({**thread, "comments": comments})
                break
            next_cursor = info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors:
                raise MetadataError("invalid review-comment cursor")
            cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise MetadataError("review comments exceeded pagination limit")
    current = review_thread_metadata(owner, repo, number, expected_head)
    if {t["id"] for t in current} != {t["id"] for t in threads}:
        raise MetadataError("review threads changed during retrieval")
    return results
