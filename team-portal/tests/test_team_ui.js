const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "team.js"), "utf8");

function fixture(audience = "shared") {
  return {
    audience,
    members: [{member_key: "test", display_name: "Test engineer", revision: "member-rev",
      ...(audience === "private" ? {goals: "SECRET-GOALS"} : {})}],
    assignments: [{member_key: "test", assignment_id: "assignment-1", title: "Public direction",
      status: "assigned", revision: "assignment-rev"}],
    meetings: [{member_key: "test", meeting_id: "meeting-1", title: "Session",
      meeting_date: "2026-10-25", revision: "meeting-rev",
      entries: [{entry_id: "entry-1", tag: "note", body: "Public note", visibility: "shared"}]},
    {member_key: "other", meeting_id: "other-meeting", title: "Other member session", entries: []}],
    calendar: {milestones: [{name: "MS9", started_at: "2026-10-01", ended_at: "2027-01-04"}],
      sprints: [{name: "MS9.1", started_at: "2026-10-01", ended_at: "2026-10-15"}]},
    metrics: {},
  };
}

function harness(saved = {}, data = fixture()) {
  let buttons = [];
  const decode = value => value.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  const root = {
    _html: "",
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this._html = value;
      buttons = [...value.matchAll(/<(button|input|select|textarea|form|details|span)\b([^>]*)>/g)].map(match => {
        const attrs = Object.fromEntries([...match[2].matchAll(/([\w-]+)="([^"]*)"/g)]
          .map(m => [m[1], decode(m[2])]));
        for(const flag of match[2].matchAll(/\s(data-[\w-]+)(?=\s|$)/g)) attrs[flag[1]]="";
        const dataset = {};
        for (const [key, val] of Object.entries(attrs)) {
          if (key.startsWith("data-")) {
            dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = val;
          }
        }
        const content=value.slice(match.index+match[0].length).split(`</${match[1]}>`)[0];
        const options=[...content.matchAll(/<option([^>]*)>([^<]*)/g)];
        const option=options.find(o=>/\bselected\b/.test(o[1]))||options[0];
        const initial=match[1]==="select"&&option?(/value="([^"]*)"/.exec(option[1])?.[1]||option[2]):
          match[1]==="textarea"?decode(content):attrs.value||"";
        return {attrs, dataset, value:initial, checked:/\bchecked\b/.test(match[2]),
          selectionStart:null, focus(){}, setSelectionRange(){},
          querySelector: selector=>root.querySelector(selector),
          querySelectorAll: selector=>root.querySelectorAll(selector)};
      });
    },
    querySelectorAll(selector) {
      return buttons.filter(b => selector.startsWith(".")
        ? (b.attrs.class || "").split(" ").includes(selector.slice(1))
        : Object.hasOwn(b.attrs, selector.slice(1, -1)));
    },
    querySelector(selector) {
      const match=/^\[([^=]+)="([^"]*)"\]$/.exec(selector);
      return match?buttons.find(b=>b.attrs[match[1]]===match[2]):this.querySelectorAll(selector)[0]||null;
    },
  };
  const writes = [], alerts = [], prompts = [];
  let respond = async () => ({ok: true, json: async () => fixture()});
  const context = vm.createContext({
    window: {__PORTAL__: {team: data}},
    document: {getElementById: id => id === "team-root" ? root : buttons.find(b => b.attrs.id === id),
      querySelectorAll: () => []},
    localStorage: {getItem: () => JSON.stringify(saved), setItem: (_, val) => writes.push(JSON.parse(val))},
    fetch: (...args) => respond(...args),
    alert: text => alerts.push(text),
    prompt: () => prompts.shift() ?? null,
    navigator:{clipboard:{writeText:async text=>copied.push(text)}},
  });
  const copied=[];
  vm.runInContext(source, context);
  const ui = context.window.TeamUI;
  ui.render();
  return {
    ui, root, writes, alerts, prompts, copied,
    setFetch: fn => { respond = fn; },
    button: selector => buttons.find(b => b.attrs.id === selector || b.attrs.class?.split(" ").includes(selector)),
  };
}

test("flat member collections render existing sessions and assignments, scoped by member", () => {
  const h = harness();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML, /Public direction/);
  h.ui.navigate("team/members");
  const tabs = h.root.querySelectorAll("[data-member-tab]");
  tabs.find(b => b.dataset.memberTab === "oneonone").onclick();
  assert.match(h.root.innerHTML, /Public note/);
  assert.doesNotMatch(h.root.innerHTML, /Other member session/);
});

