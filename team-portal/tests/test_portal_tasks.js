const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "portal.js"), "utf8");
const template = fs.readFileSync(path.join(__dirname, "..", "portal.py"), "utf8");
const dateHelpersStart = source.indexOf("function validPortalDate");
const dateHelpersEnd = source.indexOf("let TASKS", dateHelpersStart);
assert.notEqual(dateHelpersStart, -1);
assert.notEqual(dateHelpersEnd, -1);
function installDateHelpers(context){
  vm.runInContext(source.slice(dateHelpersStart,dateHelpersEnd),context);
}
function block(start, end) {
  const offset = source.indexOf(start);
  assert.notEqual(offset, -1);
  const limit = source.indexOf(end, offset);
  assert.notEqual(limit, -1);
  return source.slice(offset, limit);
}

function harness() {
  const filters = {};
  const elements = {
    q: {value: ""},
    stats: {innerHTML: ""},
    "o-hidedone": {checked: true, closest: () => ({classList: {remove() {}}})},
    "o-jira": {checked: false},
    "o-ws": {checked: false},
    "o-review": {checked: false},
  };
  const tasks = [
    {id: "T-001", category: "development", status: "todo", priority: "high", cost_hours: 2},
    {id: "T-002", category: "planning", status: "todo", priority: "low", jira: "TEST-1"},
    {id: "T-003", category: "code-review", status: "blocked", priority: "high",
      reviews: [{author: "alice", review_status: "changes-requested"}]},
    {id: "T-004", category: "code-review", status: "done", priority: "low",
      reviews: [{author: "bob", review_status: "merged", merged: true}]},
    {id: "T-005", category: "misc", status: "cancelled", priority: "low"},
    {id: "T-006", category: "track", status: "todo", priority: "medium"},
    {id: "T-007", category: "testing", status: "in-progress", priority: "medium"},
  ];
  const context = vm.createContext({
    TASKS: tasks,
    window: {},
    checkedVals: group => filters[group] || [],
    document: {getElementById: id => elements[id]},
  });
  installDateHelpers(context);
  vm.runInContext(
    block("function prs(", "// ---- workspace alignment") +
    block("function applyFilters()", "function sortTasks(") +
    block("function renderStats()", "// ---- events ----"), context);
  const ids = () => Array.from(context.applyFilters(), task => task.id);
  return {context, filters, elements, tasks, ids};
}

function draftHarness(served=true) {
  const context=vm.createContext({
    SERVED: served, encodeURIComponent, window:{},
    JIRA_BASE: "https://jira.example/browse/",
    esc: value=>String(value).replaceAll("&","&amp;").replaceAll("<","&lt;")
      .replaceAll('"',"&quot;"),
    editorHtml: ()=>"",
  });
  installDateHelpers(context);
  vm.runInContext(block("function daysSince(", "// ---- workspace alignment")+
    block("function feature(", "// ---- build filter dropdowns")+
    block("function reviewDraftsHtml(", "function wireEditor("), context);
  return context;
}

test("date display is fixed dd/mm/yyyy and date entry round-trips strictly to ISO", () => {
  const context=vm.createContext({window:{}});
  installDateHelpers(context);
  assert.equal(context.formatPortalDate("2026-10-28"),"28/10/2026");
  assert.equal(context.formatPortalDate("2026-10-28T13:45:00Z"),"28/10/2026 13:45:00Z");
  assert.equal(context.formatPortalDateInput("2026-10-28"),"28/10/2026");
  assert.equal(context.parsePortalDateInput("28/10/2026"),"2026-10-28");
  assert.equal(context.parsePortalDateInput("29/02/2024"),"2024-02-29");
  assert.equal(context.parsePortalDateInput("29/02/2026"),null);
  assert.equal(context.parsePortalDateInput("1/2/2026"),null);
  assert.equal(context.parsePortalDateInput(""),"");
});

test("review task renders a per-PR draft button, reviewed SHA and earlier passes", () => {
  const context=draftHarness();
  const task={id:"T-001",created:"2026-10-01",updated:"2026-10-02T09:10:00Z",
    due:"2026-10-31",completed:"2026-10-03",reviews:[
    {repo:"one",pr_number:1,pr_url:"https://github.com/example/one/pull/1",drafts:[
      {id:"old",updated_at:"2026-10-27",head_sha:"a".repeat(40)},
      {id:"new",updated_at:"2026-10-28",head_sha:"b".repeat(40)},
    ]},
    {repo:"two",pr_number:1,pr_url:"https://github.com/example/two/pull/1"},
  ]};
  const html=context.detailRow(task,8);
  assert.match(html,/class="btn review-draft-link" href="\/review-drafts\/T-001\/new"/);
  assert.match(html,/Open review draft/);
  assert.match(html,/Earlier drafts \(1\)/);
  assert.match(html,/\/review-drafts\/T-001\/old/);
  assert.match(html,/b{40}/);
  assert.match(html,/Saved 28\/10\/2026/);
  assert.match(html,/Created:<\/b> 01\/10\/2026/);
  assert.match(html,/Updated:<\/b> 02\/10\/2026 09:10:00Z/);
  assert.match(html,/Due:<\/b> 31\/10\/2026/);
  assert.match(html,/Completed:<\/b> 03\/10\/2026/);
  assert.equal((html.match(/Open review draft/g)||[]).length,1);
  assert.equal(task.reviews[0].drafts[0].id,"old");
});

test("static draft metadata avoids broken relative links and never embeds contents", () => {
  const context=draftHarness(false);
  const html=context.reviewDraftsHtml({id:"T-001"},{drafts:[
    {id:"one",updated_at:"2026-10-28",head_sha:"a".repeat(40),content:"PRIVATE BODY"},
  ]});
  assert.match(html,/open the served portal to read/);
  assert.doesNotMatch(html,/href=|PRIVATE BODY/);
  assert.equal(context.reviewDraftsHtml({id:"T-001"},{}),"");
});

