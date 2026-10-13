// Team Portal client. Served from /assets/portal.js by `tm.py serve`,
// and inlined into the static `tm.py dashboard` export. Every payload arrives
// through the one `window.__PORTAL__` bootstrap object the page defines just
// above this script; the fetches in init() then refresh them when served.
const BOOT = window.__PORTAL__ || {};
let TASKS = BOOT.tasks || [];
let WORKSPACES = BOOT.workspaces || {};
let BUGS = BOOT.bugs || {};
let OVERVIEW = BOOT.overview || {};
let PRS = BOOT.prs || {};
let RAID = BOOT.raid || {};
let PLDMQ = BOOT.pldmq || {};
let VMLOG = BOOT.vmlog || {};
let SERVED = false;
const JIRA_BASE = "https://elementlabs.atlassian.net/browse/";
const STATUS_ORDER = ["in-progress","blocked","todo","backlog","done","cancelled"];
const PRI_ORDER = ["critical","high","medium","low"];
const CPX_ORDER = ["trivial","small","medium","large","epic"];
const CATEGORIES = ["development","code-review","planning","testing","track","misc"];
const RV_ORDER = ["pending","in-progress","commented","changes-requested","waiting-for-author","waiting-for-re-review","approved","merged"];
// Per-PR review statuses that can be *set* from the editor (excludes the
// consolidated-only "waiting-for-author").
const RV_SETTABLE = RV_ORDER.filter(s=>s!=="waiting-for-author");

// Which workspaces are tracked, and how they are grouped, is user-editable and
// lives in the workspace-manager store — the client renders whatever the server
// sends rather than carrying its own roster.
function wsRoster(){ return (WORKSPACES&&WORKSPACES.roster)||[]; }
function wsGroups(){ return (WORKSPACES&&WORKSPACES.groups)||[]; }
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
  {key:"cost",    label:"Est",       on:true},
  {key:"jira",    label:"Jira",      on:true},
  {key:"workspace",label:"WS",       on:false},
  {key:"age",     label:"Age",       on:true},
  {key:"worked",  label:"Worked",    on:true},
];