test("calendar uses owner date fields and unknown metric values stay unknown", () => {
  const h = harness();
  h.ui.navigate("team/plan");
  assert.match(h.root.innerHTML, /MS9/);
  assert.doesNotMatch(h.root.innerHTML, /<\/b> — → —/);
  assert.doesNotMatch(h.root.innerHTML, /<td>0<\/td>/);
});

test("private mode is not restored; leaving it discards sensitive data synchronously", async () => {
  const h = harness({mode: "private"});
  assert.match(h.root.innerHTML, />Presentation</);
  h.setFetch(async () => ({ok: true, json: async () => fixture("private")}));
  await h.button("team-toggle-private").onclick();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML, /SECRET-GOALS/);
  h.ui.hidePrivate();
  assert.doesNotMatch(h.root.innerHTML, /SECRET-GOALS/);
  assert.match(h.root.innerHTML, />Presentation</);
  assert.ok(h.writes.every(state => !Object.hasOwn(state, "mode")));
});

test("late private response cannot repopulate presentation mode", async () => {
  const h = harness();
  let finish;
  h.setFetch(() => new Promise(resolve => { finish = resolve; }));
  const pending = h.button("team-toggle-private").onclick();
  h.ui.hidePrivate();
  finish({ok: true, json: async () => fixture("private")});
  await pending;
  h.ui.navigate("member/test");
  assert.doesNotMatch(h.root.innerHTML, /SECRET-GOALS/);
});

test("focus lives in member header, tasks have their own tab, and mode has no warning", () => {
  const h = harness();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML, /Current focus/);
  assert.match(h.root.innerHTML, /data-member-tab="tasks"/);
  assert.doesNotMatch(h.root.innerHTML, /data-member-tab="focus"/);
  assert.doesNotMatch(h.root.innerHTML, /sensitive|filtered|Private mode enabled/);
});

test("member tasks display stable IDs, categories and priority", () => {
  const h=harness();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML, /assignment-1/);
  assert.match(h.root.innerHTML, /<th>Category<\/th>/);
  assert.match(h.root.innerHTML, /<th>Priority<\/th>/);
  assert.match(h.root.innerHTML, /<th data-sort-type="date">Created<\/th>/);
  assert.match(h.root.innerHTML, /<th data-sort-type="number">Age<\/th>/);
  assert.match(h.root.innerHTML, /<th data-sort-type="date">Due<\/th>/);
});

test("profile uses shared popup form and focus/profile actions share a horizontal row", async () => {
  const h=harness();
  h.setFetch(async()=>({ok:true,json:async()=>fixture("private")}));
  await h.button("team-toggle-private").onclick();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML, /class="team-member-actions"><button[^>]*team-edit-focus[^]*?team-edit-member[^]*?<\/div>/);
  const profileCode=source.slice(source.indexOf("function editMember("),source.indexOf("function attach("));
  assert.match(profileCode,/formDialog\("Edit member profile"/);
  assert.doesNotMatch(profileCode,/\bprompt\(/);
  for(const field of ["main_ip_ownership","base_country","goals"]) assert.match(profileCode,new RegExp(`name="${field}"`));
  assert.match(profileCode,/visibilitySelect\("goals_visibility"/);
  assert.match(profileCode,/revision:current.revision/);
  const css=fs.readFileSync(path.join(__dirname,"..","assets","portal.css"),"utf8");
  assert.match(css,/\.team-member-actions\s*\{[^}]*display:flex;[^}]*gap:8px/);
});

test("Log replaces 1:1 label and supports multi-type filtering", () => {
  const h=harness();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML, /data-member-tab="oneonone">Log</);
  h.root.querySelectorAll("[data-member-tab]").find(b=>b.dataset.memberTab==="oneonone").onclick();
  assert.match(h.root.innerHTML, /Member log/);
  assert.match(h.root.innerHTML, /data-log-type="conversation"/);
  assert.match(h.root.innerHTML, /data-log-type="meeting"/);
  assert.match(h.root.innerHTML, /data-log-type="note"/);
  const conversation=h.root.querySelectorAll("[data-log-type]").find(b=>b.dataset.logType==="conversation");
  conversation.checked=true;
  conversation.onchange();
  assert.doesNotMatch(h.root.innerHTML, /Public note/);
});