test("draft metadata is escaped and URL segments are encoded", () => {
  const html=draftHarness().reviewDraftsHtml({id:'T-001" onmouseover="evil'}, {drafts:[
    {id:"../bad",updated_at:"<script>",head_sha:'"><img src=x>'},
  ]});
  assert.doesNotMatch(html,/<script>|<img|href="[^"]*" onmouseover/);
  assert.match(html,/%22/);
  assert.match(html,/\.\.%2Fbad/);
});

test("expanded task detail folds notes and the editor and carries the hidden-column metadata", () => {
  const context=draftHarness();
  const task={id:"T-009",title:"Do thing",jira:"TEST-9",cost_hours:3,workspace:"ws1",
    created:"2026-10-01",updated:"2026-10-02",
    notes:["2026-10-02: first pass","2026-10-03: second pass"]};
  const html=context.detailRow(task,7);
  assert.match(html,/<details class="task-notes"><summary>Notes <span class="muted">\(2\)<\/span><\/summary>/);
  assert.match(html,/<details class="task-edit-wrap"><summary>Edit task<\/summary>/);
  assert.match(html,/Jira:<\/b> <a href="https:\/\/jira\.example\/browse\/TEST-9"/);
  assert.match(html,/Est:<\/b> 3h/);
  assert.match(html,/WS:<\/b> ws1/);
  assert.match(html,/Worked:<\/b>/);
  // no description — the derived feature line stands in so context is never lost
  assert.match(html,/class="feature"/);
  // a task with a description shows it instead of the derived feature line
  const described=context.detailRow({...task,description:"Full description here.",notes:[]},7);
  assert.match(described,/<h4>Description<\/h4>Full description here\./);
  assert.doesNotMatch(described,/class="feature"|task-notes/);
  // static exports never render an edit disclosure
  assert.doesNotMatch(draftHarness(false).detailRow(task,7),/task-edit-wrap/);
});

test("task rows are single-line with an expand caret and lean default columns", () => {
  assert.match(source,/class="task-toggle" aria-expanded="false"/);
  assert.doesNotMatch(source,/class="feature">\$\{esc\(feature\(t\)\)\}<\/div><\/td>/);
  assert.match(source,/\{key:"cost",\s+label:"Est",\s+on:false\}/);
  assert.match(source,/\{key:"jira",\s+label:"Jira",\s+on:false\}/);
  assert.match(source,/\{key:"worked",\s+label:"Worked",\s+on:false\}/);
  assert.match(source,/\{key:"category",label:"Category",\s+on:true\}/);
  assert.match(source,/\{key:"age",\s+label:"Age",\s+on:true\}/);
  // the row click flips the caret glyph and its accessible expanded state
  assert.match(source,/toggle\.setAttribute\("aria-expanded",String\(show\)\)/);
  assert.match(source,/toggle\.textContent=show\?"\\u25be":"\\u25b8"/);
});

