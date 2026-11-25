const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source=fs.readFileSync(path.join(__dirname,"..","assets","portal.js"),"utf8");
function block(start,end){
  const first=source.indexOf(start);
  const last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first);
  return source.slice(first,last);
}
function harness(){
  const elements={};
  for(const id of ["updates-open","updates-panel","updates-summary","updates-list","updates-detail"])
    elements[id]={hidden:true,textContent:"",innerHTML:"",setAttribute(){}};
  const popups=[];
  const context=vm.createContext({
    document:{getElementById:id=>elements[id],addEventListener(){}},
    SERVED:true,localDmyHms:()=>"22/11/2026 12:00:07",
    esc:value=>String(value).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll('"',"&quot;"),
    setActionsOpen(){},notifyShow:event=>popups.push(event),
    DOMAIN_SECTION:{tasks:"work",team:"team",prs:"prs",workspaces:"ws"},
  });
  vm.runInContext(block("// ---- update summaries","function notifyCard("),context);
  vm.runInContext("globalThis.updates=UPDATES",context);
  return {context,elements,popups};
}

test("job status notifications reuse event delivery and deduplicate stream replay",()=>{
  const notifications=[], context=vm.createContext({
    NOTIFY:{seq:0,instance:null},notifyShow:event=>notifications.push(event),
    rcLoadOpen(){},syncLiveSettings(){},liveCfg:()=>({enabled:true}),
    UPDATE_LABELS:{},renderUpdates(){},queueDomainRefresh(){},
  });
  vm.runInContext(block("function parseEventSeq(", "function streamState("),context);
  vm.runInContext(block("function notifyEvent(", "function processReset("),context);
  for(const [seq,status] of [[1,"running"],[2,"cancelled"],[2,"cancelled"]])
    context.processStreamEvent({lastEventId:`server:${seq}`,
      data:JSON.stringify({kind:"machine_jobs",title:`Build ${status}`,link:"#vm"})});
  assert.deepEqual(notifications.map(row=>row.title),["Build running","Build cancelled"]);
});

test("Notifications Settings renders a live nonterminal toggle",()=>{
  const context=vm.createContext({esc:String,termPresets:()=>[],termSettings:()=>({}),
    SETTINGS:{defaults:{notify:{status_updates:true}}}});
  vm.runInContext(block("const SETTING_GROUPS=", "let groupsMsg="),context);
  const html=vm.runInContext(`settingField("notify",
    SETTING_GROUPS.find(([group])=>group==="notify")[2].find(([key])=>key==="status_updates"),
    {status_updates:true},"")`,context);
  assert.match(html,/type="checkbox"/);
  assert.match(html,/data-g="notify"/);
  assert.match(html,/data-k="status_updates"/);
  assert.match(html,/ checked/);
  assert.match(html,/Nonterminal status updates/);
});

test("update timestamps include seconds",()=>{
  const context=vm.createContext({});
  vm.runInContext(block("function localDmyHm(","function briefHtml("),context);
  assert.equal(context.localDmyHms(new Date(2026,10,22,12,5,7).getTime()),"22/11/2026 12:05:07");
});

test("summaries show real added, changed and removed records, not revision churn",()=>{
  const {context}=harness();
  const before=context.updateProjection("tasks",[
    {id:"T-1",title:"Fix boot",status:"todo",revision:"old"},
    {id:"T-2",title:"Old work",status:"done"},
  ]);
  const after=context.updateProjection("tasks",[
    {id:"T-1",title:"Fix boot",status:"done",revision:"new"},
    {id:"T-3",title:"New work",status:"todo"},
  ]);
  const diff=context.updateDiff("tasks",before,after);
  assert.equal(diff.summary,"Tasks: 1 added, 1 changed, 1 removed");
  assert.ok(diff.details.includes("T-1 Fix boot: status: todo → done"));
  const churn=context.updateDiff("tasks",after,context.updateProjection("tasks",[
    {id:"T-1",title:"Fix boot",status:"done",revision:"newer"},
    {id:"T-3",title:"New work",status:"todo"},
  ]));
  assert.equal(churn.changed,false);
});