test("session form is built from typed item rows with a compact notes box", () => {
  assert.match(source, /class="team-item-toolbar"/);
  assert.match(source, /class="team-notes-box"/);
  assert.match(source, /name="notes" rows="3"/);
  assert.match(source, /name="meeting_date"/);
  assert.match(source, /role="dialog" aria-modal="true"/);
  const meetingCode = source.slice(source.indexOf("async function editMeeting"), source.indexOf("function editFocus"));
  assert.doesNotMatch(meetingCode, /\bprompt\(/);
  assert.doesNotMatch(meetingCode, /name="state"|Session visibility|data\.get\("state"\)/);
  assert.doesNotMatch(meetingCode, /action_items/);
  assert.match(meetingCode, /itemTypes\(\)/);
  assert.match(meetingCode, /roadblock-raid-link/);
  assert.match(meetingCode, /\/api\/raid-new/);
});

test("dialog dimensions and mode colors meet the requested layout", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "assets", "portal.css"), "utf8");
  assert.match(css, /\.ws-modal\.team-session-dialog\s*\{[^}]*width:94vw;[^}]*height:94vh;/);
  assert.match(css, /\.team-item-rows\s*\{[^}]*min-height:30vh/);
  assert.match(css, /\.team-notes-box textarea\s*\{[^}]*min-height:56px/);
  assert.match(css, /\.team-mode\.private .team-mode-toggle\s*\{[^}]*background:#a92936/);
  assert.match(css, /\.team-mode\.presentation .team-mode-toggle\s*\{[^}]*background:#226841/);
  assert.match(css, /\.team-mode\s*\{[^}]*justify-content:flex-end/);
});

function tab(h, name) {
  h.root.querySelectorAll("[data-member-tab]").find(b=>b.dataset.memberTab===name).onclick();
}
function search(h, query) {
  const input=h.root.querySelectorAll("[data-team-search]")[0];
  input.value=query; input.oninput();
}
function choose(h, field, value, checked=true) {
  const input=h.root.querySelectorAll("[data-team-filter]").find(b=>b.dataset.teamFilter===field&&b.dataset.value===value);
  input.checked=checked; input.onchange();
}
function rowIds(h) { return h.root.querySelectorAll("[data-team-row]").map(b=>b.dataset.rowId); }
function richFixture(audience="shared") {
  const data=fixture(audience);
  data.assignments=[
    {...data.assignments[0],category:"debug",priority:"high",status:"todo",description:"Failure trace",
      created_at:"2026-10-20T01:00:00Z",target_date:"2026-11-02",
      updates:[{note:"Measured reboot",created_at:"2026-10-25"}],
      refs:[{domain_name:"jira",target_type:"issue",target_id:"HSI-77"}]},
    {member_key:"test",assignment_id:"a2",title:"Research roadmap",category:"research",priority:"high",status:"done",revision:"r2",created_at:"2026-10-22"},
    {member_key:"test",assignment_id:"a3",title:"Driver cleanup",category:"development",priority:"low",status:"todo",revision:"r3",created_at:"2026-10-24"},
  ];
  data.meetings.push({member_key:"test",meeting_id:"m2",session_type:"conversation",title:"Conversation",
    meeting_date:"2026-10-26",revision:"m2r",entries:[{entry_id:"e2",tag:"note",body:"Discuss roadmap",visibility:"shared"}]});
  data.activity={members:{test:{observations:[
    {source:"jira",kind:"issue",title:"First activity",observed_at:"2026-10-25T00:00:00Z",url:"https://example.test/1"},
    {source:"github",kind:"pr",title:"Last activity",observed_at:"2026-10-26T23:59:59Z",url:"https://example.test/2"},
    {source:"github",kind:"review",title:"Old activity",observed_at:"2026-10-24",url:"https://example.test/3"},
    {source:"jira",kind:"issue",title:"Undated activity"},
  ]}}};
  return data;
}

test("task search covers IDs, description, progress and references; filters OR within and AND across fields", () => {
  const h=harness({},richFixture()); h.ui.navigate("member/test");
  for(const query of ["assignment-1","failure trace","measured reboot","HSI-77"]) {
    search(h,query); assert.deepEqual(rowIds(h),["assignment-1"]);
  }
  search(h,"");
  choose(h,"category","debug"); choose(h,"category","research");
  assert.deepEqual(rowIds(h),["assignment-1","a2"]);
  choose(h,"priority","high"); choose(h,"status","done");
  assert.deepEqual(rowIds(h),["a2"]);
  assert.match(h.root.innerHTML,/1 \/ 3 matching/);
  h.root.querySelectorAll("[data-team-clear]")[0].onclick();
  assert.equal(rowIds(h).length,3);
  search(h,"does-not-exist"); assert.match(h.root.innerHTML,/No matching tasks/);
});