test("unified view includes every category and preserves hide-finished default", () => {
  const {ids, elements} = harness();
  assert.deepEqual(ids(), ["T-001", "T-002", "T-003", "T-006", "T-007"]);
  assert.match(source, /taskLabel\.lastChild\.textContent=" Open only"/);
  elements["o-hidedone"].checked = false;
  assert.deepEqual(ids(), ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006", "T-007"]);
});

test("VM open-only shows running jobs without changing the saved job list", () => {
  const jobs = [
    {id: "running-1", target: "vm1", status: "running"},
    {id: "passed-1", target: "vm1", status: "passed"},
    {id: "failed-1", target: "vm2", status: "failed"},
  ];
  const elements = {
    "vm-openonly": {checked: true},
    "vm-livefirst": {checked: true},
  };
  const context = vm.createContext({
    VMLOG: {jobs}, vmTargets: [],
    document: {getElementById: id => elements[id]},
  });
  vm.runInContext(block("function vmJobs()", "// The elapsed figure"), context);
  assert.deepEqual(Array.from(context.vmJobs(), job => job.id), ["running-1"]);
  elements["vm-openonly"].checked = false;
  assert.deepEqual(Array.from(context.vmJobs(), job => job.id), ["running-1", "passed-1", "failed-1"]);
  assert.equal(jobs.length, 3);
  assert.match(source, /makeOpenOnly\("vm-openonly"\)/);
  assert.match(source, /id="vm-count"/);
});

test("RAID Open only hides closed rows and retains them when cleared", () => {
  const elements = {"raid-openonly": {checked: true}};
  const context = vm.createContext({
    RAID: {rows: [{id: "R-1", closed: false}, {id: "R-2", closed: true}]},
    document: {getElementById: id => elements[id]},
  });
  vm.runInContext(block("function raidRows()", "function renderRaid()"), context);
  assert.deepEqual(Array.from(context.raidRows(), row => row.id), ["R-1"]);
  elements["raid-openonly"].checked = false;
  assert.deepEqual(Array.from(context.raidRows(), row => row.id), ["R-1", "R-2"]);
  assert.match(source, /raidInput\.id="raid-openonly"/);
});

test("phenomena Open only is the inverse of the former Show closed toggle", () => {
  const context = vm.createContext({
    PH_CLOSED: ["closed", "not-a-bug", "not-ours", "merged"],
    phFilter: {openOnly: true, status: [], signal: [], platform: [], flag: [], origin: [], q: ""},
    phIsOpen: row => !["closed", "not-a-bug", "not-ours", "merged"].includes(row.status),
    msHas: () => true, msAny: () => true,
  });
  vm.runInContext(block("function phMatches(", "function phSortKey("), context);
  const open = {status: "active"}, closed = {status: "closed"};
  assert.equal(context.phMatches(open), true);
  assert.equal(context.phMatches(closed), false);
  context.phFilter.openOnly = false;
  assert.equal(context.phMatches(closed), true);
  assert.match(source, /class="ph-openonly".*?> Open only/);
});

test("categories OR together and intersect with other filters", () => {
  const {filters, ids} = harness();
  filters.category = ["development", "planning"];
  assert.deepEqual(ids(), ["T-001", "T-002"]);
  filters.priority = ["high"];
  assert.deepEqual(ids(), ["T-001"]);
  filters.category = [];
  assert.deepEqual(ids(), ["T-001", "T-003"]);
});

test("review status and author filters work without a review tab", () => {
  const {filters, ids} = harness();
  filters.reviewstatus = ["waiting-for-author"];
  filters.author = ["alice"];
  assert.deepEqual(ids(), ["T-003"]);
  filters.author = ["bob"];
  assert.deepEqual(ids(), []);
});

test("task selection prunes hidden rows, edits multiple fields, and scopes review scans", () => {
  const context=vm.createContext({
    selectedTaskIds:new Set(["T-001","T-002","T-003","T-hidden"]),
    prs:task=>task.reviews||[],
  });
  vm.runInContext(block("function taskHasOpenReview(", "function taskBulkDialog("),context);
  const tasks=[
    {id:"T-001",revision:"r1",category:"code-review",status:"in-progress",
      reviews:[{review_status:"pending"},{review_status:"approved"}]},
    {id:"T-002",revision:"r2",category:"code-review",status:"done",
      reviews:[{review_status:"merged",merged:true}]},
    {id:"T-003",revision:"r3",category:"development",status:"todo"},
  ];
  context.pruneTaskSelection(tasks);
  assert.deepEqual(Array.from(context.selectedTaskIds),["T-001","T-002","T-003"]);
  assert.deepEqual(Array.from(context.openReviewTaskIds(tasks)),["T-001"]);
  assert.deepEqual(JSON.parse(JSON.stringify(context.taskBulkEdits(tasks,{
    priority:"high",complexity:"small",
  }))),[
    {id:"T-001",expected_revision:"r1",priority:"high",complexity:"small"},
    {id:"T-002",expected_revision:"r2",priority:"high",complexity:"small"},
    {id:"T-003",expected_revision:"r3",priority:"high",complexity:"small"},
  ]);
});

test("category summaries include merged history and multiple-category totals", () => {
  const {context, filters, elements} = harness();
  filters.category = ["code-review"];
  context.renderStats();
  assert.match(elements.stats.innerHTML, />2<\/div><div class="l">Total/);
  assert.match(elements.stats.innerHTML, />1<\/div><div class="l">Merged/);
  filters.category = ["planning", "development"];
  context.renderStats();
  assert.match(elements.stats.innerHTML, />2<\/div><div class="l">Total/);
  assert.doesNotMatch(elements.stats.innerHTML, /Merged/);
});

test("Tasks keeps the work route, removes category tabs, and exposes review filters", () => {
  assert.match(source, /id:"work",\s+label:"Tasks"/);
  assert.doesNotMatch(source, /\b(activeTab|TAB_DEFS|buildTabs|selectTab|applyTabPreset)\b/);
  assert.doesNotMatch(template, /<nav class="tabs" id="tabs">/);
  const row = template.match(/<div class="row" id="row-review"[^>]*>/);
  assert.ok(row);
  assert.doesNotMatch(row[0], /display:none/);
  assert.match(source, /reviewScan\.style\.display=SERVED\?"":"none"/);
});

test("task deep links clear conflicting filters and reveal completed records", () => {
  const {context, elements, filters} = harness();
  const events = [];
  filters.category = ["development"];
  elements.reset = {click() {
    Object.keys(filters).forEach(key => delete filters[key]);
    elements["o-hidedone"].checked = true;
  }};
  Object.assign(context, {
    selectSection: id => events.push(id),
    render() {},
    setHash: hash => events.push(hash),
    CSS: {escape: value => value},
  });
  context.document.querySelector = () => ({
    click: () => events.push("expanded"),
    scrollIntoView() {},
  });
  vm.runInContext(block("function goItem(", "function applyHash("), context);
  context.goItem("task", "T-004");
  assert.deepEqual(filters, {});
  assert.equal(elements["o-hidedone"].checked, false);
  assert.equal(elements.q.value, "T-004");
  assert.deepEqual(events, ["work", "expanded", "task/T-004"]);
});

test("workspace top Refresh only reloads saved data and renders without a status job", async () => {
  const calls = [], rendered = [];
  const saved = {roster: [{slot: "one", status: {checked: "saved"}}]};
  const button = {disabled: false, classList: {add() {}, remove() {}}};
  const note = {textContent: ""};
  const context = vm.createContext({
    SERVED: true, activeSection: "ws", WORKSPACES: {},
    selectedTaskIds:new Set(["T-001"]), taskSelectionMessage:"old selection",
    document: {getElementById: id => id === "refresh" ? button : note},
    fetch: async (url, options) => {
      calls.push({url, options});
      return {ok: true, json: async () => ({tasks: [{id: "T-1", title: "Saved title"}],
        workspaces: saved})};
    },
    buildSections() {}, renderWorkspaces() { rendered.push("workspaces"); },
    wsRefresh() { throw new Error("Live checks must not run"); },
  });
  vm.runInContext(block("async function refreshData()", 'document.getElementById("refresh").addEventListener'), context);
  await context.refreshData();
  assert.deepEqual(calls, [{url: "/api/tasks", options: calls[0].options}]);
  assert.equal(calls[0].options.method, undefined);
  assert.equal(context.WORKSPACES, saved);
  assert.equal(context.TASKS[0].title, "Saved title");
  assert.equal(context.selectedTaskIds.size, 0);
  assert.equal(context.taskSelectionMessage, "");
  assert.deepEqual(rendered, ["workspaces"]);
  assert.match(note.textContent, /live status not checked/);
  assert.equal(button.disabled, false);
});

test("workspace top Refresh reports saved-data failures and keeps previous readings", async () => {
  const saved = {roster: []}, note = {textContent: ""};
  const context = vm.createContext({
    SERVED: true, activeSection: "ws", WORKSPACES: saved,
    selectedTaskIds:new Set(["T-001"]), taskSelectionMessage:"old selection",
    document: {getElementById: id => id === "refresh" ? null : note},
    fetch: async () => ({ok: false, json: async () => ({error: "database unavailable"})}),
  });
  vm.runInContext(block("async function refreshData()", 'document.getElementById("refresh").addEventListener'), context);
  await context.refreshData();
  assert.equal(context.WORKSPACES, saved);
  assert.equal(note.textContent, "Refresh failed: database unavailable");
});

test("local, VM and per-card status checks use distinct scopes and reload after completion", async () => {
  for (const [slot, scope, expected] of [
    [null, "local", "/api/ws-refresh"],
    [null, "remote", "/api/ws-check-vm"],
    ["remote/one", "local", "/api/ws-refresh/remote%2Fone"],
  ]) {
    const events = [];
    const context = vm.createContext({
      fetch: async (url, options) => {
        events.push([url, options.method]);
        return {ok: true, json: async () => ({align_job: "job-1"})};
      },
      pollAlign: async (id, title) => { events.push([id, title]); return {status: "done"}; },
      wsReload: async () => { events.push("reloaded"); },
    });
    vm.runInContext(block("async function wsRefresh(", "async function wsShowLog("), context);
    await context.wsRefresh(slot, scope);
    assert.deepEqual(events[0], [expected, "POST"]);
    assert.equal(events[2], "reloaded");
    assert.match(events[1][1], slot ? /Checking workspace/ :
      scope === "remote" ? /Checking VM workspaces/ : /Checking local workspaces/);
  }
});

test("workspace check buttons retain explicit labels and top refresh never becomes a status check", () => {
  assert.match(source, /class="ws-refresh-local btn"[^>]*>↻ Check local status<\/button>/);
  assert.match(source, /class="ws-check-vm btn"[^>]*>↻ Check VM status<\/button>/);
  assert.doesNotMatch(source, /Refresh local/);
  assert.match(source, /refresh\.innerHTML=`<span class="ico">↻<\/span> Refresh`/);
  assert.match(source, /b\.classList\.contains\("ws-check-vm"\)\?"remote":"local"/);
});

test("portal tables expose shared sort and resize standard", () => {
  assert.match(source, /const TABLE_STD_KEY = "portal-table-std-v1"/);
  assert.match(source, /window\.PortalTableUI = \{apply: applyTableStandards\}/);
  assert.match(source, /className = "col-resizer"/);
  assert.match(source, /table\.style\.tableLayout = "fixed"/);
});

test("PR feature editor is a collapsible disclosure, collapsed by default and remembered", () => {
  const row = block("function prFeatureRow", "function mdInline");
  assert.match(row, /<details class="pr-edit-wrap" data-fid="/);
  assert.match(row, /PR_EDIT_OPEN\.has\(String\(f\.id\)\)\?" open":""/);
  assert.match(row, /<summary>Edit feature<\/summary>/);
  assert.match(row, /<\/div><\/div><\/details>'/);
  assert.doesNotMatch(row, /<h4>Edit feature<\/h4>/);
  const render = block("function renderPrs", "function prFeatureRow");
  assert.match(render, /details\.pr-edit-wrap/);
  assert.match(render, /PR_EDIT_OPEN\.add\(d\.dataset\.fid\)/);
  assert.match(render, /PR_EDIT_OPEN\.delete\(d\.dataset\.fid\)/);
});

test("workspace error banner collapses repeated per-repo failures", () => {
  const context = vm.createContext({});
  vm.runInContext(block("function wsErrorSummary(", "function wsUnknown("), context);
  const denied = "git@github.com: Permission denied (publickey). fatal: Could not read";
  assert.equal(context.wsErrorSummary([denied, denied, denied].join("; ")), `${denied} (\u00d73 repos)`);
  assert.equal(context.wsErrorSummary("a; b; a"), "a (\u00d72 repos); b");
  assert.equal(context.wsErrorSummary("single failure"), "single failure");
  assert.equal(context.wsErrorSummary(""), "");
});

test("workspace action banner shows live steps, counts, detail and failures", () => {
  const els = {"align-banner": {style: {}}, "align-spin": {style: {}},
    "align-title": {textContent: ""}, "align-body": {innerHTML: ""}};
  const context = vm.createContext({document: {getElementById: id => els[id]},
    esc: s => String(s).replace(/</g, "&lt;"), ALIGN_ICONS: {}});
  vm.runInContext(block("function wsErrorSummary(", "function wsUnknown(")
    + block("function alignRepoRow(", "async function pollAlign("), context);
  context.renderAlign({kind: "workspace-action", action: "free", slot: "two", status: "running",
    steps: [
      {key: "return", label: "Returning repositories", state: "done", done: 33, total: 33,
        errors: ["a: denied", "b: denied"]},
      {key: "sync", label: "Running octl ws sync", state: "running", detail: "Fetching <x>"}]});
  assert.equal(els["align-title"].textContent, "free · two · Running octl ws sync");
  const html = els["align-body"].innerHTML;
  assert.match(html, /33\/33/);
  assert.match(html, /2 repo failure\(s\)/);
  assert.match(html, /denied \(\u00d72 repos\)/);
  assert.match(html, /Fetching &lt;x>/);
  assert.match(html, /ab-step-running/);
});

test("workspace action banner exposes pending approval and saved stash recovery", () => {
  const els = {"align-banner": {style: {}}, "align-spin": {style: {}},
    "align-title": {textContent: ""}, "align-body": {innerHTML: ""}};
  const context = vm.createContext({document: {getElementById: id => els[id]},
    esc: s => String(s).replace(/</g, "&lt;"), ALIGN_ICONS: {}});
  vm.runInContext(block("function wsErrorSummary(", "function wsUnknown(")
    + block("function alignRepoRow(", "async function pollAlign("), context);
  context.renderAlign({id:"approval-1",kind:"workspace-action",action:"free",
    slot:"two",status:"approval_required",steps:[],repos:[],message:"No changes made"});
  assert.match(els["align-title"].textContent,/approval required/);
  assert.match(els["align-body"].innerHTML,/data-workspace-approval="approval-1"/);
  context.renderAlign({id:"sync-approval",kind:"workspace-action",action:"sync",
    slot:"two",status:"approval_required",steps:[],repos:[]});
  assert.match(els["align-body"].innerHTML,/data-workspace-approval="sync-approval">Force sync/);
  context.renderAlign({kind:"workspace-action",action:"free",slot:"two",status:"done",
    steps:[],repos:[],stashes:[{path:"/repo/<one>",branch:"dev/test",oid:"abc123"}]});
  assert.match(els["align-body"].innerHTML,/git stash apply --index/);
  assert.match(els["align-body"].innerHTML,/\/repo\/&lt;one>/);
  assert.match(els["align-body"].innerHTML,/abc123/);
});

function bannerHarness(jobs=[]) {
  const elements = {
    "align-banner": {style: {}}, "align-spin": {style: {}},
    "align-title": {textContent: ""}, "align-body": {innerHTML: "", hidden: false},
    "action-queue": {innerHTML: "", hidden: false},
    "action-queue-open": {textContent: "", hidden: true},
    "align-toggle": {textContent: "Collapse", attributes: {"aria-expanded": "true"},
      setAttribute(name, value) { this.attributes[name] = value; }},
  };
  let click, requests=0;
  const context = vm.createContext({
    document: {getElementById: id => elements[id],
      addEventListener: (event, handler) => { if(event==="click") click=handler; }},
    esc: s => String(s), encodeURIComponent,
    fetch: async () => ({ok: true, json: async () => jobs[requests++]}),
    setTimeout: callback => callback(),
  });
  vm.runInContext(block("// ---- workspace alignment", "function feature("), context);
  return {context, elements, click: id => click({target: elements[id] || {id}}),
    toggle() { click({target: Object.assign(elements["align-toggle"], {id: "align-toggle"})}); },
    requests: () => requests};
}

test("operation banner toggles details with accessible state and keeps header visible", () => {
  const {elements, toggle}=bannerHarness();
  assert.match(template, /id="align-toggle"[^>]*type="button"[^>]*aria-expanded="true"[^>]*aria-controls="align-body action-queue"/);
  toggle();
  assert.equal(elements["align-body"].hidden, true);
  assert.equal(elements["action-queue"].hidden, true);
  assert.equal(elements["align-toggle"].textContent, "Expand");
  assert.equal(elements["align-toggle"].attributes["aria-expanded"], "false");
  assert.equal(elements["align-banner"].style.display, undefined);
  toggle();
  assert.equal(elements["align-body"].hidden, false);
  assert.equal(elements["action-queue"].hidden, false);
  assert.equal(elements["align-toggle"].textContent, "Collapse");
  assert.equal(elements["align-toggle"].attributes["aria-expanded"], "true");
});

test("collapsed operation banner keeps polling and retains completion and error details", async () => {
  const running={kind: "review-scan", status: "running", repos: [], scanned: 1, total: 2};
  const done={...running, status: "done", scanned: 2, message: "Finished scan"};
  const failed={...running, status: "error", message: "Synthetic failure"};
  const {context, elements, toggle, requests}=bannerHarness([running, done, failed]);
  toggle();
  const result=await context.pollAlign("synthetic");
  assert.equal(result, done);
  assert.equal(requests(), 2);
  assert.equal(elements["align-banner"].style.display, "block");
  assert.equal(elements["align-body"].hidden, true);
  assert.match(elements["align-title"].innerHTML, /Checked 2 review PR/);
  assert.match(elements["align-body"].innerHTML, /Finished scan/);
  await context.pollAlign("next-synthetic");
  assert.equal(elements["align-body"].hidden, true);
  assert.match(elements["align-title"].innerHTML, /Review scan failed/);
  toggle();
  assert.equal(elements["align-body"].hidden, false);
  assert.match(elements["align-body"].innerHTML, /Synthetic failure/);
});

test("operation banner dismiss remains separate from collapse", () => {
  const {context, elements, toggle, click}=bannerHarness();
  toggle();
  click("align-close");
  assert.equal(elements["align-banner"].style.display, "none");
  context.renderAlign({kind: "review-scan", status: "done", repos: []});
  assert.equal(elements["align-banner"].style.display, "block");
  assert.equal(elements["align-body"].hidden, true);
});

test("queue popup keeps polling waiting jobs instead of treating queued as completion", async () => {
  const queued={id:"one",kind:"pr-scan",slot:"PRs",status:"queued",queue_position:2,repos:[]};
  const running={...queued,status:"running"};
  const done={...queued,status:"done",message:"All done"};
  const {context,elements,requests}=bannerHarness([queued,running,done]);
  context.renderAlign(queued);
  assert.match(elements["align-title"].textContent,/Waiting #2/);
  assert.match(elements["align-body"].textContent,/Queued/);
  const result=await context.pollAlign("one");
  assert.equal(result,done);
  assert.equal(requests(),3);
});

test("queue popup lists positions and errors, selects jobs, and retains collapse/dismiss state", () => {
  const {context,elements,toggle,click}=bannerHarness();
  const running={id:"one",kind:"workspace-action",action:"sync",slot:"one",status:"running",steps:[]};
  const waiting={id:"two",kind:"pr-scan",slot:"PRs",status:"queued",queue_position:1};
  const failed={id:"old",kind:"pr-scan",slot:"PRs",status:"error",message:"Synthetic error",repos:[]};
  const data={actions:[running,waiting,failed]};
  context.renderActionQueue(data);
  assert.match(elements["action-queue"].innerHTML,/Waiting #1/);
  assert.match(elements["action-queue"].innerHTML,/ab-bad/);
  assert.equal(elements["action-queue-open"].textContent,"Actions (2)");
  toggle();
  context.renderActionQueue(data);
  assert.equal(elements["action-queue"].hidden,true);
  assert.equal(elements["align-body"].hidden,true);
  vm.runInContext('ACTION_UI.selected="two"',context);
  context.renderActionQueue(data);
  assert.match(elements["align-title"].textContent,/Waiting #1/);
  click("align-close");
  context.renderActionQueue(data);
  assert.equal(elements["align-banner"].style.display,"none");
  assert.equal(vm.runInContext("ACTION_UI.dismissed",context),true);
  assert.match(template,/id="action-queue-open"/);
});

test("finishing a non-selected polled action does not overwrite selected action details", async () => {
  let release;
  const {context,elements}=bannerHarness();
  context.fetch=()=>new Promise(resolve=>{ release=resolve; });
  const polling=context.pollAlign("background");
  vm.runInContext('ACTION_UI.selected="selected"',context);
  elements["align-title"].textContent="Selected action";
  release({ok:true,json:async()=>({status:"done",kind:"pr-scan",repos:[]})});
  await polling;
  assert.equal(elements["align-title"].textContent,"Selected action");
});

test("queue API failure is explicit and retains last known readings", async () => {
  const {context,elements}=bannerHarness();
  context.renderActionQueue({actions:[{id:"one",kind:"pr-scan",slot:"PRs",status:"queued",queue_position:1}]});
  context.fetch=async()=>({ok:false,json:async()=>({error:"Storage unavailable"})});
  await context.refreshActionQueue();
  assert.match(elements["action-queue"].textContent,/Storage unavailable/);
  assert.equal(elements["action-queue-open"].textContent,"Actions (unavailable)");
  assert.equal(vm.runInContext("ACTION_UI.jobs.length",context),1);
});

test("queue observes completion after reload and refreshes saved section data without another action", async () => {
  const {context}=bannerHarness();
  const queued={id:"one",kind:"workspace-action",action:"sync",slot:"one",status:"queued"};
  const done={...queued,status:"done",message:"Sync complete",steps:[]};
  context.renderActionQueue({actions:[queued]});
  let reloads=0,sections=0;
  context.wsReload=async()=>{ reloads++; };
  context.buildSections=()=>{ sections++; };
  context.fetch=async url=>{
    assert.equal(url,"/api/actions");
    return {ok:true,json:async()=>({actions:[done]})};
  };
  await context.refreshActionQueue();
  assert.equal(reloads,1);
  assert.equal(sections,1);
  assert.equal(vm.runInContext("ACTION_UI.jobs[0].status",context),"done");
});

test("PLDM polling treats queued as pending", () => {
  const polling=block("async function pollPldm(", 'document.addEventListener("click",e=>{');
  assert.match(polling,/const pending=\["queued","running"\]\.includes\(j\.status\)/);
  assert.match(polling,/if\(!pending\)/);
  assert.match(polling,/Waiting in action queue/);
});

function workspaceEditHarness({holder=null, mirror=null, served=true, result={ok:true}, failure=null,
  untrack=false}={}){
  const workspace={slot:"slot / one",label:'A "workspace"',badge:"ONE",
    group:"dev",rev:"main",path:"/synthetic/one",status:{mirror}};
  const controls=Object.fromEntries(["label","badge","group","rev"].map(key=>[key,{
    value:workspace[key],disabled:key==="rev"&&!!(holder||mirror),focus(){},
  }]));
  controls.force={checked:false,focus(){}};
  const handlers={}, requests=[];
  const save={disabled:false}, message={textContent:""}, cancel={addEventListener(){},focus(){}};
  const form={elements:{namedItem:key=>controls[key]},reportValidity:()=>true,
    addEventListener:(event,handler)=>{ handlers[event]=handler; }};
  const overlay={innerHTML:"",removed:false,
    querySelector:selector=>({"form":form,".ws-modal-ok":save,
      ".ws-modal-msg":message,".ws-modal-cancel":cancel}[selector]),
    addEventListener(){},remove(){ this.removed=true; }};
  let appended=false;
  const context=vm.createContext({
    SERVED:served,wsRoster:()=>[workspace],wsHolder:()=>holder,
    wsGroups:()=>[{id:"dev",name:"Development"}],
    esc:value=>String(value).replaceAll('"',"&quot;"),encodeURIComponent,
    alert:()=>assert.fail("Unexpected alert"),
    document:{activeElement:{focus(){}},createElement:()=>overlay,
      body:{appendChild(){ appended=true; }}},
    wsRosterPost:async (url,payload)=>{
      requests.push({url,payload});
      if(failure) throw new Error(failure);
      return result;
    },
  });
  vm.runInContext(block("function wsUntrackDialog(", '// Modal for "+ Track workspace"'),context);
  if(untrack) context.wsUntrackDialog(workspace.slot);
  else context.wsEditDialog(workspace.slot);
  return {overlay,controls,requests,message,save,appended,
    submit:()=>handlers.submit({preventDefault(){}})};
}

test("existing workspace editor prefills and escapes fields and sends only changed metadata", async () => {
  const {overlay,controls,requests,submit}=workspaceEditHarness();
  assert.match(overlay.innerHTML,/value="A &quot;workspace&quot;"/);
  assert.match(overlay.innerHTML,/value="dev" selected/);
  assert.match(overlay.innerHTML,/aria-modal="true"/);
  controls.label.value="New label";
  controls.badge.value="NEW";
  controls.group.value="";
  controls.rev.value="release";
  await submit();
  assert.equal(overlay.removed,true);
  assert.equal(requests[0].url,"/api/ws-meta/slot%20%2F%20one");
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].payload)),
    {label:"New label",badge:"NEW",group:"",rev:"release"});
  assert.match(block("function wsCard(", "// Per-card group picker"),/class="ws-mini ws-edit"/);
  assert.match(source,/\.ws-edit"\)\.forEach.*wsEditDialog\(b\.dataset\.slot\)/);
});

test("workspace editor locks held or mirrored revisions but allows presentation edits", async () => {
  for(const options of [{holder:{type:"task"}},{mirror:{state:"active"}}]){
    const {overlay,controls,requests,submit}=workspaceEditHarness(options);
    assert.match(overlay.innerHTML,/name="rev"[^>]*disabled/);
    assert.match(overlay.innerHTML,/Free the workspace or release its mirror/);
    controls.rev.value="ignored";
    controls.label.value="Updated label";
    await submit();
    assert.deepEqual(JSON.parse(JSON.stringify(requests[0].payload)),{label:"Updated label"});
  }
});

test("workspace metadata editor preserves edits after server or network failures", async () => {
  for(const options of [{result:null},{failure:"Network unavailable"}]){
    const {overlay,controls,requests,message,save,submit}=workspaceEditHarness(options);
    controls.badge.value="EDIT";
    await submit();
    assert.equal(requests.length,1);
    assert.equal(overlay.removed,false);
    assert.equal(controls.badge.value,"EDIT");
    assert.equal(save.disabled,false);
    assert.match(message.textContent,/Save failed/);
  }
});

test("unchanged workspace form makes no write and static exports cannot open editor", async () => {
  const harness=workspaceEditHarness();
  await harness.submit();
  assert.equal(harness.requests.length,0);
  assert.equal(harness.overlay.removed,true);
  assert.equal(workspaceEditHarness({served:false}).appended,false);
});

test("untrack dialog requires an explicit unchecked-by-default override", async () => {
  for(const force of [false,true]){
    const h=workspaceEditHarness({untrack:true,holder:{type:"task"},mirror:{state:"active"}});
    assert.match(h.overlay.innerHTML,/aria-modal="true"/);
    assert.match(h.overlay.innerHTML,/aria-describedby="ws-untrack-warning"/);
    assert.match(h.overlay.innerHTML,/Task holds, mirror state and history remain intact/);
    assert.doesNotMatch(h.overlay.innerHTML,/<input[^>]*checked/);
    assert.equal(h.controls.force.checked,false);
    h.controls.force.checked=force;
    await h.submit();
    assert.equal(h.overlay.removed,true);
    assert.equal(h.requests[0].url,"/api/ws-untrack/slot%20%2F%20one");
    assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].payload)),{force});
  }
  assert.match(source,/\.ws-untrack"\)\.forEach.*wsUntrackDialog\(b\.dataset\.slot\)/);
  assert.equal(workspaceEditHarness({untrack:true,served:false}).appended,false);
});

