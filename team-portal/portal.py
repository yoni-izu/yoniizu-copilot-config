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
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import urlparse, parse_qs

PORTAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL_ROOT.parent / "lib"))
from private_paths import load_private_json, private_root  # noqa: E402

STATE_DIR = private_root() / "team-portal" / "state"
JIRA_SITE = os.environ.get(
    "JIRA_SITE", load_private_json("config/jira.json").get("site", "")).rstrip("/")
JIRA_BASE = f"{JIRA_SITE}/browse/" if JIRA_SITE else ""
_SKILLS = str(PORTAL_ROOT.parent / "skills")
sys.path.insert(0, os.path.join(_SKILLS, "task-manager"))
sys.path.insert(0, os.path.join(_SKILLS, "workspace-manager"))
import tm  # noqa: E402

import wm  # noqa: E402
import team_api  # noqa: E402

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

# team-planner owns the RAID log (database canonical); the portal uses the rd.py owner API only.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/team-planner"))
try:
    import rd  # noqa: E402
except ImportError:
    rd = None

# pjq.py (pldm-job-queue skill) tracks PLDM hardware jobs; zero-token gh polling.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/pldm-job-queue"))
try:
    import pjq  # noqa: E402
except ImportError:
    pjq = None

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
<div id="align-banner">
  <div class="ab-head">
    <span id="align-spin" class="ab-spin"></span>
    <b id="align-title">Aligning workspace…</b>
    <button id="align-close" class="ab-close" aria-label="Dismiss">&times;</button>
  </div>
  <div id="align-body"></div>