test("Activity and Log date ranges are inclusive and queries are independent between tabs", () => {
  const h=harness({},richFixture()); h.ui.navigate("member/test");
  search(h,"cleanup"); tab(h,"activity");
  for(const [field,value] of [["from","2026-10-25"],["to","2026-10-26"]]) {
    const input=h.root.querySelectorAll("[data-team-date]").find(b=>b.dataset.teamDate===field);
    input.value=value; input.onchange();
  }
  assert.equal(rowIds(h).length,2);
  choose(h,"source","github"); choose(h,"kind","pr");
  assert.equal(rowIds(h).length,1);
  search(h,"Last"); assert.equal(rowIds(h).length,1);
  tab(h,"oneonone"); search(h,"roadmap");
  assert.deepEqual(rowIds(h),["m2"]);
  const to=h.root.querySelectorAll("[data-team-date]").find(b=>b.dataset.teamDate==="to");
  to.value="2026-10-25"; to.onchange(); assert.equal(rowIds(h).length,0);
  tab(h,"tasks"); assert.deepEqual(rowIds(h),["a3"]);
  assert.equal(h.root.querySelectorAll("[data-team-search]")[0].value,"cleanup");
  tab(h,"activity");
  const from=h.root.querySelectorAll("[data-team-date]").find(b=>b.dataset.teamDate==="from");
  from.value="2026-10-27"; from.onchange();
  assert.equal(rowIds(h).length,0);
  assert.match(h.root.innerHTML,/From date must not be after Through date/);
});

test("selection is visible-only, supports partial/all, and cannot include hidden rows after filtering", () => {
  const data=richFixture(); data.members.push({member_key:"other",display_name:"Other"});
  const h=harness({},data); h.ui.navigate("member/test");
  assert.equal(rowIds(h).length,3);
  const first=h.root.querySelectorAll("[data-team-row]")[0]; first.checked=true; first.onchange();
  assert.match(h.root.innerHTML,/1 selected/);
  assert.equal(h.root.querySelectorAll("[data-team-select-all]")[0].indeterminate,true);
  const all=h.root.querySelectorAll("[data-team-select-all]")[0]; all.checked=true; all.onchange();
  assert.match(h.root.innerHTML,/3 selected/);
  search(h,"cleanup"); assert.match(h.root.innerHTML,/1 selected/);
  search(h,""); assert.match(h.root.innerHTML,/1 selected/);
  h.ui.navigate("member/other"); assert.match(h.root.innerHTML,/0 selected/);
  assert.doesNotMatch(h.root.innerHTML,/data-team-bulk=/);
});