test("untrack failures keep the dialog open without automatically forcing a retry", async () => {
  for(const options of [{result:null},{failure:"Network unavailable"}]){
    const h=workspaceEditHarness({...options,untrack:true});
    await h.submit();
    assert.equal(h.requests.length,1);
    assert.equal(h.requests[0].payload.force,false);
    assert.equal(h.overlay.removed,false);
    assert.equal(h.controls.force.checked,false);
    assert.equal(h.save.disabled,false);
    assert.match(h.message.textContent,/Untracking failed/);
  }
});

function workspaceReturnHarness({blocked=false,served=true,result={status:"done"},failure=false,action="free"}={}){
  const workspace={slot:"slot / one",label:"Test workspace",path:"/synthetic/slot",
    target:"remote",rev:"release"};
  const job={id:"approval-1",action,slot:workspace.slot,approval:{revision:"release",
    repos:[{path:"/repo/<one>",branch:"dev/feature",revision:"release",
      reasons:["uncommitted or untracked changes"]}],
    blockers:blocked?["target branch has local-only commits"]:[]}};
  const overlays=[], requests=[];
  let restoredFocus=false;
  const context=vm.createContext({
    SERVED:served,wsRoster:()=>[workspace],encodeURIComponent,
    esc:value=>String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;")
      .replaceAll('"',"&quot;"),
    alert:()=>assert.fail("Unexpected alert"),
    document:{
      activeElement:{focus(){ restoredFocus=true; }},
      createElement(){
        const handlers={}, cancel={focused:false,addEventListener(event,handler){handlers.cancel=handler;},
          focus(){this.focused=true;}};
        const save={disabled:false}, message={textContent:""};
        const form={addEventListener(event,handler){handlers[event]=handler;},
          querySelectorAll(){return [cancel,save];}};
        const overlay={innerHTML:"",removed:false,
          querySelector:selector=>({"form":form,".ws-modal-ok":save,
            ".ws-modal-msg":message,".ws-modal-cancel":cancel}[selector]),
          addEventListener(event,handler){handlers[event]=handler;},
          remove(){this.removed=true;}};
        overlays.push({overlay,handlers,cancel,save,message});
        return overlay;
      },
      body:{appendChild(){}},
    },
    wsSlotPost:async (slot,payload,msg,accepted)=>{
      requests.push({slot,payload});
      if(failure){ msg.textContent="Workspace action error"; return null; }
      accepted();
      return result;
    },
  });
  vm.runInContext(block("function wsReturnApprovalDialog(", "async function wsReviewApproval("),context);
  context.wsReturnApprovalDialog(workspace.slot,job);
  return {context,workspace,job,overlays,requests,restoredFocus:()=>restoredFocus,
    submit:index=>overlays[index].handlers.submit({preventDefault(){}})};
}