</div>
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
    <div class="clock-chip"><span aria-hidden="true">◷</span><time id="local-clock"></time><small>LOCAL</small></div>
    <div class="refresh-wrap">
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
      <div class="slicer" id="ov-sprints"></div>
    </div>

    <div class="kpis" id="ov-kpis"></div>

    <div class="ov-canvas">
      <div class="tile t12 chg-tile" style="--tk:#9E8FE9;" data-overview-tile="changes">
        <h3>What changed since your last visit</h3>
        <p class="cap" id="ov-chg-cap"></p>
        <div id="ov-changes"></div>
      </div>
      <div class="tile t8" style="--tk:#118DFF;">
        <h3>Bug flow — open vs closed</h3>
        <p class="cap">Cumulative over 90 days, from each bug's created/resolved date in the local mirror.</p>
        <div id="ov-bugflow"></div>
      </div>
      <div class="tile t4" style="--tk:#1AAB40;">
        <h3>Bug backlog health</h3>
        <p class="cap">Share of open bugs that are triaged, and that carry a milestone and a sprint.</p>
        <div id="ov-gauges"></div>
      </div>

      <div class="tile t8" style="--tk:#E66C37;">
        <h3>Mean time to close — monthly</h3>
        <p class="cap" id="ov-ttc-cap">Days from created to resolved, bucketed by the month the bug closed.</p>
        <div id="ov-ttc"></div>
      </div>
      <div class="tile t4" style="--tk:#6B007B;">
        <h3>Open bugs by IP</h3>
        <p class="cap">Components mapped to an IP via the team-weekly scoreboard.</p>
        <div class="chart-row"><div id="ov-bugip"></div><div class="legend" id="ov-bugip-lg"></div></div>
      </div>

      <div class="tile t8" style="--tk:#12239E;">
        <h3>Per-IP scoreboard</h3>
        <p class="cap">Open bugs, attention load, review pressure and the 12-week load trend per IP.</p>
        <div id="ov-scoreboard"></div>
      </div>
      <div class="tile t4" style="--tk:#E044A7;">
        <h3>Open bugs by owner</h3>
        <p class="cap">Who is actually carrying the open bug load.</p>
        <div class="chart-row"><div id="ov-bugowner"></div><div class="legend" id="ov-bugowner-lg"></div></div>
      </div>

      <div class="tile t8" style="--tk:#D64550;">
        <h3>Needs you now</h3>
        <p class="cap">One ranked queue across bugs, RAID, reviews and blocked tasks — worst first.</p>
        <div id="ov-feed"></div>
      </div>
      <div class="tile t4" style="--tk:#D9B300;">
        <h3>Bug hygiene — missing planning fields</h3>
        <p class="cap">Open bugs carrying each flag. These are the gaps to close in Jira — each row opens that exact set as a Jira search.</p>
        <div id="ov-flags"></div>
      </div>

      <div class="tile t12" style="--tk:#197278;">
        <h3>Development hygiene</h3>
        <p class="cap">How cleanly work is flowing through the pipe — outgoing PRs, my review turnaround, and work-in-progress discipline.</p>
        <div class="hyg-wrap">
          <div class="hyg-score" id="ov-hyg-score"></div>
          <div class="hyg-rules" id="ov-hygiene"></div>
        </div>
      </div>

      <div class="tile t5" style="--tk:#744EC2;">
        <h3>RAID — open rows</h3>
        <p class="cap">From the RAID database, worst RAG then worst score.</p>
        <div id="ov-raid"></div>
      </div>
      <div class="tile t3" style="--tk:#197278;">
        <h3>Review queue</h3>
        <p class="cap">PRs in my incoming review queue, by consolidated state.</p>
        <div id="ov-reviews"></div>
      </div>
      <div class="tile t4" style="--tk:#3599B8;">
        <h3>Workspace occupancy</h3>
        <p class="cap">Which physical checkouts are in use, and by what.</p>
        <div class="slots" id="ov-slots"></div>
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
      <button id="review-scan" class="btn" style="display:none;" title="Re-probe every open review PR through the gh CLI — approvals, comments, change-requests, re-pushes, merges — and update each task's status. No model, no tokens.">↻ Scan reviews</button>
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
        <button id="pr-scan" class="btn primary" title="Re-probe every tracked PR through the gh CLI, and retire features whose PRs are all merged. No model, no tokens.">↻ Scan PRs</button>
        <button id="pr-scan-sel" class="btn" hidden title="Re-probe only the PRs of the ticked features">↻ Scan selected PRs</button>
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

  <div id="sec-pldmq" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>PLDM job queue</h2>
        <p class="sub" id="pq-asof"></p>
      </div>
      <div class="slicer">
        <a class="chip" id="pq-portal" target="_blank" rel="noopener">Queue portal &#8599;</a>
        <button id="pq-scan" class="btn primary" title="Follow every tracked job through the gh CLI: build run, queue row, execution run, and the failure evidence. No model, no tokens.">&#8635; Scan queue</button>
        <span id="pq-scan-note" class="ro-note"></span>
      </div>
    </div>
    <div class="kpis" id="pq-kpis"></div>
    <table id="pq-tbl">
      <thead><tr>
        <th style="width:20%;">Job</th><th style="width:14%;">Feature</th>
        <th style="width:12%;">Image</th><th style="width:16%;">Stage</th>
        <th style="width:10%;">Status</th><th style="width:12%;">EDK</th>
        <th style="width:8%;">Age</th><th style="width:8%;">Links</th>
      </tr></thead>
      <tbody id="pq-body"></tbody>
    </table>
  </div><!-- /sec-pldmq -->

  <div id="sec-vm" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>VM delegator log</h2>
        <p class="sub" id="vm-asof"></p>
      </div>
      <div class="slicer">
        <select id="vm-target" data-filter><option value="">All targets</option></select>
        <label class="chip"><input type="checkbox" id="vm-livefirst" checked> Live on top</label>
        <button id="vm-vms" class="btn" title="Query `octl vm list` for the power state, lease and IP of every EC2 instance. Does not touch the targets over ssh.">&#9741; Check VM status</button>
        <button id="vm-sync" class="btn" title="Mirror every target's job logs into ~/.mctl/jobs. The target keeps only the newest log per workspace and overwrites it on the next run, so this is what makes an old log openable later.">&#8595; Sync logs</button>
        <button id="vm-refresh" class="btn primary" title="Ask every target for the state of its job dirs, mirror its logs, and re-read the EC2 power state. One ssh round trip per machine plus an octl call. No model, no tokens.">&#8635; Refresh status</button>
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

    <!-- Log viewer. Doubles as a live tail: for a running job the panel
         re-polls `mctl jobs-tail` on a timer, which is a plain ssh round trip,
         so no websocket and no extra dependency. -->
    <div id="vm-logpane" class="logpane" style="display:none;">
      <div class="logpane-head">
        <div class="logpane-t">
          <b id="vm-log-title">log</b>
          <span id="vm-log-sub" class="muted"></span>
        </div>
        <div class="logpane-act">
          <label class="chip" id="vm-log-followwrap"><input type="checkbox" id="vm-log-follow"> Follow</label>
          <button id="vm-log-reload" class="ci-btn" title="Re-read this log now">&#8635;</button>
          <button id="vm-log-close" class="ci-btn" title="Close">&#10005;</button>
        </div>
      </div>
      <pre id="vm-log-body" class="logpane-body"></pre>
    </div>
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