test("Activity selection copies source links without mutation or editing controls", async () => {
  const h=harness({},richFixture()); h.ui.navigate("member/test"); tab(h,"activity");
  h.setFetch(()=>{throw new Error("No network expected");});
  const all=h.root.querySelectorAll("[data-team-select-all]")[0]; all.checked=true; all.onchange();
  await h.root.querySelectorAll("[data-team-copy-links]")[0].onclick();
  assert.equal(h.copied[0].split("\n").length,3);
  assert.doesNotMatch(h.root.innerHTML,/team-inline"|data-team-bulk=/);
});

test("task rows keep edit details and remove task inline edit", async () => {
  const h=harness();
  h.setFetch(async()=>({ok:true,json:async()=>richFixture("private")}));
  await h.button("team-toggle-private").onclick();
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML,/team-edit" data-type="assignment"/);
  assert.doesNotMatch(h.root.innerHTML,/team-inline" data-type="assignment"/);
});

test("inline Log notes save preserves other entries and per-item visibility", async () => {
  const h=harness(); const data=richFixture("private"); let request;
  data.meetings[0].entries.push({entry_id:"private-e",tag:"feedback",body:"Private note",visibility:"private"});
  h.setFetch(async(url,options={})=>{
    if(options.method==="POST") {request=JSON.parse(options.body);return {ok:true,json:async()=>({result:{ok:true}})};}
    return {ok:true,json:async()=>data};
  });
  await h.button("team-toggle-private").onclick(); h.ui.navigate("member/test"); tab(h,"oneonone");
  h.root.querySelectorAll(".team-inline")[0].onclick();
  const field=h.root.querySelector("[data-team-inline-field]"); field.value="entry:entry-1"; field.onchange();
  const value=h.root.querySelector("[data-team-edit-value]"); value.value="Edited public note"; value.oninput();
  await h.root.querySelector("[data-team-inline-form]").onsubmit({preventDefault(){}});
  assert.equal(request.action,"meeting-save");
  assert.deepEqual(request.data.entries,[
    {entry_id:"entry-1",tag:"note",body:"Edited public note",visibility:"shared"},
    {entry_id:"private-e",tag:"feedback",body:"Private note",visibility:"private"},
  ]);
});

test("leaving Private clears search phrases and selections and restores only shared data", async () => {
  const h=harness({},richFixture()); h.setFetch(async()=>({ok:true,json:async()=>richFixture("private")}));
  await h.button("team-toggle-private").onclick(); h.ui.navigate("member/test"); search(h,"SECRET phrase");
  h.ui.hidePrivate();
  assert.doesNotMatch(h.root.innerHTML,/SECRET|team-inline-editor|data-team-bulk=/);
  assert.equal(rowIds(h).length,3);
});

const ITEM_TYPES={
  action:{label:"Action item",summary:"title",fields:[{key:"title",label:"Action",input:"text",required:true},
    {key:"owner",label:"Owner",input:"select",options:["me","member"]}]},
  roadblock:{label:"Roadblock",summary:"blocker",fields:[{key:"blocker",label:"Blocker",input:"text",required:true},
    {key:"unblocker",label:"Who unblocks",input:"select",options:["me","member","other"]}]},
  decision:{label:"Decision",summary:"decision",fields:[{key:"decision",label:"Decision",input:"text",required:true}]},
};
function typedFixture(audience="private", lead=false){
  const data=fixture(audience);
  data.log_item_types=ITEM_TYPES; data.manager_name="Manager";
  if(lead) data.members[0].roster_role="lead";
  data.meetings=[
    {member_key:"test",meeting_id:"t1",title:"Typed",meeting_date:"2026-10-25",revision:"r",entries:[
      {entry_id:"x1",tag:"action",body:"Write plan",fields:{title:"Write plan",owner:"member"},record_id:"assignment-1",visibility:"shared"},
      {entry_id:"x2",tag:"roadblock",body:"No board",fields:{blocker:"No board",unblocker:"me"},record_id:"rb-1",visibility:"private"}]},
    {member_key:"test",meeting_id:"t2",title:"Linked",meeting_date:"2026-10-26",revision:"r",entries:[
      {entry_id:"y1",tag:"roadblock",body:"Lab down",fields:{blocker:"Lab down"},record_id:"rb-2",raid_id:"I-007",visibility:"shared"},
      {entry_id:"y2",tag:"decision",body:"Use DMA",fields:{decision:"Use DMA"},visibility:"shared"}]},
  ];
  return data;
}

test("Log renders typed item badges, key fields, RAID chip and promote button", async () => {
  const h=harness({},typedFixture("shared"));
  h.setFetch(async()=>({ok:true,json:async()=>typedFixture("private")}));
  await h.button("team-toggle-private").onclick(); h.ui.navigate("member/test"); tab(h,"oneonone");
  const html=h.root.innerHTML;
  assert.match(html,/data-item-type="action">Action item</);
  assert.match(html,/<b>Owner:<\/b> Test engineer/);
  assert.match(html,/RAID I-007/);
  assert.equal(h.root.querySelectorAll(".team-promote-raid").length,1);
  assert.equal(h.root.querySelectorAll(".team-promote-raid")[0].dataset.entry,"x2");
  assert.match(html,/data-item-type="decision">Decision</);
});

test("Log item-type filter keeps sessions containing any selected item type", () => {
  const h=harness({},typedFixture("shared")); h.ui.navigate("member/test"); tab(h,"oneonone");
  assert.deepEqual(rowIds(h).sort(),["t1","t2"]);
  choose(h,"items","decision"); assert.deepEqual(rowIds(h),["t2"]);
  choose(h,"items","action"); assert.deepEqual(rowIds(h).sort(),["t1","t2"]);
  choose(h,"items","decision",false); assert.deepEqual(rowIds(h),["t1"]);
  assert.match(h.root.innerHTML,/> Roadblock</);
});

test("lead page labels the 1:1 counterpart as the manager", async () => {
  const h=harness({},typedFixture("shared",true));
  h.setFetch(async()=>({ok:true,json:async()=>typedFixture("private",true)}));
  await h.button("team-toggle-private").onclick(); h.ui.navigate("member/test"); tab(h,"oneonone");
  assert.match(h.root.innerHTML,/<b>Owner:<\/b> Manager \(manager\)/);
});
