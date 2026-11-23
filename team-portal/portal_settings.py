#!/usr/bin/env python3
"""Portal-owned settings (``state/portal-settings.json``).

Only portal behaviour lives here; domain owners keep their own configuration.
Saves are validated, revision-checked and atomic. Schedule fields are applied
to the daily maintenance timer through a managed systemd drop-in
(``portal-maintenance.timer.d/schedule.conf``); a failed apply rolls the file
back so the stored settings never disagree with the live timer.
"""
from __future__ import annotations

import copy
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

PORTAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL_ROOT.parent / "lib"))
from private_paths import private_root  # noqa: E402

STATE_DIR = private_root() / "team-portal" / "state"
SETTINGS_PATH = STATE_DIR / "portal-settings.json"
BRIEF_PATH = STATE_DIR / "morning-brief.json"
SYSTEMD_USER_DIR = Path(os.environ.get("PORTAL_SYSTEMD_USER_DIR",
                                       "~/.config/systemd/user")).expanduser()
TIMER = "portal-maintenance.timer"
DROPIN = SYSTEMD_USER_DIR / f"{TIMER}.d" / "schedule.conf"

ROUTINE_STEPS = ("reviews", "prs", "bugs", "workspaces", "team", "ai")
# systemd OnCalendar weekday names, in display order (Sun-first work week).
WEEK_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
DEFAULTS: dict[str, Any] = {
    "schema": 1,
    "routine": {
        "enabled": True,
        "time": "08:00",
        "days": list(WEEK_DAYS),
        "steps": {step: True for step in ROUTINE_STEPS},
        "auto_restart": True,
        "snapshot_keep": 14,
        "queue_retention_days": 90,
        "activity_window_hours": 24,
        "brief_stale_hours": 26,
    },
    # AI assistance (Copilot CLI). Every run is queued, capped and journaled.
    "ai": {
        "enabled": True,
        "model": "fusion",
        "daily_cap": 40,
        "interactive_daily_cap": 150,
        "timeout_s": 300,
        "review_timeout_s": 2400,
        "allow_jira_github_reads": True,
    },
    # Machines kept on a persistent SSH live-status link. Every registered
    # target gets a terminal; the old "terminal_targets" key is ignored.
    "machines": {
        "live_targets": ["vm"],
        "live_interval_s": 15,
        "power_checks": True,
        "power_grace_s": 30,
        "power_retry_s": 30,
        "power_retry_limit": 3,
    },
    # Pop-up notifications (browser; Windows toast when no portal tab is open).
    "notify": {
        "enabled": True,
        "queue": True,
        "machine_jobs": True,
        "ai": True,
        "reachability": True,
        "windows_toast": True,
    },
    # Automatic resource updates: domain DB watcher -> event stream -> tabs.
    "live_updates": {
        "enabled": True,
        "tasks": True,
        "workspaces": True,
        "bugs": True,
        "prs": True,
        "raid": True,
        "team": True,
        "delegation": True,
        "pr_failures": True,
        "scan_interval_s": 2,
        "quiet_ms": 750,
        "replay_events": 200,
        "push_stream": True,
        "keepalive_s": 20,
        "state_interval_s": 60,
        "poll_interval_s": 5,
        "auto_reload": True,
        "popups": True,
        "history_limit": 50,
        "detail_limit": 50,
        "refresh_delay_ms": 250,
    },
}
LIVE_DOMAINS = ("tasks", "workspaces", "bugs", "prs", "raid", "team", "delegation",
                "pr_failures")
INT_RANGES = {"snapshot_keep": (1, 60), "queue_retention_days": (7, 365),
              "activity_window_hours": (1, 168), "brief_stale_hours": (1, 168)}
GROUP_INT_RANGES = {"ai": {"daily_cap": (0, 500), "interactive_daily_cap": (0, 1000),
                           "timeout_s": (30, 1800),
                           "review_timeout_s": (300, 7200)},
                    "machines": {"live_interval_s": (5, 300),
                                 "power_grace_s": (0, 300),
                                 "power_retry_s": (5, 300),
                                 "power_retry_limit": (0, 10)},
                    "live_updates": {"scan_interval_s": (1, 60), "quiet_ms": (0, 10000),
                                     "replay_events": (50, 2000), "keepalive_s": (5, 40),
                                     "state_interval_s": (15, 600),
                                     "poll_interval_s": (2, 60), "history_limit": (10, 500),
                                     "detail_limit": (5, 200),
                                     "refresh_delay_ms": (0, 5000)}}
AI_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
SCHEDULE_KEYS = ("enabled", "time", "days")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_LOCK = threading.Lock()


class SettingsError(ValueError):
    """Invalid settings payload (HTTP 400)."""