TEAM_PLANNER = Path(os.path.expanduser("~/team-planner"))
IP_SCOREBOARD = Path(os.path.expanduser(
    "~/.copilot/skills/team-weekly/references/ip-scoreboard.md"))

# Fallback if the team-weekly reference table is missing or unparseable.
DEFAULT_IPS = [
    {"name": "PCIe EP", "owners": "Mateusz + Vasyl", "hints": ["pcie"]},
    {"name": "SE", "owners": "Akshay", "hints": ["ip-se-"]},
    {"name": "D2D", "owners": "Igal", "hints": ["d2d"]},
    {"name": "Infra/Mng", "owners": "FW Lead", "hints": ["infra", "mng", "noc"]},
]

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _plain(s: str) -> str:
    """Strip markdown links and bold markers from a field value."""
    return _MD_LINK.sub(r"\1", s or "").replace("**", "").strip()


def _parse_program_dates() -> dict:
    """Power-on / delivery gates and the sprint calendar from guidelines.md."""
    out = {"power_on": None, "a1_delivery": None, "sprints": []}
    f = TEAM_PLANNER / "guidelines.md"
    if not f.exists():
        return out
    txt = f.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\*\*Power-on:\*\*\s*\*\*(\d{4}-\d{2}-\d{2})\*\*", txt)
    if m:
        out["power_on"] = m.group(1)
    m = re.search(r"\*\*A1 big delivery:\*\*.*?\*\*(\d{4}-\d{2}-\d{2})\*\*", txt)
    if m:
        out["a1_delivery"] = m.group(1)

    year = int((out["power_on"] or out["a1_delivery"] or "2026")[:4])
    # "MS8.1=3749 (Aug 30–Sep 13)" — windows carry no year, so infer it and roll
    # over when the end month wraps past the start month.
    for name, sid, window in re.findall(r"(MS[\d.]+)=(\d+)\s*\(([^)]*)\)", txt):
        span = re.match(r"([A-Z][a-z]{2})\s+(\d{1,2})\s*[–-]\s*"
                        r"(?:([A-Z][a-z]{2})\s+)?(\d{1,2})", window.strip())
        start = end = None
        if span:
            sm, sd, em, ed = span.groups()
            em = em or sm
            try:
                start = date(year, _MONTHS[sm], int(sd))
                ey = year + 1 if _MONTHS[em] < _MONTHS[sm] else year
                end = date(ey, _MONTHS[em], int(ed))
            except (KeyError, ValueError):
                start = end = None
        out["sprints"].append({
            "name": name, "id": sid, "window": window.split(",")[0].strip(),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        })
    return out


def _parse_raid() -> list[dict]:
    """RAID rows for the Overview. team-planner's rd.py owns the parsing."""
    if rd is None:
        return []
    return rd.payload()["rows"]


def _parse_ips() -> list[dict]:
    """IP → owner → component-hint mapping from the team-weekly reference."""
    if not IP_SCOREBOARD.exists():
        return DEFAULT_IPS
    ips = []
    txt = IP_SCOREBOARD.read_text(encoding="utf-8", errors="replace")
    for line in txt.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("IP", "---") or set(cells[0]) == {"-"}:
            continue
        hints = re.findall(r'"([^"]+)"', cells[2])
        if not hints:
            continue
        ips.append({"name": cells[0], "owners": cells[1], "hints": hints})
    return ips or DEFAULT_IPS


def overview_payload() -> dict:
    """Team-level context for the portal's Overview storefront. All of it comes
    from local files — the portal never calls Jira/GitHub."""
    errors = {}
    try:
        raid = _parse_raid()
    except (ValueError, OSError) as error:
        raid = []
        errors["raid"] = str(error)
    try:
        program = _parse_program_dates()
    except (ValueError, OSError) as error:
        program = {"power_on": None, "a1_delivery": None, "sprints": []}
        errors["program"] = str(error)
    try:
        ips = _parse_ips()
    except (ValueError, OSError) as error:
        ips = DEFAULT_IPS
        errors["ips"] = str(error)
    return {"program": program, "raid": raid, "ips": ips,
            "today": date.today().isoformat(), "errors": errors}


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


