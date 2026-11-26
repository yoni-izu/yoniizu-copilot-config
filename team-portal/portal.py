#!/usr/bin/env python3
"""Team Portal dashboard and editable local server.

Extracted from task-manager/tm.py. Task and review business logic stays in
tm.py (referenced here as ``tm.<name>``); this module owns the portal runtime:
HTML template/rendering, payload aggregation, the HTTP server and its jobs.

    portal.py serve [--port N] [--stop] [--status]
    portal.py dashboard [-o PATH]
"""
import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import urlparse, parse_qs

PORTAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL_ROOT.parent / "lib"))
from private_paths import load_private_json, private_root  # noqa: E402

STATE_DIR = private_root() / "team-portal" / "state"
_DEPENDENCIES = private_root() / "team-portal" / "python"
if _DEPENDENCIES.is_dir():
    sys.path.insert(0, str(_DEPENDENCIES))
from markdown_it import MarkdownIt  # noqa: E402
from mdit_py_plugins.tasklists import tasklists_plugin  # noqa: E402

JIRA_SITE = os.environ.get(
    "JIRA_SITE", load_private_json("config/jira.json").get("site", "")).rstrip("/")
JIRA_BASE = f"{JIRA_SITE}/browse/" if JIRA_SITE else ""
_SKILLS = str(PORTAL_ROOT.parent / "skills")
sys.path.insert(0, os.path.join(_SKILLS, "task-manager"))
sys.path.insert(0, os.path.join(_SKILLS, "workspace-manager"))
import tm  # noqa: E402
import rtl_pr_guard  # noqa: E402  (tm.py puts rtl-vm-policy on sys.path)

import wm  # noqa: E402
import team_api  # noqa: E402
from action_queue import ActionQueue  # noqa: E402
import maintenance  # noqa: E402
import portal_settings  # noqa: E402
import terminal  # noqa: E402
import sso_login  # noqa: E402
import events  # noqa: E402
import domain_watch  # noqa: E402
import power_schedule  # noqa: E402
import live_status  # noqa: E402
import ai  # noqa: E402
import jobs_view  # noqa: E402

# bug-monitor owns the HSI bug store. The portal dashboard renders its Bugs
# section from it; the portal never touches ~/tasks/bugs.json directly.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/bug-monitor"))
try:
    import bm  # noqa: E402
except ImportError:      # bug-monitor not installed -> portal hides the section
    bm = None

# bug-triage owns the phenomenon registry; both it and bug-monitor persist to
# the shared ~/tasks/bugs.db through bugdb.py.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/bug-triage"))
try:
    import ph  # noqa: E402
    import bugdb  # noqa: E402
except ImportError:      # bug-triage not installed -> Phenomena tab renders empty
    ph = None
    bugdb = None

# pr-monitor owns outgoing feature tracking. Its refresh runs `gh` only,
# so the portal's scan button costs nothing.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/pr-monitor"))
try:
    import pm  # noqa: E402
except ImportError:
    pm = None

# pr-failure-analyzer owns CI-failure knowledge and the sightings store
# (~/tasks/pr_failures.db); the portal records triage verdicts through it.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/pr-failure-analyzer"))
try:
    import pfa  # noqa: E402
except ImportError:      # skill not installed -> triage runs, no sightings
    pfa = None

# team-planner owns the RAID log (database canonical); the portal uses the rd.py owner API only.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/team-planner"))
try:
    import rd  # noqa: E402
except ImportError:
    rd = None

# vd.py (vm-delegate skill) keeps the history of every job mctl delegated to a
# VM / lab machine; refreshing it is one ssh round trip per target, no tokens.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/vm-delegate"))
try:
    import vd  # noqa: E402
except ImportError:
    vd = None

# ----------------------------------------------------------------------------
# Constants & helpers
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# Subcommand: dashboard / serve (HTML view; serve adds editing via a local API)
# ----------------------------------------------------------------------------

DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Task Dashboard</title>
__STYLES__
</head>
<body>
<div class="portal-shell" id="portal-shell">
<aside class="portal-sidebar">
  <div class="portal-brand">
    <span class="portal-mark" aria-hidden="true">✦</span>
    <span class="portal-brand-copy"><b>TEAM PORTAL</b><small>HSI FIRMWARE</small></span>
  </div>
  <nav class="sections" id="sections" aria-label="Portal sections"></nav>
  <div class="sidebar-footer">
    <div class="orbit-card" title="Local stores only — no Jira or GitHub calls on render">
      <span class="orbit-icon" aria-hidden="true">◉</span>
      <span><b>LOCAL STORES</b><small>Generated __GENERATED__</small></span>
      <i aria-hidden="true"></i>
    </div>
    <button type="button" class="nav-collapse" id="nav-collapse" aria-label="Collapse navigation">
      <span aria-hidden="true">⇤</span><span class="collapse-label">Collapse navigation</span>
    </button>
  </div>
</aside>
<div class="portal-main">
<header class="portal-topbar">
  <div class="topbar-heading">
    <span class="topbar-orbit" id="active-section-icon" aria-hidden="true">◈</span>
    <div>
      <h1><span id="active-section-title">Overview</span> <span id="modebadge" class="mode ro">read-only</span></h1>
      <div class="sub">Generated __GENERATED__ · <span id="total"></span></div>
    </div>
  </div>
  <div class="topbar-tools">
    <div class="aq-anchor" id="updates-anchor">
      <button class="aq-card" id="updates-open" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="updates-panel" hidden>
        <span class="aq-text"><small>UPDATES</small><b id="updates-summary">No updates yet</b></span>
        <span class="aq-caret" aria-hidden="true">▾</span>
      </button>
      <div id="updates-panel" hidden>
        <div class="ab-head">
          <b>Recent updates</b>
          <button id="updates-clear" type="button" class="ab-link">Clear</button>
          <button id="updates-close" type="button" class="ab-close" aria-label="Dismiss updates">&times;</button>
        </div>
        <div id="updates-list"></div>
        <div id="updates-detail"></div>
      </div>
    </div>
    <div class="aq-anchor" id="aq-anchor">
      <button class="aq-card" id="action-queue-open" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="align-banner" hidden>
        <span class="aq-spin" id="aq-spin" aria-hidden="true"></span>
        <span class="aq-text"><small>ACTIONS</small><b id="aq-summary">Idle</b></span>
        <span class="aq-caret" aria-hidden="true">▾</span>
      </button>
      <div id="align-banner">
        <div class="ab-head">
          <span id="align-spin" class="ab-spin"></span>
          <b id="align-title">Aligning workspace…</b>
          <button id="align-toggle" type="button" class="ab-toggle" aria-expanded="true" aria-controls="align-body action-queue">Collapse</button>
          <button id="align-close" class="ab-close" aria-label="Dismiss">&times;</button>
        </div>
        <div id="action-queue"></div>
        <div id="align-body"></div>
      </div>
    </div>
    <div class="clock-chip"><span aria-hidden="true">◷</span><time id="local-clock"></time><small>LOCAL</small></div>
    <div class="refresh-wrap">
      <button class="refresh ai-btn" id="ai-open" type="button" hidden aria-haspopup="dialog"><span class="ico" aria-hidden="true">✦</span> AI</button>
      <button class="refresh cmdk-btn" id="cmdk-open" title="Jump to anything (Ctrl/⌘+K)"><span class="ico">⌕</span> Jump <kbd>⌘K</kbd></button>
      <button class="refresh" id="refresh" title="Reload entry values without losing filters, tab, or sort"><span class="ico">↻</span> Refresh</button>
      <div class="refresh-note" id="refresh-note"></div>
    </div>
  </div>
</header>
<div class="wrap">

  <div id="sec-overview">
    <div class="rpt-head">
      <div>
        <h2>HSI Firmware — team report</h2>
        <p class="sub" id="ov-asof"></p>
      </div>
    </div>

    <div class="ov-canvas">
      <div class="tile t12 brief-tile" style="--tk:#118DFF;" data-overview-tile="brief">
        <h3>Morning brief</h3>
        <p class="cap" id="ov-brief-cap"></p>
        <div id="ov-brief"></div>
      </div>
      <div class="fresh-strip t12" id="ov-fresh" aria-label="How old each data source is"></div>
      <div class="tile t12 pulse-tile" style="--tk:#1AAB40;" data-overview-tile="pulse">
        <h3>Live pulse</h3>
        <p class="cap" id="ov-pulse-cap"></p>
        <div class="pulse-grid" id="ov-pulse"></div>
      </div>
      <div class="tile t12 chg-tile" style="--tk:#9E8FE9;" data-overview-tile="changes">
        <h3>What changed since your last visit</h3>
        <p class="cap" id="ov-chg-cap"></p>
        <div id="ov-changes"></div>
      </div>
    </div>
  </div><!-- /sec-overview -->

  <div id="sec-work" style="display:none;">
  <div class="stats" id="stats"></div>

  <div class="controls">
    <div class="row">
      <div class="grp search-wrap">
        <span class="lbl">Search</span>
        <input type="search" id="q" placeholder="Search title, description, jira, context, notes…">
      </div>
      <div class="grp">
        <span class="lbl">Status</span>
        <div class="dd" id="dd-status">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-status"></div>
        </div>
      </div>
      <div class="grp" id="grp-category">
        <span class="lbl">Category</span>
        <div class="dd" id="dd-category">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-category"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Priority</span>
        <div class="dd" id="dd-priority">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-priority"></div>
        </div>
      </div>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="grp">
        <span class="lbl">Complexity</span>
        <div class="dd" id="dd-complexity">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-complexity"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Workspace</span>
        <div class="dd" id="dd-workspace">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-workspace"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Sort by</span>
        <select id="sort">
          <option value="priority">Priority</option>
          <option value="created">Created (newest)</option>
          <option value="worked">Last worked (recent)</option>
          <option value="age">Age (oldest)</option>
          <option value="cost">Est. cost</option>
          <option value="id">ID</option>
        </select>
      </div>
      <div class="grp">
        <span class="lbl">Options</span>
        <div class="chips">
          <label class="chip on"><input type="checkbox" id="o-hidedone" checked> Hide done/cancelled</label>
          <label class="chip"><input type="checkbox" id="o-jira"> Has Jira</label>
          <label class="chip"><input type="checkbox" id="o-ws"> Has workspace</label>
          <label class="chip"><input type="checkbox" id="o-review"> Code-review only</label>
        </div>
      </div>
    </div>
    <div class="row" id="row-review" style="margin-top:12px;">
      <div class="grp">
        <span class="lbl">Review status</span>
        <div class="dd" id="dd-reviewstatus">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-reviewstatus"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Author</span>
        <div class="dd" id="dd-author">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-author"></div>
        </div>
      </div>
    </div>
    <div class="toolbar">
      <button id="task-bulk-edit" class="btn" style="display:none;" disabled>Edit selected…</button>
      <button id="review-scan" class="btn" style="display:none;" disabled title="Scan unresolved PRs belonging only to selected active code-review tasks.">↻ Scan selected reviews</button>
      <span class="ro-note" id="review-scan-note" style="display:none;"></span>
      <div class="grp" style="flex-direction:row; align-items:center; gap:8px;">
        <span class="lbl" style="color:var(--muted);font-size:11px;">Columns</span>
        <div class="dd" id="dd-cols">
          <button type="button" class="dd-btn"><span class="dd-label">Columns</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="cols"></div>
        </div>
      </div>
      <button class="link" id="reset" style="margin-left:auto;">Reset filters</button>
    </div>
  </div>

  <div id="task-selectionbar" class="task-selectionbar" aria-live="polite"></div>
  <div class="count" id="count"></div>
  <table id="tbl">
    <thead><tr id="head"></tr></thead>
    <tbody id="body"></tbody>
  </table>
  </div><!-- /sec-work -->

  <div id="sec-ws" style="display:none;">
    <div id="wsview"></div>
  </div>

  <div id="sec-bugs" style="display:none;">
    <nav class="tabs" id="bug-tabs"></nav>
    <div class="stats" id="bug-stats"></div>

    <div class="bug-controls" id="bug-controls">
      <div class="row">
        <div class="grp search-wrap">
          <span class="lbl">Search</span>
          <input type="search" id="bq" placeholder="Search key, summary, description, root cause, notes…">
        </div>
        <div class="grp">
          <span class="lbl">Signal</span>
          <div class="chips" id="bug-signals"></div>
        </div>
      </div>
      <div class="row" style="margin-top:12px;">
        <div class="grp">
          <span class="lbl">Owner</span>
          <select id="b-owner" data-filter><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Priority</span>
          <select id="b-priority" data-filter><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Component</span>
          <select id="b-component" data-filter><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Family</span>
          <select id="b-family" data-filter><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Evidence</span>
          <select id="b-evidence" data-filter title="bug-triage evidence linked to the bug">
            <option value="">All</option>
            <option value="ph">🔬 Linked phenomenon</option>
            <option value="note">📄 Investigation note</option>
            <option value="mention">… Mentioned in a note</option>
            <option value="any">Any evidence</option>
            <option value="none">No evidence</option>
          </select>
        </div>
        <div class="grp">
          <span class="lbl">Origin</span>
          <select id="b-origin" data-filter title="Who raised the bug: the customer (aqua-external, customer sightings) or internal">
            <option value="">All</option>
            <option value="customer">Customer</option>
            <option value="internal">Internal</option>
          </select>
        </div>
        <div class="grp">
          <span class="lbl">Sort by</span>
          <select id="b-sort">
            <option value="signal">Signal (worst first)</option>
            <option value="idle">Idle (longest)</option>
            <option value="age">Age (oldest)</option>
            <option value="pings">Pings from Oded (most)</option>
            <option value="evidence">Triage evidence (most)</option>
            <option value="priority">Priority</option>
            <option value="key">Key</option>
          </select>
        </div>
        <div class="grp">
          <span class="lbl">Options</span>
          <div class="chips">
            <label class="chip on"><input type="checkbox" id="b-openonly" checked> Open only</label>
            <label class="chip"><input type="checkbox" id="b-untriaged"> Untriaged only</label>
            <label class="chip"><input type="checkbox" id="b-flagged"> Has hygiene flag</label>
            <label class="chip on"><input type="checkbox" id="b-hidemuted" checked> Hide muted</label>
          </div>
        </div>
      </div>
      <div class="toolbar">
        <button id="bug-scan" class="btn" title="Re-read every in-scope bug from Jira into the local mirror. No model, no tokens.">↻ Scan Jira</button>
        <span class="ro-note" id="bug-scanline"></span>
        <button class="link" id="b-reset" style="margin-left:auto;">Reset filters</button>
      </div>
    </div>

    <div class="bulk-bar" id="b-bulk" hidden>
      <b id="b-bulk-n"></b>
      <button class="ci-btn" id="b-bulk-ping" title="Record one more chase from Oded on every ticked bug">📣 +1 ping</button>
      <select id="b-bulk-state" title="Set the local triage state on every ticked bug (never Jira)"></select>
      <button class="ci-btn" id="b-bulk-clear">clear</button>
      <span class="bulk-msg" id="b-bulk-msg"></span>
    </div>
    <div class="count" id="bug-count"></div>
    <table id="bug-tbl">
      <thead><tr id="bug-head"></tr></thead>
      <tbody id="bug-body"></tbody>
    </table>
    <div id="famview" style="display:none;"></div>
    <div id="phview" style="display:none;"></div>
  </div><!-- /sec-bugs -->

  <div id="sec-prs" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>My feature PRs</h2>
        <p class="sub" id="pr-asof"></p>
      </div>
      <div class="slicer">
        <button id="pr-collapse" class="btn" title="Collapse or expand the PR rows of every feature">⇱ Collapse all</button>
        <button id="pr-track" class="btn" title="Track a new feature by hand: a name, an optional description, and its PRs">＋ Track feature</button>
        <div class="dd pe-menu" title="Open the CI/CD pipeline explorer (Oded's dashboard) on a view"><button type="button" class="dd-btn"><span class="dd-label">⇢ Pipelines</span><span class="dd-caret">▾</span></button><div class="dd-panel" id="pe-menu"></div></div>
        <button id="pr-scan" class="btn primary" title="Re-probe tracked PRs, sync opted-in Jira tickets assigned to you, retry archived Jira syncs, and retire fully merged features. No model, no tokens.">↻ Scan PRs</button>
        <button id="pr-scan-sel" class="btn" hidden title="Re-probe selected features and sync their opted-in Jira tickets; archived Jira retries may also run">↻ Scan selected PRs</button>
        <span id="pr-scan-note" class="ro-note"></span>
      </div>
    </div>
    <div class="kpis" id="pr-kpis"></div>
    <div class="controls pr-controls">
      <div class="row">
        <div class="grp search-wrap">
          <span class="lbl">Search</span>
          <input id="pr-q" type="search" placeholder="feature, description, repo">
        </div>
        <div class="grp">
          <span class="lbl">Feature status</span>
          <div class="dd" id="dd-pr-feature">
            <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
            <div class="dd-panel" id="pf-feature"></div>
          </div>
        </div>
        <div class="grp">
          <span class="lbl">Priority</span>
          <div class="dd" id="dd-pr-priority">
            <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
            <div class="dd-panel" id="pf-priority"></div>
          </div>
        </div>
        <div class="grp">
          <span class="lbl">PLDM</span>
          <div class="dd" id="dd-pr-pldm">
            <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
            <div class="dd-panel" id="pf-pldm"></div>
          </div>
        </div>
        <div class="grp">
          <span class="lbl">Repository</span>
          <div class="dd" id="dd-pr-repo">
            <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
            <div class="dd-panel" id="pf-repo"></div>
          </div>
        </div>
        <div class="grp">
          <span class="lbl">PR status</span>
          <div class="dd" id="dd-pr-prStatus">
            <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
            <div class="dd-panel" id="pf-prStatus"></div>
          </div>
        </div>
      </div>
    </div>
    <div class="count" id="pr-count"></div>
    <table id="pr-feature-tbl">
      <thead>
        <tr>
          <th class="prf-sel"><input type="checkbox" id="pr-sel-all" title="Select all shown features"></th>
          <th style="width:46%">Feature</th>
          <th style="width:11%">Priority</th>
          <th style="width:15%">State</th>
          <th style="width:9%">PRs</th>
          <th style="width:19%">Actions</th>
        </tr>
      </thead>
      <tbody id="pr-body"></tbody>
    </table>
  </div><!-- /sec-prs -->

  <div id="sec-vm" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>Machines</h2>
        <p class="sub" id="vm-asof"></p>
      </div>
      <div class="slicer">
        <select id="vm-target" data-filter><option value="">All targets</option></select>
        <select id="vm-source" data-filter title="Which runner wrote the job record: mctl delegation, the local fw-flash runner, the local job runner (pilot or ad-hoc), or bmcjob on a BMC">
          <option value="">All sources</option>
          <option value="delegated">Delegated (mctl)</option>
          <option value="flash">Flash (local runner)</option>
          <option value="pilot-local">Local job (pilot / ad-hoc)</option>
          <option value="pilot-bmc">Pilot bmcjob (BMC)</option>
        </select>
        <label class="chip"><input type="checkbox" id="vm-livefirst" checked> Live on top</label>
        <button id="vm-add" class="btn">+ Add machine</button>
        <button id="vm-console" class="btn" title="Open several machine shells, BMCs and UART consoles at once: Windows Terminal split panes or a browser grid with shared input (broadcast).">&#8862; Console</button>
        <button id="vm-vms" class="btn" title="Query `octl vm list` for the power state, lease and IP of every EC2 instance. Does not touch the targets over ssh.">&#9741; Check VM status</button>
        <button id="vm-sync" class="btn" title="Mirror VM and lab-machine job logs into ~/.mctl/jobs. Each target keeps only the newest log per workspace and overwrites it on the next run, so syncing makes an old log openable later.">&#8595; Sync logs</button>
        <button id="vm-refresh" class="btn primary" title="Refresh job state and logs for every VM and lab-machine target, then re-read EC2 power state. One ssh round trip per machine plus an octl call. No model, no tokens.">&#8635; Refresh status</button>
        <span id="vm-refresh-note" class="ro-note"></span>
      </div>
    </div>
    <div id="vm-machines"></div>
    <div id="vm-now"></div>
    <div class="kpis" id="vm-kpis"></div>
    <table id="vm-tbl">
      <thead><tr>
        <th style="width:8%;">Target</th><th style="width:6%;">Kind</th>
        <th style="width:19%;">Workspace</th><th style="width:17%;">Command</th>
        <th style="width:12%;">Started</th><th style="width:7%;">Duration</th>
        <th style="width:19%;">Status</th><th style="width:12%;">Log</th>
      </tr></thead>
      <tbody id="vm-body"></tbody>
    </table>

  </div><!-- /sec-vm -->

  <div id="sec-raid" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>RAID log</h2>
        <p class="sub" id="raid-asof"></p>
      </div>
      <div class="slicer">
        <label class="chip"><input type="checkbox" id="raid-showclosed"> Show closed</label>
        <button id="raid-new" class="btn primary">+ New row</button>
      </div>
    </div>
    <div class="kpis" id="raid-kpis"></div>
    <div id="raid-form" class="raid-new" style="display:none;"></div>
    <table id="raid-tbl">
      <thead><tr>
        <th style="width:8%;">ID</th><th style="width:34%;">Title</th>
        <th style="width:9%;">RAG</th><th style="width:7%;">Score</th>
        <th style="width:11%;">Owner</th><th style="width:12%;">Due</th>
        <th style="width:19%;">Status</th>
      </tr></thead>
      <tbody id="raid-body"></tbody>
    </table>
  </div><!-- /sec-raid -->

  <div id="sec-team" style="display:none;">
    <div id="team-root"></div>
  </div>

  <div id="sec-settings" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>Settings</h2>
        <p class="sub">Portal settings are saved on the server and journaled; browser preferences stay in this browser only.</p>
      </div>
    </div>
    <div class="ov-canvas">
      <div class="tile t7" style="--tk:#118DFF;"><h3>Daily routine</h3>
        <p class="cap">Portal setting · applies to the maintenance timer and <code>routine.py</code>.</p>
        <div id="set-routine"></div></div>
      <div class="tile t5" style="--tk:#6B007B;"><h3>Routine status</h3>
        <p class="cap">Live from systemd and the latest morning brief.</p>
        <div id="set-status"></div></div>
      <div class="tile t12" style="--tk:#12239E;"><h3>AI, machines &amp; notifications</h3>
        <p class="cap">Portal setting · AI runs (Copilot CLI), browser terminals, the live ssh status link and pop-up notifications.</p>
        <div id="set-groups"></div></div>
      <div class="tile t12" style="--tk:#E66C37;"><h3>Display &amp; live behaviour</h3>
        <p class="cap">Browser preference · saved instantly in this browser.</p>
        <div id="set-prefs"></div></div>
    </div>
  </div>
</div><!-- /wrap -->
</div><!-- /portal-main -->
</div><!-- /portal-shell -->

<script>window.__PORTAL__ = __BOOTDATA__;</script>
__SCRIPTS__
</body>
</html>
"""


def _annotate_tasks(tasks: list[dict], wd: dict | None = None) -> list[dict]:
    """Return shallow task copies annotated with a transient ``workspace`` slot.

    The task store no longer holds a ``workspace`` field — occupancy lives in the
    workspace-manager store. The dashboard's task views still read ``t.workspace``
    for display, so we resolve each task's slot from the wm store at render time.
    """
    wd = wd if wd is not None else wm.load()
    tid_slot = {h["task_id"]: s for s, h in wm.all_holders(wd).items()
                if h.get("type") == "task"}
    out = []
    for t in tasks:
        c = dict(t)
        c["revision"] = tm.task_revision(t)
        c["workspace"] = tid_slot.get(t["id"])
        out.append(c)
    return out


MORNING_BRIEF = STATE_DIR / "morning-brief.json"


def _read_morning_brief() -> dict | None:
    """Latest daily-maintenance brief (written by routine.py). Derived, never
    authoritative; a missing file means no run has completed yet."""
    try:
        data = json.loads(MORNING_BRIEF.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        return {"error": f"Morning brief unreadable: {error}"}
    if not isinstance(data, dict) or data.get("schema") != 1:
        return {"error": "Morning brief has an unsupported format"}
    return data


def overview_payload() -> dict:
    """Overview data that no other section serves: the routine's morning brief
    (including its pulse snapshot). The live pulse itself is computed in the
    browser by assets/pulse.js from the other sections' payloads."""
    return {"brief": _read_morning_brief(),
            "today": date.today().isoformat(), "errors": {}}


def bug_payload() -> dict:
    """Bugs section data. Empty shell when bug-monitor isn't installed."""
    if bm is None:
        return {"bugs": [], "families": [], "config": {}, "last_scan": None}
    try:
        return bm.dashboard_payload()
    except Exception as e:                       # never let a bug store break the portal
        return {"bugs": [], "families": [], "config": {}, "last_scan": None,
                "error": str(e)}


def triage_payload() -> dict:
    """Bug-triage registry (phenomena + evidence + investigation index)."""
    empty = {"phenomena": [], "evidence": {"bugs": {}, "phenomena": {}},
             "investigations": [], "statuses": [], "severities": [],
             "platforms": [], "signal_icon": {}}
    if ph is None:
        return empty
    try:
        return ph.portal_payload()
    except Exception as e:                       # never let the registry break the portal
        return {**empty, "error": str(e)}


def pr_payload() -> dict:
    """Outgoing feature-PR data from pr-monitor. Read-only; the scan button is
    what refreshes it."""
    if pm is None:
        return {"features": [], "counts": {}, "last_scan": None}
    try:
        return pm.payload()
    except Exception as e:
        return {"features": [], "counts": {}, "last_scan": None, "error": str(e)}


def pr_track_edit(path: str, payload: dict) -> tuple[bool, str]:
    """Apply one tracking edit through the PR domain owner.

    A multi-PR add reports per-reference failures rather than aborting: one
    mistyped line should not throw away the references that did resolve.
    """
    fid = payload.get("feature_id")
    if path == "/api/feature-jira-coverage":
        if type(fid) is not int:
            return False, "feature_id must be an integer"
        if not isinstance(payload.get("key"), str):
            return False, "key must be a string"
        if type(payload.get("confirmed")) is not bool:
            return False, "confirmed must be a boolean"
        if type(payload.get("expected_revision")) is not int:
            return False, "expected_revision must be an integer"
        return pm.confirm_jira(
            fid, payload["key"], confirmed=payload["confirmed"],
            expected_revision=payload["expected_revision"])
    if path == "/api/feature-track":
        ok, msg, _ = pm.add_feature(payload.get("name", ""),
                                    payload.get("description", ""),
                                    payload.get("refs") or [])
        return ok, msg
    if not isinstance(fid, int):
        return False, "a feature id is required"
    if path == "/api/feature-untrack":
        return pm.drop_feature(fid)
    if path == "/api/feature-edit":
        fields = {k: payload.get(k) for k in ("name", "priority", "summary", "jira", "description")}
        fields = {k: v for k, v in fields.items() if isinstance(v, str)}
        note = payload.get("note") if isinstance(payload.get("note"), str) else ""
        if not fields and not note.strip():
            return False, "nothing to edit"
        msgs = []
        if fields:
            ok, msg = pm.edit_feature(
                fid, **fields, expected_revision=payload.get("expected_revision"))
            if not ok:
                return ok, msg
            msgs.append(msg)
        if note.strip():
            ok, msg = pm.add_note(fid, note, payload.get("note_label") or "")
            if not ok:
                return ok, msg
            msgs.append(msg)
        return True, " \u00b7 ".join(msgs)
    if path == "/api/pr-untrack":
        num = payload.get("number")
        if not isinstance(num, int):
            return False, "a PR number is required"
        return pm.remove_pr(fid, num, payload.get("repo") or "")

    refs = payload.get("refs") or []
    if not refs:
        return False, "no PR reference given"
    added, bad = 0, []
    for ref in refs:
        ok, msg = pm.add_pr(fid, str(ref))
        added += ok
        if not ok:
            bad.append(msg)
    note = f"[{fid}] +{added} PR(s)"
    return (added > 0, note + " \u00b7 " + "; ".join(bad)) if bad else (True, note)


def raid_payload() -> dict:
    """RAID rows from team-planner. Editable from the portal via rd.apply_edit."""
    if rd is None:
        return {"rows": [], "owners": [], "counts": {}, "path": ""}
    try:
        return rd.payload()
    except Exception as e:
        return {"rows": [], "owners": [], "counts": {}, "path": "", "error": str(e)}


