const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "assets", "team.js"), "utf8");
const portalSource = fs.readFileSync(path.join(__dirname, "..", "assets", "portal.js"), "utf8");
const dateHelpersStart = portalSource.indexOf("function validPortalDate");
const dateHelpersEnd = portalSource.indexOf("let TASKS", dateHelpersStart);
assert.notEqual(dateHelpersStart, -1);
assert.notEqual(dateHelpersEnd, -1);
function portalDates(){
  const context=vm.createContext({window:{}});
  vm.runInContext(portalSource.slice(dateHelpersStart,dateHelpersEnd),context);
  return context.window.PortalDates;
}

function fixture(audience = "shared") {
  return {
    audience,
    members: [{member_key: "test", display_name: "Test engineer", revision: "member-rev",
      ...(audience === "private" ? {goals: "SECRET-GOALS"} : {})}],
    assignments: [{member_key: "test", assignment_id: "assignment-1", title: "Public direction",
      status: "assigned", created_at:"2026-10-05T13:30:00Z",target_date:"2026-11-02",revision: "assignment-rev"}],
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
          selectionStart:null, focus(){}, setSelectionRange(){}, setCustomValidity(message){this.validityMessage=message;},
          reportValidity(){return false;},
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
  const writes = [], alerts = [], prompts = [], confirmations = [], confirmMessages = [];
  let respond = async () => ({ok: true, json: async () => fixture()});
  const context = vm.createContext({
    window: {__PORTAL__: {team: data},PortalDates:portalDates()},
    document: {getElementById: id => id === "team-root" ? root : buttons.find(b => b.attrs.id === id),
      querySelectorAll: () => []},
    localStorage: {getItem: () => JSON.stringify(saved), setItem: (_, val) => writes.push(JSON.parse(val))},
    fetch: (...args) => respond(...args),
    alert: text => alerts.push(text),
    prompt: () => prompts.shift() ?? null,
    confirm: text => { confirmMessages.push(text); return confirmations.shift() ?? false; },
    navigator:{clipboard:{writeText:async text=>copied.push(text)}},
  });
  const copied=[];
  vm.runInContext(source, context);
  const ui = context.window.TeamUI;
  ui.render();
  return {
    ui, root, writes, alerts, prompts, confirmations, confirmMessages, copied,
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

test("whole-session deletion is private-only, confirmation-gated, and uses the displayed revision", async () => {
  const h=harness();
  h.ui.navigate("member/test");
  h.root.querySelectorAll("[data-member-tab]").find(b=>b.dataset.memberTab==="oneonone").onclick();
  assert.doesNotMatch(h.root.innerHTML,/team-delete-meeting/);

  let deleted=false, post;
  h.setFetch(async(url,options={})=>{
    if(options.method==="POST"){
      post={url,options,body:JSON.parse(options.body)};
      deleted=true;
      return {ok:true,json:async()=>({result:{ok:true}})};
    }
    const data=fixture("private");
    if(deleted) data.meetings=[];
    return {ok:true,json:async()=>data};
  });
  await h.button("team-toggle-private").onclick();
  h.ui.navigate("member/test");
  h.root.querySelectorAll("[data-member-tab]").find(b=>b.dataset.memberTab==="oneonone").onclick();
  const deleteButton=h.root.querySelectorAll(".team-delete-meeting")[0];
  assert.ok(deleteButton);
  h.confirmations.push(false);
  await deleteButton.onclick();
  assert.equal(post,undefined);
  h.confirmations.push(true);
  await deleteButton.onclick();
  assert.equal(post.url,"/api/team");
  assert.deepEqual(post.body,{action:"meeting-delete",data:{id:"meeting-1",revision:"meeting-rev"}});
  assert.equal(post.options.headers["X-Team-Private"],"1");
  assert.match(h.confirmMessages[1],/task assignments will remain/);
  assert.doesNotMatch(h.root.innerHTML,/Session/);
});

test("member task Open only hides done and cancelled assignments and Clear restores them", () => {
  const data=fixture();
  data.assignments.push(
    {member_key:"test",assignment_id:"assignment-done",title:"Completed direction",status:"done"},
    {member_key:"test",assignment_id:"assignment-cancelled",title:"Cancelled direction",status:"cancelled"});
  const h=harness({},data);
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML,/Completed direction/);
  assert.match(h.root.innerHTML,/Cancelled direction/);
  const openOnly=h.root.querySelectorAll("[data-team-open-only]")[0];
  assert.ok(openOnly);
  openOnly.checked=true;
  openOnly.onchange();
  assert.match(h.root.innerHTML,/Public direction/);
  assert.doesNotMatch(h.root.innerHTML,/Completed direction|Cancelled direction/);
  h.root.querySelectorAll("[data-team-clear]")[0].onclick();
  assert.match(h.root.innerHTML,/Completed direction/);
  assert.match(h.root.innerHTML,/Cancelled direction/);
});

test("calendar uses owner date fields and unknown metric values stay unknown", () => {
  const h = harness();
  h.ui.navigate("team/plan");
  assert.match(h.root.innerHTML, /MS9/);
  assert.match(h.root.innerHTML,/01\/10\/2026 → 04\/01\/2027/);
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
  assert.match(h.root.innerHTML,/05\/10\/2026 13:30:00Z/);
  assert.match(h.root.innerHTML,/02\/11\/2026/);
  assert.match(h.root.innerHTML,/data-sort-value="2026-10-05T13:30:00Z"/);
});

test("date filters use dd/mm/yyyy text while retaining ISO filter values", () => {
  const h=harness();
  h.ui.navigate("member/test");
  h.root.querySelector('[data-member-tab="activity"]').onclick();
  const from=h.root.querySelector('[data-team-date="from"]');
  assert.equal(from.attrs.type,"text");
  assert.equal(from.attrs.placeholder,"dd/mm/yyyy");
  from.value="01/10/2026";
  from.onchange();
  assert.match(h.root.innerHTML,/value="01\/10\/2026"[^>]*data-team-date="from"/);
  assert.doesNotMatch(h.root.innerHTML,/<input[^>]*type="date"/);

  const invalid=h.root.querySelector('[data-team-date="to"]');
  invalid.value="31/02/2026";
  invalid.onchange();
  assert.match(invalid.validityMessage,/dd\/mm\/yyyy/);
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
  assert.match(source, /class="team-item-row"/);
  assert.match(source, /name="item_tag"/);
  assert.match(source, /name="priority"/);
  assert.match(source, /class="team-item-extra"/);
  assert.match(source, /class="team-notes-box"/);
  assert.match(source, /name="notes" rows="3"/);
  assert.match(source, /dateInput\("meeting_date",current\?\.meeting_date\|\|""\)/);
  assert.match(source, /data-date-field/);
  assert.match(source, /role="dialog" aria-modal="true"/);
  const meetingCode = source.slice(source.indexOf("async function editMeeting"), source.indexOf("function editFocus"));
  assert.doesNotMatch(meetingCode, /\bprompt\(/);
  assert.doesNotMatch(meetingCode, /name="state"|Session visibility|data\.get\("state"\)/);
  assert.doesNotMatch(meetingCode, /action_items/);
  assert.match(meetingCode, /itemTypes\(\)/);
  assert.match(meetingCode, /roadblock-raid-link/);
  assert.match(meetingCode, /\/api\/raid-new/);
});

test("log metadata uses content-sized controls and wraps on narrow screens", () => {
  const css=fs.readFileSync(path.join(__dirname,"..","assets","portal.css"),"utf8");
  assert.match(css,/\.team-session-meta\s*\{[^}]*display:flex;[^}]*flex-wrap:wrap;[^}]*gap:12px;/);
  assert.match(css,/\.team-session-meta > \*\s*\{[^}]*min-width:0;[^}]*max-width:100%;/);
  assert.match(css,/\.team-session-meta > :nth-child\(2\)\s*\{[^}]*flex:1 1 120px;/);
  assert.match(css,/\.team-session-meta :is\(input, select\)\s*\{[^}]*min-width:0;[^}]*max-width:100%;/);
  assert.match(css,/\.team-session-meta select\s*\{[^}]*width:auto;[^}]*align-self:flex-start;/);
  assert.match(css,/\.team-session-meta input\[name="meeting_date"\]\s*\{[^}]*width:calc\(10ch \+ 22px\);/);
});

