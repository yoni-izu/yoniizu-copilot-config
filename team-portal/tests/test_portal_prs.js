const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "portal.js"), "utf8");
function harness(served = true) {
  const listeners = {};
  const menus = [];
  const context = vm.createContext({
    SERVED: served, PE_URL: "http://localhost:8080", JIRA_BASE: "https://jira.example/",
    PBI: {grey: "#999", red: "#f00", gold: "#fa0", green: "#0f0"},
    PR_STATUS_COLOR: {"ci-fail": "#f00", merged: "#0f0"},
    PRI_ORDER: ["critical", "high", "medium", "low"],
    PR_EDIT_OPEN: new Set(), PR_DETAIL_OPEN: new Set(), PR_SELECTED: new Set(),
    PR_FILTERS: {q: "", feature: new Set(), priority: new Set(), pldm: new Set(),
      repo: new Set(), prStatus: new Set(), openOnly: false},
    prCollapsed: () => false,
    esc: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
    formatPortalDate: value => value,
    pldmBadge: () => '<span class="pldm">PLDM untested</span>',
    peRun: value => value, pePr: value => value,
    document: {
      addEventListener: (type, callback) => { listeners[type] = callback; },
      querySelectorAll: () => menus,
    },
  });
  vm.runInContext(source.slice(source.indexOf("function prVisibleRows("), source.indexOf("function ciCell("))
    + source.slice(source.indexOf("function ciCell("),
      source.indexOf("// ---------------- PLDM submission")), context);
  return {context, listeners, menus};
}

function feature(prs) {
  return {
    id: 8, name: "Example feature", summary: "Short **context**", description: "Technical `details`",
    priority: "critical", worst: "ci-fail", merged: 0, total: prs ? prs.length : 1,
    logged: "2026-10-28", jira: ["TEST-42"], notes: [],
    prs: prs || [{repo: "example/repo", number: 12, title: "Make the important change",
      url: "https://github.com/example/repo/pull/12", status: "ci-fail",
      checks_fail: 2, checks_pass: 3, failed_runs: ["https://github.com/example/repo/actions/runs/1"]}],
  };
}

test("PR table precedes context and folded history, without duplicated overall status", () => {
  const html = harness().context.prFeatureRow(feature());
  assert.ok(html.indexOf('class="pr-tbl"') < html.indexOf('class="prf-context"'));
  assert.ok(html.indexOf('class="prf-context"') < html.indexOf('class="prf-history"'));
  assert.doesNotMatch(html, /overall:|<b>Priority:|<b>Progress:/);
  assert.match(html, /class="prf-more"[^>]*><summary>Feature details/);
  assert.match(html, /class="prf-history"[^>]*><summary>Activity history/);
  assert.match(html, /class="prf-actions-menu"><summary>Actions/);
});

test("all feature and PR actions retain their handler attributes and feedback targets", () => {
  const html = harness().context.prFeatureRow(feature());
  for (const attr of ["ci-all", "ci-fail", "prtrack", "pldm", "prdrop", "ci-one", "prx", "prsave"]) {
    assert.match(html, new RegExp('data-' + attr + '="8"'));
  }
  assert.match(html, /id="ci-note-8" role="status" aria-live="polite"/);
  assert.match(html, /id="pldm-box-8" hidden/);
  assert.match(html, /Stop tracking feature/);
  assert.match(html, /why red/);
});

test("static output keeps PR links and disclosures but excludes mutation controls", () => {
  const html = harness(false).context.prFeatureRow(feature());
  assert.match(html, /href="https:\/\/github.com\/example\/repo\/pull\/12"/);
  assert.match(html, /Serve the portal to edit/);
  assert.doesNotMatch(html, /prf-actions-menu|data-(ci-one|ci-all|prdrop|prtrack|prx|pldm|prsave)=/);
  assert.match(html, /data-pr-disclosure="8:context"/);
});

test("empty feature has a useful table state and no CI or PLDM run controls", () => {
  const html = harness().context.prFeatureRow(feature([]));
  assert.match(html, /No PRs tracked yet/);
  assert.match(html, /Activity history <span class="muted">\(0\)/);
  assert.doesNotMatch(html, /data-ci-all=|data-ci-fail=|data-pldm=/);
  assert.match(html, /data-prtrack="8"/);
});