class RevisionConflict(Exception):
    """Settings changed since the caller loaded them (HTTP 409)."""


class ApplyError(RuntimeError):
    """The timer could not be updated; settings were rolled back (HTTP 500)."""


# Retired keys still found in saved files; dropped instead of failing validation.
LEGACY_KEYS = {"machines": {"terminal_targets"}}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_raw() -> bytes | None:
    try:
        return SETTINGS_PATH.read_bytes()
    except FileNotFoundError:
        return None


def revision_of(raw: bytes | None) -> str:
    return hashlib.sha256(raw or b"defaults").hexdigest()[:16]


def load() -> dict:
    """Effective settings: defaults overlaid with the stored file.

    A corrupt or invalid file falls back to defaults so the routine still runs;
    ``status()`` surfaces the problem in the UI.
    """
    raw = _read_raw()
    if raw is None:
        return copy.deepcopy(DEFAULTS)
    try:
        return validate(json.loads(raw))
    except (ValueError, TypeError):
        return copy.deepcopy(DEFAULTS)


def validate(data: Any) -> dict:
    """Return full effective settings for a (possibly partial) payload."""
    if not isinstance(data, dict):
        raise SettingsError("settings must be an object")
    unknown = set(data) - {"schema", *DEFAULTS}
    if unknown:
        raise SettingsError(f"unknown settings group(s): {', '.join(sorted(unknown))}")
    if data.get("schema", 1) != 1:
        raise SettingsError("unsupported settings schema")
    routine = data.get("routine", {})
    if not isinstance(routine, dict):
        raise SettingsError("routine must be an object")
    unknown = set(routine) - set(DEFAULTS["routine"])
    if unknown:
        raise SettingsError(f"unknown routine setting(s): {', '.join(sorted(unknown))}")
    for key in ("enabled", "auto_restart"):
        if key in routine and not isinstance(routine[key], bool):
            raise SettingsError(f"routine.{key} must be true or false")
    if "time" in routine and (not isinstance(routine["time"], str)
                              or not TIME_RE.match(routine["time"])):
        raise SettingsError("routine.time must be HH:MM (24-hour)")
    for key, (low, high) in INT_RANGES.items():
        if key in routine:
            value = routine[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise SettingsError(f"routine.{key} must be an integer {low}-{high}")
    if "days" in routine:
        days = routine["days"]
        if not isinstance(days, list) or not days \
                or any(d not in WEEK_DAYS for d in days) or len(set(days)) != len(days):
            raise SettingsError("routine.days must be a non-empty list of distinct "
                                + ", ".join(WEEK_DAYS))
        routine = {**routine, "days": [d for d in WEEK_DAYS if d in days]}
    if "steps" in routine:
        steps = routine["steps"]
        if not isinstance(steps, dict) or set(steps) - set(ROUTINE_STEPS) \
                or any(not isinstance(v, bool) for v in steps.values()):
            raise SettingsError(f"routine.steps maps {', '.join(ROUTINE_STEPS)} to true/false")
    extra = {"routine": routine}
    for group in ("ai", "machines", "notify", "live_updates"):
        values = data.get(group, {})
        if isinstance(values, dict) and LEGACY_KEYS.get(group):
            values = {k: v for k, v in values.items() if k not in LEGACY_KEYS[group]}
        extra[group] = _validate_group(group, values)
    return _merge(DEFAULTS, extra)


def _validate_group(group: str, values: Any) -> dict:
    if not isinstance(values, dict):
        raise SettingsError(f"{group} must be an object")
    unknown = set(values) - set(DEFAULTS[group])
    if unknown:
        raise SettingsError(f"unknown {group} setting(s): {', '.join(sorted(unknown))}")
    for key, value in values.items():
        default = DEFAULTS[group][key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise SettingsError(f"{group}.{key} must be true or false")
        elif isinstance(default, int):
            low, high = GROUP_INT_RANGES[group][key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise SettingsError(f"{group}.{key} must be an integer {low}-{high}")
        elif isinstance(default, list):
            if not isinstance(value, list) or len(value) > 16 or len(set(value)) != len(value) \
                    or any(not isinstance(t, str) or not TARGET_RE.match(t) for t in value):
                raise SettingsError(f"{group}.{key} must be a list of distinct machine names")
    if group == "ai":
        if "model" in values and (not isinstance(values["model"], str)
                                  or not AI_MODEL_RE.match(values["model"])):
            raise SettingsError("ai.model must be a model id such as fusion")
    return values


def on_calendar(routine: dict) -> str:
    chosen = routine["days"]
    days = "" if len(chosen) == len(WEEK_DAYS) else ",".join(chosen) + " "
    return f"{days}*-*-* {routine['time']}:00"


def dropin_text(routine: dict) -> str:
    return ("# Managed by the Team Portal Settings section (portal_settings.py).\n"
            "# Edit the schedule in the portal, not here.\n"
            "[Timer]\nOnCalendar=\n"
            f"OnCalendar={on_calendar(routine)}\n")


def systemctl(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                          text=True, timeout=timeout, check=False)


def apply_schedule(routine: dict) -> str:
    """Write the drop-in and (re)arm or disable the timer. Raises ApplyError."""
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    tmp = DROPIN.with_suffix(".conf.tmp")
    tmp.write_text(dropin_text(routine), encoding="utf-8")
    os.replace(tmp, DROPIN)
    steps = [("daemon-reload",)]
    steps += [("enable", TIMER), ("restart", TIMER)] if routine["enabled"] \
        else [("disable", "--now", TIMER)]
    for args in steps:
        out = systemctl(*args)
        if out.returncode != 0:
            raise ApplyError(f"systemctl --user {' '.join(args)} failed: "
                             + (out.stderr or out.stdout).strip()[-300:])
    return (f"timer armed for {on_calendar(routine)}" if routine["enabled"]
            else "timer disabled")


def _write(raw: bytes | None) -> None:
    if raw is None:
        SETTINGS_PATH.unlink(missing_ok=True)
        return
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, SETTINGS_PATH)


def changes(old: dict, new: dict, prefix: str = "") -> list[str]:
    out = []
    for key in new:
        if isinstance(new[key], dict):
            out += changes(old.get(key) or {}, new[key], f"{prefix}{key}.")
        elif old.get(key) != new[key]:
            out.append(f"{prefix}{key}: {json.dumps(old.get(key))} -> {json.dumps(new[key])}")
    return out


def save(update: Any, revision: str) -> dict:
    """Validate, revision-check, persist and apply. Returns a result dict."""
    if not isinstance(revision, str) or not revision:
        raise SettingsError("revision is required")
    with _LOCK:
        old_raw = _read_raw()
        if revision != revision_of(old_raw):
            raise RevisionConflict("settings changed since they were loaded; reload and retry")
        current = load()
        if not isinstance(update, dict):
            raise SettingsError("settings must be an object")
        merged = validate(_merge(current, update))
        diff = changes(current, merged)
        if not diff:
            return {"changed": [], "applied": "no change", "revision": revision}
        new_raw = (json.dumps(merged, indent=2, sort_keys=True) + "\n").encode()
        _write(new_raw)
        applied = "schedule unchanged"
        if any(current["routine"][k] != merged["routine"][k] for k in SCHEDULE_KEYS) \
                or not DROPIN.is_file():
            try:
                applied = apply_schedule(merged["routine"])
            except (ApplyError, OSError, subprocess.SubprocessError) as error:
                _write(old_raw)
                try:
                    apply_schedule(current["routine"])
                except (ApplyError, OSError, subprocess.SubprocessError):
                    pass
                raise ApplyError(f"{error}; settings rolled back") from error
        return {"changed": diff, "applied": applied, "revision": revision_of(new_raw)}


def _timer_status() -> dict:
    try:
        out = systemctl("show", TIMER, "-p", "ActiveState", "-p", "UnitFileState",
                        "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec", timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        return {"error": str(error)}
    props = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    return {"active": props.get("ActiveState") or "unknown",
            "enabled": props.get("UnitFileState") or "unknown",
            "next": props.get("NextElapseUSecRealtime") or "",
            "last": props.get("LastTriggerUSec") or ""}


def _linger() -> str:
    try:
        out = subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger",
                              "--value"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _last_run() -> dict | None:
    try:
        brief = json.loads(BRIEF_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run = brief.get("run") or {}
    return {"generated_at": brief.get("generated_at"), "status": run.get("status"),
            "journal_id": run.get("journal_id")}


def status() -> dict:
    raw = _read_raw()
    problem = None
    if raw is not None:
        try:
            validate(json.loads(raw))
        except (ValueError, TypeError) as error:
            problem = f"stored settings invalid, defaults in effect: {error}"
    return {"timer": _timer_status(), "linger": _linger(), "last_run": _last_run(),
            "managed_schedule": DROPIN.is_file(), "problem": problem}


def payload(include_status: bool = True) -> dict:
    out = {"settings": load(), "defaults": copy.deepcopy(DEFAULTS),
           "revision": revision_of(_read_raw()),
           "limits": {k: list(v) for k, v in INT_RANGES.items()},
           "group_limits": {g: {k: list(v) for k, v in r.items()}
                            for g, r in GROUP_INT_RANGES.items()}}
    if include_status:
        out["status"] = status()
    return out