def pldmq_payload() -> dict:
    """Tracked PLDM jobs from pjq.py. Read-only; scanning is a separate POST."""
    if pjq is None:
        return {"jobs": [], "counts": {}, "portal": "", "error": "pjq unavailable"}
    try:
        return pjq.payload()
    except Exception as e:
        return {"jobs": [], "counts": {}, "portal": "", "error": str(e)}


def vmlog_payload() -> dict:
    """Delegated VM/lab jobs from vd.py. Read-only; the probe is a separate POST."""
    if vd is None:
        return {"jobs": [], "running": [], "counts": {}, "targets": [], "machines": [],
                "machines_checked": "", "last_refresh": "", "log": "",
                "error": "vm-delegate unavailable"}
    try:
        return vd.payload()
    except Exception as e:
        return {"jobs": [], "running": [], "counts": {}, "targets": [], "machines": [],
                "machines_checked": "", "last_refresh": "", "log": "", "error": str(e)}


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
            and r["rev"] == "main" and r["exists"]]


def ws_ui_payload(wd: dict, *, cached_only: bool = False) -> dict:
    """Workspace state plus the tracked roster and its groups.

    ``wm.load()`` only records slots that have *ever* been held, so a UI built
    from it alone can't tell a free slot from one that doesn't exist.
    """
    roster = wm.roster(wd, cached_only=cached_only)
    canonical = ([r["slot"] for r in roster if not r["target"]
                  and r["rev"] == "main" and r["exists"]]
                 if cached_only else viable_slots())
    return dict(wd, canonical=canonical,
                roster=roster, groups=wm.groups(wd), targets=wm.machine_targets())


ASSETS = Path(__file__).resolve().parent / "assets"
ASSET_TYPES = {".css": "text/css; charset=utf-8",
               ".js": "application/javascript; charset=utf-8"}


def asset_text(name: str) -> str:
    """Read an asset off disk. Never cached, so an edit shows up on reload."""
    return (ASSETS / name).read_text(encoding="utf-8")


def asset_stamp() -> str:
    """Cache-buster derived from the assets' mtimes."""
    return str(int(max((ASSETS / n).stat().st_mtime for n in ("portal.css", "portal.js", "team.js"))))


# Oded's CI/CD pipeline explorer (~/odeds_sandbox, pipeline-explorer.service).
# The portal only deep-links into it; it never calls it.
PIPELINE_EXPLORER_URL = os.environ.get("PIPELINE_EXPLORER_URL", "http://127.0.0.1:8080")


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
        "pldmq": pldmq_payload(),
        "vmlog": vmlog_payload(),
        "team": team_api.payload(),
        "explorer": {"url": PIPELINE_EXPLORER_URL.rstrip("/")},
    }, ensure_ascii=False).replace("</", "<\\/")


def render_dashboard_html(tasks: list[dict], inline: bool = False) -> str:
    """Render the portal. ``inline`` embeds the assets for the static export,
    which is opened over ``file://`` and so cannot fetch ``/assets/…``."""
    wd = wm.load()
    if inline:
        styles = "<style>\n" + asset_text("portal.css") + "\n</style>"
        scripts = ("<script>\n" + asset_text("portal.js") + "\n</script>"
                   + "<script>\n" + asset_text("team.js") + "\n</script>")
    else:
        v = asset_stamp()
        styles = f'<link rel="stylesheet" href="/assets/portal.css?v={v}">'
        scripts = (f'<script src="/assets/portal.js?v={v}"></script>'
                   f'<script src="/assets/team.js?v={v}"></script>')
    return (DASHBOARD_TEMPLATE
            .replace("__STYLES__", styles)
            .replace("__BOOTDATA__", boot_payload(tasks, wd))
            .replace("__SCRIPTS__", scripts)
            .replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M")))


def cmd_dashboard(args: argparse.Namespace) -> int:
    d = tm.load()
    out = Path(os.path.expanduser(args.output)) if args.output else STATE_DIR / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard_html(d["tasks"], inline=True), encoding="utf-8")
    print(f"[tm] wrote portal with {len(d['tasks'])} tasks and "
          f"{len(bug_payload()['bugs'])} bugs -> {out}")
    return 0


# --- editing applied by the local server (reuses store rules) ----------------

PID_FILE = STATE_DIR / ".dashboard.pid"


def apply_task_edit(ident: str, payload: dict) -> tuple[bool, str]:
    """Delegate task mutations to the task-manager domain owner."""
    return tm.apply_task_edit(ident, payload)


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

