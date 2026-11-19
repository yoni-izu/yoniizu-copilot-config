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
const pulseSource = fs.readFileSync(path.join(__dirname, "..", "assets", "pulse.js"), "utf8");
const Pulse = require(path.join(__dirname, "..", "assets", "pulse.js"));
function installDateHelpers(context){
  vm.runInContext(pulseSource,context);
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
    AI_USAGE: {}, ACTION_UI: {jobs:[]},
  });
  installDateHelpers(context);
  vm.runInContext(block("function daysSince(", "// ---- workspace alignment")+
    block("function feature(", "// ---- build filter dropdowns")+
    block("function reviewDraftsHtml(", "function wireEditor(")+
    block("function taskHasOpenReview(", "function selectedVisibleTasks("), context);
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

test("review task renders one task-level report button with every reviewed head and earlier passes", () => {
  const context=draftHarness();
  const task={id:"T-001",created:"2026-10-01",updated:"2026-10-02T09:10:00Z",
    due:"2026-10-31",completed:"2026-10-03",
    review_drafts:[
      {id:"old",kind:"report",updated_at:"2026-10-27",heads:[{repo:"one",pr_number:1,head_sha:"a".repeat(40)}]},
      {id:"new",kind:"report",updated_at:"2026-10-28",heads:[
        {repo:"one",pr_number:1,head_sha:"b".repeat(40)},{repo:"two",pr_number:1,head_sha:"c".repeat(40)}]},
    ],
    reviews:[
      {repo:"one",pr_number:1,pr_url:"https://github.com/example/one/pull/1",drafts:[
        {id:"legacy",updated_at:"2026-10-20",head_sha:"d".repeat(40)}]},
      {repo:"two",pr_number:1,pr_url:"https://github.com/example/two/pull/1"},
  ]};
  const html=context.detailRow(task,8);
  assert.match(html,/class="btn review-draft-link" href="\/review-drafts\/T-001\/new"/);
  assert.equal((html.match(/Open review draft/g)||[]).length,1);
  assert.match(html,/one#1 <code>b{40}<\/code>, two#1 <code>c{40}<\/code>/);
  assert.match(html,/Earlier drafts \(2\)/);
  assert.match(html,/\/review-drafts\/T-001\/old/);
  assert.match(html,/Per-PR draft · Saved 20\/10\/2026 · one#1 <code>d{40}<\/code>/);
  assert.match(html,/Saved 28\/10\/2026/);
  assert.match(html,/Created:<\/b> 01\/10\/2026/);
  assert.match(html,/Updated:<\/b> 02\/10\/2026 09:10:00Z/);
  assert.match(html,/Due:<\/b> 31\/10\/2026/);
  assert.match(html,/Completed:<\/b> 03\/10\/2026/);
  assert.ok(html.indexOf("review-drafts")>html.lastIndexOf('class="pr"'));
  assert.equal(task.review_drafts[0].id,"old");
});

test("combined reports are labelled and legacy-only tasks still get a button", () => {
  const html=draftHarness().reviewDraftsHtml({id:"T-002",
    review_drafts:[{id:"comb",kind:"combined",updated_at:"2026-11-15",heads:[]}],
    reviews:[{repo:"one",pr_number:2,drafts:[{id:"x",updated_at:"2026-10-01",head_sha:"e".repeat(40)}]}]});
  assert.match(html,/href="\/review-drafts\/T-002\/comb"[^]*Combined from per-PR drafts/);
  const legacyOnly=draftHarness().reviewDraftsHtml({id:"T-003",
    reviews:[{repo:"one",pr_number:2,drafts:[{id:"x",updated_at:"2026-10-01",head_sha:"e".repeat(40)}]}]});
  assert.match(legacyOnly,/Open review draft/);
});

test("static draft metadata avoids broken relative links and never embeds contents", () => {
  const context=draftHarness(false);
  const html=context.reviewDraftsHtml({id:"T-001",review_drafts:[
    {id:"one",updated_at:"2026-10-28",heads:[{repo:"r",pr_number:1,head_sha:"a".repeat(40)}],content:"PRIVATE BODY"},
  ]});
  assert.match(html,/open the served portal to read/);
  assert.doesNotMatch(html,/href=|PRIVATE BODY/);
  assert.equal(context.reviewDraftsHtml({id:"T-001"}),"");
});

test("draft metadata is escaped and URL segments are encoded", () => {
  const html=draftHarness().reviewDraftsHtml({id:'T-001" onmouseover="evil',review_drafts:[
    {id:"../bad",updated_at:"<script>",heads:[{repo:"<b>",pr_number:"<i>",head_sha:'"><img src=x>'}]},
  ]});
  assert.doesNotMatch(html,/<script>|<img|<b>|<i>|href="[^"]*" onmouseover/);
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

test("machine rows offer stop only for a running job that still owns its slot", () => {
  const context = vm.createContext({esc: s => String(s)});
  vm.runInContext(block("function vmCancelButton(", "async function vmCancel("), context);
  const row = {id: "j1", status: "running", pid: 7, kind: "build", tag: "ws", ws_label: "one/fw", target: "lab1"};
  assert.match(context.vmCancelButton(row), /class="vm-cancel" data-id="j1" data-label="build one\/fw on lab1"/);
  for (const off of [{status: "passed"}, {owns_slot: false}, {pid: null}, {kind: "mirror"}])
    assert.equal(context.vmCancelButton({...row, ...off}), "", JSON.stringify(off));
  assert.match(source, /cancelled:"stopped from the portal/);
});

function machineMenuHarness() {
  const context = vm.createContext({
    SERVED: true, URL,
    prefs: {vmHidden: [], vmPanelsHidden: [], machineTarget: ""},
    esc: s => String(s ?? "").replace(/[&<>"]/g, c =>
      ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])),
    VMLOG: {target_state: [], machines: [], terminal_targets: ["vm", "other", "desk"], live: {}},
    setTimeout: () => 0, clearTimeout: () => {},
    fetch: async () => ({ok: true, json: async () => ({logins: {}})}),
  });
  vm.runInContext(block("function vmStale(", "function vmTargetStrip("), context);
  vm.runInContext(block("function vmTargetStrip(", "// State of the persistent"), context);
  vm.runInContext(block("function vmLiveBadge(", "async function vmPower("), context);
  vm.runInContext(block("const VMSSO=", "async function vmSetMain("), context);
  return context;
}

test("SSO login is offered on tracked targets and disabled on a stopped VM", () => {
  const context = machineMenuHarness();
  const running = context.vmMachine({name: "Main", id: "i-main", type: "m7i", state: "running", target: "vm", main: true});
  assert.match(running, /<option value="sso">SSO login<\/option>/);
  const stopped = context.vmMachine({name: "Other", id: "i-other", type: "m7i", state: "stopped", target: "other"});
  assert.match(stopped, /<option value="sso" disabled>SSO login \(VM stopped\)<\/option>/);
  const untracked = context.vmMachine({name: "Loose", id: "i-loose", type: "m7i", state: "running", target: ""});
  assert.doesNotMatch(untracked, /value="sso"/);
  context.VMLOG.target_state = [{name: "desk", instance: ""}];
  assert.match(context.vmTargetStrip(), /<option value="sso">SSO login<\/option>/);
  context.SERVED = false;
  assert.match(context.vmActions("desk"), /<option value="sso" disabled>/);
});

test("SSO card status shows code and only opens AWS SSO links", () => {
  const context = machineMenuHarness();
  assert.equal(context.vmSsoOk("https://elmnt.awsapps.com/start/#/device?user_code=ABCD-EFGH"), true);
  assert.equal(context.vmSsoOk("https://device.sso.eu-central-1.amazonaws.com/?user_code=ABCD-EFGH"), true);
  assert.equal(context.vmSsoOk("https://eu-west-1.signin.aws/platform/authorize?request_uri=synthetic"), true);
  for(const bad of ["http://elmnt.awsapps.com/start", "https://evil.com/?x=.awsapps.com",
                    "https://elmnt.awsapps.com.evil.com/", "javascript:alert(1)", "https://u@elmnt.awsapps.com/",
                    "http://eu-west-1.signin.aws/platform/authorize",
                    "https://eu-west-1.signin.aws.evil.com/platform/authorize",
                    "https://evil.signin.aws/platform/authorize", "https://eu-west-1.signin.aws/other",
                    "https://eu-west-1.signin.aws/platform/authorize/extra",
                    "https://u@eu-west-1.signin.aws/platform/authorize",
                    "https://eu-west-1.signin.aws:8443/platform/authorize",
                    "https://eu-west-1.signin.aws:bad/platform/authorize"])
    assert.equal(context.vmSsoOk(bad), false, bad);
  vm.runInContext("VMSSO", context).logins.vm = {target: "vm", state: "waiting", message: "Waiting", code: "ABCD-EFGH",
                             url: "https://elmnt.awsapps.com/start/#/device?user_code=ABCD-EFGH"};
  const html = context.vmSsoHtml("vm");
  assert.match(html, /ABCD-EFGH/);
  assert.match(html, /href="https:\/\/elmnt\.awsapps\.com\/start/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /class="vm-sso-cancel btn" data-target="vm"/);
  vm.runInContext("VMSSO", context).logins.vm.url = "https://evil.example/";
  assert.doesNotMatch(context.vmSsoHtml("vm"), /evil|href=/);
  vm.runInContext("VMSSO", context).logins.desk = {target: "desk", state: "done", message: "Logged in"};
  context.VMLOG.target_state = [{name: "desk", instance: ""}];
  assert.match(context.vmSsoHtml("desk"), /share one home/);
  assert.doesNotMatch(context.vmSsoHtml("desk"), /Cancel/);
  assert.equal(context.vmSsoHtml("nobody"), "");
});

test("SSO pop-up is pointed at the safe link or closed on failure", () => {
  const context = machineMenuHarness();
  context.document = {querySelectorAll: () => []};
  context.setTimeout = () => 1; context.clearTimeout = () => {};
  const win = () => ({closed: false, opener: {}, replaced: null, shut: false,
    location: {replace(u) { win.last.replaced = u; }}, close() { this.shut = true; }});
  let w = win.last = win();
  vm.runInContext("VMSSO", context).wins.vm = w;
  context.vmSsoAdopt({vm: {state: "starting"}});
  assert.equal(w.replaced, null);
  assert.ok(vm.runInContext("VMSSO", context).wins.vm);
  context.vmSsoAdopt({vm: {state: "waiting", url: "https://evil.example/"}});
  assert.equal(w.replaced, null);
  context.vmSsoAdopt({vm: {state: "waiting", url: "https://elmnt.awsapps.com/start/#/device"}});
  assert.equal(w.replaced, "https://elmnt.awsapps.com/start/#/device");
  assert.equal(w.opener, null);
  assert.equal(vm.runInContext("VMSSO", context).wins.vm, undefined);
  w = win.last = win();
  vm.runInContext("VMSSO", context).wins.vm = w;
  const signin = "https://eu-west-1.signin.aws/platform/authorize?request_uri=synthetic";
  context.vmSsoAdopt({vm: {state: "waiting", url: signin}});
  assert.equal(w.replaced, signin);
  assert.equal(w.opener, null);
  assert.equal(vm.runInContext("VMSSO", context).wins.vm, undefined);
  w = win.last = win();
  vm.runInContext("VMSSO", context).wins.desk = w;
  context.vmSsoAdopt({desk: {state: "failed", message: "VM is stopped"}});
  assert.equal(w.shut, true);
  assert.equal(vm.runInContext("VMSSO", context).wins.desk, undefined);
});

test("native machine action menus retain quick controls and protect the main target", () => {
  const context = machineMenuHarness();
  const main = {name: "Main", id: "i-main", type: "m7i", state: "running", target: "vm", main: true};
  const other = {name: "Other", id: "i-other", type: "m7i", state: "stopped", target: "other"};
  const mainHtml = context.vmMachine(main);
  const mainMenu = mainHtml.match(/<select class="vm-actions"[^]*?<\/select>/)[0];
  assert.match(mainMenu, /aria-label="Actions for vm"/);
  assert.match(mainMenu, /<option value="provision">Provision<\/option>/);
  assert.doesNotMatch(mainMenu, /value="main"|value="remove"|<button/);
  const mainQuick = mainHtml.replace(mainMenu, "");
  assert.match(mainQuick, /class="vm-term" data-target="vm"/);
  assert.match(mainQuick, /class="vm-trf" data-target="vm"/);
  assert.match(mainQuick, /class="vm-pw stop"/);

  const otherHtml = context.vmMachine(other);
  const otherMenu = otherHtml.match(/<select class="vm-actions"[^]*?<\/select>/)[0];
  assert.match(otherMenu, /value="provision"/);
  assert.match(otherMenu, /value="main"/);
  assert.match(otherMenu, /value="remove"/);
  const otherQuick = otherHtml.replace(otherMenu, "");
  assert.match(otherQuick, /class="vm-term" data-target="other" disabled/);
  assert.match(otherQuick, /class="vm-pw start"/);
  assert.match(context.vmMachine({...other, state: "pending"}), /class="vm-pw start"[^>]* disabled/);

  const unbound = context.vmMachine({...other, target: ""});
  assert.match(unbound, /value="main"/);
  assert.match(unbound, /value="add"/);
  assert.doesNotMatch(unbound, /value="provision"|value="remove"/);
  assert.match(mainHtml, /<option value="">\u22ef<\/option>|<option value="">⋯<\/option>/);
  assert.match(mainHtml, /class="vm-fold" data-vm-fold="i-main" aria-expanded="true" aria-label="Collapse Main"/);
  assert.match(mainHtml, /<dt>Instance<\/dt><dd>i-main · m7i<\/dd>/);
  context.SERVED = false;
  assert.match(context.vmMachine(main), /<select class="vm-actions"[^>]* disabled>/);
  assert.match(context.vmMachine(main), /<option value="provision" disabled>/);
  assert.doesNotMatch(context.vmMachine(main), /value="hide"/);
  context.prefs.vmHidden = ["i-main"];
  const folded = context.vmMachine(main);
  assert.match(folded, /class="vm-card main folded"/);
  assert.match(folded, /aria-expanded="false" aria-label="Expand Main"/);
  assert.doesNotMatch(folded, /vm-actions|vm-kv|vm-pw/);
});

test("lab targets offer native menus and escape labels", () => {
  const context = machineMenuHarness();
  context.VMLOG.machines = [{id: "i-main"}];
  context.VMLOG.target_state = [
    {name: "vm", instance: "i-main"},
    {name: "desk", instance: ""},
    {name: "retired", instance: "i-gone"},
  ];
  const html = context.vmTargetStrip();
  const menus = Array.from(html.matchAll(/<select class="vm-actions"[^]*?<\/select>/g), m => m[0]);
  assert.equal(menus.length, 1);
  assert.match(menus[0], /data-target="desk"/);
  assert.match(menus[0], /value="provision"/);
  assert.match(menus[0], /value="remove"/);
  assert.match(html, /<option value="retired">retired \(VM target\)<\/option>/);
  assert.doesNotMatch(menus.join(""), /<button/);
  assert.match(html, /class="vm-trf" data-target="desk"/);
  assert.doesNotMatch(html, /<b>vm<\/b>/);
  const escaped = context.vmActions('a"<&');
  assert.match(escaped, /data-target="a&quot;&lt;&amp;"/);
  assert.match(escaped, /aria-label="Actions for a&quot;&lt;&amp;"/);
  context.prefs.machineTarget = "retired";
  const selected = context.vmTargetStrip();
  assert.match(selected, /<option value="retired" selected>/);
  assert.match(selected, /class="vm-actions"[^>]*data-target="retired"/);
  assert.doesNotMatch(selected, /class="vm-actions"[^>]*data-target="desk"/);
  context.prefs.machineTarget = "no-longer-registered";
  assert.match(context.vmTargetStrip(), /<option value="desk" selected>/);
});

test("lab machine cards offer a BMC terminal and Set BMC; VMs do not", () => {
  const context = machineMenuHarness();
  context.VMLOG.target_state = [{name: "desk", instance: "", bmc: "bmc-sa-els23-csr"}];
  const html = context.vmTargetStrip();
  assert.match(html, /<button class="vm-bmc" data-target="desk" title="[^"]*bmc-sa-els23-csr[^"]*">&gt;_ BMC<\/button>/);
  assert.match(html, /<dt>BMC<\/dt><dd>bmc-sa-els23-csr<\/dd>/);
  assert.match(html, /<option value="bmc">Set BMC\u2026<\/option>|<option value="bmc">Set BMC…<\/option>/);
  context.VMLOG.target_state = [{name: "desk", instance: ""}];
  const none = context.vmTargetStrip();
  assert.match(none, /class="vm-bmc" data-target="desk" disabled title="No BMC set/);
  assert.doesNotMatch(none, /<dt>BMC/);
  context.VMLOG.target_state = [{name: "vm-old", instance: "i-gone"}];
  const vmTarget = context.vmTargetStrip();
  assert.doesNotMatch(vmTarget, /vm-bmc|value="bmc"/);
  assert.doesNotMatch(context.vmMachine({name: "Main", id: "i-main", type: "m7i", state: "running", target: "vm", main: true}),
                      /vm-bmc|value="bmc"/);
  const opened = [];
  context.termOpen = (spec, name) => opened.push([spec, name]);
  const btn = {dataset: {target: "desk"}, addEventListener(e, cb) { this.click = cb; }};
  context.vmBindTerms({querySelectorAll: sel => sel === ".vm-bmc" ? [btn] : []});
  btn.click();
  assert.deepEqual(JSON.parse(JSON.stringify(opened)), [[{kind: "bmc", target: "desk"}, "bmc-desk"]]);
});

test("Set BMC prompts, sends the trimmed host or clears it, and skips no-ops", async () => {
  const context = machineMenuHarness();
  vm.runInContext(block("async function vmTargetPost(", "async function vmRemoveTarget("), context);
  const posts = [], note = {textContent: ""};
  context.document = {getElementById: () => note};
  context.VMLOG.target_state = [{name: "desk", bmc: "bmc-old"}];
  context.vmTargetPost = async body => posts.push(body);
  for(const answer of [null, "bmc-old", "  root@bmc-new ", ""]){
    context.prompt = () => answer;
    await context.vmSetBmc("desk");
  }
  assert.deepEqual(JSON.parse(JSON.stringify(posts)),
    [{action: "bmc", name: "desk", bmc: "root@bmc-new"}, {action: "bmc", name: "desk", bmc: ""}]);
  context.vmTargetPost = async () => { throw new Error("bad host"); };
  context.prompt = () => "x";
  await context.vmSetBmc("desk");
  assert.equal(note.textContent, "Set BMC failed: bad host");
  context.SERVED = false;
  context.prompt = () => { throw new Error("must not prompt"); };
  await context.vmSetBmc("desk");
});

test("native menu dispatch resets selection and does nothing in static mode", () => {
  const context = machineMenuHarness();
  const calls = [];
  const select = {
    value: "", dataset: {target: "desk", id: "i-other"},
    addEventListener(event, callback) { assert.equal(event, "change"); this.change = callback; },
  };
  context.vmProvision = target => calls.push(["provision", target]);
  context.vmSetMain = id => calls.push(["main", id]);
  context.vmRemoveTarget = target => calls.push(["remove", target]);
  context.vmAddDialog = id => calls.push(["add", id]);
  context.vmSsoStart = target => calls.push(["sso", target]);
  context.vmSetBmc = target => calls.push(["bmc", target]);
  context.vmBindActions({querySelectorAll: sel => sel === ".vm-actions" ? [select] : []});
  for(const action of ["provision", "main", "remove", "add", "sso", "bmc", ""]){
    select.value = action;
    select.change();
    assert.equal(select.value, "");
  }
  assert.deepEqual(calls, [["provision", "desk"], ["main", "i-other"], ["remove", "desk"], ["add", "i-other"], ["sso", "desk"], ["bmc", "desk"]]);
  context.SERVED = false;
  select.value = "provision";
  select.change();
  assert.equal(calls.length, 6);
  assert.equal(select.value, "");
});

test("machine rendering binds action menus with or without EC2 cards", () => {
  const context = machineMenuHarness(), calls = [];
  const menu = {
    dataset: {target: "other", id: "i-other"}, value: "", events: {},
    addEventListener(event, callback) { this.events[event] = callback; },
  };
  const box = {
    innerHTML: "",
    querySelector: () => ({addEventListener() {}, focus() {}}),
    querySelectorAll(selector) {
      return selector === ".vm-actions" ? [menu] : [];
    },
  };
  context.document = {getElementById: () => box};
  context.SERVED = true;
  context.confirm = () => true;
  context.vmProvision = target => calls.push({target});
  context.vmSetMain = id => calls.push({main: id});
  context.gfDash = () => "";
  context.VMLOG.machines = [
    {name: "Other", id: "i-other", type: "test", state: "running", target: "other"},
  ];
  context.VMLOG.target_state = [{name: "other", instance: "i-other"}];
  context.renderVmMachines();
  assert.match(box.innerHTML, /data-target="other" data-id="i-other"/);
  menu.value = "provision";
  menu.events.change();
  menu.value = "main";
  menu.events.change();
  assert.equal(calls[0].target, "other");
  assert.equal(calls[1].main, "i-other");
  context.renderVmMachines();
  assert.match(box.innerHTML, /<select class="vm-actions" title="More actions"/);

  context.VMLOG.machines = [];
  context.VMLOG.target_state = [{name: "desk", instance: ""}];
  menu.dataset.target = "desk";
  context.renderVmMachines();
  assert.match(box.innerHTML, /data-target="desk"/);
  menu.value = "provision";
  menu.events.change();
  assert.equal(calls[2].target, "desk");
});

test("provision still requires confirmation and is unavailable in static mode", () => {
  const context = machineMenuHarness();
  const note = {textContent: ""}, calls = [], prompts = [];
  let approved = false;
  context.SERVED = true;
  context.document = {getElementById: () => note};
  context.confirm = message => { prompts.push(message); return approved; };
  context.termOpen = (spec, name) => calls.push({spec, name});
  context.vmProvision("desk");
  assert.equal(calls.length, 0);
  assert.match(prompts[0], /stopped VM is started first/);
  approved = true;
  context.vmProvision("desk");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].spec.kind, "provision");
  assert.equal(calls[0].spec.target, "desk");
  assert.equal(calls[0].name, "prov-desk");
  context.SERVED = false;
  context.vmProvision("desk");
  assert.equal(calls.length, 1);
  assert.equal(prompts.length, 2);
  assert.equal(note.textContent, "Serve the portal to provision");
});

test("card fold arrows and the machine selector retain preferences across renders", () => {
  const context = machineMenuHarness();
  const control = dataset => ({dataset, events: {}, focus() { this.focused = true; },
    addEventListener(event, callback) { this.events[event] = callback; }});
  const folds = {}, picker = control({});
  const fold = key => (folds[key] ||= control({vmFold: key}));
  const box = {
    innerHTML: "",
    querySelector: selector => {
      const m = selector.match(/data-vm-fold="([^"]+)"/);
      if(m) return fold(m[1]);
      return selector === "#vm-machine-pick" && /id="vm-machine-pick"/.test(box.innerHTML) ? picker : null;
    },
    querySelectorAll: selector => selector === "[data-vm-fold]"
      ? Array.from(box.innerHTML.matchAll(/data-vm-fold="([^"]+)"/g), m => fold(m[1])) : [],
  };
  context.document = {getElementById: () => box};
  context.gfDash = () => "";
  context.savePrefs = changes => Object.assign(context.prefs, changes);
  context.VMLOG.machines = [
    {id:"i-11111111",name:"One",state:"running"},
    {id:"i-22222222",name:"Two",state:"stopped"},
  ];
  context.VMLOG.target_state = [{name:"desk-a"},{name:"desk-b",error:"SSH refused"}];
  context.VMLOG.terminal_targets = ["desk-a", "desk-b"];
  context.prefs.vmHidden = ["i-11111111"];
  context.renderVmMachines();
  assert.equal((box.innerHTML.match(/<article class="vm-card/g)||[]).length, 3);
  assert.equal((box.innerHTML.match(/<article class="vm-card folded"/g)||[]).length, 1);
  assert.doesNotMatch(box.innerHTML, /Hidden VMs|vm-restore|data-vm-panel/);
  assert.match(box.innerHTML, /<select id="vm-machine-pick" class="vm-pick"/);
  assert.match(box.innerHTML, /<option value="desk-a" selected>/);
  picker.events.change({target:{value:"desk-b"}});
  assert.equal(context.prefs.machineTarget, "desk-b");
  assert.match(box.innerHTML, /SSH refused/);
  assert.match(box.innerHTML, /class="vm-term" data-target="desk-b"/);
  fold("i-11111111").events.click();
  assert.deepEqual([...context.prefs.vmHidden], []);
  assert.equal(fold("i-11111111").focused, true);
  fold("i-22222222").events.click();
  assert.deepEqual([...context.prefs.vmHidden], ["i-22222222"]);
  fold("machine-card").events.click();
  assert.deepEqual([...context.prefs.vmPanelsHidden], ["machine-card"]);
  assert.doesNotMatch(box.innerHTML, /SSH refused/);
  fold("vms").events.click();
  fold("machines").events.click();
  assert.deepEqual([...context.prefs.vmPanelsHidden], ["machine-card", "vms", "machines"]);
  assert.doesNotMatch(box.innerHTML, /<article class="vm-card/);
  assert.match(box.innerHTML, /data-vm-fold="vms" aria-expanded="false"/);
  context.VMLOG.target_state = [{name:"desk-a"}];
  context.prefs.vmPanelsHidden = [];
  context.renderVmMachines();
  assert.doesNotMatch(box.innerHTML, /vm-machine-pick/);
  assert.match(box.innerHTML, /<h4>desk-a<\/h4>/);
});

test("set as main still requires confirmation and sends the selected instance", async () => {
  const context = machineMenuHarness();
  const note = {textContent: ""}, button = {disabled: false, textContent: ""}, requests = [];
  let approved = false;
  context.SERVED = true;
  context.document = {getElementById: () => note, querySelectorAll: () => [button]};
  context.confirm = () => approved;
  context.fetch = async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => ({machines: [], jobs: []})};
  };
  context.vmAdoptPayload = () => {};
  context.renderVmlog = () => {};
  context.buildSections = () => {};
  vm.runInContext(block("async function vmSetMain(", "function vmRow("), context);
  await context.vmSetMain("i-other", button);
  assert.equal(requests.length, 0);
  approved = true;
  await context.vmSetMain("i-other", button);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/vm-main");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {id: "i-other"});
  context.SERVED = false;
  await context.vmSetMain("i-other", button);
  assert.equal(requests.length, 1);
  assert.equal(note.textContent, "Serve the portal to change the Main VM");
});

test("target mutations adopt the full payload, preserve errors, and confirm removal", async () => {
  const context = machineMenuHarness(), requests = [], adopted = [];
  const note = {textContent: ""};
  context.document = {getElementById: () => note};
  context.vmAdoptPayload = data => adopted.push(data);
  context.renderVmlog = () => {};
  context.buildSections = () => {};
  let approved = false, ok = true;
  context.confirm = () => approved;
  context.fetch = async (url, opts) => {
    requests.push({url, body: JSON.parse(opts.body)});
    return {ok, json: async () => ok ? {targets: ["vm"], jobs: [], message: "Removed"} : {error: "running jobs"}};
  };
  vm.runInContext(block("async function vmTargetPost(", "function vmRow("), context);
  await context.vmRemoveTarget("desk");
  assert.equal(requests.length, 0);
  approved = true;
  await context.vmRemoveTarget("desk");
  assert.deepEqual(requests[0], {url: "/api/vm-target", body: {action: "remove", name: "desk"}});
  assert.equal(adopted.length, 1);
  assert.equal(note.textContent, "Removed");
  ok = false;
  await context.vmRemoveTarget("desk");
  assert.match(note.textContent, /Remove tracking failed: running jobs/);
  assert.equal(adopted.length, 1);
  context.SERVED = false;
  await context.vmRemoveTarget("desk");
  assert.equal(requests.length, 2);
});

test("machine cards mark the Main VM and offer Set as main on the others", () => {
  const context = vm.createContext({
    SERVED: true,
    esc: s => String(s), VMLOG: {target_state: [], machines: [], terminal_targets: [], live: {}},
  });
  vm.runInContext(block("function vmStale(", "function vmTargetStrip("), context);
  vm.runInContext(block("function vmTermBtn(", "function vmBindTerms("), context);
  vm.runInContext(block("function vmTargetBtn(", "function vmBindTargetRefresh("), context);
  vm.runInContext(block("function vmProvBadge(", "// A shell on the machine"), context);
  vm.runInContext(block("function vmMachine(", "async function vmPower("), context);
  vm.runInContext(block("const VMSSO=", "async function vmSetMain("), context);
  context.prefs = {vmHidden: [], vmPanelsHidden: []};
  const m = {name: "me-personal", id: "i-012e7b37b8bfe283b", type: "m7i", state: "running", target: "vm"};
  const main = context.vmMachine({...m, main: true});
  assert.match(main, /class="vm-card main"/);
  assert.match(main, /class="vm-main"/);
  assert.doesNotMatch(main, /value="main"/);
  const other = context.vmMachine({...m, id: "i-0ebef8d9fb0fc8d84", target: "vm-0ebef8", main: false});
  assert.match(other, /value="main">Set as main/);
  assert.match(other, /i-0ebef8d9fb0fc8d84 \u00b7 m7i|i-0ebef8d9fb0fc8d84 · m7i/);
  assert.match(source, /fetch\("\/api\/vm-main"/);
});

test("machine cards show power memory, a predicted stop and provisioning", () => {
  const context = vm.createContext({
    SERVED: true,
    esc: s => String(s),
    VMLOG: {terminal_targets: [], live: {}, machines: [],
            target_state: [{name: "vm", provision: {recipes: [{recipe: "rtl", status: "fail", note: "x"}],
                                                    ok: false, failed: ["rtl"], todo: [], last_age_h: "1h"}}]},
  });
  vm.runInContext(block("function vmStale(", "function vmTargetStrip("), context);
  vm.runInContext(block("function vmTermBtn(", "function vmBindTerms("), context);
  vm.runInContext(block("function vmTargetBtn(", "function vmBindTargetRefresh("), context);
  vm.runInContext(block("function vmProvBadge(", "// A shell on the machine"), context);
  vm.runInContext(block("function vmMachine(", "async function vmPower("), context);
  vm.runInContext(block("const VMSSO=", "async function vmSetMain("), context);
  context.prefs = {vmHidden: [], vmPanelsHidden: []};
  const m = {name: "me", id: "i-012e7b37b8bfe283b", type: "m7i", state: "running", target: "vm",
             started_ago_h: "12m", started_by: "auto", stop_in_h: "18m", stop_ts: 1794928000};
  const card = context.vmMachine(m);
  assert.match(card, /started 12m ago \(auto\)/);
  assert.match(card, /predicted stop .* \(in 18m\)/);
  assert.match(card, /class="vm-prov bad"[^>]*>provision \u26a0 rtl|class="vm-prov bad"[^>]*>provision ⚠ rtl/);
  assert.match(card, /value="provision">Provision/);
  const gone = context.vmMachine({...m, predicted_stopped: true, stopped_ago_h: "5m"});
  assert.match(gone, /lease ended 5m ago/);
  assert.doesNotMatch(gone, /predicted stop/);
  context.VMLOG.target_state = [{name: "vm", provision: {recipes: []}}];
  assert.match(context.vmMachine(m), /unprovisioned/);
  assert.match(source, /termOpen\(\{kind:"provision"/);
});

test("target strip keeps a bound target whose instance octl no longer lists", () => {
  const context = vm.createContext({
    SERVED: true,
    prefs: {machineTarget: "", vmHidden: [], vmPanelsHidden: []},
    esc: s => String(s),
    VMLOG: {machines: [{id: "i-1"}], terminal_targets: [], live: {},
            target_state: [{name: "vm", instance: "i-1"}, {name: "vm-gone", instance: "i-2"},
                           {name: "dsk61", instance: ""}]},
  });
  vm.runInContext(block("function vmStale(", "function renderVmMachines("), context);
  vm.runInContext(block("function vmTargetStrip(", "// State of the persistent"), context);
  vm.runInContext("function vmLiveBadge(){return ''} function vmTermBtn(){return ''} function vmTargetBtn(){return ''} function vmSsoSlot(){return ''} function vmActions(){return ''} function vmBmcBtn(){return ''} function vmFlashBtn(){return ''} function vmFlashLine(){return ''}", context);
  const html = context.vmTargetStrip();
  assert.match(html, /<option value="vm-gone"( selected)?>vm-gone \(VM target\)/);
  assert.match(html, /dsk61/);
  assert.doesNotMatch(html, /<option value="vm"[ >]/);
});

test("Flash FW: lab-only button, running badge, last run line, guarded stop", () => {
  const context = vm.createContext({SERVED: true, esc: s => String(s),
    VMLOG: {jobs: [
      {id: "f1", kind: "flash", target: "srv22", status: "failed", rc: 2, started: "2026-01-01T00:00:00",
       method: "bmc", image: "iridium/t", oams: [1, 3]},
      {id: "f2", kind: "flash", target: "dsk61", status: "running", started: "2026-01-02T00:00:00"}]}});
  vm.runInContext(block("function vmFlashRunning(", "function flashWindow("), context);
  vm.runInContext(block("function vmCancelButton(", "function vmFlashProgressBtn("), context);
  vm.runInContext(block("function vmFlashProgressBtn(", "async function vmCancel("), context);
  assert.equal(context.vmFlashBtn({name: "vm", instance: "i-1"}), "");
  assert.match(context.vmFlashBtn({name: "srv22", instance: ""}), /class="vm-flash" data-target="srv22"/);
  assert.match(context.vmFlashBtn({name: "dsk61", instance: ""}), /vm-flash-open live" data-id="f2"/);
  const line = context.vmFlashLine({name: "srv22"});
  assert.match(line, /bmc iridium\/t \u2192 OAM 1,3/);
  assert.match(line, /not verified/);
  assert.equal(context.vmFlashLine({name: "none"}), "");
  const w = {id: "f2", kind: "flash", target: "dsk61", status: "running", pid: 5, phase: "write", cancellable: false};
  assert.match(context.vmCancelButton(w), /disabled/);
  assert.match(context.vmCancelButton({...w, phase: "upload", cancellable: true}), /class="vm-cancel" data-id="f2"/);
  assert.match(context.vmFlashProgressBtn(w), /vm-flash-open/);
  assert.equal(context.vmFlashProgressBtn({kind: "build"}), "");
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
    "align-title": {textContent: ""}, "align-body": {innerHTML: "", insertAdjacentHTML(pos, html) { this.innerHTML += html; }}};
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
    "align-title": {textContent: ""}, "align-body": {innerHTML: "", insertAdjacentHTML(pos, html) { this.innerHTML += html; }}};
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

function bannerHarness(jobs=[], storage={}) {
  const elements = {
    "align-banner": {style: {}}, "align-spin": {style: {}},
    "align-title": {textContent: ""}, "align-body": {innerHTML: "", hidden: false, insertAdjacentHTML(pos, html) { this.innerHTML += html; }},
    "action-queue": {innerHTML: "", hidden: false},
    "action-queue-open": {textContent: "", hidden: true, dataset: {}, title: "",
      attributes: {}, setAttribute(name, value) { this.attributes[name] = value; }},
    "aq-summary": {textContent: ""}, "aq-spin": {style: {}},
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
    localStorage: {getItem: k => (k in storage ? storage[k] : null),
      setItem: (k, v) => { storage[k] = String(v); }},
  });
  vm.runInContext(block("// ---- workspace alignment", "function feature("), context);
  const target = id => Object.assign(elements[id] || {}, {id,
    closest: sel => (sel === "#" + id ? elements[id] : null)});
  return {context, elements, click: id => click({target: target(id)}),
    toggle() { click({target: Object.assign(elements["align-toggle"], {id: "align-toggle"})}); },
    requests: () => requests, raw: event => click(event)};
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
  assert.notEqual(elements["align-banner"].style.display, "block");
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

test("operation drop-down dismiss remains separate from collapse and never auto-opens", () => {
  const {context, elements, toggle, click}=bannerHarness();
  toggle();
  click("action-queue-open");
  assert.equal(elements["align-banner"].style.display, "block");
  assert.equal(elements["action-queue-open"].attributes["aria-expanded"], "true");
  click("align-close");
  assert.equal(elements["align-banner"].style.display, "none");
  assert.equal(elements["action-queue-open"].attributes["aria-expanded"], "false");
  context.renderAlign({kind: "review-scan", status: "done", repos: []});
  assert.equal(elements["align-banner"].style.display, "none");
  assert.equal(elements["align-body"].hidden, true);
  click("action-queue-open");
  click("somewhere-else");
  assert.equal(elements["align-banner"].style.display, "none");
});

test("actions card summarises one running action, several, waiting and approvals", () => {
  const {context}=bannerHarness();
  const sum=a=>context.actionCardSummary(a);
  assert.deepEqual({...sum([])},{tone:"idle",busy:false,text:"Idle"});
  const one=sum([{kind:"pr-scan",slot:"PRs",status:"running",current:"repo-a"},
    {kind:"pr-scan",slot:"PRs",status:"queued"}]);
  assert.equal(one.text,"Scan PRs · PRs · repo-a · 1 waiting");
  assert.equal(one.busy,true);
  assert.equal(sum([{status:"running",kind:"pr-scan"},{status:"running",kind:"pr-scan"}]).text,"2 running");
  assert.equal(sum([{status:"queued",kind:"pr-scan"}]).text,"1 waiting");
  assert.equal(sum([{status:"approval_required",kind:"pr-scan"}]).tone,"warn");
});

test("cancel offers only where the server can stop the work and posts it", async () => {
  const {context,elements}=bannerHarness();
  const can=job=>context.actionCancellable(job);
  assert.equal(can({status:"queued",kind:"pr-scan"}),true);
  assert.equal(can({status:"approval_required",kind:"workspace-action"}),true);
  assert.equal(can({status:"running",kind:"ai"}),true);
  assert.equal(can({status:"running",kind:"review-chat",op:"turn"}),true);
  assert.equal(can({status:"running",kind:"review-chat",op:"submit"}),false);
  assert.equal(can({status:"running",kind:"pr-scan"}),false);
  assert.equal(can({status:"running",kind:"ai",cancel_requested:"t"}),false);
  assert.equal(can({status:"done",kind:"ai"}),false);
  const queued={id:"q",kind:"pr-scan",slot:"PRs",status:"queued",queue_position:1};
  context.renderAlign(queued);
  assert.match(elements["align-body"].innerHTML,/data-aq-cancel="q">Cancel</);
  context.renderAlign({id:"r",kind:"ai",slot:"AI",status:"running",repos:[]});
  assert.match(elements["align-body"].innerHTML,/data-aq-cancel="r">Stop</);
  context.renderAlign({id:"s",kind:"ai",slot:"AI",status:"running",repos:[],cancel_requested:"t"});
  assert.doesNotMatch(elements["align-body"].innerHTML,/data-aq-cancel/);
  assert.match(elements["align-title"].textContent,/^Cancelling/);
  context.renderAlign({id:"c",kind:"pr-scan",slot:"PRs",status:"cancelled",message:"Cancelled before it started"});
  assert.match(elements["align-title"].innerHTML,/Cancelled · Scan PRs/);
  assert.match(elements["align-body"].innerHTML,/before it started/);
  assert.equal(context.actionStateText({status:"running",cancel_requested:"t"}),"cancelling");
  context.renderActionQueue({actions:[{...queued,status:"cancelled"}]});
  assert.match(elements["action-queue"].innerHTML,/ab-muted">cancelled/);
  const posts=[];
  context.confirm=()=>true;
  context.fetch=async (url,opts)=>{ if(opts?.method==="POST") posts.push(url);
    return {ok:true,json:async()=>(opts?{status:"cancelled"}:{actions:[]})}; };
  vm.runInContext('ACTION_UI.jobs=[{id:"q",kind:"pr-scan",slot:"PRs",status:"queued"}]',context);
  await context.cancelAction("q");
  assert.deepEqual(posts,["/api/actions/q/cancel"]);
  context.confirm=()=>false;
  await context.cancelAction("q");
  assert.equal(posts.length,1);
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
  assert.match(elements["aq-summary"].textContent,/^sync · one · 1 waiting$/);
  assert.equal(elements["action-queue-open"].hidden,false);
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
  assert.equal(vm.runInContext("ACTION_UI.open",context),false);
  assert.match(template,/id="action-queue-open"/);
});

test("clear finished hides done/failed rows in this browser only and can be restored", () => {
  const storage={};
  const {context,elements}=bannerHarness([], storage);
  const running={id:"run",kind:"pr-scan",slot:"PRs",status:"running",repos:[]};
  const approval={id:"ap",kind:"workspace-action",action:"sync",slot:"one",status:"approval_required",steps:[]};
  const done={id:"done",kind:"pr-scan",slot:"PRs",status:"done",repos:[]};
  const failed={id:"bad",kind:"review-scan",slot:"R",status:"error",repos:[]};
  const actions=[running,approval,done,failed];
  context.renderActionQueue({actions});
  assert.match(elements["action-queue"].innerHTML,/data-aq-clear/);
  assert.doesNotMatch(elements["action-queue"].innerHTML,/data-aq-restore/);
  vm.runInContext('ACTION_UI.selected="done"',context);
  context.clearFinishedActions();
  const html=elements["action-queue"].innerHTML;
  assert.match(html,/data-action-id="run"/);
  assert.match(html,/data-action-id="ap"/);
  assert.doesNotMatch(html,/data-action-id="done"/);
  assert.doesNotMatch(html,/data-action-id="bad"/);
  assert.match(html,/Show 2 cleared/);
  assert.doesNotMatch(html,/data-aq-clear/);
  assert.equal(vm.runInContext("ACTION_UI.selected",context),"run");
  assert.equal(vm.runInContext("ACTION_UI.jobs.length",context),4);
  assert.deepEqual(JSON.parse(storage["portal.actionQueue.cleared"]).sort(),["bad","done"]);
  // persisted across reloads; ids pruned by the store are forgotten
  const reload=bannerHarness([], storage);
  reload.context.renderActionQueue({actions:[running,done]});
  assert.doesNotMatch(reload.elements["action-queue"].innerHTML,/data-action-id="done"/);
  assert.deepEqual(JSON.parse(storage["portal.actionQueue.cleared"]),["done"]);
  reload.context.restoreClearedActions();
  assert.match(reload.elements["action-queue"].innerHTML,/data-action-id="done"/);
  assert.equal(storage["portal.actionQueue.cleared"],"[]");
});

test("clearing every action leaves an empty view and does not close the popup", () => {
  const {context,elements,click,raw}=bannerHarness();
  const done={id:"done",kind:"pr-scan",slot:"PRs",status:"done",repos:[]};
  click("action-queue-open");
  context.renderActionQueue({actions:[done]});
  // a detached button: closest() finds itself but no #aq-anchor ancestor
  const button={closest:sel=>sel==="[data-aq-clear]"?button:null};
  raw({target:button});
  assert.match(elements["action-queue"].innerHTML,/No actions in view/);
  assert.match(elements["action-queue"].innerHTML,/Show 1 cleared/);
  assert.equal(elements["align-title"].textContent,"Action queue");
  assert.equal(vm.runInContext("ACTION_UI.open",context),true);
});

test("queue popup shows concurrent jobs and explains resource or capacity waits", () => {
  const {context,elements}=bannerHarness();
  const actions=[
    {id:"one",kind:"pr-scan",status:"running",repos:[]},
    {id:"two",kind:"review-scan",status:"running",repos:[]},
    {id:"three",kind:"pr-scan",status:"queued",queue_position:1,
      waiting_reason:"Waiting for conflicting actions",blocked_by:["one"]},
  ];
  context.renderActionQueue({actions,max_workers:3});
  assert.match(elements["action-queue"].innerHTML,/2 running · 1 waiting/);
  context.renderAlign(actions[2]);
  assert.match(elements["align-body"].textContent,/conflicting actions/);
  assert.match(elements["align-body"].textContent,/Independent actions may run first/);
  context.renderAlign({...actions[2],waiting_reason:"Waiting for an execution slot"});
  assert.match(elements["align-body"].textContent,/execution slot/);
  context.renderAlign({...actions[2],waiting_reason:undefined});
  assert.match(elements["align-body"].textContent,/resources and an execution slot/);
  assert.doesNotMatch(elements["align-body"].textContent,/after earlier actions finish/);
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
  assert.equal(elements["aq-summary"].textContent,"Unavailable");
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
  workspace.purpose="general";
  const controls=Object.fromEntries(["label","badge","group","rev","purpose"].map(key=>[key,{
    value:workspace[key],disabled:key==="rev"&&!!(holder||mirror),focus(){},
  }]));
  controls.purpose.disabled=!!(holder||mirror);
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
    WORKSPACES:{purposes:{general:"General",review:"Review",development:"Development",
      "build-root":"Build root",mirror:"Mirror"}},
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
  vm.runInContext(block("function wsAllows(", "let wsPurposeFilter=")+
    block("function wsUntrackDialog(", '// Modal for "+ Track workspace"'),context);
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
    assert.match(overlay.innerHTML,/name="purpose"[^>]*disabled/);
    assert.match(overlay.innerHTML,/Free the workspace or release its mirror/);
    controls.rev.value="ignored";
    controls.label.value="Updated label";
    await submit();
    assert.deepEqual(JSON.parse(JSON.stringify(requests[0].payload)),{label:"Updated label"});
  }
});

test("dedicated purpose editor saves its changed value without checkout operations", async () => {
  const h=workspaceEditHarness();
  assert.match(h.overlay.innerHTML,/Dedicated purpose/);
  h.controls.purpose.value="review";
  await h.submit();
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].payload)),{purpose:"review"});
});

test("workspace compatibility uses the owner policy and fails closed for unknown purposes", () => {
  const context=vm.createContext({WORKSPACES:{},esc:String});
  vm.runInContext(block("function wsAllows(", "let wsPurposeFilter="),context);
  const review={purpose:"review",allowed_uses:["review","adhoc:review","mirror-source"]};
  const build={purpose:"build-root",allowed_uses:["build","test","mirror","adhoc:build"]};
  assert.equal(context.wsTaskAllows(review,{category:"code-review"}),true);
  assert.equal(context.wsTaskAllows(review,{category:"development"}),false);
  assert.equal(context.wsTaskAllows(build,{category:"code-review"}),false);
  assert.equal(context.wsTaskAllows(build,{category:"development"}),false);
  assert.equal(context.wsAllows(build,"build"),true);
  assert.equal(context.wsAllows({purpose:"unknown"},"development"),false);
  assert.equal(context.wsAllows({},"development"),true);
  assert.match(block("function wsCard(", "// Per-card group picker"),/wsTaskAllows\(s,t\)/);
  assert.match(block("function renderWorkspaces()", "// ---- column toggles"),
    /class="ws-purpose-filter" data-filter multiple/);
});

test("dedicated cards expose only compatible tasks and hold tags", () => {
  const context=vm.createContext({
    WORKSPACES:{purposes:{review:"Review","build-root":"Build root"}},
    SERVED:true,esc:String,wsHolder:()=>null,wsUnknown:()=>false,wsGroupMenu:()=>"",
    activeTaskList:()=>[
      {id:"T-review",category:"code-review",title:"Review"},
      {id:"T-dev",category:"development",title:"Development"}],
  });
  vm.runInContext(block("function wsAllows(", "let wsPurposeFilter=")+
    source.match(/const WS_ADHOC_TAGS=.*;/)[0]+
    block("function wsCard(", "// Per-card group picker"),context);
  const review=context.wsCard({slot:"review",purpose:"review",status:{},
    allowed_uses:["review","adhoc:review","mirror-source"]});
  assert.match(review,/Review only/);
  assert.match(review,/option value="T-review"/);
  assert.doesNotMatch(review,/option value="T-dev"/);
  assert.match(review,/option value="review">review/);
  assert.doesNotMatch(review,/option value="debug"/);
  const build=context.wsCard({slot:"build",purpose:"build-root",status:{},
    allowed_uses:["build","test","mirror","adhoc:build","adhoc:test"]});
  assert.match(build,/Build root only/);
  assert.doesNotMatch(build,/option value="T-/);
  assert.match(build,/ws-occupy-task" disabled/);
  assert.match(build,/option value="build">build/);
  assert.match(build,/option value="test">test/);
});

test("workspace purpose filter matches any selected purpose and empty means all", () => {
  const rows=[{slot:"legacy"},{slot:"r",purpose:"review"},{slot:"d",purpose:"development"},
    {slot:"b",purpose:"build-root"},{slot:"m",purpose:"mirror"}];
  const context=vm.createContext({
    wsRoster:()=>rows,wsPurposeFilter:[],document:{getElementById:()=>({})},
  });
  vm.runInContext(block("function renderWorkspaces(){","  const occupied=")+
    "\nreturn roster;\n}",context);
  assert.equal(context.renderWorkspaces().length,5);
  context.wsPurposeFilter=["review","development"];
  assert.deepEqual(Array.from(context.renderWorkspaces(),r=>r.slot),["r","d"]);
  context.wsPurposeFilter=["general"];
  assert.deepEqual(Array.from(context.renderWorkspaces(),r=>r.slot),["legacy"]);
  context.wsPurposeFilter=[];
  assert.equal(context.renderWorkspaces().length,5);
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

test("morning brief renders steps, linked items, stale and error states safely", () => {
  const context = vm.createContext({Date});
  vm.runInContext(block("// --- morning brief (pure renderer", "// --- end morning brief ---"), context);
  const generated = "2026-11-12T08:00:00+00:00";
  const now = Date.parse(generated) + 3600 * 1000;
  const brief = {schema: 1, generated_at: generated, run: {status: "degraded", journal_id: 7},
    steps: [{id: "prs", label: "PRs", status: "warn", summary: "1 blocked", duration_s: 2}],
    sections: [{id: "prs", title: "My PRs", tone: "warn", summary: "<b>x</b>", items: [
      {text: "Feat", link: {k: "pr", id: 7}},
      {text: "Ext", link: {href: "https://github.com/o/r/pull/1"}},
      {text: "Bad", link: {href: "javascript:alert(1)"}}]}]};
  const fresh = context.briefHtml(brief, now);
  assert.match(fresh.cap, /degraded/);
  assert.match(fresh.cap, /journal #7/);
  assert.doesNotMatch(fresh.cap, /stale/);
  assert.match(fresh.body, /brief-step warn/);
  assert.match(fresh.body, /data-k="pr" data-id="7"/);
  assert.match(fresh.body, /href="https:\/\/github.com\/o\/r\/pull\/1"/);
  assert.doesNotMatch(fresh.body, /javascript:/);
  assert.match(fresh.body, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(context.briefHtml(brief, now + 30 * 3600 * 1000).cap, /stale/);
  assert.match(context.briefHtml(null, now).body, /No brief yet/);
  assert.match(context.briefHtml({error: "bad <file>"}, now).body, /brief-error">bad &lt;file&gt;/);
});

test("morning brief staleness follows the configured threshold", () => {
  const context = vm.createContext({Date});
  vm.runInContext(block("// --- morning brief (pure renderer", "// --- end morning brief ---"), context);
  const generated = "2026-11-12T08:00:00+00:00";
  const brief = {schema: 1, generated_at: generated, run: {status: "ok"}, steps: [], sections: []};
  const later = Date.parse(generated) + 5 * 3600 * 1000;
  assert.doesNotMatch(context.briefHtml(brief, later).cap, /stale/);
  assert.match(context.briefHtml(brief, later, 4).cap, /no run in the last 4 h/);
  assert.equal(context.localDmyHm(new Date(2026, 10, 5, 7, 4).getTime()), "05/11/2026 07:04");
  assert.match(context.briefHtml(brief, later).cap, /\d{2}\/\d{2}\/2026 \d{2}:\d{2}/);
});

test("routine run days are labelled by preset or listed in week order", () => {
  const context = vm.createContext({});
  vm.runInContext(block("// --- routine days (pure", "// --- end routine days ---"), context);
  const label = context.scheduleDaysLabel;
  assert.equal(label(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]), "daily");
  assert.equal(label(["Thu", "Wed", "Tue", "Mon", "Sun"]), "Sun–Thu");
  assert.equal(label(["Mon", "Tue", "Wed", "Thu", "Fri"]), "Mon–Fri");
  assert.equal(label(["Sat", "Fri"]), "Fri, Sat");
  assert.equal(label([]), "no days");
});

test("browser prefs are validated and drive nav order and visibility", () => {
  const context = vm.createContext({});
  vm.runInContext(block("// --- browser prefs (pure", "// --- end browser prefs ---"), context);
  const ids = ["overview", "work", "bugs", "settings"];
  const sections = ids.map(id => ({id}));
  const defaults = context.readPrefs(null, ids);
  assert.equal(defaults.landing, "last");
  assert.equal(defaults.actionPollMs, 1500);
  assert.equal(context.readPrefs("{broken", ids).clock, "auto");
  const p = context.readPrefs(JSON.stringify({landing: "bugs", hidden: ["work", "settings", "nope"],
    order: ["bugs", "bugs", "x", "overview"], clock: "24", compact: true,
    actionPollMs: 7, vmPollMs: 30000, vmHidden:["i-11111111","bad",null,"i-11111111"],
    vmPanelsHidden:["vms","machines","other","vms"], machineTarget:"desk-b"}), ids);
  assert.equal(p.landing, "bugs");
  assert.deepEqual([...p.hidden], ["work"]);
  assert.deepEqual([...p.order], ["bugs", "overview"]);
  assert.equal(p.clock, "24");
  assert.equal(p.compact, true);
  assert.equal(p.actionPollMs, 1500);
  assert.equal(p.vmPollMs, 30000);
  assert.deepEqual([...p.vmHidden], ["i-11111111"]);
  assert.deepEqual([...p.vmPanelsHidden], ["vms","machines"]);
  assert.equal(p.machineTarget, "desk-b");
  assert.equal(context.readPrefs(JSON.stringify({landing: "gone"}), ids).landing, "last");
  assert.deepEqual(context.navSections(sections, p).map(s => s.id), ["bugs", "overview", "settings"]);
});


// ---- Overview live pulse (assets/pulse.js) ----
const NOW = new Date("2026-11-15T12:00:00").getTime();
const DAY = 864e5;
const localIso = ms => { const d=new Date(ms); return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); };
function pulseFixture(){
  return {
    tasks:[
      {id:"T-1",status:"in-progress",category:"development",due:localIso(NOW-2*DAY)},
      {id:"T-2",status:"blocked",category:"development",workspace:"one"},
      {id:"T-3",status:"done",category:"development",due:localIso(NOW-9*DAY)},
      {id:"T-4",status:"todo",category:"code-review",updated:localIso(NOW-5*DAY),reviews:[{review_status:"pending"}]},
      {id:"T-5",status:"todo",category:"code-review",updated:localIso(NOW),reviews:[{review_status:"commented"}]},
    ],
    workspaces:{slots:{one:{holder:{type:"task"}},two:{}},roster:[
      {slot:"one",status:{checked:new Date(NOW-3600e3).toISOString(),repos:[{dirty:true}]}},
      {slot:"two",status:{checked:null,repos:[{branch:"dev/x",revision:"main"}]}}]},
    bugs:{last_scan:"2026-11-15 10:00",bugs:[
      {key:"B-1",open:true,assignee:"a",signal:"active",triage:{state:"triaged"}},
      {key:"B-2",open:true,assignee:"",signal:"active",triage:{state:"untriaged"},is_new:true},
      {key:"B-3",open:false}]},
    triage:{phenomena:[{status:"active",flags:["regressed"]},{status:"closed",flags:["escaped"]}]},
    prs:{last_scan:"2026-11-15 11:00",features:[
      {complete:false,pldm:"not-tested",prs:[{status:"ci-fail",updated:localIso(NOW)},{status:"approved",updated:localIso(NOW-8*DAY)}]},
      {complete:true,prs:[{status:"merged"}]}]},
    raid:{rows:[{rag:"red",type:"Risk"},{rag:"green",due:localIso(NOW-DAY),type:"Issue"},{rag:"red",closed:true}]},
    vmlog:{running:[{}],stale_n:0,unreachable:[],jobs:[{status:"failed",started_ts:NOW/1000-3600},{status:"passed",started_ts:NOW/1000-90000}]},
    team:{members:[{member_key:"ann",display_name:"Ann"},{member_key:"bob",display_name:"Bob",leave:{current_leave:{end_date:"2026-11-20"}}}],
      assignments:[{member_key:"ann",status:"todo",target_date:localIso(NOW-DAY)}],
      items:[{member_key:"ann",status:"open"},{member_key:"ann",status:"done"}],
      onboarding:{ann:{done:1,total:4,overdue:2,complete:false}},
      activity:{members:{
        ann:{known:true,sources:{jira:{last_success_at:new Date(NOW-7200e3).toISOString()},github:{last_success_at:null}},
          observations:[{kind:"github-pr-authored",observed_at:new Date(NOW-DAY).toISOString()},
                        {kind:"github-pr-review",observed_at:new Date(NOW-2*DAY).toISOString()},
                        {kind:"jira-assigned",observed_at:new Date(NOW-10*DAY).toISOString()}]},
        bob:{known:true,sources:{jira:{last_success_at:new Date(NOW-7200e3).toISOString()}},observations:[]}}}},
    actions:[{status:"running"}],
    settings:{status:{timer:{active:"active",next:"Mon 08:00"},last_run:{status:"ok",generated_at:new Date(NOW-3600e3).toISOString()}}},
    brief:{generated_at:new Date(NOW-30*3600e3).toISOString()},
  };
}
const card = (cards,id) => cards.find(c=>c.id===id);
const statOf = (c,k) => c.stats.find(s=>s.k===k).v;

test("pulse cards count each section and pick the worst tone", () => {
  const cards = Pulse.cards(pulseFixture(), NOW);
  assert.deepEqual(cards.map(c=>c.id), Pulse.CARD_IDS);
  const tasks = card(cards,"tasks");
  assert.equal(tasks.value, 2);                        // reviews and done excluded
  assert.equal(statOf(tasks,"overdue"), 1);
  assert.equal(statOf(tasks,"wip"), 1);                // T-1 in progress with no workspace
  assert.equal(tasks.tone, "bad");
  const bugs = card(cards,"bugs");
  assert.equal(bugs.value, 2);
  assert.equal(statOf(bugs,"attention"), 1);
  assert.equal(statOf(bugs,"alarm"), 1);               // closed phenomena never alarm
  assert.equal(bugs.tone, "bad");
  const prs = card(cards,"prs");
  assert.equal(prs.value, 1);
  assert.equal(statOf(prs,"ci"), 1);
  assert.equal(statOf(prs,"landed"), 1);
  assert.equal(statOf(prs,"stale"), 1);
  assert.equal(statOf(prs,"pldm"), 1);
  assert.equal(prs.tone, "bad");
  assert.match(prs.note, /Pipeline clean \d+%/);
  const rv = card(cards,"reviews");
  assert.equal(rv.value, 1);                           // T-5 waits on the author
  assert.equal(statOf(rv,"sla"), 1);
  assert.equal(statOf(rv,"author"), 1);
  const ws = card(cards,"workspaces");
  assert.equal(ws.value, 1); assert.equal(ws.total, 2);
  assert.equal(statOf(ws,"dirty"), 1);
  assert.equal(statOf(ws,"stray"), 1);
  assert.equal(statOf(ws,"unchecked"), 1);
  const vmC = card(cards,"vm");
  assert.equal(vmC.value, 1);
  assert.equal(statOf(vmC,"failed"), 1);
  assert.equal(statOf(vmC,"passed"), 0);               // older than 24 h
  const raid = card(cards,"raid");
  assert.equal(raid.value, 2); assert.equal(statOf(raid,"hot"), 1); assert.equal(raid.tone, "bad");
  const team = card(cards,"team");
  assert.equal(team.value, 1); assert.equal(team.total, 2);
  assert.equal(statOf(team,"overdue"), 1);
  assert.equal(statOf(team,"onboarding"), 2);
  assert.equal(statOf(team,"leave"), 1);
  assert.equal(card(cards,"portal").value, 1);
  assert.equal(card(cards,"portal").tone, "ok");
});

test("pulse hygiene rules keep the former Overview definitions", () => {
  const rules = Pulse.hygieneRules(pulseFixture(), NOW);
  assert.deepEqual(rules.map(r=>[r.id,r.bad,r.total]), [
    ["ci",1,2],["rebase",0,2],["landed",1,2],["moving",1,2],["pldm",1,1],["sla",1,1],["wip",1,1]]);
  assert.deepEqual(Pulse.hygieneScore(rules), {pct:45, bad:6, all:11});
  assert.equal(Pulse.hygieneScore([{bad:0,total:0}]), null);
});

test("pulse snapshot and delta compare against the morning brief", () => {
  const cards = Pulse.cards(pulseFixture(), NOW);
  const snap = Pulse.snapshot(cards, NOW);
  assert.equal(snap.values.tasks, 2);
  assert.equal(snap.values["bugs.attention"], 1);
  assert.equal("portal.last" in snap.values, false);   // text-only stats are not numbers
  assert.equal(Pulse.delta(snap, "tasks", 5), 3);
  assert.equal(Pulse.delta(snap, "missing", 5), null);
  assert.equal(Pulse.delta(null, "tasks", 5), null);
});

test("pulse member activity counts the last 7 days and flags gaps", () => {
  const d = pulseFixture();
  const ann = Pulse.memberActivity(d.team, "ann", NOW);
  assert.deepEqual([ann.prs, ann.reviews, ann.jira, ann.events], [1,1,0,2]);
  assert.deepEqual([ann.openTasks, ann.overdueTasks, ann.openItems], [1,1,1]);
  assert.deepEqual(ann.onboarding, {done:1,total:4,overdue:2});
  assert.deepEqual(ann.gaps, ["github"]);
  assert.equal(ann.capped, false);
  const bob = Pulse.memberActivity(d.team, "bob", NOW);
  assert.deepEqual(bob.onLeave, {until:"2026-11-20"});
  const html = Pulse.memberSummaryHtml(bob, NOW);
  assert.match(html, /On leave/);
  assert.doesNotMatch(html, /Quiet/);                  // on leave is not quiet
  assert.match(Pulse.memberSummaryHtml(ann, NOW), /No github link/);
  // A full 100-observation window that is all recent is shown as a floor.
  const many = Array.from({length:100}, () => ({kind:"github-pr-review", observed_at:new Date(NOW-DAY).toISOString()}));
  const capped = Pulse.memberActivity({members:[{member_key:"c"}], activity:{members:{c:{known:true,sources:{},observations:many}}}}, "c", NOW);
  assert.equal(capped.capped, true);
  assert.match(Pulse.memberSummaryHtml(capped, NOW), /Reviews <b>100\+<\/b>/);
  assert.match(Pulse.memberSummaryHtml({...ann, name:"<x>"}, NOW), /^<span class="ma">/);
});

test("pulse freshness reports each source and marks stale ones", () => {
  const f = Pulse.freshness(pulseFixture(), NOW);
  const by = Object.fromEntries(f.map(x=>[x.id,x]));
  assert.equal(by.bugs.ago, "2h ago"); assert.equal(by.bugs.stale, false);
  assert.equal(by.team.stale, false);
  assert.equal(by.vm.ago, "never"); assert.equal(by.vm.stale, true);
  assert.equal(by.ws.ago, "1h ago");
  assert.equal(by.routine.stale, true);                // 30 h old
});

test("pulse snapshot CLI prints values for the routine", () => {
  const out = require("node:child_process").execFileSync(process.execPath,
    [path.join(__dirname, "..", "assets", "pulse.js"), "--snapshot"],
    {input: JSON.stringify(pulseFixture())});
  const snap = JSON.parse(out);
  assert.equal(typeof snap.taken_at, "string");
  assert.equal(snap.values["raid.hot"], 1);
});

test("browser prefs validate pulse cards and order visible cards", () => {
  const context = vm.createContext({});
  vm.runInContext(block("// --- browser prefs (pure", "// --- end browser prefs ---"), context);
  const ids = ["overview","settings"], cardIds = ["tasks","bugs","prs"];
  const p = context.readPrefs(JSON.stringify({pulseHidden:["bugs","nope"], pulseOrder:["prs","prs","x","tasks"], pulsePollMs:30000}), ids, cardIds);
  assert.deepEqual([...p.pulseHidden], ["bugs"]);
  assert.deepEqual([...p.pulseOrder], ["prs","tasks"]);
  assert.equal(p.pulsePollMs, 30000);
  assert.equal(context.readPrefs(JSON.stringify({pulsePollMs:7}), ids, cardIds).pulsePollMs, 60000);
  assert.deepEqual([...context.readPrefs("{}", ids).pulseHidden], []);
  const cards = cardIds.map(id=>({id}));
  assert.deepEqual(context.pulseVisible(cards, p).map(c=>c.id), ["prs","tasks"]);
  assert.deepEqual(context.pulseVisible(cards, p, true).map(c=>c.id), ["prs","tasks","bugs"]);
});

test("overview template keeps brief, pulse and changes and drops the old storefront", () => {
  for (const id of ["ov-brief","ov-pulse","ov-changes","ov-fresh","ov-asof"]) assert.match(template, new RegExp('id="'+id+'"'));
  for (const id of ["ov-kpis","ov-sprints","ov-feed","ov-hygiene","ov-raid","ov-slots","ov-scoreboard"]) assert.doesNotMatch(template, new RegExp('id="'+id+'"'));
  assert.match(source, /if\(id==="overview"\) return pulseShown\(\)\.filter\(c=>c\.tone==="bad"\)\.length;/);
});

test("open code-review tasks offer draft / re-review / interactive AI review by state", () => {
  const task={id:"T-009",category:"code-review",status:"in-progress",reviews:[
    {repo:"one",pr_number:1,pr_url:"https://github.com/example/one/pull/1"}]};
  const served=draftHarness();
  const fresh=served.detailRow(task,8);
  assert.match(fresh,/data-ai-review="create"/);
  assert.match(fresh,/data-ai-review="interactive"/);
  assert.doesNotMatch(fresh,/data-ai-review="rereview"/);
  const drafted=served.detailRow({...task,review_drafts:[{id:"d",file:"x.md",heads:[]}]},8);
  assert.match(drafted,/data-ai-review="rereview"/);
  assert.doesNotMatch(drafted,/data-ai-review="create"/);
  assert.match(served.aiReviewButtons(task,true),/class="ai-rv-row"/);
  vm.runInContext('REVIEW_CHATS={"T-009":{id:"rc-1",workspace:"rv0"}}',served);
  const open=served.detailRow(task,8);
  assert.match(open,/data-ai-review="resume"[^>]*data-chat="rc-1"/);
  assert.doesNotMatch(open,/data-ai-review="(create|rereview|interactive)"/);
  served.AI_USAGE={enabled:false};
  assert.doesNotMatch(served.detailRow(task,8),/data-ai-review/);
  assert.doesNotMatch(draftHarness(false).detailRow(task,8),/data-ai-review/);
  assert.doesNotMatch(draftHarness().detailRow({...task,status:"done"},8),/data-ai-review/);
});

test("AI review buttons live only in the expanded view; the row shows a state marker", () => {
  const task={id:"T-009",category:"code-review",status:"in-progress",reviews:[
    {repo:"one",pr_number:1,pr_url:"https://github.com/example/one/pull/1"}]};
  const served=draftHarness();
  vm.runInContext(block("function cellFor(", "function selOpts("), served);
  const title=served.cellFor(task,"title");
  assert.doesNotMatch(title,/data-ai-review/);
  assert.doesNotMatch(title,/ai-rv-mark/);
  const fresh=served.detailRow(task,8);
  assert.match(fresh,/data-ai-review="online"[^>]*>🌐 Online review</);
  const drafted=served.detailRow({...task,review_drafts:[{id:"d",file:"x.md",heads:[]}]},8);
  assert.match(drafted,/data-ai-review="online"[^>]*>🌐 Online re-review</);
  served.ACTION_UI.jobs=[{kind:"online-review",task_id:"T-009",status:"running"},
    {kind:"auto-review",task_id:"T-010",status:"done"}];
  assert.match(served.cellFor(task,"title"),/class="ai-rv-mark running"[^>]*>✦ AI review running</);
  assert.doesNotMatch(served.cellFor({...task,id:"T-010"},"title"),/ai-rv-mark/);
  vm.runInContext('REVIEW_CHATS={"T-009":{id:"rc-1"}}',served);
  const open=served.cellFor(task,"title");
  assert.match(open,/✦ AI review open/);
  assert.doesNotMatch(open,/<button[^>]*data-ai-review/);
  served.AI_USAGE={enabled:false};
  assert.doesNotMatch(served.cellFor(task,"title"),/ai-rv-mark/);
});

test("morning brief shows the AI paragraph escaped, above the sections", () => {
  const context=vm.createContext({Date});
  vm.runInContext(block("// --- morning brief (pure renderer", "// --- end morning brief ---"), context);
  const out=context.briefHtml({generated_at:new Date().toISOString(),run:{status:"ok"},
    ai:{text:"**Risk** first.\n\n<img src=x> second",model:"m1"},
    sections:[{id:"a",title:"Sec",items:[]}]}, Date.now());
  assert.match(out.body,/class="brief-ai"/);
  assert.match(out.body,/<b>Risk<\/b> first\./);
  assert.match(out.body,/&lt;img src=x&gt; second/);
  assert.ok(out.body.indexOf("brief-ai")<out.body.indexOf("brief-grid"));
  assert.doesNotMatch(context.briefHtml({run:{},sections:[]},Date.now()).body,/brief-ai/);
});