def vmlog_payload() -> dict:
    """Delegated VM/lab jobs from vd.py. Read-only; the probe is a separate POST."""
    if vd is None:
        return {"jobs": [], "running": [], "counts": {}, "targets": [], "machines": [],
                "machines_checked": "", "last_refresh": "", "log": "",
                "error": "vm-delegate unavailable"}
    try:
        body = vd.payload()
    except Exception as e:
        return {"jobs": [], "running": [], "counts": {}, "targets": [], "machines": [],
                "machines_checked": "", "last_refresh": "", "log": "", "error": str(e)}
    jobs_view.merge(body, portal_settings.load()["machines"])
    body["terminal_targets"] = list(vd.targets())
    body["live"] = live_payload()["links"]
    body["power_schedule"] = POWER_SCHEDULE.snapshot() if POWER_SCHEDULE else {
        "error": POWER_SCHEDULE_ERROR, "instances": {}}
    if POWER_SCHEDULE is not None:
        POWER_SCHEDULE.wake.set()
    return body


def is_pinned_slot(slot: str) -> bool:
    """True for slots whose manifest default revision is not main."""
    rec = next((r for r in wm.roster() if r["slot"] == slot), None)
    return bool(rec and rec["rev"] != "main")


def viable_slots() -> list[str]:
    """Tracked slots that can take a fresh multi-repo checkout.

    Pinned-revision slots are excluded: a PLDM build would have to re-point
    them at PR branches, clobbering the revision they exist to hold.
    """
    return [r["slot"] for r in wm.roster() if not r["target"]
            and r["rev"] == "main" and r["exists"] and wm.allows(r, "build")]


def ws_ui_payload(wd: dict, *, cached_only: bool = False) -> dict:
    """Workspace state plus the tracked roster and its groups.

    ``wm.load()`` only records slots that have *ever* been held, so a UI built
    from it alone can't tell a free slot from one that doesn't exist.
    """
    roster = wm.roster(wd, cached_only=cached_only)
    canonical = ([r["slot"] for r in roster if not r["target"]
                  and r["rev"] == "main" and r["exists"] and wm.allows(r, "build")]
                 if cached_only else viable_slots())
    return dict(wd, canonical=canonical, purposes=wm.PURPOSES, workspace_terminals=True,
                purpose_uses={p: wm.purpose_uses(p) for p in wm.PURPOSES},
                roster=roster, groups=wm.groups(wd), targets=wm.machine_targets())


ASSETS = Path(__file__).resolve().parent / "assets"
ASSET_TYPES = {".css": "text/css; charset=utf-8",
               ".js": "application/javascript; charset=utf-8"}
# Every servable asset, by exact name, so a crafted path cannot escape.
ASSET_NAMES = ("portal.css", "pulse.js", "portal.js", "team.js", "term.js", "console.js",
               "aichat.js", "flash.js",
               "vendor/xterm.js", "vendor/xterm.css", "vendor/addon-fit.js")


def asset_text(name: str) -> str:
    """Read an asset off disk. Never cached, so an edit shows up on reload."""
    return (ASSETS / name).read_text(encoding="utf-8")


def render_aichat_page() -> str:
    """The detached AI panel window (assets/aichat.js); the query names the chat."""
    v = asset_stamp()
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Portal AI</title>'
            f'<link rel="stylesheet" href="/assets/portal.css?v={v}"></head>'
            '<body class="aichat-page"><main id="aichat-root" class="aichat-window"></main>'
            f'<script src="/assets/aichat.js?v={v}"></script>'
            '<script>PortalAIChat.mountFromQuery(document.getElementById("aichat-root"));'
            '</script></body></html>')


def render_term_page() -> str:
    """The pop-up terminal window (xterm.js + assets/term.js)."""
    v = asset_stamp()
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Portal terminal</title>'
            f'<link rel="stylesheet" href="/assets/vendor/xterm.css?v={v}">'
            '<style>html,body{margin:0;height:100%;background:#0f1419;color:#d6dde6;'
            'font:13px system-ui,sans-serif}body{display:flex;flex-direction:column}'
            'header{display:flex;gap:10px;align-items:center;padding:6px 10px;'
            'border-bottom:1px solid #26303b}#term-title{font-weight:600;flex:1;'
            'overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
            '.term-status{opacity:.85}.term-status.ok{color:#7ee787}.term-status.bad{color:#ff7b72}'
            'button{background:#21262d;color:inherit;border:1px solid #3a4450;border-radius:6px;'
            'padding:3px 10px;cursor:pointer}#term{flex:1;min-height:0;padding:4px}</style>'
            '</head><body><header><span id="term-title">Terminal</span>'
            '<span id="term-status" class="term-status" role="status" aria-live="polite"></span>'
            '<button id="term-again" type="button" hidden>Reconnect</button></header>'
            '<div id="term" aria-label="Terminal"></div>'
            f'<script src="/assets/vendor/xterm.js?v={v}"></script>'
            f'<script src="/assets/vendor/addon-fit.js?v={v}"></script>'
            f'<script src="/assets/term.js?v={v}"></script></body></html>')


def render_console_page() -> str:
    """The multi-pane console window (xterm.js grid + assets/console.js)."""
    v = asset_stamp()
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Portal console</title>'
            f'<link rel="stylesheet" href="/assets/vendor/xterm.css?v={v}">'
            f'<link rel="stylesheet" href="/assets/portal.css?v={v}"></head>'
            '<body class="console-page"><header class="con-bar" id="con-bar"></header>'
            '<div class="con-banner" id="con-banner" role="status" aria-live="polite" hidden></div>'
            '<main class="con-grid" id="con-grid" aria-label="Console panes"></main>'
            f'<script src="/assets/vendor/xterm.js?v={v}"></script>'
            f'<script src="/assets/vendor/addon-fit.js?v={v}"></script>'
            f'<script src="/assets/console.js?v={v}"></script></body></html>')


_UART_CACHE: dict[str, tuple[float, dict]] = {}
UART_CACHE_S = 60.0


def uart_consoles(target: str, fresh: bool = False) -> dict:
    """``pilot -t <target> --json uart consoles`` (machine-pilot owns the registry)."""
    if vd is None or target not in vd.targets():
        raise terminal.TermError(f"'{target}' is not a registered mctl target")
    hit = _UART_CACHE.get(target)
    if hit and not fresh and time.time() - hit[0] < UART_CACHE_S:
        return hit[1]
    if not terminal.PILOT.exists():
        raise terminal.TermError("machine-pilot (pilot) is not installed")
    try:
        out = subprocess.run([str(terminal.PILOT), "-t", target, "--json", "uart", "consoles"],
                             capture_output=True, text=True, timeout=15, check=False)
        data = json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        raise terminal.TermError(f"pilot uart consoles failed: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("consoles"), list):
        raise terminal.TermError("pilot uart consoles failed: "
                                 + (out.stderr or out.stdout or "").strip()[-200:])
    body = {"target": target, "consoles": [c for c in data["consoles"] if isinstance(c, dict)],
            "hint": str(data.get("hint") or "")}
    _UART_CACHE[target] = (time.time(), body)
    return body


def sat_machine_payload(target: str) -> dict:
    """Read a registered lab target's SAT snapshot (SAT name = target host name) through machine-pilot."""
    if vd is None:
        raise RuntimeError("vm-delegate is not installed")
    try:
        row = vd.target_rows().get(target)
    except OSError as exc:
        raise RuntimeError(f"mctl target registry is unavailable: {exc}") from exc
    if not row or row.get("kind") != "remote":
        raise ValueError("SAT details require a registered lab-machine target")
    if not terminal.PILOT.exists():
        raise RuntimeError("machine-pilot is not installed")
    try:
        out = subprocess.run([str(terminal.PILOT), "-t", target, "sat", "details"],
                             capture_output=True, text=True, timeout=50, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("SAT lookup timed out; ownership is unverified") from exc
    except OSError as exc:
        raise RuntimeError(f"machine-pilot could not be started: {exc}") from exc
    if not out.stdout.strip():
        raise RuntimeError((out.stderr or "SAT lookup returned no data").strip()[-500:])
    try:
        data = json.loads(out.stdout)
    except ValueError as exc:
        raise RuntimeError("machine-pilot returned invalid SAT data") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("setup"), str)
            or not isinstance(data.get("requested_at"), str)
            or not isinstance(data.get("complete"), bool)
            or any(not isinstance(data.get(key), dict)
                   or not isinstance(data[key].get("ok"), bool)
                   or ("data" not in data[key] if data[key]["ok"]
                       else not isinstance(data[key].get("error"), str))
                   for key in ("device", "cards", "firmware"))):
        raise RuntimeError("machine-pilot returned an invalid SAT snapshot")
    if data["complete"] != all(data[key]["ok"] for key in ("device", "cards", "firmware")):
        raise RuntimeError("machine-pilot returned inconsistent SAT section status")
    if out.returncode and data["complete"]:
        raise RuntimeError("machine-pilot failed after returning a SAT snapshot")
    return {"target": target, **data}


def term_spec(data: dict) -> dict:
    """Validate a /api/term/open request into a terminal spec."""
    if not isinstance(data, dict):
        raise terminal.TermError("request must be an object")
    kind = data.get("kind")
    if kind == "shell":
        if vd is None:
            raise terminal.TermError("vm-delegate is not installed")
        return terminal.shell_spec(str(data.get("target") or ""), vd.targets(),
                                   str(data.get("session") or terminal.TMUX_SESSION))
    if kind == "workspace":
        ident = data.get("workspace")
        if not isinstance(ident, str) or not terminal.WORKSPACE_ID_RE.fullmatch(ident):
            raise terminal.TermError("invalid workspace id")
        try:
            workspace = wm.record(ident)
        except ValueError as error:
            raise terminal.TermError(str(error)) from error
        if workspace.get("id") != ident:
            raise terminal.TermError("workspace identity does not match the tracked record")
        if not workspace.get("path") and not workspace.get("target"):
            workspace["path"] = str(wm.slot_path(workspace["slot"]))
        if workspace.get("purpose") == "read-only" and data.get("read_only_confirmed") is not True:
            raise terminal.TermError("confirm the Read-only workspace warning before opening a terminal")
        if workspace.get("target") and vd is None:
            raise terminal.TermError("vm-delegate is not installed")
        return terminal.workspace_spec(workspace, vd.targets() if workspace.get("target") else [])
    if kind == "uart":
        target = str(data.get("target") or "")
        return terminal.uart_spec(target, str(data.get("console") or ""),
                                  uart_consoles(target)["consoles"])
    if kind == "log":
        ident = str(data.get("job") or "")
        if jobs_view.is_pilot_id(ident):
            if jobs_view.pilot_jobs is None:
                raise terminal.TermError("machine-pilot is not installed")
            return terminal.log_spec(jobs_view.pilot_jobs.log_view(jobs_view.pilot_id(ident)))
        if vd is None:
            raise terminal.TermError("vm-delegate is not installed")
        return terminal.log_spec(vd.log_view(ident))
    if kind == "provision":
        if vd is None:
            raise terminal.TermError("vm-delegate is not installed")
        return terminal.provision_spec(str(data.get("target") or ""), vd.targets())
    if kind == "bmc":
        if vd is None:
            raise terminal.TermError("vm-delegate is not installed")
        return terminal.bmc_spec(str(data.get("target") or ""), vd.target_bmcs())
    raise terminal.TermError("kind must be workspace, shell, uart, bmc, log or provision")


def term_launch(data: dict, client: str) -> dict:
    """POST /api/term/launch: open validated panes in Windows Terminal on this PC."""
    if client not in ("127.0.0.1", "::1"):
        raise PermissionError("Windows Terminal opens on the portal PC; use the browser here")
    if not isinstance(data, dict) or not isinstance(data.get("panes"), list):
        raise terminal.TermError("send {panes:[...], layout}")
    specs = [term_spec(p) for p in data["panes"]]
    cfg = portal_settings.load()["terminals"]
    env = vd.agent_env()[0] if vd is not None else os.environ
    return terminal.wt_launch(specs, str(data.get("layout") or "grid"), cfg["wt_window"],
                              cfg["wt_distro"] or os.environ.get("WSL_DISTRO_NAME", ""),
                              {"SSH_AUTH_SOCK": env.get("SSH_AUTH_SOCK", "")}
                              if env.get("SSH_AUTH_SOCK") else {})


def asset_stamp() -> str:
    """Cache-buster derived from the assets' mtimes."""
    return str(int(max((ASSETS / n).stat().st_mtime for n in ("portal.css", "pulse.js", "portal.js", "team.js", "flash.js"))))


# Oded's CI/CD pipeline explorer (~/odeds_sandbox, pipeline-explorer.service).
# The portal only deep-links into it; it never calls it.
PIPELINE_EXPLORER_URL = os.environ.get("PIPELINE_EXPLORER_URL", "http://127.0.0.1:8080")

# Platform DevOps surfaces (see the devops-expert skill). The portal deep-links
# into Grafana / the Platform portal and runs a few read-only octl queries.
GRAFANA_URL = os.environ.get("GRAFANA_URL", "https://grafana.elmnt.local").rstrip("/")
PLATFORM_URL = os.environ.get("PLATFORM_URL", "https://portal.ai.elmnt.local").rstrip("/")
OCTL_PROJECT = os.environ.get("PORTAL_OCTL_PROJECT", "iridium")
OCTL_MANIFEST = os.environ.get("PORTAL_OCTL_MANIFEST", "psoc-fw-ci")
DEVOPS_SKILL_DIR = Path.home() / ".copilot/skills/devops-expert"
GRAFANA_DASHBOARDS = {
    "ci_build_overview": ("CI Build Overview", "/d/ci-build-overview"),
    "ci_failure_reasons": ("CI Failure Reasons", "/d/ci-failure-reasons"),
    "merge_queue_health": ("Merge Queue Health", "/d/merge-queue-health"),
    "workflow_general": ("Workflow general view", "/d/df0453g684wzkf/workflow-general-view"),
    "tests_results": ("Tests results", "/d/begsnb827dse8d/tests-results"),
    "update_manifest": ("Update manifest", "/d/ce4fbxj65q6f4s2/update-manifest"),
    "integration_manifest": ("Integration manifest", "/d/devkmoy145edcc/integration-manifest"),
    "pldm_queue": ("PLDM job queue", "/d/pldm-job-queue-v1/pldm-job-queue"),
    "ec2_autostop": ("EC2 auto-stop monitor", "/d/ec2-auto-stop-monitor"),
    "ec2_cost": ("EC2 personal VM cost", "/d/ec2-personal-usage-cost/ec2-personal-vm-usage-cost?from=now/M&to=now"),
    "alerts": ("Alerts overview", "/d/alerts-overview/alerts-overview?orgId=1&var-datasource=P4169E866C3094E38"),
    "runner_details": ("Runner details", "/d/ce4fbxj65q6f4b/runner-details"),
    "job_lookup": ("Runner dispatcher job lookup", "/d/runner-dispatcher-lookup/runner-dispatcher-e28094-job-lookup?orgId=1&var-dc_job=dc"),
}
_PR_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")
_JOB_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+/job/\d+$")
_DEVOPS_CACHE: dict[str, tuple[float, dict]] = {}
_DEVOPS_LOCK = threading.Lock()


def grafana_links() -> dict:
    return {key: {"title": title, "url": GRAFANA_URL + path}
            for key, (title, path) in GRAFANA_DASHBOARDS.items()}


def _devops_cached(key: str, ttl: float, fn) -> dict:
    now = time.time()
    with _DEVOPS_LOCK:
        hit = _DEVOPS_CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _DEVOPS_LOCK:
        _DEVOPS_CACHE[key] = (time.time(), value)
        if len(_DEVOPS_CACHE) > 256:
            for k in sorted(_DEVOPS_CACHE, key=lambda k: _DEVOPS_CACHE[k][0])[:64]:
                _DEVOPS_CACHE.pop(k, None)
    return value


def grafana_health() -> dict:
    """Unauthenticated Grafana /api/health probe, cached for 60s."""
    def probe() -> dict:
        import ssl
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # internal CA; read-only health probe
        try:
            with urllib.request.urlopen(GRAFANA_URL + "/api/health", timeout=4, context=ctx) as r:
                body = json.loads(r.read(4096).decode() or "{}")
            return {"ok": body.get("database") == "ok", "version": body.get("version"),
                    "checked_at": ai.now_iso()}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error)[:200], "checked_at": ai.now_iso()}
    return _devops_cached("grafana-health", 60, probe)


def _octl(args: list[str], timeout: int = 90) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(["octl", *args], capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
        return cp.returncode, cp.stdout, cp.stderr
    except FileNotFoundError:
        return 127, "", "octl not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"octl timed out after {timeout}s"


def pr_stage(url: str) -> dict:
    """Which artifact versions contain a PR and their integration status (read-only)."""
    if not _PR_URL_RE.match(url or ""):
        raise ValueError("expected https://github.com/<owner>/<repo>/pull/<n>")

    def query() -> dict:
        rc, out, err = _octl(["artifacts", "versions", "--pr", url, "-p", OCTL_PROJECT,
                              "-m", OCTL_MANIFEST, "--top", "5", "-o", "json"])
        base = {"url": url, "project": OCTL_PROJECT, "manifest": OCTL_MANIFEST,
                "checked_at": ai.now_iso()}
        if rc != 0:
            return {**base, "ok": False, "error": (err or out).strip()[-600:] or f"octl exit {rc}"}
        try:
            raw = json.loads(out or "[]")
        except ValueError:
            return {**base, "ok": False, "error": "unparseable octl output", "raw": out[-2000:]}
        rows = raw if isinstance(raw, list) else raw.get("versions") or raw.get("items") or []
        versions = [{"version": r.get("Version") or r.get("version"),
                     "status": r.get("IntegrationStatus") or r.get("integration_status"),
                     "cd_manifests": r.get("CDManifests") or r.get("cd_manifests") or []}
                    for r in rows if isinstance(r, dict)]
        if not versions:
            stage = "not-in-version"
        elif any(v["status"] == "Passed" for v in versions):
            stage = "integration-passed"
        elif any(v["status"] == "Failed" for v in versions):
            stage = "integration-failed"
        else:
            stage = "integration-pending"
        if any(v["cd_manifests"] for v in versions):
            stage = "in-cd"
        return {**base, "ok": True, "stage": stage, "versions": versions}
    return _devops_cached("stage:" + url, 300, query)


def _job_grafana_links(job_url: str) -> dict:
    from urllib.parse import quote
    run_url = job_url.split("/job/")[0]
    return {"runner_details": GRAFANA_URL + GRAFANA_DASHBOARDS["runner_details"][1]
            + "?var-workflow_link=" + quote(run_url + "/attempts/1", safe=""),
            "job_lookup": GRAFANA_URL + GRAFANA_DASHBOARDS["job_lookup"][1]
            + "&var-job_link=" + quote(job_url, safe="")}


def sherlock(job_url: str) -> dict:
    """`octl workflow sherlock` failure classification for one job (read-only)."""
    if not _JOB_URL_RE.match(job_url or ""):
        raise ValueError("expected https://github.com/<o>/<r>/actions/runs/<run>/job/<job>")

    def query() -> dict:
        rc, out, err = _octl(["workflow", "sherlock", job_url], timeout=180)
        text = (out or "").strip()
        result = {"job": job_url, "ok": rc == 0, "checked_at": ai.now_iso(),
                  "links": _job_grafana_links(job_url), "raw": text[-6000:]}
        if rc != 0:
            result["error"] = (err or out).strip()[-600:] or f"octl exit {rc}"
            return result
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                result["classification"] = {k: parsed[k] for k in parsed
                                            if isinstance(parsed[k], (str, int, float, bool))}
        except ValueError:
            fields = {}
            for line in text.splitlines():
                m = re.match(r"^\s*([A-Za-z][\w /-]{1,30}):\s+(.+)$", line)
                if m and m.group(1).strip().lower() not in fields:
                    fields[m.group(1).strip().lower()] = m.group(2).strip()[:300]
            result["classification"] = fields
        return result
    return _devops_cached("sherlock:" + job_url, 900, query)


def sherlock_cached(job_url: str) -> dict | None:
    with _DEVOPS_LOCK:
        hit = _DEVOPS_CACHE.get("sherlock:" + job_url)
    return hit[1] if hit else None


def _journal_settings(status: str, detail: str, applied: str) -> None:
    """One self-contained journal event per settings save; never blocks the save."""
    try:
        maintenance.record(f"Settings {'updated' if status == 'completed' else 'save failed'}"
                           f" via portal: {detail}"[:1000], status,
                           component="portal-settings", deployment=applied)
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"[portal] settings journal write failed: {error}", file=sys.stderr)


def boot_payload(tasks: list[dict], wd: dict) -> str:
    """The one bootstrap object the page hands to portal.js.

    ``</`` is escaped because this lands inside a <script> element, where a
    literal ``</script>`` in any string would close it early.
    """
    return json.dumps({
        "jira_base": JIRA_BASE,
        "tasks": _annotate_tasks(tasks, wd),
        "workspaces": ws_ui_payload(wd),
        "bugs": bug_payload(),
        "triage": triage_payload(),
        "overview": overview_payload(),
        "prs": pr_payload(),
        "raid": raid_payload(),
        "vmlog": vmlog_payload(),
        "team": team_api.payload(),
        "explorer": {"url": PIPELINE_EXPLORER_URL.rstrip("/")},
        "grafana": {"url": GRAFANA_URL, "dashboards": grafana_links()},
        "platform": {"url": PLATFORM_URL},
        "settings": portal_settings.payload(include_status=False),
    }, ensure_ascii=False).replace("</", "<\\/")


def render_dashboard_html(tasks: list[dict], inline: bool = False,
                          event_cursor: dict | None = None) -> str:
    """Render the portal. ``inline`` embeds the assets for the static export,
    which is opened over ``file://`` and so cannot fetch ``/assets/…``."""
    wd = wm.load()
    if inline:
        styles = "<style>\n" + asset_text("portal.css") + "\n</style>"
        event_cursor = ""
        scripts = ("<script>\n" + asset_text("pulse.js") + "\n</script>"
                   + "<script>\n" + asset_text("portal.js") + "\n</script>"
                   + "<script>\n" + asset_text("team.js") + "\n</script>"
                   + "<script>\n" + asset_text("aichat.js") + "\n</script>")
    else:
        v = asset_stamp()
        cursor = json.dumps(event_cursor or EVENTS.cursor(), separators=(",", ":"))
        event_cursor = f"<script>window.PORTAL_EVENT_CURSOR={cursor};</script>"
        styles = f'<link rel="stylesheet" href="/assets/portal.css?v={v}">'
        scripts = (f'<script src="/assets/pulse.js?v={v}"></script>'
                   f'<script src="/assets/portal.js?v={v}"></script>'
                   f'<script src="/assets/aichat.js?v={v}"></script>'
                   f'<script src="/assets/team.js?v={v}"></script>')
    return (DASHBOARD_TEMPLATE
            .replace("__STYLES__", styles)
            .replace("__BOOTDATA__", boot_payload(tasks, wd))
            .replace("__SCRIPTS__", event_cursor + scripts)
            .replace("__GENERATED__", datetime.now().strftime("%d/%m/%Y %H:%M")))


def cmd_dashboard(args: argparse.Namespace) -> int:
    d = tm.load()
    out = Path(os.path.expanduser(args.output)) if args.output else STATE_DIR / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard_html(d["tasks"], inline=True), encoding="utf-8")
    print(f"[tm] wrote portal with {len(d['tasks'])} tasks and "
          f"{len(bug_payload()['bugs'])} bugs -> {out}")
    return 0


def render_review_draft(draft: dict) -> str:
    heads = draft.get("heads") or []
    if draft.get("kind") == "legacy-pr" and heads:
        title = escape(f'{draft["task_id"]} - {heads[0]["repo"]}#{heads[0]["pr_number"]}')
    else:
        title = escape(f'{draft["task_id"]} - {draft.get("title", "")}')
    covered = "".join(
        f'<li>{escape(str(h.get("repo", "")))}#{escape(str(h.get("pr_number", "")))}: '
        f'<code>{escape(str(h.get("head_sha", "")))}</code></li>' for h in heads)
    content = (MarkdownIt("gfm-like", {"html": False, "linkify": True})
               .use(tasklists_plugin)
               .render(draft["content"]))
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Review draft - {title}</title>'
        '<style>'
        ':root{color-scheme:dark;--bg:#090d1a;--panel:#11172a;--border:#28304c;'
        '--text:#e8e9f5;--muted:#a0a8c5;--accent:#b69aff;--info:#79c5ff}'
        '*{box-sizing:border-box}'
        'body{max-width:1000px;margin:0 auto;padding:2rem 1rem;'
        'font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'color:var(--text);background:radial-gradient(ellipse at 70% -12%,'
        'rgba(112,94,205,.18),transparent 42rem),var(--bg)}'
        'a{color:var(--info)}'
        '.review-header{margin:1rem 0 1.5rem}'
        '.review-header h1{font-size:1.5rem;line-height:1.3}'
        '.review-meta{color:var(--muted);font-size:.875rem}'
        '.review-content{overflow-wrap:anywhere;padding:clamp(1rem,4vw,2rem);'
        'border:1px solid var(--border);border-radius:12px;background:var(--panel)}'
        '.review-content>:first-child{margin-top:0}'
        '.review-content>:last-child{margin-bottom:0}'
        '.review-content h1,.review-content h2,.review-content h3,'
        '.review-content h4,.review-content h5,.review-content h6{line-height:1.3}'
        '.review-content pre{overflow:auto;padding:1rem;border:1px solid var(--border);'
        'border-radius:8px;background:var(--bg)}'
        '.review-content pre code{padding:0;border:0;background:transparent}'
        '.review-content :not(pre)>code{padding:.12em .35em;border:1px solid var(--border);'
        'border-radius:4px;background:var(--bg)}'
        '.review-content blockquote{margin-left:0;padding:.1rem 1rem;'
        'border-left:3px solid var(--accent);color:var(--muted)}'
        '.review-content table{display:block;max-width:100%;overflow-x:auto;'
        'border-collapse:collapse}'
        '.review-content th,.review-content td{padding:.4rem .65rem;'
        'border:1px solid var(--border);text-align:left}'
        '.review-content th{background:var(--bg)}'
        '.review-content hr{border:0;border-top:1px solid var(--border)}'
        '.review-content img{max-width:100%;height:auto}'
        '.task-list-item{list-style:none}'
        '.task-list-item-checkbox{margin:0 .45rem 0 -1.15rem}'
        '@media(max-width:600px){body{padding:1rem .75rem}}'
        '</style>'
        f'<body><a href="/#task/{escape(draft["task_id"], quote=True)}">'
        'Back to review task</a>'
        f'<header class="review-header"><h1>Review draft: {title}</h1>'
        '<p class="review-meta">Local draft only - not submitted to GitHub.</p>'
        f'<p class="review-meta">Saved: {escape(draft["updated_at"])}</p>'
        f'<p class="review-meta">Heads reviewed:</p><ul class="review-meta">{covered}</ul>'
        '</header>'
        f'<main class="review-content">{content}</main></body></html>'
    )


# --- editing applied by the local server (reuses store rules) ----------------

PID_FILE = STATE_DIR / ".dashboard.pid"


def apply_task_edit(ident: str, payload: dict) -> tuple[bool, str]:
    """Delegate task mutations to the task-manager domain owner."""
    return tm.apply_task_edit(ident, payload)


def apply_task_edits(edits: list[dict]) -> tuple[bool, str]:
    """Delegate a revision-checked bulk edit to the task-manager owner."""
    return tm.apply_task_edits(edits)