// Per-tab column presets (smart category views). Order follows COLUMNS.
const TAB_DEFS = {
  "all":         {cols:["id","title","category","priority","status","cost","jira","age","worked"]},
  "development": {cols:["id","title","priority","status","complexity","cost","workspace","age","worked"]},
  "code-review": {cols:["id","title","priority","status","reviewstatus","prs","author","age"]},
  "planning":    {cols:["id","title","priority","status","jira","age","worked"]},
  "testing":     {cols:["id","title","priority","status","complexity","cost","age"]},
  "track":       {cols:["id","title","priority","status","jira","age","worked"]},
  "misc":        {cols:["id","title","priority","status","age","worked"]},
};
let activeTab = "all";

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
// Mirrors consolidate_review_status() in the Python CLI.
function consolidateReviewStatus(pl){
  const ss=(pl||[]).map(r=>r.review_status||"pending");
  if(!ss.length) return "pending";
  if(ss.every(s=>s==="merged")) return "merged";
  if(ss.every(s=>s==="approved"||s==="merged")) return "approved";
  if(ss.some(s=>s==="commented"||s==="changes-requested")) return "waiting-for-author";
  if(ss.some(s=>s==="waiting-for-re-review")) return "waiting-for-re-review";
  if(ss.some(s=>s==="in-progress")) return "in-progress";
  return "pending";
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

// ---- workspace alignment (manual-review checkout) progress banner ----
// Tones map to classes in portal.css so the banner follows the theme; the
// per-repo rows are the only place that needs a tone other than ok/warn/bad.
const ALIGN_ICONS={"checked-out":"✓","skipped-dirty":"⚠","error":"✕","not-found":"✕","already":"✓"};
function alignRepoRow(r){
  const ic=r.icon||ALIGN_ICONS[r.action]||"·";
  const tone=r.tone||(r.action==="checked-out"?"ok":(r.action==="skipped-dirty"?"warn":"bad"));
  const tag=r.role==="review"?' <b class="ab-tag">[PR]</b>':"";
  return `<div class="ab-row"><span class="ab-${tone}">${ic}</span> `
    +`<code>${esc(r.name)}</code>${tag} — ${esc(r.detail||r.action)}</div>`;
}
function renderAlign(job){
  const banner=document.getElementById("align-banner");
  const spin=document.getElementById("align-spin");
  const title=document.getElementById("align-title");
  const body=document.getElementById("align-body");
  banner.style.display="block";
  const running=job.status==="running";
  const reset=job.kind==="reset";
  const prScan=job.kind==="pr-scan";
  const sync=job.kind==="sync";
  const revScan=job.kind==="review-scan";
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
  const banner=document.getElementById("align-banner");
  banner.style.display="block";
  document.getElementById("align-spin").style.display="inline-block";
  document.getElementById("align-title").textContent=label||"Aligning workspace…";
  document.getElementById("align-body").innerHTML="";
  let done=false, last=null;
  while(!done){
    try{
      const r=await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"});
      if(!r.ok){ document.getElementById("align-title").textContent="Alignment status unavailable"; return null; }
      last=await r.json();
      renderAlign(last);
      done=last.status!=="running";
    }catch(err){ document.getElementById("align-title").textContent="Network error during alignment"; return null; }
    if(!done) await new Promise(res=>setTimeout(res,900));
  }
  return last;
}
document.addEventListener("click",e=>{
  if(e.target && e.target.id==="align-close")
    document.getElementById("align-banner").style.display="none";
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

// ---- filtering ----
function applyFilters(){
  const q=document.getElementById("q").value.trim().toLowerCase();
  const st=checkedVals("status"), cat=checkedVals("category"), pri=checkedVals("priority");
  const cpx=checkedVals("complexity"), ws=checkedVals("workspace");
  const rvst=checkedVals("reviewstatus"), auth=checkedVals("author");
  const hideDone=document.getElementById("o-hidedone").checked;
  const needJira=document.getElementById("o-jira").checked;
  const needWs=document.getElementById("o-ws").checked;
  const reviewOnly=document.getElementById("o-review").checked;

  return TASKS.filter(t=>{
    if(activeTab!=="all" && t.category!==activeTab) return false;
    // "Hide done/cancelled" hides every finished task, merged review records
    // included; uncheck it to see the merged code-review history.
    if(hideDone && (t.status==="done"||t.status==="cancelled")) return false;
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
    case "title": return `<td class="title-cell"><div>${esc(t.title)}</div>`+
                         `<div class="feature">${esc(feature(t))}</div></td>`;
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
  if(!SERVED) return `<div class="ro-note">Read-only. Run <code>tm.py serve</code> and open the served page to edit.</div>`;
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
      <label>Workspace<input type="text" class="e-workspace" value="${esc(t.workspace||"")}" style="min-width:140px"></label>
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

function detailRow(t,ncols){
  const p=prs(t);
  const prHtml = p.length ? `<div><h4>Pull Requests <span class="badge rv-${esc(consolidateReviewStatus(p))}">overall: ${esc(consolidateReviewStatus(p))}</span></h4><div class="pr-list">`+
    p.map(r=>`<div class="pr"><a href="${esc(r.pr_url)}" target="_blank">${esc(r.repo)}#${esc(r.pr_number)}</a> `+
      `by ${esc(r.author||"?")} · <code>${esc(r.branch||"?")}</code> · ${r.files_changed||0} files `+
      `+${r.additions||0}/-${r.deletions||0} · <b>${esc(r.review_status||"")}</b>${r.merged?" · merged":""}</div>`).join("")+
    `</div></div>` : "";
  const notesHtml=(t.notes||[]).length?`<div><h4>Notes</h4><ul class="notes">`+
    t.notes.map(n=>`<li>${esc(n)}</li>`).join("")+`</ul></div>`:"";
  return `<tr class="detail"><td colspan="${ncols}"><div class="detail-box">`+
    (t.description?`<div><h4>Description</h4>${esc(t.description)}</div>`:"")+
    `<div class="kv">`+
      (t.context?`<span><b>Context:</b> ${esc(t.context)}</span>`:"")+
      `<span><b>Created:</b> ${esc(t.created||"—")}</span>`+
      `<span><b>Updated:</b> ${esc(t.updated||"—")}</span>`+
      (t.due?`<span><b>Due:</b> ${esc(t.due)}</span>`:"")+
      (t.completed?`<span><b>Completed:</b> ${esc(t.completed)}</span>`:"")+
    `</div>`+ prHtml + notesHtml + editorHtml(t) +
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
      const res=await fetch("/api/task/"+encodeURIComponent(t.id),{
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      const data=await res.json();
      if(!res.ok){ msg.textContent="Error: "+(data.error||res.status); btn.disabled=false; return; }
      TASKS=data.tasks; buildFilters(); buildTabs(); render();
      msg.textContent="Saved.";
      if(data.align_job) pollAlign(data.align_job);
    }catch(err){ msg.textContent="Network error: "+err; btn.disabled=false; }
  });
  ed.querySelectorAll("select,input,textarea").forEach(el=>
    el.addEventListener("click",e=>e.stopPropagation()));
}

function render(){
  const cols=COLUMNS.filter(c=>c.on);
  document.getElementById("head").innerHTML=cols.map(c=>{
    const active=sortState.key===c.key;
    const arr=active?(sortState.dir>0?"▲":"▼"):"";
    return `<th data-key="${c.key}">${esc(c.label)}<span class="arr">${arr}</span></th>`;
  }).join("");
  document.querySelectorAll("#head th").forEach(th=>{
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
  const body=document.getElementById("body");
  body.innerHTML="";
  if(!data.length){ body.innerHTML=`<tr><td class="empty" colspan="${cols.length}">No tasks match the current filters.</td></tr>`; }
  data.forEach(t=>{
    const tr=document.createElement("tr"); tr.className="task";
    tr.innerHTML=cols.map(c=>cellFor(t,c.key)).join("");
    const det=document.createElement("template");
    det.innerHTML=detailRow(t,cols.length);
    const detEl=det.content.firstChild; detEl.style.display="none";
    tr.addEventListener("click",()=>{
      const show=detEl.style.display==="none";
      detEl.style.display = show?"":"none";
      if(show) wireEditor(detEl,t);
    });
    body.appendChild(tr); body.appendChild(detEl);
  });
  document.getElementById("count").textContent=`Showing ${data.length} of ${TASKS.length} tasks`;
  renderStats();
}

// ---- tabs (category views) ----
function tabCount(cat){
  return TASKS.filter(t=>(cat==="all"||t.category===cat) && !(["done","cancelled"].includes(t.status))).length;
}
function buildTabs(){
  const present=CATEGORIES.filter(c=>TASKS.some(t=>t.category===c));
  const tabs=[["all","All"], ...present.map(c=>[c,c])];
  const box=document.getElementById("tabs");
  box.innerHTML=tabs.map(([k,l])=>
    `<button class="tab${k===activeTab?" on":""}" data-tab="${esc(k)}">${esc(l)}<span class="badge-count">${tabCount(k)}</span></button>`).join("");
  box.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>selectTab(b.dataset.tab)));
}
function applyTabPreset(tab){
  const preset=(TAB_DEFS[tab]||TAB_DEFS.all).cols;
  COLUMNS.forEach(c=>{ c.on=preset.includes(c.key); });
  buildColumns();
}
function selectTab(tab){
  activeTab=tab;
  document.querySelectorAll("#tabs .tab").forEach(b=>b.classList.toggle("on",b.dataset.tab===tab));
  // category filter is redundant inside a category tab: clear + hide it
  const gc=document.getElementById("grp-category");
  if(gc) gc.style.display = tab==="all" ? "" : "none";
  if(tab!=="all"){
    document.querySelectorAll('input[data-group="category"]').forEach(cb=>{ cb.checked=false; });
    updateDdLabel("category");
  }
  // review status / author filters only make sense inside the code-review tab
  const rvRow=document.getElementById("row-review");
  if(rvRow) rvRow.style.display = tab==="code-review" ? "" : "none";
  if(tab!=="code-review"){
    document.querySelectorAll('input[data-group="reviewstatus"],input[data-group="author"]')
      .forEach(cb=>{ cb.checked=false; });
    updateDdLabel("reviewstatus"); updateDdLabel("author");
  }
  // The review-scan button (gh probe) only applies to code-review, served only.
  const rvScan=document.getElementById("review-scan");
  const rvScanNote=document.getElementById("review-scan-note");
  const showScan = tab==="code-review" && SERVED;
  if(rvScan) rvScan.style.display = showScan ? "" : "none";
  if(rvScanNote && !showScan){ rvScanNote.style.display="none"; rvScanNote.textContent=""; }
  applyTabPreset(tab);
  render();
}

// ---- workspace state view ----
// Occupancy lives in the workspace-manager store (WORKSPACES), not on tasks.
function wsHolder(slot){ const r=WORKSPACES.slots&&WORKSPACES.slots[slot]; return (r&&r.holder)||null; }
function wsTaskById(id){ return TASKS.find(t=>t.id===id)||null; }
function activeTaskList(){ return TASKS.filter(t=>!["done","cancelled"].includes(t.status)); }
function ago(iso){ if(!iso) return ""; const n=daysSince(iso); return n==null?"":(n===0?"today":n+"d ago"); }
const WS_ADHOC_TAGS=["monitor","debug","development","experiment","other"];

// POST a slot occupy/free to the workspace API, then refresh the whole view.
async function wsSlotPost(slot, payload, msgEl){
  if(!SERVED){ if(msgEl) msgEl.textContent="Read-only — run tm.py serve to edit."; return; }
  if(msgEl) msgEl.textContent="Saving…";
  try{
    const res=await fetch("/api/workspace/"+encodeURIComponent(slot),{
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data=await res.json();
    if(!res.ok){ if(msgEl) msgEl.textContent="Error: "+(data.error||res.status); return; }
    TASKS=data.tasks; if(data.workspaces) WORKSPACES=data.workspaces;
    buildFilters(); buildTabs(); render();
    if(data.align_job) pollAlign(data.align_job);
  }catch(err){ if(msgEl) msgEl.textContent="Network error: "+err; }
}

async function wsSyncSlot(slot){
  if(!SERVED) return;
  try{
    const res=await fetch("/api/ws-sync/"+encodeURIComponent(slot),{method:"POST"});
    const data=await res.json();
    if(!res.ok){ alert("Sync failed: "+(data.error||res.status)); return; }
    if(data.align_job) pollAlign(data.align_job, `Syncing ${slot}…`);
  }catch(err){ alert("Network error: "+err); }
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
  const occ=!!holder;
  let bodyHtml;
  if(!holder){
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
        <button class="link ws-free" data-slot="${esc(s.slot)}">Set free</button>
        <button class="link ws-sync" data-slot="${esc(s.slot)}">⟳ Sync</button>
        <span class="ws-msg"></span></div>`;
    }else{
      const cand=activeTaskList().filter(t=>t.workspace!==s.slot);
      ctlHtml=`<div class="ws-actions ws-occupy-box" data-slot="${esc(s.slot)}">
        <div class="ws-occ-row">
          <select class="ws-pick"><option value="">— occupy with a task —</option>${
            cand.map(t=>`<option value="${esc(t.id)}">${esc(t.id)} · ${esc((t.title||"").slice(0,48))}${t.workspace?` (moves from ${esc(t.workspace)})`:""}</option>`).join("")}</select>
          <button class="primary ws-occupy-task">Occupy</button>
        </div>
        <div class="ws-occ-row">
          <input type="text" class="ws-adhoc-label" placeholder="…or an ad-hoc purpose (e.g. monitor PRs)">
          <select class="ws-adhoc-tag">${WS_ADHOC_TAGS.map(g=>`<option value="${g}">${g}</option>`).join("")}</select>
          <button class="ws-occupy-adhoc">Hold</button>
        </div>
        <div class="ws-occ-row">
          <button class="link ws-sync" data-slot="${esc(s.slot)}">⟳ Sync</button>
        </div>
        <span class="ws-msg"></span></div>`;
    }
  }else{
    ctlHtml=`<div class="ro-note">Read-only. Run <code>tm.py serve</code> and open the served page to change occupancy.</div>`;
  }
  const headTitle = occ
    ? `<span class="ws-topic">${holderHeader(holder)}</span>`
    : `<span class="ws-topic ws-free-name">${esc(s.label||s.slot)}</span>`;
  const pinned = s.rev && s.rev!=="main";
  const revChip = pinned
    ? `<span class="ws-rev" title="pinned revision / manifest">${esc(s.rev)}</span>` : "";
  const missing = s.exists===false
    ? `<span class="ws-rev ws-missing" title="${esc(s.path||"")}">missing</span>` : "";
  const tools = SERVED ? `<span class="ws-cardtools">
      <button class="ws-mini ws-group-btn" data-slot="${esc(s.slot)}" title="Group">\u25c7</button>
      <button class="ws-mini ws-untrack" data-slot="${esc(s.slot)}" title="Stop tracking">\u2715</button>
    </span>${wsGroupMenu(s)}` : "";
  return `<div class="ws-card ${occ?"occupied":"free"}${pinned?" special":""}" title="${esc(s.path||"")}">
    <div class="ws-head">
      <div class="ws-name"><span class="num${pinned?" special":""}">${esc(String(s.badge||s.slot))}</span>${headTitle}${revChip}${missing}</div>
      <span class="ws-headright"><span class="badge ws-badge-${occ?"occupied":"free"}">${occ?"occupied":"free"}</span>${tools}</span>
    </div>
    <div class="ws-body">${bodyHtml}</div>
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
  if(!SERVED){ alert("Read-only — run tm.py serve to edit the roster."); return null; }
  const res=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(payload||{})});
  const data=await res.json();
  if(!res.ok){ alert(data.error||("Error "+res.status)); return null; }
  if(data.workspaces) WORKSPACES=data.workspaces;
  if(data.tasks) TASKS=data.tasks;
  renderWorkspaces();
  return data;
}

// Modal for "+ Track workspace". A path is all that's required; the slot name
// is the directory's basename, which is what every other view keys on.
function wsTrackDialog(){
  const groupOpts=wsGroups().map(g=>`<option value="${esc(g.id)}">${esc(g.name)}</option>`).join("");
  const ov=document.createElement("div");
  ov.className="ws-modal-ov";
  ov.innerHTML=`<div class="ws-modal">
    <h3>Track a workspace</h3>
    <label>Path<input type="text" id="wstk-path" placeholder="~/projects/iridium/seven" autocomplete="off"></label>
    <label>Label <span class="opt">optional</span><input type="text" id="wstk-label" placeholder="defaults to the directory name"></label>
    <div class="ws-modal-row">
      <label>Revision <span class="opt">optional</span><input type="text" id="wstk-rev" placeholder="main"></label>
      <label>Badge <span class="opt">optional</span><input type="text" id="wstk-badge" placeholder="auto" maxlength="4"></label>
    </div>
    <label>Group<select id="wstk-group"><option value="">— ungrouped —</option>${groupOpts}</select></label>
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
      label:ov.querySelector("#wstk-label").value.trim(),
      rev:ov.querySelector("#wstk-rev").value.trim(),
      badge:ov.querySelector("#wstk-badge").value.trim(),
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
    const held=b.items.filter(s=>wsHolder(s.slot)).length;
    return `<div class="ws-group" data-gkey="${esc(key)}">
    <div class="ws-ghead">
      <button class="ws-gtoggle" title="Collapse or expand this group">${off?"\u25b8":"\u25be"}</button>
      <span class="ws-gdot" style="background:${esc(b.g.color||"#94a3b8")}"></span>
      <span class="ws-gname">${esc(b.g.name)}</span><span class="ws-gcount">${b.items.length}</span>
      ${off?`<span class="ws-gheld">${held} occupied \u00b7 ${b.items.length-held} free</span>`:""}
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
  const roster=wsRoster();
  const occupied=roster.filter(s=>wsHolder(s.slot)).length;
  const free=roster.length-occupied;
  const stats=`<div class="stats">
    <div class="stat"><div class="n">${roster.length}</div><div class="l">Workspaces</div></div>
    <div class="stat"><div class="n">${occupied}</div><div class="l">Occupied</div></div>
    <div class="stat"><div class="n">${free}</div><div class="l">Free</div></div>
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
      <button class="ws-groups-btn${wsGroupPanelOpen?" on":""}">\u2699 Groups</button>
      <button class="primary ws-track-btn">\uff0b Track workspace</button>
    </div>`:"";
  box.innerHTML = `<div class="ws-toolbar">${stats}<div class="ws-toolbar-r">${layout}${collapse}${tools}</div></div>`
    + (wsGroupPanelOpen?`<div class="ws-gpanel">${wsGroupPanelHtml()}</div>`:"")
    + wsGrids(roster);
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
  const groupsBtn=box.querySelector(".ws-groups-btn");
  if(groupsBtn) groupsBtn.addEventListener("click",()=>{ wsGroupPanelOpen=!wsGroupPanelOpen; renderWorkspaces(); });
  box.querySelectorAll(".ws-untrack").forEach(b=>b.addEventListener("click",()=>{
    if(!confirm(`Stop tracking '${b.dataset.slot}'? The directory itself is left untouched.`)) return;
    wsRosterPost("/api/ws-untrack/"+encodeURIComponent(b.dataset.slot),{});
  }));
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
  box.querySelectorAll(".ws-free").forEach(b=>b.addEventListener("click",()=>{
    const msg=b.closest(".ws-actions").querySelector(".ws-msg");
    wsSlotPost(b.dataset.slot,{action:"free"},msg);
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
  const scope = activeTab==="all" ? TASKS : TASKS.filter(t=>t.category===activeTab);
  const c={}; scope.forEach(t=>c[t.status]=(c[t.status]||0)+1);
  const open=scope.filter(t=>!["done","cancelled"].includes(t.status));
  const est=open.reduce((s,t)=>s+(t.cost_hours||0),0);
  const cards=[
    ["Total",scope.length],["Open",open.length],
    ["In&nbsp;progress",c["in-progress"]||0],["Blocked",c["blocked"]||0],
    ["Todo",c["todo"]||0],["Done",c["done"]||0],
    ["Est&nbsp;remaining",est+"h"],
  ];
  if(activeTab==="development"){
    const ws=new Set(open.map(t=>t.workspace).filter(Boolean));
    cards.push(["Workspaces&nbsp;busy",ws.size]);
  } else if(activeTab==="code-review"){
    const revTasks=scope.filter(t=>t.category==="code-review" && !["done","cancelled"].includes(t.status));
    const allPrs=scope.flatMap(t=>prs(t));
    const by={}; revTasks.forEach(t=>{ const s=consolidateReviewStatus(prs(t)); by[s]=(by[s]||0)+1; });
    const merged=scope.filter(t=>t.category==="code-review" && consolidateReviewStatus(prs(t))==="merged").length;
    cards.push(["PRs",allPrs.length],
               ["Waiting&nbsp;author",by["waiting-for-author"]||0],
               ["Re-review",by["waiting-for-re-review"]||0],
               ["Approved",by["approved"]||0],
               ["Merged",merged]);
  } else if(activeTab==="planning"){
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
// Bug tabs are saved filter presets, not categories.
const BUG_TABS = [
  ["open","Open"], ["new","New"], ["untriaged","Untriaged"],
  ["attention","Needs attention"], [FAM_TAB,"Families"], ["all","All"],
];
let bugTab = "open";
let bugSigFilter = new Set();
const bugSort = {key:"signal", dir:1};

function bugList(){ return (BUGS && BUGS.bugs) || []; }
function bugFamilies(){ return (BUGS && BUGS.families) || []; }
function famById(id){ return bugFamilies().find(f=>f.id===id)||null; }
function bugTri(b){ return b.triage||{}; }
function bugNeedsAttention(b){
  return b.open && (["stalled","dormant"].includes(b.signal)
                    || bugTri(b).state==="untriaged" || !b.assignee
                    || (b.flags||[]).includes("pinged"));
}
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
  document.querySelectorAll("#bug-tabs .tab").forEach(t=>t.classList.toggle("on",t.dataset.btab===tab));
  const fam = tab===FAM_TAB;
  document.getElementById("bug-controls").style.display = fam?"none":"";
  document.getElementById("bug-tbl").style.display     = fam?"none":"";
  document.getElementById("bug-count").style.display   = fam?"none":"";
  document.getElementById("bug-stats").style.display   = fam?"none":"";
  document.getElementById("famview").style.display     = fam?"":"none";
  applyBugTabPreset(tab);
  renderBugs();
}
// Tabs are presets: "All" opens the whole mirror (incl. closed + muted);
// every other tab is an open-bug lens.
function applyBugTabPreset(tab){
  if(tab===FAM_TAB) return;
  const set=(id,on)=>{ const cb=document.getElementById(id);
    cb.checked=on; cb.closest(".chip").classList.toggle("on",on); };
  set("b-openonly", tab!=="all");
  set("b-hidemuted", tab!=="all");
}

function buildBugFilters(){
  const bugs=bugList();
  const fill=(id, vals, labelFn)=>{
    const sel=document.getElementById(id); const keep=sel.value;
    sel.innerHTML='<option value="">All</option>'+vals.map(v=>
      `<option value="${esc(v)}">${esc(labelFn?labelFn(v):v)}</option>`).join("");
    if(vals.includes(keep)) sel.value=keep;
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

// Server-side gh probe of every open review PR — approvals, comments,
// change-requests, re-pushes, merges — then re-derive each task status.
// Runs as a background job with a live progress banner (pollAlign).
async function scanReviews(){
  const btn=document.getElementById("review-scan"), note=document.getElementById("review-scan-note");
  if(!SERVED){ note.style.display=""; note.textContent="Run tm.py serve to scan."; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">↻</span> Scanning…';
  note.style.display=""; note.textContent="probing PRs via gh…";
  try{
    const r=await fetch("/api/review-scan",{method:"POST"});
    const data=await r.json();
    if(!r.ok||data.error) throw new Error(data.error||"scan failed");
    const job=await pollAlign(data.align_job,"Scanning reviews…");
    // Pull the freshly-updated task store back into the table.
    try{
      const tr=await fetch("/api/tasks",{cache:"no-store"});
      const td=await tr.json();
      if(td.tasks){ TASKS=td.tasks; if(td.workspaces) WORKSPACES=td.workspaces;
        buildFilters(); buildTabs(); render(); }
    }catch(_){}
    const res=(job&&job.result)||{};
    const bits=[(res.scanned||0)+" PR(s) checked"];
    if((res.changed||[]).length) bits.push("<b>"+res.changed.length+" changed</b>");
    if((res.completed||[]).length) bits.push(res.completed.length+" merged→done");
    if((res.errors||[]).length) bits.push('<span class="stale-note">'+res.errors.length+" error(s)</span>");
    note.innerHTML=bits.join(" · ");
  }catch(e){
    note.innerHTML='<span class="stale-note">Scan failed: '+esc(e.message)+'</span>';
  }finally{
    btn.disabled=false; btn.textContent="↻ Scan reviews";
  }
}
document.getElementById("review-scan").addEventListener("click",scanReviews);

function bugFilters(){
  const q=(document.getElementById("bq").value||"").toLowerCase().trim();
  const owner=document.getElementById("b-owner").value;
  const pri=document.getElementById("b-priority").value;
  const comp=document.getElementById("b-component").value;
  const fam=document.getElementById("b-family").value;
  const openOnly=document.getElementById("b-openonly").checked;
  const untriOnly=document.getElementById("b-untriaged").checked;
  const flagged=document.getElementById("b-flagged").checked;
  const hideMuted=document.getElementById("b-hidemuted").checked;
  return bugList().filter(b=>{
    if(!bugMatchesTab(b,bugTab)) return false;
    if(openOnly && !b.open) return false;
    if(untriOnly && (bugTri(b).state||"untriaged")!=="untriaged") return false;
    if(flagged && !(b.flags||[]).length) return false;
    if(hideMuted && b.watch==="muted") return false;
    if(bugSigFilter.size && !bugSigFilter.has(b.signal)) return false;
    if(owner && (b.assignee||"unassigned")!==owner) return false;
    if(pri && b.priority!==pri) return false;
    if(comp && !(b.components||[]).includes(comp)) return false;
    if(fam && bugTri(b).family!==fam) return false;
    if(q){
      const hay=[b.key,b.summary,b.description,b.assignee,b.status,
                 bugTri(b).root_cause,bugTri(b).next_step,b.family_title,
                 (b.components||[]).join(" "),(b.labels||[]).join(" "),
                 (b.notes||[]).join(" ")].join(" ").toLowerCase();
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
];
function bugCell(b,key){
  switch(key){
    case "signal": return `<td title="${esc(b.signal)}">${SIGNAL_ICON[b.signal]||""}</td>`;
    case "key": return `<td class="id"><a href="${esc(b.url)}" target="_blank" rel="noopener"
        onclick="event.stopPropagation()">${esc(b.key)}</a></td>`;
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
      return `<td title="Pings from Oded${src}${b.last_ping?` · last ${esc(b.last_ping)}`:""}"${
        n?' style="font-weight:700;color:#c2410c;"':''}>${n||"—"}</td>`; }
    case "ci": { const runs=b.ci_runs||[], r=runs[0];
      if(!r) return `<td>—</td>`;
      const more=runs.length>1?`<span class="feature">+${runs.length-1}</span>`:"";
      return `<td><a href="${esc(r.url)}" target="_blank" rel="noopener"`
        +` onclick="event.stopPropagation()"`
        +` title="${esc(r.at+(r.station?" · "+r.station:""))}">run</a>${more}</td>`; }
    case "triage": { const s=bugTri(b).state||"untriaged";
      const v=bugTri(b).verdict;
      return `<td><span class="badge tr-${esc(s)}">${esc(s)}</span>${v?`<div class="feature">${esc(v)}</div>`:""}</td>`; }
    case "family": return `<td>${esc(bugTri(b).family||"—")}</td>`;
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
        <label>Watch<select class="b-watch">${opts(WATCH_STATES,b.watch||"active")}</select></label>
        <label>Pings from Oded<input type="number" class="b-pings" min="0" style="width:64px;" value="${b.oded_pings||0}" title="Overrides the auto-derived count until the next scan"></label>
        <label>&nbsp;<button class="b-ping" title="Oded chased outside Jira — bump the manual counter and save">📣 +1 ping</button></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Root cause<input type="text" class="b-root" style="min-width:320px;" value="${esc(t.root_cause||"")}"></label>
        <label style="flex:1;">Next step<input type="text" class="b-next" style="min-width:320px;" value="${esc(t.next_step||"")}"></label>
        <label>Owner (driver)<input type="text" class="b-owner2" value="${esc(t.owner||"")}"></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Append note<textarea class="b-note" placeholder="What you observed / decided"></textarea></label>
      </div>
      <div class="actions">
        <button class="primary b-save">Save</button>
        <span class="msg"></span>
        ${SERVED?"":'<span class="hint">read-only — run tm.py serve to edit</span>'}
      </div>
    </div>`;
  return `<tr class="detail"><td colspan="${span}"><div class="detail-box">
    <div class="kv">
      <span><b>Jira</b> <a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.key)}</a></span>
      <span><b>Project</b> ${esc(b.project||"—")}</span>
      <span><b>Reporter</b> ${esc(b.reporter||"—")}</span>
      <span><b>Components</b> ${esc((b.components||[]).join(", ")||"—")}</span>
      <span><b>Labels</b> ${esc((b.labels||[]).join(", ")||"—")}</span>
    </div>
    <div class="kv">
      <span><b>Milestone</b> ${esc(b.milestone||"—")}</span>
      <span><b>Sprint</b> ${esc((b.sprints||[]).join(", ")||"—")}</span>
      <span><b>Estimate</b> ${b.estimate_h?esc(b.estimate_h)+"h":"—"}</span>
      <span><b>Due</b> ${esc(b.duedate||"—")}</span>
      <span><b>Created</b> ${esc(b.created||"—")}</span>
      <span><b>First seen</b> ${esc(b.first_seen||"—")}</span>
    </div>
    <div class="kv">
      <span><b>Last activity</b> ${esc(b.last_activity||"—")} (${b.idle_days==null?"—":b.idle_days+"d idle"})</span>
      <span><b>Comments</b> ${esc(a.comment_count||0)}${a.last_comment?` (last ${esc(a.last_comment)} by ${esc(a.last_comment_by||"—")})`:""}</span>
      <span><b>Field changes</b> ${esc(a.change_count||0)}</span>
      <span><b>Last status change</b> ${esc(a.last_status_change||"—")}</span>
      <span><b>Pings from Oded</b> ${b.oded_pings||0}${
        b.manual_pings?` (${b.jira_pings||0} in Jira + ${b.manual_pings} manual)`:""}${
        b.last_ping?` (last ${esc(b.last_ping)}${b.days_since_ping==null?"":`, ${b.days_since_ping}d ago`})`:""}</span>
    </div>
    ${(b.ci_runs||[]).length?`<div><h4>CI runs <span class="feature">scraped from Jira comments</span></h4><div class="kv">${
      b.ci_runs.map(r=>`<span><a href="${esc(r.url)}" target="_blank" rel="noopener">run ${esc(r.run_id)}</a> ${esc(r.at)}${r.station?` · ${esc(r.station)}`:""}</span>`).join("")}</div></div>`:""}
    ${(b.links||[]).length?`<div><h4>Jira links</h4><div class="kv">${
      b.links.map(l=>`<span>${esc(l.type||"relates")} <a href="${JIRA_BASE}${esc(l.key)}" target="_blank" rel="noopener">${esc(l.key)}</a></span>`).join("")}</div></div>`:""}
    ${b.description?`<div><h4>Description</h4><pre style="white-space:pre-wrap;margin:0;font-size:12px;">${esc(b.description)}</pre></div>`:""}
    ${(b.changes||[]).length?`<div><h4>Observed changes</h4><ul class="notes">${
      b.changes.slice(-6).reverse().map(c=>`<li>${esc(c.at)}: ${esc(c.diff.join("; "))}</li>`).join("")}</ul></div>`:""}
    ${(b.notes||[]).length?`<div><h4>Notes</h4><ul class="notes">${
      b.notes.slice(-8).reverse().map(n=>`<li>${esc(n)}</li>`).join("")}</ul></div>`:""}
    ${editor}
  </div></td></tr>`;
}
function wireBugEditor(row,b){
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
      msg.textContent="Saved.";
    }catch(err){ msg.textContent="Network error: "+err; who.disabled=false; }
  }
  btn.addEventListener("click",e=>{
    e.stopPropagation();
    post({
      triage_state: ed.querySelector(".b-state").value,
      verdict:      ed.querySelector(".b-verdict").value,
      severity:     ed.querySelector(".b-severity").value,
      family:       ed.querySelector(".b-family2").value,
      watch:        ed.querySelector(".b-watch").value,
      oded_pings:   ed.querySelector(".b-pings").value,
      root_cause:   ed.querySelector(".b-root").value,
      next_step:    ed.querySelector(".b-next").value,
      owner:        ed.querySelector(".b-owner2").value,
      note:         ed.querySelector(".b-note").value.trim(),
    }, btn);
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
        ${f.created?`<span>since ${esc(f.created)}</span>`:""}
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
function renderBugs(){
  if(bugTab===FAM_TAB){ renderFamilies(); return; }
  renderBugStats();
  document.getElementById("bug-head").innerHTML=BUG_COLS.map(c=>{
    const arr=bugSort.key===c.key?(bugSort.dir>0?"▲":"▼"):"";
    return `<th data-bkey="${c.key}">${esc(c.label)}<span class="arr">${arr}</span></th>`;
  }).join("");
  document.querySelectorAll("#bug-head th").forEach(th=>th.addEventListener("click",()=>{
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
    body.innerHTML=`<tr><td class="empty" colspan="${BUG_COLS.length}">No bugs match the current filters.</td></tr>`;
  }
  rows.forEach(b=>{
    const tr=document.createElement("tr"); tr.className="task";
    tr.innerHTML=BUG_COLS.map(c=>bugCell(b,c.key)).join("");
    const tpl=document.createElement("template");
    tpl.innerHTML=bugDetail(b,BUG_COLS.length);
    const det=tpl.content.firstChild; det.style.display="none";
    tr.addEventListener("click",()=>{
      const show=det.style.display==="none";
      det.style.display=show?"":"none";
      if(show) wireBugEditor(det,b);
    });
    body.appendChild(tr); body.appendChild(det);
  });
  document.getElementById("bug-count").textContent=
    `Showing ${rows.length} of ${bugList().length} bugs`;
}
["bq","b-owner","b-priority","b-component","b-family"].forEach(id=>{
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
  ["b-owner","b-priority","b-component","b-family"].forEach(id=>{document.getElementById(id).value="";});
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
// Overview storefront — team status at a glance. Everything is derived from
// data already on the page; no extra fetches and no Jira calls.
// ============================================================================
// Power BI default theme palette — categorical, high contrast, colour-safe order.
const PALETTE = ["#118DFF","#E66C37","#6B007B","#E044A7","#744EC2","#D9B300",
                 "#12239E","#1AAB40","#D64550","#197278","#3599B8","#9B9B9B"];
const PBI = {blue:"#118DFF", navy:"#12239E", orange:"#E66C37", purple:"#6B007B",
             pink:"#E044A7", violet:"#744EC2", gold:"#D9B300", green:"#1AAB40",
             red:"#D64550", teal:"#197278", grey:"#9B9B9B"};
const RAG_RANK = {red:0, orange:1, yellow:2, green:3};

function ovIps(){ return (OVERVIEW && OVERVIEW.ips) || []; }
function ipOf(b){
  const comps=(b.components||[]).map(c=>String(c).toLowerCase());
  for(const ip of ovIps())
    if(comps.some(c=>(ip.hints||[]).some(h=>c.includes(String(h).toLowerCase())))) return ip.name;
  return "Other";
}
function daysUntil(iso){
  if(!iso) return null;
  return Math.round((new Date(iso+"T00:00:00") - new Date(new Date().toDateString()))/86400000);
}
// Local-calendar YYYY-MM-DD. toISOString() would shift by the UTC offset and
// silently misplace "today" for anyone east of Greenwich.
function isoDay(d){
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")
         +"-"+String(d.getDate()).padStart(2,"0");
}

// ---- SVG primitives -------------------------------------------------------
// Charts render as ONE svg with an intrinsic viewBox and no explicit height, so
// they scale proportionally inside the tile. That keeps text crisp and removes
// the plot/label alignment drift a split-svg layout suffers from.
function svgDonut(segs, size, thick, capt){
  size=size||152; thick=thick||20;
  const total=segs.reduce((a,s)=>a+s.v,0);
  const cx=size/2, r=(size-thick)/2, circ=2*Math.PI*r;
  let off=0;
  const arcs=segs.filter(s=>s.v>0).map(s=>{
    const len=total?circ*s.v/total:0;
    const el='<circle class="seg" cx="'+cx+'" cy="'+cx+'" r="'+r+'" fill="none" stroke="'+s.c+'" stroke-width="'+thick+'"'
      +' stroke-dasharray="'+len.toFixed(2)+' '+(circ-len).toFixed(2)+'" stroke-dashoffset="'+(-off).toFixed(2)+'">'
      +'<title>'+esc(s.l)+': '+s.v+(total?' ('+Math.round(100*s.v/total)+'%)':'')+'</title></circle>';
    off+=len; return el;
  }).join("");
  return '<svg class="donut" width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'" style="flex:none;">'
    +'<g transform="rotate(-90 '+cx+' '+cx+')">'
    +'<circle cx="'+cx+'" cy="'+cx+'" r="'+r+'" fill="none" stroke="var(--track)" stroke-width="'+thick+'"/>'
    +arcs+'</g>'
    +'<text x="'+cx+'" y="'+(cx-2)+'" text-anchor="middle" dominant-baseline="middle"'
    +' style="font:800 30px system-ui;fill:var(--text);">'+total+'</text>'
    +'<text x="'+cx+'" y="'+(cx+20)+'" text-anchor="middle" dominant-baseline="middle"'
    +' style="font:700 9px system-ui;fill:var(--muted);letter-spacing:.12em;">'
    +esc(capt||"OPEN")+'</text></svg>';
}
// Semicircular gauge — the Power BI KPI staple. Value is a percentage.
function svgGauge(pct, label, color, sub){
  pct=Math.max(0,Math.min(100,pct||0));
  const w=200, h=124, cx=w/2, cy=104, r=76, t=18;
  const pt=(deg,rad)=>[cx+rad*Math.cos(Math.PI*deg/180), cy+rad*Math.sin(Math.PI*deg/180)];
  const arc=(from,to,rad,col,width)=>{
    const a=pt(from,rad), b=pt(to,rad);
    return '<path d="M'+a[0].toFixed(1)+' '+a[1].toFixed(1)+' A'+rad+' '+rad+' 0 '
      +((to-from)>180?1:0)+' 1 '+b[0].toFixed(1)+' '+b[1].toFixed(1)+'" fill="none" stroke="'+col
      +'" stroke-width="'+width+'" stroke-linecap="round"/>';
  };
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="display:block;max-width:220px;margin:0 auto;">'
    +arc(180,359.9,r,"var(--track)",t)
    +(pct>0.6?arc(180,180+180*pct/100,r,color,t):"")
    +'<text x="'+cx+'" y="'+(cy-18)+'" text-anchor="middle" style="font:800 32px system-ui;fill:'+color+';">'
      +Math.round(pct)+'<tspan style="font:700 15px system-ui;">%</tspan></text>'
    +'<text x="'+cx+'" y="'+(cy-1)+'" text-anchor="middle" style="font:700 10px system-ui;fill:var(--muted);letter-spacing:.07em;">'
      +esc(label||"")+'</text>'
    +'<text x="'+(cx-r)+'" y="'+(cy+17)+'" text-anchor="middle" style="font:9px system-ui;fill:var(--muted);">0</text>'
    +'<text x="'+(cx+r)+'" y="'+(cy+17)+'" text-anchor="middle" style="font:9px system-ui;fill:var(--muted);">100</text>'
    +'<title>'+esc(sub||label||"")+'</title></svg>';
}
function svgLegend(segs, total){
  return segs.filter(s=>s.v>0).map(s=>'<div class="li">'
    +'<span class="sw" style="background:'+s.c+'"></span>'
    +'<span style="overflow:hidden;text-overflow:ellipsis;">'+esc(s.l)+'</span>'
    +'<span class="v">'+s.v+(total?' \u00b7 '+Math.round(100*s.v/total)+'%':'')+'</span></div>').join("");
}
// Stacked area over a daily x-axis. series = [{l,c,vals:[]}], stacked bottom-up.
function svgStackedArea(labels, series, w, h){
  w=w||780; h=h||250;
  const L=34, R=58, T=14, B=34, iw=w-L-R, ih=h-T-B, n=labels.length;
  if(!n) return '<div class="empty">No history yet.</div>';
  const tops=labels.map((_,i)=>series.reduce((a,s)=>a+(s.vals[i]||0),0));
  const max=Math.max(1,...tops);
  const X=i=>L+(n===1?iw/2:iw*i/(n-1));
  const Y=v=>T+ih-ih*v/max;
  const uid="g"+Math.random().toString(36).slice(2,8);
  const defs=[], bands=[], caps=[];
  let base=labels.map(()=>0);
  series.forEach((s,si)=>{
    const top=labels.map((_,i)=>base[i]+(s.vals[i]||0));
    const up=top.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(" ");
    const down=base.map((_,i)=>X(n-1-i).toFixed(1)+','+Y(base[n-1-i]).toFixed(1)).join(" ");
    defs.push('<linearGradient id="'+uid+si+'" x1="0" y1="0" x2="0" y2="1">'
      +'<stop offset="0%" stop-color="'+s.c+'" stop-opacity=".95"/>'
      +'<stop offset="100%" stop-color="'+s.c+'" stop-opacity=".55"/></linearGradient>');
    bands.push('<polygon points="'+up+' '+down+'" fill="url(#'+uid+si+')"/>'
      +'<polyline points="'+up+'" fill="none" stroke="'+s.c+'" stroke-width="2" stroke-linejoin="round"/>');
    const ly=Math.max(T+12,(Y(top[n-1])+Y(base[n-1]))/2);
    caps.push('<circle cx="'+X(n-1).toFixed(1)+'" cy="'+Y(top[n-1]).toFixed(1)+'" r="3.6" fill="'+s.c
      +'" stroke="var(--panel)" stroke-width="1.6"/>'
      +'<text x="'+(w-R+9)+'" y="'+(ly+3).toFixed(1)+'" style="font:800 15px system-ui;fill:'+s.c+';">'
      +(s.vals[n-1]||0)+'</text>'
      +'<text x="'+(w-R+9)+'" y="'+(ly+15).toFixed(1)+'" style="font:700 8.5px system-ui;fill:var(--muted);letter-spacing:.1em;">'
      +esc(s.l.toUpperCase())+'</text>');
    base=top;
  });
  const ticks=[0,.5,1].map(f=>{
    const v=Math.round(max*f);
    return '<line x1="'+L+'" y1="'+Y(v)+'" x2="'+(w-R)+'" y2="'+Y(v)+'" stroke="var(--grid)"/>'
      +'<text x="'+(L-7)+'" y="'+(Y(v)+3.5)+'" text-anchor="end" style="font:10px system-ui;fill:var(--muted);">'+v+'</text>';
  }).join("");
  const step=Math.max(1,Math.floor(n/6));
  const xlab=labels.map((d,i)=>(i%step===0||i===n-1)
    ? '<text x="'+X(i)+'" y="'+(T+ih+17)+'" text-anchor="middle" style="font:10px system-ui;fill:var(--muted);">'
      +d.slice(5).replace("-","/")+'</text>' : "").join("");
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="display:block;">'
    +'<defs>'+defs.join("")+'</defs>'+ticks+bands.join("")+caps.join("")
    +'<line x1="'+L+'" y1="'+(T+ih)+'" x2="'+(w-R)+'" y2="'+(T+ih)+'" stroke="var(--border)"/>'
    +xlab+'</svg>';
}
// Horizontal bar visual — category and value share a header line above the bar.
function barTable(rows, max){
  if(!rows.length) return '<div class="empty">Nothing to show.</div>';
  const m=Math.max(1,max);
  return '<div class="hbars">'+rows.map(r=>'<div class="hb">'
    +'<div class="hb-t"><span>'+esc(r.l)+'</span><b>'+r.v+'</b></div>'
    +'<div class="hb-r"><i style="width:'+(100*r.v/m).toFixed(1)+'%;background:'+(r.c||PBI.blue)+';"></i></div>'
    +(r.sub?'<div class="hb-s">'+esc(r.sub)+'</div>':'')
    +'</div>').join("")+'</div>';
}
// Axis-less inline trend line. Colour carries a second dimension (the signal).
function svgSparkline(vals, color, w, h){
  w=w||104; h=h||26;
  if(!vals || vals.length<2) return '<span style="color:var(--muted);">\u2014</span>';
  const n=vals.length, max=Math.max(1,...vals);
  const X=i=>1.5+(w-3)*i/(n-1), Y=v=>h-2-(h-6)*v/max;
  const pts=vals.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(" ");
  const last=vals[n-1], first=vals[0];
  const delta=last-first;
  return '<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'" style="vertical-align:middle;display:block;">'
    +'<polygon points="'+X(0).toFixed(1)+','+(h-1)+' '+pts+' '+X(n-1).toFixed(1)+','+(h-1)+'"'
      +' fill="'+color+'" fill-opacity=".15"/>'
    +'<polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="1.6"'
      +' stroke-linejoin="round" stroke-linecap="round"/>'
    +'<circle cx="'+X(n-1).toFixed(1)+'" cy="'+Y(last).toFixed(1)+'" r="2.4" fill="'+color+'"/>'
    +'<title>'+first+' \u2192 '+last+' open over '+n+' weeks (peak '+max+', '
      +(delta>0?"+"+delta:delta===0?"flat":delta)+')</title></svg>';
}
// Column visual with data labels and a dashed average line.
function svgBars(items, ref, unit, w, h){
  w=w||780; h=h||250;
  const L=34, R=12, T=24, B=44, iw=w-L-R, ih=h-T-B, n=items.length;
  if(!n) return '<div class="empty">Nothing to show.</div>';
  const max=Math.max(1, ...items.map(i=>i.v), ref||0)*1.14;
  const bw=Math.min(74, (iw/n)*0.56);
  const X=i=>L+iw*(i+0.5)/n, Y=v=>T+ih-ih*v/max;
  const ticks=[0,.5,1].map(f=>{
    const v=Math.round(max*f);
    return '<line x1="'+L+'" y1="'+Y(v)+'" x2="'+(w-R)+'" y2="'+Y(v)+'" stroke="var(--grid)"/>'
      +'<text x="'+(L-7)+'" y="'+(Y(v)+3.5)+'" text-anchor="end" style="font:10px system-ui;fill:var(--muted);">'+v+'</text>';
  }).join("");
  const bars=items.map((it,i)=>{
    if(!it.v) return '<text x="'+X(i)+'" y="'+(T+ih-7)+'" text-anchor="middle"'
      +' style="font:12px system-ui;fill:var(--muted);">\u2013</text>';
    return '<rect class="col" x="'+(X(i)-bw/2).toFixed(1)+'" y="'+Y(it.v).toFixed(1)+'" width="'+bw.toFixed(1)
      +'" height="'+(T+ih-Y(it.v)).toFixed(1)+'" rx="4" fill="'+(it.c||PBI.blue)+'">'
      +'<title>'+esc(it.label)+': '+it.v+unit+(it.sub?' ('+esc(it.sub)+')':'')+'</title></rect>'
      +'<text x="'+X(i)+'" y="'+(Y(it.v)-8).toFixed(1)+'" text-anchor="middle"'
      +' style="font:800 13px system-ui;fill:var(--text);">'+it.v+'</text>';
  }).join("");
  const refline=ref ? '<line x1="'+L+'" y1="'+Y(ref)+'" x2="'+(w-R)+'" y2="'+Y(ref)
      +'" stroke="'+PBI.red+'" stroke-width="1.4" stroke-dasharray="6 4"/>'
      +'<text x="'+(w-R)+'" y="'+(Y(ref)-6)+'" text-anchor="end"'
      +' style="font:800 9px system-ui;fill:'+PBI.red+';letter-spacing:.08em;">AVG '+ref+unit+'</text>' : "";
  const xlab=items.map((it,i)=>'<text x="'+X(i)+'" y="'+(T+ih+18)+'" text-anchor="middle"'
      +' style="font:700 11px system-ui;fill:var(--text);">'+esc(it.label)+'</text>'
    +'<text x="'+X(i)+'" y="'+(T+ih+31)+'" text-anchor="middle"'
      +' style="font:9.5px system-ui;fill:var(--muted);">'+esc(it.sub||"")+'</text>').join("");
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="display:block;">'
    +ticks+bars+refline
    +'<line x1="'+L+'" y1="'+(T+ih)+'" x2="'+(w-R)+'" y2="'+(T+ih)+'" stroke="var(--border)"/>'
    +xlab+'</svg>';
}

// ---- derived series -------------------------------------------------------
function ovBugFlow(){
  const bugs=bugList();
  if(!bugs.length) return {labels:[], series:[]};
  const days=90, today=new Date(new Date().toDateString());
  // Closed bugs occasionally lack a resolution date; fall back to when they
  // last entered their current status so the burn-up still balances.
  const pts=bugs.map(b=>({
    c:(b.created||"").slice(0,10),
    r:(b.resolved || (b.open?null:(b.status_since||b.updated)) || "").slice(0,10),
  })).filter(p=>p.c);
  const labels=[], open=[], closed=[];
  for(let i=days-1;i>=0;i--){
    const d=new Date(today); d.setDate(d.getDate()-i);
    const iso=isoDay(d);
    let c=0, o=0;
    pts.forEach(p=>{
      if(p.c>iso) return;
      if(p.r && p.r<=iso) c++; else o++;
    });
    labels.push(iso); closed.push(c); open.push(o);
  }
  return {labels, series:[
    {l:"Closed", c:"#16a34a", vals:closed},
    {l:"Open",   c:"#2563eb", vals:open},
  ]};
}
const MONTH_ABBR=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
// Days from created \u2192 resolved, bucketed by the month the bug CLOSED. Bucketing
// by close date (not open date) keeps every bucket final \u2014 it can never move.
function ovCloseTime(months){
  months=months||6;
  const byMonth={};
  bugList().forEach(b=>{
    if(b.open) return;
    const c=(b.created||"").slice(0,10);
    const r=(b.resolved || b.status_since || b.updated || "").slice(0,10);
    if(!c || !r) return;
    const d=Math.max(0, Math.round((new Date(r+"T00:00:00")-new Date(c+"T00:00:00"))/86400000));
    (byMonth[r.slice(0,7)] = byMonth[r.slice(0,7)] || []).push(d);
  });
  const now=new Date(), out=[];
  for(let i=months-1;i>=0;i--){
    const d=new Date(now.getFullYear(), now.getMonth()-i, 1);
    const vals=(byMonth[d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")]||[])
      .slice().sort((a,b)=>a-b);
    const mean=vals.length ? vals.reduce((a,v)=>a+v,0)/vals.length : 0;
    out.push({label:MONTH_ABBR[d.getMonth()], v:Math.round(mean*10)/10, n:vals.length,
              sub:vals.length?vals.length+" closed":"",
              median:vals.length?vals[Math.floor(vals.length/2)]:0});
  }
  return out;
}
// Weekly open-bug count per IP \u2014 the shape of each IP's load over time.
function ovIpTrend(weeks){
  weeks=weeks||12;
  const pts=bugList().map(b=>({ip:ipOf(b),
    c:(b.created||"").slice(0,10),
    r:(b.resolved || (b.open?null:(b.status_since||b.updated)) || "").slice(0,10)}))
    .filter(p=>p.c);
  const today=new Date(new Date().toDateString()), labels=[];
  for(let i=weeks-1;i>=0;i--){ const d=new Date(today); d.setDate(d.getDate()-i*7); labels.push(isoDay(d)); }
  const byIp={};
  pts.forEach(p=>{ byIp[p.ip]=byIp[p.ip]||labels.map(()=>0); });
  labels.forEach((iso,i)=>pts.forEach(p=>{
    if(p.c<=iso && !(p.r && p.r<=iso)) byIp[p.ip][i]++;
  }));
  return {labels, byIp};
}
function ovReviewTasks(){
  return TASKS.filter(t=>t.category==="code-review" && !["done","cancelled"].includes(t.status));
}function ovReviewRows(){
  const cnt={};
  ovReviewTasks().forEach(t=>{ const p=prs(t); if(!p.length) return;
    const s=consolidateReviewStatus(p); cnt[s]=(cnt[s]||0)+1; });
  const colors={pending:"#9B9B9B","in-progress":"#D9B300",commented:"#118DFF",
    "changes-requested":"#D64550","waiting-for-author":"#E66C37",
    "waiting-for-re-review":"#744EC2",approved:"#1AAB40",merged:"#197278"};
  return RV_ORDER.filter(s=>cnt[s]).map(s=>({l:s, v:cnt[s], c:colors[s]}));
}

// ---- development hygiene ---------------------------------------------------
// Each rule is "bad out of total"; a rule with no population is skipped rather
// than scored, so an empty PR queue can't flatter the score.
const HYG_STALE_PR_DAYS = 7;
const HYG_REVIEW_SLA_DAYS = 3;

function hygieneRules(){
  const feats=(PRS.features||[]).filter(f=>!f.complete);
  const prsAll=[].concat.apply([], feats.map(f=>f.prs||[]));
  const live=prsAll.filter(p=>!["merged","closed"].includes(p.status));
  const rvTasks=ovReviewTasks().filter(t=>prs(t).length);
  const rvWaiting=rvTasks.filter(t=>
    !["approved","merged","waiting-for-author"].includes(consolidateReviewStatus(prs(t))));
  const inProgress=TASKS.filter(t=>t.status==="in-progress");

  const rule=(label,bad,total,hint)=>({label, bad, total, hint});
  return [
    rule("CI green on open PRs",
         live.filter(p=>p.status==="ci-fail").length, live.length,
         "red checks block every reviewer behind you"),
    rule("Rebased on target",
         live.filter(p=>p.status==="needs-rebase").length, live.length,
         "behind or conflicting with the base branch"),
    rule("Approved PRs landed",
         live.filter(p=>p.status==="approved").length, live.length,
         "approved but still not merged"),
    rule("PRs moving",
         live.filter(p=>{const d=daysSince((p.updated||"").slice(0,10));
                         return d!=null && d>=HYG_STALE_PR_DAYS;}).length, live.length,
         "no activity for "+HYG_STALE_PR_DAYS+"+ days"),
    rule("Features PLDM-tested",
         feats.filter(f=>!f.pldm||f.pldm==="not-tested").length, feats.length,
         "never exercised on Palladium"),
    rule("Reviews answered in "+HYG_REVIEW_SLA_DAYS+"d",
         rvWaiting.filter(t=>{const d=daysSince(t.updated); return d!=null && d>HYG_REVIEW_SLA_DAYS;}).length,
         rvWaiting.length, "waiting on me past the turnaround target"),
    rule("WIP bound to a workspace",
         inProgress.filter(t=>!t.workspace).length, inProgress.length,
         "in-progress with no checkout assigned"),
  ].filter(r=>r.total>0);
}

function renderHygiene(){
  const rules=hygieneRules();
  const box=document.getElementById("ov-hygiene");
  if(!rules.length){
    document.getElementById("ov-hyg-score").innerHTML="";
    box.innerHTML='<div class="empty">Nothing in flight to measure.</div>';
    return;
  }
  const totBad=rules.reduce((a,r)=>a+r.bad,0), totAll=rules.reduce((a,r)=>a+r.total,0);
  const score=Math.round(100*(1-totBad/Math.max(1,totAll)));
  const sc=score>=85?PBI.green:score>=65?PBI.gold:PBI.red;
  document.getElementById("ov-hyg-score").innerHTML=
    svgGauge(score,"Pipeline clean",sc,totBad+" of "+totAll+" checks failing");

  box.innerHTML=rules.map(r=>{
    const pct=Math.round(100*r.bad/r.total);
    const c=r.bad===0?PBI.green:pct>=34?PBI.red:PBI.gold;
    return '<div class="hyg" style="--hc:'+c+'">'
      +'<div class="hyg-h"><span class="hyg-l">'+esc(r.label)+'</span>'
      +'<span class="hyg-n">'+(r.total-r.bad)+' / '+r.total+'</span></div>'
      +'<div class="hyg-bar"><i style="width:'+(100-pct)+'%"></i></div>'
      +'<div class="hyg-s">'+(r.bad?'<b>'+r.bad+'</b> '+esc(r.hint):"all clear")+'</div>'
      +'</div>';
  }).join("");
}

// ---- render ---------------------------------------------------------------
function renderOverview(){
  const bugs=bugList(), open=bugs.filter(b=>b.open);
  const attention=open.filter(bugNeedsAttention);
  const trend=ovIpTrend(12);
  const raid=((OVERVIEW&&OVERVIEW.raid)||[]).filter(r=>!r.closed);
  const raidHot=raid.filter(r=>["red","orange"].includes(r.rag));
  const rvRows=ovReviewRows();
  const rvTotal=rvRows.reduce((a,r)=>a+r.v,0);
  const rvOpen=rvRows.filter(r=>!["merged","approved"].includes(r.l)).reduce((a,r)=>a+r.v,0);
  const activeTasks=TASKS.filter(t=>!["done","cancelled"].includes(t.status));
  const prog=(OVERVIEW&&OVERVIEW.program)||{};
  const slots=(WORKSPACES&&WORKSPACES.slots)||{};
  const slotNames=Object.keys(slots);
  const busy=slotNames.filter(k=>slots[k]&&slots[k].holder).length;
  const today=isoDay(new Date());

  // --- KPI band ---
  const po=daysUntil(prog.power_on), a1=daysUntil(prog.a1_delivery);
  const kpi=o=>'<div class="kpi" style="--k:'+o.c+'">'
    +(o.spark?'<div class="spark">'+o.spark+'</div>':'')
    +'<div class="l">'+esc(o.label)+'</div>'
    +'<div class="n">'+o.n+(o.unit?'<small>'+esc(o.unit)+'</small>':'')+'</div>'
    +'<div class="s">'+(o.sub||"")+'</div>'
    +(o.pct==null?'':'<div class="bar"><i style="width:'+Math.max(0,Math.min(100,o.pct)).toFixed(0)+'%"></i></div>')
    +'</div>';
  // Program progress: how far into the window that ends at power-on, taking the
  // same-length run-up before it as the start of the window.
  const span=(po!=null)?100*(1-Math.max(0,po)/Math.max(1,po+60)):null;
  const openTrend=Object.values(trend.byIp).reduce((acc,v)=>
    v.map((x,i)=>x+(acc[i]||0)), []);
  document.getElementById("ov-kpis").innerHTML=[
    kpi({c:po!=null&&po<21?PBI.red:(po!=null&&po<45?PBI.orange:PBI.blue), label:"Power-on",
         n:po==null?"\u2014":po, unit:"days", pct:span,
         sub:esc(prog.power_on||"date unknown")+" \u00b7 A1 silicon"}),
    kpi({c:a1!=null&&a1<45?PBI.orange:PBI.navy, label:"A1 big delivery",
         n:a1==null?"\u2014":a1, unit:"days",
         sub:esc(prog.a1_delivery||"date unknown")+" \u00b7 end of MS8"}),
    kpi({c:attention.length?PBI.orange:PBI.green, label:"Open bugs", n:open.length,
         sub:attention.length+" need attention",
         pct:open.length?100*attention.length/open.length:0,
         spark:openTrend.length>1?svgSparkline(openTrend,
           attention.length?PBI.orange:PBI.green, 150, 44):""}),
    kpi({c:rvOpen>6?PBI.orange:PBI.teal, label:"Reviews on me", n:rvOpen,
         sub:rvTotal+" PR group"+(rvTotal===1?"":"s")+" in queue"}),
    kpi({c:raidHot.length?PBI.red:PBI.violet, label:"RAID open", n:raid.length,
         sub:raidHot.length+" red / orange",
         pct:raid.length?100*raidHot.length/raid.length:0}),
    kpi({c:PBI.purple, label:"Workspaces", n:busy+'<small>/'+slotNames.length+'</small>',
         sub:activeTasks.length+" active tasks",
         pct:slotNames.length?100*busy/slotNames.length:0}),
  ].join("");

  // --- report header + sprint slicer ---
  const curSprint=(prog.sprints||[]).find(s=>s.start&&s.end&&s.start<=today&&today<=s.end);
  document.getElementById("ov-asof").innerHTML=
    "As of <b>"+esc(today)+"</b> \u00b7 "+bugs.length+" bugs mirrored \u00b7 "
    +(curSprint?"sprint <b>"+esc(curSprint.name)+"</b>":"between sprints")
    +" \u00b7 "+(SERVED?"live":"static snapshot");
  document.getElementById("ov-sprints").innerHTML='<span class="sl-l">Sprint</span>'
    +(prog.sprints||[]).map(s=>{
      const now=s.start&&s.end&&s.start<=today&&today<=s.end;
      return '<div class="sprint'+(now?" now":"")+'">'+esc(s.name)
        +'<span class="w">'+esc(s.window)+'</span></div>';
    }).join("");

  // --- bug flow ---
  const flow=ovBugFlow();
  document.getElementById("ov-bugflow").innerHTML=svgStackedArea(flow.labels, flow.series);

  // --- backlog health gauges ---
  const gPct=(f)=>open.length?100*open.filter(f).length/open.length:0;
  const triaged=gPct(b=>(bugTri(b).state||"untriaged")!=="untriaged");
  const planned=gPct(b=>!(b.flags||[]).some(f=>f==="no-milestone"||f==="no-sprint"));
  const owned=gPct(b=>!!b.assignee);
  const gcol=p=>p>=80?PBI.green:p>=50?PBI.gold:PBI.red;
  document.getElementById("ov-gauges").innerHTML=open.length
    ? '<div style="display:grid;gap:10px;">'
      +svgGauge(triaged,"TRIAGED",gcol(triaged),
         Math.round(triaged*open.length/100)+" of "+open.length+" open bugs have left untriaged")
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">'
      +svgGauge(planned,"PLANNED",gcol(planned),"Has both a milestone and a sprint")
      +svgGauge(owned,"ASSIGNED",gcol(owned),"Has an assignee in Jira")
      +'</div></div>'
    : '<div class="empty">No open bugs.</div>';

  // --- mean time to close ---
  const ttc=ovCloseTime(6), ttcLive=ttc.filter(m=>m.n);
  const ttcAll=ttcLive.reduce((a,m)=>a+m.v*m.n,0)/Math.max(1,ttcLive.reduce((a,m)=>a+m.n,0));
  document.getElementById("ov-ttc").innerHTML=ttcLive.length
    ? svgBars(ttc.map(m=>({...m, c:m.v>ttcAll?"#f97316":"#2563eb"})), Math.round(ttcAll*10)/10, "d")
    : '<div class="empty">No bugs closed in the last 6 months.</div>';
  if(ttcLive.length){
    const last=ttcLive[ttcLive.length-1], prev=ttcLive[ttcLive.length-2];
    const dir=!prev ? "" : last.v<prev.v ? " \u2193 faster than "+prev.label
            : last.v>prev.v ? " \u2191 slower than "+prev.label : " \u2192 flat vs "+prev.label;
    document.getElementById("ov-ttc-cap").textContent=
      "Days from created to resolved, by close month. Dashed line = "
      +Math.round(ttcAll)+"d average." + dir + ". Bars above average are orange.";
  }

  // --- donuts ---
  const byIp={}; open.forEach(b=>{ const k=ipOf(b); byIp[k]=(byIp[k]||0)+1; });
  const ipSegs=Object.entries(byIp).sort((a,b)=>b[1]-a[1])
    .map((e,i)=>({l:e[0], v:e[1], c:PALETTE[i%PALETTE.length]}));
  document.getElementById("ov-bugip").innerHTML=svgDonut(ipSegs);
  document.getElementById("ov-bugip-lg").innerHTML=svgLegend(ipSegs, open.length);

  const byOwner={}; open.forEach(b=>{ const k=b.assignee||"Unassigned"; byOwner[k]=(byOwner[k]||0)+1; });
  const ownerSegs=Object.entries(byOwner).sort((a,b)=>b[1]-a[1])
    .map((e,i)=>({l:e[0], v:e[1], c:e[0]==="Unassigned"?"#9ca3af":PALETTE[i%PALETTE.length]}));
  document.getElementById("ov-bugowner").innerHTML=svgDonut(ownerSegs);
  document.getElementById("ov-bugowner-lg").innerHTML=svgLegend(ownerSegs, open.length);

  // --- per-IP scoreboard ---
  const rvByIp={};
  ovReviewTasks().forEach(t=>{
    const hay=((t.title||"")+" "+(t.description||"")+" "
      +prs(t).map(p=>p.repo||"").join(" ")).toLowerCase();
    const ip=ovIps().find(i=>(i.hints||[]).some(h=>hay.includes(String(h).toLowerCase())));
    const k=ip?ip.name:"Other"; rvByIp[k]=(rvByIp[k]||0)+1;
  });
  const rows=ovIps().map(i=>i.name).concat(["Other"]).map(name=>{
    const mine=open.filter(b=>ipOf(b)===name);
    const worst=mine.length?Math.min(...mine.map(b=>{
      const ix=SIGNALS.indexOf(b.signal); return ix<0?99:ix; })):99;
    return {name, owner:((ovIps().find(i=>i.name===name)||{}).owners)||"\u2014",
            n:mine.length, att:mine.filter(bugNeedsAttention).length,
            worst:worst<SIGNALS.length?SIGNALS[worst]:null,
            idle:mine.length?Math.max(...mine.map(b=>b.idle_days||0)):null,
            rv:rvByIp[name]||0};
  }).filter(r=>r.n||r.rv);
  document.getElementById("ov-scoreboard").innerHTML=rows.length
    ? '<table class="sb"><thead><tr><th>IP</th><th class="num">Bugs</th><th class="num">Attn</th>'
      +'<th class="num">Max idle</th><th class="num">Reviews</th><th>12-week trend</th></tr></thead><tbody>'
      +rows.map(r=>'<tr><td><b>'+esc(r.name)+'</b><div class="who">'+esc(r.owner)+'</div></td>'
        +'<td class="num">'+(r.n||"\u2014")+'</td>'
        +'<td class="num"'+(r.att?' style="color:'+PBI.orange+';"':'')+'>'+(r.att||"\u2014")+'</td>'
        +'<td class="num">'+(r.idle==null?"\u2014":r.idle+"d")+'</td>'
        +'<td class="num">'+(r.rv||"\u2014")+'</td>'
        +'<td style="width:130px;">'+svgSparkline(trend.byIp[r.name],
            SIGNAL_COLOR[r.worst]||"#9ca3af")+'</td></tr>').join("")
      +'</tbody></table>'
      +'<p class="cap" style="margin:8px 0 0;">Line = open bugs per week. '
      +'Colour = worst signal in that IP right now '
      +SIGNALS.filter(s=>s!=="closed").reverse().map(s=>SIGNAL_ICON[s]+" "+s).join(" \u00b7 ")+'.</p>'
    : '<div class="empty">No open bugs or reviews.</div>';

  // --- hygiene flags ---
  const fc={}; open.forEach(b=>(b.flags||[]).forEach(f=>{ fc[f]=(fc[f]||0)+1; }));
  const flagColor={untriaged:PBI.red, unassigned:PBI.red, overdue:PBI.red,
    pinged:PBI.orange,
    "no-milestone":PBI.gold, "no-sprint":PBI.gold, "no-estimate":PBI.gold,
    aging:PBI.purple, "no-discussion":PBI.grey};
  const frows=Object.entries(fc).sort((a,b)=>b[1]-a[1]).map(e=>({
    l:e[0], v:e[1], c:flagColor[e[0]]||"#6b7280",
    sub:Math.round(100*e[1]/Math.max(1,open.length))+"% of open bugs"}));
  document.getElementById("ov-flags").innerHTML=barTable(frows, open.length);

  // --- review queue ---
  document.getElementById("ov-reviews").innerHTML=
    barTable(rvRows, Math.max.apply(null, rvRows.map(r=>r.v).concat([1])));

  renderHygiene();

  // --- RAID ---
  const raidSorted=raid.slice().sort((a,b)=>
    ((RAG_RANK[a.rag]==null?9:RAG_RANK[a.rag])-(RAG_RANK[b.rag]==null?9:RAG_RANK[b.rag]))
    || (b.score-a.score));
  document.getElementById("ov-raid").innerHTML=raidSorted.length
    ? '<table class="sb"><thead><tr><th>Row</th><th>Owner</th><th class="num">Score</th>'
      +'<th class="num">Due</th></tr></thead><tbody>'
      +raidSorted.slice(0,8).map(r=>'<tr>'
        +'<td><span class="rag '+esc(r.rag||"gray")+'"></span><b>'+esc(r.id)+'</b>'
        +'<div class="who" style="white-space:normal;">'+esc(r.title)+'</div></td>'
        +'<td>'+esc(r.owner||"\u2014")+'</td>'
        +'<td class="num">'+(r.score||"\u2014")+'</td>'
        +'<td class="num"'+((r.due&&r.due<today)?' style="color:'+PBI.red+';"':'')+'>'
        +esc(r.due||"\u2014")+'</td></tr>').join("")
      +'</tbody></table>'
      +(raidSorted.length>8?'<div class="ro-note" style="margin-top:8px;">+'+(raidSorted.length-8)+' more open rows</div>':'')
    : '<div class="empty">No open RAID rows.</div>';

  // --- needs you now ---
  const feed=[];
  attention.forEach(b=>{
    const why=[], pings=b.oded_pings||0;
    if(pings) why.push("Oded pinged \u00d7"+pings+(b.last_ping?" (last "+b.last_ping+")":""));
    if(["stalled","dormant"].includes(b.signal)) why.push(b.signal+" \u2014 "+b.idle_days+"d idle");
    if((bugTri(b).state||"untriaged")==="untriaged") why.push("untriaged");
    if(!b.assignee) why.push("unassigned");
    feed.push({rank:((b.flags||[]).includes("pinged")||["dormant","stalled"].includes(b.signal))?0:1,
      kind:"bug", label:b.key,
      text:b.summary||"", why:why.join(" \u00b7 "),
      when:(b.idle_days==null?"":b.idle_days+"d idle"), href:b.url, sortv:b.idle_days||0});
  });
  // RAID is capped so a long-standing risk backlog can't crowd out live bugs.
  raidSorted.filter(r=>r.rag==="red"||(r.due&&r.due<today)).slice(0,3).forEach(r=>feed.push({
    rank:r.rag==="red"?0:2, kind:"raid", label:r.id, text:r.title||"",
    why:((r.due&&r.due<today)?"overdue since "+r.due:"red risk")+" \u00b7 "+(r.owner||"unowned"),
    when:r.score?"score "+r.score:"", sortv:r.score||0}));
  ovReviewTasks().forEach(t=>{
    const p=prs(t); if(!p.length) return;
    const s=consolidateReviewStatus(p);
    if(!["pending","in-progress","waiting-for-re-review"].includes(s)) return;
    const age=t.created?daysSince(t.created):0;
    feed.push({rank:s==="pending"?3:2, kind:"review", label:t.id,
      text:(t.title||"").slice(0,110), why:s+" \u00b7 "+p.length+" PR"+(p.length>1?"s":""),
      when:age?age+"d old":"", sortv:age});
  });
  TASKS.filter(t=>t.status==="blocked").forEach(t=>feed.push({
    rank:2, kind:"task", label:t.id, text:t.title||"", why:"blocked",
    when:"", sortv:0}));
  feed.sort((a,b)=>(a.rank-b.rank)||(b.sortv-a.sortv));
  const feedColor={bug:PBI.red, raid:PBI.orange, review:PBI.blue, task:PBI.purple};
  document.getElementById("ov-feed").innerHTML=feed.length
    ? '<div class="feed">'+feed.slice(0,12).map(f=>'<div class="it" style="--fk:'+feedColor[f.kind]+'">'
        +'<span class="pill">'+f.kind+'</span>'
        +'<span class="w"><b>'+(f.href?'<a href="'+esc(f.href)+'" target="_blank" rel="noopener">'+esc(f.label)+'</a>':esc(f.label))+'</b> '
        +esc((f.text||"").slice(0,110))
        +'<div class="why">'+esc(f.why)+'</div></span>'
        +'<span class="when">'+esc(f.when)+'</span></div>').join("")+'</div>'
      +(feed.length>12?'<div class="ro-note" style="margin-top:9px;">+'+(feed.length-12)+' more</div>':'')
    : '<div class="empty">Nothing is screaming for attention.</div>';

  // --- workspaces ---
  document.getElementById("ov-slots").innerHTML=slotNames.map(name=>{
    const h=slots[name]&&slots[name].holder;
    const who=h?(h.type==="task"?(h.task_id+" \u00b7 "+(h.detail||h.tag||"")):(h.label||h.tag||"ad-hoc")):"free";
    return '<div class="slot'+(h?" busy":"")+'"><div class="nm">'+esc(name)+'</div>'
      +'<div class="oc" title="'+esc(who)+'">'+esc(who)+'</div></div>';
  }).join("");
}

// ============================================================================
// Portal section nav — Overview / My Work / Workspaces / Bugs / PRs / Team
// ============================================================================
const SECTIONS=[
  {id:"overview", label:"Overview",   ready:true},
  {id:"work",     label:"My Work",    ready:true},
  {id:"ws",       label:"Workspaces", ready:true},
  {id:"bugs",     label:"Bugs",       ready:true},
  {id:"prs",      label:"PRs",        ready:true},
  {id:"pldmq",    label:"PLDM",       ready:true},
  {id:"vm",       label:"VM jobs",    ready:true},
  {id:"raid",     label:"RAID",       ready:true},
  {id:"team",     label:"Team",       ready:false},
];
let activeSection="overview";
function sectionCount(id){
  if(id==="overview"){
    const raid=((OVERVIEW&&OVERVIEW.raid)||[]).filter(r=>!r.closed&&["red","orange"].includes(r.rag));
    return bugList().filter(b=>bugNeedsAttention(b)).length + raid.length;
  }
  if(id==="work") return TASKS.filter(t=>!["done","cancelled"].includes(t.status)).length;
  if(id==="ws") return wsRoster().filter(s=>wsHolder(s.slot)).length;
  if(id==="bugs") return bugList().filter(b=>bugNeedsAttention(b)).length;
  if(id==="prs") return (PRS.features||[]).filter(f=>(f.blocked||[]).length).length;
  if(id==="pldmq") return (PLDMQ.jobs||[]).filter(j=>j.active).length;
  if(id==="vm") return (VMLOG.running||[]).length;
  if(id==="raid") return ((RAID.counts||{}).hot)||0;
  return 0;
}
function buildSections(){
  const box=document.getElementById("sections");
  box.innerHTML=SECTIONS.map(s=>{
    const n=sectionCount(s.id);
    const badge = s.ready ? `<span class="badge-count">${n}</span>`
                          : `<span class="badge-count">soon</span>`;
    const title = s.id==="bugs" ? "Bugs needing attention (stalled, untriaged, or unassigned)"
                : s.id==="work" ? "Active tasks"
                : s.id==="ws" ? "Tracked workspace slots that are currently occupied"
                : s.id==="prs" ? "My outgoing feature PRs that are blocked (CI, review or rebase)"
                : s.id==="pldmq" ? "PLDM hardware jobs still building, queued or running"
                : s.id==="vm" ? "Jobs delegated to a VM or lab machine that are still running"
                : s.id==="raid" ? "Open RAID rows at Red or Orange"
                : s.id==="overview" ? "Things demanding attention right now (bugs + hot RAID)"
                : "Team task monitoring — not built yet";
    return `<button class="section-btn${s.id===activeSection?" on":""}" data-section="${s.id}"
      title="${esc(title)}"${s.ready?"":" disabled"}>${esc(s.label)}${badge}</button>`;
  }).join("");
  box.querySelectorAll(".section-btn").forEach(b=>
    b.addEventListener("click",()=>selectSection(b.dataset.section)));
}
function selectSection(id){
  const sec=SECTIONS.find(s=>s.id===id);
  if(!sec || !sec.ready) return;
  activeSection=id;
  document.querySelectorAll(".section-btn").forEach(b=>b.classList.toggle("on",b.dataset.section===id));
  SECTIONS.forEach(s=>{
    const el=document.getElementById("sec-"+s.id);
    if(el) el.style.display = s.id===id ? "" : "none";
  });
  if(id==="bugs"){ buildBugFilters(); buildBugTabs(); selectBugTab(bugTab); }
  else if(id==="work"){ render(); }
  else if(id==="ws"){ renderWorkspaces(); }
  else if(id==="prs"){ renderPrs(); }
  else if(id==="pldmq"){ renderPldmq(); }
  else if(id==="vm"){ renderVmlog(); }
  else if(id==="raid"){ renderRaid(); }
  else if(id==="overview"){ renderOverview(); }
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
const PLDM_COLOR={passed:"#1AAB40", failed:"#D64550", running:"#118DFF",
  queued:"#D9B300", cancelled:"#9B9B9B"};

// PLDM Job Queue live dashboard (Grafana). Deep-linked to the iridium project,
// last 2 days — enough to spot the run you just dispatched by user/display name.
const GRAFANA_PLDM="https://grafana.elmnt.local/d/pldm-job-queue-v1/pldm-job-queue"
  +"?orgId=1&from=now-2d&to=now&timezone=browser&var-project=iridium"
  +"&var-artifact=All&var-image_type=All&var-status=All&refresh=30s";
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
  const n=s=>counts[s]||0;
  const totalPrs=feats.reduce((a,f)=>a+f.total,0);
  const blocked=feats.filter(f=>(f.blocked||[]).length);
  const done=feats.filter(f=>f.total&&f.merged===f.total);
  document.getElementById("pr-asof").innerHTML=
    feats.length+" active feature"+(feats.length===1?"":"s")+" \u00b7 "+totalPrs+" PRs \u00b7 "
    +(PRS.last_scan?"scanned <b>"+esc(PRS.last_scan)+"</b>":"<b>never scanned</b>")
    +(PRS.error?' \u00b7 <span style="color:'+PBI.red+'">'+esc(PRS.error)+'</span>':"");

  document.getElementById("pr-kpis").innerHTML=[    kpi({c:blocked.length?PBI.red:PBI.green, label:"Blocked features", n:blocked.length,
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

  document.getElementById("pr-list").innerHTML=feats.length
    ? feats.map(prFeature).join("")
    : '<div class="empty">Nothing tracked. Log a feature with the pr-monitor skill.</div>';
  wirePrCollapse();
}

// Collapsed features keep their PR table in the DOM (hidden) so the CI buttons
// stay wired; only the id set is state.
const PR_COLLAPSED=new Set();

function prCollapsed(id){ return PR_COLLAPSED.has(id); }

function setPrCollapsed(id, on){
  if(on) PR_COLLAPSED.add(id); else PR_COLLAPSED.delete(id);
  const card=document.querySelector('.prf[data-fid="'+CSS.escape(id)+'"]');
  if(!card) return;
  card.classList.toggle("collapsed", on);
  const rows=card.querySelector(".prf-rows"); if(rows) rows.hidden=on;
  const btn=card.querySelector(".prf-toggle");
  if(btn){ btn.textContent=on?"\u25b8":"\u25be"; btn.title=on?"Expand PR rows":"Collapse PR rows"; }
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
  document.querySelectorAll(".prf-toggle").forEach(b=>b.addEventListener("click",()=>{
    const id=b.closest(".prf").dataset.fid;
    setPrCollapsed(id, !prCollapsed(id));
  }));
  document.querySelectorAll(".prf header h3").forEach(h=>h.addEventListener("click",()=>{
    const id=h.closest(".prf").dataset.fid;
    setPrCollapsed(id, !prCollapsed(id));
  }));
  syncPrCollapseAll();
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

function prFeature(f){
  const c=PR_STATUS_COLOR[f.worst]||PBI.grey;
  const complete=f.total&&f.merged===f.total;
  const rows=f.prs.map(p=>{
    const sc=PR_STATUS_COLOR[p.status]||PBI.grey;
    // A failing check count is the actionable bit; everything else is noise.
    let detail="\u2014";
    if(p.checks_fail) detail='<b>'+p.checks_fail+'F</b>/'+(p.checks_pass||0)+'P';
    else if(p.checks_pass) detail=p.checks_pass+'P'+(p.checks_pending?' \u00b7 '+p.checks_pending+' run':'');
    const ci=ciCell(p);
    const gate=p.status==="merged"?"\u2014"
      :p.status==="merge-queue"?"in queue"
      :p.auto_merge?"auto-merge"
      :(p.merge_state==="BEHIND"||p.merge_state==="DIRTY")?(p.merge_state||"").toLowerCase()
      :(p.review||"").toLowerCase().replace(/_/g," ")||"\u2014";
    // A `/ci` comment is the only way CI runs here, so every open PR gets a
    // one-click re-run. Merged/closed PRs can't be re-triggered.
    const canCi=SERVED&&p.status!=="merged"&&p.status!=="closed";
    const ciBtn=canCi
      ? ' <button class="ci-btn" data-ci-one="'+f.id+'" data-ci-num="'+p.number+'" '
        +'title="Post a /ci comment to (re-)run CI on this PR">\u21bb ci</button>' : "";
    const rmBtn=SERVED
      ? ' <button class="pr-x" data-prx="'+f.id+'" data-prx-num="'+p.number+'" '
        +'data-prx-repo="'+esc(p.repo)+'" title="Stop tracking this PR \u2014 the PR '
        +'itself is untouched">\u2715</button>' : "";
    return '<tr><td><a href="'+esc(p.url||"#")+'" target="_blank" rel="noopener" title="'+esc(p.title||"")+'">'
      +esc(p.repo)+' #'+p.number+'</a>'+ciBtn+rmBtn+'</td>'
      +'<td title="'+esc(p.title||"")+'">'+esc(p.title||"")+'</td>'
      +'<td>'+ci+'</td>'
      +'<td class="chk">'+detail+'</td>'
      +'<td class="chk">'+esc(gate)+'</td>'
      +'<td><span class="st" style="--sk:'+sc+'">'+esc(p.status||"?")+'</span></td></tr>';
  }).join("");
  const pct=f.total?100*f.merged/f.total:0;
  const note=f.last_note
    ? '<div class="note"><b>'+esc(f.last_note.date)+'</b> \u2014 '+esc(f.last_note.text)+'</div>' : "";
  const pldm=pldmBadge(f);
  const testable=SERVED&&f.prs.some(p=>p.status!=="closed");
  const btn=testable
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
      +'title="Drop this feature from active tracking">\u2715</button>' : "";
  const collapsed=prCollapsed(f.id);
  return '<div class="prf'+(collapsed?" collapsed":"")+'" data-fid="'+esc(f.id)+'" style="--fk:'+c+'">'
    +'<header><button class="prf-toggle" title="'+(collapsed?"Expand":"Collapse")+' PR rows">'
    +(collapsed?"\u25b8":"\u25be")+'</button>'
    +'<span class="fid">['+f.id+']</span><h3>'+esc(f.name)+'</h3>'
    +'<span class="prf-count" title="PRs in this feature">'+f.merged+'/'+f.total+'</span>'
    +ciAll+ciFail+addBtn+btn+pldm+'<span class="st" style="--sk:'+c+'">'+esc(complete?"complete":f.worst)+'</span>'
    +dropBtn+'</header>'
    +(f.description?'<p class="desc">'+esc(f.description)+'</p>':"")
    +'<div class="prog"><i style="width:'+pct.toFixed(0)+'%"></i></div>'
    +'<div class="ro-note">'+f.merged+' of '+f.total+' merged'
    +(complete?' \u2014 retires on the next scan':'')+'</div>'
    +'<div class="ci-note" id="ci-note-'+f.id+'"></div>'
    +'<div class="prf-rows"'+(collapsed?" hidden":"")+'>'
    +'<table class="pr-tbl"><thead><tr><th style="width:23%">Repo / PR</th><th style="width:31%">Title</th>'
    +'<th style="width:10%">CI</th><th style="width:10%">Checks</th>'
    +'<th style="width:12%">Gate</th><th style="width:14%">Status</th>'
    +'</tr></thead><tbody>'+rows+'</tbody></table></div>'
    +'<div class="pldm-box" id="pldm-box-'+f.id+'" hidden></div>'
    +note+'</div>';
}

// ---------------- PLDM submission ----------------
// Remote mode builds from a workspace manifest, so submitting is a checkout +
// preflight + dispatch chain rather than a single call — hence the progress log.
const PLDM_IMAGES=["1die_w_gmem","2die_w_gmem","1die_wo_gmem","4die_w_gmem"];

function allSlots(){
  // Only number-named dirs are real multi-repo checkouts; the server rejects
  // the rest, so offering them here would just be a trap.
  const canon=(WORKSPACES&&WORKSPACES.canonical)||[];
  return canon.length?[...canon].sort():wsRoster().map(r=>r.slot);
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
    const head=j.status==="running"
      ? '<b>'+esc(PHASE[j.phase]||j.phase||"working")+'</b>'
        +(j.current?' \u00b7 '+esc(j.current):"")
      : (j.status==="done"?'<b class="ok">Queued.</b> '+esc(j.message||"")
          +(j.run_url?' \u00b7 <a href="'+esc(j.run_url)+'" target="_blank" rel="noopener">Actions run</a>':"")
        : '<b class="bad">Failed.</b> '+esc(j.message||""));
    box.innerHTML='<div class="pldm-prog">'+head+'</div>'+log
      +(j.status==="running"?"":'<div class="pldm-act">'
        +'<button class="cancel" data-cancel="'+id+'">Close</button></div>');
    if(j.status!=="running"){
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

// The scan runs `gh` server-side — no model call, so it is free to hammer.
async function scanPrs(){
  const btn=document.getElementById("pr-scan"), note=document.getElementById("pr-scan-note");
  if(!SERVED){ note.textContent="Serve the portal to scan"; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Scanning\u2026';
  note.textContent="probing GitHub via gh\u2026";
  try{
    const r=await fetch("/api/pr-scan",{method:"POST"});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"scan failed");
    const job=await pollAlign(j.job,"Scanning PRs…");
    if(!job||job.status==="error") throw new Error((job&&job.message)||"scan failed");
    const res=job.result||{};
    PRS=res; renderPrs(); buildSections();
    const gone=res.purged||[];
    note.innerHTML="probed "+(res.scanned||0)+" PRs"
      +(gone.length?' \u00b7 <b>retired '+gone.length+'</b>: '
        +gone.map(f=>esc(f.name)+" ("+f.total+"/"+f.total+")").join(", "):"");
  }catch(e){
    note.textContent="Scan failed: "+e.message;
  }finally{
    btn.disabled=false; btn.textContent="\u21bb Scan PRs";
  }
}
document.getElementById("pr-scan").addEventListener("click",scanPrs);
document.getElementById("pr-collapse").addEventListener("click",e=>{
  const expand=!!e.currentTarget.dataset.expand;
  (PRS.features||[]).forEach(f=>setPrCollapsed(f.id, !expand));
});

// ---------------- feature / PR tracking ----------------
// Every mutation returns the whole refreshed payload, so the section re-renders
// from the server instead of from a guess about what changed.
async function prPost(path, body){
  if(!SERVED) throw new Error("Read-only \u2014 run tm.py serve to edit tracking");
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body||{})});
  const j=await r.json();
  if(!r.ok||j.error) throw new Error(j.error||("Error "+r.status));
  if(j.prs){ PRS=j.prs; renderPrs(); buildSections(); }
  return j;
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
  if(!confirm("Drop ["+fid+"] "+name+" from active tracking?\n\nThe memoir entry is kept "
    +"and annotated DROPPED \u2014 nothing is deleted.")) return;
  try{ prNote((await prPost("/api/feature-untrack",{feature_id:fid})).message||""); }
  catch(e){ prNote("Failed: "+e.message); }
}

document.addEventListener("click",e=>{
  const t=e.target.closest("[data-prtrack],[data-prdrop],[data-prx]");
  if(!t) return;
  if(t.dataset.prtrack!=null) prAddDialog(parseInt(t.dataset.prtrack,10));
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

/* ===== PLDM job queue section ===== */
const PQ_STATUS_COLOR={passed:"#1AAB40", failed:"#D64550", running:"#118DFF",
  queued:"#D9B300", building:"#197278", cancelled:"#9B9B9B", error:"#E66C37",
  pending:"#9B9B9B"};
// The stage is the useful progress signal: a job sits in `queued` for hours
// waiting for an EDK, which is normal and must not read as a stall.
const PQ_STAGES=["dispatching","building","queued","running","done"];

function pqStageBar(j){
  const i=Math.max(0,PQ_STAGES.indexOf(j.stage));
  const c=PQ_STATUS_COLOR[j.status]||PBI.grey;
  return '<div class="pq-stage" title="'+esc(j.note||"")+'">'
    +PQ_STAGES.map((s,k)=>'<i class="'+(k<=i?"on":"")+'" style="--pk:'+c+'" title="'+esc(s)+'"></i>').join("")
    +'<span>'+esc(j.stage)+'</span></div>';
}

function renderPldmq(){
  const jobs=PLDMQ.jobs||[], counts=PLDMQ.counts||{};
  const n=s=>counts[s]||0;
  const active=jobs.filter(j=>j.active);
  document.getElementById("pq-portal").href=PLDMQ.portal||"#";
  document.getElementById("pq-asof").innerHTML=
    jobs.length+" tracked job"+(jobs.length===1?"":"s")+" \u00b7 "+active.length+" in flight \u00b7 "
    +(PLDMQ.last_scan?"scanned <b>"+esc(PLDMQ.last_scan)+"</b>":"<b>never scanned</b>")
    +(PLDMQ.error?' \u00b7 <span style="color:'+PBI.red+'">'+esc(PLDMQ.error)+'</span>':"");

  document.getElementById("pq-kpis").innerHTML=[
    kpi({c:active.length?PBI.blue:PBI.grey, label:"In flight", n:active.length,
         sub:"of "+jobs.length+" tracked"}),
    kpi({c:n("building")?PBI.teal:PBI.grey, label:"Building", n:n("building"), sub:"CI build"}),
    kpi({c:n("queued")?PBI.gold:PBI.grey, label:"Queued", n:n("queued"), sub:"waiting for an EDK"}),
    kpi({c:n("running")?PBI.blue:PBI.grey, label:"Running", n:n("running"), sub:"on hardware"}),
    kpi({c:PBI.green, label:"Passed", n:n("passed"), sub:"clean runs"}),
    kpi({c:(n("failed")+n("error"))?PBI.red:PBI.grey, label:"Failed", n:n("failed")+n("error"),
         sub:n("error")?n("error")+" never reached HW":"on hardware"}),
  ].join("");

  document.getElementById("pq-body").innerHTML=jobs.length
    ? jobs.map(pqRow).join("")
    : '<tr><td colspan="8"><div class="empty">No PLDM jobs tracked. '
      +'Submit one from a feature card in PRs, or with <code>pjq.py submit</code>.</div></td></tr>';
}

function pqRow(j){
  const c=PQ_STATUS_COLOR[j.status]||PBI.grey;
  const res=j.result||{};
  const links=[
    j.dispatch_url?'<a href="'+esc(j.dispatch_url)+'" target="_blank" rel="noopener" title="CI build run">build</a>':"",
    j.exec_url?'<a href="'+esc(j.exec_url)+'" target="_blank" rel="noopener" title="Hardware execution run">run</a>':"",
  ].filter(Boolean).join(" \u00b7 ")||"\u2014";
  const age=j.age_h>=24?Math.round(j.age_h/24)+"d":j.age_h+"h";
  let detail="";
  if(res.summary||(res.lines||[]).length){
    const n=(res.lines||[]).length;
    detail='<tr class="pq-detail"><td colspan="8"><details>'
      +'<summary>'+(res.summary?esc(res.summary):"failure evidence")
      +' <span>('+n+' log line'+(n===1?"":"s")+')</span></summary>'
      +((res.failed_steps||[]).length?'<div class="pq-step">failed step: '
        +esc(res.failed_steps.join(", "))+'</div>':"")
      +'<pre>'+esc((res.lines||[]).join("\n"))+'</pre></details></td></tr>';
  }
  return '<tr class="task"><td title="'+esc(j.note||"")+'"><b>'+esc(j.display_name)+'</b></td>'
    +'<td>'+esc(j.feature||"\u2014")+'</td>'
    +'<td>'+esc(j.image_type||"\u2014")+'</td>'
    +'<td>'+pqStageBar(j)+'</td>'
    +'<td><span class="st" style="--sk:'+c+'">'+esc(j.status)+'</span></td>'
    +'<td>'+esc(j.edk||(j.queue_job_id?"queue #"+j.queue_job_id:"\u2014"))+'</td>'
    +'<td>'+esc(age)+'</td><td>'+links+'</td></tr>'+detail;
}

// Follows each job through gh server-side; like the PR scan, it costs nothing.
async function scanPldmq(){
  const btn=document.getElementById("pq-scan"), note=document.getElementById("pq-scan-note");
  if(!SERVED){ note.textContent="Serve the portal to scan"; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Scanning\u2026';
  note.textContent="following jobs through GitHub\u2026";
  try{
    const r=await fetch("/api/pldm-scan",{method:"POST"});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"scan failed");
    PLDMQ=j; renderPldmq(); buildSections();
    note.textContent="probed "+(j.scanned||0)+" active job(s)";
  }catch(e){
    note.textContent="Scan failed: "+e.message;
  }finally{
    btn.disabled=false; btn.innerHTML="\u21bb Scan queue";
  }
}
document.getElementById("pq-scan").addEventListener("click",scanPldmq);

// ============================================================================
// VM delegator log — every job `mctl` dispatched to a VM / lab machine.
// The remote keeps only the latest log per (workspace, kind), so the history
// lives in ~/.mctl/joblog.jsonl and the status comes from one ssh probe.
// ============================================================================
const VM_STATUS_COLOR={running:PBI.blue, passed:PBI.green, failed:PBI.red, died:PBI.orange,
                       superseded:PBI.grey, gone:PBI.grey, unknown:PBI.grey};
const VM_STATUS_TITLE={
  running:"still executing on the target",
  passed:"finished with rc=0",
  failed:"finished with a non-zero rc",
  died:"the process vanished without writing a completion marker",
  superseded:"a newer run overwrote this log on the target",
  gone:"no log left on the target (cleaned, or it never started)",
  unknown:"never refreshed \u2014 hit Refresh status",
};
let vmTarget="";
function vmJobs(){
  let js=(VMLOG.jobs||[]).slice();
  if(vmTarget) js=js.filter(j=>j.target===vmTarget);
  const live=document.getElementById("vm-livefirst");
  if(!live||live.checked){
    const rank=j=>j.status==="running"?0:1;
    js.sort((a,b)=>rank(a)-rank(b)||String(b.started).localeCompare(String(a.started)));
  }
  return js;
}
function vmLiveCard(j){
  const mins=Math.floor((j.duration||0)/60), secs=(j.duration||0)%60;
  const el=mins?mins+"m "+String(secs).padStart(2,"0")+"s":secs+"s";
  return '<div class="vm-live"><div class="vm-live-l">'
    +'<div class="vm-live-t"><span class="spin">\u21bb</span> Running now \u00b7 '+esc(j.target)
    +' \u00b7 '+esc(j.kind)+'</div>'
    +'<h4>'+esc(j.ws_label||j.tag)+'</h4>'
    +(j.cmd?'<code>'+esc(j.cmd)+'</code>':"")
    +'<div class="vm-live-m">started '+esc(j.started)
    +(j.pid?' \u00b7 pid '+j.pid:"")
    +(j.lines?' \u00b7 '+j.lines+' log lines':"")
    +(j.errors?' \u00b7 <b style="color:'+PBI.red+'">'+j.errors+' error line(s)</b>':"")+'</div>'
    +(j.last_line?'<span class="vm-tail">'+esc(j.last_line)+'</span>':"")
    +'<button class="vm-log live" data-target="'+esc(j.target)+'" data-tag="'+esc(j.tag)+'"'
    +' data-kind="'+esc(j.kind)+'" data-live="1"'
    +' title="Open a live tail of this job\u2019s log over ssh">'
    +'<span class="spin">\u21bb</span> Tail the live log</button>'
    +'</div><div class="vm-live-r"><b>'+esc(el)+'</b>elapsed</div></div>';
}
function renderVmlog(){
  const all=VMLOG.jobs||[], counts=VMLOG.counts||{}, n=s=>counts[s]||0;
  const running=VMLOG.running||[];

  const sel=document.getElementById("vm-target");
  const tgts=[...new Set(all.map(j=>j.target))].sort();
  sel.innerHTML='<option value="">All targets</option>'
    +tgts.map(t=>'<option value="'+esc(t)+'"'+(t===vmTarget?" selected":"")+'>'+esc(t)
      +' ('+all.filter(j=>j.target===t).length+')</option>').join("");

  document.getElementById("vm-asof").innerHTML=
    all.length+" delegated job"+(all.length===1?"":"s")+" \u00b7 "+running.length+" running \u00b7 "
    +(VMLOG.last_refresh
      ? "probed <b>"+esc(VMLOG.last_refresh)+"</b>"
        +(VMLOG.refresh_age_h?' <span class="vm-age'+(vmStale(VMLOG.refresh_age_h)?" old":"")
          +'">'+esc(VMLOG.refresh_age_h)+" ago</span>":"")
      : "<b>never probed</b>")
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
    kpi({c:n("died")?PBI.orange:PBI.grey, label:"Died", n:n("died"), sub:"no completion marker"}),
    kpi({c:PBI.grey, label:"Targets", n:tgts.length,
         sub:(VMLOG.targets||[]).length+" registered"}),
  ].join("");

  const js=vmJobs();
  document.getElementById("vm-body").innerHTML=js.length
    ? js.map(vmRow).join("")
    : '<tr><td colspan="8"><div class="empty">No delegated jobs recorded yet. '
      +'Every <code>mctl build</code> / <code>mctl test</code> lands here.</div></td></tr>';
  vmBindLogs();
}
// Every "open / tail" control, in the table and on the live cards alike.
function vmBindLogs(){
  document.querySelectorAll(".vm-log").forEach(b=>b.addEventListener("click",()=>
    vmLogOpen(b.dataset.target, b.dataset.tag, b.dataset.kind, b.dataset.live==="1")));
}
// A reading older than an hour is history, not status. Saying so is the whole
// point: a day-old "stopped" that looked live is why the VM seemed unusable.
function vmStale(h){ return /\d+\s*h/.test(h||""); }
// EC2 power state for each delegation target, straight from `octl vm list`.
function renderVmMachines(){
  const ms=VMLOG.machines||[];
  const box=document.getElementById("vm-machines");
  if(!ms.length){
    box.innerHTML='<div class="vm-idle">No EC2 instance visible. Hit <b>Check VM status</b> to '
      +'query <code>octl vm list</code> \u2014 it revalidates SSO, so it takes a moment.</div>';
    return;
  }
  const stale=vmStale(VMLOG.machines_age_h);
  box.innerHTML='<div class="vm-mach">'+ms.map(vmMachine).join("")+'</div>'
    +'<div class="vm-mage'+(stale?" old":"")+'">'
    +(stale?"\u26a0 ":"")+"power state read "+esc(VMLOG.machines_checked||"?")
    +(VMLOG.machines_age_h?" \u00b7 "+esc(VMLOG.machines_age_h)+" ago":"")
    +(stale?" \u2014 hit <b>Check VM status</b>, this is history":"")+'</div>';
  box.querySelectorAll(".vm-pw").forEach(b=>b.addEventListener("click",()=>
    vmPower(b.dataset.action, b.dataset.id, b)));
}
function vmMachine(m){
  const on=m.state==="running", busy=["pending","stopping","shutting-down"].includes(m.state);
  const act=on?"stop":"start";
  const lease=m.lease_left_h
    ? '<span class="vm-lease'+(m.lease_left<=0?" bad":"")+'">lease '+esc(m.lease_left_h)
      +(m.lease_left>0?" left":"")+'</span>' : "";
  return '<div class="vm-m"><div class="vm-m-l">'
    +'<h4><span class="vm-dot '+esc(m.state)+'"></span>'+esc(m.name)
    +'<span class="vm-m-st">'+esc(m.state)+'</span></h4>'
    +'<div class="vm-m-sub">'+esc(m.type)+(m.ip?' \u00b7 '+esc(m.ip):"")
    +(m.target?' \u00b7 target <b>'+esc(m.target)+'</b>':"")
    +(m.desc?' \u00b7 '+esc(m.desc):"")+'</div>'
    +(lease?'<div class="vm-m-sub">'+lease+'</div>':"")
    +'</div>'
    +'<button class="vm-pw '+act+'" data-action="'+act+'" data-id="'+esc(m.id)+'"'
    +(busy?" disabled":"")+' title="octl vm '+act+' '+esc(m.id)+'">'
    +(busy?esc(m.state):(on?"\u25a0 Stop":"\u25b6 Start"))+'</button></div>';
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
    VMLOG=j; renderVmlog(); buildSections();
    note.textContent=action+" requested \u00b7 state follows in a minute";
  }catch(e){
    note.textContent=action+" failed: "+e.message;
    document.querySelectorAll(".vm-pw").forEach(b=>b.disabled=false);
  }
}
function vmRow(j){
  const c=VM_STATUS_COLOR[j.status]||PBI.grey;
  const rc=(j.rc===null||j.rc===undefined)?"":" rc="+j.rc;
  const tail=j.last_line&&j.status!=="running"
    ? '<span class="vm-tail" title="'+esc(j.last_line)+'">'+esc(j.last_line)+'</span>':"";
  return '<tr class="task'+(j.status==="running"?" on":"")+'">'
    +'<td title="'+esc(j.dest||"")+'">'+esc(j.target)+'</td>'
    +'<td>'+esc(j.kind)+'</td>'
    +'<td class="vm-ws"><b>'+esc(j.ws_label||j.tag)+'</b>'
      +(j.adopted?'<span>found on target \u00b7 not dispatched from here</span>':"")+'</td>'
    +'<td class="vm-cmd">'+(j.cmd?'<code>'+esc(j.cmd)+'</code>'
      :'<span class="muted" title="This job predates the dispatch log, so the command was '
       +'never recorded. Jobs delegated from now on carry it.">not recorded</span>')+'</td>'
    +'<td>'+esc(j.started||"\u2014")+'</td>'
    +'<td>'+esc(j.duration_h||"\u2014")+'</td>'
    +'<td title="'+esc((VM_STATUS_TITLE[j.status]||"")+(j.note?" \u2014 "+j.note:""))+'">'
      +'<span class="vm-st '+esc(j.status)+'" style="--sk:'+c+'">'+esc(j.status)+esc(rc)+'</span>'+tail+'</td>'
    +'<td class="vm-logcell">'+vmLogLink(j)+'</td></tr>';
}
// The Log cell. A running job gets a live tail, a settled one the stored copy.
// Either way the click opens the same panel; only the polling differs.
function vmLogLink(j){
  const live=j.status==="running";
  const meta=(j.lines?j.lines+" ln":"")
    +(j.errors?' <b style="color:'+PBI.red+'">'+j.errors+'\u2715</b>':"");
  if(!live&&!j.has_log){
    return '<span class="muted" title="No local copy held. Hit Sync logs \u2014 the target '
      +'keeps only the newest log per workspace and overwrites it on the next run.">'
      +(meta||"\u2014")+'</span>';
  }
  return '<button class="vm-log'+(live?" live":"")+'" data-target="'+esc(j.target)+'"'
    +' data-tag="'+esc(j.tag)+'" data-kind="'+esc(j.kind)+'" data-live="'+(live?1:0)+'"'
    +' title="'+(live?"Tail this log live over ssh":"Open the stored log ("
      +Math.round((j.log_bytes||0)/1024)+" KB)")+'">'
    +(live?'<span class="spin">\u21bb</span> tail':'\u2317 open')+'</button>'
    +(meta?'<span class="vm-logmeta">'+meta+'</span>':"");
}

// --- log panel -----------------------------------------------------------
// A live tail without a websocket: re-POST /api/vm-tail on a timer. Each poll
// is one ssh `tail -n`, the same round trip the probe already makes, so this
// adds no dependency and nothing to clean up but the interval.
let vmLogCur=null, vmLogTimer=null;
function vmLogStop(){ if(vmLogTimer){ clearInterval(vmLogTimer); vmLogTimer=null; } }
function vmLogClose(){
  vmLogStop(); vmLogCur=null;
  document.getElementById("vm-logpane").style.display="none";
}
async function vmLogOpen(target,tag,kind,live){
  vmLogStop();
  vmLogCur={target,tag,kind,live};
  const pane=document.getElementById("vm-logpane");
  const follow=document.getElementById("vm-log-follow");
  pane.style.display="block";
  document.getElementById("vm-log-title").textContent=tag+" \u00b7 "+kind+" @ "+target;
  document.getElementById("vm-log-sub").textContent=live?"live \u2014 polling every 5s":"loading\u2026";
  document.getElementById("vm-log-body").textContent="";
  document.getElementById("vm-log-followwrap").style.display=live?"":"none";
  follow.checked=live;
  await vmLogPoll();
  if(live&&follow.checked) vmLogTimer=setInterval(vmLogPoll,5000);
  pane.scrollIntoView({behavior:"smooth",block:"nearest"});
}
async function vmLogPoll(){
  if(!vmLogCur) return;
  const {target,tag,kind,live}=vmLogCur;
  const body=document.getElementById("vm-log-body");
  const sub=document.getElementById("vm-log-sub");
  if(!SERVED){ sub.textContent="Serve the portal to read logs"; return; }
  try{
    const r=await fetch(live?"/api/vm-tail":"/api/vm-log",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({target,tag,kind,lines:400})});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"could not read the log");
    // Only repaint when the text actually moved, so selecting a line inside a
    // tailing panel is not wiped out five seconds later.
    if(body.textContent!==j.text){
      const atEnd=body.scrollTop+body.clientHeight>=body.scrollHeight-40;
      body.textContent=j.text||"(empty)";
      if(atEnd||live) body.scrollTop=body.scrollHeight;
    }
    sub.textContent=live
      ? "live \u00b7 "+esc(j.source||"")+" \u00b7 updated "+esc(j.at||"")
      : (j.truncated?"last "+Math.round((j.text||"").length/1024)+" KB of ":"")
        +Math.round((j.bytes||0)/1024)+" KB \u00b7 "+esc(j.source||"")
        +(j.mtime?" \u00b7 "+esc(j.mtime):"");
  }catch(e){
    sub.textContent="failed: "+e.message;
    vmLogStop();
  }
}
document.getElementById("vm-log-close").addEventListener("click",vmLogClose);
document.getElementById("vm-log-reload").addEventListener("click",vmLogPoll);
document.getElementById("vm-log-follow").addEventListener("change",e=>{
  vmLogStop();
  if(e.target.checked&&vmLogCur&&vmLogCur.live) vmLogTimer=setInterval(vmLogPoll,5000);
});
// One ssh round trip per target plus an `octl vm list`. Dispatched as a
// background job and polled through the align banner: an unreachable target
// costs a 90 s ssh timeout, which used to outlive the browser's patience and
// leave the section frozen on stale data.
async function refreshVmlog(){
  const btn=document.getElementById("vm-refresh"), note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to refresh"; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Probing\u2026';
  note.textContent="asking each target for its job dirs\u2026";
  try{
    const r=await fetch("/api/vm-refresh",{method:"POST"});
    const j=await r.json();
    if(!r.ok||!j.job) throw new Error(j.error||"refresh failed");
    const done=await pollAlign(j.job,"Probing delegation targets\u2026");
    if(done&&done.result){ VMLOG=done.result; renderVmlog(); buildSections(); }
    note.textContent=(done&&done.message)||"probe finished";
  }catch(e){
    note.textContent="Refresh failed: "+e.message;
  }finally{
    btn.disabled=false; btn.innerHTML="\u21bb Refresh status";
  }
}
document.getElementById("vm-refresh").addEventListener("click",refreshVmlog);
// Mirror the remote job logs locally so an old one stays openable. The target
// keeps exactly one log per (workspace, kind) and clobbers it on the next run.
async function syncVmLogs(){
  const btn=document.getElementById("vm-sync"), note=document.getElementById("vm-refresh-note");
  if(!SERVED){ note.textContent="Serve the portal to sync logs"; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Syncing\u2026';
  note.textContent="rsyncing job logs from every target\u2026";
  try{
    const r=await fetch("/api/vm-sync",{method:"POST"});
    const j=await r.json();
    if(!r.ok||(j.error&&!j.jobs)) throw new Error(j.error||"sync failed");
    VMLOG=j; renderVmlog();
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
    VMLOG=j; renderVmlog();
    const ms=j.machines||[], up=ms.filter(m=>m.state==="running").length;
    note.textContent=ms.length+" instance(s) \u00b7 "+up+" running \u00b7 "+esc(j.machines_checked||"");
  }catch(e){
    note.textContent="VM check failed: "+e.message;
  }finally{
    btn.disabled=false; btn.innerHTML="\u260d Check VM status";
  }
}
document.getElementById("vm-vms").addEventListener("click",checkVms);
document.getElementById("vm-target").addEventListener("change",e=>{
  vmTarget=e.target.value; renderVmlog();
});
document.getElementById("vm-livefirst").addEventListener("change",renderVmlog);

const RAG_COLOR={red:PBI.red, orange:PBI.orange, yellow:PBI.gold, green:PBI.green};
const RAID_TYPES=[["risk","Risk"],["assumption","Assumption"],["issue","Issue"],["dependency","Dependency"]];
let raidOpenRow=null;

function raidRows(){
  const showClosed=document.getElementById("raid-showclosed").checked;
  return (RAID.rows||[]).filter(r=>showClosed||!r.closed);
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
        return '<tr class="task raid-row" data-id="'+esc(r.id)+'">'
          +'<td><span class="rid">'+esc(r.id)+'</span></td>'
          +'<td class="ttl">'+esc(r.title||"\u2014")+'</td>'
          +'<td><span class="ragp" style="--rk:'+(RAG_COLOR[r.rag]||PBI.grey)+'">'+esc(r.rag||"\u2014")+'</span></td>'
          +'<td>'+r.score+'</td><td>'+esc(r.owner||"\u2014")+'</td>'
          +'<td'+(overdue?' class="od"':'')+'>'+esc(r.due||"\u2014")+'</td>'
          +'<td class="stt">'+esc(r.closed?"closed":(r.status||"open"))+'</td></tr>'
          +'<tr class="detail" data-for="'+esc(r.id)+'" style="display:none;"><td colspan="7"></td></tr>';
      }).join("")
    : '<tr><td colspan="7"><div class="empty">No RAID rows.</div></td></tr>';

  document.querySelectorAll("#raid-body tr.raid-row").forEach(tr=>
    tr.addEventListener("click",()=>toggleRaid(tr.dataset.id)));
  if(raidOpenRow && rows.some(r=>r.id===raidOpenRow)){ const id=raidOpenRow; raidOpenRow=null; toggleRaid(id); }
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
  // keep their original markdown in raid.md.
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
        +'<label>Due'+inp("due",r.due_raw!=null?r.due_raw:r.due,' placeholder="YYYY-MM-DD"')+'</label>'
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
    +'</div></div>';
}

function wireRaidEditor(id){
  const root=document.querySelector('#raid-body tr.detail[data-for="'+id+'"] .raid-edit');
  if(!root) return;
  const msg=root.querySelector('[data-a="msg"]');
  const post=async body=>{
    msg.textContent="saving\u2026";
    try{
      const res=await fetch("/api/raid/"+encodeURIComponent(id),
        {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const j=await res.json();
      if(!res.ok||j.error) throw new Error(j.error||"save failed");
      RAID=j.payload; raidOpenRow=id; renderRaid(); buildSections(); renderOverview();
    }catch(e){ msg.textContent="Failed: "+e.message; }
  };
  root.querySelector('[data-a="save"]').addEventListener("click",()=>{
    const body={};
    root.querySelectorAll("[data-f]").forEach(el=>{
      const v=(el.value||"").trim();
      if(!v) return;
      const orig=el.dataset.orig;
      if(orig!==undefined && v===orig.trim()) return;   // untouched
      body[el.dataset.f]=v;
    });
    if(!Object.keys(body).length){ msg.textContent="Nothing changed"; return; }
    post(body);
  });
  const cb=root.querySelector('[data-a="close"]');
  if(cb) cb.addEventListener("click",()=>{
    const res=prompt("Resolution for "+id+":");
    if(res && res.trim()) post({close:res.trim()});
  });
}

function raidNewForm(){
  const box=document.getElementById("raid-form");
  if(box.style.display!=="none"){ box.style.display="none"; return; }
  box.innerHTML='<div class="re-row">'
    +'<label>Type<select id="nr-type">'+RAID_TYPES.map(([v,l])=>'<option value="'+v+'">'+l+'</option>').join("")+'</select></label>'
    +'<label style="flex:1 1 320px;">Title<input id="nr-title" placeholder="What is the risk / issue?"></label>'
    +'<label>Owner<input id="nr-owner" value="Yoni"></label>'
    +'<label>P<select id="nr-p">'+[1,2,3,4,5].map(v=>'<option'+(v===3?" selected":"")+'>'+v+'</option>').join("")+'</select></label>'
    +'<label>I<select id="nr-i">'+[1,2,3,4,5].map(v=>'<option'+(v===3?" selected":"")+'>'+v+'</option>').join("")+'</select></label>'
    +'<label>Due<input id="nr-due" placeholder="YYYY-MM-DD"></label>'
    +'</div>'
    +'<div class="re-row"><label style="flex:1 1 100%;">Description<input id="nr-desc" placeholder="Context, blast radius, who is affected"></label></div>'
    +'<div class="re-act"><button class="btn primary" id="nr-save">Create</button><span class="ro-note" id="nr-msg"></span></div>';
  box.style.display="";
  document.getElementById("nr-save").addEventListener("click",async()=>{
    const m=document.getElementById("nr-msg");
    const title=document.getElementById("nr-title").value.trim();
    if(!title){ m.textContent="Title is required"; return; }
    m.textContent="creating\u2026";
    try{
      const res=await fetch("/api/raid-new",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({type:document.getElementById("nr-type").value, title:title,
          fw_owner:document.getElementById("nr-owner").value.trim(),
          p:document.getElementById("nr-p").value, i:document.getElementById("nr-i").value,
          due:document.getElementById("nr-due").value.trim(),
          description:document.getElementById("nr-desc").value.trim()})});
      const j=await res.json();
      if(!res.ok||j.error) throw new Error(j.error||"create failed");
      RAID=j.payload; box.style.display="none"; raidOpenRow=j.id;
      renderRaid(); buildSections(); renderOverview();
    }catch(e){ m.textContent="Failed: "+e.message; }
  });
}
document.getElementById("raid-new").addEventListener("click",raidNewForm);
document.getElementById("raid-showclosed").addEventListener("change",renderRaid);

async function refreshData(){
  const btn=document.getElementById("refresh");
  const note=document.getElementById("refresh-note");
  if(btn){ btn.disabled=true; btn.classList.add("spinning"); }
  try{
    if(SERVED){
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
      if(rv.ok) VMLOG=await rv.json();
    }
    const fstate=captureFilterState();   // preserve active filter selections
    buildFilters();                       // rebuild options (may include new values)
    restoreFilterState(fstate);           // re-apply selections that still exist
    buildTabs();                          // refresh tab counts, keep activeTab
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
    if(note){ note.textContent="Refresh failed"; }
  }finally{
    if(btn){ btn.disabled=false; btn.classList.remove("spinning"); }
  }
}
document.getElementById("refresh").addEventListener("click",refreshData);

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
    }
  }catch(e){ /* file:// — stay read-only with embedded data */ }
  document.getElementById("total").textContent=
    `${TASKS.length} tasks · ${bugList().filter(b=>b.open).length} open bugs`;
  buildFilters();
  buildTabs();
  applyTabPreset(activeTab);
  render();
  buildBugFilters();
  buildBugTabs();
  buildSections();
  renderOverview();
  renderPrs();
  renderRaid();
}
init();