test("return dialog requires a separate explicit confirmation and allows cancellation", () => {
  const h=workspaceReturnHarness();
  const {overlay,cancel,handlers}=h.overlays[0];
  assert.match(overlay.innerHTML,/aria-modal="true"/);
  assert.match(overlay.innerHTML,/\/repo\/&lt;one>/);
  assert.match(overlay.innerHTML,/Ignored files are not stashed/);
  assert.match(overlay.innerHTML,/no hard reset/);
  assert.equal(cancel.focused,true);
  assert.equal(h.requests.length,0);
  handlers.cancel();
  assert.equal(overlay.removed,true);
  assert.equal(h.restoredFocus(),true);
  assert.equal(h.requests.length,0);
  assert.equal(workspaceReturnHarness({served:false}).overlays.length,0);
});

test("return confirmation submits only the saved approval job and closes after acceptance", async () => {
  const h=workspaceReturnHarness();
  await h.submit(0);
  assert.equal(h.overlays[0].overlay.removed,true);
  assert.equal(h.requests.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0])),{
    slot:"slot / one",payload:{action:"free",force:true,approval_job:"approval-1"}});
});

test("return dialog disables confirmation for safety blockers", async () => {
  const h=workspaceReturnHarness({blocked:true});
  assert.match(h.overlays[0].overlay.innerHTML,/Cannot safely return/);
  assert.match(h.overlays[0].overlay.innerHTML,/ws-modal-ok" disabled/);
  await h.submit(0);
  assert.equal(h.requests.length,0);
  assert.equal(h.overlays[0].overlay.removed,false);
});

test("return dialog preserves errors and prompts again for a changed workspace", async () => {
  const failure=workspaceReturnHarness({failure:true});
  await failure.submit(0);
  assert.equal(failure.overlays[0].overlay.removed,false);
  assert.equal(failure.overlays[0].save.disabled,false);
  assert.match(failure.overlays[0].message.textContent,/error/);

  const next={id:"approval-2",status:"approval_required",approval:{
    revision:"release",repos:[],blockers:[]}};
  const stale=workspaceReturnHarness({result:next});
  await stale.submit(0);
  assert.equal(stale.overlays.length,2);
  assert.equal(stale.overlays[0].overlay.removed,true);
  assert.equal(stale.overlays[1].overlay.removed,false);
  assert.equal(stale.requests.length,1);
});

test("workspace free buttons and saved queue approvals open the confirmation flow", () => {
  const render=block("function renderWorkspaces()", "// ---- column toggles");
  assert.match(render,/job\?\.status==="approval_required"/);
  assert.match(render,/wsReturnApprovalDialog\(b\.dataset\.slot,job\)/);
  assert.match(source,/data-workspace-approval/);
  assert.match(source,/wsReviewApproval\(approvalId\)/);
});

test("Force sync dialog submits a sync approval instead of freeing the workspace", async () => {
  const h=workspaceReturnHarness({action:"sync"});
  assert.match(h.overlays[0].overlay.innerHTML,/Force sync Test workspace/);
  assert.match(h.overlays[0].overlay.innerHTML,/free and independent/);
  assert.match(h.overlays[0].overlay.innerHTML,/does not occupy or release/);
  assert.equal(h.requests.length,0);
  await h.submit(0);
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].payload)),{
    action:"sync",force:true,approval_job:"approval-1"});
});

