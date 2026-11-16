"""Confirmed full-ticket coverage and fail-closed Jira lifecycle transitions."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from jira_client import JiraClient, JiraError


def pr_set(feature: dict) -> list[str]:
    return sorted(f"{p['owner']}/{p['repo']}#{p['number']}".lower()
                  for p in feature["prs"])


def evidence(feature: dict) -> tuple[dict, dict]:
    """Notes are append-only; their physical order breaks same-day ties."""
    approvals, results = {}, {}
    for note in sorted(feature["notes"], key=lambda n: n["date"]):
        label = note["label"]
        if label not in ("jira-coverage", "jira-sync"):
            continue
        try:
            data = json.loads(note["text"])
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("key"), str):
            continue
        (approvals if label == "jira-coverage" else results)[data["key"]] = data
    return approvals, results


def coverage(feature: dict) -> list[str]:
    approvals, _ = evidence(feature)
    return sorted(key for key, data in approvals.items()
                  if key in feature["jira"] and data.get("confirmed") is True
                  and data.get("prs") == pr_set(feature) and feature["prs"])


def project(feature: dict) -> dict:
    _, results = evidence(feature)
    return {**feature, "jira_coverage": coverage(feature),
            "jira_sync": [r for key, r in results.items()
                          if key in feature["jira"] and r.get("prs") == pr_set(feature)]}


def needs_retry(feature: dict) -> bool:
    if not feature["complete"] or not re.search(r"\bCOMPLETE\b", feature["name"]):
        return False
    _, results = evidence(feature)
    return any(results.get(key, {}).get("target") != "Done"
               or results[key].get("state") not in ("updated", "unchanged")
               or results[key].get("prs") != pr_set(feature)
               for key in coverage(feature))


def normalize(status: str) -> str:
    return re.sub(r"[\s_-]+", "", status).casefold()


def synchronize(feature: dict, key: str, all_features: list[dict],
                client: JiraClient, guard) -> dict:
    result = {"feature_id": feature["id"], "key": key, "prs": pr_set(feature)}

    def outcome(state, message, target=None):
        return {**result, "state": state, "message": message,
                **({"target": target} if target else {})}

    prs = feature["prs"]
    if not prs or any(p.get("error") for p in prs):
        return outcome("blocked", "Missing PRs or failed GitHub probe")
    if any(p.get("status") == "closed" or p.get("draft") for p in prs):
        return outcome("blocked", "Draft or closed-unmerged PR; ticket left unchanged")
    if any(p.get("status") not in {"merged", "pending", "approved", "merge-queue",
                                  "ci-fail", "changes-requested", "needs-rebase"}
           for p in prs):
        return outcome("blocked", "Unknown PR state")
    if any(f["id"] != feature["id"] and not f["complete"] and key in f["jira"]
           for f in all_features):
        return outcome("blocked", "Ticket is also referenced by another active feature")
    target = "Done" if all(p["status"] == "merged" for p in prs) else "In Review"
    try:
        account = client.myself().get("accountId")
        if not account:
            raise JiraError("Jira did not identify the authenticated account")
        fields = client.issue(key).get("fields") or {}
        if (fields.get("assignee") or {}).get("accountId") != account:
            return outcome("blocked", "Ticket is not assigned to the authenticated Jira user")
        status = fields.get("status") or {}
        current = status.get("name", "")
        if not current or not status.get("id"):
            raise JiraError("Jira returned no current status")
        if normalize(current) == normalize(target):
            return outcome("unchanged", f"Already {current}", target)
        category = (status.get("statusCategory") or {}).get("key")
        if category not in ("new", "indeterminate", "done"):
            raise JiraError("Jira returned an unknown status category")
        if category == "done":
            return outcome("blocked", f"Terminal ticket ({current}); will not reopen")
        choices = [t for t in client.transitions(key)
                   if normalize((t.get("to") or {}).get("name", "")) == normalize(target)]
        if len(choices) != 1 or not choices[0].get("id"):
            return outcome("blocked", f"No unique available transition to {target}", target)
        with guard() as unchanged:
            if not unchanged:
                return outcome("blocked", "Feature changed during Jira preflight; retry the scan")
            latest = client.issue(key).get("fields") or {}
            if ((latest.get("assignee") or {}).get("accountId") != account
                    or (latest.get("status") or {}).get("id") != status.get("id")):
                return outcome("blocked", "Jira ownership or status changed during preflight")
            client.transition(key, str(choices[0]["id"]))
            actual = (client.issue(key).get("fields") or {}).get("status") or {}
            if normalize(actual.get("name", "")) != normalize(target):
                raise JiraError(f"Transition to {target} was not verified; retry will re-read Jira")
            return outcome("updated", f"{current} -> {actual['name']}", target)
    except JiraError as exc:
        return outcome("error", str(exc), target)
