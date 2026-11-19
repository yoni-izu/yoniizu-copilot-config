// Team Portal client. Served from /assets/portal.js by `portal.py serve`,
// and inlined into the static `portal.py dashboard` export. Every payload arrives
// through the one `window.__PORTAL__` bootstrap object the page defines just
// above this script; the fetches in init() then refresh them when served.
const BOOT = window.__PORTAL__ || {};
function validPortalDate(year, month, day){
  if(year<1||year>9999||month<1||month>12) return false;
  const leap=year%4===0&&(year%100!==0||year%400===0);
  const days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  return day>=1&&day<=days[month-1];
}
function formatPortalDate(value){
  const raw=String(value??"").trim();
  const match=/^(\d{4})-(\d{2})-(\d{2})(.*)$/.exec(raw);
  if(!match) return raw||"—";
  const [,yearText,monthText,dayText,suffix]=match;
  const year=Number(yearText), month=Number(monthText), day=Number(dayText);
  if(!validPortalDate(year,month,day)) return raw||"—";
  const date=`${dayText}/${monthText}/${yearText}`;
  if(!suffix) return date;
  const time=/^[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?$/.exec(suffix);
  return time?`${date} ${time[1]}${time[2]||""}`:raw;
}
function formatPortalDateInput(value){
  const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value??"").trim());
  if(!match || !validPortalDate(Number(match[1]),Number(match[2]),Number(match[3]))) return "";
  return `${match[3]}/${match[2]}/${match[1]}`;
}
function parsePortalDateInput(value){
  const raw=String(value??"").trim();
  if(!raw) return "";
  const match=/^(\d{2})\/(\d{2})\/(\d{4})$/.exec(raw);
  if(!match) return null;
  const [,dayText,monthText,yearText]=match;
  const year=Number(yearText), month=Number(monthText), day=Number(dayText);
  if(!validPortalDate(year,month,day)) return null;
  return `${yearText}-${monthText}-${dayText}`;
}
window.PortalDates=Object.freeze({
  display:formatPortalDate,
  input:formatPortalDateInput,
  parse:parsePortalDateInput,
});
let TASKS = BOOT.tasks || [];
let WORKSPACES = BOOT.workspaces || {};
const selectedTaskIds = new Set();
let taskSelectionMessage = "";
let BUGS = BOOT.bugs || {};
let TRIAGE = BOOT.triage || {};
let OVERVIEW = BOOT.overview || {};
let PRS = BOOT.prs || {};
let RAID = BOOT.raid || {};
let VMLOG = BOOT.vmlog || {};
// Oded's CI/CD pipeline explorer — linked to, never called.
const PE_URL = (BOOT.explorer||{}).url || "";
const PE_VIEWS=[["/open-prs","Open PRs & merge queue"],["/pipeline-history","Pipeline history"],
  ["/queue","Integration queue"],["/branch-timeline","Branch timeline"],
  ["/manifest-versions","Manifest versions"],["/bkc","BKC firmware"],["/diagnostics","Diagnostics"]];
function peLink(path){ return PE_URL ? PE_URL+path : ""; }
function pePr(url){ return url ? peLink("/pr?url="+encodeURIComponent(url)) : ""; }
function peRun(url){ return url ? peLink("/workflow?url="+encodeURIComponent(url)) : ""; }
// An opaque no-cors fetch resolves when the explorer answers and rejects when
// it is down, so the sidebar dot can say whether its links will work.
function peProbe(){
  const dot=document.querySelector(".pe-dot:not(.gf-dot)"); if(!dot||!PE_URL) return;
  fetch(PE_URL+"/diagnostics",{mode:"no-cors",cache:"no-store"})
    .then(()=>{ dot.classList.add("up"); dot.title="explorer is up"; })
    .catch(()=>{ dot.classList.add("down");
      dot.title="explorer not reachable \u2014 systemctl --user start pipeline-explorer"; });
}
// Grafana + Platform portal: linked to; Grafana health is probed via the server.
const GRAFANA = BOOT.grafana || {};
const PLATFORM = BOOT.platform || {};
function gfDash(key, extra){
  const d=(GRAFANA.dashboards||{})[key]; return d ? d.url+(extra||"") : "";
}
function gfProbe(){
  const dot=document.querySelector(".gf-dot"); if(!dot||!SERVED) return;
  fetch("/api/grafana-health",{cache:"no-store"}).then(r=>r.json()).then(h=>{
    dot.classList.add(h.ok?"up":"down");
    dot.title=h.ok?("Grafana "+(h.version||"up")):("Grafana not reachable: "+(h.error||"unhealthy"));
  }).catch(()=>{ dot.classList.add("down"); dot.title="Grafana health unknown"; });
}
// Offset between this browser's clock and the server's, measured ONCE when a
// payload arrives. It must never be recomputed per tick: Date.now()-now_ts
// grows exactly as fast as the clock, so using it live cancels the elapsed
// time out and freezes the counter.
let vmSkewSec = (VMLOG && VMLOG.now_ts) ? (Date.now()/1000 - VMLOG.now_ts) : 0;
function vmAdoptPayload(d){
  if(d && d.now_ts) vmSkewSec = Date.now()/1000 - d.now_ts;
  VMLOG = d;
  return d;
}
let SERVED = false;
const JIRA_BASE = BOOT.jira_base || "";
const STATUS_ORDER = ["in-progress","blocked","todo","backlog","done","cancelled"];
const PRI_ORDER = ["critical","high","medium","low"];
const CPX_ORDER = ["trivial","small","medium","large","epic"];
const CATEGORIES = ["development","code-review","planning","testing","track","misc"];
const RV_ORDER = ["pending","in-progress","commented","changes-requested","waiting-for-author","waiting-for-re-review","approved","merged"];
// Per-PR review statuses that can be *set* from the editor (excludes the
// consolidated-only "waiting-for-author").
const RV_SETTABLE = RV_ORDER.filter(s=>s!=="waiting-for-author");
const TABLE_STD_KEY = "portal-table-std-v1";
const TABLE_STD_SKIP_SORT = new Set(["tbl", "bug-tbl"]);
const tableStd = readTableStd();

function readTableStd(){
  try{ return JSON.parse(localStorage.getItem(TABLE_STD_KEY)||"{}"); }
  catch{ return {}; }
}
function saveTableStd(){
  try{ localStorage.setItem(TABLE_STD_KEY, JSON.stringify(tableStd)); }catch(_){}
}
function tableStdKey(table){ return table.dataset.tableKey || table.id || "table"; }
function ensureSortMarker(th){
  let marker = th.querySelector(".table-sort-ind");
  if(!marker){
    marker = document.createElement("span");
    marker.className = "table-sort-ind";
    th.appendChild(marker);
  }
  return marker;
}
function rowIsDetail(row){
  return row.classList.contains("detail") || row.classList.contains("prf-detail")
    || row.classList.contains("vm-detail")
    || !!row.dataset.for;
}
function groupedRows(tbody){
  const groups=[]; let current=null;
  [...tbody.rows].forEach(row=>{
    if(rowIsDetail(row) && current){ current.extra.push(row); return; }
    current={main:row,extra:[]}; groups.push(current);
  });
  return groups;
}
function parseSort(raw,type){
  if(type==="number"){
    const n = Number(String(raw).replace(/[^\d.+-]/g,""));
    return Number.isFinite(n) ? n : Number.NEGATIVE_INFINITY;
  }
  if(type==="date"){
    const t = Date.parse(String(raw));
    return Number.isFinite(t) ? t : Number.NEGATIVE_INFINITY;
  }
  return String(raw||"").toLowerCase();
}
function sortTableRows(table, index, dir, type){
  const tbody = table.tBodies[0];
  if(!tbody) return;
  const groups = groupedRows(tbody).map(group=>{
    const cell = group.main.cells[index];
    const raw = (cell && (cell.dataset.sortValue || cell.textContent)) ? (cell.dataset.sortValue || cell.textContent).trim() : "";
    return {group, value:parseSort(raw,type)};
  });
  groups.sort((a,b)=>{
    if(a.value===b.value) return 0;
    return (a.value > b.value ? 1 : -1) * dir;
  });
  groups.forEach(({group})=>{
    tbody.appendChild(group.main);
    group.extra.forEach(row=>tbody.appendChild(row));
  });
}
function wireResizer(table, th, index, pref){
  let grip = th.querySelector(".col-resizer");
  if(!grip){
    grip = document.createElement("span");
    grip.className = "col-resizer";
    grip.setAttribute("aria-hidden","true");
    th.appendChild(grip);
  }
  if(grip.dataset.bound==="1") return;
  grip.dataset.bound="1";
  grip.addEventListener("pointerdown", event=>{
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidth = th.getBoundingClientRect().width;
    table.classList.add("table-resizing");
    const move = ev=>{
      const width = Math.max(60, Math.round(startWidth + ev.clientX - startX));
      th.style.width = width+"px";
      table.style.tableLayout = "fixed";
      pref.widths = pref.widths || {};
      pref.widths[index] = width;
      saveTableStd();
    };
    const up = ()=>{
      table.classList.remove("table-resizing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}
function applyTableStandards(scope=document){
  (scope.querySelectorAll ? scope : document).querySelectorAll("table").forEach(table=>{
    if(table.closest(".md")) return;
    const key = tableStdKey(table);
    const pref = tableStd[key] = tableStd[key] || {};
    const head = table.tHead && table.tHead.rows && table.tHead.rows[0];
    if(!head) return;
    [...head.cells].forEach((th,index)=>{
      const savedIndex=th.dataset.stdIndex===undefined?index:Number(th.dataset.stdIndex);
      if(Number.isInteger(savedIndex)&&savedIndex>=0&&pref.widths&&pref.widths[savedIndex]){
        table.style.tableLayout = "fixed";
        th.style.width = pref.widths[savedIndex]+"px";
      }
      if(Number.isInteger(savedIndex)&&savedIndex>=0){
        wireResizer(table, th, savedIndex, pref);
      }
      const blocked = th.dataset.noSort!==undefined
        || !!th.querySelector("input,select,button")
        || TABLE_STD_SKIP_SORT.has(table.id);
      if(blocked){
        th.classList.add("th-nosort");
        const marker=th.querySelector(".table-sort-ind"); if(marker) marker.textContent="";
        return;
      }
      th.classList.add("th-sortable");
      ensureSortMarker(th);
      if(th.dataset.stdSortBound==="1") return;
      th.dataset.stdSortBound="1";
      th.addEventListener("click", event=>{
        if(event.target.closest(".col-resizer")) return;
        const sort = pref.sort || {index,dir:1};
        sort.dir = sort.index===index ? -sort.dir : 1;
        sort.index = index;
        pref.sort = sort;
        saveTableStd();
        applyTableStandards(table.parentElement || document);
      });
    });
    if(pref.sort){
      const th = head.cells[pref.sort.index];
      if(th && !th.classList.contains("th-nosort")){
        sortTableRows(table, pref.sort.index, pref.sort.dir, th.dataset.sortType || "text");
      }
    }
    [...head.cells].forEach((th, idx)=>{
      const marker=th.querySelector(".table-sort-ind");
      if(marker){
        marker.textContent = pref.sort && pref.sort.index===idx ? (pref.sort.dir>0?" ▲":" ▼") : "";
      }
    });
  });
}
window.PortalTableUI = {apply: applyTableStandards};

// Which workspaces are tracked, and how they are grouped, is user-editable and
// lives in the workspace-manager store — the client renders whatever the server
// sends rather than carrying its own roster.
function wsRoster(){ return (WORKSPACES&&WORKSPACES.roster)||[]; }
function wsGroups(){ return (WORKSPACES&&WORKSPACES.groups)||[]; }
function wsAllows(workspace, activity){
  const purpose=workspace.purpose||"general";
  const uses=workspace.allowed_uses||(WORKSPACES.purpose_uses||{})[purpose]||
    (purpose==="general"?["*"]:[]);
  return uses.includes("*")||uses.includes(activity);
}
function wsPurposeOptions(selected){
  return Object.entries(WORKSPACES.purposes||{general:"General"}).map(([id,label])=>
    `<option value="${esc(id)}"${id===selected?" selected":""}>${esc(label)}</option>`).join("");
}
function wsPurposeLabel(workspace){
  const purpose=workspace.purpose||"general";
  return (WORKSPACES.purposes||{})[purpose]||purpose;
}
function wsTaskAllows(workspace, task){
  return wsAllows(workspace, task.category==="code-review"?"review":"development");
}
let wsPurposeFilter=[];
let wsGridCols = 3;   // Workspace State grid: 3 columns (3×2) or 2 columns (2×3)

const COLUMNS = [
  {key:"id",      label:"ID",        on:true},
  {key:"title",   label:"Title / Feature", on:true},
  {key:"category",label:"Category",  on:true},
  {key:"priority",label:"Priority",  on:true},
  {key:"status",  label:"Status",    on:true},
  {key:"reviewstatus",label:"Review", on:false},
  {key:"prs",     label:"PRs",       on:false},
  {key:"author",  label:"Author",    on:false},
  {key:"complexity",label:"Cx",      on:false},
  {key:"cost",    label:"Est",       on:false},
  {key:"jira",    label:"Jira",      on:false},
  {key:"workspace",label:"WS",       on:false},
  {key:"age",     label:"Age",       on:true},
  {key:"worked",  label:"Worked",    on:false},
];

// Column key -> how to extract a comparable value
function sortVal(t,key){
  switch(key){
    case "id": return t.id||"";
    case "title": return (t.title||"").toLowerCase();
    case "category": return t.category||"";
    case "priority": return idx(PRI_ORDER,t.priority);
    case "status": return idx(STATUS_ORDER,t.status);
    case "complexity": return idx(CPX_ORDER,t.complexity);
    case "cost": return t.cost_hours||0;
    case "jira": return t.jira||"~";
    case "workspace": return t.workspace||"~";
    case "age": return t.created||"";        // ascending = oldest first
    case "created": return t.created||"";
    case "worked": return lastWorked(t)||"";
    case "reviewstatus": { const p=prs(t); return p.length?idx(RV_ORDER,consolidateReviewStatus(p)):99; }
    case "prs": { const p=prs(t); return p.length?(p[0].repo+"#"+p[0].pr_number):"~"; }
    case "author": { const p=prs(t); return p.length?(p[0].author||""):"~"; }
  }
  return "";
}
function idx(o,v){ const i=o.indexOf(v); return i<0?99:i; }

// Sort state: header clicks set this; dropdown also sets it.
const sortState = {key:"priority", dir:1};   // dir 1=asc, -1=desc

function daysSince(iso){ if(!iso) return null;
  const d=new Date(iso+"T00:00:00"); if(isNaN(d)) return null;
  return Math.floor((Date.now()-d)/(864e5)); }
function lastWorked(t){
  const cands=[]; if(t.updated) cands.push(t.updated);
  (t.notes||[]).forEach(n=>{ const m=/^(\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?:/.exec(n); if(m) cands.push(m[1]); });
  if(!cands.length) return null;
  return cands.sort().slice(-1)[0];
}
function workedLabel(t){ const iso=lastWorked(t); if(!iso) return "—";
  const n=daysSince(iso); return n===0?"today":n+"d ago"; }
function prs(t){ return t.reviews && t.reviews.length ? t.reviews : (t.review?[t.review]:[]); }
// Collapse per-PR review statuses into one overall task review status.
// Mirrors consolidate_review_status() in the Python CLI; lives in pulse.js.
function consolidateReviewStatus(pl){ return PortalPulse.consolidateReviewStatus(pl); }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

// ---- workspace alignment (manual-review checkout) progress banner ----
// Tones map to classes in portal.css so the banner follows the theme; the
// per-repo rows are the only place that needs a tone other than ok/warn/bad.
const ALIGN_ICONS={"checked-out":"✓","skipped-dirty":"⚠","approval-required":"⚠","error":"✕","not-found":"✕","already":"✓"};
function alignRepoRow(r){
  const ic=r.icon||ALIGN_ICONS[r.action]||"·";
  const tone=r.tone||(r.action==="checked-out"?"ok":(["skipped-dirty","approval-required"].includes(r.action)?"warn":"bad"));
  const tag=r.role==="review"?' <b class="ab-tag">[PR]</b>':"";
  return `<div class="ab-row"><span class="ab-${tone}">${ic}</span> `
    +`<code>${esc(r.name)}</code>${tag} — ${esc(r.detail||r.action)}</div>`;
}
const WS_STEP_ICONS={done:"\u2713",error:"\u2715",running:"\u25b6"};
function wsStepRow(st){
  const tone=st.state==="done"?"ok":(st.state==="error"?"bad":"info");
  const count=st.total?` <span class="ab-count">${st.done||0}/${st.total}</span>`:"";
  const live=st.state==="running"&&(st.detail||st.current)
    ?`<div class="ab-detail"><code>${esc(st.detail||st.current)}</code></div>`:"";
  const errs=(st.errors||[]).length
    ?`<details class="ab-errs"><summary class="ab-bad">${st.errors.length} repo failure(s)</summary>`
      +`<div>${esc(wsErrorSummary(st.errors.map(e=>e.replace(/^[^:]*: /,"")).join("; ")))}</div>`
      +`<ul>${st.errors.map(e=>`<li><code>${esc(e)}</code></li>`).join("")}</ul></details>`:"";
  return `<div class="ab-row ab-step ab-step-${esc(st.state)}"><span class="ab-${tone}">${WS_STEP_ICONS[st.state]||"\u00b7"}</span> `
    +`${esc(st.label)}${count}${live}${errs}</div>`;
}
const ACTION_UI={jobs:[],selected:null,dismissed:false,refreshing:false,open:false,cleared:new Set()};
// "Clear finished" only hides rows in this browser; the queue store is untouched.
const AQ_CLEARED_KEY="portal.actionQueue.cleared";
const AQ_ACTIVE=["queued","running","approval_required"];
try{ ACTION_UI.cleared=new Set(JSON.parse(localStorage.getItem(AQ_CLEARED_KEY)||"[]")); }catch(_){}
function saveClearedActions(){
  try{ localStorage.setItem(AQ_CLEARED_KEY,JSON.stringify([...ACTION_UI.cleared])); }catch(_){}
}
function visibleActions(actions){
  return actions.filter(job=>AQ_ACTIVE.includes(job.status)||!ACTION_UI.cleared.has(job.id));
}
function clearFinishedActions(){
  ACTION_UI.jobs.filter(job=>!AQ_ACTIVE.includes(job.status)).forEach(job=>ACTION_UI.cleared.add(job.id));
  saveClearedActions();
  renderActionQueue({actions:ACTION_UI.jobs});
}
function restoreClearedActions(){
  ACTION_UI.cleared.clear();
  saveClearedActions();
  renderActionQueue({actions:ACTION_UI.jobs});
}
// The queue lives in a drop-down under the Actions card; nothing opens it but
// the user. The card itself carries the running summary.
function setActionsOpen(open){
  ACTION_UI.open=!!open;
  const banner=document.getElementById("align-banner");
  const opener=document.getElementById("action-queue-open");
  if(banner) banner.style.display=ACTION_UI.open?"block":"none";
  if(opener) opener.setAttribute("aria-expanded",String(ACTION_UI.open));
}
// Pure: the one-line card summary for a list of queue rows.
function actionCardSummary(actions){
  const running=actions.filter(job=>job.status==="running");
  const queued=actions.filter(job=>job.status==="queued");
  const approval=actions.filter(job=>job.status==="approval_required");
  const wait=queued.length?` · ${queued.length} waiting`:"";
  if(running.length===1){
    const job=running[0];
    const step=job.current||job.phase||"";
    return {tone:"run",busy:true,text:actionLabel(job)+(step?" · "+step:"")+wait};
  }
  if(running.length>1) return {tone:"run",busy:true,text:`${running.length} running${wait}`};
  if(queued.length) return {tone:"run",busy:false,text:`${queued.length} waiting`};
  if(approval.length) return {tone:"warn",busy:false,text:`${approval.length} need approval`};
  return {tone:"idle",busy:false,text:"Idle"};
}
// Running work is only stoppable where the server has an interrupt point:
// the copilot call of an AI job (review-chat submits post to GitHub, so never).
const AQ_INTERRUPTIBLE=new Set(["ai","online-review","review-chat"]);
function actionCancellable(job){
  if(["queued","approval_required"].includes(job.status)) return true;
  return job.status==="running"&&!job.cancel_requested
    &&AQ_INTERRUPTIBLE.has(job.kind)&&job.op!=="submit";
}
function actionStateText(job){
  if(job.status==="queued") return "Waiting #"+job.queue_position;
  if(job.status==="approval_required") return "Approval required";
  if(job.status==="running"&&job.cancel_requested) return "cancelling";
  return job.status;
}
async function cancelAction(id){
  const job=ACTION_UI.jobs.find(j=>j.id===id);
  const what=job?actionLabel(job):"this action";
  const ask=job?.status==="running"
    ?`Stop "${what}" now? Its work so far is discarded.`
    :`Cancel "${what}"? It will not run.`;
  if(!confirm(ask)) return;
  const r=await fetch("/api/actions/"+encodeURIComponent(id)+"/cancel",{method:"POST",
    headers:{"Content-Type":"application/json"},body:"{}"});
  const data=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.error||`cancel failed (${r.status})`);
  await refreshActionQueue();
}
function actionLabel(job){
  if(job.kind==="vm-refresh")
    return "Refresh machine activity"+(job.target?" · "+job.target:"");
  if(job.kind==="vm-cancel") return job.slot||"Cancel machine job";
  const labels={"pr-scan":"Scan PRs","review-scan":"Scan reviews",
    align:"Align workspace",pldm:"Submit PLDM test"};
  return (job.kind==="workspace-action"?job.action:labels[job.kind]||job.kind)+" · "+(job.slot||"");
}
function renderActionQueue(data){
  const reviewKey=()=>typeof runningReviewTasks==="function"?[...runningReviewTasks()].sort().join():"";
  const reviewsBefore=reviewKey();
  ACTION_UI.jobs=data.actions;
  if(reviewKey()!==reviewsBefore&&typeof render==="function"&&typeof TASKS!=="undefined") render();
  const panel=document.getElementById("action-queue");
  if(!panel) return;
  const active=data.actions.filter(job=>["queued","running"].includes(job.status));
  const opener=document.getElementById("action-queue-open");
  if(opener){
    const sum=actionCardSummary(data.actions);
    opener.hidden=false;
    opener.dataset.tone=sum.tone;
    opener.title=sum.text;
    document.getElementById("aq-summary").textContent=sum.text;
    document.getElementById("aq-spin").style.visibility=sum.busy?"visible":"hidden";
  }
  // Forget cleared ids the store has already pruned; skip on an empty read.
  if(data.actions.length&&[...ACTION_UI.cleared].some(id=>!data.actions.some(job=>job.id===id))){
    ACTION_UI.cleared=new Set([...ACTION_UI.cleared].filter(id=>data.actions.some(job=>job.id===id)));
    saveClearedActions();
  }
  const shown=visibleActions(data.actions);
  const hiddenCount=data.actions.length-shown.length;
  const finishedShown=shown.filter(job=>!AQ_ACTIVE.includes(job.status)).length;
  if(!ACTION_UI.selected||!shown.some(job=>job.id===ACTION_UI.selected))
    ACTION_UI.selected=(active[0]||shown[0])?.id||null;
  const focused=document.activeElement?.closest?.("[data-action-id]")?.dataset.actionId;
  const tools=(finishedShown?`<button type="button" class="ab-link" data-aq-clear title="Hide finished and failed actions from this view">Clear finished</button>`:"")
    +(hiddenCount?`<button type="button" class="ab-link" data-aq-restore>Show ${hiddenCount} cleared</button>`:"");
  panel.innerHTML=`<div class="ab-queue-count"><span role="status">${active.filter(job=>job.status==="running").length} running · ${active.filter(job=>job.status==="queued").length} waiting</span>${tools}</div>`
    +(shown.length?"":`<div class="ab-queue-empty">No actions in view.</div>`)
    +shown.map(job=>`<button type="button" class="ab-queue-item${job.id===ACTION_UI.selected?" selected":""}"
      data-action-id="${esc(job.id)}" aria-pressed="${job.id===ACTION_UI.selected}">
      <span>${esc(actionLabel(job))}</span><b class="${job.status==="error"?"ab-bad":job.status==="cancelled"?"ab-muted":""}">${esc(actionStateText(job))}</b></button>`).join("");
  if(focused) [...panel.querySelectorAll("[data-action-id]")].find(button=>button.dataset.actionId===focused)?.focus();
  const selected=shown.find(job=>job.id===ACTION_UI.selected);
  if(selected&&!ACTION_UI.dismissed) renderAlign(selected);
  else if(!selected){
    const spin=document.getElementById("align-spin"), title=document.getElementById("align-title"), body=document.getElementById("align-body");
    if(spin) spin.style.display="none";
    if(title) title.textContent="Action queue";
    if(body) body.innerHTML="";
  }
}
async function refreshActionQueue(){
  if(ACTION_UI.refreshing) return;
  ACTION_UI.refreshing=true;
  try{
    const response=await fetch("/api/actions",{cache:"no-store"});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error||`request failed (${response.status})`);
    if(data.error) throw new Error(data.error);
    const finished=data.actions.filter(job=>!["queued","running"].includes(job.status)
      &&ACTION_UI.jobs.some(previous=>previous.id===job.id&&["queued","running"].includes(previous.status)));
    for(const job of finished) await refreshActionResult(job);
    renderActionQueue(data);
  }catch(error){
    const panel=document.getElementById("action-queue");
    if(panel) panel.textContent="Action queue unavailable: "+error.message;
    const opener=document.getElementById("action-queue-open");
    if(opener){ opener.hidden=false; opener.dataset.tone="warn";
      document.getElementById("aq-summary").textContent="Unavailable"; }
  }finally{ ACTION_UI.refreshing=false; }
}
async function refreshActionResult(job){
  if(job.kind==="workspace-action"||job.kind==="align"){
    await wsReload();
  }else if(job.kind==="review-scan"){
    const response=await fetch("/api/tasks",{cache:"no-store"});
    if(!response.ok) throw new Error("Action finished, but tasks could not be refreshed");
    const data=await response.json();
    TASKS=data.tasks;
    if(data.workspaces) WORKSPACES=data.workspaces;
    buildFilters(); render();
  }else if(job.kind==="pr-scan"&&job.result){
    PRS=job.result; renderPrs();
  }else if(job.kind==="vm-refresh"&&job.result){
    vmAdoptPayload(job.result); renderVmlog();
  }else if(job.kind==="vm-cancel"){
    const response=await fetch("/api/vm-jobs",{cache:"no-store"});
    if(response.ok){ vmAdoptPayload(await response.json()); renderVmlog(); }
  }
  buildSections();
}
function renderAlign(job){
  const body=document.getElementById("align-body");
  if(job.status==="cancelled"){
    const spin=document.getElementById("align-spin"), title=document.getElementById("align-title");
    if(!body) return;
    spin.style.display="none";
    title.innerHTML=`<span class="ab-muted">Cancelled · ${esc(actionLabel(job))}</span>`;
    body.innerHTML=job.message?`<div class="ab-msg">${esc(job.message)}</div>`:"";
    return;
  }
  renderAlignDetail(job);
  if(!body) return;
  if(job.status==="running"&&job.cancel_requested)
    document.getElementById("align-title").textContent=`Cancelling · ${actionLabel(job)}`;
  else if(actionCancellable(job))
    body.insertAdjacentHTML("beforeend",`<div class="ab-cancel"><button type="button" class="btn"
      data-aq-cancel="${esc(job.id)}">${job.status==="running"?"Stop":"Cancel"}</button></div>`);
}
function renderAlignDetail(job){
  const banner=document.getElementById("align-banner");
  const spin=document.getElementById("align-spin");
  const title=document.getElementById("align-title");
  const body=document.getElementById("align-body");
  if(!banner) return;
  const running=job.status==="running";
  if(job.status==="queued"){
    spin.style.display="none";
    title.textContent=`Waiting${job.queue_position?" #"+job.queue_position:""} · ${actionLabel(job)}`;
    body.textContent=job.waiting_reason
      ?`${job.waiting_reason}. Independent actions may run first.`
      :"Queued. This action will start when its resources and an execution slot are available.";
    return;
  }
  const reset=job.kind==="reset";
  const prScan=job.kind==="pr-scan";
  const sync=job.kind==="sync";
  const revScan=job.kind==="review-scan";
  if(job.kind==="vm-refresh"||job.kind==="pldm"){
    spin.style.display=running?"inline-block":"none";
    title.textContent=`${actionLabel(job)} · ${running?(job.current||job.phase||"starting"):job.status}`;
    body.innerHTML=(job.repos||[]).map(alignRepoRow).join("")
      +(job.message?`<div class="ab-msg${job.status==="error"?" ab-bad":""}">${esc(job.message)}</div>`:"");
    return;
  }
  if(job.kind==="workspace-action"){
    spin.style.display=running?"inline-block":"none";
    const steps=job.steps||[];
    const cur=steps.length?steps[steps.length-1]:null;
    const count=s=>s&&s.total?` (${s.done||0}/${s.total})`:"";
    const state=job.status==="approval_required"?"approval required":job.status;
    title.textContent=`${job.action} · ${job.slot} · `
      +(running?(cur?cur.label+count(cur):"starting…"):state);
    const review=job.status==="approval_required"
      ?`<button type="button" class="btn" data-workspace-approval="${esc(job.id)}">${job.action==="sync"?"Force sync":"Review confirmation"}</button>`:"";
    const stashes=(job.stashes||[]).length
      ?`<details class="ab-errs"><summary>Saved changes (${job.stashes.length} stashes)</summary>
        <p>Restore the original branch, then apply the stash with <code>git stash apply --index &lt;stash-id&gt;</code>.</p>
        <ul>${job.stashes.map(s=>`<li><code>${esc(s.path)}</code> · ${esc(s.branch||"detached HEAD")}
          · <code>${esc(s.oid)}</code></li>`).join("")}</ul></details>`:"";
    body.innerHTML=steps.map(wsStepRow).join("")
      +(running?"":(job.repos||[]).map(alignRepoRow).join(""))
      +(job.message?`<div class="ab-msg">${esc(wsErrorSummary(job.message))}</div>`:"")+review+stashes;
    return;
  }
  spin.style.display=running?"inline-block":"none";
  if(running){
    const prog=job.total?` (${job.repos.length}/${job.total})`:"";
    if(prScan){
      title.textContent=`Scanning PRs${prog}`+(job.current?" · "+job.current:"");
    }else if(revScan){
      const rp=job.total?` (${job.scanned||0}/${job.total})`:"";
      title.textContent=`Scanning reviews${rp}`+(job.current?" · "+esc(job.current):"");
    }else if(sync){
      title.textContent=`Syncing ${esc(job.slot)}${prog}${job.current?" · "+esc(job.current):""}`;
    }else{
      const verb=reset?"Resetting":"Aligning";
      title.textContent=`${verb} ${esc(job.slot)}${prog}${job.current?" · "+esc(job.current):""}`;
    }
  }else if(job.status==="error"){
    const what=prScan?"PR scan":(revScan?"Review scan":(sync?"Sync":(reset?"Reset":"Alignment")));
    title.innerHTML=`<span class="ab-bad">✕ ${what} failed</span>`;
  }else{
    const done=prScan?`✓ Probed ${job.repos.length} PRs`
      :(revScan?`✓ Checked ${job.total||job.scanned||0} review PR(s)`
      :(sync?`✓ ${esc(job.slot)} synced`
      :(reset?`✓ ${esc(job.slot)} reset to main`:`✓ Aligned ${esc(job.slot)}`)));
    title.innerHTML=`<span class="ab-ok">${done}</span>`;
  }
  const rows=(job.repos||[]).map(alignRepoRow).join("");
  const msg=job.message?`<div class="ab-msg">${esc(job.message)}</div>`:"";
  const output=job.output?`<pre class="ab-out">${esc(job.output)}</pre>`:"";
  body.innerHTML=rows+msg+output;
  if(running) body.scrollTop=body.scrollHeight;
}
async function pollAlign(jobId,label){
  ACTION_UI.selected=jobId;
  ACTION_UI.dismissed=false;
  document.getElementById("align-spin").style.display="inline-block";
  document.getElementById("align-title").textContent=label||"Aligning workspace…";
  document.getElementById("align-body").innerHTML="";
  let done=false, last=null;
  while(!done){
    try{
      const r=await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"});
      if(!r.ok){ document.getElementById("align-title").textContent="Alignment status unavailable"; return null; }
      last=await r.json();
      if(ACTION_UI.selected===jobId&&!ACTION_UI.dismissed) renderAlign(last);
      done=!["queued","running"].includes(last.status);
    }catch(err){ document.getElementById("align-title").textContent="Network error during alignment"; return null; }
    if(!done) await new Promise(res=>setTimeout(res,900));
  }
  return last;
}
document.addEventListener("click",e=>{
  const approvalId=e.target?.closest?.("[data-workspace-approval]")?.dataset.workspaceApproval;
  if(approvalId) wsReviewApproval(approvalId).catch(error=>alert(error.message));
  const cancelId=e.target?.closest?.("[data-aq-cancel]")?.dataset.aqCancel;
  if(cancelId){ cancelAction(cancelId).catch(error=>alert(error.message)); return; }
  // Re-rendering detaches the clicked button, so stop before the outside-click check.
  if(e.target?.closest?.("[data-aq-clear]")){ clearFinishedActions(); return; }
  if(e.target?.closest?.("[data-aq-restore]")){ restoreClearedActions(); return; }
  const actionId=e.target?.closest?.("[data-action-id]")?.dataset.actionId;
  if(actionId){
    ACTION_UI.selected=actionId;
    ACTION_UI.dismissed=false;
    renderActionQueue({actions:ACTION_UI.jobs});
    const job=ACTION_UI.jobs.find(job=>job.id===actionId);
    if(job) renderAlign(job);
  }
  if(e.target?.closest?.("#action-queue-open")){
    ACTION_UI.dismissed=false;
    setActionsOpen(!ACTION_UI.open);
    if(ACTION_UI.open) refreshActionQueue();
    return;
  }
  if(ACTION_UI.open&&!e.target?.closest?.("#aq-anchor")) setActionsOpen(false);
  if(e.target && e.target.id==="align-toggle"){
    const body=document.getElementById("align-body");
    body.hidden=!body.hidden;
    const queue=document.getElementById("action-queue");
    if(queue) queue.hidden=body.hidden;
    e.target.setAttribute("aria-expanded",String(!body.hidden));
    e.target.textContent=body.hidden?"Expand":"Collapse";
  }
  if(e.target && e.target.id==="align-close") setActionsOpen(false);
});
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"&&ACTION_UI.open){
    setActionsOpen(false);
    document.getElementById("action-queue-open")?.focus();
  }
});

function feature(t){
  let d=(t.description||"").trim();
  let f = d ? d.split(". ")[0] : (t.title||"").replace(/^Review:\s*/i,"");
  f=f.replace(/\s+/g," ").trim();
  return f.length>90 ? f.slice(0,89)+"…" : f;
}

// ---- build filter dropdowns ----
function uniq(arr){ return [...new Set(arr)]; }
const DD_GROUPS = ["status","category","priority","complexity","workspace","reviewstatus","author"];
function ddItem(container, value, label, group){
  const lab=document.createElement("label"); lab.className="dd-opt";
  lab.innerHTML=`<input type="checkbox" data-group="${group}" value="${esc(value)}"> ${esc(label)}`;
  const cb=lab.querySelector("input");
  cb.addEventListener("change",()=>{ updateDdLabel(group); render(); });
  container.appendChild(lab);
}
function updateDdLabel(group){
  const dd=document.getElementById("dd-"+group); if(!dd) return;
  const btn=dd.querySelector(".dd-btn"); const lbl=btn.querySelector(".dd-label");
  const vals=checkedVals(group);
  btn.classList.toggle("has-sel", vals.length>0);
  lbl.textContent = vals.length===0 ? "All" : (vals.length===1 ? vals[0] : vals.length+" selected");
}
function buildFilters(){
  DD_GROUPS.forEach(g=>{ const el=document.getElementById("f-"+g); if(el) el.innerHTML=""; });
  STATUS_ORDER.filter(s=>TASKS.some(t=>t.status===s))
    .forEach(s=>ddItem(document.getElementById("f-status"),s,s,"status"));
  uniq(TASKS.map(t=>t.category)).sort()
    .forEach(c=>ddItem(document.getElementById("f-category"),c,c,"category"));
  PRI_ORDER.filter(p=>TASKS.some(t=>t.priority===p))
    .forEach(p=>ddItem(document.getElementById("f-priority"),p,p,"priority"));
  CPX_ORDER.filter(c=>TASKS.some(t=>t.complexity===c))
    .forEach(c=>ddItem(document.getElementById("f-complexity"),c,c,"complexity"));
  uniq(TASKS.map(t=>t.workspace).filter(Boolean)).sort()
    .forEach(w=>ddItem(document.getElementById("f-workspace"),w,w,"workspace"));
  // code-review-only filters: consolidated review status + PR author
  const revPrs=TASKS.filter(t=>t.category==="code-review").flatMap(t=>prs(t));
  const revStatuses=TASKS.filter(t=>t.category==="code-review").map(t=>consolidateReviewStatus(prs(t)));
  RV_ORDER.filter(s=>revStatuses.includes(s))
    .forEach(s=>ddItem(document.getElementById("f-reviewstatus"),s,s,"reviewstatus"));
  uniq(revPrs.map(r=>r.author||"?")).sort()
    .forEach(a=>ddItem(document.getElementById("f-author"),a,a,"author"));
  DD_GROUPS.forEach(updateDdLabel);
}

function checkedVals(group){
  return [...document.querySelectorAll(`input[data-group="${group}"]:checked`)].map(c=>c.value);
}

// ---- dropdown open/close ----
document.querySelectorAll(".dd").forEach(dd=>{
  dd.querySelector(".dd-btn").addEventListener("click",e=>{
    e.stopPropagation();
    const wasOpen=dd.classList.contains("open");
    document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open"));
    if(!wasOpen) dd.classList.add("open");
  });
  dd.querySelector(".dd-panel").addEventListener("click",e=>e.stopPropagation());
});
document.addEventListener("click",()=>document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open")));

// ---- multi-select filter dropdowns (portal standard) ----
// Every filter is multi-choice: mark a filter <select> with `data-filter` and it is
// upgraded automatically (at startup and whenever it is rendered later). The first
// option with value "" is the empty-state label ("All", "Any status"). Read the
// picks with msVals(sel) and match with msHas/msAny (OR within a field, empty = all).
// The <select> stays as the hidden, `multiple` state holder, so code that rebuilds
// its options or resets it with `.value=""` keeps working and `change` still fires.
// Sort pickers and editor fields are single-value by nature and stay plain selects.
function msVals(sel){
  if(typeof sel==="string") sel=document.getElementById(sel);
  if(!sel) return [];
  msLabel(sel);
  return [...sel.selectedOptions].map(o=>o.value).filter(v=>v!=="");
}
function msHas(vals, v){ return !vals.length || vals.includes(v); }
function msAny(vals, arr){ return !vals.length || vals.some(v=>(arr||[]).includes(v)); }
function msLabel(sel){
  const dd=sel._msDD; if(!dd) return;
  const on=[...sel.options].filter(o=>o.value!=="" && o.selected);
  const btn=dd.querySelector(".dd-btn");
  btn.classList.toggle("has-sel", on.length>0);
  btn.querySelector(".dd-label").textContent =
    !on.length ? sel._msAll : (on.length===1 ? on[0].textContent : on.length+" selected");
  btn.title = on.length ? on.map(o=>o.textContent).join(", ") : (sel.title||"");
}
function msPanel(sel){
  const panel=sel._msDD.querySelector(".dd-panel");
  const opts=[...sel.options].filter(o=>o.value!=="");
  const any=opts.some(o=>o.selected);
  panel.innerHTML = opts.length
    ? opts.map(o=>`<label class="dd-opt"><input type="checkbox" data-i="${o.index}"${o.selected?" checked":""}> ${esc(o.textContent)}</label>`).join("")
      +(any?'<button type="button" class="dd-clear">Clear</button>':"")
    : '<div class="dd-opt muted">No values</div>';
}
function msEnhance(sel){
  if(!sel || sel._msDD) return;
  const first=sel.options[0];
  sel._msAll = first && first.value==="" ? first.textContent : "All";
  sel.multiple=true;
  if(first && first.value==="") first.selected=false;
  sel.style.display="none";
  const dd=document.createElement("div"); dd.className="dd ms";
  dd.innerHTML='<button type="button" class="dd-btn"><span class="dd-label"></span><span class="dd-caret">\u25be</span></button><div class="dd-panel"></div>';
  sel.after(dd); sel._msDD=dd;
  const panel=dd.querySelector(".dd-panel");
  const fire=()=>{ msLabel(sel); sel.dispatchEvent(new Event("change")); };
  dd.querySelector(".dd-btn").addEventListener("click",e=>{
    e.stopPropagation();
    const was=dd.classList.contains("open");
    document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open"));
    if(!was){ msPanel(sel); dd.classList.add("open"); }
  });
  panel.addEventListener("click",e=>{
    e.stopPropagation();
    if(!e.target.classList.contains("dd-clear")) return;
    [...sel.options].forEach(o=>{ o.selected=false; });
    msPanel(sel); fire();
  });
  panel.addEventListener("change",e=>{
    const cb=e.target; if(cb.dataset.i==null) return;
    sel.options[+cb.dataset.i].selected=cb.checked;
    if(sel.options[0] && sel.options[0].value==="") sel.options[0].selected=false;
    const any=[...sel.options].some(o=>o.value!=="" && o.selected), clr=panel.querySelector(".dd-clear");
    if(any && !clr) panel.insertAdjacentHTML("beforeend",'<button type="button" class="dd-clear">Clear</button>');
    if(!any && clr) clr.remove();
    fire();
  });
  new MutationObserver(()=>{ if(dd.classList.contains("open")) msPanel(sel); msLabel(sel); })
    .observe(sel,{childList:true});
  msLabel(sel);
}
function msAuto(root){
  (root||document).querySelectorAll("select[data-filter]").forEach(msEnhance);
}
new MutationObserver(muts=>{
  for(const m of muts) for(const n of m.addedNodes){
    if(n.nodeType!==1) continue;
    if(n.matches("select[data-filter]")) msEnhance(n); else msAuto(n);
  }
}).observe(document.documentElement,{childList:true,subtree:true});

// ---- filtering ----
function applyFilters(){
  const q=document.getElementById("q").value.trim().toLowerCase();
  const st=checkedVals("status"), cat=checkedVals("category"), pri=checkedVals("priority");
  const cpx=checkedVals("complexity"), ws=checkedVals("workspace");
  const rvst=checkedVals("reviewstatus"), auth=checkedVals("author");
  const openOnly=document.getElementById("o-hidedone").checked;
  const needJira=document.getElementById("o-jira").checked;
  const needWs=document.getElementById("o-ws").checked;
  const reviewOnly=document.getElementById("o-review").checked;

  return TASKS.filter(t=>{
    if(openOnly && (t.status==="done"||t.status==="cancelled")) return false;
    if(st.length && !st.includes(t.status)) return false;
    if(cat.length && !cat.includes(t.category)) return false;
    if(pri.length && !pri.includes(t.priority)) return false;
    if(cpx.length && !cpx.includes(t.complexity)) return false;
    if(ws.length && !ws.includes(t.workspace)) return false;
    if(rvst.length && !rvst.includes(consolidateReviewStatus(prs(t)))) return false;
    if(auth.length && !prs(t).some(r=>auth.includes(r.author||"?"))) return false;
    if(needJira && !t.jira) return false;
    if(needWs && !t.workspace) return false;
    if(reviewOnly && t.category!=="code-review") return false;
    if(q){
      const hay=[t.id,t.title,t.description,t.context,t.jira,t.workspace,
                 (t.notes||[]).join(" "), prs(t).map(p=>p.repo+" "+p.author).join(" ")]
                 .join(" ").toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}

function sortTasks(arr){
  const {key,dir}=sortState;
  const cp=[...arr];
  cp.sort((a,b)=>{
    let va=sortVal(a,key), vb=sortVal(b,key);
    let c = (va<vb?-1:va>vb?1:0);
    if(c===0 && key!=="id") c = (a.id<b.id?-1:a.id>b.id?1:0);  // stable tiebreak
    return c*dir;
  });
  return cp;
}

// ---- rendering ----
function cellFor(t,key){
  switch(key){
    case "id": return `<td class="id">${esc(t.id)}</td>`;
    case "title": return `<td class="title-cell"><div class="title-line">`+
      `<button class="task-toggle" aria-expanded="false" title="Expand task details">\u25b8</button>`+
      `<span class="title-text">${esc(t.title)}</span>${aiReviewMarker(t)}</div></td>`;
    case "category": return `<td><span class="cat">${esc(t.category)}</span></td>`;
    case "priority": return `<td><span class="badge p-${esc(t.priority)}">${esc(t.priority)}</span></td>`;
    case "status": return `<td><span class="badge s-${esc(t.status)}">${esc(t.status)}</span></td>`;
    case "complexity": return `<td>${esc(t.complexity||"—")}</td>`;
    case "cost": return `<td>${t.cost_hours!=null?t.cost_hours+"h":"—"}</td>`;
    case "jira": return `<td>${t.jira?`<a href="${JIRA_BASE+encodeURIComponent(t.jira)}" target="_blank" onclick="event.stopPropagation()">${esc(t.jira)}</a>`:"—"}</td>`;
    case "workspace": return `<td>${esc(t.workspace||"—")}</td>`;
    case "age": { const n=daysSince(t.created); return `<td>${n==null?"—":n+"d"}</td>`; }
    case "worked": return `<td>${esc(workedLabel(t))}</td>`;
    case "reviewstatus": { const p=prs(t); if(!p.length) return "<td>\u2014</td>";
      const cs=consolidateReviewStatus(p);
      return `<td><span class="badge rv-${esc(cs)}">${esc(cs)}</span></td>`; }
    case "prs": { const p=prs(t); if(!p.length) return "<td>\u2014</td>";
      return `<td>${p.map(r=>`<a href="${esc(r.pr_url)}" target="_blank" onclick="event.stopPropagation()">${esc(r.repo)}#${esc(r.pr_number)}</a>${r.merged?" \u2713":""}`).join("<br>")}</td>`; }
    case "author": { const p=prs(t); if(!p.length) return "<td>\u2014</td>";
      return `<td>${esc([...new Set(p.map(r=>r.author||"?"))].join(", "))}</td>`; }
  }
  return "<td></td>";
}

function selOpts(values, current){
  return values.map(v=>`<option value="${esc(v)}" ${v===current?"selected":""}>${esc(v)}</option>`).join("");
}

function editorHtml(t){
  if(!SERVED) return `<div class="ro-note">Read-only. Run <code>portal.py serve</code> and open the served page to edit.</div>`;
  const p=prs(t);
  const reviewRow = (t.category==="code-review" && p.length) ? `<div class="fields">
      <label>Review status<select class="e-review-status"><option value="">— set on all PRs —</option>${selOpts(RV_SETTABLE,"")}</select></label>
      <span class="hint">Applies to all ${p.length} PR${p.length>1?"s":""}; task status is re-derived.</span>
    </div>` : "";
  return `<div class="editor" data-id="${esc(t.id)}">
    <div class="fields">
      <label>Status<select class="e-status">${selOpts(STATUS_ORDER,t.status)}</select></label>
      <label>Priority<select class="e-priority">${selOpts(PRI_ORDER,t.priority)}</select></label>
      <label>Category<select class="e-category">${selOpts(CATEGORIES,t.category)}</select></label>
      <label>Complexity<select class="e-complexity"><option value="">—</option>${selOpts(CPX_ORDER,t.complexity||"")}</select></label>
      <label>Est (h)<input type="number" step="0.5" class="e-cost" value="${t.cost_hours!=null?t.cost_hours:""}" style="min-width:90px"></label>
      <label>Workspace<input class="e-workspace" type="text" list="task-workspace-${esc(t.id)}" value="${esc(t.workspace||"")}">
        <datalist id="task-workspace-${esc(t.id)}">${
        wsRoster().filter(s=>s.slot===t.workspace||wsTaskAllows(s,t)).map(s=>
          `<option value="${esc(s.slot)}">${esc(s.label||s.slot)} · ${esc(wsPurposeLabel(s))}</option>`).join("")
      }</datalist></label>
    </div>
    ${reviewRow}
    <div class="fields">
      <label style="flex:1">Add note<textarea class="e-note" placeholder="Optional note to append…"></textarea></label>
    </div>
    <div class="actions">
      <button class="primary e-save">Save changes</button>
      <span class="msg"></span>
    </div>
  </div>`;
}

function reviewDraftsHtml(t){
  const prLabel=h=>`${h.repo||"?"}#${h.pr_number??"?"}`;
  const drafts=[
    ...(t.review_drafts||[]).map(d=>({...d,heads:d.heads||[]})),
    ...prs(t).flatMap(r=>(r.drafts||[]).map(d=>({...d,legacy:true,
      heads:[{repo:r.repo,pr_number:r.pr_number,head_sha:d.head_sha}]}))),
  ].sort((a,b)=>(b.updated_at||"").localeCompare(a.updated_at||""));
  if(!drafts.length) return "";
  const link=(d,label)=>SERVED
    ?`<a class="btn review-draft-link" href="/review-drafts/${encodeURIComponent(t.id)}/${encodeURIComponent(d.id)}" target="_blank" rel="noopener">${label}</a>`
    :`<span>${label} (open the served portal to read)</span>`;
  const heads=d=>d.heads.map(h=>`${esc(prLabel(h))} <code>${esc(h.head_sha||"")}</code>`).join(", ");
  const row=(d,label)=>`${link(d,label)} <small>${d.legacy?"Per-PR draft · ":d.kind==="combined"?"Combined from per-PR drafts · ":""}Saved ${esc(formatPortalDate(d.updated_at))} · ${heads(d)}</small>`;
  return `<div class="review-drafts">${row(drafts[0],"Open review draft")}`+
    (drafts.length>1?`<details><summary>Earlier drafts (${drafts.length-1})</summary><ul>`+
      drafts.slice(1).map(d=>`<li>${row(d,"Open earlier draft")}</li>`).join("")+`</ul></details>`:"")+`</div>`;
}

function detailRow(t,ncols){
  const p=prs(t);
  const autoBtn=aiReviewButtons(t,false);
  const prHtml = p.length ? `<div><h4>Pull Requests <span class="badge rv-${esc(consolidateReviewStatus(p))}">overall: ${esc(consolidateReviewStatus(p))}</span>${autoBtn}</h4><div class="pr-list">`+
    p.map(r=>`<div class="pr"><a href="${esc(r.pr_url)}" target="_blank">${esc(r.repo)}#${esc(r.pr_number)}</a> `+
      `by ${esc(r.author||"?")} · <code>${esc(r.branch||"?")}</code> · ${r.files_changed||0} files `+
      `+${r.additions||0}/-${r.deletions||0} · <b>${esc(r.review_status||"")}</b>${r.merged?" · merged":""}</div>`).join("")+
    `</div>${reviewDraftsHtml(t)}</div>` : "";
  // Notes and the editor fold into collapsed disclosures so the expanded row
  // leads with the description, metadata and (for reviews) the PR list.
  const notesHtml=(t.notes||[]).length?`<details class="task-notes"><summary>Notes <span class="muted">(${t.notes.length})</span></summary><ul class="notes">`+
    t.notes.map(n=>`<li>${esc(n)}</li>`).join("")+`</ul></details>`:"";
  const editHtml=SERVED
    ?`<details class="task-edit-wrap"><summary>Edit task</summary>${editorHtml(t)}</details>`
    :editorHtml(t);
  return `<tr class="detail"><td colspan="${ncols}"><div class="detail-box">`+
    (t.description?`<div><h4>Description</h4>${esc(t.description)}</div>`
                  :`<div class="feature">${esc(feature(t))}</div>`)+
    `<div class="kv">`+
      (t.context?`<span><b>Context:</b> ${esc(t.context)}</span>`:"")+
      `<span><b>Created:</b> ${esc(formatPortalDate(t.created))}</span>`+
      `<span><b>Updated:</b> ${esc(formatPortalDate(t.updated))}</span>`+
      (t.due?`<span><b>Due:</b> ${esc(formatPortalDate(t.due))}</span>`:"")+
      (t.completed?`<span><b>Completed:</b> ${esc(formatPortalDate(t.completed))}</span>`:"")+
      (t.jira?`<span><b>Jira:</b> <a href="${JIRA_BASE+encodeURIComponent(t.jira)}" target="_blank" rel="noopener">${esc(t.jira)}</a></span>`:"")+
      (t.cost_hours!=null?`<span><b>Est:</b> ${t.cost_hours}h</span>`:"")+
      (t.workspace?`<span><b>WS:</b> ${esc(t.workspace)}</span>`:"")+
      `<span><b>Worked:</b> ${esc(workedLabel(t))}</span>`+
    `</div>`+ prHtml + notesHtml + editHtml +
    `</div></td></tr>`;
}

function wireEditor(detEl,t){
  const ed=detEl.querySelector(".editor"); if(!ed) return;
  const btn=ed.querySelector(".e-save"); const msg=ed.querySelector(".msg");
  btn.addEventListener("click", async (e)=>{
    e.stopPropagation();
    const payload={};
    const v=s=>ed.querySelector(s).value;
    if(v(".e-status")!==t.status) payload.status=v(".e-status");
    if(v(".e-priority")!==t.priority) payload.priority=v(".e-priority");
    if(v(".e-category")!==t.category) payload.category=v(".e-category");
    const cpx=v(".e-complexity"); if((cpx||null)!==(t.complexity||null)) payload.complexity=cpx;
    const cost=v(".e-cost"); if(cost!=="" && Number(cost)!==t.cost_hours) payload.cost_hours=Number(cost);
    const ws=v(".e-workspace"); if(ws!==(t.workspace||"")) payload.workspace=ws;
    const rvSel=ed.querySelector(".e-review-status");
    if(rvSel && rvSel.value) payload.review_status=rvSel.value;
    const note=v(".e-note").trim(); if(note) payload.add_note=note;
    if(Object.keys(payload).length===0){ msg.textContent="No changes."; return; }
    btn.disabled=true; msg.textContent="Saving…";
    try{
      payload.expected_revision=t.revision;
      const res=await fetch("/api/task/"+encodeURIComponent(t.id),{
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      const data=await res.json();
      if(!res.ok){ msg.textContent="Error: "+(data.error||res.status); btn.disabled=false; return; }
      TASKS=data.tasks; buildFilters(); render();
      msg.textContent="Saved.";
      if(data.align_job) pollAlign(data.align_job);
    }catch(err){ msg.textContent="Network error: "+err; btn.disabled=false; }
  });
  ed.querySelectorAll("select,input,textarea").forEach(el=>
    el.addEventListener("click",e=>e.stopPropagation()));
}

function taskHasOpenReview(t){
  return t.category==="code-review" && !["done","cancelled"].includes(t.status) &&
    prs(t).some(p=>!p.merged&&!p.closed&&p.review_status!=="merged"&&p.review_status!=="closed");
}

let REVIEW_CHATS={};
// Task ids with an AI review draft job (workspace or online) queued/running.
function runningReviewTasks(){
  return new Set((ACTION_UI.jobs||[]).filter(j=>["auto-review","online-review"].includes(j.kind)
    &&["queued","running"].includes(j.status)&&j.task_id).map(j=>j.task_id));
}
// Collapsed rows only show state; the buttons live in the expanded view.
function aiReviewMarker(t){
  if(!SERVED||AI_USAGE.enabled===false) return "";
  if(REVIEW_CHATS[t.id]) return ` <span class="ai-rv-mark" title="An interactive AI review is open - expand the task to resume it">✦ AI review open</span>`;
  if(runningReviewTasks().has(t.id)) return ` <span class="ai-rv-mark running" title="An AI review draft is being written - see Actions">✦ AI review running</span>`;
  return "";
}
function taskHasDraft(t){
  return (t.review_drafts||[]).length>0||prs(t).some(r=>(r.drafts||[]).length>0);
}
function aiReviewButtons(t,compact){
  if(!SERVED||AI_USAGE.enabled===false||!taskHasOpenReview(t)) return "";
  const chat=REVIEW_CHATS[t.id];
  const b=(mode,full,short,title,cls)=>`<button type="button" class="btn ai-rv${cls?" "+cls:""}" data-ai-review="${mode}" data-task="${esc(t.id)}"${chat?` data-chat="${esc(chat.id)}"`:""} title="${esc(title)}">${compact?short:full}</button>`;
  const list=chat
    ?[b("resume","✦ Resume interactive review","✦ Resume",chat.status==="interrupted"?`The start was interrupted by a portal restart; ${chat.workspace||"the workspace"} is kept. Open to retry or end it.`:`Open the interactive review in ${chat.workspace||"its workspace"}`,"primary")]
    :[taskHasDraft(t)
        ?b("rereview","↻ Re-review","↻ Re-review","A draft exists: AI follows the pr-review re-review flow (only what changed since the reviewed head) in a matching review workspace, registers the new draft and releases the workspace. Nothing is posted.")
        :b("create","✦ Create review draft","✦ Draft","Occupy a free review workspace matching the PRs' manifest, let AI write a draft with /pr-review local, register it and release the workspace. Nothing is posted."),
      b("online",taskHasDraft(t)?"🌐 Online re-review":"🌐 Online review","🌐 Online","No workspace: AI reads the PRs from GitHub (pr-review remote mode) and reviews them as ONE feature set - DEPENDS_ON graph, cross-PR consistency, then each PR. Writes and registers one draft"+(taskHasDraft(t)?" (re-review of what changed since the last draft)":"")+". Nothing is posted; RTL PRs are refused."),
      b("interactive","✦ Interactive review","✦ Interactive","Check the PRs out in a review workspace and review them together with AI in a chat panel: walk the findings, discuss the code, reach verdicts, then submit after a confirmed preview.")];
  return compact?`<span class="ai-rv-row">${list.join("")}</span>`:` <span class="ai-rv-group">${list.join("")}</span>`;
}
function selectedVisibleTasks(tasks){
  return tasks.filter(task=>selectedTaskIds.has(task.id));
}

function pruneTaskSelection(tasks){
  const visibleIds=new Set(tasks.map(task=>task.id));
  for(const id of selectedTaskIds) if(!visibleIds.has(id)) selectedTaskIds.delete(id);
}

function openReviewTaskIds(tasks){
  return selectedVisibleTasks(tasks).filter(taskHasOpenReview).map(task=>task.id);
}

function taskBulkEdits(tasks, values){
  return selectedVisibleTasks(tasks).map(task=>({id:task.id,expected_revision:task.revision,...values}));
}

function taskBulkDialog(tasks){
  if(!SERVED) return;
  const selected=selectedVisibleTasks(tasks);
  if(!selected.length) return;
  const optionList=values=>'<option value="" selected disabled>Choose…</option>'+
    values.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join("");
  const field=(key,label,control)=>`<div class="task-bulk-field">
    <label><input type="checkbox" data-bulk-enabled="${key}"> Apply ${label}</label>${control}</div>`;
  const overlay=document.createElement("div");
  overlay.className="task-bulk-overlay";
  overlay.innerHTML=`<form class="task-bulk-dialog" role="dialog" aria-modal="true" aria-labelledby="task-bulk-title">
    <h3 id="task-bulk-title">Edit ${selected.length} selected tasks</h3>
    <p>Check each field to apply its chosen value to every selected task.</p>
    <div class="task-bulk-fields">
      ${field("status","Status",`<select aria-label="Status" data-bulk-value="status" disabled>${optionList(STATUS_ORDER)}</select>`)}
      ${field("priority","Priority",`<select aria-label="Priority" data-bulk-value="priority" disabled>${optionList(PRI_ORDER)}</select>`)}
      ${field("category","Category",`<select aria-label="Category" data-bulk-value="category" disabled>${optionList(CATEGORIES.filter(value=>value!=="code-review"))}</select>`)}
      ${field("complexity","Complexity",`<select aria-label="Complexity" data-bulk-value="complexity" disabled>
        <option value="" selected disabled>Choose…</option><option value="__clear__">Clear</option>
        ${CPX_ORDER.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join("")}</select>`)}
      ${field("cost_hours","Estimated hours",'<input type="number" min="0" step="0.5" aria-label="Estimated hours" data-bulk-value="cost_hours" disabled>')}
    </div>
    <details class="task-bulk-preview"><summary>Review the ${selected.length} affected tasks</summary>
      <ul>${selected.map(task=>`<li><code>${esc(task.id)}</code> ${esc(task.title||"")}</li>`).join("")}</ul>
    </details>
    <div class="task-bulk-actions"><button type="button" class="btn" data-bulk-cancel>Cancel</button>
      <span class="task-bulk-error" role="status"></span>
      <button type="submit" class="primary" data-bulk-save>Save selected</button></div>
  </form>`;
  document.body.appendChild(overlay);
  const form=overlay.querySelector("form");
  overlay.querySelectorAll("[data-bulk-enabled]").forEach(input=>{
    input.addEventListener("change",()=>{
      const control=form.querySelector(`[data-bulk-value="${input.dataset.bulkEnabled}"]`);
      control.disabled=!input.checked;
      if(input.checked) control.focus();
    });
  });
  const close=()=>overlay.remove();
  overlay.querySelector("[data-bulk-cancel]").addEventListener("click",close);
  overlay.addEventListener("click",event=>{ if(event.target===overlay) close(); });
  form.addEventListener("submit",async event=>{
    event.preventDefault();
    const error=overlay.querySelector(".task-bulk-error");
    const save=overlay.querySelector("[data-bulk-save]");
    const values={};
    for(const input of form.querySelectorAll("[data-bulk-enabled]:checked")){
      const key=input.dataset.bulkEnabled;
      const control=form.querySelector(`[data-bulk-value="${key}"]`);
      if(key==="cost_hours"){
        if(control.value===""||!Number.isFinite(Number(control.value))||Number(control.value)<0){
          error.textContent="Estimated hours must be a non-negative number.";
          control.focus();
          return;
        }
        values[key]=Number(control.value);
      }else if(!control.value){
        error.textContent=`Choose a value for ${key}.`;
        control.focus();
        return;
      }else{
        values[key]=key==="complexity"&&control.value==="__clear__"?"":control.value;
      }
    }
    if(!Object.keys(values).length){
      error.textContent="Select at least one field to update.";
      return;
    }
    const edits=taskBulkEdits(selected,values);
    save.disabled=true;
    error.textContent="Saving…";
    try{
      const response=await fetch("/api/tasks/bulk-edit",{
        method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({edits})});
      const result=await response.json();
      if(!response.ok) throw new Error(result.error||`request failed (${response.status})`);
      TASKS=result.tasks;
      selectedTaskIds.clear();
      taskSelectionMessage=result.message||`Updated ${selected.length} task(s).`;
      buildFilters();
      render();
      close();
    }catch(err){
      error.textContent="Save failed: "+err.message;
      save.disabled=false;
    }
  });
}

function render(){
  const bulkButton=document.getElementById("task-bulk-edit");
  const reviewScan=document.getElementById("review-scan");
  bulkButton.style.display=SERVED?"":"none";
  reviewScan.style.display=SERVED?"":"none";
  const cols=COLUMNS.filter(c=>c.on);
  document.getElementById("head").innerHTML='<th class="th-nosort task-select-head" data-std-index="-1" scope="col">Select</th>'+
    cols.map((c,index)=>{
    const active=sortState.key===c.key;
    const arr=active?(sortState.dir>0?"▲":"▼"):"";
    return `<th class="th-sortable" data-std-index="${index}" data-key="${c.key}">${esc(c.label)}<span class="arr">${arr}</span></th>`;
  }).join("");
  document.querySelectorAll("#head th[data-key]").forEach(th=>{
    th.addEventListener("click",()=>{
      const k=th.dataset.key;
      if(sortState.key===k){ sortState.dir*=-1; }
      else { sortState.key=k; sortState.dir = (k==="age")?1:( ["priority","status","complexity"].includes(k)?1:1 ); }
      // sync dropdown when the column maps to one of its options
      const dd=document.getElementById("sort");
      if([...dd.options].some(o=>o.value===k)) dd.value=k;
      render();
    });
  });
  const data=sortTasks(applyFilters());
  pruneTaskSelection(data);
  const selectedCount=selectedTaskIds.size;
  const eligibleCount=openReviewTaskIds(data).length;
  const selectionBar=document.getElementById("task-selectionbar");
  selectionBar.innerHTML=`<label><input type="checkbox" id="task-select-visible" aria-label="Select all visible tasks">
    Select visible</label><span id="task-selection-count" role="status">${selectedCount} selected</span>
    <button type="button" class="btn" id="task-deselect" ${selectedCount?"":"disabled"}>Deselect</button>
    <span id="task-selection-message" role="status">${esc(taskSelectionMessage)}</span>`;
  const allSelected=data.length>0&&selectedCount===data.length;
  const visibleToggle=document.getElementById("task-select-visible");
  visibleToggle.checked=allSelected;
  visibleToggle.indeterminate=selectedCount>0&&!allSelected;
  visibleToggle.disabled=!data.length;
  visibleToggle.addEventListener("change",()=>{
    selectedTaskIds.clear();
    if(visibleToggle.checked) data.forEach(task=>selectedTaskIds.add(task.id));
    taskSelectionMessage="";
    render();
  });
  const deselect=document.getElementById("task-deselect");
  deselect.addEventListener("click",()=>{
    selectedTaskIds.clear();
    taskSelectionMessage="";
    render();
  });
  bulkButton.disabled=!selectedCount;
  bulkButton.onclick=()=>taskBulkDialog(data);
  reviewScan.disabled=!eligibleCount;
  reviewScan.title=eligibleCount
    ?`Scan unresolved PRs for ${eligibleCount} selected active review task(s) only.`
    :"Select at least one active code-review task with an unresolved PR.";
  reviewScan.textContent=`↻ Scan selected reviews${eligibleCount?` (${eligibleCount})`:""}`;
  const body=document.getElementById("body");
  body.innerHTML="";
  if(!data.length){ body.innerHTML=`<tr><td class="empty" colspan="${cols.length+1}">No tasks match the current filters.</td></tr>`; }
  data.forEach(t=>{
    const tr=document.createElement("tr"); tr.className="task"; tr.dataset.id=t.id;
    tr.innerHTML=`<td class="task-select-cell"><input type="checkbox" class="task-row-select"
      data-task-select="${esc(t.id)}" aria-label="Select ${esc(t.id)}" ${selectedTaskIds.has(t.id)?"checked":""}></td>`+
      cols.map(c=>cellFor(t,c.key)).join("");
    const det=document.createElement("template");
    det.innerHTML=detailRow(t,cols.length+1);
    const detEl=det.content.firstChild; detEl.style.display="none";
    tr.addEventListener("click",event=>{
      if(event.target.closest(".task-row-select")) return;
      const show=detEl.style.display==="none";
      detEl.style.display = show?"":"none";
      const toggle=tr.querySelector(".task-toggle");
      if(toggle){
        toggle.textContent=show?"\u25be":"\u25b8";
        toggle.title=(show?"Collapse":"Expand")+" task details";
        toggle.setAttribute("aria-expanded",String(show));
      }
      if(show) wireEditor(detEl,t);
    });
    const checkbox=tr.querySelector(".task-row-select");
    checkbox.addEventListener("click",event=>event.stopPropagation());
    checkbox.addEventListener("change",()=>{
      if(checkbox.checked) selectedTaskIds.add(t.id); else selectedTaskIds.delete(t.id);
      taskSelectionMessage="";
      render();
    });
    body.appendChild(tr); body.appendChild(detEl);
  });
  taskSelectionMessage="";
  document.getElementById("count").textContent=`Showing ${data.length} of ${TASKS.length} tasks`;
  renderStats();
  applyTableStandards(document.getElementById("sec-work"));
}

// ---- workspace state view ----
// Occupancy lives in the workspace-manager store (WORKSPACES), not on tasks.
function wsHolder(slot){ const r=WORKSPACES.slots&&WORKSPACES.slots[slot]; return (r&&r.holder)||null; }
// Per-repo failures arrive joined with "; "; identical ones collapse to one line with a count.
function wsErrorSummary(error){
  const counts=new Map();
  String(error||"").split("; ").map(x=>x.trim()).filter(Boolean).forEach(m=>counts.set(m,(counts.get(m)||0)+1));
  return [...counts].map(([m,n])=>n>1?`${m} (\u00d7${n} repos)`:m).join("; ");
}
function wsUnknown(s){ return !!s.target&&(!(s.status||{}).checked||!!(s.status||{}).error||(s.status||{}).exists===false); }
function wsTaskById(id){ return TASKS.find(t=>t.id===id)||null; }
function activeTaskList(){ return TASKS.filter(t=>!["done","cancelled"].includes(t.status)); }
function ago(iso){ if(!iso) return ""; const n=daysSince(iso); return n==null?"":(n===0?"today":n+"d ago"); }
const WS_ADHOC_TAGS=["monitor","debug","development","experiment","other","review","build","test"];

// POST a slot occupy/free to the workspace API, then refresh the whole view.
async function wsSlotPost(slot, payload, msgEl, onAccepted){
  if(!SERVED){ if(msgEl) msgEl.textContent="Read-only — run portal.py serve to edit."; return null; }
  if(msgEl) msgEl.textContent="Saving…";
  try{
    const res=await fetch("/api/workspace/"+encodeURIComponent(slot),{
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data=await res.json();
    if(!res.ok){ if(msgEl) msgEl.textContent="Error: "+(data.error||res.status); return null; }
    if(!data.align_job) throw new Error("Workspace action returned no job");
    if(onAccepted) onAccepted();
    const job=await pollAlign(data.align_job);
    if(!job) throw new Error("Action status unavailable; check Actions before retrying");
    try{ await wsReload(); }
    catch(error){ alert("Action finished, but workspace data could not be reloaded: "+error.message); }
    return job;
  }catch(err){ if(msgEl) msgEl.textContent="Workspace action error: "+err.message; return null; }
}

async function wsSyncSlot(slot){
  if(!SERVED) return;
  try{
    const res=await fetch("/api/ws-sync/"+encodeURIComponent(slot),{method:"POST"});
    const data=await res.json();
    if(!res.ok){ alert("Sync failed: "+(data.error||res.status)); return; }
    if(!data.align_job) throw new Error("Sync returned no job");
    const job=await pollAlign(data.align_job, `Syncing ${slot}…`);
    if(!job) throw new Error("Sync status unavailable; check Actions before retrying");
    await wsReload();
    if(job.status==="approval_required") wsReturnApprovalDialog(slot,job);
  }catch(err){ alert("Network error: "+err); }
}

async function wsReload(){
  const res=await fetch("/api/tasks",{cache:"no-store"});
  if(!res.ok) throw new Error("Could not reload workspace data");
  const data=await res.json();
  TASKS=data.tasks; WORKSPACES=data.workspaces;
  buildFilters(); buildSections(); renderWorkspaces();
}

async function wsRefresh(slot=null,scope="local"){
  const path=scope==="remote"?"/api/ws-check-vm":
    "/api/ws-refresh"+(slot?"/"+encodeURIComponent(slot):"");
  const res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  const data=await res.json();
  if(!res.ok) throw new Error(data.error||"Workspace refresh failed");
  const job=await pollAlign(data.align_job,slot?"Checking workspace…":
    scope==="remote"?"Checking VM workspaces…":"Checking local workspaces…");
  await wsReload();
  if(!job||job.status!=="done") throw new Error(job?.message||"Workspace status check failed");
}

async function wsShowLog(slot){
  const res=await fetch("/api/workspace-log/"+encodeURIComponent(slot),{cache:"no-store"});
  const data=await res.json();
  if(!res.ok) throw new Error(data.error||"Could not read action log");
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<div class="ws-modal ws-history"><h3>Workspace action log</h3>
    <p class="ro-note">Latest 200 controller and 200 last-fetched machine events. Full logs remain on disk.</p>
    <pre>${esc(data.events.map(e=>`${formatPortalDate(e.at)} · ${e.origin||"controller"} · ${e.action} · ${e.outcome}\n${e.detail||""}`).join("\n\n")||"No actions recorded yet.")}</pre>
    <button class="btn">Close</button></div>`;
  document.body.appendChild(ov);
  ov.querySelector("button").addEventListener("click",()=>ov.remove());
  ov.addEventListener("click",e=>{ if(e.target===ov) ov.remove(); });
}

function wsReturnApprovalDialog(slot, job){
  if(!SERVED) return;
  const workspace=wsRoster().find(s=>s.slot===slot);
  if(!workspace){ alert("Workspace is no longer tracked. Refresh the workspace list."); return; }
  const approval=job.approval;
  const syncing=job.action==="sync";
  const action=syncing?"sync":"free";
  const blockers=approval.blockers||[];
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<form class="ws-modal ws-history ws-return-dialog" role="dialog" aria-modal="true"
      aria-labelledby="ws-return-title" aria-describedby="ws-return-warning">
    <h3 id="ws-return-title">${syncing?"Force sync":"Return"} ${esc(workspace.label||slot)}?</h3>
    <p id="ws-return-warning">This workspace is not clean. Approval stashes staged, unstaged and
      untracked files, then checks out each manifest revision and syncs. Ignored files are not stashed.
      Existing branches and their commits are retained; no hard reset is performed.</p>
    <p class="ro-note">${esc(workspace.target?"Remote · "+workspace.target:"Local")} ·
      <code>${esc(workspace.path||slot)}</code> · default revision <code>${esc(approval.revision)}</code></p>
    <ul>${(approval.repos||[]).map(r=>`<li><code>${esc(r.path)}</code> ·
      ${esc(r.branch||("detached "+String(r.head||"").slice(0,12)))} → <code>${esc(r.revision)}</code>
      <br>${esc((r.reasons||[]).join("; "))}</li>`).join("")}</ul>
    ${blockers.length?`<p><b>Cannot safely return until these issues are resolved:</b></p>
      <ul>${blockers.map(reason=>`<li>${esc(reason)}</li>`).join("")}</ul>`:""}
    <p class="ro-note">${syncing?"Force sync is only available while this workspace is free and independent. It does not occupy or release the workspace.":"The hold or mirror stays in place if return or verification fails."}
      Stash IDs and original branches are recorded in the action log.</p>
    <div class="ws-modal-act"><span class="ws-modal-msg" role="status"></span>
      <button type="button" class="ws-modal-cancel">Cancel</button>
      <button type="submit" class="primary ws-modal-ok"${blockers.length?" disabled":""}>${syncing?"Force sync":"Stash and return"}</button></div>
  </form>`;
  const previousFocus=document.activeElement;
  document.body.appendChild(ov);
  const form=ov.querySelector("form");
  const submit=ov.querySelector(".ws-modal-ok");
  const msg=ov.querySelector(".ws-modal-msg");
  let saving=false;
  const close=()=>{ if(!saving){ ov.remove(); previousFocus?.focus(); } };
  ov.querySelector(".ws-modal-cancel").addEventListener("click",close);
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  ov.addEventListener("keydown",e=>{
    if(e.key==="Escape"){ e.preventDefault(); close(); }
    if(e.key==="Tab"){
      const controls=[...form.querySelectorAll("button")].filter(button=>!button.disabled);
      const first=controls[0], last=controls[controls.length-1];
      if(e.shiftKey&&document.activeElement===first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey&&document.activeElement===last){ e.preventDefault(); first.focus(); }
    }
  });
  ov.querySelector(".ws-modal-cancel").focus();
  form.addEventListener("submit",async e=>{
    e.preventDefault();
    if(saving||blockers.length) return;
    saving=true;
    submit.disabled=true;
    let accepted=false;
    const next=await wsSlotPost(slot,{action,force:true,approval_job:job.id},msg,()=>{
      accepted=true;
      saving=false;
      close();
    });
    if(!accepted){ saving=false; submit.disabled=false; return; }
    if(next?.status==="approval_required") wsReturnApprovalDialog(slot,next);
  });
}

async function wsReviewApproval(jobId){
  const res=await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"});
  const job=await res.json();
  if(!res.ok) throw new Error(job.error||"Could not read workspace approval");
  if(job.status!=="approval_required"||!job.approval)
    throw new Error("Workspace approval is no longer available");
  wsReturnApprovalDialog(job.slot,job);
}

function wsTaskCard(t, holder){
  const acq=holder&&holder.since; const rel=ago(acq);
  const detail=(holder&&holder.detail)||"";
  return `<div class="ws-task">
    <div class="t-title">${esc(t.id)} · ${esc(t.title)}</div>
    <div class="t-meta">
      <span class="badge s-${esc(t.status)}">${esc(t.status)}</span>
      ${t.priority?`<span class="badge p-${esc(t.priority)}">${esc(t.priority)}</span>`:""}
      ${detail&&detail!==t.workspace?`<span><b>path</b> <code>${esc(detail)}</code></span>`:""}
      <span><b>acquired</b> ${acq?esc(acq)+(rel?` (${esc(rel)})`:""):"—"}</span>
      ${t.jira?`<span><a href="${JIRA_BASE+encodeURIComponent(t.jira)}" target="_blank">${esc(t.jira)}</a></span>`:""}
    </div>
  </div>`;
}

function wsAdhocCard(holder){
  const acq=holder.since; const rel=ago(acq);
  return `<div class="ws-task">
    <div class="t-title">${esc(holder.label||"")}</div>
    <div class="t-meta">
      <span class="badge ws-badge-occupied">ad-hoc</span>
      <span class="badge">${esc(holder.tag||"other")}</span>
      <span><b>since</b> ${acq?esc(acq)+(rel?` (${esc(rel)})`:""):"—"}</span>
    </div>
  </div>`;
}

// Topic-based card title: what work is happening on this slot, not the slot name.
// Honors an explicit holder.header; otherwise derives from the task/ad-hoc hold.
function holderHeader(holder){
  if(!holder) return "";
  if(holder.header) return esc(holder.header);
  if(holder.type==="task"){
    const t=wsTaskById(holder.task_id);
    if(!t) return esc(holder.task_id);
    let s=(t.title||"").replace(/^review:\s*/i,"").replace(/^\[[^\]]*\]\s*/,"").trim();
    if(t.category==="code-review" && !/\breview\b|\bPR\b/i.test(s)) s+=" PR review";
    if(s.length>52) s=s.slice(0,51)+"…";
    return esc(s+" ("+t.id+")");
  }
  let s=holder.label||"";
  if(s.length>60) s=s.slice(0,59)+"…";
  return esc(s);
}

function wsCard(s){
  const holder=wsHolder(s.slot);
  const reading=s.status||{};
  const mirror=reading.mirror;
  const occ=!!holder||!!mirror;
  let bodyHtml;
  if(mirror){
    bodyHtml=`<div class="ws-task"><div class="t-title">Mirror of ${esc(mirror.source_path||"local workspace")}</div>
      <div class="t-meta"><span class="badge">${esc(mirror.state||"unknown")}</span>
      <code>${esc(mirror.source_id||"source ID unknown")}</code></div></div>`;
  }else if(!holder && wsUnknown(s)){
    bodyHtml=`<div class="ws-empty">Remote availability unverified — refresh this workspace before treating it as free.</div>`;
  }else if(!holder){
    bodyHtml=`<div class="ws-empty">Free — no task or ad-hoc hold.</div>`;
  }else if(holder.type==="task"){
    const t=wsTaskById(holder.task_id);
    bodyHtml=t?wsTaskCard(t,holder):`<div class="ws-empty">Held by ${esc(holder.task_id)} (not in view)</div>`;
  }else{
    bodyHtml=wsAdhocCard(holder);
  }
  let ctlHtml="";
  if(SERVED){
    if(occ){
      ctlHtml=`<div class="ws-actions">
        <button class="link ws-free" data-slot="${esc(s.slot)}">${mirror?"Release mirror":"Set free"}</button>
        <button class="link ws-sync" data-slot="${esc(s.slot)}">⟳ Sync</button>
        <span class="ws-msg"></span></div>`;
    }else{
      const cand=activeTaskList().filter(t=>!t.workspace&&wsTaskAllows(s,t));
      const tags=WS_ADHOC_TAGS.filter(tag=>wsAllows(s,"adhoc:"+tag));
      ctlHtml=`<div class="ws-actions ws-occupy-box" data-slot="${esc(s.slot)}">
        <div class="ws-occ-row">
          <select class="ws-pick"><option value="">— occupy with a task —</option>${
            cand.map(t=>`<option value="${esc(t.id)}">${esc(t.id)} · ${esc((t.title||"").slice(0,48))}${t.workspace?` (moves from ${esc(t.workspace)})`:""}</option>`).join("")}</select>
          <button class="primary ws-occupy-task"${cand.length?"":" disabled"}>Occupy</button>
        </div>
        <div class="ws-occ-row">
          <input type="text" class="ws-adhoc-label" placeholder="…or an ad-hoc purpose (e.g. monitor PRs)">
          <select class="ws-adhoc-tag">${tags.map(g=>`<option value="${g}">${g}</option>`).join("")}</select>
          <button class="ws-occupy-adhoc"${tags.length?"":" disabled"}>Hold</button>
        </div>
        <div class="ws-occ-row">
          <button class="link ws-sync" data-slot="${esc(s.slot)}">⟳ Sync</button>
          <button class="link ws-free" data-slot="${esc(s.slot)}">Return &amp; free</button>
        </div>
        <span class="ws-msg"></span></div>`;
    }
  }else{
    ctlHtml=`<div class="ro-note">Read-only. Run <code>portal.py serve</code> and open the served page to change occupancy.</div>`;
  }
  const headTitle = mirror
    ? `<span class="ws-topic">${esc(s.label||s.slot)} · mirror</span>` : occ
    ? `<span class="ws-topic">${holderHeader(holder)}</span>`
    : `<span class="ws-topic ws-free-name">${esc(s.label||s.slot)}</span>`;
  const pinned = s.rev && s.rev!=="main";
  const revChip = pinned
    ? `<span class="ws-rev" title="pinned revision / manifest">${esc(s.rev)}</span>` : "";
  const missing = s.exists===false
    ? `<span class="ws-rev ws-missing" title="${esc(s.path||"")}">missing</span>` : "";
  const tools = SERVED ? `<span class="ws-cardtools">
      <button class="ws-mini ws-edit" data-slot="${esc(s.slot)}" title="Edit workspace" aria-label="Edit ${esc(s.label||s.slot)}">Edit</button>
      <button class="ws-mini ws-refresh" data-slot="${esc(s.slot)}" title="Refresh ${s.target?"remote":"local"} workspace status" aria-label="Refresh ${esc(s.label||s.slot)} status">&#8635;</button>
      <button class="ws-mini ws-log" data-slot="${esc(s.slot)}" title="Action log" aria-label="Action log for ${esc(s.label||s.slot)}">&#9776;</button>
      <button class="ws-mini ws-group-btn" data-slot="${esc(s.slot)}" title="Group">\u25c7</button>
      <button class="ws-mini ws-untrack" data-slot="${esc(s.slot)}" title="Stop tracking">\u2715</button>
    </span>${wsGroupMenu(s)}` : "";
  const purposeChip=s.purpose&&s.purpose!=="general"
    ?`<span class="badge ws-purpose" title="Dedicated use only">${esc(wsPurposeLabel(s))} only</span>`:"";
  return `<div class="ws-card ${occ?"occupied":"free"}${pinned?" special":""}" title="${esc(s.path||"")}">
    <div class="ws-head">
      <div class="ws-name"><span class="num${pinned?" special":""}">${esc(String(s.badge||s.slot))}</span>${headTitle}${revChip}${missing}</div>
      <span class="ws-headright"><span class="badge ws-badge-${occ?"occupied":"free"}">${occ?"occupied":wsUnknown(s)?"unverified":"free"}</span>${tools}</span>
    </div>
    <div class="ws-body">
      <div class="ws-location"><span class="badge">${s.target?"Remote · "+esc(s.target):"Local"}</span>
        ${purposeChip}<code class="ws-id">${esc(s.id||"")}</code></div>
      <div class="ws-path">${esc(s.path||"")}</div>
      <div class="ro-note">Last checked: ${reading.checked?esc(formatPortalDate(reading.checked)):"never"}
        ${reading.checked?" · "+(reading.clean?"clean baseline":reading.exists===false?"missing":"not clean baseline"):""}</div>
      ${reading.error?`<div class="ws-error">Check/operation failed: ${esc(wsErrorSummary(reading.error))}. ${reading.checked?"Last successful reading retained.":"No successful reading yet."}</div>`:""}
      ${bodyHtml}
      ${reading.reasons&&reading.reasons.length?`<details class="ws-reasons"><summary>${reading.reasons.length} status detail(s)</summary><ul>${reading.reasons.map(r=>`<li>${esc(r)}</li>`).join("")}</ul></details>`:""}
    </div>
    ${ctlHtml}
  </div>`;
}

// Per-card group picker. Rendered collapsed; the \u25c7 button toggles it.
function wsGroupMenu(s){
  const opts=wsGroups().map(g=>`<button class="ws-gopt${g.id===s.group?" on":""}" data-slot="${esc(s.slot)}" data-gid="${esc(g.id)}">`
    +`<span class="ws-gdot" style="background:${esc(g.color||"#94a3b8")}"></span>${esc(g.name)}</button>`).join("");
  return `<div class="ws-gmenu" data-slot="${esc(s.slot)}" hidden>
    <button class="ws-gopt${s.group?"":" on"}" data-slot="${esc(s.slot)}" data-gid="">\u2014 ungrouped \u2014</button>
    ${opts}
    <button class="ws-gopt ws-gnew">\uff0b New group\u2026</button>
  </div>`;
}

// Roster mutations (track / untrack / group / group CRUD) all return the whole
// refreshed workspace payload, so every one of them re-renders from the server.
async function wsRosterPost(path, payload){
  if(!SERVED){ alert("Read-only — run portal.py serve to edit the roster."); return null; }
  const res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(payload||{})});
  const data=await res.json();
  if(!res.ok){ alert(data.error||("Error "+res.status)); return null; }
  if(data.workspaces) WORKSPACES=data.workspaces;
  if(data.tasks) TASKS=data.tasks;
  renderWorkspaces();
  return data;
}

function wsUntrackDialog(slot){
  if(!SERVED) return;
  const workspace=wsRoster().find(s=>s.slot===slot);
  if(!workspace){ alert("Workspace is no longer tracked. Refresh the workspace list."); return; }
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<form class="ws-modal" role="dialog" aria-modal="true" aria-labelledby="ws-untrack-title">
    <h3 id="ws-untrack-title">Stop tracking ${esc(workspace.label||slot)}?</h3>
    <p class="ro-note">${esc(workspace.path||slot)}. The directory itself is left untouched.</p>
    <label class="ws-untrack-override"><input name="force" type="checkbox" aria-describedby="ws-untrack-warning">
      Override occupancy/mirror checks</label>
    <p id="ws-untrack-warning" class="ro-note">Override removes only the roster entry. Task holds, mirror state and history remain intact.
      It does not free the workspace or release its mirror. Active-operation locks still apply.</p>
    <div class="ws-modal-act"><span class="ws-modal-msg" role="status"></span>
      <button type="button" class="ws-modal-cancel">Cancel</button>
      <button type="submit" class="primary ws-modal-ok">Stop tracking</button></div>
  </form>`;
  const previousFocus=document.activeElement;
  document.body.appendChild(ov);
  const form=ov.querySelector("form");
  const submit=ov.querySelector(".ws-modal-ok");
  const msg=ov.querySelector(".ws-modal-msg");
  let saving=false;
  const close=()=>{ if(!saving){ ov.remove(); previousFocus?.focus(); } };
  ov.querySelector(".ws-modal-cancel").addEventListener("click",close);
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  ov.addEventListener("keydown",e=>{
    if(e.key==="Escape"){ e.preventDefault(); close(); }
    if(e.key==="Tab"){
      const controls=[...form.querySelectorAll("input, button")];
      const first=controls[0], last=controls[controls.length-1];
      if(e.shiftKey&&document.activeElement===first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey&&document.activeElement===last){ e.preventDefault(); first.focus(); }
    }
  });
  ov.querySelector(".ws-modal-cancel").focus();
  form.addEventListener("submit",async e=>{
    e.preventDefault();
    if(saving) return;
    saving=true;
    submit.disabled=true;
    msg.textContent="Stopping tracking...";
    try{
      const result=await wsRosterPost("/api/ws-untrack/"+encodeURIComponent(slot),
        {force:form.elements.namedItem("force").checked});
      saving=false;
      if(result) close();
      else msg.textContent="Untracking failed. Review the error or explicitly override the checks and retry.";
    }catch(error){
      saving=false;
      msg.textContent="Untracking failed: "+error.message;
    }finally{
      submit.disabled=false;
    }
  });
}

function wsEditDialog(slot){
  if(!SERVED) return;
  const workspace=wsRoster().find(s=>s.slot===slot);
  if(!workspace){ alert("Workspace is no longer tracked. Refresh the workspace list."); return; }
  const revisionLocked=!!wsHolder(slot)||!!workspace.status?.mirror;
  const values={label:workspace.label||slot, badge:workspace.badge||"",
    group:workspace.group||"", rev:workspace.rev||"main", purpose:workspace.purpose||"general"};
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<form class="ws-modal" role="dialog" aria-modal="true" aria-labelledby="ws-edit-title">
    <h3 id="ws-edit-title">Edit workspace</h3>
    <p class="ro-note">${esc(workspace.path||slot)}. Metadata only; saving does not sync or change checkout branches.</p>
    <label>Label<input name="label" type="text" value="${esc(values.label)}" required></label>
    <div class="ws-modal-row">
      <label>Badge<input name="badge" type="text" value="${esc(values.badge)}" maxlength="4" required></label>
      <label>Revision<input name="rev" type="text" value="${esc(values.rev)}" required ${revisionLocked?"disabled":""}></label>
    </div>
    ${revisionLocked?'<p class="ro-note">Free the workspace or release its mirror before changing revision.</p>':""}
    <label>Dedicated purpose<select name="purpose"${revisionLocked?" disabled":""}>${wsPurposeOptions(values.purpose)}</select></label>
    <p class="ro-note">Dedicated purpose restricts assignments and execution and persists after Free. Changing it requires an idle workspace with no hold or mirror.</p>
    <label>Group<select name="group"><option value="">— ungrouped —</option>
      ${wsGroups().map(g=>`<option value="${esc(g.id)}"${g.id===values.group?" selected":""}>${esc(g.name)}</option>`).join("")}</select></label>
    <div class="ws-modal-act"><span class="ws-modal-msg" role="status"></span>
      <button type="button" class="ws-modal-cancel">Cancel</button>
      <button type="submit" class="primary ws-modal-ok">Save</button></div>
  </form>`;
  const previousFocus=document.activeElement;
  document.body.appendChild(ov);
  const form=ov.querySelector("form");
  const save=ov.querySelector(".ws-modal-ok");
  const msg=ov.querySelector(".ws-modal-msg");
  let saving=false;
  const close=()=>{ if(!saving){ ov.remove(); previousFocus?.focus(); } };
  ov.querySelector(".ws-modal-cancel").addEventListener("click",close);
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  ov.addEventListener("keydown",e=>{
    if(e.key==="Escape"){ e.preventDefault(); close(); }
    if(e.key==="Tab"){
      const controls=[...form.querySelectorAll("input:not(:disabled), select, button")];
      const first=controls[0], last=controls[controls.length-1];
      if(e.shiftKey&&document.activeElement===first){ e.preventDefault(); last.focus(); }
      else if(!e.shiftKey&&document.activeElement===last){ e.preventDefault(); first.focus(); }
    }
  });
  form.elements.namedItem("label").focus();
  form.addEventListener("submit",async e=>{
    e.preventDefault();
    if(saving||!form.reportValidity()) return;
    const changes={};
    for(const key of Object.keys(values)){
      const control=form.elements.namedItem(key);
      if(!control.disabled&&control.value.trim()!==values[key]) changes[key]=control.value.trim();
    }
    if(!Object.keys(changes).length){ close(); return; }
    saving=true;
    save.disabled=true;
    msg.textContent="Saving…";
    try{
      const result=await wsRosterPost("/api/ws-meta/"+encodeURIComponent(slot),changes);
      saving=false;
      if(result) close();
      else msg.textContent="Save failed. Your edits have been kept; correct the issue and retry.";
    }catch(error){
      saving=false;
      msg.textContent="Save failed: "+error.message;
    }finally{
      save.disabled=false;
    }
  });
}

// Modal for "+ Track workspace". A path is all that's required; the slot name
// is the directory's basename, which is what every other view keys on.
function wsTrackDialog(){
  const groupOpts=wsGroups().map(g=>`<option value="${esc(g.id)}">${esc(g.name)}</option>`).join("");
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<div class="ws-modal">
    <h3>Track a workspace</h3>
    <label>Machine<select id="wstk-target"><option value="">Local machine</option>
      ${(WORKSPACES.targets||[]).map(t=>`<option value="${esc(t)}">Remote · ${esc(t)}</option>`).join("")}</select></label>
    <p class="ro-note">Remote machines use registered mctl targets. Tracking does not connect to or start the machine; use the card refresh to check it.</p>
    <label>Path<input type="text" id="wstk-path" placeholder="~/projects/iridium/seven" autocomplete="off"></label>
    <label>Label <span class="opt">optional</span><input type="text" id="wstk-label" placeholder="defaults to the directory name"></label>
    <div class="ws-modal-row">
      <label>Revision <span class="opt">optional</span><input type="text" id="wstk-rev" placeholder="main"></label>
      <label>Badge <span class="opt">optional</span><input type="text" id="wstk-badge" placeholder="auto" maxlength="4"></label>
    </div>
    <label>Group<select id="wstk-group"><option value="">— ungrouped —</option>${groupOpts}</select></label>
    <label>Dedicated purpose<select id="wstk-purpose">${wsPurposeOptions("general")}</select></label>
    <p class="ro-note">General allows any use. Review, Development, Build root and Mirror restrict this workspace to their designated work.</p>
    <div class="ws-modal-act">
      <span class="ws-modal-msg"></span>
      <button class="ws-modal-cancel">Cancel</button>
      <button class="primary ws-modal-ok">Track</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  const close=()=>ov.remove();
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  ov.querySelector(".ws-modal-cancel").addEventListener("click",close);
  const path=ov.querySelector("#wstk-path"); path.focus();
  const submit=async()=>{
    const msg=ov.querySelector(".ws-modal-msg");
    if(!path.value.trim()){ msg.textContent="A path is required."; return; }
    msg.textContent="Saving…";
    const ok=await wsRosterPost("/api/ws-track",{
      path:path.value.trim(),
      target:ov.querySelector("#wstk-target").value,
      label:ov.querySelector("#wstk-label").value.trim(),
      rev:ov.querySelector("#wstk-rev").value.trim(),
      badge:ov.querySelector("#wstk-badge").value.trim(),
      purpose:ov.querySelector("#wstk-purpose").value,
      group:ov.querySelector("#wstk-group").value});
    if(ok) close(); else msg.textContent="";
  };
  ov.querySelector(".ws-modal-ok").addEventListener("click",submit);
  path.addEventListener("keydown",e=>{ if(e.key==="Enter") submit(); });
}

async function wsNewGroup(slot){
  const name=prompt("New group name:");
  if(!name||!name.trim()) return;
  const data=await wsRosterPost("/api/ws-groups",{action:"add",name:name.trim()});
  if(data && data.group_id && slot) wsRosterPost("/api/ws-meta/"+encodeURIComponent(slot),{group:data.group_id});
}

// Group manager: one editable row per group plus an add row.
function wsGroupPanelHtml(){
  const rows=wsGroups().map(g=>{
    const n=wsRoster().filter(s=>s.group===g.id).length;
    return `<div class="ws-grow" data-gid="${esc(g.id)}">
      <input type="color" class="ws-gcolor" value="${esc(g.color||"#2563eb")}">
      <input type="text" class="ws-gnamein" value="${esc(g.name)}">
      <span class="ws-gcount">${n} ws</span>
      <button class="link ws-gsave">Save</button>
      <button class="link danger ws-gdel">Delete</button>
    </div>`;}).join("");
  return `<div class="ws-gpanel-in">
    <div class="ws-gpanel-h">Groups</div>
    ${rows||`<div class="ws-empty">No groups yet.</div>`}
    <div class="ws-grow ws-gadd">
      <input type="color" class="ws-gcolor" value="#2563eb">
      <input type="text" class="ws-gnamein" placeholder="new group name">
      <button class="primary ws-gaddbtn">Add group</button>
    </div>
  </div>`;
}

// Cards are laid out per group when any group exists, flat otherwise.
const WS_GCOLLAPSED=new Set();
function wsGroupKey(g){ return g.id||"__loose__"; }
function wsBuckets(roster){
  const gs=wsGroups();
  if(!gs.length) return null;
  const ids=new Set(gs.map(g=>g.id));
  const buckets=gs.map(g=>({g, items:roster.filter(s=>s.group===g.id)}));
  const loose=roster.filter(s=>!s.group||!ids.has(s.group));
  if(loose.length) buckets.push({g:{id:"",name:"Ungrouped",color:"#94a3b8"}, items:loose});
  return buckets.filter(b=>b.items.length);
}
function wsGrids(roster){
  const buckets=wsBuckets(roster);
  if(!buckets) return `<div class="ws-grid cols-${wsGridCols}">${roster.map(wsCard).join("")}</div>`;
  return buckets.map(b=>{
    const key=wsGroupKey(b.g), off=WS_GCOLLAPSED.has(key);
    const held=b.items.filter(s=>wsHolder(s.slot)||(s.status||{}).mirror).length;
    const unverified=b.items.filter(s=>!wsHolder(s.slot)&&!(s.status||{}).mirror&&wsUnknown(s)).length;
    return `<div class="ws-group" data-gkey="${esc(key)}">
    <div class="ws-ghead">
      <button class="ws-gtoggle" title="Collapse or expand this group">${off?"\u25b8":"\u25be"}</button>
      <span class="ws-gdot" style="background:${esc(b.g.color||"#94a3b8")}"></span>
      <span class="ws-gname">${esc(b.g.name)}</span><span class="ws-gcount">${b.items.length}</span>
      ${off?`<span class="ws-gheld">${held} occupied \u00b7 ${b.items.length-held-unverified} free${unverified?` · ${unverified} unverified`:""}</span>`:""}
    </div>
    <div class="ws-grid cols-${wsGridCols}"${off?" hidden":""}>${b.items.map(wsCard).join("")}</div>
  </div>`;
  }).join("");
}

function wsSetGroupCollapsed(key,on){
  if(on) WS_GCOLLAPSED.add(key); else WS_GCOLLAPSED.delete(key);
}

let wsGroupPanelOpen=false;

function renderWorkspaces(){
  const box=document.getElementById("wsview");
  const roster=wsRoster().filter(s=>!wsPurposeFilter.length||wsPurposeFilter.includes(s.purpose||"general"));
  const occupied=roster.filter(s=>wsHolder(s.slot)||(s.status||{}).mirror).length;
  const free=roster.filter(s=>!wsHolder(s.slot)&&!(s.status||{}).mirror&&!wsUnknown(s)).length;
  const unknown=roster.length-occupied-free;
  const stats=`<div class="stats">
    <div class="stat"><div class="n">${roster.length}</div><div class="l">Workspaces</div></div>
    <div class="stat"><div class="n">${occupied}</div><div class="l">Occupied</div></div>
    <div class="stat"><div class="n">${free}</div><div class="l">Free</div></div>
    ${unknown?`<div class="stat"><div class="n">${unknown}</div><div class="l">Unverified</div></div>`:""}
  </div>`;
  const layout=`<div class="ws-layout"><span class="lbl">Layout</span>
    <div class="ws-seg">
      <button data-cols="3" class="${wsGridCols===3?"on":""}">3×2</button>
      <button data-cols="2" class="${wsGridCols===2?"on":""}">2×3</button>
    </div></div>`;
  const buckets=wsBuckets(roster);
  const allOff=buckets&&buckets.length&&buckets.every(b=>WS_GCOLLAPSED.has(wsGroupKey(b.g)));
  const collapse=buckets&&buckets.length?`<button class="ws-collapse-btn"
      title="Collapse or expand every group">${allOff?"\u21f2 Expand all":"\u21f1 Collapse all"}</button>`:"";
  const tools=SERVED?`<div class="ws-tools">
      <button class="ws-refresh-local btn" title="Check live local workspace status and save the readings; never connects to remote machines">↻ Check local status</button>
      <button class="ws-check-vm btn" title="Check live status of tracked VM/remote workspaces and save the readings; does not start machines">↻ Check VM status</button>
      <button class="ws-groups-btn${wsGroupPanelOpen?" on":""}">\u2699 Groups</button>
      <button class="primary ws-track-btn">\uff0b Track workspace</button>
    </div>`:"";
  const purposeFilter=`<label>Purpose <select class="ws-purpose-filter" data-filter multiple>
    <option value="">All purposes</option>${Object.entries(WORKSPACES.purposes||{general:"General"}).map(([id,label])=>
      `<option value="${esc(id)}"${wsPurposeFilter.includes(id)?" selected":""}>${esc(label)}</option>`).join("")}</select></label>`;
  box.innerHTML = `<div class="ws-toolbar">${stats}<div class="ws-toolbar-r">${purposeFilter}${layout}${collapse}${tools}</div></div>`
    + (wsGroupPanelOpen?`<div class="ws-gpanel">${wsGroupPanelHtml()}</div>`:"")
    + wsGrids(roster);
  box.querySelector(".ws-purpose-filter").addEventListener("change",e=>{
    wsPurposeFilter=msVals(e.target); renderWorkspaces();
  });
  // layout toggle
  box.querySelectorAll(".ws-seg button").forEach(b=>b.addEventListener("click",()=>{
    wsGridCols=Number(b.dataset.cols); renderWorkspaces();
  }));
  // group collapse / expand
  box.querySelectorAll(".ws-ghead").forEach(h=>h.addEventListener("click",()=>{
    const key=h.closest(".ws-group").dataset.gkey;
    wsSetGroupCollapsed(key,!WS_GCOLLAPSED.has(key));
    renderWorkspaces();
  }));
  const collapseBtn=box.querySelector(".ws-collapse-btn");
  if(collapseBtn) collapseBtn.addEventListener("click",()=>{
    box.querySelectorAll(".ws-group").forEach(g=>wsSetGroupCollapsed(g.dataset.gkey,!allOff));
    renderWorkspaces();
  });
  // roster: track / untrack / group
  const trackBtn=box.querySelector(".ws-track-btn");
  if(trackBtn) trackBtn.addEventListener("click",wsTrackDialog);
  box.querySelectorAll(".ws-edit").forEach(b=>b.addEventListener("click",()=>wsEditDialog(b.dataset.slot)));
  box.querySelectorAll(".ws-refresh, .ws-refresh-local, .ws-check-vm").forEach(b=>b.addEventListener("click",async()=>{
    b.disabled=true;
    try{ await wsRefresh(b.dataset.slot||null,b.classList.contains("ws-check-vm")?"remote":"local"); }
    catch(e){ alert(e.message); }
    finally{ b.disabled=false; }
  }));
  box.querySelectorAll(".ws-log").forEach(b=>b.addEventListener("click",()=>{
    wsShowLog(b.dataset.slot).catch(e=>alert(e.message));
  }));
  const groupsBtn=box.querySelector(".ws-groups-btn");
  if(groupsBtn) groupsBtn.addEventListener("click",()=>{ wsGroupPanelOpen=!wsGroupPanelOpen; renderWorkspaces(); });
  box.querySelectorAll(".ws-untrack").forEach(b=>b.addEventListener("click",()=>wsUntrackDialog(b.dataset.slot)));
  box.querySelectorAll(".ws-group-btn").forEach(b=>b.addEventListener("click",e=>{
    e.stopPropagation();
    const menu=b.closest(".ws-head").parentElement.querySelector(".ws-gmenu");
    const wasHidden=menu.hidden;
    box.querySelectorAll(".ws-gmenu").forEach(m=>m.hidden=true);
    menu.hidden=!wasHidden;
  }));
  box.querySelectorAll(".ws-gopt[data-gid]").forEach(b=>b.addEventListener("click",()=>{
    wsRosterPost("/api/ws-meta/"+encodeURIComponent(b.dataset.slot),{group:b.dataset.gid});
  }));
  box.querySelectorAll(".ws-gnew").forEach(b=>b.addEventListener("click",()=>
    wsNewGroup(b.closest(".ws-gmenu").dataset.slot)));
  // group manager rows
  box.querySelectorAll(".ws-gsave").forEach(b=>b.addEventListener("click",()=>{
    const row=b.closest(".ws-grow");
    wsRosterPost("/api/ws-groups",{action:"edit", id:row.dataset.gid,
      name:row.querySelector(".ws-gnamein").value, color:row.querySelector(".ws-gcolor").value});
  }));
  box.querySelectorAll(".ws-gdel").forEach(b=>b.addEventListener("click",()=>{
    const row=b.closest(".ws-grow");
    if(!confirm("Delete this group? Its workspaces become ungrouped.")) return;
    wsRosterPost("/api/ws-groups",{action:"delete", id:row.dataset.gid});
  }));
  const addBtn=box.querySelector(".ws-gaddbtn");
  if(addBtn) addBtn.addEventListener("click",()=>{
    const row=addBtn.closest(".ws-grow");
    const name=row.querySelector(".ws-gnamein").value.trim();
    if(!name){ row.querySelector(".ws-gnamein").focus(); return; }
    wsRosterPost("/api/ws-groups",{action:"add", name, color:row.querySelector(".ws-gcolor").value});
  });
  // wire controls
  box.querySelectorAll(".ws-free").forEach(b=>b.addEventListener("click",async()=>{
    if(!confirm("Return every repository to its manifest revision and sync before freeing this workspace? You will be asked before stashing changes or returning an unexpected branch.")) return;
    const msg=b.closest(".ws-actions").querySelector(".ws-msg");
    b.disabled=true;
    try{
      const job=await wsSlotPost(b.dataset.slot,{action:"free"},msg);
      if(job?.status==="approval_required") wsReturnApprovalDialog(b.dataset.slot,job);
    }finally{ b.disabled=false; }
  }));
  box.querySelectorAll(".ws-sync").forEach(b=>b.addEventListener("click",()=>wsSyncSlot(b.dataset.slot)));
  box.querySelectorAll(".ws-occupy-task").forEach(b=>b.addEventListener("click",()=>{
    const wrap=b.closest(".ws-occupy-box");
    const sel=wrap.querySelector(".ws-pick"); const msg=wrap.querySelector(".ws-msg");
    if(!sel.value){ msg.textContent="Pick a task first."; return; }
    wsSlotPost(wrap.dataset.slot,{action:"occupy",type:"task",task_id:sel.value},msg);
  }));
  box.querySelectorAll(".ws-occupy-adhoc").forEach(b=>b.addEventListener("click",()=>{
    const wrap=b.closest(".ws-occupy-box");
    const lbl=wrap.querySelector(".ws-adhoc-label"); const tag=wrap.querySelector(".ws-adhoc-tag");
    const msg=wrap.querySelector(".ws-msg");
    if(!lbl.value.trim()){ msg.textContent="Describe the ad-hoc hold first."; return; }
    wsSlotPost(wrap.dataset.slot,{action:"occupy",type:"adhoc",label:lbl.value.trim(),tag:tag.value},msg);
  }));
}

// ---- column toggles ----
function buildColumns(){
  const colsBox=document.getElementById("cols");
  colsBox.innerHTML="";
  COLUMNS.forEach(c=>{
    const lab=document.createElement("label"); lab.className="dd-opt";
    lab.innerHTML=`<input type="checkbox" ${c.on?"checked":""}> ${esc(c.label)}`;
    const cb=lab.querySelector("input");
    cb.addEventListener("change",()=>{ c.on=cb.checked; render(); });
    colsBox.appendChild(lab);
  });
}

function renderStats(){
  const categories=checkedVals("category");
  const category=categories.length===1 ? categories[0] : "";
  const scope = categories.length ? TASKS.filter(t=>categories.includes(t.category)) : TASKS;
  const c={}; scope.forEach(t=>c[t.status]=(c[t.status]||0)+1);
  const open=scope.filter(t=>!["done","cancelled"].includes(t.status));
  const est=open.reduce((s,t)=>s+(t.cost_hours||0),0);
  const cards=[
    ["Total",scope.length],["Open",open.length],
    ["In&nbsp;progress",c["in-progress"]||0],["Blocked",c["blocked"]||0],
    ["Todo",c["todo"]||0],["Done",c["done"]||0],
    ["Est&nbsp;remaining",est+"h"],
  ];
  if(category==="development"){
    const ws=new Set(open.map(t=>t.workspace).filter(Boolean));
    cards.push(["Workspaces&nbsp;busy",ws.size]);
  } else if(category==="code-review"){
    const revTasks=scope.filter(t=>t.category==="code-review" && !["done","cancelled"].includes(t.status));
    const allPrs=scope.flatMap(t=>prs(t));
    const by={}; revTasks.forEach(t=>{ const s=consolidateReviewStatus(prs(t)); by[s]=(by[s]||0)+1; });
    const merged=scope.filter(t=>t.category==="code-review" && consolidateReviewStatus(prs(t))==="merged").length;
    cards.push(["PRs",allPrs.length],
               ["Waiting&nbsp;author",by["waiting-for-author"]||0],
               ["Re-review",by["waiting-for-re-review"]||0],
               ["Approved",by["approved"]||0],
               ["Merged",merged]);
  } else if(category==="planning"){
    cards.push(["With&nbsp;Jira",scope.filter(t=>t.jira).length]);
  }
  document.getElementById("stats").innerHTML=cards.map(([l,n])=>
    `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
}

// ---- events ----
document.getElementById("q").addEventListener("input",render);
document.getElementById("sort").addEventListener("change",e=>{
  sortState.key=e.target.value;
  sortState.dir = (e.target.value==="created"||e.target.value==="worked"||e.target.value==="cost")?-1:1;
  render();
});
["o-hidedone","o-jira","o-ws","o-review"]
  .forEach(id=>document.getElementById(id).addEventListener("change",render));
document.querySelectorAll("#o-jira,#o-ws,#o-review,#o-hidedone").forEach(cb=>
  cb.addEventListener("change",()=>cb.closest(".chip").classList.toggle("on",cb.checked)));
document.getElementById("reset").addEventListener("click",()=>{
  document.getElementById("q").value="";
  document.querySelectorAll('.dd-panel input[data-group]').forEach(cb=>{cb.checked=false;});
  DD_GROUPS.forEach(updateDdLabel);
  document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open"));
  sortState.key="priority"; sortState.dir=1; document.getElementById("sort").value="priority";
  const hd=document.getElementById("o-hidedone"); hd.checked=true; hd.closest(".chip").classList.add("on");
  ["o-jira","o-ws","o-review"].forEach(id=>{const cb=document.getElementById(id); cb.checked=false; cb.closest(".chip").classList.remove("on");});
  render();
});

// ============================================================================
// Bugs section — HSI bug monitor (store: ~/tasks/bugs.json, owned by bm.py)
// ============================================================================
const SIGNALS = ["dormant","stalled","slow","moving","closed"];
const SIGNAL_ICON = {moving:"🟢", slow:"🟡", stalled:"🟠", dormant:"🔴", closed:"✅"};
const SIGNAL_COLOR = {moving:"#1AAB40", slow:"#D9B300", stalled:"#E66C37",
                      dormant:"#D64550", closed:"#9B9B9B"};
const TRIAGE_STATES = ["untriaged","in-triage","triaged","deferred"];
const TRIAGE_VERDICTS = ["new","reproduction","duplicate","known-error","not-a-bug","not-ours"];
const BUG_SEVERITIES = ["critical","high","medium","low"];
const WATCH_STATES = ["active","muted","resolved"];
const JIRA_PRI_ORDER = ["Blocker","Critical","High","Medium","Low"];
const FAM_TAB = "__families__";
const PH_TAB = "__phenomena__";
// Bug tabs are saved filter presets, not categories.
const BUG_TABS = [
  ["open","Open"], ["new","New"], ["untriaged","Untriaged"],
  ["attention","Needs attention"], [FAM_TAB,"Families"], [PH_TAB,"Phenomena"], ["all","All"],
];
let bugTab = "open";
let bugSigFilter = new Set();
const bugSort = {key:"signal", dir:1};

function bugList(){ return (BUGS && BUGS.bugs) || []; }
function bugFamilies(){ return (BUGS && BUGS.families) || []; }
function famById(id){ return bugFamilies().find(f=>f.id===id)||null; }
function bugTri(b){ return b.triage||{}; }
function bugNeedsAttention(b){ return PortalPulse.bugNeedsAttention(b); }
function bugMatchesTab(b, tab){
  switch(tab){
    case "open":      return b.open;
    case "new":       return b.open && b.is_new;
    case "untriaged": return b.open && (bugTri(b).state||"untriaged")==="untriaged";
    case "attention": return bugNeedsAttention(b);
    case "all":       return true;
  }
  return true;
}
function bugTabCount(tab){
  if(tab===FAM_TAB) return bugFamilies().filter(f=>f.status!=="closed").length;
  if(tab===PH_TAB) return phList().filter(phIsOpen).length;
  return bugList().filter(b=>bugMatchesTab(b,tab)).length;
}
function buildBugTabs(){
  const box=document.getElementById("bug-tabs");
  box.innerHTML=BUG_TABS.map(([k,l])=>
    `<button class="tab${k===bugTab?" on":""}" data-btab="${esc(k)}">${esc(l)}<span class="badge-count">${bugTabCount(k)}</span></button>`).join("");
  box.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>selectBugTab(t.dataset.btab)));
}
function selectBugTab(tab){
  bugTab=tab;
  if(typeof bugBulkSync==="function") bugBulkSync();
  document.querySelectorAll("#bug-tabs .tab").forEach(t=>t.classList.toggle("on",t.dataset.btab===tab));
  const fam = tab===FAM_TAB, phx = tab===PH_TAB, alt = fam||phx;
  document.getElementById("bug-controls").style.display = alt?"none":"";
  document.getElementById("bug-tbl").style.display     = alt?"none":"";
  document.getElementById("bug-count").style.display   = alt?"none":"";
  document.getElementById("bug-stats").style.display   = alt?"none":"";
  document.getElementById("famview").style.display     = fam?"":"none";
  document.getElementById("phview").style.display      = phx?"":"none";
  applyBugTabPreset(tab);
  renderBugs();
}
// Tabs are presets: "All" opens the whole mirror (incl. closed + muted);
// every other tab is an open-bug lens.
function applyBugTabPreset(tab){
  if(tab===FAM_TAB || tab===PH_TAB) return;
  const set=(id,on)=>{ const cb=document.getElementById(id);
    cb.checked=on; cb.closest(".chip").classList.toggle("on",on); };
  set("b-openonly", tab!=="all");
  set("b-hidemuted", tab!=="all");
}

function buildBugFilters(){
  const bugs=bugList();
  const fill=(id, vals, labelFn)=>{
    const sel=document.getElementById(id); const keep=msVals(sel);
    sel.innerHTML='<option value="">All</option>'+vals.map(v=>
      `<option value="${esc(v)}"${keep.includes(v)?" selected":""}>${esc(labelFn?labelFn(v):v)}</option>`).join("");
  };
  fill("b-owner", [...new Set(bugs.map(b=>b.assignee||"unassigned"))].sort());
  fill("b-priority", JIRA_PRI_ORDER.filter(p=>bugs.some(b=>b.priority===p)));
  fill("b-component", [...new Set(bugs.flatMap(b=>b.components||[]))].sort());
  fill("b-family", bugFamilies().map(f=>f.id), id=>{
    const f=famById(id); return f?`${id} · ${f.title.slice(0,40)}`:id; });
  const sig=document.getElementById("bug-signals");
  if(!sig.dataset.built){
    sig.innerHTML=SIGNALS.map(s=>
      `<label class="chip"><input type="checkbox" data-sig="${s}"> ${SIGNAL_ICON[s]} ${s}</label>`).join("");
    sig.querySelectorAll("input[data-sig]").forEach(cb=>cb.addEventListener("change",()=>{
      cb.closest(".chip").classList.toggle("on",cb.checked);
      if(cb.checked) bugSigFilter.add(cb.dataset.sig); else bugSigFilter.delete(cb.dataset.sig);
      renderBugs();
    }));
    sig.dataset.built="1";
  }
  const scan=document.getElementById("bug-scanline");
  const stale=daysSince((BUGS.last_scan||"").slice(0,10));
  const advice=SERVED ? "hit Scan Jira" : "run <code>bm.py scan</code>";
  scan.innerHTML = BUGS.last_scan
    ? `Jira mirror last scanned ${esc(BUGS.last_scan)}` +
      (stale!=null && stale>=1 ? ` <span class="stale-note">— ${stale}d old; ${advice}</span>` : "")
    : `<span class="stale-note">Never scanned — ${advice}</span>`;
  const btn=document.getElementById("bug-scan");
  btn.style.display = SERVED ? "" : "none";
  applyTableStandards(document.getElementById("sec-bugs"));
}

// Server-side Jira pull via bm.do_scan — REST only, no model call.
async function scanBugs(){
  const btn=document.getElementById("bug-scan"), line=document.getElementById("bug-scanline");
  btn.disabled=true; btn.innerHTML='<span class="spin">↻</span> Scanning…';
  line.innerHTML="pulling from Jira…";
  try{
    const r=await fetch("/api/bug-scan",{method:"POST"});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"scan failed");
    BUGS=j; renderBugs(); buildSections();
    const s=j.scan||{};
    const bits=[(s.fetched||0)+" mirrored"];
    if((s.new||[]).length) bits.push("<b>"+s.new.length+" new</b>: "+s.new.slice(0,4).map(esc).join(", "));
    if(s.changed) bits.push(s.changed+" changed");
    if((s.closed||[]).length) bits.push(s.closed.length+" closed");
    if(s.activity) bits.push(s.activity+" refreshed");
    if(s.pings) bits.push(s.pings+" 📣");
    if(s.ci_runs) bits.push(s.ci_runs+" CI links");
    line.innerHTML=bits.join(" · ");
  }catch(e){
    line.innerHTML='<span class="stale-note">Scan failed: '+esc(e.message)+'</span>';
  }finally{
    btn.disabled=false; btn.textContent="↻ Scan Jira";
  }
}
document.getElementById("bug-scan").addEventListener("click",scanBugs);

// Server-side gh probe of selected open review PRs, then re-derive only their
// task statuses. Runs as a background job with a live progress banner.
async function scanReviews(){
  const btn=document.getElementById("review-scan"), note=document.getElementById("review-scan-note");
  if(!SERVED){ note.style.display=""; note.textContent="Run portal.py serve to scan."; return; }
  const taskIds=openReviewTaskIds(applyFilters());
  if(!taskIds.length){ note.style.display=""; note.textContent="Select an active review task with an unresolved PR."; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">↻</span> Scanning…';
  note.style.display=""; note.textContent=`probing selected reviews for ${taskIds.length} task(s) via gh…`;
  try{
    const r=await fetch("/api/review-scan",{
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task_ids:taskIds})});
    const data=await r.json();
    if(!r.ok||data.error) throw new Error(data.error||"scan failed");
    const job=await pollAlign(data.align_job,"Scanning reviews…");
    if(!job||job.status!=="done") throw new Error(job?.message||"review scan failed");
    const tr=await fetch("/api/tasks",{cache:"no-store"});
    if(!tr.ok) throw new Error("scan completed, but task data could not be refreshed");
    const td=await tr.json();
    if(!td.tasks) throw new Error("scan completed, but refreshed task data was missing");
    TASKS=td.tasks;
    if(td.workspaces) WORKSPACES=td.workspaces;
    selectedTaskIds.clear();
    buildFilters();
    render();
    const res=(job&&job.result)||{};
    const bits=[(res.scanned||0)+" PR(s) checked"];
    if((res.changed||[]).length) bits.push("<b>"+res.changed.length+" changed</b>");
    if((res.removed||[]).length) bits.push(res.removed.length+" closed→removed");
    if((res.completed||[]).length) bits.push(res.completed.length+" merged→done");
    if((res.cancelled||[]).length) bits.push(res.cancelled.length+" emptied→cancelled");
    if((res.errors||[]).length) bits.push('<span class="stale-note">'+res.errors.length+" error(s)</span>");
    note.innerHTML=bits.join(" · ");
  }catch(e){
    note.innerHTML='<span class="stale-note">'+esc(e.message)+'</span>';
  }finally{
    const remaining=openReviewTaskIds(applyFilters()).length;
    btn.disabled=!remaining;
    btn.textContent=`↻ Scan selected reviews${remaining?` (${remaining})`:""}`;
  }
}
document.getElementById("review-scan").addEventListener("click",scanReviews);

function bugFilters(){
  const q=(document.getElementById("bq").value||"").toLowerCase().trim();
  const owner=msVals("b-owner");
  const pri=msVals("b-priority");
  const comp=msVals("b-component");
  const fam=msVals("b-family");
  const openOnly=document.getElementById("b-openonly").checked;
  const untriOnly=document.getElementById("b-untriaged").checked;
  const flagged=document.getElementById("b-flagged").checked;
  const hideMuted=document.getElementById("b-hidemuted").checked;
  const evid=msVals("b-evidence");
  const orig=msVals("b-origin");
  return bugList().filter(b=>{
    if(!bugMatchesTab(b,bugTab)) return false;
    if(openOnly && !b.open) return false;
    if(untriOnly && (bugTri(b).state||"untriaged")!=="untriaged") return false;
    if(flagged && !(b.flags||[]).length) return false;
    if(hideMuted && b.watch==="muted") return false;
    if(bugSigFilter.size && !bugSigFilter.has(b.signal)) return false;
    if(!msHas(owner, b.assignee||"unassigned")) return false;
    if(!msHas(pri, b.priority)) return false;
    if(!msAny(comp, b.components)) return false;
    if(!msHas(fam, bugTri(b).family)) return false;
    if(evid.length && !evid.some(m=>bugEvidenceMatch(b,m))) return false;
    if(!msHas(orig, b.origin||"internal")) return false;
    if(q){
      const hay=[b.key,b.summary,b.description,b.assignee,b.status,
                 bugTri(b).root_cause,bugTri(b).next_step,b.family_title,
                 (b.components||[]).join(" "),(b.labels||[]).join(" "),
                 (b.notes||[]).join(" "), b.root_cause,
                 bugPh(b).map(p=>p.id+" "+(p.title||"")).join(" "),
                 bugNotes(b).map(n=>n.name).join(" "), b.origin, b.origin_why].join(" ").toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}
function bugSortVal(b,key){
  switch(key){
    case "signal": return SIGNALS.indexOf(b.signal);
    case "idle": return -(b.idle_days==null?-1:b.idle_days);
    case "age": return -(b.age_days||0);
    case "priority": { const i=JIRA_PRI_ORDER.indexOf(b.priority); return i<0?99:i; }
    case "key": return b.key||"";
    case "status": return b.status||"";
    case "owner": return b.assignee||"~";
    case "triage": return TRIAGE_STATES.indexOf(bugTri(b).state||"untriaged");
    case "family": return bugTri(b).family||"~";
    case "pings": return -(b.oded_pings||0);
    case "evidence": return -(bugPh(b).length*100 + bugStrongNotes(b).length*10 + bugNotes(b).length);
    case "ci": return -((b.ci_runs||[]).length);
    case "summary": return (b.summary||"").toLowerCase();
  }
  return "";
}
const BUG_COLS=[
  {key:"signal", label:""}, {key:"key", label:"Bug"}, {key:"summary", label:"Summary"},
  {key:"priority", label:"Pri"}, {key:"status", label:"Status"}, {key:"owner", label:"Owner"},
  {key:"idle", label:"Idle"}, {key:"age", label:"Age"}, {key:"pings", label:"\uD83D\uDCE3"},
  {key:"ci", label:"CI"},
  {key:"triage", label:"Triage"}, {key:"family", label:"Fam"}, {key:"flags", label:"Flags"},
  {key:"evidence", label:"\uD83D\uDD2C"},
];
function bugCell(b,key){
  switch(key){
    case "signal": return `<td title="${esc(b.signal)}">${SIGNAL_ICON[b.signal]||""}</td>`;
    case "key": return `<td class="id"><a href="${esc(b.url)}" target="_blank" rel="noopener"
        onclick="event.stopPropagation()">${esc(b.key)}</a>${originBadge(b.origin,b.origin_why)}</td>`;
    case "summary": return `<td class="title-cell">${esc(b.summary)}${
        b.family_title?`<div class="feature">🧬 ${esc(b.family_title)}</div>`:""}</td>`;
    case "priority": return `<td><span class="badge p-${esc((b.priority||"").toLowerCase())}">${esc(b.priority||"—")}</span></td>`;
    case "status": return `<td><span class="badge sg-${esc(b.signal)}">${esc(b.status||"—")}</span></td>`;
    case "owner": return `<td>${esc(b.assignee||"unassigned")}</td>`;
    case "idle": return `<td>${b.idle_days==null?"—":b.idle_days+"d"}</td>`;
    case "age": return `<td>${b.age_days==null?"—":b.age_days+"d"}</td>`;
    case "pings": { const n=b.oded_pings||0;
      const man=b.manual_pings||0;
      const src=man?` (${b.jira_pings||0} in Jira + ${man} manual)`:"";
      return `<td title="Pings from Oded${src}${b.last_ping?` · last ${esc(formatPortalDate(b.last_ping))}`:""}"${
        n?' style="font-weight:700;color:#c2410c;"':''}>${n||"—"}</td>`; }
    case "ci": { const runs=b.ci_runs||[], r=runs[0];
      if(!r) return `<td>—</td>`;
      const more=runs.length>1?`<span class="feature">+${runs.length-1}</span>`:"";
      return `<td><a href="${esc(r.url)}" target="_blank" rel="noopener"`
        +` onclick="event.stopPropagation()"`
        +` title="${esc(formatPortalDate(r.at)+(r.station?" · "+r.station:""))}">run</a>${more}</td>`; }
    case "triage": { const s=bugTri(b).state||"untriaged";
      const v=bugTri(b).verdict;
      return `<td><span class="badge tr-${esc(s)}">${esc(s)}</span>${v?`<div class="feature">${esc(v)}</div>`:""}</td>`; }
    case "family": return `<td>${esc(bugTri(b).family||"—")}</td>`;
    case "evidence": return bugEvidenceCell(b);
    case "flags": return `<td>${(b.flags||[]).map(f=>
        `<span class="flag${["untriaged","unassigned","overdue","pinged"].includes(f)?" warn":""}">${esc(f)}</span>`).join("")||"—"}</td>`;
  }
  return "<td></td>";
}
function bugDetail(b,span){
  const t=bugTri(b), a=b.activity||{};
  const opts=(list,cur)=>list.map(v=>`<option value="${esc(v)}"${v===cur?" selected":""}>${esc(v)}</option>`).join("");
  const famOpts=bugFamilies().map(f=>
    `<option value="${esc(f.id)}"${f.id===t.family?" selected":""}>${esc(f.id+" · "+f.title.slice(0,44))}</option>`).join("");
  const editor=`
    <div class="editor" data-key="${esc(b.key)}">
      <h4>Triage &amp; monitoring — local only, never written to Jira</h4>
      <div class="fields">
        <label>Triage state<select class="b-state">${opts(TRIAGE_STATES,t.state||"untriaged")}</select></label>
        <label>Verdict<select class="b-verdict"><option value="">—</option>${opts(TRIAGE_VERDICTS,t.verdict)}</select></label>
        <label>Severity call<select class="b-severity"><option value="">—</option>${opts(BUG_SEVERITIES,t.severity_call)}</select></label>
        <label>Family<select class="b-family2"><option value="">—</option>${famOpts}</select></label>
        <label>Origin<select class="b-origin2" title="Who raised it. Auto infers from aqua-external links and linked phenomena">
          <option value="">auto (${esc(t.origin?"inferred":(b.origin||"internal"))})</option>${opts(["customer","internal"],t.origin)}</select></label>
        <label>Watch<select class="b-watch">${opts(WATCH_STATES,b.watch||"active")}</select></label>
        <label>Pings from Oded<input type="number" class="b-pings" min="0" style="width:64px;" value="${b.oded_pings||0}" title="Overrides the auto-derived count until the next scan"></label>
        <label>&nbsp;<button class="b-ping" title="Oded chased outside Jira — bump the manual counter and save">📣 +1 ping</button></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Root cause${b.root_cause_from?` <span class="opt">owned by ${esc(b.root_cause_from)}</span>`:""}<input type="text" class="b-root" style="min-width:320px;" value="${esc((b.root_cause_from?b.root_cause:t.root_cause)||"")}"${
          b.root_cause_from?` readonly disabled title="Root cause lives on ${esc(b.root_cause_from)} — edit it in the Phenomena tab"`:""}></label>
        <label style="flex:1;">Next step<input type="text" class="b-next" style="min-width:320px;" value="${esc(t.next_step||"")}"></label>
        <label>Owner (driver)<input type="text" class="b-owner2" value="${esc(t.owner||"")}"></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Append note<textarea class="b-note" placeholder="What you observed / decided"></textarea></label>
      </div>
      <div class="actions">
        <button class="primary b-save">Save</button>
        <span class="msg"></span>
        ${SERVED?"":'<span class="hint">read-only — run portal.py serve to edit</span>'}
      </div>
    </div>`;
  return `<tr class="detail"><td colspan="${span}"><div class="detail-box">
    <div class="kv">
      <span><b>Jira</b> <a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.key)}</a></span>
      <span><b>Project</b> ${esc(b.project||"—")}</span>
      <span><b>Reporter</b> ${esc(b.reporter||"—")}</span>
      <span><b>Origin</b> ${originBadge(b.origin,b.origin_why)} <span class="feature">${esc(b.origin_why||"")}</span></span>
      <span><b>Components</b> ${esc((b.components||[]).join(", ")||"—")}</span>
      <span><b>Labels</b> ${esc((b.labels||[]).join(", ")||"—")}</span>
    </div>
    <div class="kv">
      <span><b>Milestone</b> ${esc(b.milestone||"—")}</span>
      <span><b>Sprint</b> ${esc((b.sprints||[]).join(", ")||"—")}</span>
      <span><b>Estimate</b> ${b.estimate_h?esc(b.estimate_h)+"h":"—"}</span>
      <span><b>Due</b> ${esc(formatPortalDate(b.duedate))}</span>
      <span><b>Created</b> ${esc(formatPortalDate(b.created))}</span>
      <span><b>First seen</b> ${esc(formatPortalDate(b.first_seen))}</span>
    </div>
    <div class="kv">
      <span><b>Last activity</b> ${esc(formatPortalDate(b.last_activity))} (${b.idle_days==null?"—":b.idle_days+"d idle"})</span>
      <span><b>Comments</b> ${esc(a.comment_count||0)}${a.last_comment?` (last ${esc(formatPortalDate(a.last_comment))} by ${esc(a.last_comment_by||"—")})`:""}</span>
      <span><b>Field changes</b> ${esc(a.change_count||0)}</span>
      <span><b>Last status change</b> ${esc(formatPortalDate(a.last_status_change))}</span>
      <span><b>Pings from Oded</b> ${b.oded_pings||0}${
        b.manual_pings?` (${b.jira_pings||0} in Jira + ${b.manual_pings} manual)`:""}${
        b.last_ping?` (last ${esc(formatPortalDate(b.last_ping))}${b.days_since_ping==null?"":`, ${b.days_since_ping}d ago`})`:""}</span>
    </div>
    ${(b.ci_runs||[]).length?`<div><h4>CI runs <span class="feature">scraped from Jira comments</span></h4><div class="kv">${
      b.ci_runs.map(r=>`<span><a href="${esc(r.url)}" target="_blank" rel="noopener">run ${esc(r.run_id)}</a> ${esc(formatPortalDate(r.at))}${r.station?` · ${esc(r.station)}`:""}</span>`).join("")}</div></div>`:""}
    ${(b.links||[]).length?`<div><h4>Jira links</h4><div class="kv">${
      b.links.map(l=>`<span>${esc(l.type||"relates")} <a href="${JIRA_BASE}${esc(l.key)}" target="_blank" rel="noopener">${esc(l.key)}</a></span>`).join("")}</div></div>`:""}
    ${b.description?`<div><h4>Description</h4><pre style="white-space:pre-wrap;margin:0;font-size:12px;">${esc(b.description)}</pre></div>`:""}
    ${(b.changes||[]).length?`<div><h4>Observed changes</h4><ul class="notes">${
      b.changes.slice(-6).reverse().map(c=>`<li>${esc(formatPortalDate(c.at))}: ${esc(c.diff.join("; "))}</li>`).join("")}</ul></div>`:""}
    ${(b.notes||[]).length?`<div><h4>Notes</h4><ul class="notes">${
      b.notes.slice(-8).reverse().map(n=>`<li>${esc(n)}</li>`).join("")}</ul></div>`:""}
    ${bugTriageBlock(b)}
    ${editor}
  </div></td></tr>`;
}
function wireBugEditor(row,b){
  row.dataset.wired="1";
  const tb=row.querySelector(".tri-block"); if(tb) wireTriageBlock(tb,b);
  const ed=row.querySelector(".editor"); if(!ed) return;
  const btn=ed.querySelector(".b-save"), msg=ed.querySelector(".msg");
  const pingBtn=ed.querySelector(".b-ping");
  if(!SERVED){ btn.disabled=true; if(pingBtn) pingBtn.disabled=true; return; }
  async function post(payload,who){
    who.disabled=true; msg.textContent="Saving…";
    try{
      const res=await fetch("/api/bug/"+encodeURIComponent(b.key),{
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      const data=await res.json();
      if(!res.ok){ msg.textContent="Error: "+(data.error||res.status); who.disabled=false; return; }
      BUGS=data.bugs; buildBugFilters(); buildBugTabs(); buildSections(); renderBugs();
      const fresh=document.querySelector(`#bug-body .editor[data-key="${CSS.escape(b.key)}"] .msg`);
      (fresh||msg).textContent="Saved.";
    }catch(err){ msg.textContent="Network error: "+err; who.disabled=false; }
  }
  btn.addEventListener("click",e=>{
    e.stopPropagation();
    const edit={
      triage_state: ed.querySelector(".b-state").value,
      verdict:      ed.querySelector(".b-verdict").value,
      severity:     ed.querySelector(".b-severity").value,
      family:       ed.querySelector(".b-family2").value,
      watch:        ed.querySelector(".b-watch").value,
      origin:       ed.querySelector(".b-origin2").value,
      oded_pings:   ed.querySelector(".b-pings").value,
      root_cause:   ed.querySelector(".b-root").value,
      next_step:    ed.querySelector(".b-next").value,
      owner:        ed.querySelector(".b-owner2").value,
      note:         ed.querySelector(".b-note").value.trim(),
    };
    // A linked phenomenon owns the root cause; the server rejects a change.
    if(b.root_cause_from) delete edit.root_cause;
    post(edit, btn);
  });
  // Bumping a ping is its own one-click action so it never drags along a
  // half-filled triage form.
  pingBtn.addEventListener("click",e=>{ e.stopPropagation(); post({ping:1}, pingBtn); });
  ed.querySelectorAll("select,input,textarea,button").forEach(el=>
    el.addEventListener("click",e=>e.stopPropagation()));
}
function renderBugStats(){
  const bugs=bugList(), open=bugs.filter(b=>b.open);
  const n=f=>open.filter(f).length;
  const cards=[
    ["Open", open.length],
    ["New", n(b=>b.is_new)],
    ["Untriaged", n(b=>(bugTri(b).state||"untriaged")==="untriaged")],
    ["Stalled", n(b=>["stalled","dormant"].includes(b.signal))],
    ["Unassigned", n(b=>!b.assignee)],
    ["Pinged", n(b=>b.oded_pings)],
    ["Families", bugFamilies().filter(f=>f.status!=="closed").length],
  ];
  document.getElementById("bug-stats").innerHTML=cards.map(([l,v])=>
    `<div class="stat"><div class="n">${v}</div><div class="l">${esc(l)}</div></div>`).join("");
}
function renderFamilies(){
  const box=document.getElementById("famview");
  const fams=bugFamilies();
  if(!fams.length){
    box.innerHTML=`<div class="empty">No duplicate families yet.<br>
      Run <code>bm.py dups</code> to get proposals, then <code>bm.py family new …</code> to confirm one.</div>`;
    return;
  }
  const byKey=Object.fromEntries(bugList().map(b=>[b.key,b]));
  box.innerHTML='<div class="fam-grid">'+fams.map(f=>{
    const members=(f.members||[]).map(k=>byKey[k]).filter(Boolean);
    const openN=members.filter(m=>m.open).length;
    return `<div class="fam-card${f.status==="closed"?" closed":""}">
      <div class="fam-title">${esc(f.id)} · ${esc(f.title)}</div>
      <div class="fam-meta">
        <span>${esc(f.status||"open")}</span>
        <span>${openN} open / ${(f.members||[]).length} members</span>
        <span>primary ${esc(f.primary||"—")}</span>
        ${f.created?`<span>since ${esc(formatPortalDate(f.created))}</span>`:""}
      </div>
      ${f.root_cause?`<div style="font-size:13px;"><b style="color:var(--muted);">Root cause</b> ${esc(f.root_cause)}</div>`:""}
      <div class="fam-members">${(f.members||[]).map(k=>{
        const m=byKey[k];
        if(!m) return `<div class="m"><span>${esc(k)}</span><span class="feature">not in store</span></div>`;
        return `<div class="m${k===f.primary?" primary":""}">
          <span>${SIGNAL_ICON[m.signal]||""}</span>
          <a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(k)}</a>
          <span class="badge sg-${esc(m.signal)}">${esc(m.status||"—")}</span>
          <span class="feature" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(m.summary)}</span>
        </div>`;}).join("")}</div>
      ${(f.notes||[]).length?`<ul class="notes">${f.notes.slice(-3).map(n=>`<li>${esc(n)}</li>`).join("")}</ul>`:""}
    </div>`;}).join("")+'</div>';
}
// ---------------- Bug-triage (phenomena · investigations) ----------------
// Data comes from the shared ~/tasks/bugs.db via ph.portal_payload(); every
// edit is applied server-side by ph.apply_ph_edit(), the same code path the
// bug-triage CLI uses.
const PH_SIGNALS = ["hot","active","slow","quiet","fixed","closed","unknown"];
const PH_CLOSED = PortalPulse.PH_CLOSED;
const PH_ALARM = PortalPulse.PH_ALARM;
const SRC_LABEL = {explicit:"linked", filename:"by file name", mention:"mentioned"};
let phFilter = {q:"", status:[], platform:[], flag:[], signal:[], origin:[], openOnly:true};
const phOpenRows = new Set();
const bugOpenKeys = new Set();

function phList(){ return (TRIAGE && TRIAGE.phenomena) || []; }
function phById(id){ return phList().find(p=>p.id===id) || null; }
function phIsOpen(p){ return PortalPulse.phIsOpen(p); }
function phIcon(p){ return ((TRIAGE.signal_icon||{})[p.signal]) || "⚪"; }
function invMeta(name){ return (TRIAGE.investigations||[]).find(i=>i.name===name) || {name}; }
function noteName(path){ return String(path||"").split("/").pop(); }
function bugPh(b){ return b.phenomena || []; }
function bugNotes(b){ return b.investigations || []; }
function bugStrongNotes(b){ return bugNotes(b).filter(n=>n.source!=="mention"); }
function bugEvidenceMatch(b, mode){
  const ph=bugPh(b).length, strong=bugStrongNotes(b).length, all=bugNotes(b).length;
  switch(mode){
    case "ph": return ph>0;
    case "note": return strong>0;
    case "mention": return bugNotes(b).some(n=>n.source==="mention");
    case "any": return ph+all>0;
    case "none": return ph+all===0;
  }
  return true;
}
function originBadge(o, why){
  const c=o==="customer";
  return `<span class="org org-${c?"customer":"internal"}" title="${esc((c?"Reported by the customer":"Raised internally")+(why?" — "+why:""))}">${c?"customer":"internal"}</span>`;
}
function srcBadge(src){ return `<span class="src src-${esc(src)}">${esc(SRC_LABEL[src]||src)}</span>`; }
function phChip(id){ return `<button class="ph-chip" data-goph="${esc(id)}" title="Open ${esc(id)} in the Phenomena tab">${esc(id)}</button>`; }

function bugEvidenceCell(b){
  const ph=bugPh(b), strong=bugStrongNotes(b).length, weak=bugNotes(b).length-strong;
  if(!ph.length && !strong && !weak) return `<td class="ev-cell">—</td>`;
  return `<td class="ev-cell">${ph.map(p=>phChip(p.id)).join("")}${
    strong?`<span class="ev-n" title="${strong} investigation note(s) linked or named after this bug">📄${strong}</span>`:""}${
    weak?`<span class="ev-n weak" title="mentioned in ${weak} investigation note(s)">…${weak}</span>`:""}</td>`;
}

function bugTriageBlock(b){
  const ph=bugPh(b), notes=bugNotes(b);
  const linkable=phList().filter(p=>phIsOpen(p) && !ph.some(x=>x.id===p.id));
  const cards=ph.map(x=>{
    const p=phById(x.id)||x;
    return `<div class="ph-card" data-ph="${esc(x.id)}">
      <div class="ph-card-h">
        <span title="${esc(p.signal||"")}">${phIcon(p)}</span>
        ${phChip(x.id)}
        <span class="ph-card-t">${esc(p.title||"")}</span>
        <span class="badge phs-${esc(p.status||"")}">${esc(p.status||"—")}</span>
        ${p.severity?`<span class="badge p-${esc(p.severity)}">${esc(p.severity)}</span>`:""}
        ${srcBadge(x.source)}
      </div>
      <div class="ph-card-m">
        <span>${esc((p.platforms||[]).join(", ")||"—")}</span>
        <span>${p.count||0} sighting${p.count===1?"":"s"}</span>
        ${p.last_seen?`<span>last ${esc(formatPortalDate(p.last_seen))}</span>`:""}
        ${(p.flags||[]).map(f=>`<span class="flag${PH_ALARM.includes(f)?" warn":""}">${esc(f)}</span>`).join("")}
      </div>
      ${p.root_cause?`<div class="ph-card-rc"><b>Root cause</b> ${esc(p.root_cause)}</div>`:""}
      ${x.source==="explicit"&&SERVED?`<button class="ghost-x ph-unlink" data-ph="${esc(x.id)}" title="Unlink ${esc(b.key)} from ${esc(x.id)}">unlink</button>`:""}
    </div>`;}).join("");
  const noteRows=notes.map(n=>{
    const m=invMeta(n.name);
    return `<li class="${n.source==="mention"?"weak":""}"><button class="note-open" data-note="${esc(n.name)}">📄 ${esc(m.title||n.name)}</button>
      ${srcBadge(n.source)}<span class="feature">${esc(formatPortalDate(m.date))}</span></li>`;}).join("");
  return `<div class="tri-block">
    <h4>🔬 Bug-triage <span class="feature">bugs.db · shared with the bug-triage skill</span></h4>
    ${ph.length?`<div class="ph-cards">${cards}</div>`:`<div class="ro-note">No phenomenon linked.</div>`}
    ${notes.length?`<ul class="inv-list">${noteRows}</ul>`:""}
    ${SERVED&&linkable.length?`<div class="tri-link">
      <select class="b-phlink"><option value="">Link to phenomenon…</option>${
        linkable.map(p=>`<option value="${esc(p.id)}">${esc(p.id+" · "+(p.title||"").slice(0,60))}</option>`).join("")}</select>
      <button class="ci-btn b-phlink-go" disabled>Link</button><span class="msg tri-msg"></span></div>`:""}
  </div>`;
}

async function phPost(id, payload){
  const r=await fetch("/api/phenomenon/"+encodeURIComponent(id),{
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
  const j=await r.json();
  if(!r.ok) throw new Error(j.error||r.status);
  TRIAGE=j.triage||TRIAGE; BUGS=j.bugs||BUGS;
  buildBugFilters(); buildBugTabs(); buildSections(); renderBugs();
  return j.message;
}

function wireTriageBlock(root, b){
  root.querySelectorAll("[data-goph]").forEach(el=>el.addEventListener("click",e=>{
    e.stopPropagation(); goPhenomenon(el.dataset.goph); }));
  root.querySelectorAll(".note-open").forEach(el=>el.addEventListener("click",e=>{
    e.stopPropagation(); openNote(el.dataset.note); }));
  root.querySelectorAll(".ph-unlink").forEach(el=>el.addEventListener("click",async e=>{
    e.stopPropagation();
    if(!confirm(`Unlink ${b.key} from ${el.dataset.ph}?`)) return;
    el.disabled=true;
    try{ await phPost(el.dataset.ph,{unlink_jira:b.key}); }
    catch(err){ alert("Unlink failed: "+err.message); el.disabled=false; }
  }));
  const sel=root.querySelector(".b-phlink"), go=root.querySelector(".b-phlink-go");
  if(sel&&go){
    sel.addEventListener("click",e=>e.stopPropagation());
    sel.addEventListener("change",()=>{ go.disabled=!sel.value; });
    go.addEventListener("click",async e=>{
      e.stopPropagation(); go.disabled=true;
      const msg=root.querySelector(".tri-msg"); msg.textContent="Linking…";
      try{ await phPost(sel.value,{link_jira:b.key}); }
      catch(err){ msg.textContent="Error: "+err.message; go.disabled=false; }
    });
  }
}

function goPhenomenon(id){
  phOpenRows.add(id);
  const p=phById(id);
  if(p && !phIsOpen(p)) phFilter.openOnly=false;
  phFilter.q=""; ["status","platform","flag","signal","origin"].forEach(k=>{ phFilter[k]=[]; });
  const bar=document.querySelector("#phview .ph-bar");
  if(bar) delete bar.dataset.built;
  selectBugTab(PH_TAB);
  const row=document.querySelector(`#ph-body tr.task[data-ph="${CSS.escape(id)}"]`);
  if(row) row.scrollIntoView({block:"center", behavior:"smooth"});
}
function goBug(key){
  bugOpenKeys.add(key);
  selectBugTab("all");
  document.getElementById("bq").value=key;
  renderBugs();
}

// ---- Phenomena tab ----
function phMatches(p){
  const f=phFilter;
  if(f.openOnly && !phIsOpen(p)) return false;
  if(!msHas(f.status, p.status)) return false;
  if(!msHas(f.signal, p.signal)) return false;
  if(!msAny(f.platform, p.platforms)) return false;
  if(!msAny(f.flag, p.flags)) return false;
  if(!msHas(f.origin, p.origin||"internal")) return false;
  if(f.q){
    const hay=[p.id,p.title,p.component,p.owner,p.root_cause,(p.tags||[]).join(" "),
      (p.jira||[]).join(" "),(p.notes||[]).join(" "),
      (p.sightings||[]).map(s=>[s.raw,s.note,s.where,s.fw,s.reporter].join(" ")).join(" "),
      (p.signatures||[]).map(s=>s.text).join(" ")].join(" ").toLowerCase();
    if(!hay.includes(f.q)) return false;
  }
  return true;
}
function phSortKey(p){
  const sev=BUG_SEVERITIES.indexOf(p.severity); 
  return [phIsOpen(p)?0:1, PH_SIGNALS.indexOf(p.signal), sev<0?9:sev, -(Date.parse(p.last_seen||"")||0)];
}
function buildPhBar(box){
  const bar=box.querySelector(".ph-bar");
  if(bar.dataset.built) return;
  const all=phList();
  const opt=(vals,cur,lab)=>'<option value="">'+lab+'</option>'+vals.map(v=>
    `<option value="${esc(v)}"${(cur||[]).includes(v)?" selected":""}>${esc(v)}</option>`).join("");
  const flags=[...new Set(all.flatMap(p=>p.flags||[]))].sort();
  const plats=[...new Set(all.flatMap(p=>p.platforms||[]))].sort();
  bar.innerHTML=`
    <input type="search" class="ph-q" placeholder="Search id, title, signature, sightings, notes…" value="${esc(phFilter.q)}">
    <select class="ph-f" data-filter data-k="status">${opt(TRIAGE.statuses||[],phFilter.status,"Any status")}</select>
    <select class="ph-f" data-filter data-k="signal">${opt(PH_SIGNALS,phFilter.signal,"Any signal")}</select>
    <select class="ph-f" data-filter data-k="platform">${opt(plats,phFilter.platform,"Any platform")}</select>
    <select class="ph-f" data-filter data-k="flag">${opt(flags,phFilter.flag,"Any flag")}</select>
    <select class="ph-f" data-filter data-k="origin">${opt(["customer","internal"],phFilter.origin,"Any origin")}</select>
    <label class="chip${phFilter.openOnly?" on":""}"><input type="checkbox" class="ph-openonly"${phFilter.openOnly?" checked":""}> Open only</label>
    <span class="ro-note ph-count"></span>`;
  bar.querySelector(".ph-q").addEventListener("input",e=>{ phFilter.q=e.target.value.toLowerCase().trim(); renderPhRows(); });
  msAuto(bar);
  bar.querySelectorAll(".ph-f").forEach(s=>{
    s.addEventListener("change",()=>{ phFilter[s.dataset.k]=msVals(s); renderPhRows(); }); });
  const cb=bar.querySelector(".ph-openonly");
  cb.addEventListener("change",()=>{ phFilter.openOnly=cb.checked; cb.closest(".chip").classList.toggle("on",cb.checked); renderPhRows(); });
  bar.dataset.built="1";
}
function renderPhenomena(){
  const box=document.getElementById("phview");
  if(!box.querySelector(".ph-bar")){
    box.innerHTML=`<div class="ph-head"><div class="ph-bar bug-controls"></div></div>
      <div class="ro-note ph-src"></div>
      <table id="ph-tbl"><thead><tr>
        <th></th><th>ID</th><th>Phenomenon</th><th>Sev</th><th>Status</th><th>Platforms</th>
        <th>Seen</th><th>Last</th><th>Owner</th><th>Flags</th><th>Bugs</th>
      </tr></thead><tbody id="ph-body"></tbody></table>`;
  }
  buildPhBar(box);
  box.querySelector(".ph-src").innerHTML = TRIAGE.error
    ? `<span class="stale-note">Registry unavailable: ${esc(TRIAGE.error)}</span>`
    : `Registry <code>${esc(TRIAGE.db||"bugs.db")}</code> — the same rows <code>ph.py</code> reads and writes.`;
  renderPhRows();
  applyTableStandards(document.getElementById("sec-bugs"));
}
function renderPhRows(){
  const body=document.getElementById("ph-body"); if(!body) return;
  const rows=phList().filter(phMatches).sort((a,b)=>{
    const x=phSortKey(a), y=phSortKey(b);
    for(let i=0;i<x.length;i++){ if(x[i]!==y[i]) return x[i]<y[i]?-1:1; }
    return a.id<b.id?-1:1;
  });
  document.querySelector("#phview .ph-count").textContent=`${rows.length} of ${phList().length} phenomena`;
  body.innerHTML="";
  if(!rows.length){
    body.innerHTML=`<tr><td class="empty" colspan="11">${phList().length
      ?"No phenomena match the current filters."
      :"The registry is empty. Record one with <code>ph.py sight --new …</code>."}</td></tr>`;
    return;
  }
  const bugKeys=new Set(bugList().map(b=>b.key));
  rows.forEach(p=>{
    const tr=document.createElement("tr"); tr.className="task"; tr.dataset.ph=p.id;
    const jira=p.jira||[];
    tr.innerHTML=`
      <td title="${esc(p.signal)}">${phIcon(p)}</td>
      <td class="id">${esc(p.id)}${originBadge(p.origin,p.origin_why)}</td>
      <td class="title-cell">${esc(p.title||"")}${p.component?`<div class="feature">${esc(p.component)}</div>`:""}</td>
      <td>${p.severity?`<span class="badge p-${esc(p.severity)}">${esc(p.severity)}</span>`:"—"}</td>
      <td><span class="badge phs-${esc(p.status)}">${esc(p.status)}</span></td>
      <td>${esc((p.platforms||[]).join(", ")||"—")}</td>
      <td>${p.count||0}×</td>
      <td>${esc(formatPortalDate(p.last_seen))}${p.idle_days!=null?`<div class="feature">${p.idle_days}d ago</div>`:""}</td>
      <td>${esc(p.owner||"—")}</td>
      <td>${(p.flags||[]).map(f=>`<span class="flag${PH_ALARM.includes(f)||f==="no-owner"?" warn":""}">${esc(f)}</span>`).join("")||"—"}</td>
      <td>${jira.length?jira.map(k=>`<span class="ev-n${bugKeys.has(k)?"":" weak"}">${esc(k)}</span>`).join(" "):"—"}</td>`;
    const tpl=document.createElement("template");
    tpl.innerHTML=phDetail(p,11);
    const det=tpl.content.firstElementChild;
    const open=phOpenRows.has(p.id);
    det.style.display=open?"":"none";
    if(open) wirePhDetail(det,p);
    tr.addEventListener("click",()=>{
      const show=det.style.display==="none";
      det.style.display=show?"":"none";
      if(show){ phOpenRows.add(p.id); if(!det.dataset.wired) wirePhDetail(det,p); }
      else phOpenRows.delete(p.id);
    });
    body.appendChild(tr); body.appendChild(det);
  });
  applyTableStandards(document.getElementById("sec-bugs"));
}
function phDetail(p, span){
  const ev=((TRIAGE.evidence||{}).phenomena||{})[p.id]||{};
  const fix=p.fix||{};
  const bugKeys=new Set(bugList().map(b=>b.key));
  const linkedBugs=(ev.bugs||[]).map(x=>`<span class="bug-link">${
      bugKeys.has(x.key)?`<button class="go-bug" data-key="${esc(x.key)}">${esc(x.key)}</button>`
        :`<a href="${JIRA_BASE}${esc(x.key)}" target="_blank" rel="noopener">${esc(x.key)}</a>`}
      ${srcBadge(x.source)}${x.source==="explicit"&&SERVED?`<button class="ghost-x ph-unjira" data-key="${esc(x.key)}" title="Unlink">×</button>`:""}</span>`).join("");
  const notes=(ev.investigations||[]).map(n=>{
    const m=invMeta(n.name);
    return `<li class="${n.source==="mention"?"weak":""}"><button class="note-open" data-note="${esc(n.name)}">📄 ${esc(m.title||n.name)}</button>
      ${srcBadge(n.source)}<span class="feature">${esc(formatPortalDate(m.date))}</span>${
      n.source==="explicit"&&SERVED?`<button class="ghost-x ph-unnote" data-note="${esc(n.name)}" title="Unlink note">×</button>`:""}</li>`;}).join("");
  const sightings=(p.sightings||[]).slice().reverse().map(s=>`<tr>
      <td class="id">${esc(s.id)}</td><td>${esc(formatPortalDate(s.at))}</td><td>${esc(s.platform||"—")}</td>
      <td>${esc(s.where||"—")}</td><td>${esc(s.fw||"—")}${s.after_fix?' <span class="flag warn">after-fix</span>':""}</td>
      <td>${esc(s.reporter||s.source||"—")}</td>
      <td>${esc(s.note||"")}${s.raw?`<details><summary>raw evidence</summary><pre class="ph-raw">${esc(s.raw)}</pre></details>`:""}</td></tr>`).join("");
  const sigs=(p.signatures||[]).map(s=>`<div class="ph-sig"><span class="feature">${esc(s.label||"")} · ${esc(s.fp||"")} · ${esc(formatPortalDate(s.added))}</span><pre class="ph-raw">${esc(s.text||"")}</pre></div>`).join("");
  const invOpts=(TRIAGE.investigations||[]).filter(i=>!(ev.investigations||[]).some(n=>n.name===i.name&&n.source==="explicit"))
    .map(i=>`<option value="${esc(i.name)}">${esc((i.title||i.name).slice(0,70))}</option>`).join("");
  const opt=(list,cur,blank)=>(blank?'<option value="">—</option>':"")+list.map(v=>`<option value="${esc(v)}"${v===cur?" selected":""}>${esc(v)}</option>`).join("");
  const inp=(k,v,cls="")=>`<input type="text" class="pe ${cls}" data-k="${k}" data-orig="${esc(v||"")}" value="${esc(v||"")}">`;
  return `<tr class="detail"><td colspan="${span}"><div class="detail-box">
    <div class="kv">
      <span><b>Origin</b> ${originBadge(p.origin,p.origin_why)} <span class="feature">${esc(p.origin_why||"")}${p.origin_why&&p.origin_why.startsWith("tagged")?"":" · tag customer/internal to override"}</span></span>
      <span><b>Component</b> ${esc(p.component||"—")}</span>
      <span><b>Owner</b> ${esc(p.owner||"—")}</span>
      <span><b>Created</b> ${esc(formatPortalDate(p.created))}</span>
      <span><b>First / last seen</b> ${esc(formatPortalDate(p.first_seen))} → ${esc(formatPortalDate(p.last_seen))}</span>
      <span><b>Sightings</b> ${p.count||0}</span>
      <span><b>Sources</b> ${esc((p.sources||[]).join(", ")||"—")}</span>
      <span><b>Tags</b> ${esc((p.tags||[]).join(", ")||"—")}</span>
    </div>
    ${p.root_cause?`<div><h4>Root cause</h4><div class="ph-rc">${esc(p.root_cause)}</div></div>`:""}
    ${Object.keys(fix).length?`<div><h4>Fix</h4><div class="kv">
      ${fix.version?`<span><b>Version</b> ${esc(fix.version)}</span>`:""}
      ${fix.pr?`<span><b>PR</b> <a href="${esc(fix.pr)}" target="_blank" rel="noopener">${esc(fix.pr.replace(/^https:\/\/github.com\//,""))}</a></span>`:""}
      ${fix.commit?`<span><b>Commit</b> <code>${esc(fix.commit.slice(0,12))}</code></span>`:""}
      ${fix.repo?`<span><b>Repo</b> ${esc(fix.repo)}</span>`:""}
      ${fix.at?`<span><b>Fixed</b> ${esc(formatPortalDate(fix.at))}</span>`:""}</div></div>`:""}
    <div><h4>Linked bugs</h4>${linkedBugs?`<div class="kv">${linkedBugs}</div>`:'<span class="ro-note">none</span>'}</div>
    ${notes?`<div><h4>Investigation notes</h4><ul class="inv-list">${notes}</ul></div>`:""}
    ${sightings?`<div><h4>Sightings</h4><div class="tbl-scroll"><table class="ph-sight"><thead><tr>
      <th>ID</th><th>At</th><th>Platform</th><th>Where</th><th>FW</th><th>Reporter</th><th>Note</th></tr></thead>
      <tbody>${sightings}</tbody></table></div></div>`:""}
    ${sigs?`<div><h4>Signatures</h4>${sigs}</div>`:""}
    ${(p.notes||[]).length?`<div><h4>Activity</h4><ul class="notes">${
      p.notes.slice().reverse().map(n=>`<li>${mdInline(n)}</li>`).join("")}</ul></div>`:""}
    <div class="editor ph-editor">
      <h4>Edit ${esc(p.id)} — writes bugs.db, the bug-triage registry</h4>
      <div class="fields">
        <label style="flex:1;">Title${inp("title",p.title,"wide")}</label>
        <label>Status<select class="pe" data-k="status" data-orig="${esc(p.status||"")}">${opt(TRIAGE.statuses||[],p.status)}</select></label>
        <label>Severity<select class="pe" data-k="severity" data-orig="${esc(p.severity||"")}">${opt(TRIAGE.severities||[],p.severity,true)}</select></label>
        <label>Owner${inp("owner",p.owner)}</label>
        <label>Component${inp("component",p.component)}</label>
      </div>
      <div class="fields">
        <label style="flex:1;">Root cause<textarea class="pe" data-k="root_cause" data-orig="${esc(p.root_cause||"")}">${esc(p.root_cause||"")}</textarea></label>
      </div>
      <div class="fields">
        <label>Fix version${inp("fix_version",fix.version)}</label>
        <label style="flex:1;">Fix PR${inp("fix_pr",fix.pr,"wide")}</label>
        <label>Fix commit${inp("fix_commit",fix.commit)}</label>
        <label>Fix repo${inp("fix_repo",fix.repo)}</label>
      </div>
      <div class="fields">
        <label>Add tags<input type="text" class="pe-tags" placeholder="comma separated"></label>
        <label>Link Jira<input type="text" class="pe-jira" placeholder="KEY-123, KEY-456"></label>
        <label>Link note<select class="pe-note"><option value="">—</option>${invOpts}</select></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Append note<textarea class="pe-log" placeholder="What you observed / decided"></textarea></label>
      </div>
      <div class="actions">
        <button class="primary ph-save">Save</button><span class="msg"></span>
        ${SERVED?"":'<span class="hint">read-only — run portal.py serve to edit</span>'}
      </div>
    </div>
  </div></td></tr>`;
}
function wirePhDetail(det, p){
  det.dataset.wired="1";
  det.querySelectorAll("a,button,select,input,textarea,summary,details,pre").forEach(el=>
    el.addEventListener("click",e=>e.stopPropagation()));
  det.querySelectorAll(".note-open").forEach(el=>el.addEventListener("click",()=>openNote(el.dataset.note)));
  det.querySelectorAll(".go-bug").forEach(el=>el.addEventListener("click",()=>goBug(el.dataset.key)));
  const ed=det.querySelector(".ph-editor"), btn=ed.querySelector(".ph-save"), msg=ed.querySelector(".msg");
  const send=async(payload,who)=>{
    who.disabled=true; msg.textContent="Saving…";
    try{
      const m=await phPost(p.id,payload);
      const fresh=document.querySelector(`#ph-body tr.task[data-ph="${CSS.escape(p.id)}"]`);
      const nmsg=fresh&&fresh.nextElementSibling.querySelector(".ph-editor .msg");
      if(nmsg) nmsg.textContent=m||"Saved.";
    }catch(err){ msg.textContent="Error: "+err.message; who.disabled=false; }
  };
  det.querySelectorAll(".ph-unjira").forEach(el=>el.addEventListener("click",()=>{
    if(confirm(`Unlink ${el.dataset.key} from ${p.id}?`)) send({unlink_jira:el.dataset.key},el); }));
  det.querySelectorAll(".ph-unnote").forEach(el=>el.addEventListener("click",()=>{
    if(confirm(`Unlink ${el.dataset.note} from ${p.id}?`)) send({unlink_note:el.dataset.note},el); }));
  if(!SERVED){ btn.disabled=true; return; }
  btn.addEventListener("click",()=>{
    const payload={};
    ed.querySelectorAll(".pe").forEach(el=>{ if(el.value!==el.dataset.orig) payload[el.dataset.k]=el.value; });
    const split=s=>s.split(/[,\s]+/).map(x=>x.trim()).filter(Boolean);
    const tags=split(ed.querySelector(".pe-tags").value); if(tags.length) payload.tag_add=tags;
    const jira=split(ed.querySelector(".pe-jira").value); if(jira.length) payload.link_jira=jira;
    const note=ed.querySelector(".pe-note").value; if(note) payload.link_note=note;
    const log=ed.querySelector(".pe-log").value.trim(); if(log) payload.note=log;
    if(!Object.keys(payload).length){ msg.textContent="Nothing changed."; return; }
    send(payload,btn);
  });
}

// ---- Investigation note viewer ----
function mdSpan(s){
  // Split on code spans first so nothing inside them is re-formatted.
  return String(s||"").split(/(``.+?``|`[^`]+`)/).map(part=>{
    const cm=part.match(/^(``?)\s?([\s\S]+?)\s?\1$/);
    if(cm && part.length>2) return "<code>"+esc(cm[2])+"</code>";
    return esc(part)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,'$1<a href="$2" target="_blank" rel="noopener">$2</a>')
      .replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>")
      .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g,"$1<i>$2</i>")
      .replace(/(^|[\s(])_([^_\s][^_]*)_(?=[\s).,;:]|$)/g,"$1<i>$2</i>");
  }).join("");
}
function mdBlock(md){
  const lines=String(md||"").replace(/\r/g,"").split("\n");
  const out=[]; let i=0, para=[];
  const flush=()=>{ if(para.length){ out.push("<p>"+mdSpan(para.join(" "))+"</p>"); para=[]; } };
  const cells=l=>l.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(c=>c.trim());
  while(i<lines.length){
    const l=lines[i];
    if(/^\s*```/.test(l)){
      flush(); const buf=[]; i++;
      while(i<lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++; out.push("<pre>"+esc(buf.join("\n"))+"</pre>"); continue;
    }
    let m=l.match(/^(#{1,6})\s+(.*)$/);
    if(m){ flush(); const h=Math.min(6,m[1].length+2); out.push(`<h${h}>${mdSpan(m[2])}</h${h}>`); i++; continue; }
    if(/^\s*\|/.test(l) && i+1<lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i+1])){
      flush(); const head=cells(l); i+=2; const body=[];
      while(i<lines.length && /^\s*\|/.test(lines[i])) body.push(cells(lines[i++]));
      out.push('<div class="tbl-scroll"><table><thead><tr>'+head.map(c=>"<th>"+mdSpan(c)+"</th>").join("")
        +"</tr></thead><tbody>"+body.map(r=>"<tr>"+r.map(c=>"<td>"+mdSpan(c)+"</td>").join("")+"</tr>").join("")
        +"</tbody></table></div>");
      continue;
    }
    if(/^\s*([-*+]|\d+[.)])\s+/.test(l)){
      flush(); const ordered=/^\s*\d/.test(l); const items=[];
      while(i<lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))){
        const li=lines[i].match(/^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$/);
        if(li) items.push({d:Math.min(3,Math.floor(li[1].length/2)), t:li[2]});
        else items[items.length-1].t+=" "+lines[i].trim();
        i++;
      }
      const tag=ordered?"ol":"ul";
      out.push(`<${tag}>`+items.map(x=>`<li style="margin-left:${x.d*16}px">${mdSpan(x.t.replace(/^\[( |x)\]\s*/i,(_,c)=>c.trim()?"☑ ":"☐ "))}</li>`).join("")+`</${tag}>`);
      continue;
    }
    if(/^\s*>/.test(l)){
      flush(); const buf=[];
      while(i<lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/,""));
      out.push("<blockquote>"+mdSpan(buf.join(" "))+"</blockquote>"); continue;
    }
    if(/^\s*(-{3,}|\*{3,})\s*$/.test(l)){ flush(); out.push("<hr>"); i++; continue; }
    if(!l.trim()){ flush(); i++; continue; }
    para.push(l.trim()); i++;
  }
  flush();
  return out.join("\n");
}
async function openNote(name){
  const meta=invMeta(name);
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<div class="note-modal" role="dialog" aria-label="${esc(name)}">
    <div class="note-h"><div><div class="note-t">${esc(meta.title||name)}</div>
      <div class="feature">${esc(name)}${meta.date?" · "+esc(formatPortalDate(meta.date)):""}${
        (meta.phs||[]).length?" · "+meta.phs.map(esc).join(", "):""}${
        (meta.keys||[]).length?" · "+meta.keys.map(esc).join(", "):""}</div></div>
      <button class="ghost-x note-x" title="Close (Esc)">✕</button></div>
    <div class="note-b md">Loading…</div></div>`;
  document.body.appendChild(ov);
  const close=()=>{ ov.remove(); document.removeEventListener("keydown",onKey); };
  const onKey=e=>{ if(e.key==="Escape") close(); };
  document.addEventListener("keydown",onKey);
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  ov.querySelector(".note-x").addEventListener("click",close);
  const body=ov.querySelector(".note-b");
  if(!SERVED){
    body.innerHTML=`<div class="ro-note">The note viewer needs the served portal (<code>portal.py serve</code>).<br>
      File: <code>~/.copilot-private/skills/bug-triage/investigations/${esc(name)}</code></div>`;
    return;
  }
  try{
    const r=await fetch("/api/investigation?name="+encodeURIComponent(name),{cache:"no-store"});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||r.status);
    body.innerHTML=mdBlock(j.text);
  }catch(err){ body.innerHTML=`<div class="stale-note">Could not load note: ${esc(err.message)}</div>`; }
}

function renderBugs(){
  if(bugTab===FAM_TAB){ renderFamilies(); return; }
  if(bugTab===PH_TAB){ renderPhenomena(); return; }
  renderBugStats();
  document.getElementById("bug-head").innerHTML='<th class="sel-col"><input type="checkbox" id="b-selall" title="Tick every bug shown"></th>'+BUG_COLS.map(c=>{
    const arr=bugSort.key===c.key?(bugSort.dir>0?"▲":"▼"):"";
    return `<th data-bkey="${c.key}">${esc(c.label)}<span class="arr">${arr}</span></th>`;
  }).join("");
  document.querySelectorAll("#bug-head th[data-bkey]").forEach(th=>th.addEventListener("click",()=>{
    const k=th.dataset.bkey;
    if(bugSort.key===k) bugSort.dir*=-1; else { bugSort.key=k; bugSort.dir=1; }
    const dd=document.getElementById("b-sort");
    if([...dd.options].some(o=>o.value===k)) dd.value=k;
    renderBugs();
  }));
  const rows=bugFilters().slice().sort((x,y)=>{
    const a=bugSortVal(x,bugSort.key), b=bugSortVal(y,bugSort.key);
    return (a<b?-1:a>b?1:0)*bugSort.dir || (x.key<y.key?-1:1);
  });
  const body=document.getElementById("bug-body");
  body.innerHTML="";
  if(!rows.length){
    body.innerHTML=`<tr><td class="empty" colspan="${BUG_COLS.length+1}">No bugs match the current filters.</td></tr>`;
  }
  // A tick survives a re-render only while its bug is still on screen, so a
  // bulk action can never reach a row the current filters hide.
  const shown=new Set(rows.map(b=>b.key));
  [...bugSel].forEach(k=>{ if(!shown.has(k)) bugSel.delete(k); });
  rows.forEach(b=>{
    const tr=document.createElement("tr"); tr.className="task"; tr.dataset.key=b.key;
    tr.innerHTML='<td class="sel-col"><input type="checkbox" class="b-sel" data-key="'+esc(b.key)+'"'
      +(bugSel.has(b.key)?" checked":"")+'></td>'+BUG_COLS.map(c=>bugCell(b,c.key)).join("");
    tr.querySelector(".b-sel").addEventListener("click",e=>{
      e.stopPropagation();
      if(e.target.checked) bugSel.add(b.key); else bugSel.delete(b.key);
      bugBulkSync();
    });
    const tpl=document.createElement("template");
    tpl.innerHTML=bugDetail(b,BUG_COLS.length+1);
    const det=tpl.content.firstChild;
    const open=bugOpenKeys.has(b.key);
    det.style.display=open?"":"none";
    if(open) wireBugEditor(det,b);
    tr.querySelectorAll("[data-goph]").forEach(el=>el.addEventListener("click",e=>{
      e.stopPropagation(); goPhenomenon(el.dataset.goph); }));
    tr.addEventListener("click",()=>{
      const show=det.style.display==="none";
      det.style.display=show?"":"none";
      if(show){ bugOpenKeys.add(b.key); if(!det.dataset.wired) wireBugEditor(det,b); }
      else bugOpenKeys.delete(b.key);
    });
    body.appendChild(tr); body.appendChild(det);
  });
  document.getElementById("bug-count").textContent=
    `Showing ${rows.length} of ${bugList().length} bugs`;
  const all=document.getElementById("b-selall");
  all.addEventListener("click",e=>{
    e.stopPropagation();
    rows.forEach(b=>{ if(all.checked) bugSel.add(b.key); else bugSel.delete(b.key); });
    body.querySelectorAll(".b-sel").forEach(cb=>{ cb.checked=all.checked; });
    bugBulkSync();
  });
  bugBulkSync();
}
// ---- bulk actions on ticked bugs (local monitoring fields only, never Jira) ----
const bugSel=new Set();
function bugBulkSync(){
  const bar=document.getElementById("b-bulk"); if(!bar) return;
  bar.hidden=!bugSel.size||bugTab===FAM_TAB||bugTab===PH_TAB;
  document.getElementById("b-bulk-n").textContent=bugSel.size+" ticked";
  const all=document.getElementById("b-selall"), n=document.querySelectorAll("#bug-body .b-sel").length;
  if(all){ all.checked=n>0&&bugSel.size>=n; all.indeterminate=bugSel.size>0&&bugSel.size<n; }
  bar.querySelectorAll("button:not(#b-bulk-clear),select").forEach(el=>{ el.disabled=!SERVED; });
}
async function bugBulkPost(payload,what){
  const keys=[...bugSel], msg=document.getElementById("b-bulk-msg");
  if(!keys.length||!SERVED) return;
  if(!confirm(what+" on "+keys.length+" bug"+(keys.length>1?"s":"")+"?\n\n"+keys.join(", ")
    +"\n\nLocal monitoring fields only \u2014 nothing is written to Jira.")) return;
  let ok=0, last=null; const bad=[];
  for(const k of keys){
    msg.textContent="Saving "+(ok+bad.length+1)+"/"+keys.length+"\u2026";
    try{
      const res=await fetch("/api/bug/"+encodeURIComponent(k),{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      const data=await res.json();
      if(res.ok){ ok++; last=data; } else bad.push(k+": "+(data.error||res.status));
    }catch(err){ bad.push(k+": "+err); }
  }
  if(last){ BUGS=last.bugs; buildBugFilters(); buildBugTabs(); buildSections(); renderBugs(); }
  document.getElementById("b-bulk-msg").textContent=what+": "+ok+" saved"+(bad.length?" \u00b7 failed "+bad.join("; "):"");
}
(function wireBugBulk(){
  const st=document.getElementById("b-bulk-state"); if(!st) return;
  st.innerHTML='<option value="">set triage\u2026</option>'+TRIAGE_STATES.map(s=>'<option>'+esc(s)+'</option>').join("");
  st.addEventListener("change",()=>{ const v=st.value; st.value=""; if(v) bugBulkPost({triage_state:v},"Set triage \u2192 "+v); });
  document.getElementById("b-bulk-ping").addEventListener("click",()=>bugBulkPost({ping:1},"+1 ping"));
  document.getElementById("b-bulk-clear").addEventListener("click",()=>{
    bugSel.clear(); document.querySelectorAll("#bug-body .b-sel").forEach(cb=>{ cb.checked=false; });
    document.getElementById("b-bulk-msg").textContent=""; bugBulkSync(); });
})();
msAuto();
["bq","b-owner","b-priority","b-component","b-family","b-evidence","b-origin"].forEach(id=>{
  const el=document.getElementById(id);
  el.addEventListener(id==="bq"?"input":"change",renderBugs);
});
document.getElementById("b-sort").addEventListener("change",e=>{
  bugSort.key=e.target.value; bugSort.dir=1; renderBugs(); });
["b-openonly","b-untriaged","b-flagged","b-hidemuted"].forEach(id=>{
  const cb=document.getElementById(id);
  cb.addEventListener("change",()=>{ cb.closest(".chip").classList.toggle("on",cb.checked); renderBugs(); });
});
document.getElementById("b-reset").addEventListener("click",()=>{
  document.getElementById("bq").value="";
  ["b-owner","b-priority","b-component","b-family","b-evidence","b-origin"].forEach(id=>{document.getElementById(id).value="";});
  document.querySelectorAll("#bug-signals input[data-sig]").forEach(cb=>{
    cb.checked=false; cb.closest(".chip").classList.remove("on"); });
  bugSigFilter.clear();
  [["b-untriaged",false],["b-flagged",false]].forEach(([id,on])=>{
    const cb=document.getElementById(id); cb.checked=on; cb.closest(".chip").classList.toggle("on",on); });
  applyBugTabPreset(bugTab);
  bugSort.key="signal"; bugSort.dir=1; document.getElementById("b-sort").value="signal";
  renderBugs();
});

// ============================================================================
// Overview — morning brief, live pulse and "what changed". The metrics are
// computed by assets/pulse.js from the section payloads already on the page.
// ============================================================================
const PBI = {blue:"#118DFF", navy:"#12239E", orange:"#E66C37", purple:"#6B007B",
             pink:"#E044A7", violet:"#744EC2", gold:"#D9B300", green:"#1AAB40",
             red:"#D64550", teal:"#197278", grey:"#9B9B9B"};

// Local-calendar YYYY-MM-DD. toISOString() would shift by the UTC offset and
// silently misplace "today" for anyone east of Greenwich.
function isoDay(d){
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")
         +"-"+String(d.getDate()).padStart(2,"0");
}

// ---- "what changed since your last visit" ----
// Every store already keeps a dated trail (bug change log, pr-monitor notes,
// task notes); this just merges them and hides what you have acknowledged.
// Dates are day-granular, so "seen" is a day watermark plus the ids of the
// entries already acknowledged on that day.
const CHG_KEY="teamPortal.changes";
function chgState(){
  try{ const v=JSON.parse(localStorage.getItem(CHG_KEY)||"null"); if(v&&v.since) return v; }catch(e){}
  return {since:isoDay(new Date(Date.now()-7*864e5)), seen:[]};
}
function chgEntries(){
  const out=[], clip=s=>String(s||"").replace(/\*\*|`/g,"").replace(/\s+/g," ").trim();
  bugList().forEach(b=>{
    (b.changes||[]).forEach(c=>out.push({d:c.at,k:"bug",id:b.key,tag:b.key,
      txt:(c.diff||[]).join(" \u00b7 "),ctx:b.summary}));
    if(b.first_seen) out.push({d:String(b.first_seen).slice(0,10),k:"bug",id:b.key,tag:b.key,
      txt:"new bug mirrored \u2014 "+(b.status||""),ctx:b.summary,isNew:true});
  });
  (PRS.features||[]).forEach(f=>(f.notes||[]).forEach(n=>out.push({d:n.date,k:"pr",id:String(f.id),
    tag:"F"+f.id,txt:(n.label?n.label+": ":"")+clip(n.text),ctx:f.name})));
  TASKS.forEach(t=>(t.notes||[]).forEach(n=>{
    const m=String(n).match(/^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?:?\s*(.*)$/s);
    if(m) out.push({d:m[1],tm:m[2]||"",k:"task",id:t.id,tag:t.id,txt:clip(m[3]),ctx:t.title});
  }));
  out.forEach(e=>{ e.h=e.k+"|"+e.id+"|"+e.d+"|"+e.txt.slice(0,80); });
  return out.filter(e=>e.d).sort((a,b)=>(b.d+(b.tm||"")).localeCompare(a.d+(a.tm||"")));
}
// --- morning brief (pure renderer; tests cut from here) ---
const briefStaleHours=()=>
  ((((BOOT.settings||{}).settings||{}).routine||{}).brief_stale_hours)||26;
function localDmyHm(ms){
  const d=new Date(ms); if(isNaN(d)) return "unknown time";
  const p=n=>String(n).padStart(2,"0");
  return `${p(d.getDate())}/${p(d.getMonth()+1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function briefHtml(brief, nowMs, staleHours=26){
  const e=s=>(s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  if(!brief) return {cap:"No daily maintenance run has completed yet. The timer runs daily at the time set in Settings.",
    body:'<div class="empty">No brief yet.</div>'};
  if(brief.error) return {cap:"", body:'<div class="brief-error">'+e(brief.error)+'</div>'};
  const gen=Date.parse(brief.generated_at||"");
  const when=isNaN(gen)?"unknown time":localDmyHm(gen);
  const stale=isNaN(gen)||(nowMs-gen)>staleHours*3600*1000;
  const run=brief.run||{};
  const cap="Daily maintenance run of "+e(when)+" \u00b7 status <b>"+e(run.status||"unknown")+"</b>"
    +(run.journal_id?" \u00b7 journal #"+e(run.journal_id):"")
    +(stale?' \u00b7 <span class="brief-stale">stale: no run in the last '+e(staleHours)+' h</span>':"");
  const steps=(brief.steps||[]).map(s=>'<span class="brief-step '+e(s.status)+'" title="'
    +e((s.summary||"")+(s.duration_s!=null?" ("+s.duration_s+"s)":""))+'">'+e(s.label||s.id)+'</span>').join("");
  const link=(it)=>{
    const l=it.link||{};
    if(l.k&&l.id!=null) return '<button type="button" class="brief-link" data-k="'+e(l.k)+'" data-id="'+e(l.id)+'">'+e(it.text)+'</button>';
    if(l.href&&/^https?:\/\//.test(l.href)) return '<a class="brief-link" href="'+e(l.href)+'" target="_blank" rel="noopener">'+e(it.text)+'</a>';
    return e(it.text);
  };
  const secs=(brief.sections||[]).map(s=>'<div class="brief-sec '+e(s.tone||"")+'"><h4>'+e(s.title)+'</h4>'
    +(s.summary?'<div class="brief-sum">'+e(s.summary)+'</div>':"")
    +((s.items||[]).length?'<ul>'+s.items.map(it=>'<li>'+link(it)+'</li>').join("")+'</ul>':"")+'</div>').join("");
  const ai=brief.ai&&brief.ai.text?'<div class="brief-ai"><h4>\u2726 AI summary'
    +(brief.ai.model?' <small>'+e(brief.ai.model)+'</small>':"")+'</h4>'
    +e(brief.ai.text).split(/\n{2,}/).map(p=>'<p>'+p.replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>")+'</p>').join("")+'</div>':"";
  return {cap, body:(steps?'<div class="brief-steps">'+steps+'</div>':"")+ai
    +(secs?'<div class="brief-grid">'+secs+'</div>':'<div class="empty">Nothing to report.</div>')};
}
// --- end morning brief ---
function renderBrief(){
  const box=document.getElementById("ov-brief"); if(!box) return;
  const out=briefHtml(OVERVIEW&&OVERVIEW.brief, Date.now(), briefStaleHours());
  document.getElementById("ov-brief-cap").innerHTML=out.cap;
  box.innerHTML=out.body;
  box.querySelectorAll(".brief-link[data-k]").forEach(el=>el.addEventListener("click",()=>goItem(el.dataset.k,el.dataset.id)));
}

let chgShowAll=false;
function renderChanges(){
  const box=document.getElementById("ov-changes"); if(!box) return;
  const st=chgState(), seen=new Set(st.seen||[]);
  const all=chgEntries().filter(e=>e.d>=st.since&&!seen.has(e.h));
  document.getElementById("ov-chg-cap").innerHTML=
    "Bug field changes, pr-monitor notes and task notes dated on or after <b>"+esc(formatPortalDate(st.since))+"</b>"
    +" that you have not marked seen \u00b7 "+all.length+" new"
    +(all.length?' <button class="ci-btn" id="chg-seen" title="Acknowledge everything listed">\u2713 mark all seen</button>':"")
    +' <button class="ci-btn" id="chg-back" title="Show the last 7 days again">\u21ba last 7 days</button>';
  const icon={bug:"\u2691",pr:"\u2197",task:"\u25a4"};
  const rows=chgShowAll?all:all.slice(0,10);
  let day="";
  box.innerHTML=all.length?'<div class="chg-list">'+rows.map(e=>{
    const head=e.d!==day?'<div class="chg-day">'+esc(formatPortalDate(e.d))+'</div>':""; day=e.d;
    return head+'<div class="chg-it chg-'+e.k+(e.isNew?" chg-new":"")+'" data-k="'+e.k+'" data-id="'+esc(e.id)+'">'
      +'<span class="chg-ico">'+icon[e.k]+'</span><span class="chg-tag">'+esc(e.tag)+'</span>'
      +'<span class="chg-txt">'+esc(e.txt)+'</span><span class="chg-ctx">'+esc(e.ctx||"")+'</span></div>';
  }).join("")+'</div>'
    +(all.length>10?'<button class="ci-btn" id="chg-more">'+(chgShowAll?"show fewer":"+"+(all.length-10)+" more")+'</button>':"")
    :'<div class="empty">Nothing new since '+esc(formatPortalDate(st.since))+'.</div>';
  box.querySelectorAll(".chg-it").forEach(el=>el.addEventListener("click",()=>goItem(el.dataset.k,el.dataset.id)));
  const more=document.getElementById("chg-more");
  if(more) more.onclick=()=>{ chgShowAll=!chgShowAll; renderChanges(); };
  const ok=document.getElementById("chg-seen");
  if(ok) ok.onclick=()=>{
    const today=isoDay(new Date());
    localStorage.setItem(CHG_KEY,JSON.stringify({since:today,
      seen:chgEntries().filter(e=>e.d>=today).map(e=>e.h)}));
    chgShowAll=false; renderChanges();
  };
  document.getElementById("chg-back").onclick=()=>{ localStorage.removeItem(CHG_KEY); renderChanges(); };
}

// ---- live pulse: one card per section, computed by PortalPulse ----
// The team payload is the shared audience (no private records); the settings
// status is kept apart from SETTINGS so a background refresh never clobbers an
// open Settings form.
let TEAMDATA = BOOT.team || {};
let PULSE_STATUS = null;
let pulseLastFetch = 0;
function pulseData(){
  return {tasks:TASKS, workspaces:WORKSPACES, bugs:BUGS, triage:TRIAGE, prs:PRS, raid:RAID,
    vmlog:VMLOG, team:TEAMDATA, actions:ACTION_UI.jobs,
    settings:{status:PULSE_STATUS||(SETTINGS&&SETTINGS.status)||null}, brief:(OVERVIEW||{}).brief};
}
function pulseShown(now){ return pulseVisible(PortalPulse.cards(pulseData(), now==null?Date.now():now), prefs); }
function pulseBaseline(){ const b=(OVERVIEW||{}).brief; return (b&&b.pulse&&b.pulse.values)?b.pulse:null; }
function pulseDelta(base, key, v, better){
  const d=PortalPulse.delta(base, key, v);
  if(!d) return "";
  const cls=better==null?"flat":((better==="down")===(d<0)?"good":"worse");
  return '<span class="pd '+cls+'" title="Change since the morning brief snapshot ('
    +esc(localDmyHm(Date.parse(base.taken_at)))+')">'+(d>0?"▲":"▼")+Math.abs(d)+'</span>';
}
function pulseCardHtml(c, base){
  const toneTxt={ok:"healthy",warn:"needs a look",bad:"needs action"}[c.tone]||"";
  const stats=c.stats.map(st=>'<div class="ps'+(st.tone?" "+st.tone:"")+'"'+(st.title?' title="'+esc(st.title)+'"':"")+'>'
    +'<dt>'+esc(st.label)+'</dt><dd>'+esc(st.text!=null?st.text:(st.v==null?"—":st.v))
    +pulseDelta(base, c.id+"."+st.k, st.v, st.better)+'</dd></div>').join("");
  const members=c.members?'<div class="pulse-members">'+c.members.map(a=>
      '<div class="pm"><a href="#member/'+encodeURIComponent(a.key)+'" data-pulse-member="'+esc(a.key)+'">'+esc(a.name)+'</a>'
      +PortalPulse.memberSummaryHtml(a, Date.now())+'</div>').join("")+'</div>':"";
  return '<article class="pulse-card tone-'+c.tone+(c.wide?" wide":"")+'" data-card="'+c.id+'">'
    +'<button type="button" class="pulse-head" data-pulse-go="'+c.section+'" title="Open '+esc(c.label)+'">'
    +'<span class="pi" aria-hidden="true">'+c.icon+'</span><span class="pl">'+esc(c.label)+'</span>'
    +'<span class="pt" title="'+esc(toneTxt)+'"><span class="sr-only">'+esc(toneTxt)+'</span></span></button>'
    +'<div class="pulse-main"><b class="pv">'+esc(c.value)+'</b>'+(c.total!=null?'<span class="ptot">/ '+esc(c.total)+'</span>':"")
    +pulseDelta(base, c.id, c.value, c.id==="team"||c.id==="workspaces"?null:"down")
    +'<span class="psub">'+esc(c.sub||"")+'</span></div>'
    +(c.note?'<div class="pulse-note" title="'+esc(c.noteTitle||"")+'">'+esc(c.note)+'</div>':"")
    +'<dl class="pulse-stats">'+stats+'</dl>'+members+'</article>';
}
function renderPulse(){
  const box=document.getElementById("ov-pulse"); if(!box) return;
  const base=pulseBaseline(), list=pulseShown();
  const cap=document.getElementById("ov-pulse-cap");
  if(cap) cap.innerHTML="Live counts from every section"
    +(base?" · ▲▼ change since the morning brief ("+esc(localDmyHm(Date.parse(base.taken_at)))+")":" · changes appear after the next daily routine")
    +' · <a href="#settings" data-pulse-go="settings">customize cards</a>';
  box.innerHTML=list.length?list.map(c=>pulseCardHtml(c,base)).join("")
    :'<div class="empty">All cards are hidden. <a href="#settings" data-pulse-go="settings">Choose cards in Settings</a>.</div>';
  document.querySelectorAll("#sec-overview [data-pulse-go]").forEach(el=>el.onclick=e=>{ e.preventDefault(); selectSection(el.dataset.pulseGo); });
  box.querySelectorAll("[data-pulse-member]").forEach(el=>el.onclick=e=>{ e.preventDefault(); goItem("member",el.dataset.pulseMember); });
}
function renderFresh(){
  const box=document.getElementById("ov-fresh"); if(!box) return;
  box.innerHTML='<span class="fresh-l">Data age</span>'+PortalPulse.freshness(pulseData(), Date.now()).map(f=>
    '<button type="button" class="fresh-chip'+(f.stale?" stale":"")+'" data-pulse-go="'+f.section+'" title="'
    +esc(f.label+(f.at?" last updated "+localDmyHm(f.at):" has no recorded update")+(f.stale?" — older than "+PortalPulse.STALE_HOURS+" h":""))+'">'
    +esc(f.label)+' <b>'+esc(f.ago)+'</b></button>').join("");
}
function renderOverview(){
  renderBrief();
  renderFresh();
  renderPulse();
  renderChanges();
  const asof=document.getElementById("ov-asof");
  if(asof) asof.textContent=(SERVED?"Live":"Snapshot")+" · "+localDmyHm(pulseLastFetch||Date.now())
    +(SERVED&&prefs.pulsePollMs?" · refreshes every "+(prefs.pulsePollMs/1000)+" s while open":"");
}
// Re-reads every saved payload the pulse needs. Pure reads: no Jira/GitHub
// scans, no workspace probes, no machine contact.
async function refreshOverviewData(){
  if(!SERVED) return false;
  const get=u=>fetch(u,{cache:"no-store"}).then(r=>r.ok?r.json():null).catch(()=>null);
  const [t,b,o,p,r,v,tr,tm,st]=await Promise.all(["/api/tasks","/api/bugs","/api/overview","/api/prs",
    "/api/raid","/api/vm-jobs","/api/triage","/api/team","/api/settings"].map(get));
  if(t){ TASKS=t.tasks||t; if(t.workspaces) WORKSPACES=t.workspaces; }
  if(b) BUGS=b; if(o) OVERVIEW=o; if(p) PRS=p; if(r) RAID=r; if(v) vmAdoptPayload(v);
  if(tr&&tr.phenomena) TRIAGE=tr;
  if(tm&&tm.members) TEAMDATA=tm;
  if(st&&st.status) PULSE_STATUS=st.status;
  pulseLastFetch=Date.now();
  return true;
}
let pulseTimer=null, pulseBusy=false;
async function pulseTick(force){
  if(!SERVED||activeSection!=="overview"||document.hidden||pulseBusy) return;
  if(!force&&Date.now()-pulseLastFetch<Math.max(5000,(prefs.pulsePollMs||60000)/2)) return;
  pulseBusy=true;
  try{ if(await refreshOverviewData()){ buildSections(); if(activeSection==="overview") renderOverview(); } }
  finally{ pulseBusy=false; }
}
function restartPulsePoll(){
  if(pulseTimer) clearInterval(pulseTimer);
  pulseTimer=SERVED&&prefs.pulsePollMs?setInterval(()=>pulseTick(true),prefs.pulsePollMs):null;
}
document.addEventListener("visibilitychange",()=>{ if(!document.hidden) pulseTick(false); });

// ============================================================================
// Portal section nav — Overview / Tasks / Workspaces / Bugs / PRs / Team
// ============================================================================
const SECTIONS=[
  {id:"overview", label:"Overview",   icon:"✦", group:"Overview", ready:true},
  {id:"work",     label:"Tasks",      icon:"▤", group:"Workspace", ready:true},
  {id:"ws",       label:"Workspaces", icon:"⌂", group:"Workspace", ready:true},
  {id:"bugs",     label:"Bugs",       icon:"⚑", group:"Live signals", ready:true},
  {id:"prs",      label:"PRs",        icon:"↗", group:"Live signals", ready:true},
  {id:"vm",       label:"Machines",   icon:"⌁", group:"Live signals", ready:true},
  {id:"raid",     label:"RAID",       icon:"◇", group:"Planning", ready:true},
  {id:"team",     label:"Team",       icon:"✧", group:"Planning", ready:true},
  {id:"settings", label:"Settings",   icon:"⚙", group:"Portal", ready:true},
];
const PORTAL_LAYOUT_KEY="team-portal-layout-v1";
function readPortalLayout(){
  try{
    const saved=JSON.parse(localStorage.getItem(PORTAL_LAYOUT_KEY)||"{}");
    return {
      activeSection:typeof saved.activeSection==="string"?saved.activeSection:"overview",
      sidebarCollapsed:saved.sidebarCollapsed===true,
      collapsedTiles:Array.isArray(saved.collapsedTiles)
        ? saved.collapsedTiles.filter(id=>typeof id==="string") : [],
    };
  }catch(error){
    console.warn("Unable to read saved portal layout preferences.",error);
    return {activeSection:"overview",sidebarCollapsed:false,collapsedTiles:[]};
  }
}
const portalLayout=readPortalLayout();
function savePortalLayout(changes){
  Object.assign(portalLayout,changes);
  try{
    localStorage.setItem(PORTAL_LAYOUT_KEY,JSON.stringify(portalLayout));
  }catch(error){
    console.warn("Unable to save portal layout preferences.",error);
  }
}
// --- browser prefs (pure: readPrefs, navSections) ---
// Browser preferences (Settings section). Validated on read so a hand-edited
// or older value can never break the page.
const PREFS_KEY="team-portal-prefs-v1";
const PREF_CHOICES={clock:["auto","12","24"], actionPollMs:[1000,1500,3000,5000,10000],
  vmPollMs:[5000,15000,30000,60000], pulsePollMs:[0,30000,60000,300000]};
const PREF_DEFAULTS={landing:"last", hidden:[], order:[], clock:"auto", compact:false,
  actionPollMs:1500, vmPollMs:15000, pulseHidden:[], pulseOrder:[], pulsePollMs:60000,
  vmHidden:[], vmPanelsHidden:[], machineTarget:""};
function readPrefs(raw, ids, cardIds=[]){
  const out={...PREF_DEFAULTS, hidden:[], order:[], pulseHidden:[], pulseOrder:[],
    vmHidden:[], vmPanelsHidden:[]};
  let v; try{ v=JSON.parse(raw||"{}"); }catch(_){ return out; }
  if(!v||typeof v!=="object") return out;
  if(v.landing==="last"||ids.includes(v.landing)) out.landing=v.landing;
  if(Array.isArray(v.hidden)) out.hidden=v.hidden.filter(id=>ids.includes(id)&&id!=="settings");
  if(Array.isArray(v.order)) out.order=[...new Set(v.order.filter(id=>ids.includes(id)))];
  if(Array.isArray(v.pulseHidden)) out.pulseHidden=[...new Set(v.pulseHidden.filter(id=>cardIds.includes(id)))];
  if(Array.isArray(v.pulseOrder)) out.pulseOrder=[...new Set(v.pulseOrder.filter(id=>cardIds.includes(id)))];
  for(const k of ["clock","actionPollMs","vmPollMs","pulsePollMs"]) if(PREF_CHOICES[k].includes(v[k])) out[k]=v[k];
  if(typeof v.compact==="boolean") out.compact=v.compact;
  if(Array.isArray(v.vmHidden)) out.vmHidden=[...new Set(v.vmHidden.filter(id=>
    typeof id==="string"&&/^i-[0-9a-f]{8,17}$/.test(id)))];
  if(Array.isArray(v.vmPanelsHidden)) out.vmPanelsHidden=[...new Set(v.vmPanelsHidden.filter(id=>
    ["vms","machines","machine-card"].includes(id)))];
  if(typeof v.machineTarget==="string") out.machineTarget=v.machineTarget;
  return out;
}
// Nav order: saved order first, then any section not yet placed, in default order.
function navSections(sections, p){
  const rank=id=>{ const i=p.order.indexOf(id); return i<0 ? p.order.length+sections.findIndex(s=>s.id===id) : i; };
  return sections.filter(s=>!p.hidden.includes(s.id)).sort((a,b)=>rank(a.id)-rank(b.id));
}
// Overview pulse cards: same rule — saved order first, unknown cards keep their default slot.
function pulseVisible(cards, p, all){
  const rank=id=>{ const i=p.pulseOrder.indexOf(id); return i<0 ? p.pulseOrder.length+cards.findIndex(c=>c.id===id) : i; };
  return cards.filter(c=>all||!p.pulseHidden.includes(c.id)).sort((a,b)=>rank(a.id)-rank(b.id));
}
// --- end browser prefs ---
const SECTION_IDS=SECTIONS.map(s=>s.id);
let prefs=(()=>{ try{ return readPrefs(localStorage.getItem(PREFS_KEY), SECTION_IDS, PortalPulse.CARD_IDS); }
  catch(_){ return readPrefs(null, SECTION_IDS, PortalPulse.CARD_IDS); } })();
function savePrefs(changes){
  prefs=readPrefs(JSON.stringify({...prefs,...changes}), SECTION_IDS, PortalPulse.CARD_IDS);
  try{ localStorage.setItem(PREFS_KEY,JSON.stringify(prefs)); }
  catch(error){ console.warn("Unable to save browser preferences.",error); }
}
const landingPick=prefs.landing==="last" ? portalLayout.activeSection : prefs.landing;
let activeSection=SECTIONS.some(s=>s.id===landingPick&&s.ready) ? landingPick : "overview";
const collapsedOverviewTiles=new Set(portalLayout.collapsedTiles);
function sectionCount(id){
  if(id==="overview") return pulseShown().filter(c=>c.tone==="bad").length;
  if(id==="work") return TASKS.filter(t=>!["done","cancelled"].includes(t.status)).length;
  if(id==="ws") return wsRoster().filter(s=>wsHolder(s.slot)||(s.status||{}).mirror).length;
  if(id==="bugs") return bugList().filter(b=>bugNeedsAttention(b)).length;
  if(id==="prs") return (PRS.features||[]).filter(f=>(f.blocked||[]).length).length;
  if(id==="vm") return (VMLOG.running||[]).length;
  if(id==="raid") return ((RAID.counts||{}).hot)||0;
  return 0;
}
function buildSections(){
  const box=document.getElementById("sections");
  const shown=navSections(SECTIONS, prefs);
  const groups=[...new Set(shown.map(s=>s.group))];
  box.innerHTML=groups.map(group=>{
    const items=shown.filter(s=>s.group===group).map(s=>{
      const n=sectionCount(s.id);
      const badge = s.id==="settings" ? ""
                  : s.ready ? `<span class="badge-count">${n}</span>`
                            : `<span class="badge-count">soon</span>`;
      const title = s.id==="bugs" ? "Bugs needing attention (stalled, untriaged, or unassigned)"
                  : s.id==="work" ? "Active tasks"
                  : s.id==="ws" ? "Tracked workspace slots that are currently occupied"
                  : s.id==="prs" ? "My outgoing feature PRs that are blocked (CI, review or rebase)"
                  : s.id==="vm" ? "Jobs delegated to a VM or lab machine that are still running"
                  : s.id==="raid" ? "Open RAID rows at Red or Orange"
                  : s.id==="overview" ? "Live pulse cards showing red (needs action); hidden cards are not counted"
                  : s.id==="settings" ? "Portal settings and browser preferences"
                  : "Team overview, planning and editable member records";
      return `<button class="section-btn${s.id===activeSection?" on":""}" data-section="${s.id}"
        title="${esc(title)}" aria-current="${s.id===activeSection?"page":"false"}"${s.ready?"":" disabled"}>
        <span class="section-icon" aria-hidden="true">${s.icon}</span>
        <span class="section-label">${esc(s.label)}</span>${badge}</button>`;
    }).join("");
    return `<div class="nav-group"><div class="nav-group-label">${esc(group)}</div>${items}</div>`;
  }).join("")+((PE_URL||GRAFANA.url||PLATFORM.url)?`<div class="nav-group"><div class="nav-group-label">Elsewhere</div>
    ${PE_URL?`<a class="section-btn pe-ext" href="${esc(PE_URL)}" target="_blank" rel="noopener"
       title="Oded's CI/CD pipeline explorer: queues, pipeline history, branch timeline, manifests">
      <span class="section-icon" aria-hidden="true">⇢</span>
      <span class="section-label">Pipelines</span><span class="pe-dot" title="checking…"></span></a>`:""}
    ${GRAFANA.url?`<a class="section-btn pe-ext" href="${esc(GRAFANA.url)}" target="_blank" rel="noopener"
       title="Grafana: CI runners, jobs, EC2 cost, DevOps dashboards">
      <span class="section-icon" aria-hidden="true">📈</span>
      <span class="section-label">Grafana</span><span class="gf-dot pe-dot" title="checking…"></span></a>`:""}
    ${PLATFORM.url?`<a class="section-btn pe-ext" href="${esc(PLATFORM.url)}" target="_blank" rel="noopener"
       title="Platform portal (SSO): machines, PLDM, DevOps self-service">
      <span class="section-icon" aria-hidden="true">⚙</span>
      <span class="section-label">Platform</span></a>`:""}</div>`:"");
  peProbe(); gfProbe();
  box.querySelectorAll(".section-btn[data-section]").forEach(b=>
    b.addEventListener("click",()=>selectSection(b.dataset.section)));
}
function initOverviewTiles(){
  document.querySelectorAll(".ov-canvas .tile[data-overview-tile]").forEach(tile=>{
    const heading=tile.querySelector("h3");
    if(!heading) return;
    const id=tile.dataset.overviewTile;
    const title=heading.textContent.trim();
    const toggle=document.createElement("button");
    toggle.type="button";
    toggle.className="tile-toggle";
    toggle.setAttribute("aria-expanded",String(!collapsedOverviewTiles.has(id)));
    toggle.setAttribute("aria-label",(collapsedOverviewTiles.has(id)?"Expand ":"Collapse ")+title);
    toggle.textContent=collapsedOverviewTiles.has(id)?"+":"−";
    tile.classList.toggle("is-collapsed",collapsedOverviewTiles.has(id));
    heading.append(toggle);
    toggle.addEventListener("click",()=>{
      const collapsed=tile.classList.toggle("is-collapsed");
      if(collapsed) collapsedOverviewTiles.add(id);
      else collapsedOverviewTiles.delete(id);
      toggle.setAttribute("aria-expanded",String(!collapsed));
      toggle.setAttribute("aria-label",(collapsed?"Expand ":"Collapse ")+title);
      toggle.textContent=collapsed?"+":"−";
      savePortalLayout({collapsedTiles:[...collapsedOverviewTiles]});
    });
  });
}
function selectSection(id){
  const sec=SECTIONS.find(s=>s.id===id);
  if(!sec || !sec.ready) return;
  if(id!=="team" && window.TeamUI) window.TeamUI.hidePrivate();
  activeSection=id;
  savePortalLayout({activeSection:id});
  setHash(id);
  document.querySelectorAll(".section-btn").forEach(b=>b.classList.toggle("on",b.dataset.section===id));
  document.querySelectorAll(".section-btn").forEach(b=>
    b.setAttribute("aria-current",b.dataset.section===id?"page":"false"));
  const section=SECTIONS.find(s=>s.id===id);
  const activeTitle=document.getElementById("active-section-title");
  const activeIcon=document.getElementById("active-section-icon");
  if(activeTitle) activeTitle.textContent=section.label;
  if(activeIcon) activeIcon.textContent=section.icon;
  aiSyncButton();
  const refresh=document.getElementById("refresh");
  if(refresh){
    refresh.innerHTML=`<span class="ico">↻</span> Refresh`;
    refresh.title="Reload saved portal data without checking live workspace status";
  }
  SECTIONS.forEach(s=>{
    const el=document.getElementById("sec-"+s.id);
    if(el) el.style.display = s.id===id ? "" : "none";
  });
  if(id==="bugs"){ buildBugFilters(); buildBugTabs(); selectBugTab(bugTab); }
  else if(id==="work"){ render(); }
  else if(id==="ws"){ renderWorkspaces(); }
  else if(id==="prs"){ renderPrs(); }
  else if(id==="vm"){ renderVmlog(); }
  else if(id==="raid"){ renderRaid(); }
  else if(id==="overview"){ renderOverview(); pulseTick(false); }
  else if(id==="team" && window.TeamUI){ window.TeamUI.render(); }
  else if(id==="settings"){ renderSettings(); }
  const secRoot = document.getElementById("sec-"+id);
  if(secRoot) applyTableStandards(secRoot);
  // Leaving the VM section stops its stopwatch and its poll; renderVmlog()
  // restarts both on the way back in.
  if(id!=="vm") vmTimersStop();
}

// ---- refresh: reload entry values in place, keeping filters/tab/sort/columns ----
function captureFilterState(){
  const s={}; DD_GROUPS.forEach(g=>{ s[g]=checkedVals(g); }); return s;
}
function restoreFilterState(s){
  DD_GROUPS.forEach(g=>{
    document.querySelectorAll(`input[data-group="${g}"]`).forEach(cb=>{
      cb.checked = !!(s[g] && s[g].includes(cb.value));
    });
    updateDdLabel(g);
  });
}
// ============================================================================
// PRs section — outgoing feature PRs mirrored from pr-monitor
// ============================================================================
const PR_STATUS_COLOR={merged:"#1AAB40", "merge-queue":"#118DFF", approved:"#197278", pending:"#9B9B9B",
  "needs-rebase":"#D9B300", "changes-requested":"#E66C37", "ci-fail":"#D64550",
  closed:"#6B7280"};
const PR_STATUS_ORDER=["ci-fail","changes-requested","needs-rebase","pending","approved","merge-queue","merged","closed"];
const PLDM_COLOR={passed:"#1AAB40", failed:"#D64550", running:"#118DFF",
  queued:"#D9B300", cancelled:"#9B9B9B"};

// PLDM Job Queue live dashboard (Grafana). Deep-linked to the iridium project,
// last 2 days — enough to spot the run you just dispatched by user/display name.
const GRAFANA_PLDM="https://grafana.elmnt.local/d/pldm-job-queue-v1/pldm-job-queue"
  +"?orgId=1&from=now-2d&to=now&timezone=browser&var-project=iridium"
  +"&var-artifact=All&var-image_type=All&var-status=All&refresh=30s";
const PR_COLLAPSED=new Set();
let PR_COLLAPSE_INIT=false;
// Feature ids whose "Edit feature" editor is expanded; collapsed by default.
const PR_EDIT_OPEN=new Set();
const PR_DETAIL_OPEN=new Set();
// Ticked feature ids for "Scan selected PRs"; survives re-renders and filtering.
const PR_SELECTED=new Set();
// Each multi-select filter holds the ticked values; an empty set means "all".
const PR_FILTERS={ q:"", feature:new Set(), priority:new Set(), pldm:new Set(), repo:new Set(),
  prStatus:new Set(), openOnly:false, wired:false };
function installOpenOnlyControls(){
  const taskLabel=document.getElementById("o-hidedone").closest(".chip");
  taskLabel.lastChild.textContent=" Open only";

  const makeOpenOnly=id=>{
    const label=document.createElement("label");
    label.className="chip";
    const input=document.createElement("input");
    input.type="checkbox";
    input.id=id;
    label.append(input,document.createTextNode(" Open only"));
    return label;
  };
  document.querySelector(".pr-controls .row").append(makeOpenOnly("pr-openonly"));

  document.getElementById("vm-livefirst").closest("label").after(makeOpenOnly("vm-openonly"));
  const vmCount=document.createElement("div");
  vmCount.className="count";
  vmCount.id="vm-count";
  document.getElementById("vm-tbl").before(vmCount);

  const raidInput=document.getElementById("raid-showclosed");
  const raidLabel=raidInput.closest(".chip");
  raidInput.id="raid-openonly";
  raidInput.checked=true;
  raidLabel.classList.add("on");
  raidLabel.lastChild.textContent=" Open only";
}
installOpenOnlyControls();
// The submit flow stamps the dispatched GitHub Actions run URL into pldm_details;
// pull it back out so the badge can deep-link to that specific CI run, not a portal.
function pldmRunUrl(det){
  const m=(det||"").match(/https:\/\/github\.com\/\S+\/actions\/runs\/\d+/);
  return m?m[0]:"";
}
function pldmBadge(f){
  if(!f.pldm||f.pldm==="not-tested") return '<span class="pldm">PLDM untested</span>';
  const col=PLDM_COLOR[f.pldm]||PBI.grey;
  const badge='<span class="pldm" style="background:'+col+';color:#fff;" title="'
    +esc(f.pldm_details||"")+'">PLDM '+esc(f.pldm)+'</span>';
  const url=pldmRunUrl(f.pldm_details);
  const links=[];
  if(url) links.push('<a class="pldm-link" href="'+esc(url)
    +'" target="_blank" rel="noopener" title="'+esc(f.pldm_details||"")+'">\u2197 CI run</a>');
  links.push('<a class="pldm-link" href="'+GRAFANA_PLDM
    +'" target="_blank" rel="noopener" title="PLDM Job Queue live dashboard (Grafana)">\U0001F4CA Grafana</a>');
  return badge+links.join("");
}

function renderPrs(){
  const feats=PRS.features||[], counts=PRS.counts||{};
  ciTriageLoad(false);
  if(!PR_COLLAPSE_INIT){
    feats.forEach(f=>PR_COLLAPSED.add(String(f.id)));
    PR_COLLAPSE_INIT=true;
  }
  const n=s=>counts[s]||0;
  const totalPrs=feats.reduce((a,f)=>a+f.total,0);
  const blocked=feats.filter(f=>(f.blocked||[]).length);
  const done=feats.filter(f=>f.total&&f.merged===f.total);
  document.getElementById("pr-asof").innerHTML=
    feats.length+" active feature"+(feats.length===1?"":"s")+" \u00b7 "+totalPrs+" PRs \u00b7 "
    +(PRS.last_scan?"scanned <b>"+esc(PRS.last_scan)+"</b>":"<b>never scanned</b>")
    +(PRS.error?' \u00b7 <span style="color:'+PBI.red+'">'+esc(PRS.error)+'</span>':"");

  document.getElementById("pr-kpis").innerHTML=[
    kpi({c:blocked.length?PBI.red:PBI.green, label:"Blocked features", n:blocked.length,
         sub:"of "+feats.length+" tracked"}),
    kpi({c:n("ci-fail")?PBI.red:PBI.grey, label:"CI failing", n:n("ci-fail"), sub:"PRs"}),
    kpi({c:n("needs-rebase")?PBI.gold:PBI.grey, label:"Needs rebase", n:n("needs-rebase"), sub:"PRs"}),
    kpi({c:n("changes-requested")?PBI.orange:PBI.grey, label:"Changes req.",
         n:n("changes-requested"), sub:"PRs"}),
    kpi({c:PBI.teal, label:"Approved", n:n("approved"), sub:"waiting to land"}),
    kpi({c:n("merge-queue")?PBI.blue:PBI.grey, label:"Merge queue", n:n("merge-queue"),
         sub:"landing now"}),
    kpi({c:PBI.green, label:"Merged", n:n("merged"),
         sub:done.length?done.length+" feature"+(done.length===1?"":"s")+" complete":"of "+totalPrs+" PRs"}),
  ].join("");

  syncPrFilters(feats);
  const visible=feats.filter(prFeatureMatchesFilters);
  document.getElementById("pr-count").textContent=
    visible.length+" of "+feats.length+" feature"+(feats.length===1?"":"s")+" shown";
  document.getElementById("pr-body").innerHTML=visible.length
    ? visible.map(prFeatureRow).join("")
    : '<tr><td colspan="6" class="empty">No feature matches these filters.</td></tr>';
  wirePrCollapse();
  document.querySelectorAll("#pr-body details.pr-edit-wrap").forEach(d=>{
    d.addEventListener("toggle",()=>{
      if(d.open) PR_EDIT_OPEN.add(d.dataset.fid); else PR_EDIT_OPEN.delete(d.dataset.fid);
    });
    document.querySelectorAll("#pr-body details[data-pr-disclosure]").forEach(d=>{
      d.addEventListener("toggle",()=>{
        if(d.open) PR_DETAIL_OPEN.add(d.dataset.prDisclosure);
        else PR_DETAIL_OPEN.delete(d.dataset.prDisclosure);
      });
    });
  });
  const live=new Set(feats.map(f=>String(f.id)));
  [...PR_SELECTED].forEach(id=>{ if(!live.has(id)) PR_SELECTED.delete(id); });
  syncPrSelection();
  applyTableStandards(document.getElementById("sec-prs"));
}

function syncPrSelection(){
  const btn=document.getElementById("pr-scan-sel");
  const n=PR_SELECTED.size;
  if(btn){
    btn.hidden=!n;
    if(!btn.disabled) btn.textContent="\u21bb Scan selected PRs ("+n+")";
  }
  const boxes=[...document.querySelectorAll("#pr-body .prf-pick")];
  const all=document.getElementById("pr-sel-all");
  if(all){
    const on=boxes.filter(b=>b.checked).length;
    all.checked=!!boxes.length&&on===boxes.length;
    all.indeterminate=on>0&&on<boxes.length;
  }
}

// Collapsed features keep their PR table in the DOM (hidden) so the CI buttons
// stay wired; only the id set is state.

function prCollapsed(id){ return PR_COLLAPSED.has(String(id)); }

function setPrCollapsed(id, on){
  const key=String(id);
  if(on) PR_COLLAPSED.add(key); else PR_COLLAPSED.delete(key);
  const summary=document.querySelector('tr.prf-row[data-fid="'+CSS.escape(key)+'"]');
  const detail=document.querySelector('tr.prf-detail[data-fid="'+CSS.escape(key)+'"]');
  if(!summary || !detail) return;
  summary.classList.toggle("collapsed", on);
  detail.hidden=on;
  const btn=summary.querySelector(".prf-toggle");
  if(btn){
    btn.textContent=on?"\u25b8":"\u25be";
    btn.title=on?"Expand feature details":"Collapse feature details";
    btn.setAttribute("aria-expanded",String(!on));
  }
  syncPrCollapseAll();
}

function syncPrCollapseAll(){
  const btn=document.getElementById("pr-collapse");
  if(!btn) return;
  const feats=PRS.features||[];
  const allOn=feats.length>0 && feats.every(f=>prCollapsed(f.id));
  btn.textContent=allOn?"\u21f2 Expand all":"\u21f1 Collapse all";
  btn.dataset.expand=allOn?"1":"";
}

function wirePrCollapse(){
  syncPrCollapseAll();
}

function prFeatureState(f){
  if(f.total && f.merged===f.total) return "complete";
  if((f.blocked||[]).length) return "blocked";
  if(f.worst==="approved"||f.worst==="merge-queue") return "ready";
  return "active";
}

// Rebuilds one dropdown-with-tick-boxes. A ticked value that no longer occurs
// stays listed (count 0) so it can still be unticked.
function prDd(key, rows){
  const panel=document.getElementById("pf-"+key);
  if(!panel) return;
  const sel=PR_FILTERS[key];
  [...sel].forEach(v=>{ if(!rows.some(r=>r.v===v)) rows.push({v, l:v, n:0}); });
  panel.innerHTML=rows.map(r=>'<label class="dd-opt"><input type="checkbox" data-prf="'+key+'" value="'
      +esc(r.v)+'"'+(sel.has(r.v)?" checked":"")+'> '+esc(r.l)
      +' <span class="dd-n">'+r.n+'</span></label>').join("")
    +(sel.size?'<button type="button" class="dd-clear" data-prf-clear="'+key+'">Clear</button>':"");
  const btn=document.querySelector("#dd-pr-"+key+" .dd-btn");
  btn.classList.toggle("has-sel", sel.size>0);
  btn.querySelector(".dd-label").textContent = !sel.size ? "All"
    : sel.size===1 ? (rows.find(r=>sel.has(r.v))||{l:[...sel][0]}).l : sel.size+" selected";
}

function syncPrFilters(feats){
  const cnt=fn=>v=>feats.filter(f=>fn(f,v)).length;
  const ordered=(vals,order)=>vals.sort((a,b)=>{
    const ai=order.indexOf(a), bi=order.indexOf(b);
    return (ai<0?999:ai)-(bi<0?999:bi) || a.localeCompare(b);
  });
  const repos=[...new Set(feats.flatMap(f=>(f.prs||[]).map(p=>p.repo)).filter(Boolean))].sort();
  const prStates=ordered([...new Set(feats.flatMap(f=>(f.prs||[]).map(p=>p.status)).filter(Boolean))],
    PR_STATUS_ORDER);
  const pldm=[...new Set(feats.map(f=>f.pldm||"not-tested"))].sort();
  const n1=cnt((f,v)=>prFeatureState(f)===v);
  prDd("feature",["blocked","active","ready","complete"].map(v=>({v,l:v,n:n1(v)})));
  const n2=cnt((f,v)=>(f.priority||"medium")===v);
  prDd("priority",PRI_ORDER.map(v=>({v,l:v,n:n2(v)})));
  const n3=cnt((f,v)=>(f.pldm||"not-tested")===v);
  prDd("pldm",pldm.map(v=>({v,l:v,n:n3(v)})));
  const n4=cnt((f,v)=>(f.prs||[]).some(p=>p.repo===v));
  prDd("repo",repos.map(v=>({v,l:v,n:n4(v)})));
  const n5=cnt((f,v)=>(f.prs||[]).some(p=>p.status===v));
  prDd("prStatus",prStates.map(v=>({v,l:v,n:n5(v)})));
  wirePrFilters();
}

function wirePrFilters(){
  if(PR_FILTERS.wired) return;
  PR_FILTERS.wired=true;
  document.getElementById("pr-q").addEventListener("input",e=>{
    PR_FILTERS.q=(e.target.value||"").trim().toLowerCase();
    renderPrs();
  });
  const ctl=document.querySelector(".pr-controls");
  ctl.addEventListener("change",e=>{
    if(e.target.id==="pr-openonly"){
      PR_FILTERS.openOnly=e.target.checked;
      e.target.closest(".chip").classList.toggle("on",e.target.checked);
      renderPrs();
      return;
    }
    const key=e.target.dataset.prf;
    if(!key) return;
    e.target.checked?PR_FILTERS[key].add(e.target.value):PR_FILTERS[key].delete(e.target.value);
    renderPrs();
  });
  // Bound per panel: the shared .dd-panel handler stops click propagation.
  ctl.querySelectorAll(".dd-panel").forEach(panel=>panel.addEventListener("click",e=>{
    const key=e.target.dataset&&e.target.dataset.prfClear;
    if(!key) return;
    PR_FILTERS[key].clear();
    renderPrs();
  }));
}

function prFeatureMatchesFilters(f){
  const F=PR_FILTERS;
  const prs=prVisibleRows(f);
  if(F.feature.size && !F.feature.has(prFeatureState(f))) return false;
  if(F.priority.size && !F.priority.has(f.priority||"medium")) return false;
  if(F.pldm.size && !F.pldm.has(f.pldm||"not-tested")) return false;
  if(F.openOnly && !prs.length) return false;
  if(F.repo.size && !prs.some(p=>F.repo.has(p.repo))) return false;
  if(F.prStatus.size && !prs.some(p=>F.prStatus.has(p.status))) return false;
  if(PR_FILTERS.q){
    const hay=[f.name||"",f.summary||"",f.description||"",...(f.jira||[]),
      ...(f.notes||[]).map(n=>(n.label||"")+" "+(n.text||"")),
      ...prs.flatMap(p=>[p.repo||"",p.title||"",String(p.number||"")])]
      .join(" ").toLowerCase();
    if(!hay.includes(PR_FILTERS.q)) return false;
  }
  return true;
}

function prVisibleRows(f){
  const prs=f.prs||[];
  return PR_FILTERS.openOnly
    ? prs.filter(p=>p.status!=="merged"&&p.status!=="closed")
    : prs;
}

// CI never runs on its own here — it fires on a `/ci` comment — so "not run" is
// a real, actionable state rather than a gap in the data.
function ciCell(p){
  const f=p.checks_fail||0, r=p.checks_pending||0, s=p.checks_pass||0;
  let label,color,tip;
  if(f){ label="fail"; color=PBI.red;
         tip=f+" failing: "+((p.failed_checks||[]).join(", ")||"see PR"); }
  else if(r){ label="running"; color=PBI.gold; tip=r+" check"+(r===1?"":"s")+" still running"; }
  else if(s){ label="pass"; color=PBI.green; tip="all "+s+" checks green"; }
  else { return '<span class="ci none" title="No check runs on this commit '
           +'\u2014 comment /ci on the PR to trigger it">not run</span>'; }
  return '<span class="ci" style="--ck:'+color+'" title="'+esc(tip)+'">'+label+'</span>';
}

function prRows(f){
  return prVisibleRows(f).map(p=>{
    const sc=PR_STATUS_COLOR[p.status]||PBI.grey;
    let detail="\u2014";
    if(p.checks_fail) detail='<b>'+p.checks_fail+'F</b>/'+(p.checks_pass||0)+'P';
    else if(p.checks_pass) detail=p.checks_pass+'P'+(p.checks_pending?' \u00b7 '+p.checks_pending+' run':'');
    const ci=ciCell(p);
    const gate=p.status==="merged"?"\u2014"
      :p.status==="merge-queue"?"in queue"
      :p.auto_merge?"auto-merge"
      :(p.merge_state==="BEHIND"||p.merge_state==="DIRTY")?(p.merge_state||"").toLowerCase()
      :(p.review||"").toLowerCase().replace(/_/g," ")||"\u2014";
    const canCi=SERVED&&p.status!=="merged"&&p.status!=="closed";
    const ciBtn=canCi
      ? ' <button class="ci-btn" data-ci-one="'+f.id+'" data-ci-num="'+p.number+'" '
        +'title="Post a /ci comment to (re-)run CI on this PR">\u21bb ci</button>' : "";
    const rmBtn=SERVED
      ? ' <button class="pr-x" data-prx="'+f.id+'" data-prx-num="'+p.number+'" '
        +'data-prx-repo="'+esc(p.repo)+'" title="Stop tracking this PR \u2014 the PR itself is untouched">\u2715</button>' : "";
    const run=(p.failed_runs||[])[0];
    const why=(p.checks_fail&&run&&PE_URL)
      ? ' <a class="ci-btn why" href="'+esc(peRun(run))+'" target="_blank" rel="noopener" '
        +'title="Open the failing run in the pipeline explorer: jobs, logs and failure analysis">why red \u2192</a>' : "";
    const sher=(SERVED&&p.checks_fail&&run&&/\/actions\/runs\/\d+\/job\/\d+/.test(run))
      ? ' <button class="ci-btn" data-sherlock="'+esc(run)+'" '
        +'title="Classify this failing job with octl ci sherlock (read-only)">sherlock</button>' : "";
    const stage=(SERVED&&p.url)
      ? ' <button class="pr-x" data-prstage="'+esc(p.url)+'" '
        +'title="octl ci pr-status: is this PR in a version, did integration pass, is it in CD (read-only)">stage</button>' : "";
    const pipe=(PE_URL&&p.url)
      ? ' <a class="pr-x pe-pipe" href="'+esc(pePr(p.url))+'" target="_blank" rel="noopener" '
        +'title="Where this PR is in the pipeline: CI, merge queue, integration, CD, delivery (pipeline explorer)">\u21e2</a>' : "";
    return '<tr><td><a href="'+esc(p.url||"#")+'" target="_blank" rel="noopener" title="'+esc(p.title||"")+'">'
      +esc(p.repo)+' #'+p.number+'</a><span class="pr-row-tools">'+pipe+stage+ciBtn+rmBtn+'</span>'
      +'<div class="devops-out" hidden></div></td>'
      +'<td class="pr-title" title="'+esc(p.title||"")+'">'+esc(p.title||"")+'</td>'
      +'<td>'+ci+why+sher+'</td>'
      +'<td class="chk">'+detail+'</td>'
      +'<td class="chk">'+esc(gate)+'</td>'
      +'<td><span class="st" style="--sk:'+sc+'">'+esc(p.status||"?")+'</span></td></tr>';
  }).join("");
}

function prJiraSyncResults(results){
  return (results||[]).map(row=>'<div class="ro-note" role="status">Last Jira sync result · '+esc(row.key)
    +' · '+esc(row.state)+(row.target?' → '+esc(row.target):'')
    +': '+esc(row.message||"")+'</div>').join("");
}

function prJiraTickets(f){
  return (f.jira||[]).map(key=>{
    const confirmed=(f.jira_coverage||[]).includes(key);
    return '<span><a class="jira-chip" href="'+esc(JIRA_BASE+encodeURIComponent(key))
      +'" target="_blank" rel="noopener">'+esc(key)+'</a>'
      +(confirmed?' <span class="muted">Full PR coverage confirmed</span>':'')
      +(SERVED?'<button type="button" class="ci-btn" data-jira-coverage="'+esc(f.id)
        +'" data-jira-key="'+esc(key)+'">'
        +(confirmed?'Revoke coverage':'Confirm full PR coverage')+'</button>':'')+'</span>';
  }).join("")||'<span class="muted">no ticket</span>';
}

// ---- AI CI triage (pr-failure-analyzer): per feature, on click only ----
let CI_TRIAGES={}, CI_TRIAGE_AT=0;
async function ciTriageLoad(force){
  if(!SERVED||(!force&&Date.now()-CI_TRIAGE_AT<30000)) return;
  CI_TRIAGE_AT=Date.now();
  try{
    const r=await fetch("/api/ai/ci-triages",{cache:"no-store"});
    if(!r.ok) return;
    const next=(await r.json()).triages||{};
    if(JSON.stringify(next)!==JSON.stringify(CI_TRIAGES)){ CI_TRIAGES=next; renderPrs(); }
  }catch(e){}
}
function ciTriageHtml(f,failCi){
  if(!SERVED||AI_USAGE.enabled===false||!failCi) return "";
  const t=CI_TRIAGES[String(f.id)];
  const busy=t&&(t.status==="queued"||t.status==="running");
  const done=t&&t.status==="done";
  const badge=t&&(busy||t.verdict)
    ? '<button class="ci-triage-badge v-'+esc(busy?"busy":t.verdict)+(t.stale?" stale":"")+'" data-ci-triage-open="'+esc(t.run_id)+'" title="'
      +esc(busy?"AI triage in progress \u2014 open":(t.summary||t.verdict)+(t.stale?" (CI changed since \u2014 re-triage)":""))+'">\u2726 '
      +esc(busy?"triaging\u2026":t.verdict)+'</button>' : "";
  const btn=busy?"":'<button class="ci-btn ai" data-ci-triage="'+f.id+'"'+(done?' data-force="1"':'')
    +' title="Ask AI (pr-failure-analyzer) why the '+failCi+' failing PR(s) are red. Read-only; adds a verdict note.">'
    +'\u2726 '+(done?"Re-triage":"Triage CI")+' ('+failCi+')</button>';
  return '<span class="ci-triage">'+badge+btn+'</span>';
}
async function ciTriage(fid,force,btn){
  if(btn) btn.disabled=true;
  try{
    const r=await fetch("/api/ai/ci-triage",{method:"POST",headers:aiHeaders(),
      body:JSON.stringify({feature_id:fid,force:!!force})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    ciTriageLoad(true);
    if(j.cached&&!j.job){
      const run=await (await fetch("/api/ai/run/"+encodeURIComponent(j.run_id),{cache:"no-store",headers:aiHeaders()})).json();
      if(run.status!=="queued"&&run.status!=="running"){ aiShowRun(j.run_id); return; }
    }
    AI.devops=false; AI.section=activeSection; aiOpen();
    const turn={id:j.run_id,kind:"ci-triage",question:"Triage CI",status:"queued",progress:"queued"};
    AI.thread=[turn]; AI.busy=true; aiRenderThread();
    if(j.job) await aiWait(j.job,j.run_id,turn);
    else await aiWaitRun(j.run_id,turn);
    AI.busy=false; aiRenderThread(); ciTriageLoad(true);
  }catch(e){
    AI.busy=false;
    notifyCard({title:"AI CI triage",body:String(e.message||e),tone:"bad"});
  }finally{ if(btn) btn.disabled=false; }
}
async function aiWaitRun(runId,turn){
  for(;;){
    await new Promise(res=>setTimeout(res,2500));
    let run={};
    try{ run=await (await fetch("/api/ai/run/"+encodeURIComponent(runId),{cache:"no-store",headers:aiHeaders()})).json(); }catch(e){}
    if(run.status&&run.status!=="queued"&&run.status!=="running"){ Object.assign(turn,run); return; }
    turn.progress=run.status||"waiting\u2026"; aiRenderThread();
  }
}
document.addEventListener("click",e=>{
  const open=e.target.closest("[data-ci-triage-open]");
  if(open){ e.preventDefault(); aiShowRun(open.dataset.ciTriageOpen); return; }
  const t=e.target.closest("[data-ci-triage]");
  if(t){ e.preventDefault(); ciTriage(parseInt(t.dataset.ciTriage,10),t.dataset.force==="1",t); }
});
function prFeatureRow(f){
  const visiblePrs=prVisibleRows(f);
  const c=PR_STATUS_COLOR[f.worst]||PBI.grey;
  const complete=f.total&&f.merged===f.total;
  const pldm=pldmBadge(f);
  const testable=SERVED&&f.prs.some(p=>p.status!=="closed");
  const pldmBtn=testable
    ? '<button class="pldm-btn" data-pldm="'+f.id+'" title="Grab a free workspace slot, '
      +'check it out to this feature\'s PR branches, and dispatch a remote PLDM build">'
      +'\u25b6 PLDM test</button>' : "";
  // Feature-level CI re-runs: one for every open PR, one for only the failing.
  const openCi=f.prs.filter(p=>p.status!=="merged"&&p.status!=="closed").length;
  const failCi=f.prs.filter(p=>p.status==="ci-fail").length;
  const ciAll=SERVED&&openCi
    ? '<button class="ci-btn" data-ci-all="'+f.id+'" title="Post /ci on all '+openCi
      +' open PR(s) of this feature">\u21bb CI all ('+openCi+')</button>' : "";
  const ciFail=SERVED&&failCi
    ? '<button class="ci-btn bad" data-ci-fail="'+f.id+'" title="Post /ci only on the '+failCi
      +' failing PR(s)">\u21bb CI failed ('+failCi+')</button>' : "";
  const addBtn=SERVED
    ? '<button class="ci-btn" data-prtrack="'+f.id+'" title="Track another PR on this '
      +'feature">\uff0b PR</button>' : "";
  const dropBtn=SERVED
    ? '<button class="prf-untrack" data-prdrop="'+f.id+'" data-prdrop-name="'+esc(f.name)+'" '
      +'title="Drop this feature from active tracking">Stop tracking feature</button>' : "";
  const collapsed=prCollapsed(f.id);
  const state=complete?"complete":f.worst;
  const prio=f.priority||"medium";
  const prioPill='<span class="badge p-'+esc(prio)+'"'
    +(f.priority_derived?' title="Default \u2014 not set yet; pick one in the editor"':'')+'>'+esc(prio)+'</span>';
  const edit=SERVED
    ? '<details class="pr-edit-wrap" data-fid="'+esc(f.id)+'"'+(PR_EDIT_OPEN.has(String(f.id))?" open":"")+'>'
      +'<summary>Edit feature</summary>'
      +'<div class="editor pr-edit" data-fid="'+esc(f.id)+'"><div class="fields">'
      +'<label>Name<input type="text" class="pe-name" data-orig="'+esc(f.name)+'" value="'+esc(f.name)+'"></label>'
      +'<label>Priority<select class="pe-priority" data-orig="'+esc(prio)+'">'
      +PRI_ORDER.map(p=>'<option value="'+p+'"'+(p===prio?" selected":"")+'>'+p+'</option>').join("")
      +'</select></label>'
      +'<label>Jira<input type="text" class="pe-jira" data-orig="'+esc((f.jira||[]).join(", "))+'" '
      +'value="'+esc((f.jira||[]).join(", "))+'" placeholder="e.g. IPPCIESW-684, IPPCIESW-700"></label>'
      +'</div><div class="fields">'
      +'<label class="wide">Summary<input type="text" class="pe-summary" data-orig="'+esc(f.summary||"")+'" '
      +'value="'+esc(f.summary||"")+'" placeholder="One line: what the feature does"></label>'
      +'</div><div class="fields">'
      +'<label class="wide">Technical description<textarea class="pe-desc" rows="3" data-orig="'+esc(f.description||"")+'">'
      +esc(f.description||"")+'</textarea></label>'
      +'</div><div class="fields">'
      +'<label class="tag">Note tag<input type="text" class="pe-note-label" placeholder="e.g. rebase + /ci"></label>'
      +'<label class="wide">Add note<input type="text" class="pe-note" placeholder="Append to the activity history\u2026"></label>'
      +'</div><div class="actions"><button class="primary" data-prsave="'+f.id+'">Save</button>'
      +'<span class="msg" id="pr-edit-msg-'+f.id+'"></span></div></div></details>'
    : '<div class="ro-note">Serve the portal to edit the feature or add notes.</div>';
  const jiraHtml=prJiraTickets(f);
  const notes=f.notes||[];
  const lastAct=notes.length?notes[0].date:(f.logged||"\u2014");
  const fullDesc=f.description&&f.description!==f.summary
    ? '<details class="prf-desc"><summary>Technical description</summary><p>'+mdInline(f.description)+'</p></details>' : "";
  const summaryHtml='<div class="prf-context">'
    +(f.summary?'<p class="prf-summary">'+mdInline(f.summary)
      +(f.summary_derived?' <span class="muted" title="Derived from the description">(auto)</span>':'')+'</p>':'')
    +'<div class="prf-context-links">'+jiraHtml+pldm+'</div>'
    +prJiraSyncResults(f.jira_sync)+'</div>';
  const kvHtml='<div class="kv prf-kv">'
    +'<span><b>Logged:</b> '+esc(f.logged||"\u2014")+'</span>'
    +'<span><b>Last activity:</b> '+esc(lastAct)+'</span>'
    +(complete?'<span>All PRs merged \u2014 retires on the next scan</span>':'')
    +(f.pldm_details?'<span><b>PLDM:</b> '+mdInline(f.pldm_details)+'</span>':'')
    +'</div>';
  const prsHtml='<div class="prf-sec prf-prs"><div class="prf-toolbar"><h4>Pull requests <span class="prf-count">'+visiblePrs.length+'</span></h4>'
    +ciTriageHtml(f,failCi)
    +(SERVED?'<details class="prf-actions-menu"><summary>Actions</summary>'
      +'<div class="prf-actions">'+ciAll+ciFail+addBtn+pldmBtn+dropBtn+'</div></details>':'')
    +'</div><div class="ci-note" id="ci-note-'+f.id+'" role="status" aria-live="polite"></div>'
    +'<div class="pr-table-scroll" role="region" aria-label="Pull requests for '+esc(f.name)+'" tabindex="0">'
    +'<table class="pr-tbl"><thead><tr><th style="width:21%">Repo / PR</th><th style="width:37%">Title</th>'
    +'<th style="width:14%">CI</th><th style="width:7%">Checks</th>'
    +'<th style="width:10%">Gate</th><th style="width:11%">Status</th>'
    +'</tr></thead><tbody>'+(visiblePrs.length?prRows(f):'<tr><td colspan="6" class="empty">'
      +(f.prs.length?"No PRs match the current filters.":"No PRs tracked yet.")+'</td></tr>')+'</tbody></table></div>'
    +'<div class="pldm-box" id="pldm-box-'+f.id+'" hidden></div></div>';
  const notesHtml='<details class="prf-history" data-pr-disclosure="'+f.id+':history"'
    +(PR_DETAIL_OPEN.has(f.id+":history")?' open':'')+'><summary>Activity history <span class="muted">('+notes.length+')</span></summary>'
    +(notes.length
      ? '<ul class="notes prf-notes">'+notes.map(n=>'<li><b>'+esc(formatPortalDate(n.date))+'</b>'
          +(n.label?' <span class="note-tag">'+esc(n.label)+'</span>':'')
          +' \u2014 '+mdInline(n.text||"")+'</li>').join("")+'</ul>'
      : '<div class="ro-note">No activity recorded yet.</div>')
    +'</details>';
  const detailsHtml='<details class="prf-more" data-pr-disclosure="'+f.id+':context"'
    +(PR_DETAIL_OPEN.has(f.id+":context")?' open':'')+'><summary>Feature details</summary>'
    +kvHtml+fullDesc+'</details>';
  return '<tr class="prf-row'+(collapsed?" collapsed":"")+'" data-fid="'+esc(f.id)+'" style="--fk:'+c+'">'
    +'<td class="prf-sel"><input type="checkbox" class="prf-pick" data-fid="'+esc(f.id)+'"'
    +(PR_SELECTED.has(String(f.id))?" checked":"")+' title="Select for \u201cScan selected PRs\u201d"></td>'
    +'<td class="prf-main"><div class="prf-main-in"><button class="prf-toggle" title="'+(collapsed?"Expand":"Collapse")+' feature details" '
    +'aria-expanded="'+String(!collapsed)+'">'+(collapsed?"\u25b8":"\u25be")+'</button>'
    +'<span class="fid">['+f.id+']</span> <b>'+esc(f.name)+'</b></div></td>'
    +'<td>'+(SERVED
      ? '<select class="prf-prio badge p-'+esc(prio)+'" data-fid="'+esc(f.id)+'" data-orig="'+esc(prio)+'" '
        +'title="'+(f.priority_derived?"Default \u2014 not set yet. ":"")+'Change priority">'
        +PRI_ORDER.map(p=>'<option value="'+p+'"'+(p===prio?" selected":"")+'>'+p+'</option>').join("")
        +'</select>'
      : prioPill)+'</td>'
    +'<td><span class="st" style="--sk:'+c+'">'+esc(state)+'</span></td>'
    +'<td><span class="prf-count" title="PRs merged / total">'+f.merged+'/'+f.total+'</span></td>'
    +'<td><button class="ci-btn" data-prtoggle="'+f.id+'">'+(collapsed?"Show":"Hide")+' details</button></td>'
    +'</tr>'
    +'<tr class="prf-detail" data-fid="'+esc(f.id)+'"'+(collapsed?" hidden":"")+'>'
    +'<td colspan="6"><div class="prf-detail-box">'
    +prsHtml+summaryHtml+'<div class="prf-secondary">'+detailsHtml+notesHtml+edit+'</div></div></td></tr>';
}

document.addEventListener("click",e=>{
  document.querySelectorAll(".prf-actions-menu[open]").forEach(menu=>{
    if(!menu.contains(e.target)) menu.open=false;
    else if(e.target.closest("button")){
      menu.open=false;
      menu.querySelector("summary").focus();
    }
  });
});
document.addEventListener("keydown",e=>{
  if(e.key!=="Escape") return;
  document.querySelectorAll(".prf-actions-menu[open]").forEach(menu=>{
    menu.open=false;
    menu.querySelector("summary").focus();
  });
});

// Memoir notes carry light markdown; render only **bold** and `code`, after escaping.
function mdInline(s){
  return esc(s||"")
    .replace(/`([^`]+)`/g,"<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>");
}

// ---------------- PLDM submission ----------------
// Remote mode builds from a workspace manifest, so submitting is a checkout +
// preflight + dispatch chain rather than a single call — hence the progress log.
const PLDM_IMAGES=["1die_w_gmem","2die_w_gmem","1die_wo_gmem","4die_w_gmem"];

function allSlots(){
  // The server's local, non-pinned roster is authoritative, even when empty.
  return [...((WORKSPACES&&WORKSPACES.canonical)||[])].sort();
}

function freeSlots(){
  const slots=(WORKSPACES&&WORKSPACES.slots)||{};
  return allSlots().filter(s=>!(slots[s]&&slots[s].holder));
}

function pldmForm(f){
  const free=freeSlots();
  const jira=(f.name.match(/[A-Z]+-\d+/)||[])[0];
  const dn=(jira||f.name).toLowerCase().replace(/[^a-z0-9]+/g,"-").slice(0,32);
  const slotOpts='<option value="">auto \u2014 first free slot</option>'
    +allSlots().map(s=>{const f=free.includes(s);
      return '<option value="'+esc(s)+'"'+(f?"":" disabled")+'>'+esc(s)
        +(f?" (free)":" (occupied)")+'</option>';}).join("");
  return '<div class="pldm-form">'
    +'<label>Display name<input id="pl-dn-'+f.id+'" value="'+esc(dn)+'"></label>'
    +'<label>Image type<select id="pl-img-'+f.id+'">'
      +PLDM_IMAGES.map(i=>'<option>'+i+'</option>').join("")+'</select></label>'
    +'<label>Group<input id="pl-grp-'+f.id+'" value="default" placeholder="empty = all groups"></label>'
    +'<label>Timeout (min)<input id="pl-to-'+f.id+'" type="number" min="1" max="120" value="20"></label>'
    +'<label>Args<input id="pl-args-'+f.id+'" placeholder="e.g. smoke"></label>'
    +'<label>Workspace<select id="pl-slot-'+f.id+'">'+slotOpts+'</select></label>'
    +'</div>'
    +'<div class="pldm-warn">Dispatches a real CI build and queues hardware. '
    +'The chosen slot is checked out to this feature\u2019s PR branches \u2014 all commits must be pushed.'
    +(free.length?"":' <b>No free slot right now.</b>')+'</div>'
    +'<div class="pldm-act"><button class="go" data-go="'+f.id+'">Submit to queue</button>'
    +'<button class="cancel" data-cancel="'+f.id+'">Cancel</button>'
    +'<span class="pldm-note" id="pl-note-'+f.id+'"></span></div>';
}

function togglePldm(id){
  const box=document.getElementById("pldm-box-"+id);
  if(!box) return;
  if(!box.hidden){ box.hidden=true; box.innerHTML=""; return; }
  const f=(PRS.features||[]).find(x=>x.id===id);
  if(!f) return;
  box.innerHTML=pldmForm(f); box.hidden=false;
}

async function submitPldm(id){
  const note=document.getElementById("pl-note-"+id);
  const go=document.querySelector('[data-go="'+id+'"]');
  const body={feature_id:id,
    display_name:document.getElementById("pl-dn-"+id).value.trim(),
    image_type:document.getElementById("pl-img-"+id).value,
    group:document.getElementById("pl-grp-"+id).value.trim(),
    timeout:parseInt(document.getElementById("pl-to-"+id).value,10)||20,
    args:document.getElementById("pl-args-"+id).value.trim(),
    slot:document.getElementById("pl-slot-"+id).value};
  go.disabled=true; note.textContent="dispatching\u2026";
  try{
    const r=await fetch("/api/pldm-test",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"submit failed");
    note.textContent=j.message||"";
    pollPldm(j.align_job,id);
  }catch(e){
    note.textContent="Failed: "+e.message; go.disabled=false;
  }
}

async function pollPldm(jobId,id){
  const box=document.getElementById("pldm-box-"+id);
  const PHASE={align:"checking out PR branches",preflight:"verifying pushed state",
               dispatch:"running octl \u2014 this takes a few minutes",done:"done"};
  for(;;){
    await new Promise(s=>setTimeout(s,2000));
    let j;
    try{ j=await (await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"})).json(); }
    catch(e){ break; }
    if(!j||j.error) break;
    const log=(j.repos||[]).map(r=>'<div class="pl-row '+r.action+'">'+esc(r.name)
      +' <span>'+esc(r.detail||r.action)+'</span></div>').join("");
    const pending=["queued","running"].includes(j.status);
    const head=j.status==="queued" ? "<b>Waiting in action queue</b>" : j.status==="running"
      ? '<b>'+esc(PHASE[j.phase]||j.phase||"working")+'</b>'
        +(j.current?' \u00b7 '+esc(j.current):"")
      : (j.status==="done"?'<b class="ok">Queued.</b> '+esc(j.message||"")
          +(j.run_url?' \u00b7 <a href="'+esc(j.run_url)+'" target="_blank" rel="noopener">Actions run</a>':"")
        : j.status==="cancelled"?'<b>Cancelled.</b> '+esc(j.message||"")
        : '<b class="bad">Failed.</b> '+esc(j.message||""));
    box.innerHTML='<div class="pldm-prog">'+head+'</div>'+log
      +(pending?"":'<div class="pldm-act">'
        +'<button class="cancel" data-cancel="'+id+'">Close</button></div>');
    if(!pending){
      if(j.status==="done") scanPrs();
      break;
    }
  }
}

document.addEventListener("click",e=>{
  const t=e.target.closest("[data-pldm],[data-go],[data-cancel]");
  if(!t) return;
  if(t.dataset.pldm) togglePldm(parseInt(t.dataset.pldm,10));
  else if(t.dataset.go) submitPldm(parseInt(t.dataset.go,10));
  else if(t.dataset.cancel) togglePldm(parseInt(t.dataset.cancel,10));
});

// CI only ever fires on a `/ci` comment here, so these buttons post exactly that
// — for one PR, every open PR, or only the failing PRs of a feature.
async function rerunCi(id,scope,number,btn){
  const note=document.getElementById("ci-note-"+id);
  const label=scope==="one"?("PR #"+number):(scope==="failed"?"failed PRs":"all open PRs");
  if(!SERVED){ if(note) note.textContent="Serve the portal to re-run CI"; return; }
  const was=btn?btn.textContent:"";
  if(btn) btn.disabled=true;
  if(note) note.textContent="posting /ci to "+label+"\u2026";
  const body={feature_id:id,scope:scope};
  if(number!=null) body.number=number;
  try{
    const r=await fetch("/api/pr-ci",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"post failed");
    const fails=(j.results||[]).filter(x=>!x.ok);
    let msg="/ci posted to "+j.posted+" PR"+(j.posted===1?"":"s");
    if(j.failed) msg+=" \u00b7 "+j.failed+" failed: "
      +fails.map(x=>esc(x.repo)+" #"+x.number+" ("+esc(x.detail||"error")+")").join(", ");
    if(note) note.innerHTML=msg;
  }catch(err){
    if(note) note.textContent="Failed: "+err.message;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent=was; }
  }
}
document.addEventListener("click",e=>{
  const t=e.target.closest("[data-ci-one],[data-ci-all],[data-ci-fail]");
  if(!t) return;
  if(t.dataset.ciOne!=null)
    rerunCi(parseInt(t.dataset.ciOne,10),"one",parseInt(t.dataset.ciNum,10),t);
  else if(t.dataset.ciAll!=null)
    rerunCi(parseInt(t.dataset.ciAll,10),"all",null,t);
  else if(t.dataset.ciFail!=null)
    rerunCi(parseInt(t.dataset.ciFail,10),"failed",null,t);
});

// Read-only DevOps lookups: octl PR stage + sherlock job classification.
function devopsKv(obj){
  return Object.entries(obj||{}).filter(([,v])=>v!=null&&v!=="")
    .map(([k,v])=>`<span class="dv-k">${esc(k)}</span> ${esc(typeof v==="object"?JSON.stringify(v):String(v))}`)
    .join("<br>");
}
async function devopsLookup(t){
  const tr=t.closest("tr"), out=tr&&tr.querySelector(".devops-out");
  if(!out) return;
  const stage=t.dataset.prstage!=null;
  const url=stage?"/api/pr-stage?url="+encodeURIComponent(t.dataset.prstage)
                 :"/api/sherlock?job="+encodeURIComponent(t.dataset.sherlock);
  t.disabled=true; out.hidden=false; out.innerHTML=`<span class="muted">${stage?"Querying octl…":"Running sherlock…"}</span>`;
  try{
    const r=await fetch(url,{cache:"no-store"});
    const j=await r.json().catch(()=>({ok:false,error:"HTTP "+r.status}));
    if(!r.ok||!j.ok){
      out.innerHTML=`<span class="bad">${esc(j.error||("HTTP "+r.status))}</span>`+
        (j.raw?`<pre class="dv-raw">${esc(String(j.raw).slice(0,2000))}</pre>`:"");
      return;
    }
    if(stage){
      const vs=(j.versions||[]).map(v=>`<li>${esc(v.version)} — ${esc(v.status||"?")}`+
        (v.cd_manifests&&v.cd_manifests.length?` · CD: ${esc(v.cd_manifests.join(", "))}`:"")+"</li>").join("");
      out.innerHTML=`<b>Stage:</b> <span class="dv-stage dv-${esc(j.stage)}">${esc(j.stage)}</span>`+
        ` <span class="muted">(${esc(j.project)}/${esc(j.manifest)})</span>`+(vs?`<ul>${vs}</ul>`:"");
    }else{
      const l=j.links||{};
      out.innerHTML=devopsKv(j.classification)+
        `<div class="dv-links">`+
        (l.runner_details?`<a href="${esc(l.runner_details)}" target="_blank" rel="noopener">Runner details ↗</a> `:"")+
        (l.job_lookup?`<a href="${esc(l.job_lookup)}" target="_blank" rel="noopener">Job lookup ↗</a>`:"")+`</div>`;
    }
  }catch(err){
    out.innerHTML=`<span class="bad">${esc(String(err))}</span>`;
  }finally{ t.disabled=false; }
}
document.addEventListener("click",e=>{
  const t=e.target.closest("[data-sherlock],[data-prstage]");
  if(t){ e.preventDefault(); devopsLookup(t); }
});

// Explicit scans probe GitHub and may update opted-in Jira tickets server-side.
// ``ids`` limits it to the ticked features; omitted = every tracked feature.
async function scanPrs(ids){
  const sel=Array.isArray(ids);
  const btn=document.getElementById(sel?"pr-scan-sel":"pr-scan"), note=document.getElementById("pr-scan-note");
  const other=document.getElementById(sel?"pr-scan":"pr-scan-sel");
  if(!SERVED){ note.textContent="Serve the portal to scan"; return; }
  btn.disabled=true; if(other) other.disabled=true;
  btn.innerHTML='<span class="spin">\u21bb</span> Scanning\u2026';
  note.textContent="probing GitHub and syncing opted-in Jira tickets\u2026";
  try{
    const r=await fetch("/api/pr-scan",sel
      ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({feature_ids:ids})}
      : {method:"POST"});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"scan failed");
    const job=await pollAlign(j.job,"Scanning PRs…");
    if(!job) throw new Error("scan failed");
    const res=job.result||{};
    if(job.result){ PRS=res; renderPrs(); buildSections(); }
    if(job.status==="error") throw new Error(job.message||"scan failed");
    const gone=res.purged||[];
    note.innerHTML="probed "+(res.scanned||0)+" PRs"+(sel?" in "+ids.length+" selected feature"+(ids.length===1?"":"s"):"")
      +(gone.length?' \u00b7 <b>retired '+gone.length+'</b>: '
        +gone.map(f=>esc(f.name)+" ("+f.total+"/"+f.total+")").join(", "):"")
      +(job.message?' \u00b7 '+esc(job.message):"");
  }catch(e){
    note.textContent="Scan failed: "+e.message;
  }finally{
    btn.disabled=false; if(other) other.disabled=false;
    document.getElementById("pr-scan").textContent="\u21bb Scan PRs";
    syncPrSelection();
  }
}
document.getElementById("pr-scan").addEventListener("click",()=>scanPrs());
(function fillPeMenu(){
  const panel=document.getElementById("pe-menu"); if(!panel) return;
  if(!PE_URL){ panel.closest(".dd").hidden=true; return; }
  panel.innerHTML=PE_VIEWS.map(([p,l])=>
    `<a class="dd-opt pe-opt" href="${esc(peLink(p))}" target="_blank" rel="noopener">${esc(l)} \u2197</a>`).join("");
})();
document.getElementById("pr-scan-sel").addEventListener("click",()=>
  scanPrs([...PR_SELECTED].map(Number)));
document.getElementById("pr-feature-tbl").addEventListener("change",e=>{
  const t=e.target;
  if(t.classList.contains("prf-prio")){
    saveFeaturePriority(t);
    return;
  }
  if(t.classList.contains("prf-pick")){
    t.checked?PR_SELECTED.add(t.dataset.fid):PR_SELECTED.delete(t.dataset.fid);
  }else if(t.id==="pr-sel-all"){
    document.querySelectorAll("#pr-body .prf-pick").forEach(b=>{
      b.checked=t.checked;
      t.checked?PR_SELECTED.add(b.dataset.fid):PR_SELECTED.delete(b.dataset.fid);
    });
  }else return;
  syncPrSelection();
});
document.getElementById("pr-collapse").addEventListener("click",e=>{
  const expand=!!e.currentTarget.dataset.expand;
  (PRS.features||[]).forEach(f=>setPrCollapsed(f.id, !expand));
});
document.addEventListener("click",e=>{
  const quick=e.target.closest("[data-prtoggle]");
  if(quick){
    const id=quick.dataset.prtoggle;
    setPrCollapsed(id, !prCollapsed(id));
    return;
  }
  const tri=e.target.closest(".prf-toggle");
  if(tri){
    const id=tri.closest(".prf-row").dataset.fid;
    setPrCollapsed(id, !prCollapsed(id));
    return;
  }
  const main=e.target.closest(".prf-main");
  if(main){
    const id=main.closest(".prf-row").dataset.fid;
    setPrCollapsed(id, !prCollapsed(id));
  }
});

// ---------------- feature / PR tracking ----------------
// Every mutation returns the whole refreshed payload, so the section re-renders
// from the server instead of from a guess about what changed.
async function prPost(path, body){
  if(!SERVED) throw new Error("Read-only \u2014 run portal.py serve to edit tracking");
  if(body && body.feature_id!==undefined) body.feature_id=Number(body.feature_id);
  if(path==="/api/feature-edit" && body && body.feature_id!==undefined){
    const feature=(PRS.features||[]).find(f=>String(f.id)===String(body.feature_id));
    if(feature) body.expected_revision=feature.revision;
  }
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body||{})});
  const j=await r.json();
  if(!r.ok||j.error) throw new Error(j.error||("Error "+r.status));
  if(j.prs){ PRS=j.prs; renderPrs(); buildSections(); }
  return j;
}

function prJiraCoverageDialog(fid, key){
  if(!SERVED) return;
  const feature=(PRS.features||[]).find(f=>String(f.id)===String(fid));
  if(!feature||!(feature.jira||[]).includes(key)) return;
  const confirmed=!(feature.jira_coverage||[]).includes(key);
  const revision=feature.revision;
  prModal('<h3>'+esc(confirmed?'Confirm full PR coverage':'Revoke Jira coverage')+'</h3>'
    +'<p>'+esc(feature.name)+' · '+esc(key)+'</p>'
    +(confirmed?'<p>I confirm that this feature’s entire current PR set fully implements '
      +esc(key)+'. This grants automatic In Review / Done updates on CLI or Portal scans, '
      +'only if the ticket is assigned to the authenticated Jira user. Changing the PR set '
      +'invalidates this confirmation.</p>'
      :'<p>Revoke approval for automatic Jira updates for this ticket and PR set. '
        +'This does not undo Jira transitions already made.</p>')
    +'<ul>'+(feature.prs||[]).map(pr=>'<li>'+esc(pr.repo)+' #'+esc(pr.number)+'</li>').join("")+'</ul>'
    +'<div class="ws-modal-act"><span class="ws-modal-msg" role="status"></span>'
    +'<button class="ws-modal-cancel">Cancel</button><button class="ws-modal-ok primary">'
    +(confirmed?'Confirm full coverage':'Revoke coverage')+'</button></div>',
    async (ov,close)=>{
      const button=ov.querySelector(".ws-modal-ok");
      button.disabled=true;
      try{
        const result=await prPost("/api/feature-jira-coverage",{
          feature_id:fid, key, confirmed, expected_revision:revision});
        close(); prNote(result.message||"Jira coverage saved.");
      }catch(error){
        ov.querySelector(".ws-modal-msg").textContent=error.message;
        button.disabled=false;
      }
    });
}
document.addEventListener("click",event=>{
  const button=event.target.closest("[data-jira-coverage]");
  if(button) prJiraCoverageDialog(button.dataset.jiraCoverage,button.dataset.jiraKey);
});

async function saveFeaturePriority(sel){
  const fid=sel.dataset.fid, v=sel.value;
  if(v===sel.dataset.orig) return;
  sel.disabled=true;
  sel.className="prf-prio badge p-"+v;
  try{
    await prPost("/api/feature-edit",{feature_id:fid, priority:v});
  }catch(e){
    sel.value=sel.dataset.orig; sel.className="prf-prio badge p-"+sel.dataset.orig;
    sel.disabled=false;
    prNote("Priority not saved: "+e.message);
  }
}

async function saveFeatureMeta(fid){
  const ed=document.querySelector('.pr-edit[data-fid="'+fid+'"]');
  const say=t=>{ const m=document.getElementById("pr-edit-msg-"+fid); if(m) m.textContent=t; };
  if(!ed) return;
  // Only changed fields go out: the summary/jira shown may be *derived*, and
  // posting them back unchanged would freeze a derived value in storage.
  const body={feature_id:fid};
  [["name",".pe-name"],["priority",".pe-priority"],["jira",".pe-jira"],["summary",".pe-summary"],["description",".pe-desc"]]
    .forEach(([k,sel])=>{
      const el=ed.querySelector(sel);
      if(el && el.value.trim()!==(el.dataset.orig||"").trim()) body[k]=el.value.trim();
    });
  if(body.name==="") { say("Name is required."); return; }
  const note=ed.querySelector(".pe-note").value.trim();
  if(note){ body.note=note; body.note_label=ed.querySelector(".pe-note-label").value.trim(); }
  if(Object.keys(body).length===1){ say("Nothing changed."); return; }
  say("Saving\u2026");
  try{
    const j=await prPost("/api/feature-edit",body);
    say(j.message||"Saved.");
  }catch(e){
    say(e.message);
  }
}

// One per line or comma-separated; each is a PR URL or owner/repo#123.
const prRefs=s=>(s||"").split(/[\s,]+/).map(x=>x.trim()).filter(Boolean);

function prNote(msg){
  const n=document.getElementById("pr-scan-note");
  if(n) n.textContent=msg||"";
}

function prModal(html, onOk){
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML='<div class="ws-modal">'+html+'</div>';
  document.body.appendChild(ov);
  const close=()=>ov.remove();
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  ov.querySelector(".ws-modal-cancel").addEventListener("click",close);
  ov.querySelector(".ws-modal-ok").addEventListener("click",()=>onOk(ov,close));
  return ov;
}

const PR_REF_HINT='<span class="opt">one per line \u2014 URL or owner/repo#123</span>';
const PR_REF_EG="https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep/pull/286";

function prTrackDialog(){
  const ov=prModal('<h3>Track a feature</h3>'
    +'<label>Name<input type="text" id="prtk-name" autocomplete="off" '
    +'placeholder="Inactive PPW power-down (IPPCIESW-516)"></label>'
    +'<label>Description <span class="opt">optional</span>'
    +'<input type="text" id="prtk-desc" placeholder="what this feature delivers"></label>'
    +'<label>PRs '+PR_REF_HINT+'<textarea id="prtk-refs" placeholder="'+PR_REF_EG+'"></textarea></label>'
    +'<div class="ws-modal-act"><span class="ws-modal-msg"></span>'
    +'<button class="ws-modal-cancel">Cancel</button>'
    +'<button class="primary ws-modal-ok">Track</button></div>',
    async (ov,close)=>{
      const msg=ov.querySelector(".ws-modal-msg");
      const name=ov.querySelector("#prtk-name").value.trim();
      if(!name){ msg.textContent="A name is required."; return; }
      msg.textContent="Resolving the PRs through gh\u2026";
      try{
        const j=await prPost("/api/feature-track",{name:name,
          description:ov.querySelector("#prtk-desc").value.trim(),
          refs:prRefs(ov.querySelector("#prtk-refs").value)});
        close(); prNote(j.message||"");
      }catch(e){ msg.textContent=e.message; }
    });
  ov.querySelector("#prtk-name").focus();
}

function prAddDialog(fid){
  const f=(PRS.features||[]).find(x=>x.id===fid);
  const ov=prModal('<h3>Track PRs on ['+fid+'] '+esc(f?f.name:"")+'</h3>'
    +'<label>PRs '+PR_REF_HINT+'<textarea id="pradd-refs" placeholder="'+PR_REF_EG+'"></textarea></label>'
    +'<div class="ws-modal-act"><span class="ws-modal-msg"></span>'
    +'<button class="ws-modal-cancel">Cancel</button>'
    +'<button class="primary ws-modal-ok">Add</button></div>',
    async (ov,close)=>{
      const msg=ov.querySelector(".ws-modal-msg");
      const refs=prRefs(ov.querySelector("#pradd-refs").value);
      if(!refs.length){ msg.textContent="Paste at least one PR."; return; }
      msg.textContent="Resolving the PRs through gh\u2026";
      try{
        const j=await prPost("/api/pr-track",{feature_id:fid,refs:refs});
        close(); prNote(j.message||"");
      }catch(e){ msg.textContent=e.message; }
    });
  ov.querySelector("#pradd-refs").focus();
}

async function untrackPr(fid,number,repo){
  if(!confirm("Stop tracking "+repo+" #"+number+"?\n\nThe row leaves the feature; "
    +"the PR itself is untouched.")) return;
  try{ prNote((await prPost("/api/pr-untrack",
    {feature_id:fid,number:number,repo:repo})).message||""); }
  catch(e){ prNote("Failed: "+e.message); }
}

async function dropFeature(fid,name){
  if(!confirm("Drop ["+fid+"] "+name+" from active tracking?\n\nThe historical entry is kept "
    +"and annotated DROPPED \u2014 nothing is deleted.")) return;
  try{ prNote((await prPost("/api/feature-untrack",{feature_id:fid})).message||""); }
  catch(e){ prNote("Failed: "+e.message); }
}

document.addEventListener("click",e=>{
  const t=e.target.closest("[data-prtrack],[data-prdrop],[data-prx],[data-prsave]");
  if(!t) return;
  if(t.dataset.prsave!=null) saveFeatureMeta(parseInt(t.dataset.prsave,10));
  else if(t.dataset.prtrack!=null) prAddDialog(parseInt(t.dataset.prtrack,10));
  else if(t.dataset.prdrop!=null)
    dropFeature(parseInt(t.dataset.prdrop,10), t.dataset.prdropName||"");
  else if(t.dataset.prx!=null)
    untrackPr(parseInt(t.dataset.prx,10), parseInt(t.dataset.prxNum,10), t.dataset.prxRepo);
});
document.getElementById("pr-track").addEventListener("click",()=>{
  if(!SERVED){ prNote("Serve the portal to edit tracking"); return; }
  prTrackDialog();
});

// ---------------- RAID ----------------
const kpi=o=>'<div class="kpi" style="--k:'+o.c+'"><div class="l">'+esc(o.label)+'</div>'
  +'<div class="n">'+o.n+'</div><div class="s">'+(o.sub||"")+'</div></div>';

// ============================================================================
// Machine activity — every job `mctl` dispatched to a VM / lab machine.
// The remote keeps only the latest log per (workspace, kind), so the history
// lives in ~/.mctl/joblog.jsonl and the status comes from one ssh probe.
// ============================================================================
const VM_STATUS_COLOR={running:PBI.blue, passed:PBI.green, failed:PBI.red, died:PBI.orange,
                       cancelled:PBI.orange, superseded:PBI.grey, gone:PBI.grey, unknown:PBI.grey};
const VM_STATUS_TITLE={
  running:"still executing on the target",
  passed:"finished with rc=0",
  failed:"finished with a non-zero rc",
  died:"the process vanished without writing a completion marker",
  cancelled:"stopped from the portal (mctl cancel)",
  superseded:"a newer run overwrote this log on the target",
  gone:"no log left on the target (cleaned, or it never started)",
  unknown:"not confirmed by the target \u2014 never probed, or the last reading "
         +"aged out. Hit Refresh status",
};
// A status is only true as of the moment the target confirmed it. Anything the
// server marked `stale` has not been reconfirmed since, so it is shown as a
// READING WITH AN AGE, never as current fact -- an unreachable target used to
// leave day-old "running" rows looking live.
function vmVerified(j){
  if(!j.checked) return '<span class="vm-unver">never verified</span>';
  const txt=(j.verified_age_h||"")+" ago";
  return j.stale
    ? '<span class="vm-unver">\u26a0 unverified '+esc(txt)+'</span>'
    : '<span class="vm-ver">verified '+esc(txt)+'</span>';
}
function vmStaleTitle(j){
  if(!j.stale) return "";
  const was=j.demoted_from
    ? "last seen "+j.demoted_from+", but nothing has confirmed that since "
      +formatPortalDate(j.checked||"?")+" \u2014 it has most likely finished or died while the "
      +"target was unreachable"
    : "not reconfirmed since "+formatPortalDate(j.checked||"?");
  return was+(j.note?" \u2014 "+j.note:"");
}
let vmTarget="", vmTargets=[];   // vmTarget = the single picked target (scopes refresh)
function vmJobs(){
  let js=(VMLOG.jobs||[]).slice();
  if(vmTargets.length) js=js.filter(j=>vmTargets.includes(j.target));
  if(document.getElementById("vm-openonly").checked) js=js.filter(j=>j.status==="running");
  const live=document.getElementById("vm-livefirst");
  if(!live||live.checked){
    const rank=j=>j.status==="running"?0:1;
    js.sort((a,b)=>rank(a)-rank(b)||String(b.started).localeCompare(String(a.started)));
  }
  return js;
}
// The elapsed figure on a live card ticks in the browser, so it must be anchored
// to an ABSOLUTE start and corrected for any clock skew against the server --
// and it may only tick while the server still reports the job as confirmed
// running. vmLivePoll() below is what guarantees the second half.
function vmSkew(){
  return vmSkewSec;
}
function vmElapsedText(secs){
  const s=Math.max(0, Math.floor(secs));
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), ss=s%60;
  return h ? h+"h "+String(m).padStart(2,"0")+"m "+String(ss).padStart(2,"0")+"s"
           : (m ? m+"m "+String(ss).padStart(2,"0")+"s" : ss+"s");
}
function vmLiveCard(j){
  const el=j.started_ts
    ? vmElapsedText(Date.now()/1000 - vmSkew() - j.started_ts)
    : vmElapsedText(j.duration||0);
  return '<div class="vm-live"><div class="vm-live-l">'
    +'<div class="vm-live-t"><span class="spin">\u21bb</span> Running now \u00b7 '+esc(j.target)
    +' \u00b7 '+esc(j.kind)+'</div>'
    +'<h4>'+esc(j.ws_label||j.tag)+'</h4>'
    +(j.cmd?'<code>'+esc(j.cmd)+'</code>':"")
    +'<div class="vm-live-m">started '+esc(formatPortalDate(j.started))
    +(j.pid?' \u00b7 pid '+j.pid:"")
    +(j.lines?' \u00b7 '+j.lines+' log lines':"")
    +(j.errors?' \u00b7 <b style="color:'+PBI.red+'">'+j.errors+' error line(s)</b>':"")+'</div>'
    +(j.last_line?'<span class="vm-tail">'+esc(j.last_line)+'</span>':"")
    +'<button class="vm-log live" data-target="'+esc(j.target)+'" data-tag="'+esc(j.tag)+'"'
    +' data-kind="'+esc(j.kind)+'" data-live="1" data-id="'+esc(j.id)+'"'
    +' title="Open a terminal window tailing this job\u2019s log on the machine">'
    +'<span class="spin">\u21bb</span> Tail the live log</button>'
    +'</div><div class="vm-live-r"><b class="vm-el" data-start="'+(j.started_ts||0)+'">'
    +esc(el)+'</b>elapsed</div></div>';
}
function renderVmlog(){
  const all=VMLOG.jobs||[], counts=VMLOG.counts||{}, n=s=>counts[s]||0;
  const running=VMLOG.running||[];

  const sel=document.getElementById("vm-target");
  // Every REGISTERED target, not just the ones with history: picking one is how
  // you scope the refresh, and a target you have never run anything on is
  // exactly the one whose reachability you want to test.
  const tgts=[...new Set([...(VMLOG.targets||[]), ...all.map(j=>j.target)])].sort();
  sel.innerHTML='<option value="">All targets</option>'
    +tgts.map(t=>'<option value="'+esc(t)+'"'+(vmTargets.includes(t)?" selected":"")+'>'+esc(t)
      +' ('+all.filter(j=>j.target===t).length+')</option>').join("");
  const rbtn=document.getElementById("vm-refresh");
  rbtn.innerHTML=vmTarget?"\u21bb Refresh "+esc(vmTarget):"\u21bb Refresh status";
  rbtn.title=vmTarget
    ? "Probe "+vmTarget+" only \u2014 one ssh round trip. Clear the target filter to ask every machine."
    : "Ask every target for the state of its job dirs, mirror its logs, and re-read the EC2 power "
      +"state. One ssh round trip per machine plus an octl call. Pick a target above to scope it.";

  // "probed" must mean VERIFIED. When a probe is attempted but every target
  // refuses, the view is NOT fresh -- saying so is the whole fix: the portal
  // used to report a failed sweep as a successful one and present untouched
  // day-old verdicts as current.
  const stale=VMLOG.stale_n||0, unreach=VMLOG.unreachable||[];
  const attemptLate=VMLOG.last_attempt&&VMLOG.last_attempt>VMLOG.last_refresh;
  document.getElementById("vm-asof").innerHTML=
    all.length+" delegated job"+(all.length===1?"":"s")+" \u00b7 "+running.length+" running \u00b7 "
    +(VMLOG.last_refresh
      ? "verified <b>"+esc(VMLOG.last_refresh)+"</b>"
        +(VMLOG.refresh_age_h?' <span class="vm-age'+(vmStale(VMLOG.refresh_age_h)?" old":"")
          +'">'+esc(VMLOG.refresh_age_h)+" ago</span>":"")
      : "<b>never verified</b>")
    +(attemptLate?' \u00b7 <span class="vm-unver">last probe attempt '+esc(VMLOG.attempt_age_h||"")
      +" ago got no answer</span>":"")
    +(stale?' \u00b7 <span class="vm-unver">\u26a0 '+stale+" unverified reading"
      +(stale===1?"":"s")+'</span>':"")
    +(unreach.length?' \u00b7 <span class="vm-unver">unreachable: '+esc(unreach.join(", "))
      +'</span>':"")
    +" \u00b7 "+(VMLOG.logs_held||0)+" log(s) held locally"
    +(VMLOG.error?' \u00b7 <span style="color:'+PBI.red+'">'+esc(VMLOG.error)+'</span>':"");

  renderVmMachines();

  document.getElementById("vm-now").innerHTML=running.length
    ? '<div class="vm-now">'+running.map(vmLiveCard).join("")+'</div>'
    : '<div class="vm-idle">Nothing running on any target right now. '
      +'Delegate with <code>mctl build</code> or <code>mctl test</code>, then hit '
      +'<b>Refresh status</b>.</div>';

  document.getElementById("vm-kpis").innerHTML=[
    kpi({c:running.length?PBI.blue:PBI.grey, label:"Running", n:running.length,
         sub:"of "+all.length+" delegated"}),
    kpi({c:PBI.green, label:"Passed", n:n("passed"), sub:"rc=0"}),
    kpi({c:n("failed")?PBI.red:PBI.grey, label:"Failed", n:n("failed"), sub:"non-zero rc"}),
    kpi({c:n("died")?PBI.orange:PBI.grey, label:"Died", n:n("died"),
         sub:"no completion marker"+(n("cancelled")?" \u00b7 "+n("cancelled")+" cancelled":"")}),
    kpi({c:PBI.grey, label:"Targets", n:tgts.length,
         sub:(VMLOG.targets||[]).length+" registered"}),
  ].join("");

  const js=vmJobs();
  document.getElementById("vm-count").textContent=
    js.length+" of "+all.length+" delegated job"+(all.length===1?"":"s")+" shown";
  document.getElementById("vm-body").innerHTML=js.length
    ? js.map(vmRow).join("")
    : '<tr><td colspan="8"><div class="empty">No delegated jobs recorded yet. '
      +'Every <code>mctl build</code> / <code>mctl test</code> lands here.</div></td></tr>';
  vmBindLogs();
  vmTimersSync();
  applyTableStandards(document.getElementById("sec-vm"));
}
// --- live elapsed -------------------------------------------------------
// Two timers, with different jobs:
//   vmTickTimer  repaints the elapsed figure every second (pure arithmetic,
//                no network) so the card reads like a stopwatch;
//   vmLiveTimer  re-reads /api/vm-jobs so the ticking STOPS when the job does.
// The second one is not optional. A counter that keeps climbing is a strong
// claim that the job is alive, and /api/vm-jobs is a local file read -- it
// never probes a target -- so polling it while something runs costs nothing.
let vmTickTimer=null, vmLiveTimer=null;
const VM_LIVE_POLL_MS=()=>prefs.vmPollMs;

function vmTick(){
  const els=document.querySelectorAll(".vm-el[data-start]");
  if(!els.length){ vmTimersStop(); return; }
  const skew=vmSkew(), now=Date.now()/1000;
  els.forEach(e=>{
    const t0=parseFloat(e.dataset.start||"0");
    if(t0>0) e.textContent=vmElapsedText(now-skew-t0);
  });
}
function vmTimersStop(){
  if(vmTickTimer){ clearInterval(vmTickTimer); vmTickTimer=null; }
  if(vmLiveTimer){ clearInterval(vmLiveTimer); vmLiveTimer=null; }
}
// Cheap change detector, so a poll that learned nothing does not blow away the
// section (and with it any selection the user is holding).
function vmSignature(d){
  return (d&&d.jobs?d.jobs:[]).map(j=>j.id+":"+j.status+":"+j.checked+":"+j.lines
    +":"+j.stale).join("|")+"#"+((d&&d.last_refresh)||"")
    +"#"+Object.entries((d&&d.live)||{}).map(([k,v])=>k+":"+v.state).join(",");
}
async function vmLivePoll(){
  if(!SERVED||activeSection!=="vm"){ return; }
  try{
    const r=await fetch("/api/vm-jobs",{cache:"no-store"});
    if(!r.ok) return;
    const j=await r.json();
    const changed=vmSignature(j)!==vmSignature(VMLOG);
    vmAdoptPayload(j);
    if(changed){ renderVmlog(); buildSections(); }
    else if(!(VMLOG.running||[]).length) vmTimersStop();
  }catch(e){ /* a failed poll must not kill the view; the next tick retries */ }
}
// Started only when the server says something is CONFIRMED running -- the
// payload's `running` already excludes readings that have gone unverified.
function vmTimersSync(){
  // Only while the VM section is actually on screen: a stopwatch nobody can see
  // is just a timer that keeps the laptop awake.
  const live=(VMLOG.running||[]).length>0&&activeSection==="vm";
  if(document.hidden){ vmTimersStop(); return; }
  if(live&&!vmTickTimer) vmTickTimer=setInterval(vmTick,1000);
  if(live&&!vmLiveTimer) vmLiveTimer=setInterval(vmLivePoll,VM_LIVE_POLL_MS());
  if(!live) vmTimersStop();
}
// A hidden tab need not tick or poll. Coming back recomputes from the absolute
// start, so the figure is correct immediately rather than resuming where it
// was paused.
document.addEventListener("visibilitychange",()=>{
  if(document.hidden){ vmTimersStop(); return; }
  if(activeSection==="vm"){ vmTick(); vmTimersSync(); vmLivePoll(); }
});
// Every "open / tail" control, in the table and on the live cards alike.
function vmBindLogs(){
  document.querySelectorAll(".vm-log").forEach(b=>b.addEventListener("click",()=>
    vmLogOpen(b.dataset.target, b.dataset.tag, b.dataset.kind, b.dataset.live==="1",
              b.dataset.id||"")));
  document.querySelectorAll(".vm-cancel").forEach(b=>b.addEventListener("click",()=>
    vmCancel(b.dataset.id,b.dataset.label)));
  vmBindFlashOpen(document);
}
// A reading older than an hour is history, not status. Saying so is the whole
// point: a day-old "stopped" that looked live is why the VM seemed unusable.
function vmStale(h){ return /\d+\s*h/.test(h||""); }
function vmActions(target,id="",main=false,up=true){
  const off=SERVED?"":" disabled";
  return '<select class="vm-actions" title="More actions" aria-label="Actions for '+esc(target||id)+'"'
    +' data-target="'+esc(target)+'" data-id="'+esc(id)+'"'+off+'>'
    +'<option value="">\u22ef</option>'
    +(target?'<option value="provision"'+off+'>Provision</option>'
      :'<option value="add"'+off+'>Add tracking</option>')
    +(target?'<option value="sso"'+(SERVED&&up?"":" disabled")+'>SSO login'+(up?"":" (VM stopped)")+'</option>':"")
    +(target?'<option value="edit"'+off+'>Edit details\u2026</option>':"")
    +(id&&!main?'<option value="main"'+off+'>Set as main</option>':"")
    +'</select>';
}
function vmBindActions(box){
  box.querySelectorAll(".vm-actions").forEach(select=>select.addEventListener("change",()=>{
    const action=select.value;
    select.value="";
    if(!SERVED) return;
    if(action==="provision") vmProvision(select.dataset.target);
    else if(action==="main") vmSetMain(select.dataset.id,select);
    else if(action==="add") vmAddDialog(select.dataset.id);
    else if(action==="sso") vmSsoStart(select.dataset.target);
    else if(action==="edit") vmEditDialog(select.dataset.target);
  }));
  vmSsoBind(box);
}
// Folding: EC2 ids fold single VM cards (prefs.vmHidden); "vms"/"machines" fold
// a whole panel and "machine-card" the machine card (prefs.vmPanelsHidden).
function vmFoldList(key){ return /^i-/.test(key)?"vmHidden":"vmPanelsHidden"; }
function vmFolded(key){ return prefs[vmFoldList(key)].includes(key); }
function vmFoldToggle(key){
  const list=vmFoldList(key), cur=prefs[list];
  savePrefs({[list]:cur.includes(key)?cur.filter(x=>x!==key):[...cur,key]});
}
function vmChev(open){ return '<span class="vm-chev" aria-hidden="true">'+(open?"\u25be":"\u25b8")+'</span>'; }
function vmPanelHead(key,title,count){
  const open=!vmFolded(key);
  return '<h3 class="vm-panel-h"><button type="button" class="vm-panel-fold" data-vm-fold="'+key+'"'
    +' aria-expanded="'+open+'" title="'+(open?"Collapse ":"Expand ")+title+'">'+vmChev(open)+title
    +'<span class="vm-count">'+count+'</span></button></h3>';
}
// One card layout for VMs and lab machines: title + compact tool row on one
// line, key/value body below, and a fold arrow on the left edge.
function vmCard(o){
  const open=!vmFolded(o.key);
  return '<article class="vm-card'+(o.cls?" "+o.cls:"")+(open?"":" folded")+'">'
    +'<button type="button" class="vm-fold" data-vm-fold="'+esc(o.key)+'" aria-expanded="'+open+'"'
    +' aria-label="'+(open?"Collapse ":"Expand ")+esc(o.label)+'" title="'+(open?"Collapse":"Expand")+'">'
    +vmChev(open)+'</button>'
    +'<header class="vm-card-h"><div class="vm-card-title">'+o.title+'</div>'
    +(open?'<div class="vm-tools">'+o.tools+'</div>':"")+'</header>'
    +(open?o.body||"":"")+'</article>';
}
function vmKv(rows){
  rows=rows.filter(r=>r&&r[1]);
  return rows.length?'<dl class="vm-kv">'+rows.map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+v+'</dd>').join("")+'</dl>':"";
}
function vmJobsLine(t){
  if(!t) return "";
  return '<span class="vm-t-age'+(vmStale(t.age_h)?" old":"")+'">'
    +(t.checked?"probed "+esc(t.age_h)+" ago":"never probed")+'</span>'
    +(t.running?' \u00b7 <b>'+t.running+' running</b>':"");
}
function vmOtherTargets(){
  const ids=new Set((VMLOG.machines||[]).map(m=>m.id));
  return (VMLOG.target_state||[]).filter(t=>!t.instance||!ids.has(t.instance));
}
// EC2 power state for each delegation target, straight from `octl vm list`.
function renderVmMachines(){
  const ms=VMLOG.machines||[];
  const box=document.getElementById("vm-machines");
  const stale=vmStale(VMLOG.machines_age_h);
  box.innerHTML='<section class="vm-panel" id="vm-ec2-panel" aria-label="VM cards">'
    +vmPanelHead("vms","VMs",ms.length)
    +(vmFolded("vms")?"":ms.length?'<div class="vm-mach">'+ms.map(vmMachine).join("")+'</div>'
      +'<div class="vm-foot"><span class="vm-mage'+(stale?" old":"")+'">'
      +(stale?"\u26a0 ":"")+"Power state read "+esc(VMLOG.machines_checked||"?")
      +(VMLOG.machines_age_h?" \u00b7 "+esc(VMLOG.machines_age_h)+" ago":"")
      +(stale?" \u2014 hit <b>Check VM status</b>, this is history":"")+'</span> \u00b7 '+vmBudgetNote()+'</div>'
      :'<div class="vm-idle">No EC2 instance visible. Hit <b>Check VM status</b> to query '
        +'<code>octl vm list</code> \u2014 it revalidates SSO, so it takes a moment.</div>')
    +'</section><section class="vm-panel" id="vm-machine-panel" aria-label="Machine card">'
    +vmPanelHead("machines","Machine",vmOtherTargets().length)
    +(vmFolded("machines")?"":vmTargetStrip())+'</section>';
  box.querySelectorAll("[data-vm-fold]").forEach(b=>b.addEventListener("click",()=>{
    const key=b.dataset.vmFold;
    vmFoldToggle(key);
    renderVmMachines();
    const again=box.querySelector('[data-vm-fold="'+key+'"]');
    if(again) again.focus();
  }));
  const pick=box.querySelector("#vm-machine-pick");
  if(pick) pick.addEventListener("change",e=>{
    savePrefs({machineTarget:e.target.value});
    renderVmMachines();
    box.querySelector("#vm-machine-pick").focus();
  });
  box.querySelectorAll(".vm-pw").forEach(b=>b.addEventListener("click",()=>
    vmPower(b.dataset.action, b.dataset.id, b)));
  vmBindTargetRefresh(box);
  vmBindTerms(box);
  vmBindActions(box);
  if(SERVED&&!VMSSO.loaded) vmSsoPoll();
}
// Platform EC2 policy (devops-expert skill, references/vms-and-runners.md).
function vmBudgetNote(){
  const href=gfDash("ec2_cost");
  return '<span class="vm-budget">Budget $50/month per user \u00b7 mctl starts VMs on a 30m lease,'
    +' renews it before work and while a job runs, and auto-starts a stopped VM on the next mctl action'
    +' \u00b7 lease max 72h'
    +' \u00b7 instances stopped 60 days are terminated'
    +(href?' \u00b7 <a href="'+esc(href)+'" target="_blank" rel="noopener">EC2 cost in Grafana \u2197</a>':"")
    +'</span>';
}
// Unlisted VM targets remain selectable alongside lab machines.
function vmTargetStrip(){
  const ts=vmOtherTargets();
  if(!ts.length) return '<div class="vm-idle">No other machines tracked. Use <b>Add machine</b> to register one.</div>';
  const t=ts.find(t=>t.name===prefs.machineTarget)||ts[0];
  const pick=ts.length>1
    ?'<select id="vm-machine-pick" class="vm-pick" aria-label="Select machine" title="Switch machine">'
      +ts.map(x=>'<option value="'+esc(x.name)+'"'+(x.name===t.name?" selected":"")+'>'
        +esc(x.name)+(x.instance?" (VM target)":"")+'</option>').join("")+'</select>'
    :'<h4>'+esc(t.name)+'</h4>';
  const title='<span class="vm-dot '+(t.error?"bad":t.checked?"ok":"")+'"></span>'+pick+vmLiveBadge(t.name);
  const body=vmKv([
    ["Jobs",vmJobsLine(t)],
    ["Flash",vmFlashLine(t)],
    ["Instance",t.instance?esc(t.instance):""],
    ["BMC",t.bmc?esc(t.bmc):""],
    ["Error",t.error?'<span class="vm-t-err">\u26a0 '+esc(t.error)+'</span>':""],
    ["Setup",vmProvBadge(t)],
  ])+vmSsoSlot(t.name);
  return vmCard({key:"machine-card",label:t.name,cls:t.error?"bad":"",title,body,
    tools:vmTermBtn(t.name,true)+vmBmcBtn(t)+vmFlashBtn(t)+vmTargetBtn(t.name)+vmActions(t.name,"",false,true)});
}
// Result of the last `mctl provision` on a target (latest per recipe).
function vmProvBadge(t){
  const p=(t&&t.provision)||{};
  if(!p.recipes||!p.recipes.length)
    return '<span class="vm-prov none" title="Never provisioned with mctl provision">unprovisioned</span>';
  const lines=p.recipes.map(r=>r.recipe+": "+r.status+(r.note?" \u2014 "+r.note:"")).join("\n");
  const cls=p.ok?(p.todo&&p.todo.length?"todo":"ok"):"bad";
  const txt=p.ok?(p.todo&&p.todo.length?"provisioned \u00b7 "+p.todo.length+" manual":"provisioned")
                :"provision \u26a0 "+p.failed.join(", ");
  return '<span class="vm-prov '+cls+'" title="'+esc("Last run "+(p.last_age_h||"?")+" ago\n"+lines)+'">'
    +esc(txt)+'</span>';
}
// Runs `mctl -t <target> provision` (the target's default recipe list) live in
// a terminal window; finished recipes are skipped, so re-running is cheap.
function vmProvision(target){
  if(!SERVED){ document.getElementById("vm-refresh-note").textContent="Serve the portal to provision"; return; }
  if(!confirm("Run mctl provision on "+target+"?\n\nInstalls the missing prerequisites from its default list"
    +" (a stopped VM is started first). Output streams into a terminal window.")) return;
  termOpen({kind:"provision",target},"prov-"+target);
}
// Started / predicted stop from mctl's power memory. A cached "running" whose
// lease has passed is shown as probably stopped -- the reaper beat the probe.
function vmPowerLine(m){
  if(m.predicted_stopped)
    return '<span class="vm-lease bad" title="The cached state says running, but the lease ended '
      +esc(m.stopped_ago_h||"")+' ago, so the instance has most likely been stopped. The next mctl action starts it again.">'
      +'\u26a0 lease ended '+esc(m.stopped_ago_h||"")+' ago \u2014 probably stopped; auto-starts on next use</span>';
  const bits=[];
  if(m.started_ago_h) bits.push("started "+esc(m.started_ago_h)+" ago"+(m.started_by?" ("+esc(m.started_by)+")":""));
  if(m.stop_in_h&&m.stop_ts) bits.push("predicted stop "+esc(new Date(m.stop_ts*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}))
    +" (in "+esc(m.stop_in_h)+")");
  return bits.length?'<span class="vm-lease">'+bits.join(" \u00b7 ")+'</span>':"";
}
// A shell on the machine, in its own window: attaches to the persistent tmux
// session "portal" (so work survives closing the window), bash otherwise.
function vmTermBtn(name,on){
  if(!(VMLOG.terminal_targets||[]).includes(name)) return "";
  return '<button class="vm-term" data-target="'+esc(name)+'"'+(on?"":" disabled")
    +' title="'+(on?"Open a terminal on "+esc(name)+" (tmux session \u201cportal\u201d)"
      :esc(name)+" is not running")+'">&gt;_ Terminal</button>';
}
// State of the persistent ssh status link (Settings \u203a Machines \u203a live).
function vmLiveBadge(name){
  const l=(VMLOG.live||{})[name];
  if(!l) return "";
  const age=l.last_block?Math.max(0,Math.round(Date.now()/1000-l.last_block)):null;
  const title=l.state==="connected"?"Live ssh link \u2014 last update "+age+"s ago"
    :l.state==="down"?"Live link down: "+(l.error||"")+" \u2014 retrying"
    :"Connecting the live ssh link\u2026";
  return '<span class="vm-livebadge '+esc(l.state)+'" title="'+esc(title)+'">'
    +(l.state==="connected"?"live":l.state)+'</span>';
}
// Lab machines only (EC2 VMs have no BMC); the server resolves the BMC host.
function vmBmcBtn(t){
  if(t.instance) return "";
  return '<button class="vm-bmc" data-target="'+esc(t.name)+'"'+(t.bmc&&SERVED?"":" disabled")
    +' title="'+(t.bmc?"Open an ssh terminal on "+esc(t.name)+"\u2019s BMC ("+esc(t.bmc)+")"
      :"No BMC set \u2014 use \u22ef \u203a Edit details\u2026")+'">&gt;_ BMC</button>';
}
function vmBindTerms(box){
  box.querySelectorAll(".vm-term").forEach(b=>b.addEventListener("click",()=>
    vmTermOpen(b.dataset.target)));
  box.querySelectorAll(".vm-bmc").forEach(b=>b.addEventListener("click",()=>
    termOpen({kind:"bmc",target:b.dataset.target},"bmc-"+b.dataset.target)));
  box.querySelectorAll(".vm-flash").forEach(b=>b.addEventListener("click",()=>
    vmFlashDialog(b.dataset.target)));
  vmBindFlashOpen(box);
}
function vmBindFlashOpen(root){
  root.querySelectorAll(".vm-flash-open:not([data-bound])").forEach(b=>{
    b.dataset.bound="1";
    b.addEventListener("click",()=>flashWindow(b.dataset.id));
  });
}
// --- Flash FW -------------------------------------------------------------
// Images come from the fw-deploy store, the flash runs fw-flasher's fw_flash.py
// as a local machine job (kind "flash"), so it lands in the Jobs list with its
// log and survives a portal restart. The progress window is /flash?id=.
function vmFlashRunning(name){
  return (VMLOG.jobs||[]).find(j=>j.kind==="flash"&&j.target===name&&j.status==="running");
}
function vmFlashBtn(t){
  if(t.instance) return "";
  const run=vmFlashRunning(t.name);
  if(run) return '<button class="vm-flash-open live" data-id="'+esc(run.id)+'"'
    +' title="A flash is running on '+esc(t.name)+' — open its progress window">'
    +'<span class="spin">\u21bb</span> Flashing\u2026</button>';
  return '<button class="vm-flash" data-target="'+esc(t.name)+'"'+(SERVED?"":" disabled")
    +' title="Flash a firmware image from the fw-deploy store onto '+esc(t.name)+'\u2019s OAMs">'
    +'\u26a1 Flash FW</button>';
}
function vmFlashLine(t){
  const last=(VMLOG.jobs||[]).filter(j=>j.kind==="flash"&&j.target===t.name)
    .sort((a,b)=>(b.started||"").localeCompare(a.started||""))[0];
  if(!last) return "";
  return '<button class="linklike vm-flash-open" data-id="'+esc(last.id)+'" title="Open the flash progress window">'
    +esc((last.method||"")+" "+(last.image||"")+" \u2192 OAM "+(last.oams||[]).join(","))+'</button>'
    +' <span class="vm-st '+esc(last.status)+'">'+esc(last.status==="failed"&&last.rc===2?"not verified":last.status)+'</span>';
}
function flashWindow(id){
  if(!SERVED||!id) return null;
  const w=window.open("/flash?"+new URLSearchParams({id}).toString(),"flash-"+id,"popup=yes,width=980,height=760");
  if(!w) alert("The browser blocked the flash window \u2014 allow pop-ups for this page.");
  return w;
}
function flashSize(n){ return n>=1048576?(n/1048576).toFixed(1)+" MB":n>=1024?Math.round(n/1024)+" KB":(n||0)+" B"; }
function vmFlashDialog(target){
  if(!SERVED) return;
  const previousFocus=document.activeElement, dialog=document.createElement("dialog");
  dialog.className="ws-modal vm-flash-dialog";
  dialog.setAttribute("aria-labelledby","vm-flash-title");
  dialog.innerHTML='<form><h3 id="vm-flash-title">Flash FW \u00b7 '+esc(target)+'</h3>'
    +'<div class="flash-body"><p class="ro-note">Reading the image store and probing '+esc(target)
    +' (read-only: BMC device list, flash versions, host tools)\u2026</p></div>'
    +'<div class="ws-modal-act"><span class="ws-modal-msg" role="status"></span>'
    +'<button type="button" class="ws-modal-cancel">Cancel</button>'
    +'<button type="button" class="flash-back" hidden>\u2190 Back</button>'
    +'<button type="submit" class="primary" disabled>Review\u2026</button></div></form>';
  document.body.appendChild(dialog);
  const form=dialog.querySelector("form"), body=dialog.querySelector(".flash-body"),
        msg=dialog.querySelector(".ws-modal-msg"), submit=form.querySelector('[type="submit"]'),
        back=dialog.querySelector(".flash-back");
  const st={images:[],disc:null,step:"pick",busy:false,sel:{}};
  const close=()=>{ if(!st.busy) dialog.close(); };
  dialog.addEventListener("close",()=>{ dialog.remove(); previousFocus?.focus(); });
  dialog.addEventListener("cancel",e=>{ if(st.busy) e.preventDefault(); });
  dialog.addEventListener("keydown",e=>{ e.stopPropagation(); if(e.key==="Escape"){ e.preventDefault(); close(); } });
  dialog.querySelector(".ws-modal-cancel").addEventListener("click",close);
  back.addEventListener("click",()=>{ st.step="pick"; draw(); });
  dialog.showModal();

  const imgs=()=>st.images.filter(i=>i.valid&&(!st.sel.project||i.project===st.sel.project));
  const method=()=>st.disc&&st.disc.methods[st.sel.method];
  function collect(){
    const f=form.elements, v=n=>f.namedItem(n);
    if(v("project")) st.sel.project=v("project").value;
    if(v("image")) st.sel.image=v("image").value;
    const m=form.querySelector('input[name="method"]:checked'); if(m) st.sel.method=m.value;
    st.sel.oams=[...form.querySelectorAll('input[name="oam"]:checked')].map(x=>parseInt(x.value,10));
    if(v("force")) st.sel.force=v("force").checked;
    if(v("reset_verify")) st.sel.reset_verify=v("reset_verify").checked;
  }
  function pickHtml(){
    const projects=[...new Set(st.images.filter(i=>i.valid).map(i=>i.project))].sort();
    if(!st.sel.project||!projects.includes(st.sel.project)) st.sel.project=projects[0]||"";
    const list=imgs();
    if(!list.some(i=>i.directory===st.sel.image)) st.sel.image=(list[0]||{}).directory||"";
    const bad=st.images.filter(i=>!i.valid).length;
    const img=list.find(i=>i.directory===st.sel.image);
    const ms=Object.entries(st.disc.methods);
    if(!st.sel.method||!(st.disc.methods[st.sel.method]||{}).available) st.sel.method=st.disc.default||"";
    const m=method(), oams=(m&&m.oams)||[];
    if(!st.sel.oams) st.sel.oams=[];
    const one=st.sel.method==="spi-bmc";
    return '<fieldset><legend>Image</legend>'
      +(st.images.length?"":'<p class="vm-t-err">The fw-deploy store is empty. Import an image first: '
        +'<code>fw-deploy import &lt;image&gt; --project \u2026 --topic \u2026 --notes \u2026 --platform \u2026</code></p>')
      +'<label>Project<select name="project">'+projects.map(p=>'<option'+(p===st.sel.project?" selected":"")+'>'+esc(p)+'</option>').join("")+'</select></label>'
      +'<label>Image<select name="image">'+list.map(i=>'<option value="'+esc(i.directory)+'"'+(i.directory===st.sel.image?" selected":"")+'>'
        +esc([i.deployment,i.platform,i.variant].filter(Boolean).join(" / ")+" \u00b7 "+(i.created_utc||"").slice(0,16))+'</option>').join("")+'</select></label>'
      +(img?'<p class="flash-imgmeta"><code>'+esc(img.image)+'</code> \u00b7 '+flashSize(img.size)+' \u00b7 sha256 <code title="'+esc(img.sha256)+'">'+esc((img.sha256||"").slice(0,16))+'\u2026</code>'
        +(img.notes?'<br><span class="muted">'+esc(String(img.notes).split("\n").find(l=>l.trim())||"")+'</span>':"")+'</p>':"")
      +(bad?'<p class="ro-note">'+bad+' store entr'+(bad>1?"ies":"y")+' failed verification and are hidden.</p>':"")
      +'</fieldset><fieldset><legend>Method</legend>'
      +ms.map(([k,v])=>'<label class="flash-radio'+(v.available?"":" off")+'" title="'+esc(v.reason||"")+'">'
        +'<input type="radio" name="method" value="'+esc(k)+'"'+(k===st.sel.method?" checked":"")+(v.available?"":" disabled")+'> '
        +esc(v.label||k)+(v.available?"":' <span class="muted">\u2014 '+esc(v.reason||"unavailable")+'</span>')+'</label>').join("")
      +'</fieldset><fieldset><legend>OAMs'+(oams.length&&!one?' <button type="button" class="linklike flash-all">all</button> \u00b7 <button type="button" class="linklike flash-none">none</button>':"")+'</legend>'
      +(oams.length?'<div class="flash-oams">'+oams.map(o=>{
          const ready=!o.state||/^ready$/i.test(o.state);
          return '<label class="flash-oam'+(ready?"":" warn")+'"><input type="'+(one?"radio":"checkbox")+'" name="oam" value="'+o.index+'"'
            +(st.sel.oams.includes(o.index)?" checked":"")+'> <b>'+esc(o.label||("OAM "+o.index))+'</b>'
            +(o.state?' <span class="vm-st '+(ready?"passed":"failed")+'">'+esc(o.state)+'</span>':"")
            +(o.build||o.version?'<br><span class="muted">'+esc(o.build||o.version)+'</span>':"")+'</label>';
        }).join("")+'</div>':'<p class="muted">'+(m?"No OAMs reported for this method.":"No usable flash method on this machine.")+'</p>')
      +'</fieldset><fieldset><legend>Options</legend>'
      +'<label class="flash-radio"><input type="checkbox" name="reset_verify"'+(st.sel.reset_verify?" checked":"")+'> Reset &amp; verify boot after writing'
      +' <span class="muted">(intrusive: resets each OAM and checks the booted build)</span></label>'
      +'<label class="flash-radio"><input type="checkbox" name="force"'+(st.sel.force?" checked":"")+'> Flash OAMs that are not READY'
      +' <span class="muted">(skips the readiness gate)</span></label></fieldset>';
  }
  function reviewHtml(){
    const img=st.images.find(i=>i.directory===st.sel.image)||{}, m=method()||{};
    const rows=(m.oams||[]).filter(o=>st.sel.oams.includes(o.index));
    return '<p>Review before writing. Read-back verification runs after every write'
      +(st.sel.reset_verify?", then each OAM is reset and its booted build checked":"")+'.</p>'
      +'<dl class="vm-kv flash-review"><dt>Machine</dt><dd>'+esc(target)+'</dd>'
      +'<dt>Method</dt><dd>'+esc(m.label||st.sel.method)+'</dd>'
      +'<dt>Image</dt><dd>'+esc([img.project,img.deployment,img.platform,img.variant].filter(Boolean).join(" / "))+'<br><code>'+esc(img.path||"")+'</code></dd>'
      +'<dt>SHA-256</dt><dd><code>'+esc(img.sha256||"")+'</code></dd>'
      +'<dt>Size</dt><dd>'+flashSize(img.size)+'</dd>'
      +'<dt>OAMs</dt><dd>'+rows.map(o=>esc(o.label||("OAM "+o.index))+(o.build?' <span class="muted">(now '+esc(o.build)+')</span>':"")).join("<br>")+'</dd>'
      +(st.sel.force?'<dt>Force</dt><dd class="vm-t-err">readiness gate skipped</dd>':"")+'</dl>'
      +'<p class="ro-note">A write in progress cannot be stopped; the job can be cancelled only before the write phase starts.</p>';
  }
  function validate(){
    collect();
    const n=st.sel.oams.length, ok=!!(st.sel.image&&st.sel.method&&n);
    submit.disabled=!ok||st.busy;
    submit.textContent=st.step==="pick"?"Review\u2026":"\u26a1 Flash "+n+" OAM"+(n===1?"":"s");
    msg.textContent=st.step==="pick"&&!ok?(!st.sel.image?"Pick an image":!st.sel.method?"No usable method":"Select at least one OAM"):"";
  }
  function draw(){
    back.hidden=st.step!=="review";
    body.innerHTML=st.step==="pick"?pickHtml():reviewHtml();
    body.querySelectorAll("select,input").forEach(x=>x.addEventListener("change",()=>{
      collect();
      if(["project","method"].includes(x.name)||x.name==="image"){ if(x.name==="method") st.sel.oams=[]; draw(); }
      else validate();
    }));
    const all=body.querySelector(".flash-all"), none=body.querySelector(".flash-none");
    if(all) all.addEventListener("click",()=>{ body.querySelectorAll('input[name="oam"]').forEach(x=>x.checked=true); validate(); });
    if(none) none.addEventListener("click",()=>{ body.querySelectorAll('input[name="oam"]').forEach(x=>x.checked=false); validate(); });
    validate();
  }
  form.addEventListener("submit",async e=>{
    e.preventDefault();
    if(st.busy||submit.disabled) return;
    if(st.step==="pick"){ collect(); st.step="review"; draw(); submit.focus(); return; }
    st.busy=true; submit.disabled=true; back.disabled=true; msg.textContent="Starting\u2026";
    const win=window.open("about:blank","flash-pending","popup=yes,width=980,height=760");
    try{
      const r=await fetch("/api/flash/start",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({target,method:st.sel.method,image:st.sel.image,oams:st.sel.oams,
          force:!!st.sel.force,reset_verify:!!st.sel.reset_verify})});
      const j=await r.json();
      if(!r.ok) throw new Error(j.error||"flash failed to start");
      if(win){ win.location.href="/flash?"+new URLSearchParams({id:j.job}).toString(); win.name="flash-"+j.job; }
      else flashWindow(j.job);
      st.busy=false; dialog.close();
      vmLivePoll(); vmTimersSync();
    }catch(err){
      if(win) win.close();
      msg.textContent=err.message; st.busy=false; back.disabled=false; validate();
    }
  });
  (async()=>{
    try{
      const [ri,rd]=await Promise.all([fetch("/api/flash/images",{cache:"no-store"}),
        fetch("/api/flash/discover",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target})})]);
      const ji=await ri.json(), jd=await rd.json();
      if(!ri.ok) throw new Error("image store: "+(ji.error||ri.status));
      if(!rd.ok) throw new Error("probe "+target+": "+(jd.error||rd.status));
      st.images=ji.images||[]; st.disc=jd; draw();
    }catch(err){
      body.innerHTML='<p class="vm-t-err">\u26a0 '+esc(err.message)+'</p>';
    }
  })();
}
// One ↻ per target. Scoping matters because an unreachable host costs a 90 s
// ssh timeout, so "just the VM" returns in seconds where the fleet takes minutes.
function vmTargetBtn(name){
  return '<button class="vm-trf" data-target="'+esc(name)+'"'
    +' title="Probe '+esc(name)+' only \u2014 one ssh round trip, no octl, no other target">'
    +'\u21bb</button>';
}
function vmBindTargetRefresh(box){
  box.querySelectorAll(".vm-trf").forEach(b=>b.addEventListener("click",()=>
    refreshVmlog(b.dataset.target)));
}
function vmMachine(m){
  const on=m.state==="running", busy=["pending","stopping","shutting-down"].includes(m.state);
  const act=on?"stop":"start";
  const lease=m.lease_left_h
    ? '<span class="vm-lease'+(m.lease_left<=0?" bad":(m.lease_left<3600?" warn":""))+'"'
      +(m.lease_left>0&&m.lease_left<3600?' title="Lease expires within the hour \u2014 extend it or the instance stops"':"")+'>lease '+esc(m.lease_left_h)
      +(m.lease_left>0?" left":"")+'</span>' : "";
  const t=(VMLOG.target_state||[]).find(x=>x.name===m.target);
  const main=m.main
    ? '<span class="vm-main" title="Main VM: target vm \u2014 neo-ask and default delegation run here">Main</span>'
    : "";
  const title='<span class="vm-dot '+esc(m.state)+'"></span><h4>'+esc(m.name)+'</h4>'
    +'<span class="vm-m-st">'+esc(m.state)+'</span>'+main+(m.target?vmLiveBadge(m.target):"");
  const tools=(m.target?vmTermBtn(m.target,on):"")
    +'<button class="vm-pw '+act+'" data-action="'+act+'" data-id="'+esc(m.id)+'"'
    +(busy?" disabled":"")+' title="octl vm '+act+' '+esc(m.id)+'">'
    +(busy?esc(m.state):(on?"\u25a0 Stop":"\u25b6 Start"))+'</button>'
    +(m.target?vmTargetBtn(m.target):"")
    +vmActions(m.target||"",m.id,!!m.main,on);
  const body=vmKv([
    ["Instance",esc(m.id)+' \u00b7 '+esc(m.type)+(m.ip?' \u00b7 '+esc(m.ip):"")],
    ["Target",m.target?'<b>'+esc(m.target)+'</b>':'<span class="vm-t-age">not tracked</span>'],
    ["Note",m.desc?esc(m.desc):""],
    ["Power",[lease,vmPowerLine(m)].filter(Boolean).join(" \u00b7 ")],
    ["Jobs",vmJobsLine(t)],
    ["Error",t&&t.error?'<span class="vm-t-err">\u26a0 '+esc(t.error)+'</span>':""],
    ["Setup",t?vmProvBadge(t):""],
  ])+(m.target?vmSsoSlot(m.target):"");
  return vmCard({key:m.id,label:m.name,cls:m.main?"main":"",title,tools,body});
}
async function vmPower(action,id,btn){
  const note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to control the VM"; return; }
  if(action==="stop" && !confirm("Stop "+id+"? Any running job on it dies with the machine."))
    return;
  document.querySelectorAll(".vm-pw").forEach(b=>b.disabled=true);
  btn.textContent="\u2026"; note.textContent="octl vm "+action+" "+id+"\u2026";
  try{
    const r=await fetch("/api/vm-power",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({action,id})});
    const j=await r.json();
    if(!r.ok||(j.error&&!j.machines)) throw new Error(j.error||(action+" failed"));
    vmAdoptPayload(j); renderVmlog(); buildSections();
    note.textContent=action+" requested \u00b7 state follows in a minute";
  }catch(e){
    note.textContent=action+" failed: "+e.message;
    document.querySelectorAll(".vm-pw").forEach(b=>b.disabled=false);
  }
}
// SSO login: `octl login` runs on the target (never starting a stopped VM); the
// device link it prints is opened here. The window is opened on the click,
// because a pop-up opened after an await is blocked, then pointed at the link.
// Only an AWS SSO https link is ever opened (the server filters it as well).
const VMSSO={logins:{},wins:{},timer:null,loaded:false};
function vmSsoOk(url){
  try{ const u=new URL(url);
    return u.protocol==="https:"&&!u.port&&!u.username&&!u.password
      &&(/^(?:[a-z0-9-]+\.awsapps\.com|device\.sso\.[a-z0-9-]+\.amazonaws\.com)$/.test(u.hostname)
        ||(/^[a-z]{2}(?:-[a-z]+)+-\d+\.signin\.aws$/.test(u.hostname)&&u.pathname==="/platform/authorize"));
  }catch(e){ return false; }
}
function vmSsoActive(l){ return !!l&&(l.state==="starting"||l.state==="waiting"); }
function vmSsoSlot(target){
  return '<div class="vm-sso-slot" data-target="'+esc(target)+'">'+vmSsoHtml(target)+'</div>';
}
function vmSsoHtml(target){
  const l=VMSSO.logins[target];
  if(!l) return "";
  const t=(VMLOG.target_state||[]).find(x=>x.name===target);
  const shared=t&&!t.instance&&target!=="vm"?" \u00b7 lab machines share one home, so this covers them all":"";
  let body='<b>SSO login:</b> '+esc(l.message||l.state);
  if(vmSsoActive(l)){
    if(l.code) body+=' \u00b7 code <code class="vm-sso-code">'+esc(l.code)+'</code>';
    if(l.url&&vmSsoOk(l.url)) body+=' \u00b7 <a href="'+esc(l.url)+'" target="_blank" rel="noopener noreferrer">Open link \u2197</a>';
    body+=' <button class="vm-sso-cancel btn" data-target="'+esc(target)+'">Cancel</button>';
  }else if(l.state==="done") body+=shared;
  return '<div class="vm-sso '+esc(l.state)+'" role="status">'+body+'</div>';
}
function vmSsoPaint(){
  document.querySelectorAll(".vm-sso-slot").forEach(slot=>{
    const html=vmSsoHtml(slot.dataset.target);
    if(slot.innerHTML!==html){ slot.innerHTML=html; vmSsoBind(slot); }
  });
}
function vmSsoBind(box){
  box.querySelectorAll(".vm-sso-cancel").forEach(b=>b.addEventListener("click",()=>
    vmSsoPost(b.dataset.target,"cancel")));
}
function vmSsoWin(target,l){
  const w=VMSSO.wins[target];
  if(!w) return;
  if(w.closed){ delete VMSSO.wins[target]; return; }
  if(l&&l.url&&vmSsoOk(l.url)){
    try{ w.opener=null; w.location.replace(l.url); }catch(e){}
    delete VMSSO.wins[target];
  }else if(!vmSsoActive(l)){ w.close(); delete VMSSO.wins[target]; }
}
function vmSsoAdopt(logins){
  VMSSO.logins=logins||{};
  Object.keys(VMSSO.wins).forEach(t=>vmSsoWin(t,VMSSO.logins[t]));
  vmSsoPaint();
  const busy=Object.values(VMSSO.logins).some(vmSsoActive)||Object.keys(VMSSO.wins).length;
  clearTimeout(VMSSO.timer);
  if(busy) VMSSO.timer=setTimeout(vmSsoPoll,2000);
}
async function vmSsoPoll(){
  if(!SERVED) return;
  VMSSO.loaded=true;
  try{
    const r=await fetch("/api/sso-login");
    const j=await r.json();
    if(r.ok) vmSsoAdopt(j.logins);
  }catch(e){ clearTimeout(VMSSO.timer); VMSSO.timer=setTimeout(vmSsoPoll,5000); }
}
async function vmSsoPost(target,action){
  const r=await fetch("/api/sso-login",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({target,action})});
  const j=await r.json();
  if(!r.ok) throw new Error(j.error||"SSO login failed");
  VMSSO.logins[target]=j.login;
  vmSsoAdopt(VMSSO.logins);
  return j.login;
}
async function vmSsoStart(target){
  const note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to run SSO login"; return; }
  const w=window.open("","_blank");
  if(w){
    try{ w.document.title="SSO login \u2014 "+target;
      w.document.body.textContent="Waiting for the AWS SSO link from "+target+"\u2026"; }catch(e){}
    VMSSO.wins[target]=w;
  }
  try{
    await vmSsoPost(target,"start");
    note.textContent="SSO login started on "+target+(w?"":" \u2014 pop-up blocked, use Open link on the card");
  }catch(e){
    if(w) w.close();
    delete VMSSO.wins[target];
    note.textContent="SSO login failed: "+e.message;
  }
}
async function vmSetMain(id){
  const note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to change the Main VM"; return; }
  if(!confirm("Make "+id+" the Main VM?\n\nTarget \u201cvm\u201d (neo-ask, default delegation) will point at it; "
    +"the current Main stays registered as vm-<id>. Workspaces mirrored on the old VM are not moved."))
    return;
  document.querySelectorAll(".vm-actions").forEach(b=>b.disabled=true);
  note.textContent="mctl vm main "+id+"\u2026";
  try{
    const r=await fetch("/api/vm-main",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({id})});
    const j=await r.json();
    if(!r.ok||(j.error&&!j.machines)) throw new Error(j.error||"switch failed");
    vmAdoptPayload(j); renderVmlog(); buildSections();
    note.textContent="Main VM is now "+id;
  }catch(e){
    note.textContent="Set main failed: "+e.message;
    document.querySelectorAll(".vm-actions").forEach(b=>b.disabled=false);
  }
}
async function vmTargetPost(body){
  const r=await fetch("/api/vm-target",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok||!Array.isArray(j.targets)) throw new Error(j.error||"Machine tracking failed");
  vmAdoptPayload(j); renderVmlog(); buildSections();
  document.getElementById("vm-refresh-note").textContent=j.message;
  return j;
}
async function vmRemoveTarget(name){
  if(!SERVED) return false;
  if(!confirm("Remove tracking for "+name+"?\n\nThis unregisters the shared mctl target and disables its portal controls."
    +" The machine is not stopped or deleted; job and workspace history is kept."
    +" EC2 instances remain visible as untracked inventory.")) return false;
  await vmTargetPost({action:"remove",name});
  return true;
}
// One form per registry row. Name and kind are keys elsewhere (job history,
// cached $HOME, provision state), so they are shown but never editable.
function vmEditDialog(name){
  if(!SERVED) return;
  const t=(VMLOG.target_state||[]).find(x=>x.name===name);
  if(!t) return;
  const isVm=t.kind==="vm"||!!t.instance, mainVm=name==="vm";
  const loginPat=isVm?"([A-Za-z_][A-Za-z0-9._-]{0,63})?":"[A-Za-z_][A-Za-z0-9._-]{0,63}";
  const previousFocus=document.activeElement, dialog=document.createElement("dialog");
  dialog.className="ws-modal vm-add-dialog vm-edit-dialog";
  dialog.setAttribute("aria-labelledby","vm-edit-title");
  dialog.innerHTML='<form><h3 id="vm-edit-title">Machine details \u00b7 '+esc(name)+'</h3>'
    +'<p class="ro-note">Edits the shared mctl target. Does not connect to, start or provision the machine.</p>'
    +'<label>Target name<input name="name" value="'+esc(name)+'" readonly'
      +' title="The name keys job history and workspace bindings; remove and re-add to rename"></label>'
    +'<label>Machine type<input name="kind" value="'+(isVm?"EC2 VM":"SSH machine")+'" readonly></label>'
    +'<label>'+(isVm?"EC2 instance ID":"SSH hostname / IPv4 address")+'<input name="address" required'
      +' value="'+esc(t.host||t.instance||"")+'" pattern="'+(isVm?"i-[0-9a-f]{8,17}":"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")+'"></label>'
    +'<label>SSH login<input name="login"'+(isVm?"":" required")+' pattern="'+loginPat+'"'
      +' value="'+esc(t.login||"")+'" autocomplete="username"></label>'
    +(isVm?"":'<label>BMC host (empty = none)<input name="bmc" value="'+esc(t.bmc||"")+'" placeholder="bmc-sa-els23-csr"'
      +' pattern="([A-Za-z_][A-Za-z0-9._-]{0,63}@)?[A-Za-z0-9][A-Za-z0-9.-]{0,252}"></label>')
    +'<p class="ro-note">A login or host change drops mctl\u2019s cached home/IP for this target.</p>'
    +'<div class="ws-modal-act">'
    +'<button type="button" class="btn danger vm-edit-delete"'+(mainVm?' disabled title="Switch the Main VM before removing its target"':
      ' title="Unregister this target (machine and history are kept)"')+'>Delete tracking</button>'
    +'<span class="ws-modal-msg" role="status"></span>'
    +'<button type="button" class="ws-modal-cancel">Cancel</button>'
    +'<button type="submit" class="primary">Save</button></div></form>';
  document.body.appendChild(dialog);
  let saving=false;
  const msg=dialog.querySelector(".ws-modal-msg");
  const close=()=>{ if(!saving) dialog.close(); };
  dialog.addEventListener("close",()=>{ dialog.remove(); previousFocus?.focus(); });
  dialog.addEventListener("cancel",e=>{ if(saving) e.preventDefault(); });
  dialog.addEventListener("keydown",e=>{
    e.stopPropagation();
    if(e.key==="Escape"){ e.preventDefault(); close(); }
  });
  dialog.querySelector(".ws-modal-cancel").addEventListener("click",close);
  dialog.querySelector(".vm-edit-delete").addEventListener("click",async()=>{
    if(saving) return;
    saving=true;
    try{ if(await vmRemoveTarget(name)){ saving=false; close(); } }
    catch(error){ msg.textContent="Remove tracking failed: "+error.message; }
    finally{ saving=false; }
  });
  dialog.querySelector("form").addEventListener("submit",async e=>{
    e.preventDefault();
    if(saving) return;
    const form=e.target, button=form.querySelector('[type="submit"]');
    const get=n=>form.elements.namedItem(n).value.trim();
    const req={action:"edit",name,login:get("login"),address:get("address")};
    if(!isVm) req.bmc=get("bmc");
    if(req.login===(t.login||"")&&req.address===(t.host||t.instance||"")&&(isVm||req.bmc===(t.bmc||""))){
      close(); return;
    }
    saving=true; button.disabled=true;
    try{ await vmTargetPost(req); saving=false; close(); }
    catch(error){ msg.textContent=error.message; }
    finally{ saving=false; button.disabled=false; }
  });
  dialog.showModal();
}
function vmAddDialog(instance=""){
  if(!SERVED){ document.getElementById("vm-refresh-note").textContent="Serve the portal to add machines"; return; }
  const previousFocus=document.activeElement, dialog=document.createElement("dialog");
  dialog.className="ws-modal vm-add-dialog";
  dialog.setAttribute("aria-labelledby","vm-add-title");
  dialog.innerHTML='<form><h3 id="vm-add-title">Add tracked machine</h3>'
    +'<p class="ro-note">Registers a shared mctl target. Does not connect, start or provision the machine.</p>'
    +'<label>Target name<input name="name" required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,62}" maxlength="63" placeholder="build-desktop"></label>'
    +'<label>Machine type<select name="kind"><option value="remote">SSH machine</option>'
    +'<option value="vm"'+(instance?" selected":"")+'>EC2 VM</option></select></label>'
    +'<label>SSH hostname / IPv4 address, or EC2 instance ID<input name="address" required value="'+esc(instance)+'"></label>'
    +'<label>SSH login<input name="login" required pattern="[A-Za-z_][A-Za-z0-9._-]{0,63}" autocomplete="username"></label>'
    +'<label>BMC host (optional, SSH machines only)<input name="bmc" placeholder="bmc-sa-els23-csr"'
    +' pattern="([A-Za-z_][A-Za-z0-9._-]{0,63}@)?[A-Za-z0-9][A-Za-z0-9.-]{0,252}"></label>'
    +'<p class="ro-note">Enable live status separately in Settings &gt; Machines.</p>'
    +'<div class="ws-modal-act"><span class="ws-modal-msg" role="status"></span>'
    +'<button type="button" class="ws-modal-cancel">Cancel</button>'
    +'<button type="submit" class="primary">Add machine</button></div></form>';
  document.body.appendChild(dialog);
  let saving=false;
  const close=()=>{ if(!saving) dialog.close(); };
  dialog.addEventListener("close",()=>{ dialog.remove(); previousFocus?.focus(); });
  dialog.addEventListener("cancel",e=>{ if(saving) e.preventDefault(); });
  dialog.addEventListener("keydown",e=>{
    e.stopPropagation();
    if(e.key==="Escape"){ e.preventDefault(); close(); }
  });
  dialog.querySelector(".ws-modal-cancel").addEventListener("click",close);
  dialog.querySelector("form").addEventListener("submit",async e=>{
    e.preventDefault();
    if(saving) return;
    const form=e.target, button=form.querySelector('[type="submit"]');
    const get=name=>form.elements.namedItem(name).value.trim();
    saving=true; button.disabled=true;
    try{
      const req={action:"add",name:get("name"),kind:get("kind"),address:get("address"),login:get("login")};
      if(req.kind==="remote"&&get("bmc")) req.bmc=get("bmc");
      await vmTargetPost(req);
      saving=false; close();
    }catch(error){ form.querySelector(".ws-modal-msg").textContent=error.message; }
    finally{ saving=false; button.disabled=false; }
  });
  dialog.showModal();
}
function vmRow(j){
  const c=VM_STATUS_COLOR[j.status]||PBI.grey;
  const rc=(j.rc===null||j.rc===undefined)?"":" rc="+j.rc;
  const tail=j.last_line&&j.status!=="running"
    ? '<span class="vm-tail" title="'+esc(j.last_line)+'">'+esc(j.last_line)+'</span>':"";
  const title=(VM_STATUS_TITLE[j.status]||"")+(j.note?" \u2014 "+j.note:"")
    +(j.stale?" \u2014 "+vmStaleTitle(j):"");
  return '<tr class="task'+(j.status==="running"?" on":"")+(j.stale?" vm-stale":"")+'">'
    +'<td title="'+esc([j.dest,j.instance].filter(Boolean).join(" \u00b7 "))+'">'+esc(j.target)+'</td>'
    +'<td>'+esc(j.kind)+'</td>'
    +'<td class="vm-ws"><b>'+esc(j.ws_label||j.tag)+'</b>'
      +(j.adopted?'<span>found on target \u00b7 not dispatched from here</span>':"")+'</td>'
    +'<td class="vm-cmd">'+(j.cmd?'<code>'+esc(j.cmd)+'</code>'
      :'<span class="muted" title="This job predates the dispatch log, so the command was '
       +'never recorded. Jobs delegated from now on carry it.">not recorded</span>')+'</td>'
    +'<td>'+esc(formatPortalDate(j.started))+'</td>'
    // A confirmed-running row ticks with the live card, so the two cannot
    // disagree; anything else shows its settled (or last-verified) duration.
    +'<td>'+((j.status==="running"&&!j.stale&&j.started_ts)
      ? '<span class="vm-el" data-start="'+j.started_ts+'">'
        +esc(vmElapsedText(Date.now()/1000-vmSkew()-j.started_ts))+'</span>'
      : esc(j.duration_h||"\u2014"))+'</td>'
    +'<td title="'+esc(title)+'">'
      +'<span class="vm-st '+esc(j.status)+'" style="--sk:'+c+'">'+esc(j.status)+esc(rc)+'</span>'
      +'<span class="vm-verline">'+vmVerified(j)+'</span>'+tail+'</td>'
    +'<td class="vm-logcell">'+vmLogLink(j)+vmFlashProgressBtn(j)+vmCancelButton(j)+'</td></tr>';
}
// Only a run that still owns its workspace slot can be stopped: mctl kills the
// pid recorded in that slot and refuses when a newer run has taken it over.
function vmCancelButton(j){
  if(j.kind==="flash"&&j.status==="running"&&!j.cancellable)
    return '<button class="vm-cancel" disabled title="Writing flash ('+esc(j.phase||"")+') \u2014 cannot be stopped safely">\u25a0 stop</button>';
  if(j.status!=="running"||j.owns_slot===false||!j.pid||!["build","test","run","flash"].includes(j.kind))
    return "";
  return '<button class="vm-cancel" data-id="'+esc(j.id)+'" data-label="'
    +esc(j.kind+" "+(j.ws_label||j.tag)+" on "+j.target)+'"'
    +' title="Stop this job on '+esc(j.target)+' (queued as a portal action)">\u25a0 stop</button>';
}
function vmFlashProgressBtn(j){
  if(j.kind!=="flash") return "";
  return '<button class="vm-flash-open" data-id="'+esc(j.id)+'" title="Open the flash progress window (per-OAM state, phases, result)">'
    +'\u26a1 progress</button>';
}
async function vmCancel(id,label){
  const note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to stop a job"; return; }
  if(!confirm("Stop "+label+"? The process group is terminated on the machine; "
    +"containers it started may need manual cleanup.")) return;
  note.textContent="stopping "+label+"\u2026";
  try{
    const r=await fetch("/api/vm-cancel",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
    const j=await r.json();
    if(!r.ok||!j.job) throw new Error(j.error||"cancel failed");
    const done=await pollAlign(j.job,"Stopping "+label+"\u2026");
    const rv=await fetch("/api/vm-jobs",{cache:"no-store"});
    if(rv.ok){ vmAdoptPayload(await rv.json()); renderVmlog(); buildSections(); }
    note.textContent=(done&&done.message)||"cancel finished";
  }catch(e){
    note.textContent="Stop failed: "+e.message;
  }
}
// The Log cell. The remote keeps ONE log per (workspace, kind), so "a log
// exists" never implies "this run's log exists": `has_log` is now server-side
// provenance (the stored copy's pid matches this dispatch). A job that still
// owns its slot is tailed live; an older run can only be opened if its output
// was mirrored before a successor overwrote it.
function vmLogLink(j){
  const live=j.status==="running"&&j.owns_slot!==false;
  // Openable when this run's output is still reachable: mirrored here with
  // matching provenance, or still the slot's current log on the target.
  // Runs dispatched since mctl archives each log (runs/<kind>-<stamp>-<pid>.log)
  // can still be found on the target after a successor took the slot.
  const canOpen=j.has_log||j.owns_slot!==false||!!j.pid;
  const meta=(j.lines?j.lines+" ln":"")
    +(j.errors?' <b style="color:'+PBI.red+'">'+j.errors+'\u2715</b>':"");
  if(!live&&!canOpen){
    const lost=j.owns_slot===false
      ? "A later run in the same workspace overwrote "+j.kind+".log on the target, and "
        +"this run's output was never mirrored here"
        +(j.log_pid?" (the stored copy belongs to pid "+j.log_pid+")":"")+"."
      : "No copy of this run held. Hit Sync logs \u2014 the target keeps only the newest "
        +"log per workspace and overwrites it on the next run.";
    return '<span class="muted" title="'+esc(lost)+'">'+(meta||"\u2014")+'</span>';
  }
  // Not yet probed but still the slot's owner: the remote log IS this run's.
  const what=live?"Open a terminal tailing this log live on "+j.target
    :(j.has_log?"Open this run\u2019s stored log in a terminal pager ("+Math.round((j.log_bytes||0)/1024)+" KB"
        +(j.log_at?", mirrored "+j.log_at:"")+")"
      :(j.owns_slot===false?"Look up this run\u2019s archived log on "+j.target
        :"Open this run\u2019s log on "+j.target+" in a terminal pager"));
  return '<button class="vm-log'+(live?" live":"")+'" data-target="'+esc(j.target)+'"'
    +' data-tag="'+esc(j.tag)+'" data-kind="'+esc(j.kind)+'" data-live="'+(live?1:0)+'"'
    +' data-id="'+esc(j.id)+'"'
    +' title="'+esc(what)+'">'
    +(live?'<span class="spin">\u21bb</span> tail':'\u2317 open')+'</button>'
    +(meta?'<span class="vm-logmeta">'+meta+'</span>':"");
}

// --- log + terminal windows ----------------------------------------------
// Logs open in a terminal window that runs `tail` on the machine itself
// (tail -F while the run is live, the last lines of a finished or archived
// run), streamed over the portal's websocket.
// Nothing is downloaded or polled; closing the window ends the ssh session.
function termUrl(params){
  return "/term?"+new URLSearchParams(params).toString();
}
function termOpen(params,name){
  if(!SERVED){ alert("Serve the portal to open a terminal"); return null; }
  const w=window.open(termUrl(params),name||"",
    "popup=yes,width=1100,height=700");
  if(!w) alert("The browser blocked the terminal window \u2014 allow pop-ups for this page.");
  return w;
}
function vmLogOpen(target,tag,kind,live,id){
  if(!id) return;
  termOpen({kind:"log",job:id},"log-"+id);
}
function vmTermOpen(target){
  termOpen({kind:"shell",target},"term-"+target);
}
// One ssh round trip per target plus an `octl vm list`. Dispatched as a
// background job and polled through the align banner: an unreachable target
// costs a 90 s ssh timeout, which used to outlive the browser's patience and
// leave the section frozen on stale data.
async function refreshVmlog(only){
  const btn=only?null:document.getElementById("vm-refresh");
  const note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to refresh"; return; }
  if(btn){ btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Probing\u2026'; }
  document.querySelectorAll(".vm-trf").forEach(b=>{
    b.disabled=true; if(b.dataset.target===only) b.innerHTML='<span class="spin">\u21bb</span>';
  });
  note.textContent=only?("asking "+only+" for its job dirs\u2026")
                       :"asking VM and lab-machine targets for their job dirs\u2026";
  try{
    const r=await fetch("/api/vm-refresh",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(only?{target:only}:{})});
    const j=await r.json();
    if(!r.ok||!j.job) throw new Error(j.error||"refresh failed");
    const done=await pollAlign(j.job, only?("Probing "+only+"\u2026"):"Probing machine targets\u2026");
    if(done&&done.result){ vmAdoptPayload(done.result); renderVmlog(); buildSections(); }
    note.textContent=(done&&done.message)||"probe finished";
  }catch(e){
    note.textContent="Refresh failed: "+e.message;
  }finally{
    if(btn){ btn.disabled=false; btn.innerHTML="\u21bb Refresh status"; }
    document.querySelectorAll(".vm-trf").forEach(b=>{ b.disabled=false; b.innerHTML="\u21bb"; });
  }
}
// The toolbar button honours the target filter: picking a target in the
// dropdown and hitting Refresh asks only that machine, which is seconds rather
// than the minutes a fleet probe costs with an unreachable host in it.
document.getElementById("vm-refresh").addEventListener("click",()=>refreshVmlog(vmTarget||null));
// Mirror the remote job logs locally so an old one stays openable. The target
// keeps exactly one log per (workspace, kind) and clobbers it on the next run.
async function syncVmLogs(){
  const btn=document.getElementById("vm-sync"), note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to sync logs"; return; }
  const only=vmTarget||null;
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Syncing\u2026';
  note.textContent="rsyncing job logs from "+(only||"every VM and lab-machine target")+"\u2026";
  try{
    const r=await fetch("/api/vm-sync",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(only?{target:only}:{})});
    const j=await r.json();
    if(!r.ok||(j.error&&!j.jobs)) throw new Error(j.error||"sync failed");
    vmAdoptPayload(j); renderVmlog();
    note.textContent=(j.logs_held||0)+" log(s) held locally"
      +(j.sync_error?" \u00b7 \u26a0 "+j.sync_error:"");
  }catch(e){
    note.textContent="Log sync failed: "+e.message;
  }finally{
    btn.disabled=false; btn.innerHTML="\u2193 Sync logs";
  }
}
document.getElementById("vm-sync").addEventListener("click",syncVmLogs);
// Power state only — `octl vm list`, without the per-target ssh probe.
async function checkVms(){
  const btn=document.getElementById("vm-vms"), note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to query octl"; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Checking\u2026';
  note.textContent="octl vm list\u2026 (revalidates SSO, takes a moment)";
  try{
    const r=await fetch("/api/vm-machines",{method:"POST"});
    const j=await r.json();
    if(!r.ok||(j.error&&!j.machines)) throw new Error(j.error||"check failed");
    vmAdoptPayload(j); renderVmlog();
    const ms=j.machines||[], up=ms.filter(m=>m.state==="running").length;
    note.textContent=ms.length+" instance(s) \u00b7 "+up+" running \u00b7 "+esc(j.machines_checked||"");
  }catch(e){
    note.textContent="VM check failed: "+e.message;
  }finally{
    btn.disabled=false; btn.innerHTML="\u260d Check VM status";
  }
}
document.getElementById("vm-vms").addEventListener("click",checkVms);
document.getElementById("vm-add")?.addEventListener("click",()=>vmAddDialog());
document.getElementById("vm-target").addEventListener("change",e=>{
  vmTargets=msVals(e.target); vmTarget=vmTargets.length===1?vmTargets[0]:""; renderVmlog();
});
document.getElementById("vm-livefirst").addEventListener("change",renderVmlog);
document.getElementById("vm-openonly").addEventListener("change",renderVmlog);

const RAG_COLOR={red:PBI.red, orange:PBI.orange, yellow:PBI.gold, green:PBI.green};
const RAID_TYPES=[["risk","Risk"],["assumption","Assumption"],["issue","Issue"],["dependency","Dependency"]];
let raidOpenRow=null;

function raidRows(){
  const openOnly=document.getElementById("raid-openonly").checked;
  return (RAID.rows||[]).filter(r=>!openOnly||!r.closed);
}

function renderRaid(){
  const c=RAID.counts||{}, rows=raidRows(), today=isoDay(new Date());
  document.getElementById("raid-asof").innerHTML=
    "<b>"+(c.open||0)+"</b> open \u00b7 <b>"+(c.hot||0)+"</b> red / orange \u00b7 <b>"
    +(c.overdue||0)+"</b> overdue \u00b7 "+(c.closed||0)+" closed"
    +(RAID.error?' \u00b7 <span class="stale-note">'+esc(RAID.error)+"</span>":"")
    +(SERVED?"":' \u00b7 <span class="ro-note">serve the portal to edit</span>');

  const open=(RAID.rows||[]).filter(r=>!r.closed);
  const byRag=g=>open.filter(r=>r.rag===g).length;
  document.getElementById("raid-kpis").innerHTML=[
    kpi({c:PBI.red,    label:"Red",      n:byRag("red"),      sub:"act now"}),
    kpi({c:PBI.orange, label:"Orange",   n:byRag("orange"),   sub:"needs a plan"}),
    kpi({c:PBI.gold,   label:"Yellow",   n:byRag("yellow"),   sub:"watch"}),
    kpi({c:PBI.green,  label:"Green",    n:byRag("green"),    sub:"under control"}),
    kpi({c:PBI.violet, label:"Overdue",  n:c.overdue||0,      sub:"trigger date passed"}),
    kpi({c:PBI.grey,   label:"Closed",   n:c.closed||0,       sub:"resolved to date"}),
  ].join("");

  document.getElementById("raid-body").innerHTML = rows.length
    ? rows.map(r=>{
        const overdue=!r.closed && r.due && r.due<today;
        return '<tr class="raid-row" data-id="'+esc(r.id)+'">'
          +'<td><span class="rid">'+esc(r.id)+'</span></td>'
          +'<td class="ttl">'+esc(r.title||"\u2014")+'</td>'
          +'<td><span class="ragp" style="--rk:'+(RAG_COLOR[r.rag]||PBI.grey)+'">'+esc(r.rag||"\u2014")+'</span></td>'
          +'<td>'+r.score+'</td><td>'+esc(r.owner||"\u2014")+'</td>'
          +'<td'+(overdue?' class="od"':'')+'>'+esc(formatPortalDate(r.due))+'</td>'
          +'<td class="stt">'+esc(r.closed?"closed":(r.status||"open"))+'</td></tr>'
          +'<tr class="detail" data-for="'+esc(r.id)+'" style="display:none;"><td colspan="7"></td></tr>';
      }).join("")
    : '<tr><td colspan="7"><div class="empty">No RAID rows.</div></td></tr>';

  document.querySelectorAll("#raid-body tr.raid-row").forEach(tr=>
    tr.addEventListener("click",()=>toggleRaid(tr.dataset.id)));
  if(raidOpenRow && rows.some(r=>r.id===raidOpenRow)){ const id=raidOpenRow; raidOpenRow=null; toggleRaid(id); }
  applyTableStandards(document.getElementById("sec-raid"));
}

function toggleRaid(id){
  const det=document.querySelector('#raid-body tr.detail[data-for="'+id+'"]');
  if(!det) return;
  if(det.style.display!=="none"){ det.style.display="none"; raidOpenRow=null; return; }
  document.querySelectorAll("#raid-body tr.detail").forEach(d=>d.style.display="none");
  const r=(RAID.rows||[]).find(x=>x.id===id);
  det.firstElementChild.innerHTML=raidDetail(r);
  det.style.display="";
  raidOpenRow=id;
  wireRaidEditor(id);
}

function raidField(label,val){
  return val ? '<div class="rf"><span>'+esc(label)+'</span><p>'+esc(val)+'</p></div>' : "";
}

function raidDetail(r){
  const ro=!SERVED;
  // data-orig lets Save submit only what actually changed, so untouched fields
  // keep their original Markdown in the RAID domain store.
  const inp=(name,val,extra)=>'<input data-f="'+name+'" data-orig="'+esc(val||"")+'" value="'
    +esc(val||"")+'"'+(extra||"")+(ro?" disabled":"")+'>';
  const sel=(name,opts,cur)=>{
    const vals=opts.map(o=>Array.isArray(o)?o[0]:o);
    const hit=vals.find(v=>String(v).toLowerCase()===String(cur).toLowerCase());
    return '<select data-f="'+name+'" data-orig="'+esc(hit==null?"":hit)+'"'+(ro?" disabled":"")+'>'
      +opts.map(o=>{const[v,l]=Array.isArray(o)?o:[o,o];
        return '<option value="'+esc(v)+'"'+(String(cur).toLowerCase()===String(v).toLowerCase()?" selected":"")+'>'+esc(l)+'</option>';}).join("")+'</select>';
  };
  const num=n=>[1,2,3,4,5].map(v=>[v,String(v)]);
  return '<div class="raid-det">'
    +'<div class="rf-grid">'
      +raidField("Description",r.description)+raidField("Trigger",r.trigger)
      +raidField("Mitigation",r.mitigation)+raidField("Contingency",r.contingency)
      +raidField("External owner",r.external)+raidField("Link",r.link)
      +raidField("Status",r.status)
    +'</div>'
    +'<div class="raid-edit">'
      +'<div class="re-row">'
        +'<label>Title'+inp("title",r.title_raw!=null?r.title_raw:r.title)+'</label>'
      +'</div>'
      +'<div class="re-row">'
        +'<label>Type'+sel("type",RAID_TYPES,r.type)+'</label>'
        +'<label>Owner'+inp("fw_owner",r.owner)+'</label>'
        +'<label>P'+sel("p",num(),r.p)+'</label>'
        +'<label>I'+sel("i",num(),r.i)+'</label>'
        +'<label>RAG'+sel("rag",["green","yellow","orange","red"],r.rag)+'</label>'
        +'<label>Due<input data-f="due" data-orig="'+esc(r.due_raw!=null?r.due_raw:r.due||"")+'" value="'
          +esc(formatPortalDateInput(r.due_raw!=null?r.due_raw:r.due))+'" type="text" inputmode="numeric" autocomplete="off"'
          +' placeholder="dd/mm/yyyy" pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}" title="Enter date as dd/mm/yyyy"'+(ro?" disabled":"")+'></label>'
      +'</div>'
      +'<div class="re-row">'
        +'<label>Mitigation<input data-f="mitigation" placeholder="leave blank to keep" '+(ro?"disabled":"")+'></label>'
        +'<label>Status<input data-f="status" placeholder="leave blank to keep" '+(ro?"disabled":"")+'></label>'
      +'</div>'
      +'<div class="re-act">'
        +'<button class="btn primary" data-a="save"'+(ro?" disabled":"")+'>Save</button>'
        +(r.closed?"":'<button class="btn" data-a="close"'+(ro?" disabled":"")+'>Close row\u2026</button>')
        +'<span class="ro-note" data-a="msg"></span>'
      +'</div>'
      // Inline, not prompt(): VS Code's integrated browser throws on prompt().
      +(r.closed||ro?"":'<div class="re-act re-close" data-a="close-box" hidden>'
        +'<input data-a="resolution" placeholder="Resolution \u2014 why is this row closed?">'
        +'<button class="btn primary" data-a="close-ok">Confirm close</button>'
        +'<button class="btn" data-a="close-cancel">Cancel</button>'
      +'</div>')
    +'</div></div>';
}

function wireRaidEditor(id){
  const root=document.querySelector('#raid-body tr.detail[data-for="'+id+'"] .raid-edit');
  if(!root) return;
  const msg=root.querySelector('[data-a="msg"]');
  const post=async body=>{
    msg.textContent="saving\u2026";
    try{
      body.expected_revision=RAID.revision;
      const res=await fetch("/api/raid/"+encodeURIComponent(id),
        {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const j=await res.json();
      if(!res.ok||j.error) throw new Error(j.error||"save failed");
      RAID=j.payload; raidOpenRow=id; renderRaid(); buildSections(); renderOverview();
    }catch(e){ msg.textContent="Failed: "+e.message; }
  };
  root.querySelector('[data-a="save"]').addEventListener("click",()=>{
    const body={};
    let invalidDue=false;
    root.querySelectorAll("[data-f]").forEach(el=>{
      let v=(el.value||"").trim();
      if(el.dataset.f==="due"){
        const iso=parsePortalDateInput(v);
        if(v&&!iso){
          el.setCustomValidity("Enter a valid date as dd/mm/yyyy.");
          el.reportValidity();
          invalidDue=true;
          return;
        }
        el.setCustomValidity("");
        v=iso;
      }
      if(!v) return;
      const orig=el.dataset.orig;
      if(orig!==undefined && v===orig.trim()) return;   // untouched
      body[el.dataset.f]=v;
    });
    if(invalidDue){ msg.textContent="Enter a valid due date as dd/mm/yyyy."; return; }
    msg.textContent="";
    if(!Object.keys(body).length){ msg.textContent="Nothing changed"; return; }
    post(body);
  });
  const cb=root.querySelector('[data-a="close"]');
  const box=root.querySelector('[data-a="close-box"]');
  if(cb && box){
    const inp=box.querySelector('[data-a="resolution"]');
    const submit=()=>{
      const res=inp.value.trim();
      if(!res){ msg.textContent="Resolution is required"; inp.focus(); return; }
      post({close:res});
    };
    cb.addEventListener("click",()=>{ box.hidden=false; msg.textContent=""; inp.focus(); });
    box.querySelector('[data-a="close-ok"]').addEventListener("click",submit);
    box.querySelector('[data-a="close-cancel"]').addEventListener("click",()=>{ box.hidden=true; inp.value=""; });
    inp.addEventListener("keydown",e=>{
      if(e.key==="Enter") submit();
      else if(e.key==="Escape") box.querySelector('[data-a="close-cancel"]').click();
    });
  }
}

function raidNewForm(){
  const box=document.getElementById("raid-form");
  if(box.style.display!=="none"){ box.style.display="none"; return; }
  box.innerHTML='<div class="re-row">'
    +'<label>Type<select id="nr-type">'+RAID_TYPES.map(([v,l])=>'<option value="'+v+'">'+l+'</option>').join("")+'</select></label>'
    +'<label style="flex:1 1 320px;">Title<input id="nr-title" placeholder="What is the risk / issue?"></label>'
    +'<label>Owner<input id="nr-owner" value="Team lead"></label>'
    +'<label>P<select id="nr-p">'+[1,2,3,4,5].map(v=>'<option'+(v===3?" selected":"")+'>'+v+'</option>').join("")+'</select></label>'
    +'<label>I<select id="nr-i">'+[1,2,3,4,5].map(v=>'<option'+(v===3?" selected":"")+'>'+v+'</option>').join("")+'</select></label>'
    +'<label>Due<input id="nr-due" type="text" inputmode="numeric" autocomplete="off" placeholder="dd/mm/yyyy"'
      +' pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}" title="Enter date as dd/mm/yyyy"></label>'
    +'</div>'
    +'<div class="re-row"><label style="flex:1 1 100%;">Description<input id="nr-desc" placeholder="Context, blast radius, who is affected"></label></div>'
    +'<div class="re-act"><button class="btn primary" id="nr-save">Create</button><span class="ro-note" id="nr-msg"></span></div>';
  box.style.display="";
  document.getElementById("nr-save").addEventListener("click",async()=>{
    const m=document.getElementById("nr-msg");
    const title=document.getElementById("nr-title").value.trim();
    const dueInput=document.getElementById("nr-due");
    const due=parsePortalDateInput(dueInput.value);
    if(!title){ m.textContent="Title is required"; return; }
    if(dueInput.value.trim()&&!due){
      dueInput.setCustomValidity("Enter a valid date as dd/mm/yyyy.");
      dueInput.reportValidity();
      m.textContent="Enter a valid due date as dd/mm/yyyy.";
      return;
    }
    dueInput.setCustomValidity("");
    m.textContent="creating\u2026";
    try{
      const res=await fetch("/api/raid-new",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({type:document.getElementById("nr-type").value, title:title,
          fw_owner:document.getElementById("nr-owner").value.trim(),
          p:document.getElementById("nr-p").value, i:document.getElementById("nr-i").value,
          due:due,
          description:document.getElementById("nr-desc").value.trim()})});
      const j=await res.json();
      if(!res.ok||j.error) throw new Error(j.error||"create failed");
      RAID=j.payload; box.style.display="none"; raidOpenRow=j.id;
      renderRaid(); buildSections(); renderOverview();
    }catch(e){ m.textContent="Failed: "+e.message; }
  });
}
document.getElementById("raid-new").addEventListener("click",raidNewForm);
document.getElementById("raid-openonly").addEventListener("change",e=>{
  e.target.closest(".chip").classList.toggle("on",e.target.checked);
  renderRaid();
});

async function refreshData(){
  const btn=document.getElementById("refresh");
  const note=document.getElementById("refresh-note");
  selectedTaskIds.clear();
  taskSelectionMessage="";
  if(btn){ btn.disabled=true; btn.classList.add("spinning"); }
  try{
    if(activeSection==="team" && window.TeamUI){
      await window.TeamUI.refresh();
      if(note) note.textContent="Saved team data refreshed; no external scans";
      return;
    }
    if(activeSection==="settings"){
      await loadSettings(); renderSettings();
      if(note) note.textContent="Settings and timer status reloaded";
      return;
    }
    if(SERVED && activeSection==="ws"){
      const response=await fetch("/api/tasks",{cache:"no-store"});
      const data=await response.json();
      if(!response.ok) throw new Error(data.error||"Could not reload saved workspace data");
      TASKS=data.tasks;
      WORKSPACES=data.workspaces;
      buildSections(); renderWorkspaces();
      if(note) note.textContent="Saved workspace data refreshed; live status not checked";
      return;
    }
    if(SERVED&&activeSection==="overview") await refreshOverviewData();
    else if(SERVED){
      const r=await fetch("/api/tasks",{cache:"no-store"});
      if(r.ok){ const j=await r.json(); TASKS=j.tasks||j; if(j.workspaces) WORKSPACES=j.workspaces; }
      const rb=await fetch("/api/bugs",{cache:"no-store"});
      if(rb.ok) BUGS=await rb.json();
      const ro=await fetch("/api/overview",{cache:"no-store"});
      if(ro.ok) OVERVIEW=await ro.json();
      const rp=await fetch("/api/prs",{cache:"no-store"});
      if(rp.ok) PRS=await rp.json();
      const rr=await fetch("/api/raid",{cache:"no-store"});
      if(rr.ok) RAID=await rr.json();
      // Read-only re-read of the delegation log + jobstate. It never probes a
      // target, so it is as cheap as the others -- but without it the VM
      // section kept rendering whatever was in the page's boot payload, which
      // is why a finished job never stopped looking "running".
      const rv=await fetch("/api/vm-jobs",{cache:"no-store"});
      if(rv.ok) vmAdoptPayload(await rv.json());
    }
    const fstate=captureFilterState();   // preserve active filter selections
    buildFilters();                       // rebuild options (may include new values)
    restoreFilterState(fstate);           // re-apply selections that still exist
    buildBugFilters(); buildBugTabs(); buildSections();
    document.getElementById("total").textContent=
      `${TASKS.length} tasks · ${bugList().filter(b=>b.open).length} open bugs`;
    if(activeSection==="bugs") renderBugs();
    else if(activeSection==="overview") renderOverview();
    else if(activeSection==="ws") renderWorkspaces();
    else if(activeSection==="prs") renderPrs();
    else if(activeSection==="vm") renderVmlog();
    else if(activeSection==="raid") renderRaid();
    else render();
    if(note){
      const now=new Date();
      const hh=String(now.getHours()).padStart(2,"0"), mm=String(now.getMinutes()).padStart(2,"0"), ss=String(now.getSeconds()).padStart(2,"0");
      note.textContent = (SERVED ? "Updated " : "Re-rendered ") + `${hh}:${mm}:${ss}`;
    }
  }catch(e){
    if(note){ note.textContent="Refresh failed: "+e.message; }
  }finally{
    if(btn){ btn.disabled=false; btn.classList.remove("spinning"); }
  }
}
document.getElementById("refresh").addEventListener("click",refreshData);

// ---- notifications ------------------------------------------------------
// One poll of /api/events every few seconds: new events become an in-page card
// (and a desktop notification when the tab is hidden and permission is
// granted). The poll doubles as the "a tab is open" heartbeat; with no tab the
// server raises a Windows toast instead. The live ssh link reports a change
// stamp here too, which re-reads the Machines section without a manual refresh.
const NOTIFY={seq:null,timer:null,liveChanged:0,max:4};
const NOTIFY_POLL_MS=5000;
function notifyCard(ev){
  let stack=document.getElementById("notify-stack");
  if(!stack){
    stack=document.createElement("div"); stack.id="notify-stack";
    stack.setAttribute("role","status"); stack.setAttribute("aria-live","polite");
    document.body.appendChild(stack);
  }
  const card=document.createElement("div");
  card.className="nt "+(ev.tone||"");
  card.innerHTML="<b>"+esc(ev.title)+"</b>"+(ev.body?"<span>"+esc(ev.body)+"</span>":"");
  card.title="Click to open \u00b7 dismisses in 12 s";
  card.addEventListener("click",()=>{ if(ev.link) location.hash=ev.link; card.remove(); });
  stack.appendChild(card);
  while(stack.children.length>NOTIFY.max) stack.firstChild.remove();
  setTimeout(()=>card.remove(),12000);
}
function notifyDesktop(ev){
  if(!("Notification" in window)||Notification.permission!=="granted") return false;
  try{
    const n=new Notification(ev.title,{body:ev.body||"",tag:"portal-"+ev.seq});
    n.onclick=()=>{ window.focus(); if(ev.link) location.hash=ev.link; n.close(); };
    return true;
  }catch(e){ return false; }
}
function notifyShow(ev){
  if(document.hidden&&notifyDesktop(ev)) return;
  notifyCard(ev);
}
async function notifyPoll(){
  if(!SERVED) return;
  try{
    const r=await fetch("/api/events?since="+(NOTIFY.seq??0),{cache:"no-store"});
    if(!r.ok) return;
    const j=await r.json();
    // First poll only learns the cursor: no replay of events from before load.
    if(NOTIFY.seq!==null) (j.events||[]).forEach(notifyShow);
    if((NOTIFY.seq!==null&&(j.events||[]).some(ev=>ev.kind==="ai"))||Date.now()-(NOTIFY.rcAt||0)>60000){
      NOTIFY.rcAt=Date.now(); rcLoadOpen();
    }
    NOTIFY.seq=j.seq||0;
    const changed=(j.live&&j.live.changed)||0;
    if(changed>NOTIFY.liveChanged){
      const first=!NOTIFY.liveChanged;
      NOTIFY.liveChanged=changed;
      if(!first&&activeSection==="vm") vmLivePoll();
    }
    if(j.ai){ AI_USAGE=j.ai; aiSyncButton(); }
  }catch(e){ /* the next tick retries */ }
}
function notifyStart(){
  if(NOTIFY.timer) return;
  notifyPoll();
  NOTIFY.timer=setInterval(notifyPoll,NOTIFY_POLL_MS);
}
let AI_USAGE={};

/* ---- AI drawer: Explain this view / Ask / follow-ups -----------------------
   Every call is a queued server action (copilot -p); the drawer only posts the
   request and polls the action + run record. Answers arrive as HTML rendered
   server-side by markdown-it with raw HTML disabled. */
const AI_SECTION_IDS=["overview","work","ws","bugs","prs","vm","raid","team"];
const AI={open:false, thread:[], busy:false, history:[], section:null, devops:false};
function aiSec(){ return AI.devops?"devops":activeSection; }
function aiHeaders(){
  const h={"Content-Type":"application/json"};
  if(activeSection==="team"&&window.TeamUI&&window.TeamUI.isPrivate&&window.TeamUI.isPrivate()) h["X-Team-Private"]="1";
  return h;
}
function aiUsageLabel(){
  const u=AI_USAGE||{};
  if(u.enabled===false) return "AI is off (Settings → AI)";
  if(u.cap==null) return "";
  return `${u.used||0}/${u.cap} runs today${u.premium?` · ${u.premium} premium req.`:""} · ${u.model||""}`;
}
function aiFocus(){
  const sec=document.getElementById("sec-"+activeSection);
  if(!sec) return "";
  const bits=[];
  sec.querySelectorAll(".tab.on,[role=tab][aria-selected=true]").forEach(el=>bits.push("tab: "+el.textContent.trim()));
  sec.querySelectorAll("input[type=search]").forEach(el=>{ if(el.value) bits.push("search: "+el.value); });
  return bits.join("; ").slice(0,500);
}
function aiEnsureDrawer(){
  let d=document.getElementById("ai-drawer");
  if(d) return d;
  d=document.createElement("aside");
  d.id="ai-drawer"; d.className="ai-drawer"; d.hidden=true;
  d.setAttribute("role","dialog"); d.setAttribute("aria-label","AI assistant");
  d.innerHTML=`<div class="ai-head"><b>✦ AI · <span id="ai-sec"></span></b>
      <small id="ai-usage"></small>
      <button type="button" class="ab-close" id="ai-pop" title="Pop out this conversation into a window" aria-label="Pop out into a window">⧉</button>
      <button type="button" class="ab-close" id="ai-close" aria-label="Close AI panel">&times;</button></div>
    <div class="ai-actions"><button type="button" class="btn primary" id="ai-explain">Explain this view</button>
      <button type="button" class="btn" id="ai-new">New conversation</button>
      <label class="ai-devops" title="Answer CI/CD, octl, Grafana and DevOps questions using the devops-expert knowledge"><input type="checkbox" id="ai-devops"> DevOps mode</label></div>
    <div class="ai-thread" id="ai-thread" aria-live="polite"></div>
    <form class="ai-ask" id="ai-ask"><textarea id="ai-q" rows="3" placeholder="Ask about this view… (Ctrl+Enter to send)"></textarea>
      <button type="submit" class="btn primary" id="ai-send">Ask</button></form>
    <details class="ai-history"><summary>Recent in this section</summary><ul id="ai-hist"></ul></details>
    <p class="ai-foot">Read-only: the assistant sees a snapshot of this view and may read Jira/GitHub if allowed in Settings. It cannot change anything.</p>`;
  document.body.appendChild(d);
  d.querySelector("#ai-close").addEventListener("click",aiClose);
  d.querySelector("#ai-pop").addEventListener("click",()=>{
    const last=[...AI.thread].reverse().find(r=>r.id);
    if(!last) return;
    const priv=aiHeaders()["X-Team-Private"]==="1"?"&private=1":"";
    if(PortalAIChat.popOut("run="+encodeURIComponent(last.id)+priv,"aithread-"+last.id)) aiClose();
  });
  d.querySelector("#ai-explain").addEventListener("click",()=>aiSubmit("explain"));
  d.querySelector("#ai-new").addEventListener("click",()=>{ AI.thread=[]; aiRenderThread(); });
  d.querySelector("#ai-devops").addEventListener("change",e=>{ AI.devops=e.target.checked; aiOpen(); });
  d.querySelector("#ai-ask").addEventListener("submit",e=>{ e.preventDefault(); aiSubmit("ask"); });
  d.querySelector("#ai-q").addEventListener("keydown",e=>{
    if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); aiSubmit("ask"); } });
  d.addEventListener("keydown",e=>{ if(e.key==="Escape") aiClose(); });
  d.querySelector("#ai-hist").addEventListener("click",e=>{
    const a=e.target.closest("[data-run]"); if(!a) return;
    e.preventDefault(); aiLoadRun(a.dataset.run);
  });
  return d;
}
function aiSyncButton(){
  const b=document.getElementById("ai-open");
  if(!b) return;
  b.hidden=!SERVED||!AI_SECTION_IDS.includes(activeSection);
  b.disabled=AI_USAGE.enabled===false;
  b.title=AI_USAGE.enabled===false?"AI is turned off in Settings":"Explain this view or ask a question (AI)";
  if(AI.open&&AI.section!==aiSec()) aiOpen();
}
function aiOpen(){
  const d=aiEnsureDrawer();
  if(AI.section!==aiSec()){ AI.thread=[]; AI.section=aiSec(); }
  AI.open=true; d.hidden=false;
  d.querySelector("#ai-devops").checked=AI.devops;
  const label=AI.devops?"DevOps":((SECTIONS.find(s=>s.id===activeSection)||{}).label||activeSection);
  d.querySelector("#ai-sec").textContent=label;
  d.querySelector("#ai-usage").textContent=aiUsageLabel();
  aiRenderThread(); aiLoadHistory();
  d.querySelector("#ai-explain").focus();
}
function aiClose(){
  AI.open=false;
  const d=document.getElementById("ai-drawer"); if(d) d.hidden=true;
  const b=document.getElementById("ai-open"); if(b&&!b.hidden) b.focus();
}
function aiRunHtml(r){
  const q=r.kind==="explain"?`Explain this view`:r.question;
  const meta=[r.model, r.premium?`${r.premium} premium`:"", r.truncated?"partial data":"",
              r.finished?formatPortalDate(r.finished):""].filter(Boolean).join(" · ");
  const body=r.status==="done"?`<div class="ai-answer">${r.answer_html||esc(r.answer||"")}</div>`
    :r.status==="error"?`<div class="ai-err">${esc(r.error||"failed")}</div>`
    :`<div class="ai-wait"><span class="aq-spin" aria-hidden="true"></span> ${esc(r.progress||"thinking…")}</div>`;
  return `<div class="ai-turn"><div class="ai-q">${esc(q||"")}</div>${body}${meta?`<small class="ai-meta">${esc(meta)}</small>`:""}</div>`;
}
function aiRenderThread(){
  const el=document.getElementById("ai-thread"); if(!el) return;
  el.innerHTML=AI.thread.length?AI.thread.map(aiRunHtml).join("")
    :`<p class="muted">Ask a question about this view, or let the AI explain what stands out.</p>`;
  el.scrollTop=el.scrollHeight;
  const busy=AI.busy||AI_USAGE.enabled===false;
  ["ai-explain","ai-send"].forEach(id=>{ const b=document.getElementById(id); if(b) b.disabled=busy; });
  const pop=document.getElementById("ai-pop");
  if(pop) pop.disabled=!AI.thread.some(r=>r.id);
}
async function aiLoadHistory(){
  const ul=document.getElementById("ai-hist"); if(!ul) return;
  try{
    const r=await fetch("/api/ai/runs?section="+encodeURIComponent(aiSec()),{cache:"no-store",headers:aiHeaders()});
    const j=await r.json();
    if(j.usage){ AI_USAGE=j.usage; const u=document.getElementById("ai-usage"); if(u) u.textContent=aiUsageLabel(); }
    ul.innerHTML=(j.runs||[]).filter(x=>!["auto-review","review-turn"].includes(x.kind)).map(x=>
      `<li><a href="#" data-run="${esc(x.id)}">${esc(x.kind==="explain"?"Explain":x.question||x.kind)}</a> <small>${esc(formatPortalDate(x.started||""))} · ${esc(x.status)}</small></li>`).join("")
      ||"<li class='muted'>No AI runs yet.</li>";
  }catch(e){ ul.innerHTML=`<li class="muted">History unavailable</li>`; }
}
async function aiShowRun(id){
  try{
    const r=await fetch("/api/ai/run/"+encodeURIComponent(id),{cache:"no-store",headers:aiHeaders()});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||r.status);
    AI.devops=j.section==="devops";
    if(j.section&&j.section!==activeSection&&AI_SECTION_IDS.includes(j.section)) selectSection(j.section);
    AI.section=aiSec(); aiOpen(); AI.thread=[j]; aiRenderThread();
  }catch(e){ notifyCard({title:"AI",body:String(e.message||e),tone:"bad"}); }
}
async function aiLoadRun(id){
  try{
    const r=await fetch("/api/ai/run/"+encodeURIComponent(id),{cache:"no-store",headers:aiHeaders()});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||r.status);
    AI.thread=[j]; aiRenderThread();
  }catch(e){ notifyCard({title:"AI",body:String(e.message||e),tone:"bad"}); }
}
async function aiSubmit(kind){
  if(AI.busy) return;
  const q=document.getElementById("ai-q");
  const question=kind==="ask"?q.value.trim():"";
  if(kind==="ask"&&!question){ q.focus(); return; }
  const last=AI.thread[AI.thread.length-1];
  const body={kind, section:aiSec(), question, focus:aiFocus()};
  if(kind==="ask"&&last&&last.status==="done"&&last.session_id) body.parent=last.id;
  AI.busy=true;
  const turn={id:null,kind,question,status:"queued",progress:"queued"};
  AI.thread.push(turn); aiRenderThread();
  try{
    const r=await fetch("/api/ai/run",{method:"POST",headers:aiHeaders(),body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    if(kind==="ask") q.value="";
    turn.id=j.run_id;
    await aiWait(j.job,j.run_id,turn);
  }catch(e){
    Object.assign(turn,{status:"error",error:String(e.message||e)});
  }
  AI.busy=false; aiRenderThread(); aiLoadHistory();
}
async function aiWait(jobId,runId,turn){
  for(;;){
    await new Promise(res=>setTimeout(res,2000));
    let job={};
    try{ job=await (await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"})).json(); }catch(e){}
    if(job.current){ turn.progress=job.current; aiRenderThread(); }
    if(["done","error","cancelled","approval_required"].includes(job.status)||job.error==="unknown job") break;
  }
  const r=await fetch("/api/ai/run/"+encodeURIComponent(runId),{cache:"no-store",headers:aiHeaders()});
  const run=await r.json();
  Object.assign(turn,r.ok?run:{status:"error",error:run.error||"answer unavailable"});
}
// ---- AI review: draft / re-review / interactive ----
// REVIEW_CHATS maps task id -> its open interactive review (server state).
async function rcLoadOpen(){
  if(!SERVED) return;
  try{
    const r=await fetch("/api/ai/review-chats?open=1",{cache:"no-store"});
    if(!r.ok) return;
    const next={};
    ((await r.json()).chats||[]).forEach(c=>{ if(!next[c.task_id]) next[c.task_id]=c; });
    const changed=JSON.stringify(next)!==JSON.stringify(REVIEW_CHATS);
    REVIEW_CHATS=next;
    if(changed&&typeof render==="function") render();
  }catch(e){ /* next refresh retries */ }
}
const RC={handle:null,id:null};
function rcClose(){
  if(RC.handle) RC.handle.stop();
  RC.handle=null; RC.id=null;
  const d=document.getElementById("rc-drawer"); if(d) d.hidden=true;
}
function rcOpen(chatId){
  if(AI.open) aiClose();
  let d=document.getElementById("rc-drawer");
  if(!d){
    d=document.createElement("aside"); d.id="rc-drawer"; d.className="ai-drawer rc-drawer";
    d.setAttribute("role","dialog"); d.setAttribute("aria-label","Interactive review");
    d.addEventListener("keydown",e=>{ if(e.key==="Escape") rcClose(); });
    document.body.appendChild(d);
  }
  if(RC.id===chatId&&!d.hidden){ RC.handle.refresh(); return; }
  if(RC.handle) RC.handle.stop();
  d.hidden=false; d.innerHTML="";
  RC.id=chatId;
  RC.handle=PortalAIChat.mountReview(d,chatId,{onClose:rcClose,onPopped:rcClose,onEnded:()=>setTimeout(rcLoadOpen,1500)});
}
async function aiReviewAction(mode,tid,btn){
  if(mode==="resume"){ rcOpen(btn.dataset.chat); return; }
  const task=(typeof TASKS!=="undefined"?TASKS:[]).find(x=>x.id===tid);
  const hasDraft=!!(task&&taskHasDraft(task));
  const text={
    create:`Create an AI review draft for ${tid}?\n\nThe portal picks a free review workspace whose deployed manifest (project, revision) matches the PRs, aligns it to the PR branches, runs /pr-review local (draft only - nothing is posted), registers the draft on the task and releases the workspace.`,
    rereview:`Re-review ${tid} with AI?\n\nSame workspace handling as a draft; the AI follows the pr-review re-review flow (only what changed since the reviewed head, still-open findings carried forward) and registers a new draft. If nothing changed, the draft stays as is. Nothing is posted.`,
    online:`${hasDraft?"Online re-review":"Online review"} of ${tid}?\n\nNo workspace is used: the AI reads the PRs from GitHub (read-only) and reviews them as one feature set - the DEPENDS_ON graph and cross-PR consistency first, then each PR${hasDraft?", only what changed since the last draft":""}. It writes one report, which the portal registers on the task. Related PRs missing from the task are proposed in a task note. RTL PRs are refused. Nothing is posted.`,
    interactive:`Start an interactive review of ${tid}?\n\nThe portal occupies a matching review workspace and checks the PRs out, then the AI runs /pr-review local with you in a chat panel. The workspace stays occupied until you end the review (or after 24 h without messages).`}[mode];
  if(!text||!confirm(text)) return;
  btn.disabled=true;
  try{
    const url=mode==="interactive"?"/api/ai/review-chat":"/api/ai/auto-review";
    const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(mode==="interactive"?{task_id:tid}
        :mode==="online"?{task_id:tid,mode:hasDraft?"rereview":"create",online:true}
        :{task_id:tid,mode})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    if(mode==="interactive"){
      await rcLoadOpen(); rcOpen(j.chat_id);
    }else if(mode==="online"){
      notifyCard({title:`Online ${hasDraft?"re-review":"review"} ${tid} queued`,body:"No workspace needed. You'll be notified when the draft is registered.",tone:"ok"});
      if(typeof pollAlign==="function") pollAlign(j.job);
      if(typeof refreshActionQueue==="function") refreshActionQueue();
    }else{
      notifyCard({title:`${mode==="create"?"Review draft":"Re-review"} ${tid} queued`,body:`Workspace ${j.workspace} (${j.manifest||""}). You'll be notified when it is done.`,tone:"ok"});
      if(typeof pollAlign==="function") pollAlign(j.job);
    }
  }catch(e){
    notifyCard({title:`AI review of ${tid} not started`,body:String(e.message||e),tone:"bad"});
  }
  btn.disabled=false;
}
document.addEventListener("click",e=>{
  const b=e.target.closest("[data-ai-review]"); if(!b) return;
  e.preventDefault(); e.stopPropagation();
  aiReviewAction(b.dataset.aiReview,b.dataset.task,b);
},true);  // capture: the buttons sit inside clickable task rows

// ---- served-mode detection: enables editing + live data ----
async function init(){
  try{
    const h=await fetch("/health",{cache:"no-store"});
    if(h.ok){
      SERVED=true;
      const badge=document.getElementById("modebadge");
      badge.textContent="editable"; badge.className="mode edit";
      const r=await fetch("/api/tasks",{cache:"no-store"});
      if(r.ok){ const j=await r.json(); TASKS=j.tasks||j; if(j.workspaces) WORKSPACES=j.workspaces; }
      const rb=await fetch("/api/bugs",{cache:"no-store"});
      if(rb.ok) BUGS=await rb.json();
      const ro=await fetch("/api/overview",{cache:"no-store"});
      if(ro.ok) OVERVIEW=await ro.json();
      const rp=await fetch("/api/prs",{cache:"no-store"});
      if(rp.ok) PRS=await rp.json();
      const rr=await fetch("/api/raid",{cache:"no-store"});
      if(rr.ok) RAID=await rr.json();
      notifyStart();
      const aiBtn=document.getElementById("ai-open");
      if(aiBtn){ aiBtn.addEventListener("click",()=>AI.open?aiClose():aiOpen()); aiSyncButton(); }
      rcLoadOpen();
    }
  }catch(e){ /* file:// — stay read-only with embedded data */ }
  document.getElementById("total").textContent=
    `${TASKS.length} tasks · ${bugList().filter(b=>b.open).length} open bugs`;
  buildFilters();
  buildColumns();
  render();
  buildBugFilters();
  buildBugTabs();
  buildSections();
  initOverviewTiles();
  const shell=document.getElementById("portal-shell");
  const collapse=document.getElementById("nav-collapse");
  if(shell && collapse){
    shell.classList.toggle("sidebar-collapsed",portalLayout.sidebarCollapsed);
    collapse.setAttribute("aria-label",portalLayout.sidebarCollapsed?"Expand navigation":"Collapse navigation");
    const lbl=collapse.querySelector(".collapse-label");
    if(lbl) lbl.textContent=portalLayout.sidebarCollapsed?"Expand navigation":"Collapse navigation";
    collapse.addEventListener("click",()=>{
      const collapsed=shell.classList.toggle("sidebar-collapsed");
      collapse.setAttribute("aria-label",collapsed?"Expand navigation":"Collapse navigation");
      const nextLbl=collapse.querySelector(".collapse-label");
      if(nextLbl) nextLbl.textContent=collapsed?"Expand navigation":"Collapse navigation";
      savePortalLayout({sidebarCollapsed:collapsed});
    });
  }
  const updateClock=()=>{
    const clock=document.getElementById("local-clock");
    if(clock){
      clock.textContent=
        new Intl.DateTimeFormat(undefined,{hour:"2-digit",minute:"2-digit",
          ...(prefs.clock==="auto"?{}:{hour12:prefs.clock==="12"})}).format(new Date());
    }
  };
  updateClock();
  window.__updateClock=updateClock;
  document.body.classList.toggle("compact-tables",prefs.compact);
  setInterval(updateClock,30000);
  renderOverview();
  renderPrs();
  renderRaid();
  if(!applyHash()) selectSection(activeSection);
  const ck=document.getElementById("cmdk-open"); if(ck) ck.addEventListener("click",openCmdk);
  if(SERVED){
    refreshActionQueue();
    restartActionPoll();
    restartPulsePoll();
    pulseTick(true);
  }
}

// --- routine days (pure: scheduleDaysLabel; tests cut from here) ---
const WEEK_DAYS=["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const DAY_PRESETS=[["Every day",WEEK_DAYS],["Sun–Thu",["Sun","Mon","Tue","Wed","Thu"]],
  ["Mon–Fri",["Mon","Tue","Wed","Thu","Fri"]]];
function scheduleDaysLabel(days){
  const set=WEEK_DAYS.filter(d=>(days||[]).includes(d));
  const preset=DAY_PRESETS.find(([,ds])=>ds.length===set.length&&ds.every(d=>set.includes(d)));
  if(preset) return preset[0]==="Every day"?"daily":preset[0];
  return set.length?set.join(", "):"no days";
}
// --- end routine days ---

// ---- Settings section ----
// Portal settings (server, journaled, revision-checked) + browser preferences.
let actionPollTimer=null;
function restartActionPoll(){
  if(!SERVED) return;
  if(actionPollTimer) clearInterval(actionPollTimer);
  actionPollTimer=setInterval(refreshActionQueue,prefs.actionPollMs);
}
let SETTINGS=BOOT.settings||null;
let settingsMsg={text:"",tone:""};
const ROUTINE_STEP_LABELS={reviews:"Reviews",prs:"PRs (no Jira sync)",bugs:"Bugs",
  workspaces:"Local workspaces",team:"Team activity",ai:"AI summary in the brief"};
// Generic forms for the flat setting groups. [key, label, hint]; the type
// comes from the default value (bool / int / list of machine names / text).
const SETTING_GROUPS=[
  ["ai","AI assistance",[
    ["enabled","Allow AI runs","Explain/Ask, the brief paragraph, review drafts and interactive reviews. Off blocks every AI call."],
    ["model","Model","Copilot CLI --model; \u201cfusion\u201d lets Copilot pick per request."],
    ["daily_cap","Runs per day","hard cap for Explain/Ask/brief and review drafts; 0 disables"],
    ["interactive_daily_cap","Interactive review messages per day","separate cap for interactive-review chat turns; 0 disables"],
    ["timeout_s","Run timeout","seconds, Explain/Ask/brief"],
    ["review_timeout_s","Review timeout","seconds per review draft run or interactive-review turn"],
    ["allow_jira_github_reads","Allow Jira / GitHub reads","read-only lookups during AI runs; the only GitHub write is posting an interactive review you confirmed"]]],
  ["machines","Machines",[
    ["live_targets","Live-status machines","comma-separated targets kept on a persistent ssh status link"],
    ["live_interval_s","Live update interval","seconds between probes on the live link"]]],
  ["notify","Notifications",[
    ["enabled","Pop-up notifications","master switch"],
    ["queue","Action queue","an action finished, failed or needs approval"],
    ["machine_jobs","Machine jobs","a build/test on a machine finished"],
    ["ai","AI runs","an AI answer, review draft or interactive-review start/submit/end"],
    ["reachability","Machine reachability","a live-status machine went down or came back"],
    ["windows_toast","Windows toast when no tab is open","via powershell.exe from WSL"]]]];
function settingField(group,[key,label,hint],values,dis){
  const v=values[key], def=((SETTINGS.defaults||{})[group]||{})[key];
  const id=`sg-${group}-${key}`;
  if(typeof def==="boolean")
    return `<label class="set-row"><input type="checkbox" id="${id}" data-g="${group}" data-k="${key}"${v?" checked":""}${dis}>
      <span>${esc(label)}${hint?`<small>${esc(hint)}</small>`:""}</span></label>`;
  if(typeof def==="number"){
    const [lo,hi]=((SETTINGS.group_limits||{})[group]||{})[key]||[0,9999];
    return `<label class="set-row"><span class="set-lbl">${esc(label)}</span>
      <input type="number" id="${id}" data-g="${group}" data-k="${key}" min="${lo}" max="${hi}" step="1" value="${esc(v)}" required${dis}>
      <small>${esc(hint)} (${lo}\u2013${hi})</small></label>`;
  }
  const list=Array.isArray(def);
  return `<label class="set-row"><span class="set-lbl">${esc(label)}</span>
    <input type="text" id="${id}" data-g="${group}" data-k="${key}"${list?' data-list="1"':""}
      value="${esc(list?(v||[]).join(", "):v)}"${list?"":" required"}${dis}>
    <small>${esc(hint)}</small></label>`;
}
let groupsMsg={text:"",tone:""};
function renderGroupSettings(values){
  const box=document.getElementById("set-groups"); if(!box) return;
  if(!SETTINGS||!SETTINGS.settings||!SETTINGS.settings.ai){ box.innerHTML='<div class="empty">Loading\u2026</div>'; return; }
  const dis=SERVED?"":" disabled", src=values||SETTINGS.settings;
  const perm=("Notification" in window)?Notification.permission:"unsupported";
  box.innerHTML=`<form id="set-groups-form" class="set-form">
    <div class="set-groups">${SETTING_GROUPS.map(([g,title,fields])=>`<fieldset class="set-steps"><legend>${esc(title)}</legend>
      ${fields.map(f=>settingField(g,f,src[g]||{},dis)).join("")}</fieldset>`).join("")}</div>
    <div class="set-row"><span class="set-lbl">Desktop notifications in this browser</span>
      <button type="button" class="btn" id="notify-perm"${perm==="granted"||perm==="unsupported"?" disabled":""}>${
        perm==="granted"?"\u2713 Allowed":perm==="denied"?"Blocked \u2014 allow in the browser\u2019s site settings":perm==="unsupported"?"Not supported":"Allow desktop notifications"}</button>
      <button type="button" class="btn" id="notify-test">Show a test notification</button>
      <small>Without permission, notifications appear as cards in the corner of the portal.</small></div>
    <div class="set-actions">
      <button type="submit" class="primary"${dis}>Save</button>
      <button type="button" class="btn" data-groups-defaults${dis}>Fill in defaults</button>
      <span class="set-msg ${esc(groupsMsg.tone)}" role="status" aria-live="polite">${esc(groupsMsg.text)}</span>
    </div></form>`;
  const form=document.getElementById("set-groups-form");
  form.addEventListener("submit",ev=>{ ev.preventDefault(); saveGroupSettings(form); });
  form.querySelector("[data-groups-defaults]").addEventListener("click",()=>{
    groupsMsg={text:"Defaults filled in \u2014 Save to apply.",tone:""};
    renderGroupSettings(SETTINGS.defaults);
  });
  document.getElementById("notify-perm").addEventListener("click",async()=>{
    try{ await Notification.requestPermission(); }catch(e){ /* older API */ }
    renderGroupSettings();
  });
  document.getElementById("notify-test").addEventListener("click",()=>{
    const ev={seq:Date.now(),title:"Team Portal notification test",body:"This is how a finished job is announced.",tone:"ok",link:"#settings"};
    if(!notifyDesktop(ev)) notifyCard(ev);
  });
}
function groupFormValues(form){
  const out={};
  form.querySelectorAll("[data-g]").forEach(el=>{
    const g=el.dataset.g, k=el.dataset.k; out[g]=out[g]||{};
    out[g][k]=el.type==="checkbox"?el.checked:el.type==="number"?Number(el.value)
      :el.dataset.list?el.value.split(/[\s,]+/).filter(Boolean):el.value.trim();
  });
  return out;
}
async function saveGroupSettings(form){
  if(!form.reportValidity()) return;
  const btn=form.querySelector("button[type=submit]"); btn.disabled=true;
  form.querySelector(".set-msg").textContent="Saving\u2026";
  try{
    const r=await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({revision:SETTINGS.revision,settings:groupFormValues(form)})});
    const data=await r.json().catch(()=>({}));
    if(!r.ok){
      groupsMsg={text:(r.status===409?"Someone else changed settings \u2014 reloaded. ":"")+(data.error||"HTTP "+r.status),tone:"bad"};
      if(r.status===409) await loadSettings();
    }else{
      SETTINGS={settings:data.settings,defaults:data.defaults,revision:data.revision,limits:data.limits,
        group_limits:data.group_limits,status:data.status};
      BOOT.settings=SETTINGS;
      groupsMsg={text:data.changed.length?`Saved ${data.changed.length} change(s)`:"No changes",tone:data.changed.length?"ok":""};
    }
  }catch(error){ groupsMsg={text:"Save failed: "+error.message,tone:"bad"}; }
  renderGroupSettings(); renderRoutineSettings();
}
const ROUTINE_NUMBERS=[
  ["snapshot_keep","Daily DB snapshots kept","days"],
  ["queue_retention_days","Keep finished queue actions","days"],
  ["activity_window_hours","Team activity window","hours"],
  ["brief_stale_hours","Mark the brief stale after","hours"]];
async function loadSettings(){
  if(!SERVED) return;
  try{
    const r=await fetch("/api/settings",{cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    SETTINGS=await r.json();
  }catch(error){ settingsMsg={text:"Could not load settings: "+error.message,tone:"bad"}; }
}
function renderSettings(){
  renderRoutineSettings(); renderGroupSettings(); renderSettingsStatus(); renderPrefs();
  if(SERVED&&!(SETTINGS&&SETTINGS.status)){
    loadSettings().then(()=>{ renderRoutineSettings(); renderGroupSettings(); renderSettingsStatus(); });
  }
}
function renderRoutineSettings(values){
  const box=document.getElementById("set-routine"); if(!box) return;
  if(!SETTINGS){ box.innerHTML='<div class="empty">Loading…</div>'; return; }
  const r=values||SETTINGS.settings.routine, lim=SETTINGS.limits||{}, dis=SERVED?"":" disabled";
  const chk=(k,label,hint)=>`<label class="set-row"><input type="checkbox" data-r="${k}"${r[k]?" checked":""}${dis}>
    <span>${esc(label)}${hint?`<small>${esc(hint)}</small>`:""}</span></label>`;
  box.innerHTML=(SERVED?"":`<div class="ro-note">Read-only. Open the served portal to change portal settings.</div>`)
    +`<form id="set-routine-form" class="set-form">
    ${chk("enabled","Run the daily routine","Off disables the systemd timer; you can still run it by hand.")}
    <label class="set-row"><span class="set-lbl">Run time</span>
      <input type="time" step="60" data-r="time" value="${esc(r.time)}" required${dis}>
      <small>local time · the timer may add up to 2 min random delay</small></label>
    <fieldset class="set-steps set-days"><legend>Run on</legend>
      ${WEEK_DAYS.map(d=>`<label><input type="checkbox" data-day="${d}"${(r.days||[]).includes(d)?" checked":""}${dis}> ${d}</label>`).join("")}
      <span class="set-presets">${DAY_PRESETS.map(([l,ds])=>`<button type="button" class="btn" data-days="${ds.join(",")}"${dis}>${esc(l)}</button>`).join("")}</span>
      <small>Unchecked days (e.g. Fri–Sat) are skipped; check them to run on weekends too.</small></fieldset>
    ${chk("auto_restart","Restart the portal automatically if unhealthy","only when no action is queued or running")}
    <fieldset class="set-steps"><legend>Refresh steps</legend>
      ${Object.entries(ROUTINE_STEP_LABELS).map(([k,l])=>`<label><input type="checkbox" data-step="${k}"${r.steps[k]?" checked":""}${dis}> ${esc(l)}</label>`).join("")}
      <small>Health, queue, integrity, housekeeping and self-test always run.</small></fieldset>
    <div class="set-nums">${ROUTINE_NUMBERS.map(([k,l,u])=>{ const [lo,hi]=lim[k]||[1,999];
      return `<label class="set-row"><span class="set-lbl">${esc(l)}</span>
        <input type="number" data-r="${k}" min="${lo}" max="${hi}" step="1" value="${esc(r[k])}" required${dis}>
        <small>${esc(u)} (${lo}–${hi})</small></label>`; }).join("")}</div>
    <div class="set-actions">
      <button type="submit" class="primary"${dis}>Save portal settings</button>
      <button type="button" class="btn" data-set-defaults${dis}>Fill in defaults</button>
      <span class="set-msg ${esc(settingsMsg.tone)}" role="status" aria-live="polite">${esc(settingsMsg.text)}</span>
    </div></form>`;
  const form=document.getElementById("set-routine-form");
  form.addEventListener("submit",ev=>{ ev.preventDefault(); saveRoutineSettings(form); });
  form.querySelectorAll("[data-days]").forEach(b=>b.addEventListener("click",()=>{
    const want=b.dataset.days.split(",");
    form.querySelectorAll("[data-day]").forEach(el=>{ el.checked=want.includes(el.dataset.day); });
  }));
  form.querySelector("[data-set-defaults]").addEventListener("click",()=>{
    settingsMsg={text:"Defaults filled in — Save to apply.",tone:""};
    renderRoutineSettings(SETTINGS.defaults.routine);
  });
}
function routineFormValues(form){
  const out={steps:{},days:[]};
  form.querySelectorAll("[data-r]").forEach(el=>{
    out[el.dataset.r]=el.type==="checkbox"?el.checked:el.type==="number"?Number(el.value):el.value;
  });
  form.querySelectorAll("[data-step]").forEach(el=>{ out.steps[el.dataset.step]=el.checked; });
  form.querySelectorAll("[data-day]").forEach(el=>{ if(el.checked) out.days.push(el.dataset.day); });
  return out;
}
async function saveRoutineSettings(form){
  if(!form.reportValidity()) return;
  if(!form.querySelector("[data-day]:checked")){
    settingsMsg={text:"Pick at least one run day, or turn the routine off.",tone:"bad"};
    form.querySelector(".set-msg").textContent=settingsMsg.text;
    form.querySelector(".set-msg").className="set-msg bad";
    return;
  }
  const btn=form.querySelector("button[type=submit]"); btn.disabled=true;
  settingsMsg={text:"Saving…",tone:""};
  form.querySelector(".set-msg").textContent=settingsMsg.text;
  try{
    const r=await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({revision:SETTINGS.revision,settings:{routine:routineFormValues(form)}})});
    const data=await r.json().catch(()=>({}));
    if(!r.ok){
      settingsMsg={text:(r.status===409?"Someone else changed settings — reloaded. ":"")+(data.error||"HTTP "+r.status),tone:"bad"};
      if(r.status===409) await loadSettings();
    }else{
      SETTINGS={settings:data.settings,defaults:data.defaults,revision:data.revision,limits:data.limits,
        group_limits:data.group_limits,status:data.status};
      BOOT.settings=SETTINGS;
      settingsMsg={text:data.changed.length?`Saved ${data.changed.length} change(s) · ${data.applied}`:"No changes",tone:data.changed.length?"ok":""};
      if(typeof renderBrief==="function") renderBrief();
    }
  }catch(error){ settingsMsg={text:"Save failed: "+error.message,tone:"bad"}; }
  renderRoutineSettings(); renderSettingsStatus();
}
function renderSettingsStatus(){
  const box=document.getElementById("set-status"); if(!box) return;
  if(!SERVED){ box.innerHTML='<div class="empty">Available on the served portal.</div>'; return; }
  const st=SETTINGS&&SETTINGS.status;
  if(!st){ box.innerHTML='<div class="empty">Loading…</div>'; return; }
  const t=st.timer||{}, last=st.last_run, cfg=SETTINGS.settings.routine;
  const row=(k,v,tone)=>`<div class="set-kv"><span>${esc(k)}</span><b class="${tone||""}">${v}</b></div>`;
  const when=last&&last.generated_at?localDmyHm(Date.parse(last.generated_at)):"";
  box.innerHTML=
    (st.problem?`<div class="brief-error">${esc(st.problem)}</div>`:"")
    +(t.error?row("Timer",esc(t.error),"bad")
      :row("Timer",esc(t.active)+" · "+esc(t.enabled),t.active==="active"?"ok":"warn")
       +row("Next run",esc(t.next||"—"))
       +row("Last trigger",esc(t.last&&t.last!=="n/a"?t.last:"—")))
    +row("Schedule",esc(scheduleDaysLabel(cfg.days)+" "+cfg.time)+(st.managed_schedule?"":" <small>(unit default)</small>"))
    +row("Last brief",last?esc(when+" · "+(last.status||"?"))+(last.journal_id?" · journal #"+esc(last.journal_id):""):"none yet",
         last&&last.status==="ok"?"ok":last?"warn":"")
    +row("Linger",esc(st.linger),st.linger==="yes"?"ok":"warn")
    +(st.linger==="yes"?"":`<p class="cap">With linger off the timer fires only while you are logged in; a missed run catches up at next login. Enable with <code>sudo loginctl enable-linger $USER</code>.</p>`)
    +`<div class="set-actions"><button type="button" class="btn" id="set-status-refresh">↻ Refresh status</button></div>`;
  document.getElementById("set-status-refresh").addEventListener("click",async()=>{
    await loadSettings(); renderSettingsStatus();
  });
}
function renderPrefs(){
  const box=document.getElementById("set-prefs"); if(!box) return;
  const order=navSections(SECTIONS,{...prefs,hidden:[]});
  const cards=pulseVisible(PortalPulse.cards({},Date.now()),prefs,true);
  const opt=(v,l,cur)=>`<option value="${esc(v)}"${String(v)===String(cur)?" selected":""}>${esc(l)}</option>`;
  box.innerHTML=`<div class="set-prefs">
    <div class="set-col">
      <label class="set-row"><span class="set-lbl">Landing section</span>
        <select data-pref="landing">${opt("last","Last visited",prefs.landing)}${SECTIONS.map(s=>opt(s.id,s.label,prefs.landing)).join("")}</select></label>
      <label class="set-row"><span class="set-lbl">Clock</span>
        <select data-pref="clock">${opt("auto","Browser default",prefs.clock)}${opt("24","24-hour",prefs.clock)}${opt("12","12-hour",prefs.clock)}</select></label>
      <label class="set-row"><input type="checkbox" data-pref="compact"${prefs.compact?" checked":""}> <span>Compact tables</span></label>
      <label class="set-row"><span class="set-lbl">Action queue refresh</span>
        <select data-pref="actionPollMs">${PREF_CHOICES.actionPollMs.map(v=>opt(v,(v/1000)+" s",prefs.actionPollMs)).join("")}</select></label>
      <label class="set-row"><span class="set-lbl">Machines live refresh</span>
        <select data-pref="vmPollMs">${PREF_CHOICES.vmPollMs.map(v=>opt(v,(v/1000)+" s",prefs.vmPollMs)).join("")}</select></label>
      <label class="set-row"><span class="set-lbl">Overview live refresh</span>
        <select data-pref="pulsePollMs">${PREF_CHOICES.pulsePollMs.map(v=>opt(v,v?(v>=60000?(v/60000)+" min":(v/1000)+" s"):"Off",prefs.pulsePollMs)).join("")}</select></label>
      <div class="set-actions">
        <button type="button" class="btn" data-reset-layout>Reset layout (tiles, sidebar)</button>
        <button type="button" class="btn" data-reset-prefs>Reset preferences</button>
      </div>
    </div>
    <div class="set-col"><span class="set-lbl">Navigation — show, hide and reorder sections</span>
      <ol class="set-nav">${order.map((s,i)=>`<li><label><input type="checkbox" data-nav-show="${s.id}"${prefs.hidden.includes(s.id)?"":" checked"}${s.id==="settings"?" disabled title=\"Settings is always shown\"":""}>
        <span aria-hidden="true">${s.icon}</span> ${esc(s.label)}</label>
        <span class="set-move"><button type="button" class="btn" data-nav-move="${s.id}" data-dir="-1" aria-label="Move ${esc(s.label)} up"${i?"":" disabled"}>↑</button>
        <button type="button" class="btn" data-nav-move="${s.id}" data-dir="1" aria-label="Move ${esc(s.label)} down"${i<order.length-1?"":" disabled"}>↓</button></span></li>`).join("")}</ol>
      <small>Hidden sections stay reachable from ⌘K and links.</small></div>
    <div class="set-col"><span class="set-lbl">Overview live pulse — show, hide and reorder cards</span>
      <ol class="set-nav">${cards.map((c,i)=>`<li><label><input type="checkbox" data-card-show="${c.id}"${prefs.pulseHidden.includes(c.id)?"":" checked"}>
        <span aria-hidden="true">${c.icon}</span> ${esc(c.label)}</label>
        <span class="set-move"><button type="button" class="btn" data-card-move="${c.id}" data-dir="-1" aria-label="Move ${esc(c.label)} card up"${i?"":" disabled"}>↑</button>
        <button type="button" class="btn" data-card-move="${c.id}" data-dir="1" aria-label="Move ${esc(c.label)} card down"${i<cards.length-1?"":" disabled"}>↓</button></span></li>`).join("")}</ol>
      <small>The Overview badge counts red cards among the ones shown.</small></div>
  </div>`;
  box.querySelectorAll("[data-pref]").forEach(el=>el.addEventListener("change",()=>{
    const k=el.dataset.pref;
    const v=el.type==="checkbox"?el.checked:/Ms$/.test(k)?Number(el.value):el.value;
    savePrefs({[k]:v});
    if(k==="compact") document.body.classList.toggle("compact-tables",prefs.compact);
    if(k==="clock"&&window.__updateClock) window.__updateClock();
    if(k==="actionPollMs") restartActionPoll();
    if(k==="pulsePollMs") restartPulsePoll();
  }));
  box.querySelectorAll("[data-card-show]").forEach(el=>el.addEventListener("change",()=>{
    const id=el.dataset.cardShow;
    savePrefs({pulseHidden:el.checked?prefs.pulseHidden.filter(x=>x!==id):[...prefs.pulseHidden,id]});
    buildSections(); renderPrefs();
  }));
  box.querySelectorAll("[data-card-move]").forEach(el=>el.addEventListener("click",()=>{
    const ids=cards.map(c=>c.id), i=ids.indexOf(el.dataset.cardMove), j=i+Number(el.dataset.dir);
    if(j<0||j>=ids.length) return;
    [ids[i],ids[j]]=[ids[j],ids[i]];
    savePrefs({pulseOrder:ids}); renderPrefs();
    const again=document.querySelector(`[data-card-move="${el.dataset.cardMove}"][data-dir="${el.dataset.dir}"]`);
    if(again&&!again.disabled) again.focus();
  }));
  box.querySelectorAll("[data-nav-show]").forEach(el=>el.addEventListener("change",()=>{
    const id=el.dataset.navShow;
    savePrefs({hidden:el.checked?prefs.hidden.filter(x=>x!==id):[...prefs.hidden,id]});
    buildSections(); renderPrefs();
  }));
  box.querySelectorAll("[data-nav-move]").forEach(el=>el.addEventListener("click",()=>{
    const ids=order.map(s=>s.id), i=ids.indexOf(el.dataset.navMove), j=i+Number(el.dataset.dir);
    if(j<0||j>=ids.length) return;
    [ids[i],ids[j]]=[ids[j],ids[i]];
    savePrefs({order:ids}); buildSections(); renderPrefs();
    const again=document.querySelector(`[data-nav-move="${el.dataset.navMove}"][data-dir="${el.dataset.dir}"]`);
    if(again&&!again.disabled) again.focus();
  }));
  box.querySelector("[data-reset-layout]").addEventListener("click",()=>{
    try{ localStorage.removeItem(PORTAL_LAYOUT_KEY); }catch(_){}
    location.hash="settings"; location.reload();
  });
  box.querySelector("[data-reset-prefs]").addEventListener("click",()=>{
    try{ localStorage.removeItem(PREFS_KEY); }catch(_){}
    location.hash="settings"; location.reload();
  });
}

// ---- ⌘K command palette + deep-link URL state ----
// One fuzzy jump box over every store, and a URL hash that names the current
// view (#prs, #bug/KEY, #ph/PH-004, #task/T-101, #pr/17, #raid/I-007) so any
// view can be bookmarked or pasted to someone else on their own portal.
let navFromHash=false;
function setHash(h){
  if(navFromHash) return;
  const want="#"+h;
  if(location.hash!==want) history.replaceState(null,"",want);
}
function goItem(kind,id){
  if(kind==="member" && window.TeamUI){ selectSection("team"); window.TeamUI.navigate("member/"+id); return; }
  if(kind==="section"){ selectSection(id); return; }
  if(kind==="bug"){ selectSection("bugs"); goBug(id); setHash("bug/"+id); return; }
  if(kind==="ph"){ selectSection("bugs"); goPhenomenon(id); setHash("ph/"+id); return; }
  if(kind==="task"){
    selectSection("work");
    document.getElementById("reset").click();
    const task=TASKS.find(t=>t.id===id);
    if(task && ["done","cancelled"].includes(task.status)){
      const hideDone=document.getElementById("o-hidedone");
      hideDone.checked=false; hideDone.closest(".chip").classList.remove("on");
    }
    document.getElementById("q").value=id; render();
    const tr=document.querySelector(`#body tr.task[data-id="${CSS.escape(id)}"]`);
    if(tr){ tr.click(); tr.scrollIntoView({block:"center"}); }
    setHash("task/"+id); return;
  }
  if(kind==="pr"){
    const feature=(PRS.features||[]).find(f=>String(f.id)===String(id));
    if(PR_FILTERS.openOnly && feature
      && !(feature.prs||[]).some(p=>p.status!=="merged"&&p.status!=="closed")){
      PR_FILTERS.openOnly=false;
      const openOnly=document.getElementById("pr-openonly");
      openOnly.checked=false; openOnly.closest(".chip").classList.remove("on");
    }
    selectSection("prs");
    setPrCollapsed(id,false);
    const tr=document.querySelector('tr.prf-row[data-fid="'+CSS.escape(String(id))+'"]');
    if(tr) tr.scrollIntoView({block:"center"});
    setHash("pr/"+id); return;
  }
  if(kind==="raid"){
    const row=(RAID.rows||[]).find(r=>r.id===id);
    if(row?.closed){
      const openOnly=document.getElementById("raid-openonly");
      openOnly.checked=false; openOnly.closest(".chip").classList.remove("on");
    }
    raidOpenRow=id; selectSection("raid"); setHash("raid/"+id); return;
  }
}
function applyHash(){
  const h=decodeURIComponent(location.hash.slice(1)); if(!h) return false;
  const [kind,...rest]=h.split("/"), id=rest.join("/");
  navFromHash=true;
  try{
    if(window.TeamUI && (kind==="member" || (kind==="team" && id))){
      selectSection("team"); window.TeamUI.navigate(h); return true;
    }
    if(!id){ if(SECTIONS.some(s=>s.id===kind&&s.ready)){ selectSection(kind); return true; } return false; }
    if(["bug","ph","task","pr","raid"].includes(kind)){ goItem(kind,id); return true; }
    if(kind==="ai"&&SERVED){ aiShowRun(id); return true; }
    if(kind==="review-chat"&&SERVED&&/^rc-[0-9a-f]{32}$/.test(id)){ rcOpen(id); return true; }
  }finally{ navFromHash=false; }
  return false;
}
window.addEventListener("hashchange",applyHash);

function cmdkIndex(){
  const out=[];
  if(window.TeamUI) out.push(...window.TeamUI.index());
  SECTIONS.filter(s=>s.ready).forEach(s=>out.push({k:"section",id:s.id,tag:"Go to",label:s.label,icon:s.icon}));
  TASKS.forEach(t=>out.push({k:"task",id:t.id,tag:t.id,label:t.title,sub:t.status,icon:"\u25a4",dim:["done","cancelled"].includes(t.status)}));
  bugList().forEach(b=>out.push({k:"bug",id:b.key,tag:b.key,label:b.summary,sub:b.status,icon:"\u2691",dim:b.closed||/^(done|closed|resolved)$/i.test(b.status||"")}));
  phList().forEach(p=>out.push({k:"ph",id:p.id,tag:p.id,label:p.title,sub:p.status,icon:"\u2b21"}));
  (PRS.features||[]).forEach(f=>{
    out.push({k:"pr",id:String(f.id),tag:"F"+f.id,label:f.name,sub:f.worst,icon:"\u2197",extra:(f.jira||[]).join?(f.jira||[]).join(" "):(f.jira||"")});
    (f.prs||[]).forEach(p=>out.push({k:"pr",id:String(f.id),tag:p.repo+"#"+p.number,label:p.title||f.name,sub:p.status,icon:"\u2197",url:p.url}));
  });
  (RAID.rows||[]).forEach(r=>out.push({k:"raid",id:r.id,tag:r.id,label:r.title,sub:r.rag,icon:"\u25c7",dim:!!r.closed}));
  if(PE_URL) PE_VIEWS.forEach(([p,l])=>out.push({k:"href",href:peLink(p),tag:"Pipelines",label:l,icon:"\u21e2"}));
  return out;
}
// Things typed that are not in any store still go somewhere useful.
function cmdkExternal(q){
  const v=q.trim(), out=[];
  if(/^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+/.test(v)&&PE_URL)
    out.push({k:"href",href:pePr(v),tag:"Pipelines",label:"PR pipeline status for "+v.replace("https://github.com/",""),icon:"\u21e2"});
  const run=v.match(/^(https:\/\/github\.com\/[^/]+\/[^/]+\/actions\/runs\/\d+)/);
  if(run&&PE_URL) out.push({k:"href",href:peRun(run[1]),tag:"Pipelines",label:"Analyse workflow run",icon:"\u21e2"});
  const rid=v.match(/^run:(\d+)$/i);
  if(rid&&PE_URL) out.push({k:"href",href:peLink("/search?q="+encodeURIComponent(v)),tag:"Pipelines",label:"Find run "+rid[1],icon:"\u21e2"});
  const rp=v.match(/^([\w.-]+)#(\d+)$/);
  if(rp&&PE_URL) out.push({k:"href",href:pePr("https://github.com/Elmnt-Internal/"+rp[1]+"/pull/"+rp[2]),tag:"Pipelines",label:"Pipeline status for "+v,icon:"\u21e2"});
  if(/^[A-Z][A-Z0-9]+-\d+$/i.test(v)) out.push({k:"href",href:JIRA_BASE+v.toUpperCase(),tag:"Jira",label:"Open "+v.toUpperCase()+" in Jira",icon:"\u2197"});
  return out;
}
function cmdkScore(it,q){
  const tag=(it.tag||"").toLowerCase(), lab=(it.label||"").toLowerCase();
  if(tag===q) return 100;
  if(tag.startsWith(q)) return 80;
  if(lab.startsWith(q)) return 60;
  if(tag.includes(q)) return 50;
  const words=q.split(/\s+/).filter(Boolean);
  const hay=tag+" "+lab+" "+(it.sub||"").toLowerCase()+" "+(it.extra||"").toLowerCase();
  if(words.every(w=>hay.includes(w))) return 30-(it.dim?10:0);
  return 0;
}
function openCmdk(){
  if(document.querySelector(".cmdk")) return;
  const idx=cmdkIndex();
  const ov=document.createElement("div"); ov.className="cmdk";
  ov.innerHTML=`<div class="cmdk-box" role="dialog" aria-label="Jump to">
    <input class="cmdk-q" placeholder="Jump to a task, bug, PH-…, PR, RAID row, section — or paste a GitHub PR/run URL, repo#123, a Jira key" autocomplete="off">
    <div class="cmdk-list" role="listbox"></div>
    <div class="cmdk-foot"><kbd>\u2191</kbd><kbd>\u2193</kbd> move <kbd>\u21b5</kbd> open <kbd>esc</kbd> close</div></div>`;
  document.body.append(ov);
  const inp=ov.querySelector(".cmdk-q"), list=ov.querySelector(".cmdk-list");
  let hits=[], cur=0;
  const close=()=>{ ov.remove(); document.removeEventListener("keydown",onKey,true); };
  const pick=it=>{ if(!it) return; close();
    if(it.k==="href") window.open(it.href,"_blank","noopener"); else goItem(it.k,it.id); };
  const draw=()=>{
    const q=inp.value.trim().toLowerCase();
    let local=q?idx.map(it=>[cmdkScore(it,q),it]).filter(x=>x[0]>0).sort((a,b)=>b[0]-a[0]).map(x=>x[1]):[];
    // A store hit on the exact tag beats any "open elsewhere" fallback for it.
    const exact=local.some(it=>(it.tag||"").toLowerCase()===q);
    hits = q ? local.slice(0,12).concat(exact?[]:cmdkExternal(inp.value)).slice(0,14)
             : idx.filter(it=>it.k==="section");
    cur=Math.min(cur,Math.max(0,hits.length-1));
    list.innerHTML=hits.length?hits.map((it,i)=>`<div class="cmdk-it${i===cur?" on":""}${it.dim?" dim":""}" data-i="${i}" role="option">
      <span class="cmdk-ico">${esc(it.icon||"")}</span><span class="cmdk-tag">${esc(it.tag||"")}</span>
      <span class="cmdk-lab">${esc(it.label||"")}</span>${it.sub?`<span class="cmdk-sub">${esc(it.sub)}</span>`:""}</div>`).join("")
      :'<div class="cmdk-empty">No match</div>';
    const on=list.querySelector(".cmdk-it.on"); if(on) on.scrollIntoView({block:"nearest"});
  };
  const onKey=e=>{
    if(e.key==="Escape"){ e.preventDefault(); close(); }
    else if(e.key==="ArrowDown"){ e.preventDefault(); cur=Math.min(cur+1,hits.length-1); draw(); }
    else if(e.key==="ArrowUp"){ e.preventDefault(); cur=Math.max(cur-1,0); draw(); }
    else if(e.key==="Enter"){ e.preventDefault(); pick(hits[cur]); }
  };
  document.addEventListener("keydown",onKey,true);
  inp.addEventListener("input",()=>{ cur=0; draw(); });
  list.addEventListener("click",e=>{ const el=e.target.closest(".cmdk-it"); if(el) pick(hits[+el.dataset.i]); });
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
  draw(); inp.focus();
}
document.addEventListener("keydown",e=>{
  if((e.ctrlKey||e.metaKey)&&!e.altKey&&e.key.toLowerCase()==="k"){ e.preventDefault(); openCmdk(); }
});

// ---- keyboard: j/k move, Enter/e open, x tick, / search, g+letter section, ? help ----
const KB_ROWS={work:"#body tr.task", bugs:"#bug-body tr.task", prs:"#pr-body tr.prf-row", raid:"#raid-body tr.raid-row"};
const KB_SEARCH={work:"q", bugs:"bq", prs:"pr-q"};
const KB_GO={o:"overview",w:"work",s:"ws",b:"bugs",p:"prs",v:"vm",r:"raid"};
let kbIdx=-1, kbPending="", kbKey="";
const kbId=r=>r?(r.dataset.key||r.dataset.fid||r.dataset.id||""):"";
function kbRows(){
  const sel=KB_ROWS[activeSection]; if(!sel) return [];
  return [...document.querySelectorAll(sel)].filter(r=>r.offsetParent!==null);
}
function kbMark(rows){
  document.querySelectorAll(".kb-cur").forEach(r=>r.classList.remove("kb-cur"));
  const r=rows[kbIdx]; if(r){ r.classList.add("kb-cur"); kbKey=kbId(r); r.scrollIntoView({block:"nearest"}); }
}
function kbOpen(r){
  if(activeSection==="prs"){ const id=r.dataset.fid; setPrCollapsed(id,!prCollapsed(id)); }
  else r.click();
}
function kbHelp(){
  if(document.querySelector(".cmdk")) return;
  const ov=document.createElement("div"); ov.className="cmdk";
  const row=(k,d)=>'<div class="kb-row"><span>'+k.map(x=>"<kbd>"+x+"</kbd>").join(" ")+'</span>'+d+'</div>';
  ov.innerHTML='<div class="cmdk-box kb-help" role="dialog" aria-label="Keyboard shortcuts"><h3>Keyboard shortcuts</h3>'
    +row(["Ctrl/\u2318","K"],"Jump to anything")+row(["j"],"Next row")+row(["k"],"Previous row")
    +row(["Enter"],"Expand / collapse row")+row(["e"],"Expand / collapse row")+row(["x"],"Tick row (Bugs, PRs)")
    +row(["/"],"Search this section")+row(["g","o/w/s/b/p/l/v/r"],"Go to Overview / Work / Workspaces / Bugs / PRs / PLDM / VM / RAID")
    +row(["Esc"],"Close / leave search")+row(["?"],"This help")+'</div>';
  document.body.append(ov);
  const close=()=>{ ov.remove(); document.removeEventListener("keydown",esc1,true); };
  const esc1=e=>{ if(e.key==="Escape"||e.key==="?"){ e.preventDefault(); e.stopPropagation(); close(); } };
  document.addEventListener("keydown",esc1,true);
  ov.addEventListener("click",e=>{ if(e.target===ov) close(); });
}
document.addEventListener("keydown",e=>{
  if(e.ctrlKey||e.metaKey||e.altKey||document.querySelector(".cmdk")) return;
  const t=e.target, typing=t&&(t.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
  if(typing){ if(e.key==="Escape"&&t.blur) t.blur(); return; }
  if(kbPending==="g"){ kbPending=""; const id=KB_GO[e.key]; if(id){ e.preventDefault(); selectSection(id); kbIdx=-1; kbKey=""; } return; }
  if(e.key==="g"){ kbPending="g"; setTimeout(()=>{ kbPending=""; },1200); return; }
  if(e.key==="?"){ e.preventDefault(); kbHelp(); return; }
  if(e.key==="/"){ const el=document.getElementById(KB_SEARCH[activeSection]||""); if(el){ e.preventDefault(); el.focus(); el.select(); } return; }
  const rows=kbRows(); if(!rows.length) return;
  // A re-render drops the class, so fall back to the remembered row identity.
  const cur=document.querySelector(".kb-cur")||(kbKey&&rows.find(r=>kbId(r)===kbKey));
  kbIdx=cur?rows.indexOf(cur):-1; if(cur&&!cur.classList.contains("kb-cur")) kbMark(rows);
  if(e.key==="j"||e.key==="ArrowDown"&&e.shiftKey){ e.preventDefault(); kbIdx=Math.min(kbIdx+1,rows.length-1); kbMark(rows); }
  else if(e.key==="k"){ e.preventDefault(); kbIdx=Math.max(kbIdx-1,0); kbMark(rows); }
  else if((e.key==="Enter"||e.key==="e")&&kbIdx>=0){ e.preventDefault(); kbOpen(rows[kbIdx]); kbMark(kbRows()); }
  else if(e.key==="x"&&kbIdx>=0){
    const cb=rows[kbIdx].querySelector("input.b-sel, input.prf-pick"); if(cb){ e.preventDefault(); cb.click(); }
  }
});
init();