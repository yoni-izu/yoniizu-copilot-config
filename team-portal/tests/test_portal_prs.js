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