test("context, full description, activity and editor state are retained", () => {
  const {context} = harness();
  context.PR_DETAIL_OPEN.add("8:history");
  context.PR_DETAIL_OPEN.add("8:context");
  context.PR_EDIT_OPEN.add("8");
  const f = feature();
  f.notes = [{date: "2026-10-29", label: "Update", text: "A **note**"}];
  const html = context.prFeatureRow(f);
  assert.match(html, /data-pr-disclosure="8:history" open/);
  assert.match(html, /data-pr-disclosure="8:context" open/);
  assert.match(html, /class="pr-edit-wrap" data-fid="8" open/);
  assert.match(html, /Technical <code>details<\/code>/);
  assert.match(html, /A <b>note<\/b>/);
  assert.match(html, /Logged:|Last activity:/);
});

test("dynamic content is escaped, including the scroll region's accessible name", () => {
  const f = feature();
  f.name = 'Bad"><img src=x>';
  f.summary = "<script>alert(1)</script>";
  f.prs[0].title = "<img src=x>";
  const html = harness().context.prFeatureRow(f);
  assert.doesNotMatch(html, /<img|<script>/);
  assert.match(html, /aria-label="Pull requests for Bad&quot;&gt;&lt;img src=x&gt;" tabindex="0"/);
});

test("Jira coverage is per ticket and sync readings escape all owner values", () => {
  const f = feature();
  f.jira = ["TEST-42", 'BAD"><img src=x>'];
  f.jira_coverage = ["TEST-42"];
  f.jira_sync = [{key: 'BAD"><img src=x>', state: "error", target: "<Done>",
    message: "<script>fail</script>"}];
  const html = harness().context.prFeatureRow(f);
  assert.match(html, /data-jira-coverage="8" data-jira-key="TEST-42">Revoke coverage/);
  assert.match(html, /data-jira-key="BAD&quot;&gt;&lt;img src=x&gt;">Confirm full PR coverage/);
  assert.match(html, /Full PR coverage confirmed/);
  assert.match(html, /error → &lt;Done&gt;: &lt;script&gt;fail&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<img|<script>/);
  const readonly = harness(false).context.prFeatureRow(f);
  assert.doesNotMatch(readonly, /data-jira-coverage|Confirm full PR coverage|Revoke coverage/);
  assert.match(readonly, /Full PR coverage confirmed/);
  assert.match(readonly, /error → &lt;Done&gt;/);
});

test("a retained sync result after revocation is historical, not active coverage", () => {
  const f = feature();
  f.jira_coverage = [];
  f.jira_sync = [{key: "TEST-42", state: "updated", target: "In Review", message: "transitioned"}];
  const html = harness().context.prFeatureRow(f);
  assert.match(html, /Last Jira sync result · TEST-42 · updated → In Review/);
  assert.match(html, /data-jira-key="TEST-42">Confirm full PR coverage/);
  assert.doesNotMatch(html, /Full PR coverage confirmed|Revoke coverage/);
});

function coverageHarness(served = true) {
  const h = harness(served);
  const {context} = h;
  const f = feature();
  f.revision = 7;
  context.PRS = {features: [f]};
  context.prModal = (html, submit) => { h.dialog = {html, submit}; };
  context.prNote = text => { h.note = text; };
  context.renderPrs = () => { h.renders = (h.renders || 0) + 1; };
  context.buildSections = () => {};
  context.fetch = async (url, options) => {
    h.request = {url, body: JSON.parse(options.body)};
    return {ok: true, json: async () => ({message: "saved", prs: {features: []}})};
  };
  vm.runInContext(source.slice(source.indexOf("async function prPost("),
    source.indexOf("async function saveFeaturePriority(")), context);
  h.button = {disabled: false};
  h.message = {textContent: ""};
  h.overlay = {querySelector: selector => selector === ".ws-modal-ok" ? h.button : h.message};
  h.close = () => { h.closed = true; };
  return h;
}