test("log metadata fields retain order and visibility in create and edit dialogs", () => {
  const scenarios=[
    {name:"create",mode:"private",current:null,fields:["session_type","title","meeting_date","title_visibility"]},
    {name:"private edit",mode:"private",current:{title_visibility:"private",entries:[]},
      fields:["session_type","title","meeting_date","title_visibility"]},
    {name:"shared edit",mode:"presentation",current:{title_visibility:"shared",entries:[]},
      fields:["session_type","title","meeting_date","title_visibility"]},
    {name:"hidden topic",mode:"presentation",current:{title_visibility:"private",entries:[]},
      fields:["session_type","meeting_date"]},
  ];
  for(const scenario of scenarios){
    let markup;
    const data=fixture();
    data.log_types={one_on_one:"1:1",conversation:"A longer conversation option",meeting:"Meeting",note:"Note"};
    const context=vm.createContext({
      window:{__PORTAL__:{team:data},PortalDates:portalDates(),
        captureDialog:(_title,fields)=>{markup=fields;}},
      localStorage:{getItem:()=>null},
    });
    const instrumented=source
      .replace(source.slice(source.indexOf("  function formDialog("),
        source.indexOf("  async function editAssignment(")),
        "  function formDialog(...args){ window.captureDialog(...args); }\n")
      .replace('let mode = "presentation"',`let mode = "${scenario.mode}"`)
      .replace("window.TeamUI =", "window.openLog = meetingDialog; window.TeamUI =");
    vm.runInContext(instrumented,context);
    context.window.openLog("test",scenario.current);
    const metadata=markup.slice(markup.indexOf('<div class="team-session-meta">'),
      markup.indexOf('<div class="team-item-toolbar"'));
    const fields=[...metadata.matchAll(/name="(session_type|title|meeting_date|title_visibility)"/g)].map(m=>m[1]);
    assert.deepEqual(fields,scenario.fields,scenario.name);
    assert.match(metadata,/>A longer conversation option<\/option>/,scenario.name);
    if(scenario.fields.includes("title")){
      assert.match(metadata,/<input type="text" name="title"/,scenario.name);
      assert.match(metadata,/>Presentation<\/option>/,scenario.name);
    }else{
      assert.match(metadata,/Topic is hidden and will remain unchanged/,scenario.name);
    }
  }
});