ALIGN_JOBS: dict[str, dict] = {}
ALIGN_LOCK = _threading.Lock()
# Serialises the PR scan so a double-click can't launch two gh storms.
PR_SCAN_LOCK = _threading.Lock()

# Serialises `/ci` comment posting so a double-click can't double-fire CI.
PR_CI_LOCK = _threading.Lock()

# Same for the Jira mirror refresh.
BUG_SCAN_LOCK = _threading.Lock()

# Serialises the review scan (gh probe of every open review PR).
REVIEW_SCAN_LOCK = _threading.Lock()

# the RAID owner (rd.py) does not lock across processes.
RAID_LOCK = _threading.Lock()

# Serialises the PLDM queue scan (gh probe of every active tracked job).
PLDMQ_SCAN_LOCK = _threading.Lock()

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


def start_pldm_job(feature: dict, opts: dict) -> tuple[str | None, str]:
    """Reserve a slot and launch a PLDM submission job. Returns (job_id, message).

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
        ok, msg, _ = wm.occupy_adhoc(slot, f"PLDM test: {display}", tag="experiment")
        if not ok:
            return None, msg
        held = True

    jid = f"align-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "pldm", "slot": slot, "held": held,
        "feature_id": feature["id"], "feature": feature["name"],
        "display_name": display,
        "image_type": opts.get("image_type") or PLDM_IMAGE_TYPES[0],
        "group": opts.get("group", "default"),
        "timeout": max(1, min(120, int(opts.get("timeout") or 20))),
        "args": opts.get("args", ""),
        "prs": prs, "status": "running", "phase": "align",
        "started": tm.NOW, "finished": None, "current": None, "total": 0,
        "message": "", "repos": [], "run_url": "",
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_pldm_submit, args=(job,), daemon=True).start()
    return jid, f"{display} → {slot}"


PR_SCAN_ICONS = {
    "merged": ("\u2713", "ok"), "approved": ("\u2713", "ok"),
    "merge-queue": ("\u21bb", "ok"),
    "pending": ("\u00b7", "warn"), "needs-rebase": ("\u26a0", "warn"),
    "ci-fail": ("\u2715", "bad"), "changes-requested": ("\u2715", "bad"),
    "closed": ("\u2715", "bad"),
}


def run_pr_scan(job: dict, only=None) -> None:
    """Re-probe every tracked PR, streaming one row per PR into the job.

    Runs on a worker thread and releases PR_SCAN_LOCK, which the request handler
    acquired, so a second scan can't start while this one is in flight.
    """
    try:
        _run_pr_scan(job, only)
    finally:
        PR_SCAN_LOCK.release()


def _run_pr_scan(job: dict, only=None) -> None:
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
        result = pm.refresh(only=only, progress=progress)
    except Exception as e:
        with ALIGN_LOCK:
            job.update(status="error", finished=tm.NOW, current=None, message=str(e))
        return
    with ALIGN_LOCK:
        job["current"] = None
        job["finished"] = tm.NOW
        if result.get("error"):
            job.update(status="error", message=result["error"])
            return
        job["status"] = "done"
        job["result"] = result
        gone = result.get("purged") or []
        bad = result.get("failed") or []
        parts = []
        if gone:
            parts.append(f"retired {len(gone)}: " + ", ".join(f["name"] for f in gone))
        if bad:
            parts.append(f"\u26a0 {len(bad)} PR(s) gh could not reach: "
                         + ", ".join(f"{b['repo']}#{b['number']}" for b in bad[:4]))
        job["message"] = " \u00b7 ".join(parts)


def start_pr_scan_job(only=None) -> str:
    """Register + launch a background PR scan (all features, or ``only`` ids)."""
    jid = f"prscan-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "pr-scan", "slot": "PRs",
        "status": "running", "started": tm.NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_pr_scan, args=(job, only), daemon=True).start()
    return jid


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
        except Exception:                                      # noqa: BLE001
            pass                 # a sweep that fails must not kill the loop
        finally:
            VM_REFRESH_LOCK.release()


def start_vm_refresh_job(target: str | None = None) -> str:
    """Register + launch a background VM probe; return its id."""
    jid = f"vmrefresh-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "vm-refresh", "slot": f"VM jobs \u00b7 {target}" if target else "VM jobs",
        "target": target or "",
        "status": "running", "started": tm.NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_vm_refresh, args=(job,), daemon=True).start()
    return jid


# gh review state → (banner icon, tone) for each streamed review-scan row.
REVIEW_SCAN_ICONS = {
    "approved": ("\u2713", "ok"), "merged": ("\u2713", "ok"),
    "commented": ("\U0001f4ac", "warn"),
    "waiting-for-re-review": ("\u21bb", "warn"),
    "changes-requested": ("\u2715", "bad"), "closed": ("\u2715", "bad"),
    "error": ("\u26a0", "bad"),
}


def run_review_scan(job: dict) -> None:
    """Background worker: gh-probe every open review PR, streaming one row per
    changed/errored PR into the job so the UI banner can narrate progress.

    Releases REVIEW_SCAN_LOCK (acquired by the request handler) when done.
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
        result = tm.portal_review_scan(progress=progress)
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


