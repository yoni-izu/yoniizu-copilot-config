(function(){
  const BOOT = window.__PORTAL__ || {};
  const TEAM0 = BOOT.team || {};
  const JIRA_BASE = BOOT.jira_base || "";
  const TEAM_UI_KEY = "team-ui-v1";
  let TEAM = TEAM0;
  let sharedTeam = TEAM0;
  let requestVersion = 0;
  let mode = "presentation"; // presentation | private
  let view = {tab:"overview", member:null, subtab:"tasks", logTypes:[]};
  const state = readState();
  const filters = {
    tasks:{query:"",category:[],priority:[],status:[]},
    activity:{query:"",source:[],kind:[],from:"",to:""},
    log:{query:"",items:[],from:"",to:""},
  };
  const selected = {tasks:new Set(), activity:new Set(), log:new Set()};
  const visibleRows = {tasks:[], activity:[], log:[]};
  let inlineEdit = null;
  const taskStatuses = ["todo","backlog","in-progress","blocked","done","cancelled","assigned","open","deferred"];
  const DAY_MS = 24*60*60*1000;
  const bulkFields = {
    tasks:{category:"Category",priority:"Priority",status:"Status",target_date:"Due date"},
    log:{session_type:"Type",meeting_date:"Date",title_visibility:"Topic visibility"},
  };

  function esc(s){ return String(s??"").replace(/[&<>"]/g, m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }
  function fmtDate(s){ if(!s) return "—"; try{ const d = new Date(s); return d.toLocaleDateString(); }catch{ return s; } }
  function ageDays(s){
    if(!s) return null;
    const ts = Date.parse(s);
    if(Number.isNaN(ts)) return null;
    return Math.max(0, Math.floor((Date.now()-ts)/DAY_MS));
  }
  function saveState(){ try{ localStorage.setItem(TEAM_UI_KEY, JSON.stringify({view})); }catch(_){} }
  function readState(){ try{ return JSON.parse(localStorage.getItem(TEAM_UI_KEY)||"{}"); }catch{ return {}; } }
  function isPrivate(){ return mode==="private"; }
  function root(){ return document.getElementById("team-root"); }
  function mv(v){ return Number.isFinite(v)?v:"—"; }
  function memberByKey(k){ return (TEAM.members||[]).find(m=>m.member_key===k) || null; }
  function memberAssignments(k){ return (TEAM.assignments||[]).filter(a=>a.member_key===k); }
  function matchesText(value, query){
    const text=JSON.stringify(value).toLowerCase();
    return query.trim().toLowerCase().split(/\s+/).filter(Boolean).every(word=>text.includes(word));
  }
  function matchesChoices(value, choices){ return !choices.length || choices.includes(value); }
  function matchesDates(value, filter){
    if(!filter.from && !filter.to) return true;
    const day=String(value||"").slice(0,10);
    return !!day && (!filter.from || day>=filter.from) && (!filter.to || day<=filter.to);
  }
  function filterToolbar(tab, groups, count, total, dates=false){
    const filter=filters[tab];
    return `<div class="team-filterbar" data-filter-tab="${tab}">
      <label class="team-filter-search">Search ${tab==="log"?"log":tab}<input type="search" data-team-search="${tab}" value="${esc(filter.query)}" placeholder="Search visible records…"></label>
      ${groups.map(([field,label,options,labels])=>{
        const values=[...new Set([...options,...(filter[field]||[])])].filter(Boolean).sort();
        const logTypes=tab==="log" && field==="types";
        const selected=logTypes?view.logTypes:filter[field];
        return `<details class="team-filter-dropdown" data-filter-group="${field}"><summary>${esc(label)}${selected.length?` (${selected.length})`:""}</summary><div class="team-filter-options">
          ${values.map(value=>`<label><input type="checkbox" ${logTypes?`data-log-type="${esc(value)}"`:`data-team-filter="${field}" data-value="${esc(value)}"`} ${selected.includes(value)?"checked":""}> ${esc(labels?.[value]||value)}</label>`).join("")||"<span>No values</span>"}
        </div></details>`;
      }).join("")}
      ${dates?`<label>From<input type="date" data-team-date="from" value="${esc(filter.from)}"></label><label>Through<input type="date" data-team-date="to" value="${esc(filter.to)}"></label>`:""}
      <button class="btn" data-team-clear="${tab}">Clear filters</button>
      <span class="cap team-filter-count" role="status">${count} / ${total} matching</span>
      ${dates&&filter.from&&filter.to&&filter.from>filter.to?'<span role="alert">From date must not be after Through date.</span>':""}
    </div>`;
  }
  function rowId(tab, row){
    return tab==="tasks"?row.assignment_id:tab==="log"?row.meeting_id:
      JSON.stringify([row.source,row.kind,row.at,row.url,row.label]);
  }
  function applyTableStandards(){
    if(window.PortalTableUI && typeof window.PortalTableUI.apply==="function"){
      window.PortalTableUI.apply(root());
    }
  }
  function resetSelection(){
    Object.values(selected).forEach(set=>set.clear());
    inlineEdit=null;
  }
  function selectionBar(tab, rows){
    visibleRows[tab]=rows;
    const ids=new Set(rows.map(row=>rowId(tab,row)));
    selected[tab]=new Set([...selected[tab]].filter(id=>ids.has(id)));
    const count=selected[tab].size;
    return `<div class="team-selectionbar">
      <label><input type="checkbox" data-team-select-all="${tab}" aria-label="Select all visible ${tab}" ${count&&count===ids.size?"checked":""} ${rows.length?"":"disabled"}> Select visible</label>
      <span role="status">${count} selected</span>
      <button class="btn" data-team-deselect="${tab}" ${count?"":"disabled"}>Deselect</button>
      ${tab==="activity"?`<button class="btn" data-team-copy-links ${count?"":"disabled"}>Copy selected links</button>`:
        isPrivate()?`<button class="btn" data-team-bulk="${tab}" ${count?"":"disabled"}>Edit selected field…</button>`:""}
      <span class="team-selection-message" role="status"></span>
    </div>`;
  }
  function rowCheckbox(tab,row){
    const id=rowId(tab,row);
    return `<input type="checkbox" data-team-row="${tab}" data-row-id="${esc(id)}" aria-label="Select ${esc(row.title||row.label||id)}" ${selected[tab].has(id)?"checked":""}>`;
  }
  function fieldValue(tab,row,field){
    if(field.startsWith("entry:")) return row.entries.find(e=>e.entry_id===field.slice(6)).body;
    if(field==="new-notes") return "";
    return row[field]??(field==="category"?"misc":field==="priority"?"medium":"");
  }
  function valueControl(tab,field,value){
    const choices=field==="category"?(TEAM.task_categories||["development","debug","research","planning","testing","code-review","track","misc"]):
      field==="priority"?["critical","high","medium","low"]:field==="status"?taskStatuses:
      field==="session_type"?(TEAM.log_types||{one_on_one:"1:1",conversation:"Conversation",meeting:"Meeting",note:"Note"}):
      field==="title_visibility"?{private:"Private",shared:"Presentation"}:null;
    if(choices){
      const entries=Array.isArray(choices)?choices.map(v=>[v,v]):Object.entries(choices);
      return `<select name="value" data-team-edit-value>${entries.map(([v,label])=>`<option value="${esc(v)}" ${v===value?"selected":""}>${esc(label)}</option>`).join("")}</select>`;
    }
    if(field==="description"||field==="new-notes"||field.startsWith("entry:")){
      return `<textarea name="value" data-team-edit-value>${esc(value)}</textarea>`;
    }
    return `<input name="value" data-team-edit-value type="${field.endsWith("_date")?"date":"text"}" value="${esc(value)}" ${field==="meeting_date"?"required":""}>`;
  }
  function inlineEditor(tab,row){
    const id=rowId(tab,row);
    if(!isPrivate()||inlineEdit?.tab!==tab||inlineEdit.id!==id) return "";
    const fields={title:"Title",...bulkFields[tab],...Object.fromEntries((row.entries||[]).map((e,i)=>[`entry:${e.entry_id}`,`Notes ${i+1}: ${e.tag} (${e.visibility})`]))};
    if(tab==="log"&&!(row.entries||[]).length) fields["new-notes"]="Notes (private)";
    return `<form class="team-inline-editor" data-team-inline-form="${tab}">
      <label>Field<select data-team-inline-field>${Object.entries(fields).map(([key,label])=>`<option value="${esc(key)}" ${key===inlineEdit.field?"selected":""}>${esc(label)}</option>`).join("")}</select></label>
      <label>Value${valueControl(tab,inlineEdit.field,inlineEdit.value)}</label>
      <div><button class="btn primary" type="submit">Save</button> <button class="btn" type="button" data-team-inline-cancel>Cancel</button></div>
      <span class="team-inline-message" role="alert"></span>
    </form>`;
  }
  function bulkDialog(tab){
    const rows=visibleRows[tab].filter(row=>selected[tab].has(rowId(tab,row)));
    const items=rows.map(row=>({id:rowId(tab,row),revision:row.revision}));
    const fields=bulkFields[tab];
    const first=Object.keys(fields)[0];
    formDialog(`Edit ${rows.length} selected ${tab==="log"?"log entries":"tasks"}`,`
      <p>Apply one field to all ${rows.length} selected records for ${esc(memberByKey(view.member)?.display_name||view.member)}. No hidden rows will change.</p>
      <ul class="team-bulk-preview">${rows.map(row=>`<li>${esc(row.title||rowId(tab,row))}</li>`).join("")}</ul>
      <label>Field<select name="field">${Object.entries(fields).map(([key,label])=>`<option value="${key}">${label}</option>`).join("")}</select></label>
      <label class="team-bulk-value">New value${valueControl(tab,first,fieldValue(tab,rows[0],first))}</label>
      <p class="cap">Save confirms this change. A stale record cancels the entire batch; reload and reselect after a conflict.</p>`,
      async data=>{
        await mutate("bulk-set",{entity_type:tab==="tasks"?"assignment":"meeting",member_key:rows[0].member_key,
          items,field:data.get("field"),value:data.get("value")||(data.get("field")==="target_date"?null:"")});
      },{setup:form=>{
        form.querySelector('[name="field"]').onchange=event=>{
          const field=event.target.value;
          form.querySelector(".team-bulk-value").innerHTML=`New value${valueControl(tab,field,fieldValue(tab,rows[0],field))}`;
        };
      }});
  }

  function buildTopTabs(){
    const tabs = [
      ["overview","Overview"],["plan","Plan"],["members","Members"],
    ];
    return `<div class="tabs team-tabs">` + tabs.map(([id,label])=>
      `<button class="tab ${view.tab===id?"on":""}" data-team-tab="${id}">${esc(label)}</button>`
    ).join("") + `</div>`;
  }

  function modeBar(){
    return `<div class="team-mode ${isPrivate()?"private":"presentation"}">
      <button class="btn" id="team-refresh">Refresh</button>
      <button class="btn team-mode-toggle" id="team-toggle-private" aria-pressed="${isPrivate()}">${isPrivate()?"Private":"Presentation"}</button>
    </div>`;
  }

  function card(m){
    const leave = m.leave || {};
    const next = leave.next_approved_leave ? `${fmtDate(leave.next_approved_leave.start_date)}→${fmtDate(leave.next_approved_leave.end_date)}` : "—";
    const reviews = (TEAM.evidence?.per_member?.[m.member_key]?.reviews || []).length;
    return `<tr>
      <td><a href="#member/${encodeURIComponent(m.member_key)}">${esc(m.display_name||m.member_key)}</a>${m.roster_role==="lead"?' <span class="badge">Lead</span>':""}</td>
      <td>${esc(m.main_ip_ownership||"—")}</td>
      <td>${esc(m.base_country||"—")}</td>
      <td>${esc(m.focus||"—")}</td>
      <td>${esc(next)}</td>
      <td>${reviews}</td>
      <td>${fmtDate(TEAM.evidence?.per_member?.[m.member_key]?.last_activity_at)}</td>
    </tr>`;
  }

  function renderOverview(){
    return `<div class="tile"><h3>Team overview</h3>
      <table data-table-key="team-overview"><thead><tr><th>Member</th><th>Main IP</th><th>Country</th><th>Current focus</th><th>Next leave</th><th>Pending reviews</th><th data-sort-type="date">Last activity</th></tr></thead>
      <tbody>${(TEAM.members||[]).map(card).join("")}</tbody></table>
    </div>`;
  }

  function renderPlan(){
    const milestones = TEAM.calendar?.milestones || [];
    const sprints = TEAM.calendar?.sprints || [];
    const metric = TEAM.metrics || {};
    const rows = (TEAM.members||[]).map(m=>{
      const mm = metric[m.member_key]?.total?.metrics || {};
      return `<tr><td>${esc(m.display_name||m.member_key)}</td><td>${mv(mm.features_delivered)}</td><td>${mv(mm.bugs_closed)}</td><td>${mv(mm.attributes_completed)}</td><td>${mv(mm.stories_completed)}</td></tr>`;
    }).join("");
    return `<div class="tile"><h3>Planning status</h3>
      <p class="cap">Milestones may overlap calendar quarters; windows are start-inclusive/end-exclusive.</p>
      <div class="team-grid">
        <div><h4>Milestones</h4><ul>${milestones.map(x=>`<li><b>${esc(x.name)}</b> ${fmtDate(x.started_at)} → ${fmtDate(x.ended_at)}</li>`).join("")}</ul>${milestones.length?"":"<p>No milestone calendar has been recorded.</p>"}</div>
        <div><h4>Sprints</h4><ul>${sprints.map(x=>`<li>${esc(x.name)} ${fmtDate(x.started_at)} → ${fmtDate(x.ended_at)}</li>`).join("")}</ul>${sprints.length?"":"<p>No sprint calendar has been recorded.</p>"}</div>
      </div>
      <h4>Delivery metrics (recorded evidence)</h4>
      <table data-table-key="team-plan"><thead><tr><th>Member</th><th data-sort-type="number">Features</th><th data-sort-type="number">Bugs</th><th data-sort-type="number">Attributes</th><th data-sort-type="number">Stories</th></tr></thead><tbody>${rows}</tbody></table>
    </div>`;
  }

  function memberTabs(m){
    const tabs = [["tasks","Tasks"],["activity","Activity"],["oneonone","Log"],["reviews","Reviews"]];
    return `<div class="tabs">${tabs.map(([id,label])=>`<button class="tab ${view.subtab===id?"on":""}" data-member-tab="${id}">${esc(label)}</button>`).join("")}</div>`;
  }
  function memberHeader(m){
    const leave = m.leave || {};
    const current = leave.current_leave ? `${fmtDate(leave.current_leave.start_date)}→${fmtDate(leave.current_leave.end_date)}` : "—";
    const next = leave.next_approved_leave ? `${fmtDate(leave.next_approved_leave.start_date)}→${fmtDate(leave.next_approved_leave.end_date)}` : "—";
    return `<div class="tile"><h3>${esc(m.display_name||m.member_key)} ${m.roster_role==="lead"?"<span class='badge'>Lead</span>":""}</h3>
      <p class="cap">IP: ${esc(m.main_ip_ownership||"—")} · Country: ${esc(m.base_country||"—")} · Joined: ${fmtDate(m.onboarding_date)}</p>
      <p class="cap">Leave — current: ${esc(current)} · next: ${esc(next)}</p>
      <div class="team-focus"><b>Current focus</b><p>${esc(m.focus||"Not recorded")}</p></div>
      ${m.goals?`<p>Goals: ${esc(m.goals)}</p>`:""}
      ${isPrivate()?`<div class="team-member-actions"><button class="btn team-edit-focus" data-member="${esc(m.member_key)}">Edit focus</button>
      <button class="btn team-edit-member" data-member="${esc(m.member_key)}">Edit profile</button></div>`:""}
    </div>`;
  }
  function rowEditBtns(type,id,rev){
    return isPrivate()
      ? `${type==="meeting"?`<button class="btn team-inline" data-type="${type}" data-id="${esc(id)}">Edit inline</button>`:""}
        <button class="btn team-edit" data-type="${type}" data-id="${esc(id)}" data-rev="${esc(rev||"")}">Edit details</button>`
      : "";
  }
  function memberTasks(m){
    const all = memberAssignments(m.member_key), filter=filters.tasks;
    const list=all.filter(a=>matchesText(a,filter.query) &&
      matchesChoices(a.category||"misc",filter.category) &&
      matchesChoices(a.priority||"medium",filter.priority) && matchesChoices(a.status,filter.status));
    return `<div class="tile"><h3>Tasks ${isPrivate()?'<button class="btn primary team-new-assignment" data-member="'+esc(m.member_key)+'">New task</button>':""}</h3>
      ${filterToolbar("tasks",[["category","Category",all.map(a=>a.category||"misc")],["priority","Priority",all.map(a=>a.priority||"medium")],["status","Status",all.map(a=>a.status)]],list.length,all.length)}
      ${selectionBar("tasks",list)}
      <table data-table-key="team-member-tasks"><thead><tr><th aria-label="Selection" data-no-sort></th><th>ID</th><th>Task</th><th>Category</th><th>Priority</th><th>Status</th><th data-sort-type="date">Created</th><th data-sort-type="number">Age</th><th data-sort-type="date">Due</th><th data-no-sort></th></tr></thead><tbody>
      ${list.map(a=>`<tr><td class="team-select-cell">${rowCheckbox("tasks",a)}</td><td><code>${esc(a.assignment_id)}</code></td><td><b>${esc(a.title)}</b>
      <p>${esc(a.description||"")}</p>${a.jira_key?`<a href="${JIRA_BASE+encodeURIComponent(a.jira_key)}" target="_blank" rel="noopener">${esc(a.jira_key)}</a>`:""}
      <ul>${(a.refs||[]).map(ref=>`<li>${referenceLink(ref)}</li>`).join("")}</ul>
      ${(a.updates||[]).map(u=>`<p class="cap">${fmtDate(u.created_at)}: ${esc(u.note)}</p>`).join("")}
      </td><td>${esc(a.category||"misc")}</td><td>${esc(a.priority||"medium")}</td><td>${esc(a.status)}</td><td data-sort-value="${esc(String(a.created_at||""))}">${fmtDate(a.created_at)}</td><td data-sort-value="${esc(String(ageDays(a.created_at)??""))}">${ageDays(a.created_at)==null?"—":ageDays(a.created_at)+"d"}</td><td data-sort-value="${esc(String(a.target_date||""))}">${fmtDate(a.target_date)}</td><td>${rowEditBtns("assignment",a.assignment_id,a.revision)}
      ${isPrivate()?`<button class="btn team-task-note" data-id="${esc(a.assignment_id)}">Add note</button>`:""}</td></tr>
      `).join("")}
      ${list.length?"":'<tr><td colspan="10">No matching tasks.</td></tr>'}</tbody></table></div>`;
  }
  function referenceLink(ref){
    const id=String(ref.target_id||"");
    let url=null;
    if(ref.domain_name==="github" && /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/[1-9]\d*$/.test(id)) url=id;
    if(ref.domain_name==="jira" && /^[A-Z][A-Z0-9]+-\d+$/.test(id)) url=JIRA_BASE+encodeURIComponent(id);
    if(ref.domain_name==="tasks" && ref.target_type==="task") url="#task/"+encodeURIComponent(id);
    const label=esc(`${ref.domain_name}/${ref.target_type}: ${id}`);
    return url?`<a href="${esc(url)}">${label}</a>`:label;
  }
  function memberActivity(m){
    const e = TEAM.evidence?.per_member?.[m.member_key] || {};
    const scanned=TEAM.activity?.members?.[m.member_key]||{};
    const observations=(scanned.observations||[]).map(a=>({at:a.observed_at,kind:a.kind,label:a.title,url:a.url,source:a.source}));
    const all=[...observations,...(e.activity||[])].sort((a,b)=>String(b.at||"").localeCompare(String(a.at||"")));
    const filter=filters.activity;
    const activity=all.filter(a=>matchesText(a,filter.query) && matchesChoices(a.source,filter.source) &&
      matchesChoices(a.kind,filter.kind) && matchesDates(a.at,filter));
    const selection=selectionBar("activity",activity);
    const rows = activity.map(a=>`<tr><td class="team-select-cell">${rowCheckbox("activity",a)}</td><td>${fmtDate(a.at)}</td><td>${esc(a.kind)}</td><td>${a.url?`<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.label)}</a>`:esc(a.label)}</td><td>${esc(a.source||"")}</td></tr>`).join("");
    return `<div class="tile"><h3>Activity ${isPrivate()?`<button class="btn primary team-scan" data-member="${esc(m.member_key)}">Scan Jira &amp; GitHub</button>`:""}</h3>
      <p class="cap">Scans continue from each source's last successful scan; first scan covers seven days.</p>
      <p class="cap">${Object.entries(scanned.sources||{}).map(([source,s])=>`${esc(source)}: ${s.last_success_at?esc(s.last_success_at):"not scanned"}`).join(" · ")}</p>
      <div class="team-scan-message" role="status">${(scanned.errors||[]).map(e=>esc(e.source+": "+e.message)).join("; ")}</div>
      ${filterToolbar("activity",[["source","Source",all.map(a=>a.source)],["kind","Activity type",all.map(a=>a.kind)]],activity.length,all.length,true)}
      ${selection}
      <table data-table-key="team-member-activity"><thead><tr><th aria-label="Selection" data-no-sort></th><th data-sort-type="date">When</th><th>Kind</th><th>Item</th><th>Source</th></tr></thead><tbody>${rows||'<tr><td colspan="5">No matching activity.</td></tr>'}</tbody></table></div>`;
  }
  function itemTypes(){ return TEAM.log_item_types||{}; }
  function isLeadKey(k){ return memberByKey(k)?.roster_role==="lead"; }
  function partyLabel(member, value){
    if(value==="me") return isLeadKey(member)?"Me":"Me (lead)";
    if(value==="member") return isLeadKey(member)?`${TEAM.manager_name||"Manager"} (manager)`:(memberByKey(member)?.display_name||member);
    return value?value.charAt(0).toUpperCase()+value.slice(1):"—";
  }
  function itemLabel(tag){ return itemTypes()[tag]?.label||(tag==="notes"||tag==="note"?"Notes":tag||"Note"); }
  function isTypedEntry(e){ return !!itemTypes()[e.tag] && e.fields && Object.keys(e.fields).length>0; }
  function taskTitle(id){ return (TEAM.assignments||[]).find(a=>a.assignment_id===id)?.title||id; }
  function itemDetails(member, e){
    const spec=itemTypes()[e.tag];
    if(!spec) return "";
    return spec.fields.filter(f=>f.key!==spec.summary && e.fields[f.key]!==undefined && e.fields[f.key]!=="" && e.fields[f.key]!==false)
      .map(f=>{
        let v=e.fields[f.key];
        if(f.input==="checkbox") v="yes";
        else if(f.input==="task") v=taskTitle(v);
        else if(f.input==="select" && ["owner","raised_by","decided_by","unblocker"].includes(f.key)) v=partyLabel(member,v);
        return `<span class="team-item-kv"><b>${esc(f.label)}:</b> ${esc(v)}</span>`;
      }).join("");
  }
  function meetingEntries(mt){
    const entries = mt.entries || [];
    return entries.map(e=>{
      const typed=isTypedEntry(e);
      const raid=e.raid_id?` <span class="chip team-raid-chip">RAID ${esc(e.raid_id)}</span>`:
        (isPrivate() && e.tag==="roadblock" && e.record_id?` <button class="btn team-promote-raid" data-meeting="${esc(mt.meeting_id)}" data-entry="${esc(e.entry_id)}">Promote to RAID</button>`:"");
      const task=e.tag==="action" && e.record_id?` <a href="#" class="team-item-task" data-task="${esc(e.record_id)}">task</a>`:"";
      return `<li class="team-item team-item-${esc(e.tag||"note")}"><span class="badge team-item-badge" data-item-type="${esc(e.tag||"note")}">${esc(itemLabel(e.tag))}</span> ${esc(e.body)}${task}${raid}
        ${typed?`<div class="team-item-meta">${itemDetails(mt.member_key,e)}</div>`:""}
        ${isPrivate()?`<i class="cap">(${esc(e.visibility==="shared"?"presentation":"private")})</i>`:""}</li>`;
    }).join("");
  }
  function memberOneOnOne(m){
    const types=TEAM.log_types||{one_on_one:"1:1",conversation:"Conversation",meeting:"Meeting",note:"Note"};
    const all=(TEAM.meetings||[]).filter(mt=>mt.member_key===m.member_key);
    const items=filters.log.items;
    const itemLabels=Object.fromEntries(Object.entries(itemTypes()).map(([k,v])=>[k,v.label]));
    const tags=[...Object.keys(itemTypes()),...all.flatMap(mt=>(mt.entries||[]).map(e=>e.tag))];
    tags.forEach(t=>{ if(!itemLabels[t]) itemLabels[t]=itemLabel(t); });
    const meetings=all.filter(mt=>matchesChoices(mt.session_type||"one_on_one",view.logTypes) &&
      (!items.length || (mt.entries||[]).some(e=>items.includes(e.tag))) &&
      matchesText({title:mt.title,entries:mt.entries,shared_summary:mt.shared_summary,
        private_summary:mt.private_summary,full_markdown:mt.full_markdown},filters.log.query) &&
      matchesDates(mt.meeting_date,filters.log));
    return `<div class="tile"><h3>Member log ${isPrivate()?'<button class="btn primary team-new-meeting" data-member="'+esc(m.member_key)+'">New entry</button>':""}</h3>
      ${filterToolbar("log",[["types","Type",Object.keys(types),types],["items","Item type",tags,itemLabels]],meetings.length,all.length,true)}
      ${selectionBar("log",meetings)}
      ${meetings.length?"":'<p class="empty">No matching log entries.</p>'}
      ${meetings.map(mt=>`<div class="team-meeting team-log-row"><div class="team-select-cell">${rowCheckbox("log",mt)}</div><div><h4><span class="badge">${esc(types[mt.session_type]||"1:1")}</span> ${esc(mt.title||"Session")} · ${fmtDate(mt.meeting_date)}</h4>
      <p class="cap">${esc(mt.shared_summary||mt.private_summary||"")}</p><ul>${meetingEntries(mt)}</ul>${rowEditBtns("meeting",mt.meeting_id,mt.revision)}${inlineEditor("log",mt)}</div></div>`).join("")}
    </div>`;
  }
  function memberReviews(m){
    const reviews = TEAM.evidence?.per_member?.[m.member_key]?.reviews || [];
    return `<div class="tile"><h3>Reviews waiting for me</h3>
      <table data-table-key="team-member-reviews"><thead><tr><th>Task</th><th>Status</th><th>PRs</th></tr></thead><tbody>
      ${reviews.map(r=>`<tr><td><a href="#task/${encodeURIComponent(r.task_id)}">${esc(r.title||r.task_id)}</a></td><td>${esc(r.status||"")}</td><td>${(r.pr_urls||[]).map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener">PR</a>`).join(" ")}</td></tr>`).join("")}
      </tbody></table></div>`;
  }
  function renderMembers(){
    const members = TEAM.members || [];
    const member = memberByKey(view.member || members[0]?.member_key);
    if(!member) return `<div class="empty">No team members available.</div>`;
    view.member = member.member_key;
    let body = memberTasks(member);
    if(view.subtab==="activity") body = memberActivity(member);
    else if(view.subtab==="oneonone") body = memberOneOnOne(member);
    else if(view.subtab==="reviews") body = memberReviews(member);
    const picker = `<div class="team-member-picker">` + members.map(m=>
      `<button class="chip ${m.member_key===view.member?"on":""}" data-member="${esc(m.member_key)}">${esc(m.display_name||m.member_key)}</button>`
    ).join("") + `</div>`;
    return `${memberHeader(member)}${picker}${memberTabs(member)}${body}`;
  }

  async function fetchTeam(){
    const audience = isPrivate()?"private":"shared";
    const version = ++requestVersion;
    const q = `/api/team?audience=${audience}`;
    const res = await fetch(q, {cache:"no-store", headers: audience==="private"?{"X-Team-Private":"1"}:{}});
    const body = await res.json();
    if(version!==requestVersion) return false;
    if(!res.ok) throw new Error(body.error || "Failed to load Team data");
    if(body.audience!==audience) throw new Error("Team audience mismatch");
    TEAM = body;
    resetSelection();
    if(audience==="shared") sharedTeam = body;
    return true;
  }
  async function mutate(action, data){
    if(!isPrivate()) throw new Error("Enable private mode to edit team records.");
    const res = await fetch("/api/team", {
      method:"POST",
      headers:{"Content-Type":"application/json","X-Team-Private":"1"},
      body:JSON.stringify({action,data}),
    });
    const body = await res.json();
    if(!res.ok) throw new Error(body.error || "Save failed");
    return body.result;
  }
  function parseMmDd(s){
    const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec((s||"").trim());
    return m ? `${m[3]}-${m[2]}-${m[1]}` : s;
  }
  function visibilitySelect(name, value="private", label="Visibility"){
    return `<label>${label}<select name="${name}"><option value="private" ${value==="private"?"selected":""}>Private</option><option value="shared" ${value==="shared"?"selected":""}>Presentation</option></select></label>`;
  }
  function taskFields(current={}){
    const categories=TEAM.task_categories||["development","debug","research","planning","testing","code-review","track","misc"];
    return `<div class="ws-modal-row"><label>Category<select name="category">${categories.map(c=>`<option value="${c}" ${c===(current.category||"misc")?"selected":""}>${c}</option>`).join("")}</select></label>
      <label>Priority<select name="priority">${["critical","high","medium","low"].map(p=>`<option ${p===(current.priority||"medium")?"selected":""}>${p}</option>`).join("")}</select></label></div>
      <label>Jira reference (optional)<input name="jira_key" value="${esc(current.jira_key||"")}"></label>
      <label>PR URLs (one per line)<textarea name="pr_urls">${esc((current.refs||[]).filter(r=>r.domain_name==="github").map(r=>r.target_id).join("\n"))}</textarea></label>
      <label>Other references (one per line: domain | type | ID | relation)<textarea name="other_refs" placeholder="bugs | bug | BUG-ID | related-to">${esc((current.refs||[]).filter(r=>r.domain_name!=="github").map(r=>[r.domain_name,r.target_type,r.target_id,r.relation||"related-to"].join(" | ")).join("\n"))}</textarea></label>`;
  }
  function taskFieldValues(container){
    const value=name=>container.querySelector(`[name="${name}"]`).value;
    const refs=value("pr_urls").split("\n").map(x=>x.trim()).filter(Boolean).map(url=>({domain_name:"github",target_type:"pr",target_id:url,relation:"related-to"}));
    value("other_refs").split("\n").map(x=>x.trim()).filter(Boolean).forEach(line=>{
      const fields=line.split("|").map(x=>x.trim());
      if(fields.length<3 || fields.length>4 || fields.slice(0,3).some(x=>!x)) throw new Error("References need domain | type | ID | relation");
      refs.push({domain_name:fields[0],target_type:fields[1],target_id:fields[2],relation:fields[3]||"related-to"});
    });
    return {category:value("category"),priority:value("priority"),jira_key:value("jira_key")||null,refs};
  }
  function formDialog(title, fields, onSave, {large=false, setup=()=>{}}={}){
    const previous = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "ws-modal-ov team-dialog-overlay";
    overlay.innerHTML = `<form class="ws-modal team-dialog ${large?"team-session-dialog":""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <h3>${esc(title)}</h3>${fields}<div class="ws-modal-act">
      <span class="ws-modal-msg" role="alert"></span><button type="button" class="team-cancel">Cancel</button><button type="submit" class="primary">Save</button></div></form>`;
    const form = overlay.querySelector("form");
    const close = ()=>{ overlay.remove(); previous?.focus(); };
    overlay.querySelector(".team-cancel").onclick = close;
    overlay.addEventListener("keydown", e=>{
      if(e.key==="Escape"){ e.preventDefault(); close(); }
      if(e.key==="Tab"){
        const nodes=[...overlay.querySelectorAll("button,input,select,textarea")].filter(x=>!x.disabled);
        const first=nodes[0], last=nodes[nodes.length-1];
        if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}
        else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}
      }
    });
    form.onsubmit = async e=>{
      e.preventDefault();
      const save = form.querySelector('[type="submit"]');
      save.disabled = true;
      try{
        await onSave(new FormData(form), form);
        close();
        await refresh();
      }catch(err){
        form.querySelector(".ws-modal-msg").textContent = err.message;
        save.disabled = false;
      }
    };
    document.body.append(overlay);
    setup(form);
    form.querySelector("input,textarea,select")?.focus();
    return overlay;
  }
  async function editAssignment(id, rev){
    const current = (TEAM.assignments||[]).find(a=>a.assignment_id===id);
    if(!current) return;
    assignmentDialog(current.member_key, current);
  }
  async function newAssignment(member){
    assignmentDialog(member);
  }
  function assignmentDialog(member, current=null){
    formDialog(current?"Edit task":"New task", `
      <label>Title<input name="title" required value="${esc(current?.title||"")}"></label>
      <label>Description<textarea name="description">${esc(current?.description||"")}</textarea></label>
      ${current?`<p>ID: <code>${esc(current.assignment_id)}</code></p>`:""}
      <div class="ws-modal-row"><label>Status<select name="status">${["todo","backlog","in-progress","blocked","done","cancelled","assigned","open","deferred"].map(s=>`<option ${s===current?.status?"selected":""}>${s}</option>`).join("")}</select></label>
      <label>Due date<input type="date" name="target_date" value="${esc(current?.target_date||"")}"></label></div>
      ${taskFields(current||{})}
      ${visibilitySelect("visibility",current?.visibility)}`, async (data,form)=>{
      const fields = Object.fromEntries(data);
      delete fields.pr_urls;
      delete fields.other_refs;
      fields.target_date ||= null;
      Object.assign(fields,taskFieldValues(form));
      await mutate(current?"assignment-set":"assignment-create",
        current?{id:current.assignment_id,revision:current.revision,...fields}
          :{member_key:member,kind:"task",...fields});
    });
  }
  function meetingById(id){ return (TEAM.meetings||[]).find(m=>m.meeting_id===id); }
  async function editMeeting(id, rev){
    const current=meetingById(id);
    if(current) meetingDialog(current.member_key,current);
  }
  async function newMeeting(member){
    meetingDialog(member);
  }
  function itemFieldInput(member, field, value){
    const name=`f:${field.key}`;
    const req=field.required?"required":"";
    if(field.input==="textarea") return `<label class="team-item-wide">${esc(field.label)}<textarea name="${name}" rows="2" ${req}>${esc(value||"")}</textarea></label>`;
    if(field.input==="checkbox") return `<label class="team-item-check"><input type="checkbox" name="${name}" ${value?"checked":""}> ${esc(field.label)}</label>`;
    if(field.input==="date") return `<label>${esc(field.label)}<input type="date" name="${name}" value="${esc(value||"")}" ${req}></label>`;
    if(field.input==="task"){
      const tasks=memberAssignments(member);
      return `<label>${esc(field.label)}<select name="${name}" ${req}><option value="">— choose task —</option>${tasks.map(t=>`<option value="${esc(t.assignment_id)}" ${t.assignment_id===value?"selected":""}>${esc(t.title)} (${esc(t.status||"")})</option>`).join("")}</select></label>`;
    }
    if(field.input==="select"){
      const party=["owner","raised_by","decided_by","unblocker"].includes(field.key);
      return `<label>${esc(field.label)}<select name="${name}">${field.options.map(o=>`<option value="${esc(o)}" ${o===(value??field.options[0])?"selected":""}>${esc(o===""?"(unchanged)":party?partyLabel(member,o):o)}</option>`).join("")}</select></label>`;
    }
    return `<label class="${field.key===itemTypes()[field._tag]?.summary?"team-item-main":""}">${esc(field.label)}<input name="${name}" value="${esc(value||"")}" ${req}></label>`;
  }
  function itemRow(member, tag, entry=null){
    const spec=itemTypes()[tag];
    const fields=entry?.fields||{};
    const locked=tag==="action" && entry?.record_id;
    const visibility=spec.always_private?`<span class="cap">Always private</span>`:
      visibilitySelect("visibility",entry?.visibility||spec.default_visibility||"private");
    const body=locked
      ? `<div class="team-item-locked"><b>${esc(fields.title||entry.body)}</b> ${itemDetails(member,entry)}
          <span class="cap">Saved as task <code>${esc(entry.record_id)}</code> — edit it in Tasks.</span></div>`
      : `<div class="team-item-fields">${spec.fields.map(f=>itemFieldInput(member,{...f,_tag:tag},fields[f.key])).join("")}</div>`;
    const raid=tag!=="roadblock"?"":entry?.raid_id?`<span class="chip team-raid-chip">RAID ${esc(entry.raid_id)}</span>`:
      entry?.record_id?`<button type="button" class="btn team-promote-raid" data-meeting="${esc(entry.meeting_id||"")}" data-entry="${esc(entry.entry_id)}">Promote to RAID</button>`:
      `<span class="cap">Save to enable RAID promotion</span>`;
    return `<div class="team-item-row" data-item-tag="${esc(tag)}" data-item-type="${esc(tag)}" ${entry?.entry_id?`data-entry-id="${esc(entry.entry_id)}"`:""}>
      <div class="team-item-head"><span class="badge team-item-badge" data-item-type="${esc(tag)}">${esc(spec.label)}</span>
        ${visibility}${raid}<button type="button" class="btn team-item-remove" aria-label="Remove ${esc(spec.label)}">−</button></div>
      ${body}</div>`;
  }
  function itemRowValue(row, entryById){
    const tag=row.dataset.itemTag, id=row.dataset.entryId;
    const spec=itemTypes()[tag];
    const original=id?entryById[id]:null;
    let fields;
    if(tag==="action" && original?.record_id) fields={...original.fields};
    else{
      fields={};
      spec.fields.forEach(f=>{
        const input=row.querySelector(`[name="f:${f.key}"]`);
        if(!input) return;
        if(f.input==="checkbox") fields[f.key]=input.checked;
        else if(input.value.trim()) fields[f.key]=input.value.trim();
      });
    }
    const vis=row.querySelector('[name="visibility"]');
    return {...(id?{entry_id:id}:{}),tag,fields,visibility:spec.always_private?"private":(vis?.value||"private")};
  }
  function meetingDialog(member, current=null){
    const existing = current?.entries||[];
    const note = existing.find(e=>e.tag==="notes" || e.tag==="note");
    const typed = existing.filter(e=>e!==note && isTypedEntry(e));
    const legacy = existing.filter(e=>e!==note && !isTypedEntry(e));
    const entryById=Object.fromEntries(existing.map(e=>[e.entry_id,{...e,meeting_id:current?.meeting_id}]));
    const legacyFields=(entry,index)=>`<div class="team-entry" data-entry-index="${index}">
      <label>Type<input name="tag" value="${esc(entry.tag)}" required></label>
      <label>Entry<textarea name="body" rows="2">${esc(entry.body)}</textarea></label>${visibilitySelect("visibility",entry.visibility)}</div>`;
    const types=TEAM.log_types||{one_on_one:"1:1",conversation:"Conversation",meeting:"Meeting",note:"Note"};
    const counterpart=isLeadKey(member)?(TEAM.manager_name||"Manager"):(memberByKey(member)?.display_name||member);
    formDialog(current?"Edit log entry":"New log entry", `
      <div class="team-session-meta">
        <label>Type<select name="session_type">${Object.entries(types).map(([key,label])=>`<option value="${key}" ${key===(current?.session_type||"one_on_one")?"selected":""}>${esc(label)}</option>`).join("")}</select></label>
        <label>Topic (optional)<input name="title" value="${esc(current?.title||"")}" placeholder="1:1 with ${esc(counterpart)} - YYYY-MM-DD"></label>
        <label>Date (blank = today)<input type="date" name="meeting_date" value="${esc(current?.meeting_date||"")}"></label>
        ${visibilitySelect("title_visibility",current?.title_visibility,"Topic visibility")}
      </div>
      <div class="team-item-toolbar" role="group" aria-label="Add item">${Object.entries(itemTypes()).map(([tag,spec])=>
        `<button type="button" class="btn team-add-item" data-item-type="${esc(tag)}">+ ${esc(spec.label)}</button>`).join("")}</div>
      <div class="team-item-rows">${typed.map(e=>itemRow(member,e.tag,entryById[e.entry_id])).join("")}</div>
      <p class="cap team-item-empty" ${typed.length?"hidden":""}>Add items with the buttons above: actions, decisions, roadblocks, feedback…</p>
      ${legacy.length?`<details class="team-legacy-entries"><summary>Other entries (${legacy.length})</summary>${legacy.map(legacyFields).join("")}</details>`:""}
      <div class="team-notes-box"><label>Notes<textarea name="notes" rows="3" placeholder="Short freestyle notes (optional)…">${esc(note?.body||"")}</textarea></label>
      ${visibilitySelect("notes_visibility",note?.visibility,"Notes visibility")}</div>
      ${current?`<p class="cap">Removing a roadblock or feedback row deletes its record; saved action items stay as tasks.</p>`:""}`,
      async (data,form)=>{
        const entries=[];
        if(data.get("notes") || note){
          entries.push({...(note?{entry_id:note.entry_id}:{}),tag:note?.tag||"notes",body:data.get("notes"),visibility:data.get("notes_visibility")});
        }
        form.querySelectorAll(".team-item-row").forEach(row=>entries.push(itemRowValue(row,entryById)));
        form.querySelectorAll(".team-entry").forEach(row=>{
          const original=legacy[Number(row.dataset.entryIndex)];
          entries.push({entry_id:original.entry_id,tag:row.querySelector('[name="tag"]').value,
            body:row.querySelector('[name="body"]').value,visibility:row.querySelector('[name="visibility"]').value});
        });
        const fields={title:data.get("title"),meeting_date:data.get("meeting_date"),
          session_type:data.get("session_type"),title_visibility:data.get("title_visibility"),entries};
        await mutate(current?"meeting-save":"meeting-create",current
          ?{id:current.meeting_id,revision:current.revision,...fields}:{member_key:member,...fields});
      },{large:true,setup:form=>{
        const rows=form.querySelector(".team-item-rows");
        const sync=()=>{ form.querySelector(".team-item-empty").hidden=!!rows.children.length; };
        const wire=row=>{
          row.querySelector(".team-item-remove").onclick=()=>{ row.remove(); sync(); };
          const promote=row.querySelector(".team-promote-raid");
          if(promote) promote.onclick=()=>promoteRoadblock(entryById[promote.dataset.entry]);
        };
        rows.querySelectorAll(".team-item-row").forEach(wire);
        form.querySelectorAll(".team-add-item").forEach(btn=>btn.onclick=()=>{
          const holder=document.createElement("div");
          holder.innerHTML=itemRow(member,btn.dataset.itemType);
          const row=holder.firstElementChild;
          rows.append(row); wire(row); sync();
          row.querySelector(".team-item-fields input,.team-item-fields textarea,.team-item-fields select")?.focus();
        });
      }});
  }
  function findEntry(meetingId, entryId){
    const mt=meetingById(meetingId);
    const e=(mt?.entries||[]).find(x=>x.entry_id===entryId);
    return e?{...e,meeting_id:meetingId,member_key:mt.member_key}:null;
  }
  function promoteRoadblock(entry){
    if(!entry?.record_id) return;
    const f=entry.fields||{};
    const owner=memberByKey(entry.member_key||view.member)?.display_name||"Team lead";
    formDialog("Promote roadblock to RAID", `
      <label>Title<input name="title" required value="${esc(f.blocker||entry.body||"")}"></label>
      <div class="ws-modal-row"><label>FW owner<input name="fw_owner" required value="${esc(owner)}"></label>
      <label>P<select name="p">${[1,2,3,4,5].map(v=>`<option ${v===3?"selected":""}>${v}</option>`).join("")}</select></label>
      <label>I<select name="i">${[1,2,3,4,5].map(v=>`<option ${v===3?"selected":""}>${v}</option>`).join("")}</select></label>
      <label>Due<input type="date" name="due"></label></div>
      <label>Description<textarea name="description">${esc(f.impact||"")}</textarea></label>
      <p class="cap">Creates a RAID issue and links it to this roadblock.</p>`, async data=>{
        if(!isPrivate()) throw new Error("Enable private mode to edit team records.");
        const payload={type:"issue",...Object.fromEntries(data)};
        const res=await fetch("/api/raid-new",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
        const body=await res.json();
        if(!res.ok||body.error) throw new Error(body.error||"RAID create failed");
        await mutate("roadblock-raid-link",{roadblock_id:entry.record_id,raid_id:body.id});
      });
  }
  function editFocus(member){
    const current=memberByKey(member);
    formDialog("Edit current focus", `<label>Current focus<textarea name="focus">${esc(current.focus||"")}</textarea></label>
      ${visibilitySelect("focus_visibility",current.focus_visibility||"shared")}`,async data=>{
      await mutate("member-set",{id:member,revision:current.revision,...Object.fromEntries(data)});
    });
  }
  function editMember(memberKey){
    const current = memberByKey(memberKey);
    if(!current) return;
    formDialog("Edit member profile", `
      <label>Main IP ownership<input name="main_ip_ownership" value="${esc(current.main_ip_ownership||"")}"></label>
      <label>Base country<input name="base_country" value="${esc(current.base_country||"")}"></label>
      <label>Goals (optional)<textarea name="goals">${esc(current.goals||"")}</textarea></label>
      ${visibilitySelect("goals_visibility",current.goals_visibility||"private","Goals visibility")}`,async data=>{
      await mutate("member-set", {
        id:memberKey,revision:current.revision,
        main_ip_ownership:data.get("main_ip_ownership")||null,
        base_country:data.get("base_country")||null,
        goals:data.get("goals"),goals_visibility:data.get("goals_visibility"),
      });
    });
  }

  function attach(){
    const el = root();
    if(!el) return;
    el.querySelectorAll("[data-team-tab]").forEach(b=>b.onclick=()=>{ view.tab=b.dataset.teamTab; saveState(); render(); });
    el.querySelectorAll("[data-member]").forEach(b=>b.onclick=()=>{ resetSelection(); view.tab="members"; view.member=b.dataset.member; saveState(); render(); });
    el.querySelectorAll("[data-member-tab]").forEach(b=>b.onclick=()=>{ inlineEdit=null; view.subtab=b.dataset.memberTab; saveState(); render(); });
    const activeFilterTab=view.subtab==="oneonone"?"log":view.subtab;
    el.querySelectorAll("[data-team-search]").forEach(input=>input.oninput=()=>{
      const tab=input.dataset.teamSearch, caret=input.selectionStart;
      filters[tab].query=input.value;
      render();
      const next=root().querySelector(`[data-team-search="${tab}"]`);
      next.focus();
      if(caret!==null) next.setSelectionRange(caret,caret);
    });
    const renderKeepingGroup=field=>{
      render();
      const group=root().querySelector(`[data-filter-group="${field}"]`);
      if(group) group.open=true;
    };
    el.querySelectorAll("[data-team-filter]").forEach(input=>input.onchange=()=>{
      const field=input.dataset.teamFilter;
      const values=filters[activeFilterTab][field];
      filters[activeFilterTab][field]=input.checked?[...new Set([...values,input.dataset.value])]:values.filter(x=>x!==input.dataset.value);
      renderKeepingGroup(field);
    });
    el.querySelectorAll("[data-team-date]").forEach(input=>input.onchange=()=>{
      filters[activeFilterTab][input.dataset.teamDate]=input.value;
      render();
    });
    el.querySelectorAll("[data-team-clear]").forEach(button=>button.onclick=()=>{
      const tab=button.dataset.teamClear;
      Object.keys(filters[tab]).forEach(key=>{filters[tab][key]=Array.isArray(filters[tab][key])?[]:"";});
      if(tab==="log"){view.logTypes=[];saveState();}
      render();
    });
    el.querySelectorAll("[data-team-row]").forEach(input=>input.onchange=()=>{
      const tab=input.dataset.teamRow, id=input.dataset.rowId;
      if(input.checked) selected[tab].add(id); else selected[tab].delete(id);
      render();
    });
    el.querySelectorAll("[data-team-select-all]").forEach(input=>{
      const tab=input.dataset.teamSelectAll;
      input.indeterminate=selected[tab].size>0&&!input.checked;
      input.onchange=()=>{
        selected[tab]=input.checked?new Set(visibleRows[tab].map(row=>rowId(tab,row))):new Set();
        render();
      };
    });
    el.querySelectorAll("[data-team-deselect]").forEach(button=>button.onclick=()=>{
      selected[button.dataset.teamDeselect].clear(); render();
    });
    el.querySelectorAll("[data-team-bulk]").forEach(button=>button.onclick=()=>{
      if(isPrivate()&&selected[button.dataset.teamBulk].size) bulkDialog(button.dataset.teamBulk);
    });
    el.querySelectorAll("[data-team-copy-links]").forEach(button=>button.onclick=async()=>{
      const message=el.querySelector(".team-selection-message");
      const links=[...new Set(visibleRows.activity.filter(row=>selected.activity.has(rowId("activity",row))).map(row=>row.url).filter(Boolean))];
      try{
        if(!links.length) throw new Error("Selected rows have no source links.");
        await navigator.clipboard.writeText(links.join("\n"));
        message.textContent=`Copied ${links.length} source links.`;
      }catch(err){ message.textContent="Copy failed: "+err.message; }
    });
    el.querySelectorAll(".team-inline").forEach(button=>button.onclick=()=>{
      if(!isPrivate()) return;
      if(button.dataset.type!=="meeting") return;
      const tab="log";
      const row=visibleRows[tab].find(row=>rowId(tab,row)===button.dataset.id);
      inlineEdit={tab,id:button.dataset.id,field:"title",value:row.title||""};
      render();
      root().querySelector("[data-team-edit-value]")?.focus();
    });
    el.querySelectorAll("[data-team-inline-cancel]").forEach(button=>button.onclick=()=>{
      inlineEdit=null; render();
    });
    el.querySelectorAll("[data-team-inline-field]").forEach(input=>input.onchange=()=>{
      const {tab,id}=inlineEdit;
      const row=visibleRows[tab].find(row=>rowId(tab,row)===id);
      inlineEdit.field=input.value;
      inlineEdit.value=fieldValue(tab,row,input.value);
      render();
      root().querySelector("[data-team-edit-value]")?.focus();
    });
    el.querySelectorAll("[data-team-edit-value]").forEach(input=>{
      input.oninput=input.onchange=()=>{if(inlineEdit) inlineEdit.value=input.value;};
    });
    el.querySelectorAll("[data-team-inline-form]").forEach(form=>form.onsubmit=async event=>{
      event.preventDefault();
      if(!isPrivate()) return;
      const {tab,id,field,value}=inlineEdit;
      const row=visibleRows[tab].find(row=>rowId(tab,row)===id);
      const save=form.querySelector('[type="submit"]');
      save.disabled=true;
      try{
        const entries=(row.entries||[]).map(e=>({entry_id:e.entry_id,tag:e.tag,body:e.body,visibility:e.visibility}));
        const data={id,revision:row.revision,meeting_date:row.meeting_date,title:row.title,entries};
        if(field.startsWith("entry:")) entries.find(e=>e.entry_id===field.slice(6)).body=value;
        else if(field==="new-notes") entries.push({tag:"notes",body:value,visibility:"private"});
        else data[field]=value;
        await mutate("meeting-save",data);
        inlineEdit=null;
        await refresh();
      }catch(err){
        form.querySelector(".team-inline-message").textContent=err.message;
        save.disabled=false;
      }
    });
    const toggle = document.getElementById("team-toggle-private");
    if(toggle) toggle.onclick = async()=>{
      if(isPrivate()) hidePrivate();
      else { mode="private"; saveState(); }
      await refresh();
    };
    const refreshBtn = document.getElementById("team-refresh");
    if(refreshBtn) refreshBtn.onclick = ()=>refresh();
    const guarded = action => async()=>{ try{ await action(); }catch(err){ alert("Team save failed: "+err.message); } };
    el.querySelectorAll(".team-edit").forEach(b=>b.onclick=guarded(async()=>{
      if(!isPrivate()) return alert("Enable private mode to edit team records.");
      if(b.dataset.type==="assignment") return editAssignment(b.dataset.id, b.dataset.rev);
      if(b.dataset.type==="meeting") return editMeeting(b.dataset.id, b.dataset.rev);
    }));
    el.querySelectorAll(".team-new-assignment").forEach(b=>b.onclick=guarded(()=>newAssignment(b.dataset.member)));
    el.querySelectorAll(".team-new-meeting").forEach(b=>b.onclick=guarded(()=>newMeeting(b.dataset.member)));
    el.querySelectorAll(".team-promote-raid").forEach(b=>b.onclick=guarded(()=>promoteRoadblock(findEntry(b.dataset.meeting,b.dataset.entry))));
    el.querySelectorAll(".team-item-task").forEach(a=>a.onclick=e=>{
      e.preventDefault();
      view.subtab="tasks"; filters.tasks.query=a.dataset.task; saveState(); render();
    });
    el.querySelectorAll(".team-edit-member").forEach(b=>b.onclick=()=>editMember(b.dataset.member));
    el.querySelectorAll(".team-edit-focus").forEach(b=>b.onclick=()=>editFocus(b.dataset.member));
    el.querySelectorAll("[data-log-type]").forEach(b=>b.onchange=()=>{
      view.logTypes=b.checked?[...new Set([...view.logTypes,b.dataset.logType])]:view.logTypes.filter(t=>t!==b.dataset.logType);
      saveState(); renderKeepingGroup("types");
    });
    el.querySelectorAll(".team-task-note").forEach(b=>b.onclick=()=>{
      const current=(TEAM.assignments||[]).find(a=>a.assignment_id===b.dataset.id);
      formDialog("Add task progress note",`<label>Note<textarea name="note" required></textarea></label>${visibilitySelect("visibility")}`,async data=>{
        await mutate("assignment-note",{id:current.assignment_id,revision:current.revision,...Object.fromEntries(data)});
      });
    });
    el.querySelectorAll(".team-scan").forEach(b=>b.onclick=async()=>{
      b.disabled=true;
      const message=el.querySelector(".team-scan-message");
      message.textContent="Scanning Jira and GitHub…";
      try{
        const result=await mutate("activity-scan",{member_key:b.dataset.member});
        await refresh();
        const status=root()?.querySelector(".team-scan-message");
        if(status) status.textContent=result.errors?.length?result.errors.map(e=>`${e.source}: ${e.message}`).join("; "):"Scan completed.";
      }catch(err){
        message.textContent="Scan failed: "+err.message;
      }finally{b.disabled=false;}
    });
  }

  function render(){
    const el = root(); if(!el) return;
    const content = view.tab==="overview" ? renderOverview()
      : view.tab==="plan" ? renderPlan()
      : renderMembers();
    el.innerHTML = modeBar()+buildTopTabs()+content;
    attach();
    applyTableStandards();
  }
  async function refresh(){
    try{
      if(await fetchTeam()) render();
    }catch(err){
      const el = root(); if(el) el.innerHTML = `<div class="empty">Team refresh failed: ${esc(err.message)}</div>`;
    }
  }
  function hidePrivate(){
    ++requestVersion;
    document.querySelectorAll(".team-dialog-overlay").forEach(el=>el.remove());
    Object.values(filters).forEach(filter=>Object.keys(filter).forEach(key=>{filter[key]=Array.isArray(filter[key])?[]:"";}));
    resetSelection();
    mode="presentation";
    TEAM=sharedTeam;
    saveState();
    render();
  }
  function index(){
    const out = [{k:"section",id:"team",tag:"Go to",label:"Team",icon:"✧"}];
    (TEAM.members||[]).forEach(m=>{
      out.push({k:"member",id:m.member_key,tag:"Member",label:m.display_name||m.member_key,icon:"✧"});
    });
    return out;
  }
  function navigate(hashLike){
    const h = String(hashLike||"");
    if(h.startsWith("member/")){ resetSelection(); view.tab="members"; view.member=decodeURIComponent(h.slice(7)); render(); return; }
    if(h.startsWith("team/")){ view.tab = h.slice(5) || "overview"; render(); return; }
  }

  if(state.view) view = Object.assign(view, state.view);
  if(!Array.isArray(view.logTypes)) view.logTypes=[];
  if(view.subtab==="focus") view.subtab="tasks";
  window.TeamUI = {render, refresh, hidePrivate, index, navigate};
})();
