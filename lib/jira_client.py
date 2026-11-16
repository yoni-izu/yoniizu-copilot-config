"""Bounded Jira REST access using the existing private credential locations."""
from __future__ import annotations

import base64
import json
import netrc
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from private_paths import load_private_json


class JiraError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise JiraError("Jira redirected the request; configure the canonical HTTPS site")


def credentials(site: str) -> tuple[str, str]:
    if not site:
        raise JiraError("Jira site is not configured in private config/jira.json")
    email, token = os.environ.get("ATLASSIAN_EMAIL"), os.environ.get("ATLASSIAN_TOKEN")
    if email and token:
        return email, token
    host = urllib.parse.urlparse(site).hostname
    try:
        auth = netrc.netrc().authenticators(host)
    except (OSError, netrc.NetrcParseError):
        auth = None
    if auth and auth[0] and auth[2]:
        return auth[0], auth[2]
    token_file = Path("~/.atlassian-token").expanduser()
    email_file = Path("~/.atlassian-email").expanduser()
    if token_file.exists():
        lines = token_file.read_text(encoding="utf-8").strip().splitlines()
        token = lines[0].strip() if lines else ""
        email = email_file.read_text(encoding="utf-8").strip() if email_file.exists() else email
        if token and email:
            return email, token
    raise JiraError(
        "No Atlassian credentials; configure ~/.netrc or ATLASSIAN_EMAIL/ATLASSIAN_TOKEN")


class JiraClient:
    def __init__(self) -> None:
        config = load_private_json("config/jira.json")
        self.site = os.environ.get("JIRA_SITE", config.get("site", "")).rstrip("/")
        parsed = urllib.parse.urlparse(self.site)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise JiraError("Jira requires an HTTPS site in private config/jira.json or JIRA_SITE")
        email, token = credentials(self.site)
        self.auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            self.site + "/rest/api/3/" + path,
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": self.auth, "Accept": "application/json",
                     "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=30) as response:
                data = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise JiraError(f"Jira {method} {path}: HTTP {exc.code}") from None
        except (OSError, TimeoutError) as exc:
            raise JiraError(f"Jira {method} {path}: {exc}") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise JiraError(f"Jira {method} {path}: invalid JSON") from None
        if not isinstance(data, dict):
            raise JiraError(f"Jira {method} {path}: expected an object")
        return data

    def myself(self) -> dict:
        return self.request("GET", "myself")

    def issue(self, key: str) -> dict:
        return self.request("GET", f"issue/{key}?fields=assignee,status")

    def transitions(self, key: str) -> list[dict]:
        result = self.request("GET", f"issue/{key}/transitions")
        if not isinstance(result.get("transitions"), list):
            raise JiraError(f"Jira {key}: missing transitions")
        return result["transitions"]

    def transition(self, key: str, transition_id: str) -> None:
        self.request("POST", f"issue/{key}/transitions", {"transition": {"id": transition_id}})