test("Notes disclosure shows its state and previews edits in every log dialog", () => {
  const css=fs.readFileSync(path.join(__dirname,"..","assets","portal.css"),"utf8");
  assert.match(css,/\.team-notes-collapse,\s*\.team-notes-row\[open\] \.team-notes-expand\s*\{[^}]*display:none/);
  assert.match(css,/\.team-notes-row\[open\] \.team-notes-collapse\s*\{[^}]*display:inline/);
  assert.match(css,/\.team-notes-row \.team-item-row-title\s*\{[^}]*text-overflow:ellipsis/);

  for(const scenario of ["create","private-edit","shared-edit"]){
    const current=scenario==="create"?null:{
      meeting_id:"log-1",revision:"revision-1",title_visibility:"shared",
      entries:[{entry_id:"note-1",tag:"notes",body:"Saved <note>",priority:"low",visibility:"shared"}],
    };
    let markup, setup;
    const context=vm.createContext({
      window:{__PORTAL__:{team:typedFixture()},PortalDates:portalDates(),
        captureDialog:(_title,fields,_onSave,options)=>{markup=fields;setup=options.setup;}},
      localStorage:{getItem:()=>null},
    });
    const instrumented=source
      .replace(source.slice(source.indexOf("  function formDialog("),
        source.indexOf("  async function editAssignment(")),
        "  function formDialog(...args){ window.captureDialog(...args); }\n")
      .replace('let mode = "presentation"',`let mode = "${scenario==="shared-edit"?"presentation":"private"}"`)
      .replace("window.TeamUI =", "window.openLog = meetingDialog; window.TeamUI =");
    vm.runInContext(instrumented,context);
    context.window.openLog("test",current);
    assert.match(markup,/<details class="team-item-row team-notes-row" open>/);
    assert.match(markup,/<summary class="team-item-row-summary"><span class="badge">Notes<\/span>/);
    assert.match(markup,/<span class="team-notes-toggle" aria-hidden="true">/);
    assert.match(markup,/class="team-notes-expand">&#9656; Expand/);
    assert.match(markup,/class="team-notes-collapse">&#9662; Collapse/);
    if(current) assert.match(markup,/Saved &lt;note&gt;/);

    const field=value=>({
      value,listeners:{},
      addEventListener(type,handler){this.listeners[type]=handler;},
      fire(type){this.listeners[type]();},
    });
    const notes=field(current?.entries[0].body||"");
    const priority=field(current?.entries[0].priority||"medium");
    const preview={textContent:""};
    const priorityPreview={textContent:""};
    const noteRow={querySelector:selector=>({
      '[name="notes"]':notes,
      '[name="notes_priority"]':priority,
      ".team-item-row-title":preview,
      ".team-priority":priorityPreview,
    })[selector]};
    const rows={children:[],querySelectorAll:()=>[]};
    setup({
      querySelector:selector=>selector===".team-item-rows"?rows:noteRow,
      querySelectorAll:()=>[],
    });
    notes.value="Draft <text>\nsecond line";
    notes.fire("input");
    assert.equal(preview.textContent,notes.value);
    priority.value="critical";
    priority.fire("change");
    assert.equal(priorityPreview.textContent,"Priority: Critical");
    notes.value="";
    notes.fire("input");
    assert.equal(preview.textContent,"Add a short note…");
  }
});

test("log saves serialize typed items, notes and legacy entries exactly once", async () => {
  for(const scenario of ["create","private-edit","shared-edit"]){
    for(const includeTyped of [false,true]){
      const entries=[
        {entry_id:"note-1",tag:"notes",body:"Saved notes",priority:"low",visibility:"shared"},
        {entry_id:"legacy-1",tag:"custom",body:"Legacy entry",priority:"high",visibility:"shared"},
      ];
      const current=scenario==="create"?null:{
        meeting_id:"log-1",revision:"revision-1",title:"Topic",title_visibility:"shared",entries,
      };
      let save, markup, post;
      const data=typedFixture();
      const context=vm.createContext({
        window:{__PORTAL__:{team:data},PortalDates:portalDates(),
          captureDialog:(_title,fields,onSave)=>{markup=fields;save=onSave;}},
        localStorage:{getItem:()=>null},
        fetch:async(_url,options)=>{
          post=JSON.parse(options.body);
          return {ok:true,json:async()=>({result:{ok:true}})};
        },
      });
      const instrumented=source
        .replace(source.slice(source.indexOf("  function formDialog("),
          source.indexOf("  async function editAssignment(")),
          "  function formDialog(...args){ window.captureDialog(...args); }\n")
        .replace('let mode = "presentation"',`let mode = "${scenario==="shared-edit"?"presentation":"private"}"`)
        .replace("window.TeamUI =", "window.openLog = meetingDialog; window.TeamUI =");
      vm.runInContext(instrumented,context);
      context.window.openLog("test",current);
      assert.match(markup,/team-item-row team-notes-row/);

      const row=(values,dataset={})=>({
        dataset,
        querySelector:selector=>{
          const name=/^\[name="([^"]+)"\]$/.exec(selector)?.[1];
          return Object.hasOwn(values,name)?{value:values[name]}:null;
        },
      });
      const typed=includeTyped?[row({item_tag:"decision","f:decision":"Use DMA",
        priority:"critical",visibility:"shared"})]:[];
      const legacy=current?[row({tag:"custom",body:"Edited legacy",priority:"high",
        visibility:"shared"},{entryIndex:"0"})]:[];
      const notes=row({notes:"Edited notes"});
      const form={
        querySelector:selector=>selector===".team-notes-row"?notes:null,
        querySelectorAll:selector=>{
          if(selector===".team-item-rows .team-item-row") return typed;
          if(selector===".team-item-row") return [...typed,...legacy,notes];
          if(selector===".team-entry") return legacy;
          throw new Error(`Unexpected selector: ${selector}`);
        },
      };
      await save(new Map(Object.entries({
        notes:"Edited notes",notes_priority:"low",notes_visibility:"shared",
        meeting_date:"",session_type:"one_on_one",title:"Topic",title_visibility:"shared",
      })),form);
      assert.equal(post.action,scenario==="create"?"meeting-create":
        scenario==="shared-edit"?"meeting-shared-save":"meeting-save");
      assert.deepEqual(post.data.entries,[
        {...(current?{entry_id:"note-1"}:{}),tag:"notes",body:"Edited notes",priority:"low",visibility:"shared"},
        ...(includeTyped?[{tag:"decision",fields:{decision:"Use DMA"},priority:"critical",visibility:"shared"}]:[]),
        ...(current?[{entry_id:"legacy-1",tag:"custom",body:"Edited legacy",priority:"high",visibility:"shared"}]:[]),
      ]);
      if(current) assert.equal(post.data.revision,"revision-1");
      else assert.equal(post.data.member_key,"test");
    }
  }
});

test("Presentation Log exposes scoped edit while keeping each item collapsible and prioritized", () => {
  const data=typedFixture("shared");
  data.meetings=[{member_key:"test",meeting_id:"shared-log",title:"Shared topic",
    title_visibility:"shared",edit_token:"opaque",meeting_date:"2026-10-25",entries:[
      {entry_id:"low",tag:"decision",body:"Low decision",priority:"low",visibility:"shared"},
      {entry_id:"action",tag:"action",body:"Action",priority:"high",visibility:"shared"},
      {entry_id:"critical",tag:"decision",body:"Critical decision",priority:"critical",visibility:"shared"},
      {entry_id:"block",tag:"roadblock",body:"Blocker",priority:"medium",visibility:"shared"},
    ]}];
  const h=harness({},data); h.ui.navigate("member/test"); tab(h,"oneonone");
  const html=h.root.innerHTML;
  assert.match(html,/Edit shared log/);
  assert.match(html,/class="team-item-view"/);
  assert.match(html,/Priority: Critical/);
  const action=html.indexOf('data-item-type="action">Action item');
  const critical=html.indexOf('class="team-item-view-summary">Critical decision');
  const low=html.indexOf('class="team-item-view-summary">Low decision');
  const roadblock=html.indexOf('data-item-type="roadblock">Roadblock');
  assert.ok(action<critical);
  assert.ok(critical<low);
  assert.ok(low<roadblock);
  assert.doesNotMatch(html,/Private log item/);
});

test("Status update supports a standalone named activity without mutating task status", () => {
  const fieldCode=source.slice(source.indexOf("function itemFieldInput"),source.indexOf("function itemRowTitle"));
  assert.match(fieldCode,/field\.key==="activity"\?"standalone"/);
  assert.match(fieldCode,/field\.key==="status"\?"linked"/);
  assert.match(fieldCode,/data-status-mode="\$\{statusMode\}"/);
  assert.match(fieldCode,/no task \(standalone update\)/);
  const meetingCode=source.slice(source.indexOf("function meetingDialog"),source.indexOf("function findEntry"));
  assert.match(meetingCode,/const syncStatusFields=\(\)=>/);
  assert.match(meetingCode,/activity\.disabled=linked/);
  assert.match(meetingCode,/taskStatus\.disabled=!linked/);
});

test("Status activity is always visible and leads the collapsed row title", () => {
  const titleCode=source.slice(source.indexOf("function itemRowTitle"),source.indexOf("function itemRow("));
  // Collapsed title: task title when linked, else activity, else progress note.
  assert.match(titleCode,/fields\.assignment_id\?taskTitle\(fields\.assignment_id\):/);
  assert.match(titleCode,/fields\.activity\|\|fields\[spec\.summary\]\|\|"New item"/);
  const rowCode=source.slice(source.indexOf("function itemRow("),source.indexOf("function itemRowValue("));
  // Activity renders with the summary field, outside the "More details" section.
  assert.match(rowCode,/tag==="status"\?\["activity",spec\.summary\]:\[spec\.summary\]/);
  assert.match(rowCode,/detailFields=spec\.fields\.filter\(f=>!mainFields\.includes\(f\)&&f\.key!=="priority"&&/);
  assert.match(rowCode,/itemRowTitle\(tag,fields,entry\)/);
  const meetingCode=source.slice(source.indexOf("function meetingDialog"),source.indexOf("function findEntry"));
  // Live preview follows the same precedence and refreshes on activity input and task link changes.
  assert.match(meetingCode,/linkedTask\?taskTitle\(linkedTask\):\s*activity\|\|/);
  assert.match(meetingCode,/querySelectorAll\(".team-item-summary-field input,.team-item-summary-field textarea"\)\s*\.forEach\(el=>el\.addEventListener\("input",updatePreview\)\)/);
  assert.match(meetingCode,/taskStatus\.value="";\s*\}\s*updatePreview\(\);/);
});

test("member log visibility controls default to Presentation and preserve saved values", () => {
  const itemRow=source.slice(source.indexOf("function itemRow("),source.indexOf("function itemRowValue("));
  const itemValue=source.slice(source.indexOf("function itemRowValue("),source.indexOf("function meetingDialog("));
  const meeting=source.slice(source.indexOf("function meetingDialog("),source.indexOf("function findEntry("));
  assert.match(itemRow,/entry\?\.visibility\|\|spec\.default_visibility\|\|"shared"/);
  assert.match(itemValue,/vis\?\.value\|\|"shared"/);
  assert.match(meeting,/visibilitySelect\("visibility",entry\.visibility\|\|"shared"\)/);
  assert.match(meeting,/visibilitySelect\("title_visibility",current\?\.title_visibility\|\|"shared","Topic visibility"\)/);
  assert.match(meeting,/visibilitySelect\("notes_visibility",note\?\.visibility\|\|"shared","Notes visibility"\)/);
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
  for(const [field,value] of [["from","25/10/2026"],["to","26/10/2026"]]) {
    const input=h.root.querySelectorAll("[data-team-date]").find(b=>b.dataset.teamDate===field);
    input.value=value; input.onchange();
  }
  assert.match(h.root.innerHTML,/value="25\/10\/2026"/);
  assert.match(h.root.innerHTML,/value="26\/10\/2026"/);
  assert.equal(rowIds(h).length,2);
  choose(h,"source","github"); choose(h,"kind","pr");
  assert.equal(rowIds(h).length,1);
  search(h,"Last"); assert.equal(rowIds(h).length,1);
  tab(h,"oneonone"); search(h,"roadmap");
  assert.deepEqual(rowIds(h),["m2"]);
  const to=h.root.querySelectorAll("[data-team-date]").find(b=>b.dataset.teamDate==="to");
  to.value="25/10/2026"; to.onchange(); assert.equal(rowIds(h).length,0);
  tab(h,"tasks"); assert.deepEqual(rowIds(h),["a3"]);
  assert.equal(h.root.querySelectorAll("[data-team-search]")[0].value,"cleanup");
  tab(h,"activity");
  const from=h.root.querySelectorAll("[data-team-date]").find(b=>b.dataset.teamDate==="from");
  from.value="27/10/2026"; from.onchange();
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

function trackedFixture(audience="shared"){
  const data=typedFixture(audience);
  data.item_statuses=["unspecified","open","in-progress","blocked","done","cancelled","deferred"];
  data.items=[
    {item_id:"item-task",member_key:"test",item_type:"action",body:"Tracked task",status:"todo",
      priority:"high",owner_key:"test",owner:"member",target_date:"2026-11-12",follow_up:false,
      assignment_id:"assignment-1",assignment_revision:"task-r",revision:"item-task-r",fields:{title:"Tracked task"},
      occurrences:[],updates:[]},
    {item_id:"item-decision",member_key:"test",item_type:"decision",body:"Tracked decision",status:"open",
      priority:"medium",owner:null,target_date:null,follow_up:true,revision:"item-decision-r",
      fields:{decision:"Tracked decision"},occurrences:[{meeting_id:"t2",meeting_date:"2026-10-26",
        title:"Linked",body:"Discussed the decision"}],updates:[]},
    {item_id:"item-notes",member_key:"test",item_type:"notes",body:"Tracked notes",status:"done",
      priority:"low",follow_up:false,revision:"item-notes-r",fields:null,occurrences:[],updates:[]},
    {item_id:"item-other",member_key:"other",item_type:"notes",body:"Other tracked item",status:"open",
      priority:"medium",follow_up:true,revision:"item-other-r",fields:null,occurrences:[],updates:[]},
  ];
  return data;
}

test("Items replaces Tasks and scopes all tracked types, filters and selections", () => {
  const h=harness({},trackedFixture());
  h.ui.navigate("member/test");
  assert.match(h.root.innerHTML,/data-member-tab="tasks">Items</);
  assert.match(h.root.innerHTML,/Tracked task/);
  assert.match(h.root.innerHTML,/Tracked decision/);
  assert.match(h.root.innerHTML,/Tracked notes/);
  assert.doesNotMatch(h.root.innerHTML,/Other tracked item/);
  assert.deepEqual(rowIds(h),["item-task","item-decision","item-notes"]);
  choose(h,"follow_up","true");
  assert.deepEqual(rowIds(h),["item-decision"]);
  h.root.querySelector("[data-team-clear]").onclick();
  h.root.querySelector("[data-team-open-only]").checked=true;
  h.root.querySelector("[data-team-open-only]").onchange();
  assert.deepEqual(rowIds(h),["item-task","item-decision"]);
  search(h,"item-task");
  assert.deepEqual(rowIds(h),["item-task"]);
});

test("tracked log renders current status and carried-follow-up source without legacy checkbox duplication", () => {
  const data=trackedFixture();
  data.log_item_types={...ITEM_TYPES,discussion:{label:"Discussion point",summary:"topic",fields:[
    {key:"topic",label:"Topic",input:"text"},{key:"follow_up",label:"Follow-up needed",input:"checkbox"},
  ]}};
  data.meetings[0].entries=[{entry_id:"e",tag:"discussion",body:"Discussion",fields:{topic:"Discussion",follow_up:true},
    tracking:{status:"done",follow_up:false},follow_up_from:"2026-10-18",visibility:"shared"}];
  const h=harness({},data);
  h.ui.navigate("member/test"); tab(h,"oneonone");
  assert.match(h.root.innerHTML,/Follow-up from 18\/10\/2026/);
  assert.match(h.root.innerHTML,/class="chip">done/);
  assert.doesNotMatch(h.root.innerHTML,/Follow-up needed:|Follow up next 1:1/);
});

function instrumentTracking(data, captureDialog=()=>{}, fetch=async()=>({ok:true,json:async()=>({result:{ok:true}})})){
  const context=vm.createContext({
    window:{__PORTAL__:{team:data},PortalDates:portalDates(),captureDialog},
    localStorage:{getItem:()=>null},fetch,
  });
  const instrumented=source
    .replace(source.slice(source.indexOf("  function formDialog("),source.indexOf("  async function editAssignment(")),
      "  function formDialog(...args){ window.captureDialog(...args); }\n")
    .replace('let mode = "presentation"','let mode = "private"')
    .replace("window.TeamUI =",`window.tracking={controls:trackingControls,values:trackingValues,
      row:itemRow,serialize:itemRowValue,open:meetingDialog,newMeeting,editItem}; window.TeamUI =`);
  vm.runInContext(instrumented,context);
  return context.window.tracking;
}

function trackingRow(values,dataset={}){
  return {dataset,querySelector:selector=>{
    const name=/^\[name="([^"]+)"\]$/.exec(selector)?.[1];
    if(!Object.hasOwn(values,name)) return null;
    return name==="follow_up"?{checked:values[name]}:{value:values[name]};
  }};
}

test("tracking serialization preserves item identity, changed status, due and follow-up without replaying unchanged state", () => {
  const tracking=instrumentTracking(trackedFixture("private"));
  const original={item_id:"item-decision",item_revision:"rev",tracking:{status:"open",owner:null,target_date:null}};
  const values=tracking.values(trackingRow({follow_up:true,tracking_status:"open",tracking_owner:"",tracking_due:""}),original);
  assert.equal(values.item_id,"item-decision");
  assert.equal(values.item_revision,"rev");
  assert.equal(values.follow_up,true);
  assert.equal(values.tracking_status,undefined);
  assert.equal(values.tracking_owner,undefined);
  assert.equal(values.tracking_due,undefined);
  const changed=tracking.values(trackingRow({follow_up:false,tracking_status:"done",
    tracking_owner:"member",tracking_due:"12/11/2026"}),original);
  assert.equal(changed.tracking_status,"done");
  assert.equal(changed.tracking_due,"2026-11-12");
  assert.throws(()=>tracking.values(trackingRow({follow_up:true,tracking_status:"open",tracking_due:"31/02/2026"})),/valid due date/);
});

test("prepared rows show follow-up marks and serialize typed and freeform items using the same identity", () => {
  const tracking=instrumentTracking(trackedFixture("private"));
  const original={entry_id:"draft-note",item_id:"item-notes",item_revision:"rev",tag:"notes",body:"Carried note",
    tracking_status:"open",tracking_owner:null,tracking_due:null,follow_up:true,follow_up_from:"2026-11-01"};
  const html=tracking.row("test","notes",original);
  assert.match(html,/data-follow-up-item="item-notes"/);
  assert.match(html,/Follow-up from 01\/11\/2026/);
  assert.match(html,/name="follow_up" checked/);
  assert.match(html,/name="body"/);
  const entry=tracking.serialize(trackingRow({item_tag:"notes",body:"Discussed note",priority:"medium",
    visibility:"private",follow_up:false,tracking_status:"open",tracking_owner:"",tracking_due:""},
    {entryId:"draft-note",itemGeneric:"true"}),{"draft-note":original});
  assert.equal(entry.item_id,"item-notes");
  assert.equal(entry.item_revision,"rev");
  assert.equal(entry.body,"Discussed note");
  assert.equal(entry.follow_up,false);
  assert.equal(entry.fields,undefined);
});

test("new 1:1 preparation uses the owner API and failures do not become empty successful drafts", async () => {
  const posts=[],dialogs=[];
  const data=trackedFixture("private");
  const tracking=instrumentTracking(data,(...args)=>dialogs.push(args),async(_url,options)=>{
    posts.push(JSON.parse(options.body));
    return {ok:true,json:async()=>({result:{entity:{entries:[],meeting_date:"2026-11-05"}}})};
  });
  await tracking.newMeeting("test");
  assert.equal(posts[0].action,"meeting-prepare");
  assert.equal(posts[0].data.member_key,"test");
  assert.equal(dialogs.length,1);
  const failed=instrumentTracking(data,()=>{throw new Error("Must not open");},async()=>({
    ok:false,json:async()=>({error:"Preparation failed"}),
  }));
  await assert.rejects(failed.newMeeting("test"),/Preparation failed/);
});

test("item edit updates common tracking through the owner API rather than creating a task", async () => {
  let save,post;
  const tracking=instrumentTracking(trackedFixture("private"),(_title,_html,onSave)=>{save=onSave;},
    async(_url,options)=>{
      post=JSON.parse(options.body);
      return {ok:true,json:async()=>({result:{ok:true}})};
    });
  tracking.editItem("item-decision");
  await save(new Map([["priority","high"],["visibility","shared"]]),trackingRow({
    follow_up:false,tracking_status:"done",tracking_owner:"member",tracking_due:"12/11/2026",
  }));
  assert.equal(post.action,"item-set");
  assert.equal(post.data.id,"item-decision");
  assert.equal(post.data.revision,"item-decision-r");
  assert.equal(post.data.status,"done");
  assert.equal(post.data.target_date,"2026-11-12");
  assert.equal(post.data.follow_up,false);
});