test("Sync opens Force sync confirmation only for workspace approval results", async () => {
  for(const status of ["approval_required","done","error"]){
    const prompts=[],alerts=[];
    const context=vm.createContext({
      SERVED:true,encodeURIComponent,
      fetch:async()=>({ok:true,json:async()=>({align_job:"sync-job"})}),
      pollAlign:async()=>({id:"sync-job",action:"sync",status}),
      wsReload:async()=>{},wsReturnApprovalDialog:(slot,job)=>prompts.push({slot,job}),
      alert:message=>alerts.push(message),
    });
    vm.runInContext(block("async function wsSyncSlot(", "async function wsReload("),context);
    await context.wsSyncSlot("one");
    assert.equal(prompts.length,status==="approval_required"?1:0);
    assert.deepEqual(alerts,[]);
  }
});

test("workspace action posts an approval job and returns its terminal result", async () => {
  const requests=[], messages=[], job={id:"new-job",status:"approval_required"};
  let accepted=0, reloads=0;
  const context=vm.createContext({
    SERVED:true,encodeURIComponent,
    fetch:async (url,options)=>{
      requests.push({url,payload:JSON.parse(options.body)});
      return {ok:true,json:async()=>({align_job:"new-job"})};
    },
    pollAlign:async id=>{assert.equal(id,"new-job");return job;},
    wsReload:async()=>{reloads++;},alert:message=>messages.push(message),
  });
  vm.runInContext(block("async function wsSlotPost(", "async function wsSyncSlot("),context);
  const msg={textContent:""};
  const result=await context.wsSlotPost("slot / one",
    {action:"free",force:true,approval_job:"prior"},msg,()=>accepted++);
  assert.equal(result,job);
  assert.equal(accepted,1);
  assert.equal(reloads,1);
  assert.deepEqual(requests,[{url:"/api/workspace/slot%20%2F%20one",
    payload:{action:"free",force:true,approval_job:"prior"}}]);
  assert.deepEqual(messages,[]);
});

test("workspace action keeps failed submissions visible without accepting them", async () => {
  let accepted=0, polled=0;
  const context=vm.createContext({
    SERVED:true,encodeURIComponent,
    fetch:async()=>({ok:false,status:409,json:async()=>({error:"approval expired"})}),
    pollAlign:async()=>{polled++;},wsReload:async()=>{},
  });
  vm.runInContext(block("async function wsSlotPost(", "async function wsSyncSlot("),context);
  const msg={textContent:""};
  assert.equal(await context.wsSlotPost("one",{action:"free"},msg,()=>accepted++),null);
  assert.equal(accepted,0);
  assert.equal(polled,0);
  assert.match(msg.textContent,/approval expired/);
});