test("PR details identify the PR and changed check status",()=>{
  const {context}=harness();
  const data=checks=>({features:[{id:"F-1",name:"Driver",prs:[
    {repo:"firmware",number:42,title:"Fix driver",checks_fail:checks},
  ]}]});
  const diff=context.updateDiff("prs",context.updateProjection("prs",data(1)),
    context.updateProjection("prs",data(0)));
  assert.equal(diff.summary,"PRs: 1 changed");
  assert.match(diff.details[0],/firmware#42 Fix driver: checks fail: 1 → 0/);
});

test("workspace ownership changes are described but status-check timestamps are ignored",()=>{
  const {context}=harness();
  const data=(holder,checked)=>({roster:[{id:"WS-1",slot:"one",label:"Review slot",
    status:{checked,clean:true,safe:true}}],slots:{one:{holder}}});
  const before=context.updateProjection("workspaces",data("T-1","old"));
  const timestamp=context.updateDiff("workspaces",before,
    context.updateProjection("workspaces",data("T-1","new")));
  assert.equal(timestamp.changed,false);
  const changed=context.updateDiff("workspaces",before,
    context.updateProjection("workspaces",data("T-2","new")));
  assert.match(changed.details[0],/holder: T-1 → T-2/);
});

test("Team update history never includes private record content",()=>{
  const {context}=harness();
  const before=context.updateProjection("team",{items:[],members:[]});
  const after=context.updateProjection("team",{
    items:[{title:"Sensitive note",description:"Private details"}],
    members:[{display_name:"Private name"}],
  });
  const diff=context.updateDiff("team",before,after);
  assert.match(diff.summary,/Team: 2 changed/);
  assert.doesNotMatch(JSON.stringify(diff),/Sensitive|Private details|Private name/);
});

test("pending events coalesce, then produce specific popup and selectable escaped details",()=>{
  const {context,elements,popups}=harness();
  const before=[{id:"T-1",title:"<img src=x>",status:"todo"}];
  context.updates.baseline.set("tasks",context.updateProjection("tasks",before));
  context.observeDomainUpdate("tasks");
  context.observeDomainUpdate("tasks");
  assert.equal(context.updates.rows.length,1);
  assert.equal(popups.length,0);
  context.finishDomainUpdate("tasks",[{...before[0],status:"done"}]);
  assert.equal(popups.length,1);
  assert.equal(popups[0].title,"Tasks: 1 changed");
  assert.match(popups[0].body,/status: todo → done/);
  assert.equal(context.updates.rows[0].state,"Updated");
  assert.match(elements["updates-detail"].innerHTML,/&lt;img/);
  assert.doesNotMatch(elements["updates-detail"].innerHTML,/<img/);
});

test("history stays bounded and opening Updates does not fetch domain data",()=>{
  const {context,elements}=harness();
  for(let i=0;i<60;i++){
    context.observeDomainUpdate("tasks");
    context.updates.rows[0].state="Updated";
  }
  assert.equal(context.updates.rows.length,50);
  context.setUpdatesOpen(true);
  assert.equal(elements["updates-panel"].hidden,false);
  context.setUpdatesOpen(false);
  assert.equal(elements["updates-panel"].hidden,true);
});

test("Automatic updates settings bound history, details and popups",()=>{
  const {context,popups}=harness();
  context.SETTINGS={settings:{live_updates:{history_limit:10,detail_limit:2,popups:false,auto_reload:false}}};
  for(let i=0;i<15;i++){
    context.observeDomainUpdate("tasks");
    context.updates.rows[0].state="Updated";
  }
  assert.equal(context.updates.rows.length,10);
  assert.match(context.updates.rows[0].details[0],/Use Refresh/);
  const rows=n=>Array.from({length:n},(_,i)=>({id:`T-${i}`,title:"x",status:"todo"}));
  const diff=context.updateDiff("tasks",{},context.updateProjection("tasks",rows(5)));
  assert.deepEqual([diff.details.length,diff.omitted],[2,3]);
  context.updates.baseline.set("tasks",context.updateProjection("tasks",[]));
  context.observeDomainUpdate("tasks");
  context.finishDomainUpdate("tasks",rows(1));
  assert.equal(context.updates.rows[0].summary,"Tasks: 1 added");
  assert.equal(popups.length,0);
});