test("coverage confirmation explicitly grants guarded sync for the displayed PR set", async () => {
  const h = coverageHarness();
  h.context.PRS.features[0].name = "<script>feature</script>";
  h.context.prJiraCoverageDialog(8, "TEST-42");
  assert.match(h.dialog.html, /entire current PR set fully implements TEST-42/);
  assert.match(h.dialog.html, /In Review \/ Done/);
  assert.match(h.dialog.html, /assigned to the authenticated Jira user/);
  assert.match(h.dialog.html, /Changing the PR set invalidates this confirmation/);
  assert.match(h.dialog.html, /example\/repo #12/);
  assert.doesNotMatch(h.dialog.html, /<script>/);
  assert.equal(h.request, undefined);
  // Keep the displayed revision even if a background payload refresh occurs.
  h.context.PRS.features[0].revision = 8;
  await h.dialog.submit(h.overlay, h.close);
  assert.deepEqual(h.request, {url: "/api/feature-jira-coverage",
    body: {feature_id: 8, key: "TEST-42", confirmed: true, expected_revision: 7}});
  assert.equal(h.closed, true);
  assert.equal(h.renders, 1);
});

test("coverage revocation sends false and owner rejection stays visible", async () => {
  const h = coverageHarness();
  h.context.PRS.features[0].jira_coverage = ["TEST-42"];
  h.context.prJiraCoverageDialog("8", "TEST-42");
  assert.match(h.dialog.html, /does not undo Jira transitions already made/);
  h.context.fetch = async (url, options) => {
    h.request = JSON.parse(options.body);
    return {ok: false, json: async () => ({error: "feature changed; reload"})};
  };
  await h.dialog.submit(h.overlay, h.close);
  assert.equal(h.request.confirmed, false);
  assert.equal(h.request.expected_revision, 7);
  assert.equal(h.closed, undefined);
  assert.equal(h.message.textContent, "feature changed; reload");
  assert.equal(h.button.disabled, false);
});

test("coverage controls are delegated and static or missing tickets cannot open dialogs", () => {
  const h = coverageHarness();
  h.listeners.click({target: {closest: () =>
    ({dataset: {jiraCoverage: "8", jiraKey: "TEST-42"}})}});
  assert.match(h.dialog.html, /Confirm full PR coverage/);
  for (const [served, id, key] of [[false, 8, "TEST-42"], [true, 99, "TEST-42"],
    [true, 8, "OTHER-1"]]) {
    const readonly = coverageHarness(served);
    readonly.context.prJiraCoverageDialog(id, key);
    assert.equal(readonly.dialog, undefined);
    assert.equal(readonly.request, undefined);
  }
});

test("partial scan adopts fresh PR data but reports archived Jira failure rather than success", async () => {
  const h = coverageHarness();
  const elements = {};
  h.context.document.getElementById = id => elements[id] ||= {};
  h.context.syncPrSelection = () => {};
  const result = {features: [], scanned: 1, jira_sync: [
    {feature_id: 90, key: "TEST-90", state: "error", message: "Jira unavailable"}]};
  h.context.fetch = async () => ({ok: true, json: async () => ({job: "scan-1"})});
  h.context.pollAlign = async () => ({status: "error", result,
    message: "Jira sync: 1 error; archived retries: feature 90 / TEST-90: Jira unavailable"});
  vm.runInContext(source.slice(source.indexOf("async function scanPrs("),
    source.indexOf('document.getElementById("pr-scan").addEventListener')), h.context);
  await h.context.scanPrs();
  assert.equal(h.context.PRS, result);
  assert.equal(h.renders, 1);
  assert.match(elements["pr-scan-note"].textContent, /Scan failed: Jira sync: 1 error; archived retries/);
  assert.equal(elements["pr-scan"].disabled, false);
});

test("successful scan shows escaped Jira outcome counts in its completion note", async () => {
  const h = coverageHarness();
  const elements = {};
  h.context.document.getElementById = id => elements[id] ||= {};
  h.context.syncPrSelection = () => {};
  h.context.fetch = async () => ({ok: true, json: async () => ({job: "scan-1"})});
  h.context.pollAlign = async () => ({status: "done", result: {features: [], scanned: 1},
    message: "Jira sync: 1 updated, 0 error <unsafe>"});
  vm.runInContext(source.slice(source.indexOf("async function scanPrs("),
    source.indexOf('document.getElementById("pr-scan").addEventListener')), h.context);
  await h.context.scanPrs();
  assert.match(elements["pr-scan-note"].innerHTML, /Jira sync: 1 updated, 0 error &lt;unsafe&gt;/);
});

test("scan action banner never shows green success for archived Jira failures", () => {
  const {context} = harness();
  const elements = {};
  context.document.getElementById = id => elements[id] ||= {style: {}};
  context.alignRepoRow = () => "";
  context.actionCancellable = () => false;
  vm.runInContext(source.slice(source.indexOf("function renderAlign("),
    source.indexOf("async function pollAlign(")), context);
  context.renderAlign({kind: "pr-scan", status: "error", repos: [],
    result: {jira_sync: [{feature_id: 90, key: "TEST-90", state: "error"}]},
    message: "Jira sync: 1 error; archived retries: TEST-90 <unavailable>"});
  assert.match(elements["align-title"].innerHTML, /ab-bad/);
  assert.doesNotMatch(elements["align-title"].innerHTML, /ab-ok|✓/);
  assert.match(elements["align-body"].innerHTML, /1 error; archived retries: TEST-90 &lt;unavailable&gt;/);
});

test("merged and closed PRs cannot rerun CI; completed notice remains in details", () => {
  const f = feature([
    {...feature().prs[0], status: "merged"},
    {...feature().prs[0], number: 13, status: "closed"},
  ]);
  let html = harness().context.prFeatureRow(f);
  assert.doesNotMatch(html, /data-ci-one=|data-ci-all=|data-ci-fail=/);
  f.merged = f.total;
  html = harness().context.prFeatureRow(f);
  assert.match(html, /retires on the next scan/);
});

test("PR table keeps distinct CI verdicts, check counts and merge gates", () => {
  const {context} = harness();
  for (const [checks, verdict] of [
    [{checks_fail: 2, checks_pass: 3}, "fail"],
    [{checks_pending: 1, checks_pass: 3}, "running"],
    [{checks_pass: 3}, "pass"],
    [{}, "not run"],
  ]) {
    const f = feature([{repo: "example/repo", number: 12, status: "pending", ...checks}]);
    assert.match(context.prRows(f), new RegExp(">" + verdict + "<"));
  }
  const f = feature();
  f.prs[0].merge_state = "BEHIND";
  assert.match(context.prRows(f), /2F<\/b>\/3P/);
  assert.match(context.prRows(f), />behind</);
});

test("PR Open only hides merged/closed PR rows and features with no open PR", () => {
  const {context} = harness();
  vm.runInContext(
    source.slice(source.indexOf("function prFeatureMatchesFilters("), source.indexOf("function ciCell(")),
    context);
  const f = feature([
    {...feature().prs[0], number: 12, status: "pending"},
    {...feature().prs[0], number: 13, status: "merged"},
    {...feature().prs[0], number: 14, status: "closed"},
  ]);
  context.PR_FILTERS.openOnly = true;
  assert.equal(context.prFeatureMatchesFilters(f), true);
  const html = context.prFeatureRow(f);
  assert.match(html, /Pull requests <span class="prf-count">1<\/span>/);
  assert.match(html, /#12/);
  assert.doesNotMatch(html, /#13|#14/);
  const completed = feature([{...feature().prs[0], status: "merged"}]);
  assert.equal(context.prFeatureMatchesFilters(completed), false);
  context.PR_FILTERS.openOnly = false;
  assert.match(context.prFeatureRow(f), /Pull requests <span class="prf-count">3<\/span>/);
  assert.match(source, /append\(makeOpenOnly\("pr-openonly"\)\)/);
});

test("action menu closes on outside click, action selection and Escape with focus restored", () => {
  const {listeners, menus} = harness();
  let focused = false;
  const menu = {open: true, contains: target => target.inside,
    querySelector: () => ({focus: () => { focused = true; }})};
  menus.push(menu);
  listeners.click({target: {inside: true, closest: () => null}});
  assert.equal(menu.open, true);
  listeners.click({target: {inside: false, closest: () => null}});
  assert.equal(menu.open, false);
  menu.open = true;
  listeners.click({target: {inside: true, closest: () => ({})}});
  assert.equal(menu.open, false);
  menu.open = true;
  listeners.keydown({key: "Enter"});
  assert.equal(menu.open, true);
  listeners.keydown({key: "Escape"});
  assert.equal(menu.open, false);
  assert.equal(focused, true);
});