def apply_workspace_edit(slot: str, payload: dict, *, validated: bool = False) -> tuple[bool, str]:
    """Occupy or free a workspace slot from the dashboard.

    Payloads:
      * ``{"action": "free"}`` — release the slot's holder.
      * ``{"action": "occupy", "type": "task", "task_id": "T-NNN", "force": bool}``
      * ``{"action": "occupy", "type": "adhoc", "label": "...", "tag": "monitor", "force": bool}``

    Task holds are kept consistent with the task store (status promotion + notes);
    ad-hoc holds live only in the workspace-manager store.
    """
    root = wm.normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}"
    action = payload.get("action", "occupy")
    force = bool(payload.get("force"))

    if action == "free":
        ok, holder = wm.release(root, reset=False)
        if not ok:
            return False, f"invalid slot: {slot!r}"
        if holder is None:
            return True, f"{root} already free"
        if holder.get("type") == "task":
            d = tm.load()
            t = tm.find_task(d, holder["task_id"])
            if t:
                t["updated"] = tm.TODAY
                tm.append_note(t, f"dashboard: released workspace {root}")
                tm.save(d)
        return True, f"{root} freed ({wm.holder_summary(holder)})"

    htype = payload.get("type", "task")
    if htype == "task":
        tid = (payload.get("task_id") or "").strip()
        if not tid:
            return False, "task_id required"
        d = tm.load()
        t = tm.find_task(d, tid)
        if not t:
            return False, f"task not found: {tid}"
        ok, msg, displaced = wm.occupy_task(root, t["id"], detail=root, force=force,
                                           _validated=validated)
        if not ok:
            return False, msg
        if displaced and displaced.get("type") == "task":
            other = tm.find_task(d, displaced["task_id"])
            if other:
                tm.append_note(other, f"dashboard: workspace {root} released to {t['id']}")
        if t["status"] == "todo":
            t["status"] = "in-progress"
        t["updated"] = tm.TODAY
        tm.append_note(t, f"dashboard: assigned to workspace {root}")
        tm.save(d)
        return True, f"{t['id']} → {root}"

    if htype == "adhoc":
        label = (payload.get("label") or "").strip()
        tag = payload.get("tag") or "other"
        ok, msg, _ = wm.occupy_adhoc(root, label, tag=tag, force=force, _validated=validated)
        if not ok:
            return False, msg
        return True, msg

    return False, f"invalid holder type: {htype}"


# ----------------------------------------------------------------------------
# Workspace alignment (dashboard "manual review" checkout)
# ----------------------------------------------------------------------------
# When a code-review task is assigned to a workspace slot from the dashboard,
# the server checks the slot's repos out to the PR branches (everything else to
# the default branch) so the user can review by hand — the no-AI/no-credit
# equivalent of review-mgr `start --manual`. This runs as a background job so
# the request returns immediately; the UI polls ``/api/align/<job>`` for a
# progress/done indicator. Non-destructive: dirty repos are skipped and
# reported, never reset.

import threading as _threading  # noqa: E402
import contextlib as _contextlib  # noqa: E402
import copy as _copy  # noqa: E402

ALIGN_JOBS: dict[str, dict] = {}
ALIGN_LOCK = _threading.Lock()
ACTION_QUEUE: ActionQueue | None = None


def enqueue_action(job: dict) -> str:
    if ACTION_QUEUE is None:
        raise RuntimeError("Portal action queue is not available")
    return ACTION_QUEUE.enqueue(job)


# Running actions that can be stopped safely: job id -> cancel event. Only
# pure-AI work registers (its copilot process is killed); workspace, PR, PLDM
# and auto-review runs are never interrupted mid-flight.
AI_CANCEL: dict[str, _threading.Event] = {}
AI_CANCEL_LOCK = _threading.Lock()


@_contextlib.contextmanager
def ai_cancellable(job: dict):
    """Make ``job`` interruptible while the block runs; yields its cancel event."""
    event = _threading.Event()
    ident = job.get("id")
    if ident:
        with AI_CANCEL_LOCK:
            AI_CANCEL[ident] = event
    try:
        yield event
    finally:
        if ident:
            with AI_CANCEL_LOCK:
                if AI_CANCEL.get(ident) is event:
                    AI_CANCEL.pop(ident, None)


def interrupt_action(job: dict) -> bool:
    """ActionQueue hook: signal a running action to stop, if it is interruptible."""
    with AI_CANCEL_LOCK:
        event = AI_CANCEL.get(job.get("id") or "")
    if event is None:
        return False
    event.set()
    return True


def cancel_action(ident: str) -> dict:
    """Cancel a queued/approval/running action and settle its owner records."""
    if ACTION_QUEUE is None:
        raise RuntimeError("Portal action queue is not available")
    outcome = ACTION_QUEUE.cancel(ident)
    with ALIGN_LOCK:
        job = _copy.deepcopy(ALIGN_JOBS.get(ident) or {})
    if outcome == "cancelled":
        _settle_cancelled(job)
    job.pop("request", None)
    return {"status": outcome, "job": job}


def _settle_cancelled(job: dict) -> None:
    """A job cancelled before it ran: close the owner records it was holding open."""
    kind = job.get("kind")
    if kind in {"ai", "auto-review", "online-review", "review-chat"} and job.get("run_id"):
        run = AI_STORE.get(job["run_id"])
        if run and run.get("status") in {"queued", "running"}:
            again = "; send the message again" if kind == "review-chat" else ""
            run.update(status="error", error="cancelled before it started" + again,
                       finished=ai.now_iso())
            AI_STORE.save(run)
    if kind == "review-chat" and job.get("op") == "open":
        chat = AI_STORE.get_chat(job.get("chat_id") or "")
        if chat and chat.get("status") == "starting":
            _chat_update(chat["id"], status=CHAT_INTERRUPTED,
                         interrupted="start cancelled before it ran")


def action_resources(job: dict) -> set[str]:
    """Resolve current owner identities; never trust resource claims from clients."""
    kind = job["kind"]
    if kind == "pr-scan":
        return {"domain:prs"}
    if kind == "review-scan":
        # Completion can release any review's workspace hold.
        return {"domain:tasks", "workspace:*"}
    if kind == "vm-refresh":
        # The delegation owner and VM_REFRESH_LOCK still serialize fleet probes.
        return {"domain:delegation", "vm:*"}
    if kind == "vm-cancel":
        return {"domain:delegation", "vm:" + job["target"]}
    if kind == "ai":
        return {"ai:" + job["run_id"]}
    if kind == "auto-review":
        # Only the run itself: its occupy/free steps are separate queued
        # workspace actions with their own claims (claiming the workspace
        # here would deadlock them).
        return {"auto-review:" + job["ws_id"]}
    if kind == "online-review":
        # No workspace: only one online pass per task at a time.
        return {"online-review:" + job["task_id"]}
    if kind == "review-chat":
        # One message at a time per chat; opening also serialises with any
        # auto-review on the same workspace.
        claims = {"review-chat:" + job["chat_id"]}
        if job.get("op") == "open":
            claims.add("auto-review:" + job["ws_id"])
        return claims
    if kind not in {"workspace-action", "align", "pldm"}:
        return {"*"}
    if kind == "pldm":
        slot = job["request"].get("slot")
        if not slot:
            return {"*"}  # Auto-selection is deferred until execution.
        slots = [slot]
        claims = {"domain:prs", "domain:pldm"}
    elif kind == "align":
        slots = [job["slot"]]
        claims = {"domain:tasks"}
    else:
        slots = job["request"]["slots"]
        claims = set()
        if job["action"] in {"occupy", "free"}:
            claims.add("domain:tasks")
        if job["action"] == "occupy":
            # Reassignment may also release the task's previous workspace.
            claims.add("workspace:*")
    for slot in slots:
        rec = wm.record(slot)
        claims.add("workspace:" + rec["id"])
        if rec.get("target"):
            claims.add("vm:" + rec["target"])
        mirror = wm.status_read(rec["id"]).get("mirror") or {}
        if mirror.get("source_id"):
            claims.add("workspace:" + mirror["source_id"])
    return claims or {"*"}


def execute_action(job: dict, store_lock=None) -> None:
    if job["kind"] in {"workspace-action", "align", "pldm"}:
        claims = action_resources(job)
        if any(key.startswith("vm:") for key in claims):
            # Also exclude inline VM power/sync and the automatic watchdog.
            with VM_REFRESH_LOCK:
                return _execute_action(job, store_lock)
    return _execute_action(job, store_lock)


def _execute_action(job: dict, store_lock=None) -> None:
    kind = job["kind"]
    if kind == "workspace-action":
        return run_workspace_job(job, store_lock)
    if kind == "align":
        holder = wm.get_holder(job["slot"])
        if not holder or holder.get("task_id") != job["task_id"]:
            raise ValueError("Workspace assignment changed while alignment was queued")
        return run_workspace_alignment(job, store_lock)
    if kind == "pr-scan":
        PR_SCAN_LOCK.acquire()
        if job["request"].get("jira", True) is False:
            return run_pr_scan(job, job["request"].get("only"), jira=False)
        return run_pr_scan(job, job["request"].get("only"))
    if kind == "review-scan":
        REVIEW_SCAN_LOCK.acquire()
        return run_review_scan(job)
    if kind == "vm-refresh":
        VM_REFRESH_LOCK.acquire()
        return run_vm_refresh(job)
    if kind == "vm-cancel":
        if jobs_view.is_pilot_id(job.get("vm_job")):
            return run_pilot_stop(job)
        with VM_REFRESH_LOCK:
            return run_vm_cancel(job)
    if kind == "ai":
        return run_ai_job(job)
    if kind == "auto-review":
        return run_auto_review(job)
    if kind == "online-review":
        return run_online_review(job)
    if kind == "review-chat":
        return run_review_chat_job(job)
    if kind == "pldm":
        with PLDM_LOCK:
            feature = next((f for f in pm.records() if f["id"] == job["feature_id"]), None)
            if feature is None:
                raise ValueError("PLDM feature no longer exists")
            prepared, message = prepare_pldm_job(feature, job["request"])
            if prepared is None:
                raise ValueError(message)
            with ALIGN_LOCK:
                job.update(prepared)
            return run_pldm_submit(job)
    raise ValueError(f"Unknown queued action kind: {kind}")
# Serialises the PR scan so a double-click can't launch two gh storms.
PR_SCAN_LOCK = _threading.Lock()

# Serialises `/ci` comment posting so a double-click can't double-fire CI.
PR_CI_LOCK = _threading.Lock()

# Same for the Jira mirror refresh.
BUG_SCAN_LOCK = _threading.Lock()

# Serialises the review scan (gh probe of selected open review PRs).
REVIEW_SCAN_LOCK = _threading.Lock()

# the RAID owner (rd.py) does not lock across processes.
RAID_LOCK = _threading.Lock()

# Serialises the VM delegation probe (one ssh round trip per target).
VM_REFRESH_LOCK = _threading.Lock()


