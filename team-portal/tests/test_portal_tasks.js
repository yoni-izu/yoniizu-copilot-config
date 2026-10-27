const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "portal.js"), "utf8");
const template = fs.readFileSync(path.join(__dirname, "..", "portal.py"), "utf8");
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
    checkedVals: group => filters[group] || [],
    document: {getElementById: id => elements[id]},
  });
  vm.runInContext(
    block("function prs(", "// ---- workspace alignment") +
    block("function applyFilters()", "function sortTasks(") +
    block("function renderStats()", "// ---- events ----"), context);
  const ids = () => Array.from(context.applyFilters(), task => task.id);
  return {context, filters, elements, tasks, ids};
}

test("unified view includes every category and preserves hide-finished default", () => {
  const {ids, elements} = harness();
  assert.deepEqual(ids(), ["T-001", "T-002", "T-003", "T-006", "T-007"]);
  elements["o-hidedone"].checked = false;
  assert.deepEqual(ids(), ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006", "T-007"]);
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
  assert.match(source, /getElementById\("review-scan"\)\.style\.display = SERVED \? "" : "none"/);
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
  assert.deepEqual(rendered, ["workspaces"]);
  assert.match(note.textContent, /live status not checked/);
  assert.equal(button.disabled, false);
});

test("workspace top Refresh reports saved-data failures and keeps previous readings", async () => {
  const saved = {roster: []}, note = {textContent: ""};
  const context = vm.createContext({
    SERVED: true, activeSection: "ws", WORKSPACES: saved,
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