def start_review_scan_job() -> str:
    """Register + launch a background review scan; return its id."""
    jid = f"revscan-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "review-scan", "slot": "reviews",
        "status": "running", "started": tm.NOW, "finished": None,
        "current": None, "total": 0, "scanned": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_review_scan, args=(job,), daemon=True).start()
    return jid


def start_alignment_job(task_id: str, slot_root: str, store_lock=None) -> str:
    """Register + launch a background alignment job; return its id."""
    jid = f"align-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "task_id": task_id, "slot": slot_root, "kind": "align",
        "status": "running", "started": tm.NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    th = _threading.Thread(
        target=run_workspace_alignment, args=(job, store_lock), daemon=True)
    th.start()
    return jid


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
    jid = "workspace-" + uuid.uuid4().hex
    with ALIGN_LOCK:
        if any(s in WORKSPACE_BUSY for s in slots):
            raise ValueError("a workspace operation is already running")
        WORKSPACE_BUSY.update(slots)
        job = {"id": jid, "kind": "workspace-action", "action": action,
               "slot": slot or f"{scope} workspaces", "status": "running",
               "started": wm.stamp(), "finished": None, "current": None,
               "total": len(slots), "message": "", "repos": [], "steps": []}
        ALIGN_JOBS[jid] = job

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
        reading = wm.workspace_request(
            current, "status" if action == "refresh" else action, progress=progress)
        if action == "free":
            progress({"step": "release", "label": "Releasing workspace holder"})
            with (store_lock or _threading.Lock()):
                ok, msg = apply_workspace_edit(current, {"action": "free"})
            if not ok:
                raise ValueError(msg)
        return reading

    def worker():
        errors = []
        try:
            for current in slots:
                try:
                    with ALIGN_LOCK:
                        job["current"] = wm.record(current)["label"]
                    reading = operate(current)
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
                job.update(status="error" if errors else "done", current=None,
                           finished=wm.stamp(), message="; ".join(errors) or f"{action} complete")
        finally:
            with ALIGN_LOCK:
                WORKSPACE_BUSY.difference_update(slots)

    _threading.Thread(target=worker, daemon=True).start()
    return jid


JOB_STEP_ERRORS_MAX = 100