def _git(repo_dir: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _wip_snapshot(ws_dir: Path, name: str) -> bool:
    """Best-effort: scan the aligned workspace and save a named `wip` snapshot.

    Lets a later re-align restore the exact composite state with a single
    `wip load <name> && wip all checkout` instead of re-querying gh / the
    manifest. Silently no-ops if the `wip` CLI isn't installed.
    """
    wip = shutil.which("wip")
    if not wip:
        return False
    try:
        subprocess.run([wip, "scan"], cwd=str(ws_dir),
                       capture_output=True, text=True, timeout=120, check=False)
        r = subprocess.run([wip, "save", name], cwd=str(ws_dir),
                           capture_output=True, text=True, timeout=60, check=False)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# owner/repo out of any GitHub remote (git@…:o/r.git, ssh://…/o/r.git, https://…/o/r).
_REMOTE_RE = re.compile(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?/?$", re.I)


def _worktree_remote_key(repo_dir: Path) -> tuple[str, str] | None:
    """(owner, repo) lower-cased for a worktree's origin remote, or None."""
    r = _git(repo_dir, "remote", "get-url", "origin", timeout=15)
    if r.returncode != 0:
        return None
    m = _REMOTE_RE.search(r.stdout.strip())
    return (m.group(1).lower(), m.group(2).lower()) if m else None


def _discover_worktrees(ws_dir: Path) -> list[Path]:
    """All git worktrees under a (repo/west-style) workspace.

    Repos live at arbitrary nested paths whose dir name does not match the
    GitHub repo name, so we walk the tree, stop descending once a `.git` is
    found (repos aren't nested under each other here), and prune tool internals.
    """
    found: list[Path] = []
    skip = {".repo", ".west", ".cache", "node_modules", "build", ".venv"}
    for root, dirs, files in os.walk(ws_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        if ".git" in dirs or ".git" in files:
            found.append(Path(root))
            dirs[:] = []  # don't descend into a repo's source tree
    return sorted(found)


def _repo_default_branch(repo_dir: Path) -> str:
    """Best-effort default branch for a repo (origin/HEAD, else main/master)."""
    r = _git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", timeout=15)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("/")[-1]
    for cand in ("main", "master"):
        if _git(repo_dir, "rev-parse", "--verify", cand, timeout=15).returncode == 0:
            return cand
    return "main"


def _is_dirty(repo_dir: Path) -> bool:
    r = _git(repo_dir, "status", "--porcelain", timeout=30)
    return bool(r.stdout.strip())


def _checkout_pr_branch(repo_dir: Path, branch: str, pr_number: int | None) -> tuple[bool, str]:
    """Fetch + checkout a PR branch; fall back to pull/<n>/head. Non-destructive."""
    _git(repo_dir, "fetch", "origin", branch, timeout=180)
    r = _git(repo_dir, "checkout", branch, timeout=60)
    if r.returncode == 0:
        return True, f"on {branch}"
    if pr_number:
        f = _git(repo_dir, "fetch", "origin",
                 f"pull/{pr_number}/head:{branch}", timeout=180)
        if f.returncode == 0 and _git(repo_dir, "checkout", branch, timeout=60).returncode == 0:
            return True, f"on {branch} (via pull/{pr_number}/head)"
    return False, (r.stderr.strip() or "checkout failed").splitlines()[-1]


def _checkout_default_record(repo_dir: Path) -> dict | None:
    """Checkout a repo to its default branch; return a job record, or None if
    it's already there (nothing to report).
    """
    key = _worktree_remote_key(repo_dir)
    label = f"{key[0]}/{key[1]}" if key else repo_dir.name
    default = _repo_default_branch(repo_dir)
    cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
    if cur == default:
        return None
    if _is_dirty(repo_dir):
        return {"name": label, "role": "default", "action": "skipped-dirty",
                "detail": f"on {cur}, uncommitted changes — left as-is"}
    r = _git(repo_dir, "checkout", default, timeout=60)
    ok = r.returncode == 0
    return {"name": label, "role": "default",
            "action": "checked-out" if ok else "error",
            "detail": (f"{cur} → {default}" if ok
                       else (r.stderr.strip() or "checkout failed").splitlines()[-1])}


def _try_snapshot_restore(ws_dir: Path, worktrees: list, pr_by_repo: dict,
                          name: str, upd, add_repo):
    """Re-align a slot from a saved `wip` snapshot instead of re-querying gh.

    Mirrors the review-mgr `start` snapshot fast path so the dashboard uses the
    same `wip` tool as the skills: if a `review-<task-id>` snapshot exists (saved
    by a prior alignment) and the slot is clean, restore the whole composite
    state with `wip load <name> && wip all checkout`. Returns the set of matched
    review-repo keys when it handled the alignment, or ``None`` to fall back to
    the per-PR checkout loop (no `wip`, no snapshot, or any dirty repo).
    """
    wip = shutil.which("wip")
    snap = Path(os.path.expanduser(f"~/.wip/saved/{name}.wip"))
    if not wip or not snap.is_file():
        return None
    if any(_is_dirty(w) for w in worktrees):
        return None  # let the careful loop report skipped-dirty repos
    upd(current=f"restoring snapshot {name}")
    try:
        lr = subprocess.run([wip, "load", name], cwd=str(ws_dir),
                            capture_output=True, text=True, timeout=60, check=False)
        if lr.returncode != 0:
            return None
        subprocess.run([wip, "all", "checkout"], cwd=str(ws_dir),
                       capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError):
        return None

    seen = set()
    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD",
                   timeout=15).stdout.strip()
        pr = pr_by_repo.get(key) if key else None
        if pr:
            seen.add(key)
            exp = pr.get("branch") or ""
            ok = bool(exp) and cur == exp
            add_repo({"name": pr.get("repo") or f"{key[0]}/{key[1]}",
                      "role": "review",
                      "action": "checked-out" if ok else "error",
                      "detail": (f"on {cur} (snapshot)" if ok
                                 else f"expected {exp}, on {cur}")})
        else:
            if not key or cur == "HEAD":
                continue
            default = _repo_default_branch(repo_dir)
            if cur == default:
                continue
            add_repo({"name": f"{key[0]}/{key[1]}", "role": "default",
                      "action": "checked-out", "detail": f"on {cur} (snapshot)"})
    return seen


def _octl_bin() -> str:
    """Resolve the `octl` executable. systemd user services don't inherit the
    interactive shell's PATH, so `~/.local/bin` (its usual home) is checked
    explicitly rather than relying on a bare `"octl"` lookup.
    """
    found = shutil.which("octl")
    if found:
        return found
    local = os.path.expanduser("~/.local/bin/octl")
    return local if os.path.isfile(local) else "octl"


def _octl_ws_roots(ws_dir: Path) -> list[Path]:
    """All repo-tool workspace roots directly under a project slot.

    A slot (e.g. ``~/projects/iridium/two``) can hold *several* independent
    octl/west checkouts side by side (``two/fw``, ``two/kmd``, ``two/elpci``,
    ...), each with its own `.repo` — every one of them needs its own
    `octl ws sync`, not just the first one found.
    """
    if (ws_dir / ".repo").is_dir():
        return [ws_dir]
    roots = []
    if ws_dir.is_dir():
        for child in sorted(ws_dir.iterdir()):
            if child.is_dir() and (child / ".repo").is_dir():
                roots.append(child)
    return roots or [ws_dir]


def _octl_ws_root(ws_dir: Path) -> Path:
    """The primary repo-tool workspace root under a slot (prefers `fw`).

    Used for actions scoped to one checkout (e.g. the PLDM build, which
    targets the firmware tree specifically) rather than every root.
    """
    roots = _octl_ws_roots(ws_dir)
    for r in roots:
        if r.name == "fw":
            return r
    return roots[0]


def _find_ssh_auth_sock() -> str | None:
    """Locate an ssh-agent socket that actually has an identity loaded.

    The dashboard runs as a systemd user service, whose `SSH_AUTH_SOCK` may
    point at an empty gpg-agent emulation while the user's real, key-bearing
    agent lives at a per-login `/tmp/ssh-*/agent.*` path. Probe every candidate
    with `ssh-add -l` and return the first one that reports a key, so git auth
    works without the user pre-loading anything into a specific agent.
    """
    import glob
    xdg = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    candidates = []
    if os.environ.get("SSH_AUTH_SOCK"):
        candidates.append(os.environ["SSH_AUTH_SOCK"])
    candidates += sorted(glob.glob("/tmp/ssh-*/agent.*"), reverse=True)
    candidates += [f"{xdg}/ssh-agent.socket", f"{xdg}/keyring/ssh",
                   f"{xdg}/gnupg/S.gpg-agent.ssh"]
    seen = set()
    for sock in candidates:
        if sock in seen or not os.path.exists(sock):
            continue
        seen.add(sock)
        try:
            r = subprocess.run(["ssh-add", "-l"], env=dict(os.environ, SSH_AUTH_SOCK=sock),
                               capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return sock
    return None


def _sync_octl_roots(ws_dir: Path, upd, add_repo) -> tuple[int, int, str]:
    """Run `octl ws sync` in every repo-tool root under a slot, one at a time,
    reporting each directory to the job as it starts and finishes so the UI
    banner can narrate progress instead of going silent for the whole batch.

    Returns (ok_count, total_roots, combined output across all roots).
    """
    roots = _octl_ws_roots(ws_dir)
    ok_n = 0
    chunks = []
    # Without a usable SSH agent, git falls back to the identity file and
    # blocks on a passphrase prompt with no tty to answer it — BatchMode
    # turns that into an immediate failure instead of a multi-minute hang.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10")
    # Point git at whichever agent actually holds a key (the service's own
    # SSH_AUTH_SOCK is often an empty gpg-agent emulation).
    sock = _find_ssh_auth_sock()
    if sock:
        env["SSH_AUTH_SOCK"] = sock
    for i, root in enumerate(roots, 1):
        label = str(root.relative_to(ws_dir)) if root != ws_dir else "."
        upd(current=f"octl ws sync · {label} ({i}/{len(roots)})")
        try:
            r = subprocess.run([_octl_bin(), "ws", "sync"], cwd=str(root), env=env,
                                capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            add_repo({"name": label, "role": "sync", "action": "error",
                      "detail": f"failed to run: {e}"})
            chunks.append(f"== {label} ==\n{e}")
            continue
        out = (r.stdout or "") + (r.stderr or "")
        chunks.append(f"== {label} ==\n{out}")
        if r.returncode == 0:
            ok_n += 1
            add_repo({"name": label, "role": "sync", "action": "checked-out",
                      "detail": "synced"})
        else:
            tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["see output"]
            add_repo({"name": label, "role": "sync", "action": "error",
                      "detail": f"exited {r.returncode}: {tail[0][:150]}"})
    return ok_n, len(roots), "\n".join(chunks)[-8000:]



def run_workspace_alignment(job: dict, store_lock=None) -> None:
    """Background worker: align a workspace slot for a code-review task.

    Updates ``job`` in place (guarded by ALIGN_LOCK) so the UI can poll progress.

    If any review-branch checkout errors out, the whole slot is reverted: every
    repo is switched back to its default branch and the occupy is undone — a
    broken checkout must never squat a slot that looks assigned but isn't
    actually on the reviewer's branches.
    """
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    d = tm.load()
    t = tm.find_task(d, job["task_id"])
    if not t:
        upd(status="error", message="task not found", finished=tm.NOW)
        return
    prs = [p for p in tm.pr_entries(t) if not tm.pr_resolved(p)]
    wm.log_action(job["slot"], "align", "started", f"manual review {job['task_id']}")
    if wm.record(job["slot"]).get("target"):
        try:
            reading = wm.workspace_request(job["slot"], "align", prs=prs)
            upd(status="done", current=None, finished=wm.stamp(), message="remote review branches aligned")
            add_repo({"name": job["slot"], "role": "review", "action": "checked-out",
                      "detail": f"{len(reading.get('repos', []))} repositories checked"})
        except ValueError as e:
            upd(status="error", current=None, finished=wm.stamp(), message=str(e))
        return
    pr_by_repo = {(p.get("owner", "").lower(), p.get("repo", "").lower()): p
                  for p in prs if p.get("repo")}

    ws_dir = wm.slot_path(job["slot"])
    if not ws_dir.is_dir():
        upd(status="error", message=f"workspace dir not found: {ws_dir}", finished=tm.NOW)
        return

    # Match repos to PR entries by their origin remote (owner/repo), NOT by dir
    # name — this is a repo/west workspace where dir names differ from repo names.
    worktrees = _discover_worktrees(ws_dir)
    upd(total=len(worktrees))
    left_alone = 0

    # Fast path: restore the composite state from a prior `wip` snapshot rather
    # than re-checking out every PR branch via gh. Falls back to the per-repo
    # loop below when no snapshot/wip is available or the slot is dirty.
    seen_review = _try_snapshot_restore(
        ws_dir, worktrees, pr_by_repo, f"review-{job['task_id']}", upd, add_repo)

    if seen_review is None:
        seen_review = set()
        for repo_dir in worktrees:
            key = _worktree_remote_key(repo_dir)
            try:
                upd(current=str(repo_dir.relative_to(ws_dir)))
            except ValueError:
                upd(current=repo_dir.name)
            pr = pr_by_repo.get(key) if key else None
            label = pr.get("repo") if pr else (f"{key[0]}/{key[1]}" if key else repo_dir.name)
            if pr:
                seen_review.add(key)
                if _is_dirty(repo_dir):
                    add_repo({"name": label, "role": "review", "action": "skipped-dirty",
                              "detail": "uncommitted changes — left as-is"})
                    continue
                ok, detail = _checkout_pr_branch(
                    repo_dir, pr.get("branch") or "", pr.get("pr_number"))
                add_repo({"name": label, "role": "review",
                          "action": "checked-out" if ok else "error", "detail": detail})
                continue
            # Non-PR repo → switch to default branch, but never disturb a
            # manifest-pinned (detached HEAD) checkout — leave it as the manifest set it.
            cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
            if not key or cur == "HEAD":
                left_alone += 1
                continue
            default = _repo_default_branch(repo_dir)
            if cur == default:
                left_alone += 1
                continue
            if _is_dirty(repo_dir):
                add_repo({"name": label, "role": "default", "action": "skipped-dirty",
                          "detail": f"on {cur}, uncommitted changes — left as-is"})
                continue
            r = _git(repo_dir, "checkout", default, timeout=60)
            ok = r.returncode == 0
            add_repo({"name": label, "role": "default",
                      "action": "checked-out" if ok else "error",
                      "detail": (f"{cur} → {default}" if ok
                                 else (r.stderr.strip() or "checkout failed").splitlines()[-1])})

    # Report review repos the task references but that aren't in the workspace.
    for key, pr in pr_by_repo.items():
        if key not in seen_review:
            add_repo({"name": pr.get("repo") or f"{key[0]}/{key[1]}",
                      "role": "review", "action": "not-found",
                      "detail": "repo not present in this workspace"})

    review_checkout_failed = any(
        r["role"] == "review" and r["action"] == "error" for r in job["repos"])

    if review_checkout_failed:
        upd(current="reverting — checkout failed")
        for repo_dir in worktrees:
            rec = _checkout_default_record(repo_dir)
            if rec is not None:
                add_repo(rec)
        wm.release(job["slot"])
        errors = sum(1 for r in job["repos"]
                     if r["role"] == "review" and r["action"] == "error")
        msg = (f"{errors} branch checkout(s) failed — reverted to main and "
               f"released {job['slot']}")
        upd(status="error", current=None, finished=tm.NOW, message=msg)
        note = f"dashboard: workspace {job['slot']} checkout failed — reverted, slot released"
        if store_lock is not None:
            store_lock.acquire()
        try:
            d2 = tm.load()
            t2 = tm.find_task(d2, job["task_id"])
            if t2:
                tm.append_note(t2, note)
                t2["updated"] = tm.TODAY
                tm.save(d2)
        finally:
            if store_lock is not None:
                store_lock.release()
        return

    errors = sum(1 for r in job["repos"] if r["action"] in ("error", "not-found"))
    dirty = sum(1 for r in job["repos"] if r["action"] == "skipped-dirty")
    ok_n = sum(1 for r in job["repos"] if r["action"] == "checked-out")
    msg = f"{ok_n} aligned"
    if left_alone:
        msg += f", {left_alone} left as-is"
    if dirty:
        msg += f", {dirty} skipped (dirty)"
    if errors:
        msg += f", {errors} problem(s)"
    upd(status="done", current=None, finished=tm.NOW, message=msg)
    wm.log_action(job["slot"], "align", "error" if errors or dirty else "ok", msg)

    # Snapshot the aligned composite state (best-effort) so re-aligning this
    # task later is a single cheap `wip load review-<id> && wip all checkout`.
    _wip_snapshot(ws_dir, f"review-{job['task_id']}")

    # Record the outcome on the task (best-effort, guarded by the store lock).
    note = f"dashboard: workspace {job['slot']} aligned for manual review — {msg}"
    if store_lock is not None:
        store_lock.acquire()
    try:
        d2 = tm.load()
        t2 = tm.find_task(d2, job["task_id"])
        if t2:
            tm.append_note(t2, note)
            t2["updated"] = tm.TODAY
            tm.save(d2)
    finally:
        if store_lock is not None:
            store_lock.release()


def run_workspace_reset(job: dict, store_lock=None) -> None:
    """Compatibility worker using guarded return-to-revision and Sync."""
    try:
        wm.workspace_request(job["slot"], "free")
        with (store_lock or _threading.Lock()):
            ok, msg = apply_workspace_edit(job["slot"], {"action": "free"})
        if not ok:
            raise ValueError(msg)
        status, message = "done", "returned to manifest revisions and synced"
    except ValueError as e:
        status, message = "error", str(e)
    with ALIGN_LOCK:
        job.update(status=status, message=message, current=None, finished=wm.stamp())


# ----------------------------------------------------------------------------
# PLDM (Palladium) test submission
# ----------------------------------------------------------------------------
# `octl ws build --remote --target pldm` builds from the *workspace manifest*,
# not from a PR reference, so a feature can only be submitted once a slot is
# physically checked out on every one of its PR branches, clean and pushed.
# The job therefore does the whole chain: grab a free slot, align it, preflight,
# write the tests-config, dispatch, then stamp the result through the PR owner.

PLDM_LOCK = _threading.Lock()
PLDM_IMAGE_TYPES = ["1die_w_gmem", "2die_w_gmem", "1die_wo_gmem", "4die_w_gmem"]
# Anything above this and the run is almost certainly stuck behind a busy EDK
# rather than actually building; octl itself caps a test at 120 minutes.
PLDM_DISPATCH_TIMEOUT = 1800

_RUN_URL_RE = re.compile(r"https://github\.com/\S+/actions/runs/\d+")


def _unpushed(repo_dir: Path) -> str | None:
    """Describe unpushed commits on the current branch, or None if in sync."""
    br = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
    if not br or br == "HEAD":
        return None
    r = _git(repo_dir, "rev-list", "--count", f"origin/{br}..{br}", timeout=30)
    if r.returncode != 0:
        return f"{br} has no upstream on origin"
    n = r.stdout.strip()
    return f"{n} unpushed commit(s) on {br}" if n.isdigit() and int(n) else None


def run_pldm_submit(job: dict) -> None:
    """Background worker: align a slot to a feature's PRs and enqueue a PLDM job."""
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    def fail(msg):
        upd(status="error", current=None, finished=tm.NOW, message=msg)
        if job.get("held"):
            wm.release(job["slot"])
            upd(held=False)

    prs = job["prs"]
    pr_by_repo = {(p["owner"].lower(), p["repo"].lower()): p for p in prs}
    ws_dir = wm.slot_path(job["slot"])
    if not ws_dir.is_dir():
        return fail(f"workspace dir not found: {ws_dir}")

    upd(phase="align", current="scanning workspace")
    worktrees = _discover_worktrees(ws_dir)
    upd(total=len(worktrees))
    seen: set[tuple[str, str]] = set()
    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        upd(current=repo_dir.name)
        pr = pr_by_repo.get(key) if key else None
        if not pr:
            continue
        seen.add(key)
        label = f"{pr['repo']}#{pr['number']}"
        if _is_dirty(repo_dir):
            add_repo({"name": label, "action": "error",
                      "detail": "uncommitted changes — commit or stash first"})
            continue
        ok, detail = _checkout_pr_branch(repo_dir, pr.get("branch") or "", pr["number"])
        add_repo({"name": label, "action": "checked-out" if ok else "error",
                  "detail": detail})
    for key, pr in pr_by_repo.items():
        if key not in seen:
            add_repo({"name": f"{pr['repo']}#{pr['number']}", "action": "not-found",
                      "detail": "repo not present in this workspace"})

    bad = [r for r in job["repos"] if r["action"] != "checked-out"]
    if bad:
        return fail(f"{len(bad)} repo(s) not ready — nothing dispatched")

    # Remote mode rebuilds from origin, so an unpushed commit would silently be
    # left out of the very build meant to test it.
    upd(phase="preflight", current="checking for unpushed commits")
    stray = []
    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        if key and key in pr_by_repo:
            u = _unpushed(repo_dir)
            if u:
                stray.append(f"{pr_by_repo[key]['repo']}: {u}")
    if stray:
        return fail("push these first — " + "; ".join(stray))

    cfg_dir = Path(os.path.expanduser("~/tasks/pldm"))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / f"{job['display_name']}.yaml"
    cfg.write_text(
        f'display_name: "{job["display_name"]}"\n'
        "tests:\n"
        f'  - image_type: "{job["image_type"]}"\n'
        f'    group: "{job["group"]}"\n'
        f'    timeout_minutes: {job["timeout"]}\n'
        f'    args: "{job["args"]}"\n'
        '    extra_flags: ""\n', encoding="utf-8")
    upd(phase="dispatch", current="octl ws build --remote --target pldm",
        config=str(cfg))

    try:
        r = subprocess.run(
            [_octl_bin(), "ws", "build", "--remote", "--target", "pldm",
             "--from-file", str(cfg)],
            cwd=str(_octl_ws_root(ws_dir)), capture_output=True, text=True,
            timeout=PLDM_DISPATCH_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return fail(f"octl dispatch failed: {e}")

    out = (r.stdout or "") + (r.stderr or "")
    upd(output=out[-4000:])
    if r.returncode != 0:
        tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["see output"]
        return fail(f"octl exited {r.returncode}: {tail[0][:200]}")

    m = _RUN_URL_RE.search(out)
    url = m.group(0) if m else ""
    detail = f"{job['display_name']} · {url or 'run URL not printed'} · updated {tm.TODAY}"
    ok, msg = pm.set_pldm(job["feature_id"], "queued", detail)

    upd(status="done", phase="done", current=None, finished=tm.NOW, run_url=url,
        message=f"queued on {job['image_type']}" + ("" if ok else f" (tracking: {msg})"))
    if job.get("held"):
        wm.release(job["slot"])
        upd(held=False)


def prepare_pldm_job(feature: dict, opts: dict) -> tuple[dict | None, str]:
    """Validate and reserve a PLDM workspace at execution time.

    ``opts["dry"]`` resolves and validates everything, then reports the plan
    without reserving a slot, touching a checkout or dispatching a build.
    """
    prs = [p for p in feature.get("prs", []) if p.get("status") != "closed"]
    if not prs:
        return None, "feature has no open PRs to test"

    slot = (opts.get("slot") or "").strip()
    held = False
    known = viable_slots()
    if not known:
        return None, "no buildable workspace slots found"
    if slot:
        # Must name a real slot: normalize_slot() happily passes ".." through,
        # which would point the checkout at the whole projects root.
        slot = wm.normalize_slot(slot) or ""
        if slot not in known:
            return None, (f"{opts.get('slot')!r} is not a buildable workspace "
                          f"(use one of: {', '.join(known)})")
    else:
        free = [s for s in known if wm.is_free(s)]
        if not free:
            return None, "no free workspace slot — release one or pick a slot"
        slot = free[0]
    name = re.sub(r"[^a-z0-9]+", "-", feature["name"].lower()).strip("-")[:40]
    display = (opts.get("display_name") or f"{name}-{datetime.now():%m%d-%H%M}").strip()

    if not wm.is_free(slot) and not opts.get("force"):
        # Aligning would check PR branches out over someone else's work.
        return None, (f"slot '{slot}' is held by {wm.holder_summary(wm.get_holder(slot))}"
                      " \u2014 free it or choose another slot")
    if opts.get("dry"):
        return None, (f"dry run OK \u2014 would submit '{display}' from slot '{slot}' "
                      f"({len(prs)} PR(s), image {opts.get('image_type') or PLDM_IMAGE_TYPES[0]})")

    if wm.is_free(slot):
        ok, msg, _ = wm.occupy_adhoc(slot, f"PLDM test: {display}", tag="build")
        if not ok:
            return None, msg
        held = True

    job = {
        "kind": "pldm", "slot": slot, "held": held,
        "feature_id": feature["id"], "feature": feature["name"],
        "display_name": display,
        "image_type": opts.get("image_type") or PLDM_IMAGE_TYPES[0],
        "group": opts.get("group", "default"),
        "timeout": max(1, min(120, int(opts.get("timeout") or 20))),
        "args": opts.get("args", ""),
        "prs": prs, "phase": "align",
        "current": None, "total": 0,
        "message": "", "repos": [], "run_url": "",
    }
    return job, f"{display} → {slot}"


def start_pldm_job(feature: dict, opts: dict) -> tuple[str | None, str]:
    # Validate without reserving or touching a checkout until execution starts.
    _, message = prepare_pldm_job(feature, {**opts, "dry": True})
    if opts.get("dry") or not message.startswith("dry run OK"):
        return None, message
    jid = enqueue_action({
        "kind": "pldm", "slot": opts.get("slot") or "auto",
        "feature_id": feature["id"], "feature": feature["name"],
        "request": opts, "repos": [], "total": 0, "message": "",
    })
    return jid, "PLDM submission queued"


PR_SCAN_ICONS = {
    "merged": ("\u2713", "ok"), "approved": ("\u2713", "ok"),
    "merge-queue": ("\u21bb", "ok"),
    "pending": ("\u00b7", "warn"), "needs-rebase": ("\u26a0", "warn"),
    "ci-fail": ("\u2715", "bad"), "changes-requested": ("\u2715", "bad"),
    "closed": ("\u2715", "bad"),
}


def run_pr_scan(job: dict, only=None, jira: bool = True) -> None:
    """Probe tracked PRs and release the execution-time PR store lock."""
    try:
        if jira:
            _run_pr_scan(job, only)
        else:
            _run_pr_scan(job, only, jira_enabled=False)
    finally:
        PR_SCAN_LOCK.release()


def _run_pr_scan(job: dict, only=None, jira_enabled: bool = True) -> None:
    def progress(done, total, feat, pr):
        with ALIGN_LOCK:
            job["total"] = total
            if pr is None:          # opening callback: batch queue-state lookup
                job["current"] = "merge-queue states"
                return
            icon, tone = PR_SCAN_ICONS.get(pr.get("status") or "", ("\u00b7", "warn"))
            name = f"{pr['owner']}/{pr['repo']} #{pr['number']}"
            job["current"] = name
            job["repos"].append({
                "name": name, "action": pr.get("status") or "unknown",
                "icon": icon, "tone": tone,
                "detail": f"{pr.get('status') or 'unknown'} \u00b7 {feat['name']}",
            })
    try:
        if jira_enabled:
            result = pm.refresh(only=only, progress=progress)
        else:
            result = pm.refresh(only=only, progress=progress, jira=False)
    except Exception as e:
        with ALIGN_LOCK:
            job.update(status="error", finished=tm.NOW, current=None, message=str(e))
        return
    with ALIGN_LOCK:
        job["current"] = None
        job["finished"] = tm.NOW
        job["result"] = result
        job["status"] = "error" if result.get("error") else "done"
        gone = result.get("purged") or []
        bad = result.get("failed") or []
        jira = result.get("jira_sync") or []
        jira_counts = {state: sum(row.get("state") == state for row in jira)
                       for state in ("updated", "unchanged", "blocked", "error")}
        result["jira_sync_counts"] = jira_counts
        parts = [str(result["error"])] if result.get("error") else []
        if gone:
            parts.append(f"retired {len(gone)}: " + ", ".join(f["name"] for f in gone))
        if bad:
            parts.append(f"\u26a0 {len(bad)} PR(s) gh could not reach: "
                         + ", ".join(f"{b['repo']}#{b['number']}" for b in bad[:4]))
        if result.get("jira_sync_skipped"):
            parts.append("Jira sync skipped")
        if jira:
            parts.append("Jira sync: " + ", ".join(
                f"{count} {state}" for state, count in jira_counts.items()))
        jira_failed = [row for row in jira if row.get("state") in ("blocked", "error")]
        if jira_failed:
            job["status"] = "error"
            parts.append("\u26a0 Jira sync incomplete (including archived retries): "
                         + "; ".join(
                             f"feature {row.get('feature_id')} / {row.get('key')}: "
                             f"{row.get('state')} \u2014 {row.get('message', '')}"
                             for row in jira_failed))
        job["message"] = " \u00b7 ".join(parts)


def start_pr_scan_job(only=None, jira: bool = True) -> str:
    """Queue a PR scan (all features, or ``only`` ids); ``jira=False`` is GitHub-only."""
    request = {"only": only} if jira else {"only": only, "jira": False}
    job = {
        "kind": "pr-scan", "slot": "PRs", "request": request,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    return enqueue_action(job)


def refresh_pilot_bmc(job: dict, names: list) -> None:
    """Read machine-pilot's bmcjob dirs on the refreshed targets that have a BMC."""
    cfg = portal_settings.load()["machines"]
    if not (cfg["pilot_jobs"] and cfg["pilot_bmc_on_refresh"]) or jobs_view.pilot_jobs is None:
        return
    try:
        bmcs = [t for t in jobs_view.pilot_jobs.bmc_targets() if t in names]
    except Exception:                                          # noqa: BLE001
        return
    for t in bmcs:
        with ALIGN_LOCK:
            job["current"] = f"reading bmcjobs on {t}"
            job["total"] = job.get("total", 0) + 1
        try:
            res = jobs_view.pilot_jobs.refresh_bmc(t, auto=True)
        except Exception as e:                                 # noqa: BLE001
            res = {"error": str(e)}
        bad = res.get("error")
        with ALIGN_LOCK:
            job["repos"].append({
                "name": f"{t} BMC", "action": "skipped" if res.get("skipped") else
                ("unreachable" if bad else "read"), "icon": "\u26a0" if bad else "\u2713",
                "tone": "warn" if bad else "ok",
                "detail": bad or f"{res.get('jobs', 0)} bmcjob(s) read"})
    observe_pilot_jobs()


def run_vm_refresh(job: dict) -> None:
    """Probe target(s), mirror their logs, re-read EC2 — on a worker thread.

    This used to be done inline in the request handler. One unreachable target
    costs a 90 s ssh timeout and `octl vm list` revalidates SSO, so the whole
    call could outlive the browser's patience: the fetch gave up, the section
    never re-rendered, and the VM tab looked dead even though the probe was
    succeeding on the server. Streaming it as a job fixes that AND lets the
    banner show which target is being asked.
    """
    only = job.get("target") or None
    try:
        names = []
        try:
            names = [only] if only else vd.targets()
        except Exception:                                      # noqa: BLE001
            pass
        with ALIGN_LOCK:
            job["total"] = len(names) + 1
            job["current"] = f"probing {only}" if only else "probing targets"
        try:
            res = vd.refresh(only)
            observe_machine_jobs()
        except Exception as e:                                 # noqa: BLE001
            with ALIGN_LOCK:
                job.update(status="error", finished=tm.NOW, current=None, message=str(e))
            return
        errs = res.get("errors") or {}
        with ALIGN_LOCK:
            for t in res.get("targets") or []:
                bad = errs.get(t)
                job["repos"].append({
                    "name": t, "action": "unreachable" if bad else "probed",
                    "icon": "\u26a0" if bad else "\u2713",
                    "tone": "bad" if bad else "ok",
                    "detail": bad or "job dirs read \u00b7 logs mirrored locally",
                })
            job["current"] = "octl vm list"
        refresh_pilot_bmc(job, names)
        # Scoped to a bare-metal target there is no instance to ask about, and
        # `octl vm list` costs an SSO revalidation — skip it.
        want_ec2 = True
        if only:
            try:
                want_ec2 = vd.has_instance(only)
            except Exception:                                  # noqa: BLE001
                want_ec2 = False
        if want_ec2:
            try:
                vd.machines(force=True)
            except Exception as e:                             # noqa: BLE001
                with ALIGN_LOCK:
                    job["repos"].append({"name": "octl vm list", "action": "failed",
                                         "icon": "\u26a0", "tone": "bad", "detail": str(e)})
        payload = vmlog_payload()
        with ALIGN_LOCK:
            job["current"] = None
            job["finished"] = tm.NOW
            job["status"] = "done"
            job["result"] = payload
            live = len(payload.get("running") or [])
            scope = f"{only} only" if only else f"{res.get('probed', 0)} target(s)"
            parts = [f"probed {scope}",
                     f"{payload.get('logs_held', 0)} log(s) held locally"]
            if live:
                parts.append(f"{live} job(s) running")
            if errs:
                parts.append("\u26a0 " + ", ".join(f"{t}: {e}" for t, e in errs.items()))
            job["message"] = " \u00b7 ".join(parts)
    finally:
        VM_REFRESH_LOCK.release()


# The VM tab is refreshed by hand, so a reading can sit unconfirmed for as long
# as nobody clicks. That is tolerable for a settled job and wrong for a live
# one, and it is what let a single failed probe leave "running" rows on screen
# for days. The watchdog re-probes ONLY targets holding an unconfirmed job, so
# an idle fleet costs nothing and a watched job stays honest on its own.
VM_WATCHDOG_EVERY = 60           # seconds between sweeps; vd decides what is due


def run_vm_watchdog(stop: _threading.Event) -> None:
    """Background self-heal loop: re-probe targets with unconfirmed jobs."""
    while not stop.wait(VM_WATCHDOG_EVERY):
        if vd is None:
            continue
        # Never contend with a user-initiated refresh: skip this tick instead.
        if not VM_REFRESH_LOCK.acquire(blocking=False):
            continue
        try:
            vd.watchdog()
            observe_machine_jobs()
        except Exception:                                      # noqa: BLE001
            logging.exception("Portal machine watchdog failed")
        finally:
            VM_REFRESH_LOCK.release()


# --------------------------------------------------------------- live + events
# Notifications for every tab (and a Windows toast when none is open).
EVENTS = events.Bus(settings=lambda: portal_settings.load()["notify"])
LIVE: live_status.Manager | None = None
WATCHER: domain_watch.Watcher | None = None
POWER_SCHEDULE: power_schedule.PowerSchedule | None = None
POWER_SCHEDULE_ERROR = ""


def power_observations() -> dict:
    """Local owner readings only; never initiate an EC2 or SSH query."""
    cached = vd.machines()
    now = time.time()
    return {**cached, "machines": [{**m, **vd.power_view(m, now)}
                                  for m in cached.get("machines", [])]}


def start_power_schedule(stop: threading.Event) -> None:
    global POWER_SCHEDULE, POWER_SCHEDULE_ERROR
    try:
        POWER_SCHEDULE = power_schedule.PowerSchedule(
            STATE_DIR / "power-schedule.json", power_observations, vd.portal_machines,
            lambda: portal_settings.load()["machines"], VM_REFRESH_LOCK,
            lambda: EVENTS.publish_domains(["delegation"]), refresh_s=VM_WATCHDOG_EVERY)
    except (OSError, ValueError) as error:
        POWER_SCHEDULE_ERROR = f"Power scheduler unavailable: {error}"
        print(f"[portal/power] {POWER_SCHEDULE_ERROR}", file=sys.stderr)
        return
    threading.Thread(target=POWER_SCHEDULE.run, args=(stop,), daemon=True,
                     name="portal-power-schedule").start()


def apply_live_updates(cfg: dict | None = None) -> None:
    """Apply the Settings > Automatic updates server parameters without a restart."""
    cfg = cfg or portal_settings.load()["live_updates"]
    EVENTS.resize(cfg["replay_events"])
    if POWER_SCHEDULE is not None:
        POWER_SCHEDULE.wake.set()
    if WATCHER is not None:
        WATCHER.configure(enabled=cfg["enabled"],
                          domains=[d for d in portal_settings.LIVE_DOMAINS if cfg[d]],
                          interval=cfg["scan_interval_s"], quiet=cfg["quiet_ms"] / 1000)
SSO_LOGINS = sso_login.SsoLogins(
    env_for=lambda: vd.agent_env(recheck=True)[0] if vd is not None else dict(os.environ))
LIVE_STATE = {"changed": 0.0}
JOB_DONE_WORDS = {"passed": ("\u2713 passed", "ok"), "failed": ("\u2717 failed", "bad"),
                  "died": ("\u2717 died", "bad"), "cancelled": ("cancelled", "warn")}


def machine_notification_snapshot() -> dict:
    rows = vd.job_statuses() if vd is not None else {}
    for ident, row in rows.items():
        if row["kind"] == "flash":
            job = vd._job_by_id(ident)
            if job:
                row.update(vd.local_status(job))
    return rows


def notify_machine_job(ident: str, row: dict) -> None:
    status = row["status"]
    if status not in {*JOB_DONE_WORDS, "running", "unknown"}:
        return
    LIVE_STATE["changed"] = time.time()
    if status not in JOB_DONE_WORDS and not portal_settings.load()["notify"]["status_updates"]:
        return
    word, tone = JOB_DONE_WORDS.get(status, (status, "warn" if status == "unknown" else ""))
    title = f"{row['kind'].capitalize()} {word} on {row['target']}"
    body = row["ws"] + (f" \u00b7 rc={row['rc']}" if row["rc"] not in (None, 0) else "")
    link = "#vm"
    if row["kind"] == "flash":
        word, tone = {"running": ("started", ""), "passed": ("verified", "ok"),
                      "cancelled": ("cancelled", "warn"), "unknown": ("unconfirmed", "warn")}.get(
            status, ("NOT verified" if row.get("rc") == 2 else status, "bad"))
        title, body = f"Flash {word} \u00b7 {row['target']}", row.get("last_line") or body
        link = f"/flash?id={ident}"
    EVENTS.publish("machine_jobs", title, body, tone, link)


MACHINE_NOTIFICATIONS = events.JobTransitions(machine_notification_snapshot, notify_machine_job)


def pilot_notification_snapshot() -> dict:
    return {ident: {"status": r["status"], "kind": r["kind"], "target": r["target"],
                    "ws": r["tag"], "rc": r["rc"], "last_line": r["last_line"]}
            for ident, r in jobs_view.snapshot(portal_settings.load()["machines"]).items()}


PILOT_CHANGED = threading.Event()


def notify_pilot_job(ident: str, row: dict) -> None:
    PILOT_CHANGED.set()
    notify_machine_job(ident, row)


PILOT_NOTIFICATIONS = events.JobTransitions(pilot_notification_snapshot, notify_pilot_job)


def observe_pilot_jobs() -> bool:
    """Observe machine-pilot job transitions; True when any status changed."""
    PILOT_CHANGED.clear()
    try:
        PILOT_NOTIFICATIONS.observe()
    except Exception:
        logging.exception("Portal machine-pilot observation failed")
    return PILOT_CHANGED.is_set()


def run_pilot_watch(stop: threading.Event) -> None:
    """Poll machine-pilot's local job dirs so starts/finishes show without a click."""
    observe_pilot_jobs()
    while True:
        cfg = portal_settings.load()["machines"]
        try:
            jobs_view.prune(cfg)
        except Exception:                                        # noqa: BLE001
            logging.exception("Portal job record retention failed")
        if stop.wait(cfg["pilot_poll_s"]):
            return
        if cfg["pilot_jobs"] and observe_pilot_jobs():
            LIVE_STATE["changed"] = time.time()
            EVENTS.publish_domains(["delegation"])


def observe_machine_jobs() -> None:
    try:
        MACHINE_NOTIFICATIONS.observe()
    except Exception:
        logging.exception("Portal machine notification observation failed")


def publish_domain_changes(domains: list[str]) -> None:
    EVENTS.publish_domains(domains)
    if "delegation" in domains:
        observe_machine_jobs()


def live_block(target: str, text: str) -> None:
    """Reconcile one jobs-watch block and observe status transitions."""
    if vd is None:
        return
    rows = vd.parse_probe(text)
    # A user refresh / workspace action owns the delegation state right now;
    # the next block (seconds away) will catch up.
    if not VM_REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        before = vd.job_statuses()
        vd.refresh(only=target, sync=False, preprobed={target: rows})
        after = vd.job_statuses()
        observe_machine_jobs()
    finally:
        VM_REFRESH_LOCK.release()
    changed = [jid for jid, row in after.items()
               if (before.get(jid) or {}).get("status") != row["status"]]
    if changed:
        LIVE_STATE["changed"] = time.time()
    settled = False
    for jid in changed:
        row, prev = after[jid], (before.get(jid) or {}).get("status")
        if prev == "running" and row["status"] in JOB_DONE_WORDS:
            settled = True
    if settled:
        # Keep a local copy of the finished log before the slot is reused.
        threading.Thread(target=lambda: vd.sync_logs(target), daemon=True).start()


def live_reach(target: str, up: bool, why: str) -> None:
    EVENTS.publish("reachability", f"{target} is reachable again" if up
                   else f"{target} became unreachable", why or "", "ok" if up else "bad", "#vm")


def registered_machine_settings() -> dict:
    cfg = portal_settings.load()["machines"]
    known = set(vd.targets()) if vd is not None else set()
    return cfg | {"live_targets": [t for t in cfg["live_targets"] if t in known]}


def start_live_status() -> None:
    global LIVE
    if vd is None:
        return
    LIVE = live_status.Manager(
        live_block, live_reach, lambda: vd.agent_env()[0],
        registered_machine_settings)
    LIVE.sync()


# ------------------------------------------------------------------ AI ----
AI_STORE = ai.Store(STATE_DIR / "ai")
AI_SECTIONS = {"overview": "Overview", "work": "Tasks", "ws": "Workspaces",
               "bugs": "Bugs", "prs": "PRs", "vm": "Machines", "raid": "RAID",
               "team": "Team", "devops": "DevOps"}
# Live job dicts of running AI calls, so a follow-up can't race its parent.
AI_ACTIVE: set[str] = set()


def ai_usage_payload() -> dict:
    cfg = portal_settings.load()["ai"]
    use = AI_STORE.usage()
    return {"enabled": bool(cfg["enabled"]), "used": use["runs"], "cap": cfg["daily_cap"],
            "premium": use.get("premium", 0), "model": cfg["model"],
            "interactive_used": use.get("interactive", 0),
            "interactive_cap": cfg.get("interactive_daily_cap", 150)}


def _recent_tasks(tasks: list[dict]) -> list[dict]:
    """Open tasks plus anything finished in the last two weeks."""
    cutoff = (date.today().toordinal() - 14)
    out = []
    for t in tasks:
        done = t.get("status") in {"done", "cancelled"}
        when = t.get("completed") or t.get("updated") or ""
        try:
            recent = date.fromisoformat(str(when)[:10]).toordinal() >= cutoff
        except ValueError:
            recent = False
        if not done or recent:
            out.append({k: v for k, v in t.items() if k not in {"notes", "history"}})
    return out


def ai_section_data(section: str, private: bool) -> Any:
    """What the section shows, read now, from the same payloads it renders."""
    if section == "overview":
        tasks = tm.load()["tasks"]
        open_tasks = [t for t in tasks if t.get("status") not in {"done", "cancelled"}]
        return {"brief": overview_payload().get("brief"),
                "open_tasks": len(open_tasks),
                "tasks_in_progress": [{"id": t["id"], "title": t.get("title"),
                                       "category": t.get("category")}
                                      for t in open_tasks if t.get("status") == "in-progress"],
                "prs": pr_payload().get("counts"),
                "machines": {k: vmlog_payload().get(k) for k in ("counts", "running")}}
    if section == "work":
        return {"tasks": _recent_tasks(_annotate_tasks(tm.load()["tasks"], wm.load()))}
    if section == "ws":
        return ws_ui_payload(wm.load(), cached_only=True)
    if section == "bugs":
        return bug_payload()
    if section == "prs":
        return pr_payload()
    if section == "vm":
        data = vmlog_payload()
        data.pop("log", None)
        return data
    if section == "raid":
        return raid_payload()
    if section == "team":
        return team_api.payload("private" if private else "shared", None)
    if section == "devops":
        return {"grafana_url": GRAFANA_URL, "grafana": grafana_links(),
                "platform_url": PLATFORM_URL, "octl_project": OCTL_PROJECT,
                "octl_manifest": OCTL_MANIFEST, "skill": str(DEVOPS_SKILL_DIR)}
    raise ValueError("unknown section")


def ai_render(markdown: str) -> str:
    return (MarkdownIt("gfm-like", {"html": False, "linkify": True})
            .use(tasklists_plugin).render(markdown or ""))


def _strip_machine_line(text: str) -> str:
    return "\n".join(line for line in (text or "").splitlines()
                     if not line.strip().startswith(("PORTAL-TRIAGE:", ai.REVIEW_MARK))).rstrip()


def ai_public(run: dict) -> dict:
    out = {k: v for k, v in run.items() if k not in {"prompt", "context"}}
    if run.get("kind") in {"ci-triage", "online-review"} or run.get("profile") == "ci-triage":
        out["answer"] = _strip_machine_line(run.get("answer") or "")
        run = out
    out["answer_html"] = ai_render(run.get("answer") or "") if run.get("answer") else ""
    return out


def start_ai_run(body: dict, private: bool) -> dict:
    """Validate an Explain/Ask/brief request, reserve budget and queue it."""
    cfg = portal_settings.load()["ai"]
    if not cfg["enabled"]:
        raise ValueError("AI is turned off in Settings")
    kind = body.get("kind")
    if kind not in {"explain", "ask", "brief"}:
        raise ValueError("kind must be explain, ask or brief")
    section = body.get("section") if kind != "brief" else "overview"
    if section not in AI_SECTIONS:
        raise ValueError("unknown section")
    question = body.get("question") or ""
    focus = body.get("focus") or ""
    if not isinstance(question, str) or not isinstance(focus, str):
        raise ValueError("question and focus must be text")
    question, focus = question.strip()[:4000], focus.strip()[:600]
    if kind == "ask" and not question:
        raise ValueError("a question is required")
    parent = None
    if body.get("parent"):
        parent = AI_STORE.get(str(body["parent"]))
        if not parent or not parent.get("session_id"):
            raise ValueError("the conversation to continue is not available")
        if parent.get("private") and not private:
            raise PermissionError("enable private mode to continue this conversation")
        if parent["id"] in AI_ACTIVE:
            raise ValueError("wait for the previous answer first")
        if kind != "ask":
            raise ValueError("follow-ups must be questions")
        if parent.get("kind") == "ci-triage" or parent.get("profile") == "ci-triage":
            section = "prs"
    context = body.get("context")
    if kind == "brief":
        if not isinstance(context, (dict, list)):
            raise ValueError("the brief run needs its sections as context")
    else:
        context = None
    AI_STORE.reserve(cfg["daily_cap"])
    run = ai.new_run(kind, section=section, question=question, focus=focus,
                     private=bool(private and section == "team"),
                     parent=parent["id"] if parent else None,
                     resume=parent["session_id"] if parent else None)
    if parent and parent.get("profile") == "ci-triage":
        run.update(profile="ci-triage", feature_id=parent.get("feature_id"))
    if context is not None:
        run["context"] = context
    AI_STORE.save(run)
    label = {"explain": f"Explain {AI_SECTIONS[section]}",
             "ask": f"Ask \u00b7 {AI_SECTIONS[section]}",
             "brief": "AI brief"}[kind]
    job_id = enqueue_action({"kind": "ai", "run_id": run["id"], "title": label,
                             "slot": label, "current": "queued", "message": "",
                             "repos": [], "total": 0,
                             "link": f"ai/{run['id']}"})
    return {"run_id": run["id"], "job": job_id}


def _ai_progress(job: dict):
    def on_line(ev: dict) -> None:
        text = ai.progress_text(ev)
        if text:
            with ALIGN_LOCK:
                job["current"] = text
    return on_line


def run_ai_job(job: dict) -> None:
    run = AI_STORE.get(job.get("run_id", ""))
    if run is None:
        raise ValueError("AI run record is missing")
    cfg = portal_settings.load()["ai"]
    AI_ACTIVE.add(run["id"])
    if run.get("parent"):
        AI_ACTIVE.add(run["parent"])
    try:
        read_at = ai.now_iso()
        run.update(status="running", started=read_at)
        with ALIGN_LOCK:
            job["current"] = "reading portal data"
        if run["kind"] == "brief":
            text, cut = ai.compact(run.pop("context", {}), 20_000)
            prompt = ai.brief_prompt(text, read_at, cut)
        elif run["kind"] == "ci-triage":
            feature = run.get("context") or {}
            repos = sorted({f"{p.get('owner')}/{p.get('repo')}" for p in feature.get("prs") or []})
            prompt = ai.ci_triage_prompt(feature, _pfa_recent(repos), read_at)
            text, cut = prompt, False
        elif run.get("resume"):
            text, cut = "", False
            prompt = ai.followup_prompt(run["question"])
        else:
            data = ai_section_data(run["section"], run.get("private", False))
            text, cut = ai.compact(data)
            label = AI_SECTIONS[run["section"]]
            focus = run["focus"]
            if run["section"] == "devops":
                focus = (f"Answer from the devops-expert skill: read {DEVOPS_SKILL_DIR}/SKILL.md "
                         f"and the relevant {DEVOPS_SKILL_DIR}/references/*.md first. "
                         + (focus or "")).strip()
            prompt = (ai.explain_prompt(run["section"], label, text, read_at, cut, focus)
                      if run["kind"] == "explain" else
                      ai.ask_prompt(run["section"], label, text, read_at, cut,
                                    run["question"], focus))
        run.update(context_bytes=len(text), truncated=cut)
        AI_STORE.save(run)
        argv = ai.build_argv(prompt, model=cfg["model"], profile=run.get("profile") or "read",
                             allow_reads=cfg["allow_jira_github_reads"],
                             resume=run.get("resume"),
                             read_dirs=((str(DEVOPS_SKILL_DIR),)
                                        if run.get("section") == "devops" else ()))
        timeout = max(cfg["timeout_s"], 600) if run["kind"] == "ci-triage" else cfg["timeout_s"]
        with ai_cancellable(job) as cancel:
            result = ai.run_copilot(argv, cwd=str(Path.home()), timeout=timeout,
                                    on_line=_ai_progress(job), cancel=cancel)
        _ai_finish(run, result)
        if run["kind"] == "ci-triage" and not run["error"]:
            _ci_triage_record(run)
        shown = _strip_machine_line(run["answer"]) if run.get("profile") == "ci-triage" else run["answer"]
        with ALIGN_LOCK:
            job.update(status="error" if run["error"] else "done", current=None,
                       message=run["error"] or (run.get("verdict_summary")
                                                or (shown.strip().splitlines() or [""])[0])[:200])
    except Exception as error:                                   # noqa: BLE001
        run.update(status="error", error=str(error), finished=ai.now_iso())
        AI_STORE.save(run)
        raise
    finally:
        AI_ACTIVE.discard(run["id"])
        if run.get("parent"):
            AI_ACTIVE.discard(run["parent"])


def _ai_finish(run: dict, result: dict) -> None:
    run.update(answer=result.get("answer") or "", error=result.get("error") or "",
               session_id=result.get("session_id"), model=result.get("model"),
               premium=result.get("premium") or 0, nano_aiu=result.get("nano_aiu") or 0,
               tools=result.get("tools") or 0, finished=ai.now_iso(),
               status="error" if result.get("error") else "done")
    AI_STORE.save(run)
    AI_STORE.add_premium(run["premium"])


# ------------------------------------------------------- PR CI triage ----
def _pfa_recent(repos: list[str]) -> list[dict]:
    if pfa is None:
        return []
    try:
        return pfa.recent(repos)[:15]
    except Exception:                                            # noqa: BLE001
        return []


def ci_triage_key(feature: dict) -> str:
    """Identity of a feature's current failure: its failing run URLs (or, before
    runs are known, the failing PRs and check names). A new push/run changes it."""
    runs, fallback = set(), set()
    for pr in feature.get("prs") or []:
        if pr.get("status") != "ci-fail":
            continue
        runs.update(pr.get("failed_runs") or [])
        fallback.add(f"{pr.get('repo')}#{pr.get('number')}:"
                     + ",".join(sorted(pr.get("failed_checks") or [])))
    return "|".join(sorted(runs or fallback))


def _ci_triage_runs() -> list[dict]:
    runs = [r for r in AI_STORE.recent(300) if r.get("kind") == "ci-triage"]
    return sorted(runs, key=lambda r: r.get("created") or "", reverse=True)


def ci_triage_index() -> dict:
    """feature_id -> latest triage summary, flagged stale when the failure moved."""
    features = {f.get("id"): f for f in pr_payload().get("features") or []}
    out: dict = {}
    for run in _ci_triage_runs():
        fid = run.get("feature_id")
        if fid in out or fid not in features:
            continue
        out[fid] = {"run_id": run["id"], "status": run.get("status"),
                    "verdict": run.get("verdict"), "summary": run.get("verdict_summary"),
                    "finished": run.get("finished"),
                    "stale": run.get("ci_key") != ci_triage_key(features[fid])}
    return {str(k): v for k, v in out.items()}


def start_ci_triage(feature_id, force: bool = False) -> dict:
    """Queue (or reuse) the AI triage of one feature set's red CI."""
    cfg = portal_settings.load()["ai"]
    if not cfg["enabled"]:
        raise ValueError("AI is turned off in Settings")
    if not cfg.get("allow_jira_github_reads"):
        raise ValueError("CI triage reads GitHub logs: allow Jira/GitHub reads in Settings \u203a AI")
    if type(feature_id) is not int:
        raise ValueError("feature_id must be an integer")
    feature = next((f for f in pr_payload().get("features") or []
                    if f.get("id") == feature_id), None)
    if feature is None:
        raise ValueError("unknown feature")
    failing = [p for p in feature.get("prs") or [] if p.get("status") == "ci-fail"]
    if not failing:
        raise ValueError("no PR of this feature is failing CI")
    key = ci_triage_key(feature)
    latest = next((r for r in _ci_triage_runs() if r.get("feature_id") == feature_id), None)
    if latest and latest.get("status") in {"queued", "running"}:
        return {"run_id": latest["id"], "job": None, "cached": True}
    if latest and latest.get("status") == "done" and latest.get("ci_key") == key and not force:
        return {"run_id": latest["id"], "job": None, "cached": True}
    keep = ("owner", "repo", "number", "title", "url", "status", "checks_fail",
            "checks_pass", "failed_checks", "failed_runs", "branch", "head", "base")
    context = {"id": feature_id, "name": feature.get("name"), "jira": feature.get("jira"),
               "prs": [{k: p.get(k) for k in keep if p.get(k) is not None}
                       for p in feature.get("prs") or []]}
    known = {}
    for p in feature.get("prs") or []:
        for u in p.get("failed_runs") or []:
            hit = sherlock_cached(u) if isinstance(u, str) and _JOB_URL_RE.match(u) else None
            if hit and hit.get("ok"):
                known[u] = hit.get("classification") or hit.get("raw", "")[-1500:]
    if known:
        context["sherlock"] = known
    AI_STORE.reserve(cfg["daily_cap"])
    name = feature.get("name") or f"feature {feature_id}"
    run = ai.new_run("ci-triage", section="prs", question=f"Triage CI \u00b7 {name}",
                     focus="", private=False, feature_id=feature_id, ci_key=key,
                     profile="ci-triage", context=context)
    AI_STORE.save(run)
    title = f"AI CI triage \u00b7 {name}"[:120]
    job_id = enqueue_action({"kind": "ai", "run_id": run["id"], "title": title,
                             "slot": title, "current": "queued", "message": "",
                             "repos": [], "total": 0, "link": f"ai/{run['id']}"})
    return {"run_id": run["id"], "job": job_id, "cached": False}


def _ci_triage_record(run: dict) -> None:
    """After a triage: keep its verdict, record sightings, add the feature note.
    Never fails the run - bookkeeping errors are kept on the record."""
    parsed = pfa.parse_verdict(run.get("answer") or "") if pfa else None
    if not parsed:
        run["note_error"] = "no PORTAL-TRIAGE verdict line in the answer"
        AI_STORE.save(run)
        return
    run.update(verdict=parsed["verdict"], verdict_summary=parsed.get("summary") or "")
    context = run.get("context") or {}
    failing = [p for p in context.get("prs") or [] if p.get("status") == "ci-fail"]

    def pr_of(item: dict) -> dict:
        repo = (item.get("repo") or "").rsplit("/", 1)[-1]
        return next((p for p in failing if p.get("number") == item.get("pr")
                     and (not repo or p.get("repo") == repo)), {})

    errors = []
    if pfa is not None:
        items = parsed.get("items") or [
            {"repo": p.get("repo"), "pr": p.get("number"),
             "check": ",".join(p.get("failed_checks") or []), "run_url": "",
             "signature": parsed.get("summary") or "", "verdict": parsed["verdict"]}
            for p in failing]
        for item in items:
            pr = pr_of(item)
            repo = (f"{pr['owner']}/{pr['repo']}" if pr.get("owner") and pr.get("repo")
                    else item.get("repo") or "")
            try:
                pfa.record(repo=repo, verdict=item.get("verdict") or parsed["verdict"],
                           pr=item.get("pr"), feature_id=context.get("id"),
                           run_url=item.get("run_url") or (pr.get("failed_runs") or [""])[0],
                           check_name=item.get("check") or "",
                           signature=item.get("signature") or parsed.get("summary") or "",
                           summary=parsed.get("summary") or "", source="portal")
            except Exception as error:                           # noqa: BLE001
                errors.append(f"sighting: {error}")
    if pm is not None and context.get("id") is not None:
        try:
            ok, msg = pm.add_note(context["id"], f"AI CI triage: {parsed['verdict']} \u2014 "
                                  f"{parsed.get('summary') or ''}".strip(), label="ai-triage")
            if not ok:
                errors.append(f"note: {msg}")
        except Exception as error:                               # noqa: BLE001
            errors.append(f"note: {error}")
    run["note_error"] = "; ".join(errors)
    AI_STORE.save(run)


# ------------------------------------------------------------ auto-review ----
AUTO_REVIEW_WAIT = 3600


def _task(tid: str) -> dict | None:
    return next((t for t in tm.load()["tasks"] if t["id"] == tid), None)


def tm_note(tid: str, message: str) -> None:
    """Append a task note through the task owner's transaction."""
    with tm.task_store.transaction() as data:
        task = tm.find_task(data, tid)
        if task is not None:
            tm.append_note(task, message)
            task["updated"] = tm.TODAY


def _draft_marks(task: dict) -> dict:
    marks = {(r.get("pr_url"), d.get("id")): d.get("updated_at")
             for r in task.get("reviews") or [] for d in r.get("drafts") or []}
    marks.update({("task", d.get("id")): d.get("updated_at")
                  for d in task.get("review_drafts") or []})
    return marks


def _open_reviews(task: dict) -> list[dict]:
    return [r for r in task.get("reviews") or []
            if r.get("pr_url") and not r.get("merged") and not r.get("closed")]


def _pr_repo(review: dict) -> str:
    if review.get("repo"):
        return review["repo"]
    parts = urlparse(review.get("pr_url") or "").path.strip("/").split("/")
    return parts[1] if len(parts) > 1 else ""


def review_requirement(task: dict) -> dict:
    """The manifest a workspace must have deployed to review this task's PRs.

    ``revision`` is the PRs' common base branch (read from GitHub);
    ``project`` comes from ``project.<name>.*`` repos and is empty when every
    PR is in a project-neutral repo (``ip.*``), which any project can host.
    Mixed projects or bases cannot share one workspace and are refused.
    """
    open_reviews = _open_reviews(task)
    projects = set()
    for r in open_reviews:
        parts = _pr_repo(r).split(".")
        if len(parts) > 2 and parts[0] == "project":
            projects.add(parts[1])
    if len(projects) > 1:
        raise ValueError("the PRs span several projects (" + ", ".join(sorted(projects))
                         + "); one workspace cannot review them together")
    bases = _pr_bases(task)
    if len(bases) != 1:
        raise ValueError("the PRs target different base branches (" + ", ".join(sorted(bases))
                         + "); one workspace cannot review them together" if bases
                         else "could not determine the PRs' base branch")
    return {"project": projects.pop() if projects else "", "revision": bases.pop()}


def _need_text(need: dict) -> str:
    return f"{need['project'] or 'any project'} @ {need['revision']}"


def review_workspace_mismatch(rec: dict, need: dict) -> str:
    """Why ``rec`` cannot host a review needing ``need`` ('' when it can)."""
    if rec.get("target"):
        return "remote workspace (auto-review runs locally)"
    dep = wm.deployed_manifest(rec["slot"])
    if dep.get("error"):
        return dep["error"]
    if need["project"] and dep["project"] != need["project"]:
        return f"deploys project '{dep['project']}', not '{need['project']}'"
    if dep["revision"] != need["revision"]:
        return f"deploys revision '{dep['revision']}', not '{need['revision']}'"
    return ""


def pick_review_workspace(tid: str, need: dict) -> tuple[dict, bool]:
    """(workspace record, already held by this task) or ValueError.

    Only review-purpose workspaces whose *deployed* manifest (project and
    revision from ``.repo/metadata.json``) matches the PRs are used: the
    task's own one if it holds it, else the first free match. Nothing else is
    borrowed; when nothing fits, the refusal says why each candidate was
    skipped.
    """
    held = wm.slot_of_task(tid)
    if held:
        rec = wm.record(held)
        if rec.get("purpose") != "review":
            raise ValueError(f"{tid} already holds {rec['label']}, which is not a review "
                             "workspace; free it first")
        why = review_workspace_mismatch(rec, need)
        if why:
            raise ValueError(f"{tid} holds {rec['label']}, which cannot review "
                             f"{_need_text(need)}: {why}; free it first")
        return rec, True
    skipped = []
    for rec in wm.roster(cached_only=True):
        if rec.get("purpose") != "review":
            continue
        if not wm.is_free(rec["slot"]):
            skipped.append(f"{rec['label']}: occupied")
            continue
        why = review_workspace_mismatch(rec, need)
        if why:
            skipped.append(f"{rec['label']}: {why}")
            continue
        return rec, False
    raise ValueError(f"no free review workspace with {_need_text(need)} deployed ("
                     + ("; ".join(skipped) if skipped else "no workspace has purpose 'review'")
                     + "); free one or add one in Workspaces")


def has_review_draft(task: dict) -> bool:
    return bool(task.get("review_drafts")) or any(
        r.get("drafts") for r in task.get("reviews") or [])


REVIEW_MODE_LABELS = {"create": "Review draft", "rereview": "Re-review"}


def _review_task(tid: str) -> dict:
    """The open code-review task ``tid`` with open PRs, or ValueError."""
    task = _task(tid)
    if task is None or task.get("category") != "code-review":
        raise ValueError("AI review needs a code-review task")
    if task.get("status") in {"done", "cancelled"}:
        raise ValueError("the review task is closed")
    if not _open_reviews(task):
        raise ValueError("the task has no open PRs to review")
    return task


def _review_busy(tid: str) -> str:
    """Why another AI review of ``tid`` cannot start now ('' when it can)."""
    with ALIGN_LOCK:
        for other in ALIGN_JOBS.values():
            if (other.get("kind") in {"auto-review", "online-review"} and other.get("task_id") == tid
                    and other.get("status") in {"queued", "running"}):
                return f"an AI review draft of {tid} is already queued"
    if AI_STORE.chats(tid, open_only=True):
        return f"an interactive review of {tid} is open; resume or end it first"
    return ""


def start_auto_review(tid: str, mode: str = "create") -> dict:
    cfg = portal_settings.load()["ai"]
    if not cfg["enabled"]:
        raise ValueError("AI is turned off in Settings")
    if mode not in ai.REVIEW_MODES:
        raise ValueError("mode must be create or rereview")
    task = _review_task(tid)
    if mode == "create" and has_review_draft(task):
        raise ValueError(f"{tid} already has a review draft; use Re-review")
    if mode == "rereview" and not has_review_draft(task):
        raise ValueError(f"{tid} has no review draft yet; use Create review draft")
    busy = _review_busy(tid)
    if busy:
        raise ValueError(busy)
    need = review_requirement(task)
    rec, held = pick_review_workspace(tid, need)
    AI_STORE.reserve(cfg["daily_cap"])
    label = REVIEW_MODE_LABELS[mode]
    run = ai.new_run("auto-review", section="work", task_id=tid, mode=mode,
                     question=f"{label} {tid}: {task.get('title', '')}",
                     workspace=rec["label"])
    AI_STORE.save(run)
    title = f"{label} {tid} \u00b7 {rec['label']}"
    job_id = enqueue_action({"kind": "auto-review", "task_id": tid, "run_id": run["id"],
                             "ws_id": rec["id"], "ws_slot": rec["slot"], "held": held,
                             "mode": mode,
                             "need": need, "title": title, "slot": title, "current": "queued",
                             "message": "", "repos": [], "steps": [], "total": 4,
                             "link": f"task/{tid}"})
    return {"run_id": run["id"], "job": job_id, "workspace": rec["label"],
            "manifest": _need_text(need), "mode": mode}


def _wait_action(job_id: str, timeout: int = AUTO_REVIEW_WAIT) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with ALIGN_LOCK:
            sub = dict(ALIGN_JOBS.get(job_id) or {})
        if sub.get("status") in {"done", "error", "cancelled", "approval_required"}:
            return sub
        time.sleep(2)
    raise TimeoutError(f"action {job_id} did not finish in {timeout}s")


def _pr_bases(task: dict) -> set[str]:
    bases = set()
    for r in _open_reviews(task):
        out = subprocess.run(["gh", "pr", "view", r["pr_url"], "--json", "baseRefName",
                              "-q", ".baseRefName"], capture_output=True, text=True,
                             timeout=60, check=False)
        if out.returncode != 0:
            raise ValueError(f"cannot read the base branch of {r['pr_url']}: "
                             f"{(out.stderr or '').strip()[:200]}")
        bases.add(out.stdout.strip())
    return bases


def run_auto_review(job: dict) -> None:
    run = AI_STORE.get(job.get("run_id", "")) or {}
    if not run:
        raise ValueError("auto-review run record is missing")
    cfg = portal_settings.load()["ai"]
    tid, slot = job["task_id"], job["ws_slot"]
    occupied_here = False

    def step(key: str, label: str, state: str = "running", detail: str = "") -> None:
        with ALIGN_LOCK:
            steps = job.setdefault("steps", [])
            if steps and steps[-1]["state"] == "running":
                steps[-1]["state"] = "done"
            if state == "running":
                steps.append({"key": key, "label": label, "state": state,
                              "started": wm.stamp(), "errors": [], "detail": detail})
            elif steps:
                steps[-1].update(state=state, detail=detail or steps[-1].get("detail", ""))
            job["current"] = label if state == "running" else None

    def fail(message: str) -> None:
        step("", "", "error", message)
        run.update(status="error", error=message, finished=ai.now_iso())
        AI_STORE.save(run)
        with ALIGN_LOCK:
            job.update(status="error", current=None, message=message)

    run.update(status="running", started=ai.now_iso())
    AI_STORE.save(run)
    try:
        task = _task(tid)
        if task is None:
            return fail(f"{tid} no longer exists")
        rec = wm.record(slot)
        need = job.get("need") or review_requirement(task)
        step("manifest", f"Checking {rec['label']} deploys {_need_text(need)}")
        # Re-read at execution: the workspace may have been re-synced while queued.
        why = review_workspace_mismatch(rec, need)
        if why:
            return fail(f"{rec['label']} can no longer review {_need_text(need)}: {why}")
        if not job.get("held"):
            step("occupy", f"Occupying and aligning {rec['label']}")
            sub_id = start_workspace_job("occupy", slot,
                                         {"action": "occupy", "type": "task", "task_id": tid})
            sub = _wait_action(sub_id)
            if sub.get("status") != "done":
                return fail(f"occupy/align failed: {sub.get('message') or sub.get('status')}")
            occupied_here = True
        before = _draft_marks(_task(tid) or task)
        step("review", f"AI review in {rec['label']}")
        mode = job.get("mode") or "create"
        argv = ai.build_argv(ai.review_prompt(task, rec["path"], need["revision"], mode),
                             model=cfg["model"], profile="review",
                             allow_reads=cfg["allow_jira_github_reads"])
        result = ai.run_copilot(argv, cwd=rec["path"], timeout=cfg["review_timeout_s"],
                                on_line=_ai_progress(job))
        _ai_finish(run, result)
        after = _draft_marks(_task(tid) or task)
        fresh = [k for k, v in after.items() if before.get(k) != v]
        run["drafts"] = len(fresh)
        unchanged = (mode == "rereview" and not fresh
                     and ai.NO_CHANGE_MARK in (result.get("answer") or ""))
        if not result.get("error") and not fresh and not unchanged:
            run.update(status="error", error="the review finished but registered no draft")
        AI_STORE.save(run)
        if fresh:
            what = "initial auto review draft" if mode == "create" else "AI re-review draft"
            tm_note(tid, f"{what} done ({len(fresh)} draft(s), portal {run['id']})")
        outcome = run["error"] or (
            f"no new commits since the last review of {tid}; draft unchanged" if unchanged
            else f"{len(fresh)} draft(s) registered on {tid}")
        step("", "", "error" if run["error"] else "done", outcome)
        release = ""
        if occupied_here:
            step("free", f"Releasing {rec['label']}")
            sub = _wait_action(start_workspace_job("free", slot, {}))
            if sub.get("status") == "done":
                release = f"; {rec['label']} released"
                step("", "", "done")
            else:
                release = (f"; {rec['label']} NOT released "
                           f"({sub.get('message') or sub.get('status')}) - free it in Workspaces")
                step("", "", "error", release)
        with ALIGN_LOCK:
            job.update(status="error" if run["error"] else "done", current=None,
                       message=outcome + release)
    except Exception as error:                                   # noqa: BLE001
        fail(str(error))
        if occupied_here:
            try:
                start_workspace_job("free", slot, {})
            except Exception:                                    # noqa: BLE001
                pass


# --------------------------------------------------------- online review ----
# /pr-review remote mode over all of a task's PRs as ONE feature set, without
# a workspace. The portal verifies non-RTL + pins the heads, the AI writes one
# report into the drafts directory (its only writable place), and the portal
# - not the AI - registers it through the task owner's API.
REVIEW_MARK_RE = re.compile(r"^\s*" + re.escape(ai.REVIEW_MARK) + r"\s*(\{.*\})\s*$")
PR_URL_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)$")


def _pr_ident(url: str) -> tuple[str, str, int]:
    m = PR_URL_RE.match(url or "")
    if not m:
        raise ValueError(f"not a GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def _latest_draft(task: dict) -> dict | None:
    drafts = sorted(task.get("review_drafts") or [],
                    key=lambda d: d.get("updated_at") or d.get("created_at") or "")
    if not drafts:
        return None
    last = drafts[-1]
    return {"file": last.get("file"),
            "heads": {h.get("pr_url"): h.get("head_sha") for h in last.get("heads") or []
                      if h.get("pr_url") and h.get("head_sha")}}


def start_online_review(tid: str, mode: str = "create") -> dict:
    cfg = portal_settings.load()["ai"]
    if not cfg["enabled"]:
        raise ValueError("AI is turned off in Settings")
    if not cfg["allow_jira_github_reads"]:
        raise ValueError("online review reads the PRs from GitHub: enable "
                         "'Jira/GitHub reads' in Settings > AI first")
    if mode not in ai.REVIEW_MODES:
        raise ValueError("mode must be create or rereview")
    task = _review_task(tid)
    if mode == "create" and has_review_draft(task):
        raise ValueError(f"{tid} already has a review draft; use Re-review")
    if mode == "rereview" and not has_review_draft(task):
        raise ValueError(f"{tid} has no review draft yet; use Create review draft")
    for r in _open_reviews(task):
        owner, repo, _ = _pr_ident(r["pr_url"])
        if rtl_pr_guard.is_rtl_repo(repo):
            raise ValueError(f"{owner}/{repo} is an RTL repository: online review is "
                             "refused (RTL is reviewed only through the VM agent)")
    busy = _review_busy(tid)
    if busy:
        raise ValueError(busy)
    AI_STORE.reserve(cfg["daily_cap"])
    label = "Online review" if mode == "create" else "Online re-review"
    run = ai.new_run("online-review", section="work", task_id=tid, mode=mode,
                     profile="review-online",
                     question=f"{label} {tid}: {task.get('title', '')}")
    AI_STORE.save(run)
    title = f"{label} {tid}"
    job_id = enqueue_action({"kind": "online-review", "task_id": tid, "run_id": run["id"],
                             "mode": mode, "title": title, "slot": title, "current": "queued",
                             "message": "", "repos": [], "steps": [], "total": 3,
                             "link": f"task/{tid}"})
    return {"run_id": run["id"], "job": job_id, "mode": mode, "online": True}


def _pr_head(url: str) -> str:
    out = subprocess.run(["gh", "pr", "view", url, "--json", "headRefOid", "-q",
                          ".headRefOid"], capture_output=True, text=True, timeout=60,
                         check=False)
    return out.stdout.strip() if out.returncode == 0 else ""


def _draft_files(drafts: Path) -> dict:
    return {p.name: p.stat().st_mtime_ns for p in drafts.glob("*.md") if p.is_file()}


def _online_report(answer: str, tid: str, drafts: Path, before: dict) -> tuple[Path | None, list]:
    """The report the AI wrote or rewrote in this run (validated) and its proposed
    sibling PR URLs. Files untouched since ``before`` (a _draft_files snapshot)
    are never picked up."""
    claim: dict = {}
    for line in reversed((answer or "").splitlines()):
        m = REVIEW_MARK_RE.match(line)
        if m:
            try:
                claim = json.loads(m.group(1))
            except ValueError:
                claim = {}
            break
    siblings = [u for u in claim.get("siblings") or [] if isinstance(u, str)
                and PR_URL_RE.match(u)][:20] if isinstance(claim, dict) else []
    root = drafts.resolve()

    def fresh(path: Path) -> bool:
        return (path.is_file() and path.suffix == ".md" and path.name.startswith(tid + "-")
                and path.resolve().parent == root
                and before.get(path.name) != path.stat().st_mtime_ns)

    name = claim.get("report") if isinstance(claim, dict) else None
    if isinstance(name, str) and name:
        path = (root / name).resolve() if not os.path.isabs(name) else Path(name).resolve()
        if fresh(path):
            return path, siblings
    found = sorted((p for p in root.glob(f"{tid}-*.md") if fresh(p)),
                   key=lambda p: p.stat().st_mtime)
    return (found[-1] if found else None), siblings


def run_online_review(job: dict) -> None:
    run = AI_STORE.get(job.get("run_id", "")) or {}
    if not run:
        raise ValueError("online-review run record is missing")
    cfg = portal_settings.load()["ai"]
    tid, mode = job["task_id"], job.get("mode") or "create"

    def step(label: str, state: str = "running", detail: str = "") -> None:
        with ALIGN_LOCK:
            steps = job.setdefault("steps", [])
            if steps and steps[-1]["state"] == "running":
                steps[-1]["state"] = "done"
            if state == "running":
                steps.append({"key": label, "label": label, "state": state,
                              "started": wm.stamp(), "errors": [], "detail": detail})
            elif steps:
                steps[-1].update(state=state, detail=detail or steps[-1].get("detail", ""))
            job["current"] = label if state == "running" else None

    def finish(outcome: str, error: str = "") -> None:
        if error:
            run.update(status="error", error=error, finished=ai.now_iso())
        AI_STORE.save(run)
        step("", "error" if error else "done", error or outcome)
        with ALIGN_LOCK:
            job.update(status="error" if error else "done", current=None,
                       message=error or outcome)

    run.update(status="running", started=ai.now_iso())
    AI_STORE.save(run)
    try:
        task = _task(tid)
        if task is None:
            return finish("", f"{tid} no longer exists")
        reviews = _open_reviews(task)
        if not reviews:
            return finish("", f"{tid} has no open PRs left")
        step("Checking the PRs are not RTL and pinning their heads")
        prs = []
        for r in reviews:
            owner, repo, num = _pr_ident(r["pr_url"])
            kind, reason, head, base, _ = rtl_pr_guard.classify_pr_details(owner, repo, num)
            if kind != "software":
                return finish("", f"online review refused: {r['pr_url']} is {kind} "
                                  f"({reason}); RTL/unclassified PRs need the VM agent")
            prs.append({"url": r["pr_url"], "repo": f"{owner}/{repo}", "head": head,
                        "base": r.get("base") or "", "branch": r.get("branch") or "",
                        "author": r.get("author") or ""})
        baseline = _latest_draft(task) if mode == "rereview" else None
        if baseline and all(baseline["heads"].get(p["url"], "").lower() == p["head"].lower()
                            for p in prs):
            run.update(status="done", finished=ai.now_iso(), answer=(
                f"No PR of {tid} has new commits since the last draft; nothing to re-review."))
            return finish(f"no new commits since the last review of {tid}; draft unchanged")
        drafts = tm.review_drafts_root()
        drafts.mkdir(parents=True, exist_ok=True)
        skills = Path.home() / ".copilot" / "skills"
        reads = tuple(str(d) for d in (skills, drafts.parent) if d.is_dir())
        before = _draft_files(drafts)
        step(f"AI reviewing {len(prs)} PR(s) as one feature set")
        argv = ai.build_argv(ai.online_review_prompt(task, prs, str(drafts), mode, baseline),
                             model=cfg["model"], profile="review-online",
                             allow_reads=cfg["allow_jira_github_reads"],
                             write_dir=str(drafts), read_dirs=reads)
        with ai_cancellable(job) as cancel:
            result = ai.run_copilot(argv, cwd=str(drafts), timeout=cfg["review_timeout_s"],
                                    on_line=_ai_progress(job), cancel=cancel)
        _ai_finish(run, result)
        if run["error"]:
            return finish("", run["error"])
        answer = run.get("answer") or ""
        report, siblings = _online_report(answer, tid, drafts, before)
        if report is None:
            if mode == "rereview" and ai.NO_CHANGE_MARK in answer:
                return finish(f"no relevant changes since the last review of {tid}; "
                              "draft unchanged")
            return finish("", "the AI finished but wrote no report into the drafts directory")
        step("Registering the draft on the task")
        draft = tm.attach_review_draft(tid, str(report), {p["url"]: p["head"] for p in prs})
        on_task = {r.get("pr_url") for r in task.get("reviews") or []}
        proposed = [u for u in dict.fromkeys(siblings) if u not in on_task]
        moved = [p["url"] for p in prs if (h := _pr_head(p["url"])) and h.lower() != p["head"].lower()]
        run.update(drafts=1, draft_id=draft.get("id"), report=report.name,
                   proposed_prs=proposed, moved_prs=moved)
        what = "AI online review draft" if mode == "create" else "AI online re-review draft"
        note = f"{what} done ({report.name}, {len(prs)} PR(s) as one feature set, portal {run['id']})"
        if proposed:
            note += ("; related PRs not on this task (DEPENDS_ON/feature siblings) - "
                     "consider adding them: " + ", ".join(proposed))
        if moved:
            note += "; PR head moved during the review, re-review recommended: " + ", ".join(moved)
        tm_note(tid, note)
        outcome = f"draft registered on {tid} ({report.name})"
        if proposed:
            outcome += f"; {len(proposed)} related PR(s) not on the task proposed in a note"
        if moved:
            outcome += f"; {len(moved)} PR(s) moved during the review"
        finish(outcome)
    except Exception as error:                                   # noqa: BLE001
        finish("", str(error))


# ---------------------------------------------------------- review chat ----
# Interactive review: a server-side /pr-review local conversation held in a
# review workspace. Every user message is one queued ``--resume`` turn; the
# chat record (state/ai/chats/rc-*.json) ties the session, the workspace and
# the turns together. Posting to GitHub needs a preview the user confirmed.
REVIEW_CHAT_IDLE_S = 24 * 3600
REVIEW_CHAT_SWEEP_EVERY = 600
REVIEW_CHAT_LOCK = _threading.RLock()
CHAT_OPS = {"open", "turn", "preview", "submit", "end"}
# A start cut off by a portal restart: the chat and its workspace are kept
# and the user can Retry start or End review (nothing is replayed unasked).
CHAT_INTERRUPTED = "interrupted"


def _chat_update(chat_id: str, **fields) -> dict:
    with REVIEW_CHAT_LOCK:
        chat = AI_STORE.get_chat(chat_id)
        if chat is None:
            raise ValueError("unknown review chat")
        chat.update(fields)
        return AI_STORE.save_chat(chat)


def _chat_job(chat_id: str) -> dict | None:
    with ALIGN_LOCK:
        for job in ALIGN_JOBS.values():
            if (job.get("kind") == "review-chat" and job.get("chat_id") == chat_id
                    and job.get("status") in {"queued", "running"}):
                return dict(job)
    return None


def review_chat_public(chat: dict) -> dict:
    turns = []
    for run_id in chat.get("turns") or []:
        run = AI_STORE.get(run_id)
        turns.append(ai_public(run) if run else
                     {"id": run_id, "status": "error", "error": "this turn has expired"})
    busy = _chat_job(chat["id"])
    return {**chat, "turns": turns, "busy": bool(busy),
            "busy_op": (busy or {}).get("op"), "progress": (busy or {}).get("current"),
            "usage": ai_usage_payload()}


def review_chat_summary(chat: dict) -> dict:
    return {k: chat.get(k) for k in ("id", "task_id", "status", "workspace", "created",
                                    "last_activity", "ended", "end_reason")}


def _enqueue_chat(chat: dict, op: str, run_id: str | None = None, **extra) -> str:
    title = {"open": "Interactive review", "turn": "Review chat", "preview": "Review preview",
             "submit": "Submit review", "end": "End interactive review"}[op]
    title = f"{title} {chat['task_id']} \u00b7 {chat.get('workspace', '')}"
    return enqueue_action({"kind": "review-chat", "op": op, "chat_id": chat["id"],
                           "run_id": run_id, "task_id": chat["task_id"],
                           "ws_id": chat.get("ws_id"), "title": title, "slot": title,
                           "current": "queued", "message": "", "repos": [], "total": 0,
                           "link": f"review-chat/{chat['id']}", **extra})


def start_review_chat(tid: str) -> dict:
    cfg = portal_settings.load()["ai"]
    if not cfg["enabled"]:
        raise ValueError("AI is turned off in Settings")
    task = _review_task(tid)
    existing = AI_STORE.chats(tid, open_only=True)
    if existing:
        return {"chat_id": existing[0]["id"], "resumed": True,
                "workspace": existing[0].get("workspace")}
    busy = _review_busy(tid)
    if busy:
        raise ValueError(busy)
    need = review_requirement(task)
    rec, held = pick_review_workspace(tid, need)
    AI_STORE.reserve(cfg.get("interactive_daily_cap", 150), "interactive")
    chat = ai.new_chat(tid, workspace=rec["label"], ws_id=rec["id"], ws_slot=rec["slot"],
                       ws_path=rec["path"], need=need, held=held,
                       title=task.get("title", ""))
    run = ai.new_run("review-turn", section="work", task_id=tid, chat_id=chat["id"],
                     role="open", question="Start the interactive review")
    chat["turns"] = [run["id"]]
    AI_STORE.save(run)
    AI_STORE.save_chat(chat)
    job_id = _enqueue_chat(chat, "open", run["id"])
    return {"chat_id": chat["id"], "job": job_id, "resumed": False,
            "workspace": rec["label"], "manifest": _need_text(need)}


def review_chat_action(chat_id: str, op: str, body: dict) -> dict:
    """Queue a user message, a submit preview/confirmation, a start retry or the end."""
    cfg = portal_settings.load()["ai"]
    if op not in {"turn", "preview", "submit", "end", "retry"}:
        raise ValueError("unknown review chat action")
    with REVIEW_CHAT_LOCK:
        chat = AI_STORE.get_chat(chat_id)
        if chat is None:
            raise LookupError("unknown review chat")
        if chat["status"] == "ended":
            raise ValueError("this interactive review has ended")
        if _chat_job(chat_id):
            raise ValueError("wait for the current answer first")
        if op == "end":
            free = body.get("free", True)
            if not isinstance(free, bool):
                raise ValueError("free must be true or false")
            return {"job": _enqueue_chat(chat, "end", None, free=free, reason="ended by user")}
        if not cfg["enabled"]:
            raise ValueError("AI is turned off in Settings")
        if op == "retry":
            if chat["status"] != CHAT_INTERRUPTED:
                raise ValueError("only an interrupted start can be retried")
            AI_STORE.reserve(cfg.get("interactive_daily_cap", 150), "interactive")
            run = ai.new_run("review-turn", section="work", task_id=chat["task_id"],
                             chat_id=chat_id, role="open",
                             question="Start the interactive review (retry)")
            AI_STORE.save(run)
            chat["turns"].append(run["id"])
            chat.update(status="starting", last_activity=ai.now_iso(), interrupted=None)
            AI_STORE.save_chat(chat)
            return {"job": _enqueue_chat(chat, "open", run["id"]), "run_id": run["id"]}
        if chat["status"] != "open" or not chat.get("session_id"):
            raise ValueError("the review session is not ready")
        extra: dict = {}
        if op == "turn":
            message = body.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("a message is required")
            question, role = message.strip()[:8000], "turn"
        elif op == "preview":
            question, role = "Submit: preview the final review", "preview"
        else:
            preview = AI_STORE.get(str(body.get("preview_run") or ""))
            if (not preview or preview.get("chat_id") != chat_id
                    or preview["id"] != chat.get("preview_run")
                    or preview.get("status") != "done"):
                raise ValueError("confirm the latest submit preview first")
            if chat.get("turns") and chat["turns"][-1] != preview["id"]:
                raise ValueError("the conversation moved on after that preview; "
                                 "press Submit again for a fresh preview")
            question, role = "Submit: post the confirmed review", "submit"
            extra["preview_run"] = preview["id"]
        AI_STORE.reserve(cfg.get("interactive_daily_cap", 150), "interactive")
        run = ai.new_run("review-turn", section="work", task_id=chat["task_id"],
                         chat_id=chat_id, role=role, question=question)
        AI_STORE.save(run)
        chat["turns"].append(run["id"])
        chat["last_activity"] = ai.now_iso()
        AI_STORE.save_chat(chat)
        return {"job": _enqueue_chat(chat, op, run["id"], **extra), "run_id": run["id"]}


def _free_chat_workspace(chat: dict) -> str:
    if not chat.get("occupied_here"):
        return ""
    sub = _wait_action(start_workspace_job("free", chat["ws_slot"], {}))
    if sub.get("status") == "done":
        return f"{chat['workspace']} released"
    return (f"{chat['workspace']} NOT released ({sub.get('message') or sub.get('status')})"
            " - free it in Workspaces")


def _end_chat(job: dict, chat: dict, reason: str, free: bool) -> str:
    release = _free_chat_workspace(chat) if free else ""
    if not free and chat.get("occupied_here"):
        release = f"{chat['workspace']} kept for {chat['task_id']}"
    _chat_update(chat["id"], status="ended", ended=ai.now_iso(), end_reason=reason,
                 occupied_here=bool(chat.get("occupied_here")) and not release.endswith("released"))
    tm_note(chat["task_id"], f"interactive AI review ended ({reason}; portal {chat['id']})")
    message = f"interactive review of {chat['task_id']} ended ({reason})" + (
        f"; {release}" if release else "")
    with ALIGN_LOCK:
        job.update(status="error" if "NOT released" in release else "done",
                   current=None, message=message)
    return message


def run_review_chat_job(job: dict) -> None:
    op = job.get("op")
    if op not in CHAT_OPS:
        raise ValueError("unknown review chat operation")
    chat = AI_STORE.get_chat(job.get("chat_id", ""))
    if chat is None:
        raise ValueError("review chat record is missing")
    if op == "end":
        _end_chat(job, chat, job.get("reason") or "ended", bool(job.get("free", True)))
        return
    run = AI_STORE.get(job.get("run_id") or "")
    if run is None:
        raise ValueError("review chat turn is missing")
    cfg = portal_settings.load()["ai"]

    def progress(text: str) -> None:
        with ALIGN_LOCK:
            job["current"] = text

    run.update(status="running", started=ai.now_iso())
    AI_STORE.save(run)
    try:
        task = _task(chat["task_id"])
        if task is None:
            raise ValueError(f"{chat['task_id']} no longer exists")
        profile = "review"
        if op == "open":
            rec = wm.record(chat["ws_slot"])
            need = chat["need"]
            progress(f"checking {rec['label']} deploys {_need_text(need)}")
            why = review_workspace_mismatch(rec, need)
            if why:
                raise ValueError(f"{rec['label']} can no longer review {_need_text(need)}: {why}")
            if (not chat.get("held") and not chat.get("occupied_here")
                    and wm.slot_of_task(chat["task_id"]) == chat["ws_slot"]):
                # A start interrupted by a restart already occupied it.
                chat = _chat_update(chat["id"], occupied_here=True)
            if not chat.get("held") and not chat.get("occupied_here"):
                progress(f"occupying and aligning {rec['label']}")
                sub = _wait_action(start_workspace_job(
                    "occupy", chat["ws_slot"],
                    {"action": "occupy", "type": "task", "task_id": chat["task_id"]}))
                if sub.get("status") != "done":
                    raise ValueError(f"occupy/align failed: "
                                     f"{sub.get('message') or sub.get('status')}")
                chat = _chat_update(chat["id"], occupied_here=True)
            prompt = ai.chat_open_prompt(task, chat["ws_path"], need["revision"])
            resume = None
        elif op == "turn":
            prompt, resume = ai.chat_turn_prompt(run["question"]), chat["session_id"]
        elif op == "preview":
            prompt, resume = ai.submit_preview_prompt(), chat["session_id"]
        else:
            preview = AI_STORE.get(job.get("preview_run") or "") or {}
            if not preview.get("answer"):
                raise ValueError("the confirmed preview is no longer available")
            prompt, resume = ai.submit_prompt(preview["answer"]), chat["session_id"]
            profile = "review-submit"
        progress("AI is working")
        argv = ai.build_argv(prompt, model=cfg["model"], profile=profile,
                             allow_reads=cfg["allow_jira_github_reads"], resume=resume)
        # Opening, chatting and previewing only talk to the model; a submit
        # posts to GitHub and must not be cut off half-way.
        with (ai_cancellable(job) if op != "submit"
              else _contextlib.nullcontext(None)) as cancel:
            result = ai.run_copilot(argv, cwd=chat["ws_path"], timeout=cfg["review_timeout_s"],
                                    on_line=_ai_progress(job), cancel=cancel)
        _ai_finish(run, result)
    except Exception as error:                                   # noqa: BLE001
        run.update(status="error", error=str(error), finished=ai.now_iso())
        AI_STORE.save(run)
    fields: dict = {"last_activity": ai.now_iso()}
    ok = run.get("status") == "done"
    if op == "open":
        if ok and run.get("session_id"):
            fields.update(status="open", session_id=run["session_id"])
        else:
            chat = AI_STORE.get_chat(chat["id"]) or chat
            _end_chat(job, chat, f"failed to start: {run.get('error') or 'no session'}",
                      free=True)
            with ALIGN_LOCK:
                job.update(status="error")
            return
    elif ok and run.get("session_id"):
        fields["session_id"] = run["session_id"]
    if op == "preview" and ok:
        fields["preview_run"] = run["id"]
    if op == "submit" and ok:
        fields.update(submitted_run=run["id"], preview_run=None)
        tm_note(chat["task_id"], f"interactive AI review submitted to GitHub "
                                 f"(portal {chat['id']})")
    _chat_update(chat["id"], **fields)
    first = (run.get("answer") or "").strip().splitlines() or [""]
    with ALIGN_LOCK:
        job.update(status="done" if ok else "error", current=None,
                   message=run.get("error") or first[0][:200])


def sweep_review_chats(now: float | None = None) -> list[str]:
    """End chats idle past REVIEW_CHAT_IDLE_S (workspace freed, draft kept)."""
    now = time.time() if now is None else now
    ended = []
    for chat in AI_STORE.chats(open_only=True):
        try:
            last = datetime.fromisoformat(chat.get("last_activity") or chat["created"])
        except (KeyError, ValueError):
            continue
        if now - last.timestamp() < REVIEW_CHAT_IDLE_S:
            continue
        with REVIEW_CHAT_LOCK:
            if _chat_job(chat["id"]):
                continue
            _enqueue_chat(chat, "end", None, free=True,
                          reason="idle 24 h - workspace released, draft kept")
        ended.append(chat["id"])
    return ended


def recover_review_chats() -> None:
    """After a restart, keep every chat (the conversation survives a restart).

    Chat jobs still queued are resumed by the action queue and are left alone.
    A turn whose job was cut off mid-run is marked failed ("send it again");
    the chat stays open on its session. A start cut off mid-run becomes
    ``interrupted``: the workspace stays with the task (an occupy that already
    happened is recorded) and the panel offers Retry start / End review. The
    24 h idle release still applies.
    """
    with ALIGN_LOCK:
        pending = {job.get("run_id") for job in ALIGN_JOBS.values()
                   if job.get("kind") == "review-chat"
                   and job.get("status") in {"queued", "running"}}
    for chat in AI_STORE.chats(open_only=True):
        for run_id in chat.get("turns") or []:
            run = AI_STORE.get(run_id)
            if run and run.get("status") in {"queued", "running"} and run_id not in pending:
                run.update(status="error", error="interrupted by a portal restart; "
                                                 "send the message again",
                           finished=ai.now_iso())
                AI_STORE.save(run)
        if chat.get("status") != "starting" or _chat_job(chat["id"]):
            continue
        fields: dict = {"status": CHAT_INTERRUPTED,
                        "interrupted": "start interrupted by a portal restart"}
        if (not chat.get("held") and not chat.get("occupied_here")
                and wm.slot_of_task(chat["task_id"]) == chat.get("ws_slot")):
            fields["occupied_here"] = True
        _chat_update(chat["id"], **fields)


def run_review_chat_sweeper(stop: _threading.Event) -> None:
    while not stop.wait(REVIEW_CHAT_SWEEP_EVERY):
        try:
            sweep_review_chats()
        except Exception:                                      # noqa: BLE001
            pass


def live_payload() -> dict:
    return {"links": LIVE.snapshot() if LIVE else {}, "changed": LIVE_STATE["changed"]}


ACTION_TITLES = {"pr-scan": "Scan PRs", "review-scan": "Scan reviews", "align": "Align workspace",
                 "pldm": "Submit PLDM test", "vm-refresh": "Refresh machine activity",
                 "ai": "AI", "auto-review": "AI review draft",
                 "online-review": "AI online review",
                 "review-chat": "Interactive review",
                 "vm-cancel": "Cancel machine job"}


def action_title(job: dict) -> str:
    if job.get("title"):
        return str(job["title"])
    kind = job.get("kind", "action")
    head = job.get("action") if kind == "workspace-action" else ACTION_TITLES.get(kind, kind)
    tail = job.get("slot") or job.get("target") or ""
    return f"{head} \u00b7 {tail}" if tail else str(head)


def notify_action(job: dict) -> None:
    """Persisted queue transition -> notification (AI kinds report under 'ai')."""
    status = job.get("status")
    if status not in {"queued", "running", "cancelling", "done", "error",
                      "approval_required", "cancelled"}:
        return
    nonterminal = status in {"queued", "running", "cancelling"}
    if nonterminal and not portal_settings.load()["notify"]["status_updates"]:
        return
    if job.get("kind") == "vm-refresh" and status not in {"error", "cancelled"}:
        return
    # Chat replies show up in the open panel; only failures pop up.
    if (status not in {"error", "cancelled"} and job.get("kind") == "review-chat"
            and job.get("op") in {"turn", "preview"}):
        return
    label = action_title(job)
    kind = "ai" if job.get("kind") in {"ai", "auto-review", "online-review",
                                       "review-chat"} else "queue"
    text = {"queued": "queued", "running": "started", "cancelling": "cancelling",
            "cancelled": "cancelled", "done": "finished", "error": "failed",
            "approval_required": "needs approval"}[status]
    EVENTS.publish(kind, f"{label} {text}", (job.get("message") or "")[:300],
                   {"done": "ok", "error": "bad", "cancelled": "warn",
                    "approval_required": "warn"}.get(status, ""),
                   job.get("link") or "")


def execute_and_notify(job: dict, store_lock=None) -> None:
    execute_action(job, store_lock)


def start_vm_cancel_job(job_id: str) -> str:
    """Queue stopping one running machine job (vm-delegate or machine-pilot)."""
    if jobs_view.is_pilot_id(job_id):
        return start_pilot_stop_job(job_id)
    rows = {j.get("id"): j for j in vd.jobs()}
    rec = rows.get(job_id)
    if rec is None:
        raise LookupError("unknown machine job")
    if rec.get("status") != "running":
        raise ValueError(f"machine job is not running (status: {rec.get('status')})")
    target = rec.get("target") or "vm"
    what = f"{rec.get('kind') or 'job'} {rec.get('ws_label') or rec.get('tag') or ''}".strip()
    slot = f"Cancel {what} \u00b7 {target}"
    return enqueue_action({"kind": "vm-cancel", "slot": slot, "title": slot,
                           "vm_job": job_id, "target": target,
                           "current": None, "total": 1, "message": "", "repos": []})


def start_pilot_stop_job(job_id: str) -> str:
    """Queue stopping one running machine-pilot job; refused commands never queue."""
    if jobs_view.pilot_jobs is None:
        raise LookupError("machine-pilot is not installed")
    rec = jobs_view.pilot_jobs.find(jobs_view.pilot_id(job_id))
    if rec is None:
        raise LookupError("unknown machine job")
    if rec["status"] != "running":
        raise ValueError(f"machine job is not running (status: {rec['status']})")
    if rec["stale"]:
        raise ValueError("the BMC reading is unverified; refresh the machine before stopping")
    if rec["stop_refusal"]:
        raise ValueError(rec["stop_refusal"])
    target = rec["target"] or jobs_view.UNATTRIBUTED
    slot = f"Stop {rec['source']} job {rec['tag']} \u00b7 {target}"
    return enqueue_action({"kind": "vm-cancel", "slot": slot, "title": slot,
                           "vm_job": job_id, "target": target,
                           "current": None, "total": 1, "message": "", "repos": []})


def run_pilot_stop(job: dict) -> None:
    """TERM a machine-pilot job through its owner API (verifies the pid first)."""
    with ALIGN_LOCK:
        job["current"] = f"stopping {job['vm_job'].split(':')[-1]}"
    res = jobs_view.pilot_jobs.stop(jobs_view.pilot_id(job["vm_job"]))
    observe_pilot_jobs()
    EVENTS.publish_domains(["delegation"])
    with ALIGN_LOCK:
        job.update(current=None, result={k: res[k] for k in ("result", "error") if res.get(k)},
                   status="error" if res.get("error") else "done",
                   message=res.get("error") or res.get("result", "").lower().replace("_", " "))


def run_vm_cancel(job: dict) -> None:
    """Stop a running delegated job via vd.portal_cancel (mctl cancel + re-probe)."""
    with ALIGN_LOCK:
        job["current"] = f"stopping job on {job['target']}"
    res = vd.portal_cancel(job["vm_job"])
    observe_machine_jobs()
    result = {k: res.get(k) for k in ("cancel", "acted", "error") if res.get(k)}
    with ALIGN_LOCK:
        job.update(current=None, result=result,
                   status="error" if res.get("error") else "done",
                   message=res.get("error") or res.get("acted") or "cancel requested")


def start_vm_refresh_job(target: str | None = None) -> str:
    """Queue a machine activity probe and return its id."""
    slot = f"Machine activity \u00b7 {target}" if target else "Machine activity"
    job = {
        "kind": "vm-refresh", "slot": slot,
        "target": target or "",
        "current": None, "total": 0, "message": "", "repos": [],
    }
    return enqueue_action(job)


# --- Flash FW --------------------------------------------------------------
# Images come from the fw-deploy store; the flash itself is fw-flasher's
# fw_flash.py, run as a vm-delegate local-runner job (kind "flash") so it shows
# in the machine's Jobs list with its full log (BMC orchestrator journal
# included) and survives a portal restart.
FW_DEPLOY = Path.home() / ".copilot/skills/fw-deploy/scripts/fw_deploy.py"
FW_FLASH = Path.home() / ".copilot/skills/fw-flasher/scripts/fw_flash.py"
FLASH_METHODS = ("bmc", "pcie", "spi-ftdi", "spi-bmc")
FLASH_DISCOVER_TIMEOUT = 150
FLASH_WATCH_MAX = 4 * 3600


def _tool_error(p: subprocess.CompletedProcess, what: str) -> str:
    lines = [l for l in (p.stderr or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else f"{what} failed (rc={p.returncode})"


def flash_images() -> dict:
    """The fw-deploy local image store (read-only listing, verified identities)."""
    if not FW_DEPLOY.exists():
        raise RuntimeError("fw-deploy is not installed")
    p = subprocess.run([sys.executable, str(FW_DEPLOY), "list", "--json"],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(_tool_error(p, "fw-deploy list"))
    data = json.loads(p.stdout or "{}")
    images = data.get("images") or []
    return {"root": data.get("root") or "", "images": images,
            "projects": sorted({i.get("project") or "" for i in images} - {""})}


def _flash_target(target) -> str:
    if vd is None:
        raise RuntimeError("vm-delegate is not installed")
    if not isinstance(target, str) or target not in vd.targets():
        raise ValueError(f"'{target}' is not a registered machine")
    if target in vd.vm_targets():
        raise ValueError(f"'{target}' is a cloud VM; flashing needs a lab machine")
    return target


def flash_discover(target) -> dict:
    """Read-only: which flash methods work on this machine, and its OAMs."""
    target = _flash_target(target)
    try:
        p = subprocess.run([sys.executable, str(FW_FLASH), "discover", "--target", target, "--json"],
                           capture_output=True, text=True, timeout=FLASH_DISCOVER_TIMEOUT,
                           env=vd.agent_env()[0])
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"discovery on {target} timed out ({FLASH_DISCOVER_TIMEOUT}s)") from e
    if p.returncode != 0:
        raise RuntimeError(_tool_error(p, "fw-flash discover"))
    return json.loads(p.stdout)


def flash_start(payload: dict) -> dict:
    """Validate a Flash FW request and start it as a local-runner machine job."""
    allowed = {"target", "method", "image", "oams", "force", "reset_verify"}
    if not isinstance(payload, dict) or not set(payload) <= allowed:
        raise ValueError("send {target, method, image, oams[, force, reset_verify]}")
    target = _flash_target(payload.get("target"))
    method = payload.get("method")
    if method not in FLASH_METHODS:
        raise ValueError(f"method must be one of {', '.join(FLASH_METHODS)}")
    oams = payload.get("oams")
    if (not isinstance(oams, list) or not oams or len(oams) > 64
            or any(not isinstance(o, int) or isinstance(o, bool) or not 0 <= o < 64 for o in oams)):
        raise ValueError("oams must be a non-empty list of OAM indexes")
    oams = sorted(set(oams))
    if method == "spi-bmc" and len(oams) != 1:
        raise ValueError("spi-bmc writes one flash part; select exactly one")
    for flag in ("force", "reset_verify"):
        if not isinstance(payload.get(flag, False), bool):
            raise ValueError(f"{flag} must be true or false")
    # The image is named by its store directory and resolved against a fresh,
    # verified listing -- the client never supplies a path that gets flashed.
    want = payload.get("image")
    img = next((i for i in flash_images()["images"] if i.get("directory") == want), None)
    if img is None:
        raise ValueError("unknown image; pick one from the fw-deploy store")
    if not img.get("valid"):
        raise ValueError(f"image failed verification: {img.get('error') or 'invalid'}")
    argv = [sys.executable, str(FW_FLASH), "run", "--target", target, "--method", method,
            "--image", img["path"], "--sha256", img["sha256"],
            "--events", "{events}", "--job-id", "{job}"]
    for o in oams:
        argv += ["--oam", str(o)]
    if payload.get("force"):
        argv.append("--force")
    if payload.get("reset_verify"):
        argv.append("--reset-verify")
    name = "/".join(x for x in (img.get("project"), img.get("deployment"),
                                img.get("platform"), img.get("variant")) if x)
    label = f"fw-flash {method} {name or img.get('image')} \u2192 OAM {','.join(map(str, oams))}"
    res = vd.start_local_job(target, "flash", argv, label=label, meta={
        "image": name or img.get("image") or "", "image_dir": img["directory"],
        "sha256": img["sha256"], "method": method, "oams": oams,
        "reset_verify": bool(payload.get("reset_verify")), "force": bool(payload.get("force"))})
    if res.get("error"):
        raise FileExistsError(res["error"]) if res.get("job_id") else ValueError(res["error"])
    job = res["job"]
    LIVE_STATE["changed"] = time.time()
    observe_machine_jobs()
    threading.Thread(target=_flash_watch, args=(job,), daemon=True,
                     name=f"flash-watch-{job['id']}").start()
    return {"job": job["id"], "target": target}


def _flash_watch(job: dict) -> None:
    """Notify once the flash settles (the Jobs list re-reads on live.changed)."""
    t0 = time.time()
    while time.time() - t0 < FLASH_WATCH_MAX:
        time.sleep(3)
        try:
            st = vd.local_status(job)
        except Exception:                                   # noqa: BLE001
            continue
        if st["status"] == "running":
            continue
        LIVE_STATE["changed"] = time.time()
        observe_machine_jobs()
        return


def flash_status(job_id: str) -> dict:
    """Progress of one flash job, folded from its events + the log tail."""
    if vd is None:
        raise RuntimeError("vm-delegate is not installed")
    job = vd._job_by_id(job_id or "")
    if not vd.is_local(job) or job.get("kind") != "flash":
        raise LookupError("unknown flash job")
    st = vd.local_status(job)
    oams = {str(o): {"oam": o, "state": "pending", "pct": None, "message": ""}
            for o in job.get("oams") or []}
    phases, result, image, cancel = [], None, {}, None
    for e in vd.local_events(job):
        kind = e.get("type")
        if kind == "phase":
            phases.append({"phase": e.get("phase"), "message": e.get("message") or "",
                           "t": e.get("t")})
        elif kind == "oam":
            row = oams.setdefault(str(e.get("oam")), {"oam": e.get("oam"), "state": "pending",
                                                      "pct": None, "message": ""})
            row["state"] = e.get("state") or row["state"]
            if e.get("pct") is not None:
                row["pct"] = e["pct"]
            if e.get("message"):
                row["message"] = e["message"]
            for k in ("before", "after"):
                if e.get(k):
                    row[k] = e[k]
        elif kind == "image":
            image = {k: e.get(k) for k in ("sha256", "size", "expected", "path")}
        elif kind == "result":
            result = {k: e.get(k) for k in ("ok", "error", "cancelled", "expected")}
            for i, r in (e.get("oams") or {}).items():
                row = oams.setdefault(str(i), {"oam": int(i), "pct": None, "message": ""})
                row["state"] = r.get("state") or row.get("state")
                if r.get("reason"):
                    row["message"] = r["reason"]
                for k in ("before", "after", "booted"):
                    if r.get(k):
                        row[k] = r[k]
        elif kind == "cancel":
            cancel = {"accepted": bool(e.get("accepted")), "t": e.get("t")}
    phase = phases[-1]["phase"] if phases else ""
    log = vd._read_local(job, 96 * 1024)
    return {"id": job["id"], "target": job.get("target"), "method": job.get("method"),
            "image": job.get("image"), "sha256": job.get("sha256"), "started": job.get("started"),
            "reset_verify": job.get("reset_verify"), "unit": job.get("unit") or "",
            "status": st["status"], "rc": st["rc"], "finished": st["finished"],
            "phase": phase, "phases": phases, "oams": [oams[k] for k in sorted(oams, key=int)],
            "result": result, "image_info": image, "cancel": cancel,
            "cancellable": st["status"] == "running" and phase not in vd.LOCAL_UNSTOPPABLE,
            "log": "\n".join(log.get("text", "").splitlines()[-200:])}


def render_flash_page() -> str:
    """The Flash FW progress pop-up (assets/flash.js)."""
    v = asset_stamp()
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Flash FW</title>'
            f'<link rel="stylesheet" href="/assets/portal.css?v={v}"></head>'
            '<body class="flash-page"><main id="flash-root" class="flash-window" '
            'aria-live="polite"></main>'
            f'<script src="/assets/flash.js?v={v}"></script></body></html>')


# gh review state → (banner icon, tone) for each streamed review-scan row.
REVIEW_SCAN_ICONS = {
    "approved": ("\u2713", "ok"), "merged": ("\u2713", "ok"),
    "commented": ("\U0001f4ac", "warn"),
    "waiting-for-re-review": ("\u21bb", "warn"),
    "changes-requested": ("\u2715", "bad"), "closed": ("\u2715", "bad"),
    "error": ("\u26a0", "bad"),
}


def run_review_scan(job: dict) -> None:
    """Background worker: gh-probe selected open review PRs, streaming one row
    per changed/errored PR into the job so the UI banner can narrate progress.

    Releases REVIEW_SCAN_LOCK (acquired at execution time) when done.
    """
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    def progress(i, total, tid, label, status, detail):
        if status is None:                       # about to fetch this PR
            upd(current=f"{label} ({i}/{total})", total=total, scanned=i)
            return
        if status == "nochange":
            return
        icon, tone = REVIEW_SCAN_ICONS.get(status, ("\u00b7", "warn"))
        add_repo({"name": f"{tid} {label}", "action": status,
                  "icon": icon, "tone": tone, "detail": detail})

    try:
        result = tm.portal_review_scan(
            progress=progress, task_ids=job.get("task_ids"))
    except Exception as e:
        upd(status="error", finished=tm.NOW, current=None, message=str(e))
        return
    finally:
        REVIEW_SCAN_LOCK.release()

    parts = [f"{result['scanned']} PR(s) checked"]
    if result["changed"]:
        parts.append(f"{len(result['changed'])} changed")
    if result.get("removed"):
        parts.append(f"{len(result['removed'])} closed→removed")
    if result["completed"]:
        parts.append(f"{len(result['completed'])} merged→done")
    if result.get("cancelled"):
        parts.append(f"{len(result['cancelled'])} emptied→cancelled")
    if result["errors"]:
        parts.append(f"{len(result['errors'])} error(s)")
    upd(status="done", finished=tm.NOW, current=None, result=result,
        message=" \u00b7 ".join(parts))


def start_review_scan_job(task_ids: list[str] | None) -> str:
    """Queue a review scan for selected tasks, or every open review PR (None)."""
    job = {
        "kind": "review-scan", "slot": "reviews",
        "task_ids": task_ids,
        "current": None, "total": 0, "scanned": 0, "message": "", "repos": [],
    }
    return enqueue_action(job)


def start_alignment_job(task_id: str, slot_root: str, store_lock=None) -> str:
    """Queue workspace alignment and return its id."""
    job = {
        "task_id": task_id, "slot": slot_root, "kind": "align",
        "current": None, "total": 0, "message": "", "repos": [],
    }
    return enqueue_action(job)


def start_reset_job(slot_root: str, store_lock=None) -> str:
    """Compatibility entry point for guarded Free."""
    return start_workspace_job("free", slot_root, store_lock=store_lock)


def run_workspace_sync(job: dict) -> None:
    """Compatibility worker using the same guarded Sync as the portal."""
    try:
        wm.workspace_request(job["slot"], "sync")
        status, message = "done", "synced"
    except ValueError as e:
        status, message = "error", str(e)
    with ALIGN_LOCK:
        job.update(status=status, message=message, current=None, finished=wm.stamp())


def start_sync_job(slot_root: str) -> str:
    """Compatibility entry point for guarded Sync."""
    return start_workspace_job("sync", slot_root)


WORKSPACE_BUSY: set[str] = set()


def start_workspace_job(action: str, slot: str | None = None,
                        payload: dict | None = None, store_lock=None,
                        *, scope: str = "local") -> str:
    if action not in {"refresh", "sync", "free", "occupy"}:
        raise ValueError(f"unknown workspace action: {action}")
    if scope not in {"local", "remote"}:
        raise ValueError(f"unknown workspace scope: {scope}")
    if slot:
        slots = [wm.record(slot)["slot"]]
    elif action == "refresh":
        slots = [r["slot"] for r in wm.roster(cached_only=True)
                 if bool(r["target"]) == (scope == "remote")]
    else:
        raise ValueError("a workspace is required")
    if action in {"free", "sync"}:
        payload = payload or {}
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise ValueError("force must be a boolean")
        approval_job = payload.get("approval_job")
        approved = None
        if force:
            if not isinstance(approval_job, str) or not approval_job:
                raise ValueError("an approval job is required for a forced return")
            with ALIGN_LOCK:
                previous = ALIGN_JOBS.get(approval_job)
                if (not previous or previous.get("kind") != "workspace-action"
                        or previous.get("action") != action
                        or previous.get("slot") != slots[0]
                        or previous.get("status") != "approval_required"):
                    raise ValueError("workspace approval is no longer available")
                approval = previous.get("approval") or {}
                approved = approval.get("token")
                if not isinstance(approved, str) or not re.fullmatch(r"[0-9a-f]{64}", approved):
                    raise ValueError("invalid workspace approval")
                if approval.get("blockers"):
                    raise ValueError("resolve the reported safety blockers before returning")
        elif approval_job is not None:
            raise ValueError("workspace approval requires force=true")
        payload = {"action": action, "force": force, "approved": approved}
    job_id = enqueue_action({
        "kind": "workspace-action", "action": action,
        "slot": slots[0] if slot else f"{scope} workspaces", "current": None,
        "total": len(slots), "message": "", "repos": [], "steps": [],
        "request": {"slots": slots, "payload": payload or {}},
    })
    if slot and action in {"free", "sync"}:
        with ALIGN_LOCK:
            pending = [ident for ident, other in ALIGN_JOBS.items()
                       if ident != job_id and other.get("kind") == "workspace-action"
                       and other.get("action") == action and other.get("slot") == slots[0]
                       and other.get("status") == "approval_required"]
        for ident in pending:
            message = (f"Confirmation used by {job_id}" if ident == approval_job
                       else f"Superseded by {job_id}")
            if ACTION_QUEUE is not None:
                ACTION_QUEUE.settle(ident, message)
            else:
                with ALIGN_LOCK:
                    if ALIGN_JOBS[ident].get("status") == "approval_required":
                        ALIGN_JOBS[ident].update(status="done", message=message,
                                                 approval=None, current=None)
    return job_id


def run_workspace_job(job: dict, store_lock=None) -> None:
    slots = job["request"]["slots"]
    payload = job["request"]["payload"]
    action = job["action"]
    with ALIGN_LOCK:
        WORKSPACE_BUSY.update(slots)

    def step(event: dict, current: str) -> None:
        with ALIGN_LOCK:
            record_job_step(job, event, prefix=current if len(slots) > 1 else "")

    @wm.workspace_serialized
    def operate(current):
        def progress(event: dict) -> None:
            step(event, current)
        if action == "occupy":
            wm.require_clean(current)
            with (store_lock or _threading.Lock()):
                ok, msg = apply_workspace_edit(current, payload or {}, validated=True)
            if not ok:
                raise ValueError(msg)
            align = alignment_target_for_ws_payload(current, payload or {})
            if align:
                alignment = dict(job, task_id=align[0], slot=current, repos=[])
                run_workspace_alignment(alignment, store_lock)
                if alignment["status"] == "error":
                    raise ValueError(alignment["message"])
            return wm.workspace_request(current, progress=progress)
        option = {"free": "portal_free", "sync": "portal_sync"}.get(action)
        options = ({option: {"approved": payload.get("approved")}} if option else {})
        reading = wm.workspace_request(
            current, "status" if action == "refresh" else action, progress=progress,
            **options)
        if reading.get("approval_required"):
            return reading
        if reading.get("stashes"):
            with ALIGN_LOCK:
                job["stashes"] = reading["stashes"]
        if reading.get("warnings"):
            with ALIGN_LOCK:
                job["warnings"] = reading["warnings"]
        if action == "free":
            progress({"step": "release", "label": "Releasing workspace holder"})
            with (store_lock or _threading.Lock()):
                ok, msg = apply_workspace_edit(current, {"action": "free"})
            if not ok:
                raise ValueError(msg)
        return reading

    def worker():
        errors = []
        approval = None
        try:
            for current in slots:
                try:
                    with ALIGN_LOCK:
                        job["current"] = wm.record(current)["label"]
                    reading = operate(current)
                    approval = reading.get("approval_required")
                    if approval:
                        row = {"name": wm.record(current)["label"], "role": "workspace",
                               "action": "approval-required",
                               "detail": "Confirmation required; no checkout changes made"}
                    else:
                        detail = ("missing" if reading.get("exists") is False else
                                  "clean" if reading.get("clean") else "checked; not at clean baseline")
                        row = {"name": wm.record(current)["label"], "role": "workspace",
                               "action": "checked-out", "detail": detail}
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as e:
                    errors.append(str(e))
                    wm.log_action(current, action, "error", str(e))
                    with ALIGN_LOCK:
                        finish_job_steps(job, "error")
                    row = {"name": current, "role": "workspace",
                           "action": "error", "detail": str(e)}
                with ALIGN_LOCK:
                    job["repos"].append(row)
            with ALIGN_LOCK:
                finish_job_steps(job, "done")
                if approval and not errors:
                    job.update(status="approval_required", approval=approval, current=None,
                               finished=wm.stamp(),
                               message=("Confirmation required; Sync was blocked by workspace issues"
                                        if action == "sync" else
                                        "Confirmation required; the workspace has not been returned or freed"))
                else:
                    job.update(status="error" if errors else "done", current=None,
                               finished=wm.stamp(), message="; ".join(errors) or f"{action} complete")
        finally:
            with ALIGN_LOCK:
                WORKSPACE_BUSY.difference_update(slots)

    worker()


JOB_STEP_ERRORS_MAX = 100


def record_job_step(job: dict, event: dict, prefix: str = "") -> None:
    """Fold a runtime progress event into the job's step timeline (caller holds ALIGN_LOCK)."""
    if event.get("warning"):
        warnings = job.setdefault("warnings", [])
        if event["warning"] not in warnings:
            warnings.append(event["warning"])
    key = f"{prefix}:{event.get('step')}" if prefix else str(event.get("step") or "")
    label = str(event.get("label") or event.get("step") or "working")
    if prefix:
        label = f"{prefix} · {label}"
    steps = job.setdefault("steps", [])
    if not steps or steps[-1]["key"] != key:
        if steps and steps[-1]["state"] == "running":
            steps[-1]["state"] = "done"
        steps.append({"key": key, "label": label, "state": "running",
                      "started": wm.stamp(), "errors": []})
    cur = steps[-1]
    for field in ("done", "total", "current", "detail"):
        if event.get(field) is not None:
            cur[field] = event[field]
    if event.get("error") and len(cur["errors"]) < JOB_STEP_ERRORS_MAX:
        cur["errors"].append(f"{event.get('current') or '?'}: {event['error']}")
    job["current"] = label


def finish_job_steps(job: dict, state: str) -> None:
    """Close the running step as done/error (caller holds ALIGN_LOCK)."""
    steps = job.get("steps") or []
    if steps and steps[-1]["state"] == "running":
        steps[-1]["state"] = state
        steps[-1]["finished"] = wm.stamp()


def alignment_target_for_ws_payload(slot: str, payload: dict) -> tuple[str, str] | None:
    """Return (task_id, slot_root) if a workspace occupy assigns a code-review task."""
    if payload.get("action", "occupy") != "occupy":
        return None
    if payload.get("type", "task") != "task":
        return None
    tid = (payload.get("task_id") or "").strip()
    if not tid:
        return None
    t = tm.find_task(tm.load(), tid)
    if not t or t.get("category") != "code-review":
        return None
    root = wm.normalize_slot(slot)
    return (t["id"], root) if root else None


def alignment_target_for_task_payload(ident: str, payload: dict) -> tuple[str, str] | None:
    """Return (task_id, slot_root) if a task edit assigns a code-review task to a slot."""
    ws = (payload.get("workspace") or "").strip()
    if not ws:
        return None
    t = tm.find_task(tm.load(), ident)
    if not t or t.get("category") != "code-review":
        return None
    root = wm.normalize_slot(ws)
    return (t["id"], root) if root else None


def _server_running() -> int | None:
    """Return the PID of a running server (per PID file), or None."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def cmd_serve(args: argparse.Namespace) -> int:
    global ACTION_QUEUE
    import signal
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if args.status:
        pid = _server_running()
        print(f"running (pid {pid})" if pid else "not running")
        return 0

    if args.stop:
        pid = _server_running()
        if not pid:
            print("[tm] no server running")
            return 0
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        print(f"[tm] stopped server (pid {pid})")
        return 0

    pid = _server_running()
    if pid:
        print(f"[tm] server already running (pid {pid}) on http://127.0.0.1:{args.port}")
        return 0

    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if (self.path.startswith(("/api/team", "/api/vm-sat"))
                    or self.path in ("/", "/index.html")):
                self.send_header("Cache-Control", "no-store")
            if self.path.startswith("/review-drafts/"):
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; style-src 'unsafe-inline'; "
                                 "frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _stream_event(self, name, cursor, body):
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(
                f"id: {cursor}\nevent: {name}\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        def _stream_bus_event(self, instance, event):
            body = dict(event)
            if event["kind"] == "ai":
                body["ai"] = ai_usage_payload()
            self._stream_event("message", f"{instance}:{event['seq']}", body)

        def _stream_reset(self):
            cursor = EVENTS.cursor()
            self._stream_event(
                "reset", f"{cursor['instance']}:{cursor['seq']}",
                {"live": live_payload(), "ai": ai_usage_payload()})
            return cursor

        def _events_stream(self):
            self.protocol_version = "HTTP/1.1"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            cfg = portal_settings.load()["live_updates"]
            self.connection.settimeout(cfg["keepalive_s"] + 25)

            last_id = self.headers.get("Last-Event-ID", "")
            initial_connection = not last_id
            if initial_connection:
                last_id = parse_qs(urlparse(self.path).query).get("since", [""])[0]
            last_state = time.monotonic()
            if not last_id:
                cursor = self._stream_reset()
            else:
                instance, separator, raw_seq = last_id.rpartition(":")
                try:
                    seq = int(raw_seq) if separator else -1
                except ValueError:
                    seq = -1
                feed = EVENTS.wait_after(instance, seq, 0)
                if feed["reset"]:
                    cursor = self._stream_reset()
                else:
                    cursor = {"instance": feed["instance"], "seq": seq}
                    if initial_connection:
                        self._stream_event(
                            "hello", f"{feed['instance']}:{seq}",
                            {"live": live_payload(), "ai": ai_usage_payload()})
                    for event in feed["events"]:
                        self._stream_bus_event(feed["instance"], event)
                        cursor["seq"] = event["seq"]

            while True:
                feed = EVENTS.wait_after(cursor["instance"], cursor["seq"], cfg["keepalive_s"])
                if feed["reset"]:
                    cursor = self._stream_reset()
                    continue
                if feed["events"]:
                    for event in feed["events"]:
                        self._stream_bus_event(feed["instance"], event)
                        cursor["seq"] = event["seq"]
                else:
                    cursor["seq"] = feed["seq"]
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    cfg = portal_settings.load()["live_updates"]
                    self.connection.settimeout(cfg["keepalive_s"] + 25)
                    if time.monotonic() - last_state >= cfg["state_interval_s"]:
                        self._stream_event(
                            "state", f"{feed['instance']}:{cursor['seq']}",
                            {"live": live_payload(), "ai": ai_usage_payload()})
                        last_state = time.monotonic()

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, "ok", "text/plain")
            if self.path.startswith("/review-drafts/"):
                match = re.fullmatch(r"/review-drafts/(T-[0-9]+)/([0-9a-f]{32})",
                                     urlparse(self.path).path)
                if not match:
                    return self._send(404, json.dumps({"error": "unknown review draft"}))
                try:
                    with lock:
                        draft = tm.read_review_draft(*match.groups())
                    return self._send(200, render_review_draft(draft), "text/html; charset=utf-8")
                except FileNotFoundError:
                    return self._send(404, json.dumps({"error": "Saved draft file is missing"}))
                except ValueError as exc:
                    return self._send(400, json.dumps({"error": str(exc)}))
                except OSError as exc:
                    print(f"[portal/review-draft] read failed: {exc}", file=sys.stderr)
                    return self._send(500, json.dumps({"error": "Draft could not be read; check service logs"}))
            route = urlparse(self.path).path
            if route == "/term":
                return self._send(200, render_term_page(), "text/html; charset=utf-8")
            if route == "/console":
                return self._send(200, render_console_page(), "text/html; charset=utf-8")
            if route == "/api/term/uart":
                q = parse_qs(urlparse(self.path).query)
                try:
                    body = uart_consoles(q.get("target", [""])[0], q.get("fresh", [""])[0] == "1")
                except terminal.TermError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(body, ensure_ascii=False))
            if route == "/api/vm-sat":
                target = parse_qs(urlparse(self.path).query).get("target", [""])[0]
                try:
                    body = sat_machine_payload(target)
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                except RuntimeError as e:
                    return self._send(503, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(body, ensure_ascii=False))
            if route == "/flash":
                return self._send(200, render_flash_page(), "text/html; charset=utf-8")
            if route in ("/api/flash/images", "/api/flash/status"):
                try:
                    if route == "/api/flash/images":
                        body = flash_images()
                    else:
                        body = flash_status(parse_qs(urlparse(self.path).query).get("id", [""])[0])
                except LookupError as e:
                    return self._send(404, json.dumps({"error": str(e)}))
                except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(body, ensure_ascii=False))
            if route == "/ws/term":
                token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
                spec = terminal.take_token(token)
                if not spec:
                    return self._send(403, json.dumps({"error": "terminal token expired or used"}))
                if not terminal.handshake(self, f"http://{self.headers.get('Host')}"):
                    return None
                self.close_connection = True
                env = vd.agent_env()[0] if vd is not None else None
                terminal.serve(self.connection, spec, env)
                return None
            if route in {"/api/grafana-health", "/api/pr-stage", "/api/sherlock"}:
                query = parse_qs(urlparse(self.path).query)
                try:
                    if route == "/api/grafana-health":
                        body = {**grafana_health(), "url": GRAFANA_URL}
                    elif route == "/api/pr-stage":
                        body = pr_stage(query.get("url", [""])[0])
                    else:
                        body = sherlock(query.get("job", [""])[0])
                except ValueError as error:
                    return self._send(400, json.dumps({"error": str(error)}))
                return self._send(200, json.dumps(body))
            if route == "/api/events":
                try:
                    seq = int(parse_qs(urlparse(self.path).query).get("since", ["0"])[0])
                except ValueError:
                    seq = 0
                body = EVENTS.since(seq)
                body["live"] = live_payload()
                body["ai"] = ai_usage_payload()
                return self._send(200, json.dumps(body))
            if route == "/api/stream":
                try:
                    return self._events_stream()
                except OSError:
                    self.close_connection = True
                    return None
            if route == "/api/ai/runs":
                query = parse_qs(urlparse(self.path).query)
                section = query.get("section", [None])[0]
                runs = AI_STORE.recent(30, section)
                if self.headers.get("X-Team-Private") != "1":
                    runs = [r for r in runs if r.get("section") != "team"]
                return self._send(200, json.dumps({"runs": runs, "usage": ai_usage_payload()},
                                                  ensure_ascii=False))
            if route == "/api/ai/ci-triages":
                return self._send(200, json.dumps({"triages": ci_triage_index()},
                                                  ensure_ascii=False))
            if route.startswith("/api/ai/run/"):
                run = AI_STORE.get(route.rsplit("/", 1)[-1])
                if run is None:
                    return self._send(404, json.dumps({"error": "unknown AI run"}))
                if run.get("private") and self.headers.get("X-Team-Private") != "1":
                    return self._send(403, json.dumps({"error": "enable private mode to read this answer"}))
                return self._send(200, json.dumps(ai_public(run), ensure_ascii=False))
            if route == "/ai-chat":
                return self._send(200, render_aichat_page(), "text/html; charset=utf-8")
            if route == "/api/ai/review-chats":
                query = parse_qs(urlparse(self.path).query)
                chats = AI_STORE.chats(query.get("task", [None])[0],
                                       open_only=query.get("open", ["0"])[0] == "1")
                return self._send(200, json.dumps({"chats": [review_chat_summary(c)
                                                             for c in chats[:50]]}))
            if route.startswith("/api/ai/review-chat/"):
                chat = AI_STORE.get_chat(route.rsplit("/", 1)[-1])
                if chat is None:
                    return self._send(404, json.dumps({"error": "unknown review chat"}))
                return self._send(200, json.dumps(review_chat_public(chat), ensure_ascii=False))
            if route == "/api/term/active":
                return self._send(200, json.dumps({"sessions": terminal.active()}))
            if route == "/api/sso-login":
                return self._send(200, json.dumps({"logins": SSO_LOGINS.status()}))
            if self.path.startswith("/assets/"):
                name = self.path[len("/assets/"):].split("?")[0]
                ctype = ASSET_TYPES.get(Path(name).suffix)
                if name not in ASSET_NAMES or not ctype:
                    return self._send(404, json.dumps({"error": "not found"}))
                return self._send(200, asset_text(name), ctype)
            if self.path in ("/", "/index.html"):
                with lock:
                    cursor = EVENTS.cursor()
                    html = render_dashboard_html(tm.load()["tasks"], event_cursor=cursor)
                return self._send(200, html, "text/html; charset=utf-8")
            if self.path == "/api/tasks":
                with lock:
                    wd = wm.load()
                    return self._send(200, json.dumps(
                        {"tasks": _annotate_tasks(tm.load()["tasks"], wd),
                         "workspaces": ws_ui_payload(wd, cached_only=True)},
                        ensure_ascii=False))
            if urlparse(self.path).path == "/api/team":
                query = parse_qs(urlparse(self.path).query)
                audience = query.get("audience", ["shared"])[0]
                if audience == "private" and self.headers.get("X-Team-Private") != "1":
                    return self._send(403, json.dumps({"error": "Explicit private-mode request required"}))
                try:
                    with lock:
                        data = team_api.payload(audience, query.get("as_of", [None])[0])
                    return self._send(200, json.dumps(data, ensure_ascii=False))
                except (ValueError, KeyError) as exc:
                    return self._send(400, json.dumps({"error": str(exc)}))
                except Exception as exc:
                    print(f"[portal/team] read failed: {exc}", file=sys.stderr)
                    return self._send(500, json.dumps({"error": "Team data could not be loaded; check service logs"}))
            if self.path == "/api/workspaces":
                return self._send(200, json.dumps(
                    ws_ui_payload(wm.load(), cached_only=True), ensure_ascii=False))
            if self.path.startswith("/api/workspace-log/"):
                slot = unquote(self.path[len("/api/workspace-log/"):])
                try:
                    rec = wm.record(slot)
                    local = [dict(e, origin="controller") for e in wm.action_log(slot)]
                    remote = [dict(e, origin="machine") for e in wm.status_read(rec["id"]).get("log", [])]
                    return self._send(200, json.dumps(
                        {"events": sorted(local + remote, key=lambda r: r["at"], reverse=True)},
                        ensure_ascii=False))
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
            if self.path == "/api/bugs":
                with lock:
                    return self._send(200, json.dumps(bug_payload(), ensure_ascii=False))
            if self.path == "/api/triage":
                with lock:
                    return self._send(200, json.dumps(triage_payload(), ensure_ascii=False))
            if self.path.startswith("/api/investigation?"):
                if bugdb is None:
                    return self._send(404, json.dumps({"error": "bug-triage not installed"}))
                name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
                text = bugdb.read_investigation(name)
                if text is None:
                    return self._send(404, json.dumps({"error": f"no such note: {name}"}))
                return self._send(200, json.dumps({"name": name, "text": text},
                                                  ensure_ascii=False))
            if self.path == "/api/settings":
                return self._send(200, json.dumps(portal_settings.payload(), ensure_ascii=False))
            if self.path == "/api/overview":
                with lock:
                    return self._send(200, json.dumps(overview_payload(), ensure_ascii=False))
            if self.path == "/api/prs":
                with lock:
                    return self._send(200, json.dumps(pr_payload(), ensure_ascii=False))
            if self.path == "/api/raid":
                with RAID_LOCK:
                    return self._send(200, json.dumps(raid_payload(), ensure_ascii=False))
            if self.path == "/api/vm-jobs":
                return self._send(200, json.dumps(vmlog_payload(), ensure_ascii=False))
            if self.path == "/api/actions":
                if ACTION_QUEUE is None:
                    return self._send(503, json.dumps({"error": "Action queue unavailable"}))
                try:
                    snapshot = ACTION_QUEUE.snapshot()
                except (sqlite3.Error, OSError) as e:
                    return self._send(503, json.dumps({"error": f"Action queue storage unavailable: {e}"}))
                return self._send(503 if snapshot.get("error") else 200,
                                  json.dumps(snapshot, ensure_ascii=False))
            if self.path.startswith("/api/align/"):
                jid = self.path[len("/api/align/"):]
                with ALIGN_LOCK:
                    job = ALIGN_JOBS.get(jid)
                    body = json.dumps({k: v for k, v in job.items() if k != "request"},
                                      ensure_ascii=False) if job else None
                if job and ACTION_QUEUE is not None:
                    try:
                        snapshot = ACTION_QUEUE.snapshot()
                    except (sqlite3.Error, OSError) as e:
                        return self._send(503, json.dumps({"error": f"Action queue storage unavailable: {e}"}))
                    reading = next((j for j in snapshot["actions"] if j["id"] == jid), None)
                    if reading:
                        body = json.dumps(reading, ensure_ascii=False)
                if body is None:
                    return self._send(404, json.dumps({"error": "unknown job"}))
                return self._send(200, body)
            return self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            from urllib.parse import unquote

            if self.path == "/api/team":
                origin = self.headers.get("Origin")
                expected = f"http://{self.headers.get('Host')}"
                if origin and origin != expected:
                    return self._send(403, json.dumps({"error": "Enable private mode to edit team records"}))
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    if not 0 < size <= 2_000_000:
                        raise ValueError("Request must be between 1 byte and 2 MB")
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict) or not isinstance(data.get("action"), str):
                        raise ValueError("action and data are required")
                    if (self.headers.get("X-Team-Private") != "1"
                            and data["action"] != "meeting-shared-save"):
                        return self._send(403, json.dumps({
                            "error": "Enable private mode to edit team records"
                        }))
                    if data["action"] == "activity-scan":
                        result = team_api.mutate(data["action"], data.get("data"))
                    else:
                        with lock:
                            result = team_api.mutate(data["action"], data.get("data"))
                    return self._send(200, json.dumps({"result": result}, ensure_ascii=False))
                except (ValueError, KeyError, TypeError) as exc:
                    status = 409 if "revision" in str(exc).lower() else 400
                    return self._send(status, json.dumps({"error": str(exc)}))
                except Exception as exc:
                    print(f"[portal/team] save failed: {exc}", file=sys.stderr)
                    return self._send(500, json.dumps({"error": "Team record was not saved; check service logs"}))

            # The PR scan shells out to `gh` and can run long. An optional
            # {"feature_ids": [..]} body limits it to the selected features.
            if self.path == "/api/pr-scan":
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                only = None
                jira = True
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}")
                        ids = body.get("feature_ids")
                        if ids is not None:
                            only = [int(i) for i in ids]
                    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                        return self._send(400, json.dumps({"error": "feature_ids must be a list of ids"}))
                    if only is not None and not only:
                        return self._send(400, json.dumps({"error": "no features selected"}))
                    jira = body.get("jira_sync", True)
                    if not isinstance(jira, bool):
                        return self._send(400, json.dumps({"error": "jira_sync must be a boolean"}))
                try:
                    jid = start_pr_scan_job(only) if jira else start_pr_scan_job(only, jira=False)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "job": jid}))

            # Re-read just the EC2 power state (`octl vm list`), no ssh probe.
            if self.path == "/api/vm-machines":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_machines()
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                # A hard octl failure returns a bare {"error": ...}; a full
                # payload carries "machines" and may still hold a soft, purely
                # informational per-target probe error from the cached state.
                if "machines" not in result:
                    return self._send(502, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # The bug scan hits the Jira REST API; also bodyless and slow.
            if self.path == "/api/bug-scan":
                if bm is None:
                    return self._send(503, json.dumps({"error": "bug-monitor not installed"}))
                with BUG_SCAN_LOCK:
                    try:
                        result = bm.portal_scan()
                    except Exception as e:
                        return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": "invalid JSON"}))
            if not isinstance(payload, dict):
                return self._send(400, json.dumps({"error": "request body must be an object"}))

            # Only selected task IDs may enter a review scan. The task-manager
            # owner applies its own eligibility and unresolved-PR rules.
            # {"scope": "all"} (daily maintenance) re-probes every open review PR.
            if self.path == "/api/review-scan":
                if "scope" in payload and payload != {"scope": "all"}:
                    return self._send(400, json.dumps({"error": 'scope must be exactly {"scope": "all"}'}))
                if payload == {"scope": "all"}:
                    try:
                        jid = start_review_scan_job(None)
                    except Exception as e:
                        return self._send(500, json.dumps({"error": str(e)}))
                    return self._send(200, json.dumps({"ok": True, "align_job": jid}))
                task_ids = payload.get("task_ids")
                if (not isinstance(task_ids, list) or not task_ids
                        or any(not isinstance(ident, str) or not ident for ident in task_ids)
                        or len(set(task_ids)) != len(task_ids)):
                    return self._send(400, json.dumps(
                        {"error": "select one or more unique task IDs to scan"}))
                try:
                    jid = start_review_scan_job(task_ids)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            # Cancel one queued action, pending approval or interruptible
            # (pure-AI) running action. Same-origin only.
            cancel_route = re.fullmatch(r"/api/actions/(action-[0-9a-f]{32})/cancel", self.path)
            if cancel_route:
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                try:
                    result = cancel_action(cancel_route.group(1))
                except LookupError:
                    return self._send(404, json.dumps({"error": "unknown action"}))
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                except RuntimeError as e:
                    return self._send(503, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, **result}, ensure_ascii=False))

            # Daily maintenance: drop finished ("done"/"cancelled") queue history
            # older than the retention window. Pending, running, failed and
            # approval results are kept. Same-origin only; no browser control calls it.
            if self.path == "/api/maintenance/queue-retention":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                days = payload.get("days")
                if set(payload) != {"days"} or not isinstance(days, int) or isinstance(days, bool) \
                        or days < 7:
                    return self._send(400, json.dumps({"error": "days must be an integer >= 7"}))
                if ACTION_QUEUE is None:
                    return self._send(503, json.dumps({"error": "Action queue unavailable"}))
                try:
                    result = ACTION_QUEUE.prune(days)
                except (sqlite3.Error, OSError) as e:
                    return self._send(503, json.dumps({"error": f"Action queue storage unavailable: {e}"}))
                return self._send(200, json.dumps({"ok": True, **result}))

            # Portal-owned settings. Same-origin only; revision-checked; each
            # change is journaled. Schedule edits rewrite the timer drop-in.
            chat_route = re.fullmatch(r"/api/ai/review-chat(?:/(rc-[0-9a-f]{32})/(turn|preview|submit|end|retry))?",
                                      self.path)
            if self.path in ("/api/ai/run", "/api/ai/auto-review", "/api/ai/ci-triage") or chat_route:
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                try:
                    if self.path == "/api/ai/run":
                        result = start_ai_run(payload, self.headers.get("X-Team-Private") == "1")
                    elif self.path == "/api/ai/ci-triage":
                        result = start_ci_triage(payload.get("feature_id"),
                                                 payload.get("force") is True)
                    elif chat_route and chat_route.group(1):
                        result = review_chat_action(chat_route.group(1), chat_route.group(2),
                                                    payload)
                    else:
                        tid = payload.get("task_id")
                        if not isinstance(tid, str) or not re.fullmatch(r"T-\d+", tid):
                            raise ValueError("task_id must be a task id")
                        if chat_route:
                            result = start_review_chat(tid)
                        elif payload.get("online") is True:
                            result = start_online_review(tid, payload.get("mode") or "create")
                        else:
                            result = start_auto_review(tid, payload.get("mode") or "create")
                except LookupError as e:
                    return self._send(404, json.dumps({"error": str(e)}))
                except ai.CapReached as e:
                    return self._send(429, json.dumps({"error": str(e)}))
                except PermissionError as e:
                    return self._send(403, json.dumps({"error": str(e)}))
                except (ValueError, RuntimeError) as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, **result}))

            if self.path == "/api/term/open":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                try:
                    spec = term_spec(payload)
                    token = terminal.issue_token(
                        spec, limit=portal_settings.load()["terminals"]["max_sessions"])
                except terminal.TermError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"token": token, "title": spec["title"],
                                                   "kind": spec["kind"]}, ensure_ascii=False))

            if self.path == "/api/term/launch":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                try:
                    result = term_launch(payload, self.client_address[0])
                except PermissionError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                except terminal.TermError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, **result}))

            if self.path == "/api/settings":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                if set(payload) != {"revision", "settings"}:
                    return self._send(400, json.dumps({"error": "send exactly {revision, settings}"}))
                try:
                    result = portal_settings.save(payload["settings"], payload["revision"])
                except portal_settings.SettingsError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                except portal_settings.RevisionConflict as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                except portal_settings.ApplyError as e:
                    _journal_settings("failed", str(e), "")
                    return self._send(500, json.dumps({"error": str(e)}))
                if result["changed"]:
                    _journal_settings("completed", "; ".join(result["changed"]), result["applied"])
                    if LIVE is not None:
                        threading.Thread(target=LIVE.sync, daemon=True).start()
                    if any(c.startswith("live_updates.") for c in result["changed"]):
                        apply_live_updates()
                    if POWER_SCHEDULE is not None:
                        POWER_SCHEDULE.wake.set()
                    if any(c.startswith(("live_updates.", "machines.")) for c in result["changed"]):
                        EVENTS.publish_settings()
                return self._send(200, json.dumps({"ok": True, **result,
                                                   **portal_settings.payload()}, ensure_ascii=False))

            if self.path == "/api/tasks/bulk-edit":
                edits = payload.get("edits")
                with lock:
                    try:
                        ok, msg = apply_task_edits(edits)
                    except ValueError as e:
                        return self._send(400, json.dumps({"error": str(e)}))
                    if not ok:
                        status = 409 if "revision conflict" in msg.lower() else 400
                        return self._send(status, json.dumps({"error": msg}))
                    wd = wm.load()
                    tasks = _annotate_tasks(tm.load()["tasks"], wd)
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "tasks": tasks,
                     "workspaces": ws_ui_payload(wd)}, ensure_ascii=False))

            if self.path == "/api/ws-check-vm":
                try:
                    jid = start_workspace_job("refresh", store_lock=lock, scope="remote")
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            if self.path == "/api/ws-refresh" or self.path.startswith("/api/ws-refresh/"):
                slot = (unquote(self.path[len("/api/ws-refresh/"):])
                        if self.path.startswith("/api/ws-refresh/") else None)
                try:
                    jid = start_workspace_job("refresh", slot, store_lock=lock)
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            # Open one stored job log, or poll a live one. Deliberately does
            # NOT take VM_REFRESH_LOCK: reading a local file must never be
            # blocked by an in-flight probe, and the tail is polled on a timer.
            if self.path in ("/api/vm-log", "/api/vm-tail"):
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                target = str(payload.get("target") or "")
                tag = str(payload.get("tag") or "")
                kind = str(payload.get("kind") or "build")
                # The job id is what binds a log to the run that produced it.
                # Without it the slot's single log file is served to whichever
                # job asks, so a re-run of the same workspace shows the previous
                # run's output.
                job_id = str(payload.get("id") or "")
                try:
                    if self.path == "/api/vm-tail":
                        result = vd.portal_tail(target, tag, kind,
                                                payload.get("lines") or 200, job_id)
                    else:
                        result = vd.portal_log(target, tag, kind, job_id)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # Sync uses the owner's occupancy and mirror guards.
            if self.path.startswith("/api/ws-sync/"):
                slot = unquote(self.path[len("/api/ws-sync/"):])
                root = wm.normalize_slot(slot)
                if not root:
                    return self._send(400, json.dumps({"error": f"invalid slot: {slot!r}"}))
                try:
                    jid = start_workspace_job("sync", root, payload, store_lock=lock)
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            # The VM delegation probe is one ssh round trip per target plus an
            # `octl vm list`, so it is dispatched as a background job and polled
            # through /api/align/<id> like the PR scan. Running it inline made
            # the browser give up on a slow or unreachable target.
            #
            # An optional {"target": "<name>"} scopes it to one machine. That is
            # not a nicety: an unreachable lab desktop costs a 90 s ssh timeout,
            # so a fleet refresh is minutes while "just the VM" is seconds.
            if self.path == "/api/vm-refresh":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                only = str(payload.get("target") or "") or None
                if only and not vd.is_target(only):
                    return self._send(400, json.dumps({"error": f"unknown target: {only}"}))
                try:
                    jid = start_vm_refresh_job(only)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "job": jid}))

            # Stop one running delegated machine job. Queued so it serialises with
            # probes of that target; vm-delegate's mctl cancel re-checks the pid
            # on the machine, so a job that ended meanwhile is never hit.
            if self.path == "/api/vm-cancel":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                ident = payload.get("id")
                if set(payload) != {"id"} or not isinstance(ident, str) or not ident:
                    return self._send(400, json.dumps({"error": "id of a machine job is required"}))
                if vd is None and not jobs_view.is_pilot_id(ident):
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                try:
                    jid = start_vm_cancel_job(ident)
                except LookupError as e:
                    return self._send(404, json.dumps({"error": str(e)}))
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                except Exception as e:                       # noqa: BLE001
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "job": jid}))

            # Mirror job logs locally without probing or asking octl anything.
            # The remote keeps one log per slot and overwrites it on the next
            # run, so this is what makes an old log openable at all.
            if self.path == "/api/vm-sync":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                only = str(payload.get("target") or "") or None
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_sync(only)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                if "jobs" not in result:
                    return self._send(400, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # EC2 power control for a delegation target (`octl vm start|stop`).
            if self.path == "/api/vm-power":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_power(payload.get("action", ""), payload.get("id", ""))
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                # Same shape rule as /api/vm-machines: no "machines" key means
                # octl itself refused, anything else is a full payload.
                if "machines" not in result:
                    return self._send(400, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # Main VM: which instance the `vm` target (neo-ask, default
            # delegation) points at. Registry-only via `mctl vm main`; the old
            # Main stays registered as vm-<id6>.
            if self.path == "/api/vm-main":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                ident = payload.get("id")
                if set(payload) != {"id"} or not isinstance(ident, str) \
                        or not re.fullmatch(r"i-[0-9a-f]{8,17}", ident):
                    return self._send(400, json.dumps({"error": "an EC2 instance id is required"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_set_main(ident)
                except Exception as e:                       # noqa: BLE001
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                if "machines" not in result:
                    return self._send(400, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # Remote `octl login`: the device link is opened by the browser.
            if self.path == "/api/sso-login":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                target, action = payload.get("target"), payload.get("action")
                if set(payload) != {"target", "action"} or not isinstance(target, str) \
                        or action not in ("start", "cancel"):
                    return self._send(400, json.dumps({"error": "target and action start|cancel are required"}))
                try:
                    if action == "start":
                        result = SSO_LOGINS.start(target, vd.targets())
                    else:
                        result = SSO_LOGINS.cancel(target)
                except sso_login.SsoError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"login": result}))

            # Flash FW: read-only discovery, then a validated local-runner job.
            if self.path in ("/api/flash/discover", "/api/flash/start"):
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                try:
                    if self.path == "/api/flash/discover":
                        if set(payload) != {"target"}:
                            raise ValueError("send {target}")
                        body = flash_discover(payload["target"])
                    else:
                        body = flash_start(payload)
                except FileExistsError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                except (RuntimeError, json.JSONDecodeError) as e:
                    return self._send(502, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(body, ensure_ascii=False))

            if self.path == "/api/vm-target":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    return self._send(403, json.dumps({"error": "cross-origin request rejected"}))
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_target(payload)
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                except Exception as e:                       # noqa: BLE001
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                if "jobs" not in result:
                    return self._send(400, json.dumps(result, ensure_ascii=False))
                if LIVE is not None and payload.get("action") == "remove":
                    LIVE.sync()
                return self._send(200, json.dumps(vmlog_payload() | {
                    "message": result["message"]}, ensure_ascii=False))

            # --- tracked roster + groups -------------------------------------
            # Which workspaces the portal shows is user-editable; every mutation
            # answers with the whole refreshed payload so the view re-renders.
            def _roster_reply(msg, **extra):
                with lock:
                    wd = wm.load()
                    body = {"ok": True, "message": msg,
                            "tasks": _annotate_tasks(tm.load()["tasks"], wd),
                            "workspaces": ws_ui_payload(wd)}
                body.update(extra)
                return self._send(200, json.dumps(body, ensure_ascii=False))

            if self.path == "/api/ws-track":
                ok, msg, _slot = wm.track(
                    payload.get("path", ""), label=payload.get("label"),
                    rev=payload.get("rev"), group=payload.get("group"),
                    badge=payload.get("badge"), target=payload.get("target"),
                    purpose=payload.get("purpose", "general"))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/ws-untrack/"):
                slot = unquote(self.path[len("/api/ws-untrack/"):])
                force = payload.get("force", False)
                if not isinstance(force, bool):
                    return self._send(400, json.dumps({"error": "force must be a boolean"}))
                try:
                    ok, msg = wm.untrack(slot, force=force)
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/ws-meta/"):
                slot = unquote(self.path[len("/api/ws-meta/"):])
                try:
                    if ("purpose" in payload and ACTION_QUEUE is not None
                            and payload["purpose"] != wm.record(slot).get("purpose", "general")):
                        with ACTION_QUEUE.idle_resources({"workspace:" + wm.record(slot)["id"]}):
                            ok, msg = wm.set_slot_fields(slot, payload)
                    else:
                        ok, msg = wm.set_slot_fields(slot, payload)
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path == "/api/ws-groups":
                action = payload.get("action", "add")
                if action == "add":
                    ok, msg, gid = wm.add_group(payload.get("name", ""), payload.get("color"))
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    return _roster_reply(msg, group_id=gid)
                if action == "edit":
                    ok, msg = wm.edit_group(payload.get("id", ""), payload.get("name"),
                                            payload.get("color"))
                elif action == "delete":
                    ok, msg = wm.delete_group(payload.get("id", ""))
                else:
                    return self._send(400, json.dumps({"error": f"unknown action: {action}"}))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/workspace/"):
                slot = unquote(self.path[len("/api/workspace/"):])
                try:
                    jid = start_workspace_job(payload.get("action", "occupy"),
                                              slot, payload, lock)
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            if self.path.startswith("/api/bug/"):
                if bm is None:
                    return self._send(400, json.dumps({"error": "bug-monitor not installed"}))
                key = unquote(self.path[len("/api/bug/"):])
                with lock:
                    ok, msg = bm.apply_bug_edit(key, payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    bugs = bug_payload()
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "bugs": bugs}, ensure_ascii=False))

            if self.path.startswith("/api/phenomenon/"):
                if ph is None:
                    return self._send(400, json.dumps({"error": "bug-triage not installed"}))
                pid = unquote(self.path[len("/api/phenomenon/"):])
                with lock:
                    ok, msg = ph.apply_ph_edit(pid, payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    out = {"ok": True, "message": msg, "triage": triage_payload(),
                           "bugs": bug_payload()}
                return self._send(200, json.dumps(out, ensure_ascii=False))

            if self.path == "/api/raid-new":
                if rd is None:
                    return self._send(400, json.dumps({"error": "team-planner not installed"}))
                with RAID_LOCK:
                    ok, msg = rd.new_row(payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    out = raid_payload()
                return self._send(200, json.dumps(
                    {"ok": True, "id": msg, "payload": out}, ensure_ascii=False))

            # Dispatches a real CI build onto shared emulation hardware, so it
            # is serialised and always reports which slot it took.
            if self.path == "/api/pldm-test":
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                with PLDM_LOCK:
                    feats = [f for f in pm.records()
                             if f["id"] == payload.get("feature_id")]
                    if not feats:
                        return self._send(404, json.dumps(
                            {"error": f"feature [{payload.get('feature_id')}] not found"}))
                    jid, msg = start_pldm_job(feats[0], payload)
                if not jid:
                    if payload.get("dry") and msg.startswith("dry run OK"):
                        return self._send(200, json.dumps(
                            {"ok": True, "dry": True, "message": msg}, ensure_ascii=False))
                    return self._send(400, json.dumps({"error": msg}))
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "align_job": jid}, ensure_ascii=False))

            # Posts a `/ci` comment on one, all, or only-failed PRs of a feature
            # to (re-)trigger CI. Serialised so a double-click can't double-fire.
            if self.path == "/api/pr-ci":
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                with PR_CI_LOCK:
                    res = pm.trigger_ci(payload.get("feature_id"),
                                        scope=payload.get("scope", "all"),
                                        number=payload.get("number"))
                if res.get("error"):
                    code = 404 if "not found" in res["error"] else 400
                    return self._send(code, json.dumps(res, ensure_ascii=False))
                return self._send(200, json.dumps({"ok": True, **res}, ensure_ascii=False))

            # Keep portal edits separate from an in-flight external scan.
            # Owner transactions also protect independent CLI writers.
            if self.path in ("/api/pr-track", "/api/pr-untrack",
                             "/api/feature-track", "/api/feature-untrack",
                             "/api/feature-edit", "/api/feature-jira-coverage"):
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                if not PR_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps(
                        {"error": "a PR scan is running \u2014 try again once it finishes"}))
                try:
                    ok, msg = pr_track_edit(self.path, payload)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    PR_SCAN_LOCK.release()
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                with lock:
                    return self._send(200, json.dumps(
                        {"ok": True, "message": msg, "prs": pr_payload()},
                        ensure_ascii=False))

            if self.path.startswith("/api/raid/"):
                if rd is None:
                    return self._send(400, json.dumps({"error": "team-planner not installed"}))
                rid = unquote(self.path[len("/api/raid/"):])
                with RAID_LOCK:
                    if payload.get("close"):
                        ok, msg = rd.apply_edit(rid, {
                            "status": f"**Closed {rd.TODAY}** — {payload['close']}",
                            "expected_revision": payload.get("expected_revision"),
                        })
                    else:
                        ok, msg = rd.apply_edit(rid, payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    out = raid_payload()
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "payload": out}, ensure_ascii=False))

            if not self.path.startswith("/api/task/"):
                return self._send(404, json.dumps({"error": "not found"}))
            ident = self.path[len("/api/task/"):]
            ident = unquote(ident)
            with lock:
                try:
                    ok, msg = apply_task_edit(ident, payload)
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                wd = wm.load()
                tasks = _annotate_tasks(tm.load()["tasks"], wd)
                align = alignment_target_for_task_payload(ident, payload)
            resp = {"ok": True, "message": msg, "tasks": tasks,
                    "workspaces": ws_ui_payload(wd)}
            if align:
                resp["align_job"] = start_alignment_job(*align, store_lock=lock)
            return self._send(200, json.dumps(resp, ensure_ascii=False))

    class Server(ThreadingHTTPServer):
        # Avoid "Address already in use" on quick restarts (TIME_WAIT sockets).
        allow_reuse_address = True
        daemon_threads = True

    PID_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    httpd = Server(("127.0.0.1", args.port), Handler)
    if vd is not None:
        observe_machine_jobs()
    ACTION_QUEUE = ActionQueue(
        STATE_DIR / "action-queue.db",
        lambda job: execute_and_notify(job, lock), ALIGN_JOBS, ALIGN_LOCK,
        resources=action_resources, interrupt=interrupt_action,
        on_transition=notify_action, max_workers=4)
    ACTION_QUEUE.start()
    PID_FILE.write_text(str(os.getpid()))

    # Run the request loop in a background thread so the main thread can call
    # httpd.shutdown() safely (shutdown() deadlocks if invoked from the same
    # thread as serve_forever()).
    stop = threading.Event()
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    global WATCHER
    WATCHER = domain_watch.Watcher(Path.home() / "tasks", publish_domain_changes)
    apply_live_updates()
    threading.Thread(target=WATCHER.run, args=(stop,), daemon=True,
                     name="portal-domain-watch").start()

    # Keeps delegated machine job statuses honest without a click.
    if vd is not None:
        threading.Thread(target=run_vm_watchdog, args=(stop,), daemon=True).start()
        start_power_schedule(stop)
        start_live_status()
    try:
        recover_review_chats()
    except Exception as error:                                   # noqa: BLE001
        print(f"[portal/review-chat] recovery failed: {error}", file=sys.stderr)
    threading.Thread(target=run_review_chat_sweeper, args=(stop,), daemon=True).start()
    if jobs_view.pilot_jobs is not None:
        threading.Thread(target=run_pilot_watch, args=(stop,), daemon=True,
                         name="portal-pilot-jobs").start()

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"[tm] serving editable dashboard at http://127.0.0.1:{args.port} (Ctrl-C to stop)")
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if POWER_SCHEDULE is not None:
            POWER_SCHEDULE.shutdown()
        if LIVE is not None:
            LIVE.stop()
        if WATCHER is not None:
            WATCHER.shutdown(stop)
        ACTION_QUEUE.stop()
        httpd.shutdown()
        httpd.server_close()
        PID_FILE.unlink(missing_ok=True)
    return 0


# ----------------------------------------------------------------------------
# Argparse wiring
# ----------------------------------------------------------------------------

def add_subcommands(sub) -> None:
    a = sub.add_parser("dashboard", help="generate a self-contained HTML dashboard of all tasks")
    a.add_argument("-o", "--output", help="output path (default ~/.copilot-private/team-portal/state/dashboard.html)")
    a.set_defaults(func=cmd_dashboard)

    a = sub.add_parser("serve", help="serve an editable dashboard on 127.0.0.1")
    a.add_argument("--port", type=int, default=8787)
    a.add_argument("--stop", action="store_true", help="stop a running server and exit")
    a.add_argument("--status", action="store_true", help="report whether a server is running and exit")
    a.set_defaults(func=cmd_serve)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="portal", description="Team Portal")
    add_subcommands(p.add_subparsers(dest="cmd", required=True))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