def record_job_step(job: dict, event: dict, prefix: str = "") -> None:
    """Fold a runtime progress event into the job's step timeline (caller holds ALIGN_LOCK)."""
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
            if self.path.startswith("/api/team") or self.path in ("/", "/index.html"):
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, "ok", "text/plain")
            if self.path.startswith("/assets/"):
                name = self.path[len("/assets/"):].split("?")[0]
                ctype = ASSET_TYPES.get(Path(name).suffix)
                # Only the three known asset names, so a crafted path cannot escape.
                if name not in ("portal.css", "portal.js", "team.js") or not ctype:
                    return self._send(404, json.dumps({"error": "not found"}))
                return self._send(200, asset_text(name), ctype)
            if self.path in ("/", "/index.html"):
                with lock:
                    html = render_dashboard_html(tm.load()["tasks"])
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
            if self.path == "/api/overview":
                with lock:
                    return self._send(200, json.dumps(overview_payload(), ensure_ascii=False))
            if self.path == "/api/prs":
                with lock:
                    return self._send(200, json.dumps(pr_payload(), ensure_ascii=False))
            if self.path == "/api/raid":
                with RAID_LOCK:
                    return self._send(200, json.dumps(raid_payload(), ensure_ascii=False))
            if self.path == "/api/pldm-jobs":
                return self._send(200, json.dumps(pldmq_payload(), ensure_ascii=False))
            if self.path == "/api/vm-jobs":
                return self._send(200, json.dumps(vmlog_payload(), ensure_ascii=False))
            if self.path.startswith("/api/align/"):
                jid = self.path[len("/api/align/"):]
                with ALIGN_LOCK:
                    job = ALIGN_JOBS.get(jid)
                    body = json.dumps(job, ensure_ascii=False) if job else None
                if body is None:
                    return self._send(404, json.dumps({"error": "unknown job"}))
                return self._send(200, body)
            return self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            from urllib.parse import unquote

            if self.path == "/api/team":
                origin = self.headers.get("Origin")
                expected = f"http://{self.headers.get('Host')}"
                if self.headers.get("X-Team-Private") != "1" or (origin and origin != expected):
                    return self._send(403, json.dumps({"error": "Enable private mode to edit team records"}))
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    if not 0 < size <= 2_000_000:
                        raise ValueError("Request must be between 1 byte and 2 MB")
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict) or not isinstance(data.get("action"), str):
                        raise ValueError("action and data are required")
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
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    try:
                        ids = json.loads(self.rfile.read(n) or b"{}").get("feature_ids")
                        if ids is not None:
                            only = [int(i) for i in ids]
                    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                        return self._send(400, json.dumps({"error": "feature_ids must be a list of ids"}))
                    if only is not None and not only:
                        return self._send(400, json.dumps({"error": "no features selected"}))
                if not PR_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a PR scan is already running"}))
                try:
                    jid = start_pr_scan_job(only)
                except Exception as e:
                    PR_SCAN_LOCK.release()
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

            # The review scan probes every open review PR through gh (bodyless,
            # slow-ish). Runs as a background job that streams progress into
            # ALIGN_JOBS so the UI can show a live banner; the worker mutates the
            # task store and releases REVIEW_SCAN_LOCK when done.
            if self.path == "/api/review-scan":
                if not REVIEW_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps(
                        {"error": "a review scan is already running"}))
                try:
                    jid = start_review_scan_job()
                except Exception as e:
                    REVIEW_SCAN_LOCK.release()
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            # The PLDM queue scan follows every active tracked job through the
            # gh CLI (bodyless, slow-ish, zero tokens). It returns the refreshed
            # payload directly so the section can re-render in place.
            if self.path == "/api/pldm-scan":
                if pjq is None:
                    return self._send(503, json.dumps({"error": "pldm-job-queue not installed"}))
                if not PLDMQ_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a PLDM scan is already running"}))
                try:
                    result = pjq.portal_scan()
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    PLDMQ_SCAN_LOCK.release()
                return self._send(200, json.dumps(result, ensure_ascii=False))

            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": "invalid JSON"}))

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

            # Manual `octl ws sync`, independent of occupy/free — usable on an
            # occupied or a free slot alike.
            if self.path.startswith("/api/ws-sync/"):
                slot = unquote(self.path[len("/api/ws-sync/"):])
                root = wm.normalize_slot(slot)
                if not root:
                    return self._send(400, json.dumps({"error": f"invalid slot: {slot!r}"}))
                try:
                    jid = start_workspace_job("sync", root, store_lock=lock)
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
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM refresh is already running"}))
                try:
                    jid = start_vm_refresh_job(only)
                except Exception as e:
                    VM_REFRESH_LOCK.release()
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
                    badge=payload.get("badge"), target=payload.get("target"))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/ws-untrack/"):
                slot = unquote(self.path[len("/api/ws-untrack/"):])
                try:
                    ok, msg = wm.untrack(slot)
                except ValueError as e:
                    return self._send(409, json.dumps({"error": str(e)}))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/ws-meta/"):
                slot = unquote(self.path[len("/api/ws-meta/"):])
                try:
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
                             "/api/feature-edit"):
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
    PID_FILE.write_text(str(os.getpid()))

    # Run the request loop in a background thread so the main thread can call
    # httpd.shutdown() safely (shutdown() deadlocks if invoked from the same
    # thread as serve_forever()).
    stop = threading.Event()
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()

    # Keeps VM job statuses honest without a click: see run_vm_watchdog.
    if vd is not None:
        threading.Thread(target=run_vm_watchdog, args=(stop,), daemon=True).start()

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"[tm] serving editable dashboard at http://127.0.0.1:{args.port} (Ctrl-C to stop)")
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
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
