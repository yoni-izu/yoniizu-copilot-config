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
    tasks:{query:"",category:[],priority:[],status:[],openOnly:false},
    items:{query:"",item_type:[],priority:[],status:[],follow_up:[],openOnly:false},
    activity:{query:"",source:[],kind:[],from:"",to:""},
    log:{query:"",items:[],from:"",to:""},
  };
  const selected = {tasks:new Set(), items:new Set(), activity:new Set(), log:new Set()};
  const visibleRows = {tasks:[], items:[], activity:[], log:[]};
  let inlineEdit = null;
  const taskStatuses = ["todo","backlog","in-progress","blocked","done","cancelled","assigned","open","deferred"];
  const DAY_MS = 24*60*60*1000;
  const bulkFields = {
    tasks:{category:"Category",priority:"Priority",status:"Status",target_date:"Due date"},
    items:{priority:"Priority",status:"Status",target_date:"Due date",follow_up:"Follow up"},
    log:{session_type:"Type",meeting_date:"Date",title_visibility:"Topic visibility"},
  };

  function esc(s){ return String(s??"").replace(/[&<>"]/g, m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }
  function fmtDate(s){ return window.PortalDates.display(s); }
  function dateInput(name,value,attributes=""){
    return `<input type="text" ${name?`name="${esc(name)}"`:""} data-date-field inputmode="numeric" autocomplete="off" placeholder="dd/mm/yyyy" pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}" title="Enter date as dd/mm/yyyy" value="${esc(window.PortalDates.input(value))}" ${attributes}>`;
  }
  function normalizedDateFormData(form){
    const data=new FormData(form);
    form.querySelectorAll("[data-date-field]").forEach(input=>{
      const iso=window.PortalDates.parse(input.value);
      if(input.value.trim()&&!iso){
        input.setCustomValidity("Enter a valid date as dd/mm/yyyy.");
        input.reportValidity();
        throw new Error("Enter valid dates as dd/mm/yyyy.");
      }
      input.setCustomValidity("");
      data.set(input.name,iso);
    });
    return data;
  }
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
  function filterToolbar(tab, groups, count, total, dates=false, openOnly=false){
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
      ${dates?`<label>From${dateInput("",filter.from,`data-team-date="from"`)}</label><label>Through${dateInput("",filter.to,`data-team-date="to"`)}</label>`:""}
      ${openOnly?`<div><label class="chip${filter.openOnly?" on":""}"><input type="checkbox" data-team-open-only ${filter.openOnly?"checked":""}> Open only</label></div>`:""}
      <button class="btn" data-team-clear="${tab}">Clear filters</button>
      <span class="cap team-filter-count" role="status">${count} / ${total} matching</span>
      ${dates&&filter.from&&filter.to&&filter.from>filter.to?'<span role="alert">From date must not be after Through date.</span>':""}
    </div>`;
  }
  function rowId(tab, row){
    return tab==="items"?row.item_id:tab==="tasks"?row.assignment_id:tab==="log"?row.meeting_id:
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
    const choices=field==="follow_up"?{true:"Follow up next 1:1",false:"No follow-up"}:
      field==="category"?(TEAM.task_categories||["development","debug","research","planning","testing","code-review","track","misc"]):
      field==="priority"?["critical","high","medium","low"]:field==="status"?
        (tab==="items"?[...new Set([...(TEAM.item_statuses||[]),...taskStatuses])]:taskStatuses):
      field==="session_type"?(TEAM.log_types||{one_on_one:"1:1",conversation:"Conversation",meeting:"Meeting",note:"Note"}):
      field==="title_visibility"?{private:"Private",shared:"Presentation"}:null;
    if(choices){
      const entries=Array.isArray(choices)?choices.map(v=>[v,v]):Object.entries(choices);
      return `<select name="value" data-team-edit-value>${entries.map(([v,label])=>`<option value="${esc(v)}" ${v===value?"selected":""}>${esc(label)}</option>`).join("")}</select>`;
    }
    if(field==="description"||field==="new-notes"||field.startsWith("entry:")){
      return `<textarea name="value" data-team-edit-value>${esc(value)}</textarea>`;
    }
    if(field.endsWith("_date")) return dateInput("value",value,`data-team-edit-value ${field==="meeting_date"?"required":""}`);
    return `<input name="value" data-team-edit-value type="text" value="${esc(value)}">`;
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
    formDialog(`Edit ${rows.length} selected ${tab==="log"?"log entries":tab==="items"?"items":"tasks"}`,`
      <p>Apply one field to all ${rows.length} selected records for ${esc(memberByKey(view.member)?.display_name||view.member)}. No hidden rows will change.</p>
      <ul class="team-bulk-preview">${rows.map(row=>`<li>${esc(row.title||rowId(tab,row))}</li>`).join("")}</ul>
      <label>Field<select name="field">${Object.entries(fields).map(([key,label])=>`<option value="${key}">${label}</option>`).join("")}</select></label>
      <label class="team-bulk-value">New value${valueControl(tab,first,fieldValue(tab,rows[0],first))}</label>
      <p class="cap">Save confirms this change. A stale record cancels the entire batch; reload and reselect after a conflict.</p>`,
      async data=>{
        const field=data.get("field");
        await mutate("bulk-set",{entity_type:tab==="items"?"item":tab==="tasks"?"assignment":"meeting",member_key:view.member,
          items,field,value:field==="follow_up"?data.get("value")==="true":
            data.get("value")||(field==="target_date"?null:"")});
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
    const tabs = [["tasks",TEAM.items?"Items":"Tasks"],["activity","Activity"],["oneonone","Log"],["reviews","Reviews"]];
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
        ${type==="meeting"?`<button class="btn danger team-delete-meeting" data-id="${esc(id)}" data-rev="${esc(rev||"")}">Delete entry</button>`:""}
        <button class="btn team-edit" data-type="${type}" data-id="${esc(id)}" data-rev="${esc(rev||"")}">Edit details</button>`
      : type==="meeting"
        ? `<button class="btn team-edit" data-type="${type}" data-id="${esc(id)}" data-rev="${esc(rev||"")}">Edit shared log</button>`
        : "";
  }
  function memberTasks(m){
    const all = memberAssignments(m.member_key), filter=filters.tasks;
    const list=all.filter(a=>matchesText(a,filter.query) &&
      matchesChoices(a.category||"misc",filter.category) &&
      matchesChoices(a.priority||"medium",filter.priority) && matchesChoices(a.status,filter.status) &&
      (!filter.openOnly || !["done","cancelled"].includes(a.status)));
    return `<div class="tile"><h3>Tasks ${isPrivate()?'<button class="btn primary team-new-assignment" data-member="'+esc(m.member_key)+'">New task</button>':""}</h3>
      ${filterToolbar("tasks",[["category","Category",all.map(a=>a.category||"misc")],["priority","Priority",all.map(a=>a.priority||"medium")],["status","Status",all.map(a=>a.status)]],list.length,all.length,false,true)}
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
  function memberItems(m){
    if(!TEAM.items) return memberTasks(m);
    const all=TEAM.items.filter(i=>i.member_key===m.member_key||i.owner_key===m.member_key);
    const filter=filters.items;
    const labels=Object.fromEntries(Object.keys(itemTypes()).map(tag=>[tag,itemLabel(tag)]));
    labels.action="Task";
    const list=all.filter(i=>matchesText(i,filter.query)&&
      matchesChoices(i.item_type,filter.item_type)&&matchesChoices(i.priority,filter.priority)&&
      matchesChoices(i.status,filter.status)&&matchesChoices(String(i.follow_up),filter.follow_up)&&
      (!filter.openOnly||!["done","cancelled"].includes(i.status)));
    const history=i=>`<details class="team-item-history"><summary>History (${(i.occurrences||[]).length} log rows, ${(i.updates||[]).length} updates)</summary>
      <ul>${(i.occurrences||[]).map(o=>`<li><button class="btn team-item-meeting" data-member="${esc(i.member_key)}" data-meeting="${esc(o.meeting_id)}">${fmtDate(o.meeting_date)} · ${esc(o.title)}</button> ${esc(o.body)}</li>`).join("")}
      ${(i.updates||[]).map(u=>`<li>${fmtDate(u.created_at)}: ${esc(u.note)}</li>`).join("")}</ul></details>`;
    return `<div class="tile"><h3>Items ${isPrivate()?`<button class="btn primary team-new-assignment" data-member="${esc(m.member_key)}">New task</button>
      <button class="btn team-new-meeting" data-member="${esc(m.member_key)}">New 1:1 entry</button>`:""}</h3>
      <p class="cap">Tasks are one item type. Checked follow-ups carry into the next 1:1 until cleared or completed.</p>
      ${filterToolbar("items",[["item_type","Type",all.map(i=>i.item_type),labels],["priority","Priority",all.map(i=>i.priority)],
        ["status","Status",all.map(i=>i.status)],["follow_up","Follow up",["true","false"],{true:"Needed",false:"Not needed"}]],list.length,all.length,false,true)}
      ${selectionBar("items",list)}
      <table data-table-key="team-member-items"><thead><tr><th aria-label="Selection" data-no-sort></th><th>ID</th><th>Type</th><th>Item</th><th>Status</th><th>Priority</th><th>Owner</th><th data-sort-type="date">Due</th><th>Follow up</th><th data-no-sort></th></tr></thead><tbody>
      ${list.map(i=>`<tr><td>${rowCheckbox("items",i)}</td><td><code>${esc(i.item_id)}</code></td>
        <td><span class="badge">${esc(labels[i.item_type]||itemLabel(i.item_type))}</span></td>
        <td><b>${esc(i.body)}</b>${i.fields?`<div class="team-item-meta">${itemDetails(i.member_key,{tag:i.item_type,fields:i.fields})}</div>`:""}${history(i)}</td>
        <td>${esc(i.status)}</td><td>${esc(i.priority)}</td>
        <td>${esc(i.owner_label==="manager"?(TEAM.manager_name||"Manager"):i.owner_key?
          (memberByKey(i.owner_key)?.display_name||i.owner_key):i.owner?partyLabel(i.member_key,i.owner):"Unassigned")}</td>
        <td data-sort-value="${esc(i.target_date||"")}">${fmtDate(i.target_date)}</td>
        <td>${i.follow_up?"Needed":"—"}</td><td>${isPrivate()?`<button class="btn team-edit-item" data-id="${esc(i.item_id)}">Edit tracking</button>
          <button class="btn team-item-note" data-id="${esc(i.item_id)}">Add update</button>
          ${i.assignment_id?rowEditBtns("assignment",i.assignment_id,i.assignment_revision):""}`:""}</td></tr>`).join("")}
      ${list.length?"":'<tr><td colspan="10">No matching items.</td></tr>'}</tbody></table></div>`;
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
      <p class="cap">${Object.entries(scanned.sources||{}).map(([source,s])=>`${esc(source)}: ${s.last_success_at?fmtDate(s.last_success_at):"not scanned"}`).join(" · ")}</p>
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
    return spec.fields.filter(f=>f.key!==spec.summary && f.key!=="priority" &&
      !(e.tag==="discussion"&&f.key==="follow_up") &&
      e.fields[f.key]!==undefined && e.fields[f.key]!=="" && e.fields[f.key]!==false)
      .map(f=>{
        let v=e.fields[f.key];
        if(f.input==="checkbox") v="yes";
        else if(f.input==="task") v=taskTitle(v);
        else if(f.input==="select" && ["owner","raised_by","decided_by","unblocker"].includes(f.key)) v=partyLabel(member,v);
        else if(f.input==="date") v=fmtDate(v);
        return `<span class="team-item-kv"><b>${esc(f.label)}:</b> ${esc(v)}</span>`;
      }).join("");
  }
  function priorityLabel(value){
    const priority=value||"medium";
    return priority.charAt(0).toUpperCase()+priority.slice(1);
  }
  function meetingEntries(mt){
    const entries = (mt.entries || []).map((entry,index)=>({entry,index})).sort((a,b)=>{
      const category=itemLabel(a.entry.tag).localeCompare(itemLabel(b.entry.tag));
      const priority=["critical","high","medium","low"].indexOf(a.entry.priority||"medium")-
        ["critical","high","medium","low"].indexOf(b.entry.priority||"medium");
      return category||priority||a.index-b.index;
    }).map(item=>item.entry);
    return entries.map(e=>{
      const typed=isTypedEntry(e);
      const raid=e.raid_id?` <span class="chip team-raid-chip">RAID ${esc(e.raid_id)}</span>`:
        (isPrivate() && e.tag==="roadblock" && e.record_id?` <button class="btn team-promote-raid" data-meeting="${esc(mt.meeting_id)}" data-entry="${esc(e.entry_id)}">Promote to RAID</button>`:"");
      const task=e.tag==="action" && e.record_id?` <a href="#" class="team-item-task" data-task="${esc(e.record_id)}">task</a>`:"";
      return `<li class="team-item team-item-${esc(e.tag||"note")}"><details class="team-item-view">
        <summary><span class="badge team-item-badge" data-item-type="${esc(e.tag||"note")}">${esc(itemLabel(e.tag))}</span>
          <span class="team-item-view-summary">${esc(e.body)}</span>
          <span class="chip team-priority">Priority: ${esc(priorityLabel(e.priority))}</span>
          ${e.tracking?`<span class="chip">${esc(e.tracking.status)}</span>`:""}
          ${e.follow_up_from?`<span class="chip team-follow-up-label">Follow-up from ${fmtDate(e.follow_up_from)}</span>`:""}
          ${e.tracking?.follow_up?'<span class="chip team-follow-up-label">Follow up next 1:1</span>':""}</summary>
        <div class="team-item-view-details">${task}${raid}
          ${typed?`<div class="team-item-meta">${itemDetails(mt.member_key,e)}</div>`:""}
          ${isPrivate()?`<i class="cap">(${esc(e.visibility==="shared"?"presentation":"private")})</i>`:""}
        </div></details></li>`;
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
      <p class="cap">${esc(mt.shared_summary||mt.private_summary||"")}</p><ul>${meetingEntries(mt)}</ul>${rowEditBtns("meeting",mt.meeting_id,mt.revision||mt.edit_token)}${inlineEditor("log",mt)}</div></div>`).join("")}
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
    let body = memberItems(member);
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
    const sharedEdit=action==="meeting-shared-save";
    if(!isPrivate()&&!sharedEdit) throw new Error("Enable private mode to edit team records.");
    const headers={"Content-Type":"application/json"};
    if(!sharedEdit) headers["X-Team-Private"]="1";
    const res = await fetch("/api/team", {
      method:"POST",
      headers,
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
  function prioritySelect(name,value="medium"){
    const priorities=TEAM.log_item_priorities||["critical","high","medium","low"];
    return `<label>Priority<select name="${name}">${priorities.map(priority=>
      `<option value="${esc(priority)}" ${priority===value?"selected":""}>${esc(priorityLabel(priority))}</option>`).join("")}</select></label>`;
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
        await onSave(normalizedDateFormData(form), form);
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
      <label>Due date${dateInput("target_date",current?.target_date||"")}</label></div>
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
  async function deleteMeeting(id, revision){
    if(!isPrivate()) throw new Error("Enable private mode to delete team records.");
    const current=meetingById(id);
    if(!current) throw new Error("Log entry is no longer available; refresh and try again.");
    const title=current.title||"this log entry";
    if(!confirm(`Delete "${title}"? Linked progress, roadblock, and feedback records will be removed; task assignments will remain.`)) return;
    await mutate("meeting-delete",{id,revision});
    await refresh();
  }
  async function newMeeting(member){
    if(!TEAM.items) return meetingDialog(member);
    const draft=(await mutate("meeting-prepare",{member_key:member})).entity;
    if(!isPrivate()) throw new Error("Enable private mode to prepare a 1:1.");
    meetingDialog(member,null,draft);
  }
  function trackingControls(member,entry={},full=false){
    if(!TEAM.items) return "";
    const tracking=entry.tracking||{
      status:entry.tracking_status,owner:entry.tracking_owner,target_date:entry.tracking_due,
      follow_up:entry.follow_up,
    };
    const task=!entry.generic&&(entry.assignment_id||entry.item_type==="action"&&entry.fields?.title||
      entry.tag==="action"&&(entry.fields===undefined||entry.fields?.title||entry.record_id||entry.locked));
    const statuses=task?taskStatuses:(TEAM.item_statuses||["unspecified","open","in-progress","blocked","done","cancelled","deferred"]);
    const status=tracking.status||(task?"todo":"open");
    return `<div class="team-tracking-fields">
      <label>Tracking status<select name="tracking_status">${statuses.map(s=>`<option value="${esc(s)}" ${s===status?"selected":""}>${esc(s==="unspecified"?"Unspecified":s)}</option>`).join("")}</select></label>
      ${!task||full?`<label>Owner (optional)<select name="tracking_owner">${(task?["me","member"]:["","me","member"]).map(owner=>
        `<option value="${owner}" ${owner===(tracking.owner||"")?"selected":""}>${esc(owner?partyLabel(member,owner):"Unassigned")}</option>`).join("")}</select></label>
        <label>Due (optional)${dateInput("tracking_due",tracking.target_date||"")}</label>`:""}
      <label class="team-item-check"><input type="checkbox" name="follow_up" ${tracking.follow_up?"checked":""}> Follow up next 1:1</label>
    </div>`;
  }
  function trackingValues(container,original=null){
    const follow=container.querySelector('[name="follow_up"]');
    if(!follow) return {};
    const status=container.querySelector('[name="tracking_status"]');
    const owner=container.querySelector('[name="tracking_owner"]');
    const due=container.querySelector('[name="tracking_due"]');
    const values={follow_up:follow.checked,tracking_status:status.value};
    if(owner) values.tracking_owner=owner.value||null;
    if(due){
      const day=window.PortalDates.parse(due.value);
      if(due.value.trim()&&!day) throw new Error("Enter a valid due date as dd/mm/yyyy.");
      values.tracking_due=day||null;
    }
    if(original?.item_id){
      const before=original.tracking||{status:original.tracking_status,owner:original.tracking_owner,
        target_date:original.tracking_due};
      if(values.tracking_status===before.status) delete values.tracking_status;
      if(values.tracking_owner===before.owner) delete values.tracking_owner;
      if(values.tracking_due===before.target_date) delete values.tracking_due;
      values.item_id=original.item_id;
      values.item_revision=original.item_revision||original.item_edit_token;
    }
    return values;
  }
  function editItem(ident){
    const item=(TEAM.items||[]).find(i=>i.item_id===ident);
    if(!item) throw new Error("Tracked item not found; refresh Team.");
    formDialog("Edit item tracking",`<p><code>${esc(item.item_id)}</code> · ${esc(item.item_type==="action"?"Task":itemLabel(item.item_type))}</p>
      <h4>${esc(item.body)}</h4>${trackingControls(item.member_key,{...item,tracking:item},true)}
      ${prioritySelect("priority",item.priority)}${visibilitySelect("visibility",item.visibility)}
      <p class="cap">This updates the tracked item. Previous 1:1 text stays in its history.</p>`,
      async(data,form)=>{
        const values=trackingValues(form);
        await mutate("item-set",{id:item.item_id,revision:item.revision,
          status:values.tracking_status,owner:values.tracking_owner,target_date:values.tracking_due,
          follow_up:values.follow_up,priority:data.get("priority"),visibility:data.get("visibility")});
      });
  }
  function itemFieldInput(member, field, value, linkedStatusTask=false){
    const name=`f:${field.key}`;
    const req=field.required?"required":"";
    const statusMode=field._tag==="status"&&field.key==="activity"?"standalone":
      field._tag==="status"&&field.key==="status"?"linked":null;
    const modeAttrs=statusMode?` data-status-mode="${statusMode}" ${statusMode==="standalone"&&linkedStatusTask||statusMode==="linked"&&!linkedStatusTask?"hidden":""}`:"";
    if(field.input==="textarea") return `<label class="team-item-wide">${esc(field.label)}<textarea name="${name}" rows="${field._detail?5:2}" ${req}>${esc(value||"")}</textarea></label>`;
    if(field.input==="checkbox") return `<label class="team-item-check"><input type="checkbox" name="${name}" ${value?"checked":""}> ${esc(field.label)}</label>`;
    if(field.input==="date") return `<label>${esc(field.label)}${dateInput(name,value||"",req)}</label>`;
    if(field.input==="task"){
      const tasks=memberAssignments(member);
      return `<label>${esc(field.label)}<select name="${name}" ${req}><option value="">— no task (standalone update) —</option>${tasks.map(t=>`<option value="${esc(t.assignment_id)}" ${t.assignment_id===value?"selected":""}>${esc(t.title)} (${esc(t.status||"")})</option>`).join("")}</select></label>`;
    }
    if(field.input==="select"){
      const party=["owner","raised_by","decided_by","unblocker"].includes(field.key);
      const disabled=field._tag==="status"&&field.key==="status"&&!linkedStatusTask?"disabled":"";
      return `<label${modeAttrs}>${esc(field.label)}<select name="${name}" ${disabled}>${field.options.map(o=>`<option value="${esc(o)}" ${o===(value??field.options[0])?"selected":""}>${esc(o===""?"(unchanged)":party?partyLabel(member,o):o)}</option>`).join("")}</select></label>`;
    }
    return `<label${modeAttrs} class="${field.key===itemTypes()[field._tag]?.summary?"team-item-main":""}">${esc(field.label)}<input name="${name}" value="${esc(value||"")}" ${req}></label>`;
  }
  function itemRowTitle(tag, fields, entry){
    const spec=itemTypes()[tag];
    if(!spec) return entry?.body||"New item";
    if(tag==="status")
      return fields.assignment_id?taskTitle(fields.assignment_id):
        fields.activity||fields[spec.summary]||"New item";
    return entry?.body||fields[spec.summary]||"New item";
  }
  function itemRow(member, tag, entry=null, sharedOnly=false){
    const known=itemTypes()[tag]&&(!entry||entry.fields)?itemTypes()[tag]:null;
    const spec=known||{label:itemLabel(tag),summary:"body",fields:[]};
    const fields=entry?.fields||{};
    const linkedStatusTask=tag==="status"&&!!fields.assignment_id;
    const locked=tag==="action" && !!(entry?.record_id||entry?.locked);
    const priority=entry?.priority||fields.priority||"medium";
    const priorities=TEAM.log_item_priorities||["critical","high","medium","low"];
    const categories=Object.entries(itemTypes());
    if(!known) categories.push([tag,spec]);
    const visibility=sharedOnly?`<span class="cap">Presentation</span>`:
      spec.always_private?`<span class="cap">Always private</span>`:
        visibilitySelect("visibility",entry?.visibility||spec.default_visibility||"shared");
    const mainKeys=tag==="status"?["activity",spec.summary]:[spec.summary];
    const mainFields=spec.fields.filter(f=>mainKeys.includes(f.key));
    const detailFields=spec.fields.filter(f=>!mainFields.includes(f)&&f.key!=="priority"&&
      !(tag==="discussion"&&f.key==="follow_up"));
    const body=!known?`<label>Entry<textarea name="body" rows="3">${esc(entry?.body||"")}</textarea></label>`:locked
      ? `<div class="team-item-locked"><b>${esc(fields.title||entry.body)}</b> ${itemDetails(member,entry)}
          <span class="cap">Saved as task${entry.record_id?` <code>${esc(entry.record_id)}</code>`:""} — edit it in Tasks.</span></div>`
      : `${mainFields.length?`<div class="team-item-summary-field">${mainFields.map(f=>itemFieldInput(member,{...f,_tag:tag},fields[f.key],linkedStatusTask)).join("")}</div>`:""}
        ${detailFields.length?`<details class="team-item-extra"><summary>More details</summary>
          <div class="team-item-fields">${detailFields.map(f=>itemFieldInput(member,{...f,_tag:tag,_detail:true},fields[f.key],linkedStatusTask)).join("")}</div>
        </details>`:""}`;
    const raid=tag!=="roadblock"?"":entry?.raid_id?`<span class="chip team-raid-chip">RAID ${esc(entry.raid_id)}</span>`:
      entry?.record_id?`<button type="button" class="btn team-promote-raid" data-meeting="${esc(entry.meeting_id||"")}" data-entry="${esc(entry.entry_id)}">Promote to RAID</button>`:
      `<span class="cap">Save to enable RAID promotion</span>`;
    return `<details class="team-item-row" data-item-tag="${esc(tag)}" data-item-type="${esc(tag)}"
        ${!known?'data-item-generic="true"':""}
        ${entry?.follow_up_from?`data-follow-up-item="${esc(entry.item_id)}"`:""}
        ${entry?.entry_id?`data-entry-id="${esc(entry.entry_id)}"`:""} ${entry?"":"open"}>
      <summary class="team-item-row-summary"><span class="badge team-item-badge" data-item-type="${esc(tag)}">${esc(spec.label)}</span>
        <span class="team-item-row-title">${esc(itemRowTitle(tag,fields,entry))}</span>
        <span class="chip team-priority">Priority: ${esc(priorityLabel(priority))}</span>
        ${entry?.follow_up_from?`<span class="chip team-follow-up-label">Follow-up from ${fmtDate(entry.follow_up_from)}</span>`:""}</summary>
      <div class="team-item-row-content">
        <div class="team-item-head"><label>Category<select name="item_tag" ${entry?.follow_up_from?"disabled":""}>${categories.map(([key,item])=>
          `<option value="${esc(key)}" ${key===tag?"selected":""}>${esc(item.label)}</option>`).join("")}</select></label>
          <label>Priority<select name="priority" ${locked?"disabled":""}>${priorities.map(value=>
            `<option value="${esc(value)}" ${value===priority?"selected":""}>${esc(priorityLabel(value))}</option>`).join("")}</select></label>
          ${locked?'<span class="cap">Change task priority in Tasks.</span>':""}${visibility}${raid}
          <button type="button" class="btn team-item-remove" aria-label="Remove ${esc(spec.label)}">−</button></div>
        ${body}${trackingControls(member,{...entry,tag,generic:!known})}</div></details>`;
  }
  function itemRowValue(row, entryById){
    const id=row.dataset.entryId;
    const tag=row.querySelector('[name="item_tag"]').value;
    const spec=row.dataset.itemGeneric?null:itemTypes()[tag];
    const original=id?entryById[id]:null;
    let fields;
    if(!spec) fields=null;
    else if(tag==="action" && original?.tag==="action" && (original?.record_id||original?.locked))
      fields={...original.fields};
    else{
      fields={};
      spec.fields.filter(f=>f.key!=="priority"&&!(tag==="discussion"&&f.key==="follow_up")).forEach(f=>{
        const input=row.querySelector(`[name="f:${f.key}"]`);
        if(!input) return;
        if(f.input==="checkbox") fields[f.key]=input.checked;
        else if(f.input==="date"){
          const iso=window.PortalDates.parse(input.value);
          if(input.value.trim()&&!iso) throw new Error("Enter a valid date as dd/mm/yyyy.");
          if(iso) fields[f.key]=iso;
        }else if(input.value.trim()) fields[f.key]=input.value.trim();
      });
    }
    const priority=row.querySelector('[name="priority"]').value||"medium";
    if(tag==="action"&&fields) fields.priority=priority;
    const vis=row.querySelector('[name="visibility"]');
    return {...(id?{entry_id:id}:{}),tag,...(fields===null?{body:row.querySelector('[name="body"]').value}:{fields}),priority,
      ...trackingValues(row,original?.tag===tag?original:null),
      visibility:spec?.always_private?"private":(vis?.value||"shared")};
  }
  function meetingDialog(member, current=null, draft=null){
    const presentationEdit=!!current&&!isPrivate();
    const existing = current?.entries||draft?.entries||[];
    const note = current?existing.find(e=>e.tag==="notes" || e.tag==="note"):null;
    const typed = existing.filter(e=>e!==note && (!current||isTypedEntry(e)));
    const legacy = current?existing.filter(e=>e!==note && !isTypedEntry(e)):[];
    const entryById=Object.fromEntries(existing.map(e=>[e.entry_id,{...e,meeting_id:current?.meeting_id}]));
    const legacyFields=(entry,index)=>`<details class="team-item-row team-entry" data-entry-index="${index}" open>
      <summary class="team-item-row-summary"><span class="badge">${esc(itemLabel(entry.tag))}</span>
        <span class="team-item-row-title">${esc(entry.body)}</span>
        <span class="chip team-priority">Priority: ${esc(priorityLabel(entry.priority))}</span></summary>
      <div class="team-item-row-content"><label>Category<input name="tag" value="${esc(entry.tag)}" required></label>
        <label>Entry<textarea name="body" rows="2">${esc(entry.body)}</textarea></label>
        ${prioritySelect("priority",entry.priority||"medium")}
        ${presentationEdit?"":visibilitySelect("visibility",entry.visibility||"shared")}
        ${trackingControls(member,entry)}</div></details>`;
    const types=TEAM.log_types||{one_on_one:"1:1",conversation:"Conversation",meeting:"Meeting",note:"Note"};
    const counterpart=isLeadKey(member)?(TEAM.manager_name||"Manager"):(memberByKey(member)?.display_name||member);
    const showTopic=!presentationEdit||current.title_visibility==="shared";
    formDialog(current?"Edit log entry":"New log entry", `
      <div class="team-session-meta">
        <label>Type<select name="session_type">${Object.entries(types).map(([key,label])=>`<option value="${key}" ${key===(current?.session_type||"one_on_one")?"selected":""}>${esc(label)}</option>`).join("")}</select></label>
        ${showTopic?`<label>Topic (optional)<input type="text" name="title" value="${esc(current?.title||"")}" placeholder="1:1 with ${esc(counterpart)} - YYYY-MM-DD"></label>`
          :'<p class="cap">Topic is hidden and will remain unchanged in Presentation mode.</p>'}
        <label>Date (blank = today)${dateInput("meeting_date",current?.meeting_date||"")}</label>
        ${!presentationEdit||showTopic?visibilitySelect("title_visibility",current?.title_visibility||"shared","Topic visibility"):""}
      </div>
      <div class="team-item-toolbar" role="group" aria-label="Add item">${Object.entries(itemTypes()).map(([tag,spec])=>
        `<button type="button" class="btn team-add-item" data-item-type="${esc(tag)}">+ ${esc(spec.label)}</button>`).join("")}</div>
      ${!current&&TEAM.items?'<p class="cap">Checked items below are follow-ups from previous 1:1s. Clear their follow-up checkbox to stop carrying them. Removing a carried row only skips it in this draft.</p><button type="button" class="btn team-reload-follow-ups">Reload follow-ups</button>':""}
      <div class="team-item-rows">${typed.map(e=>itemRow(member,e.tag,entryById[e.entry_id],presentationEdit)).join("")}</div>
      <p class="cap team-item-empty" ${typed.length?"hidden":""}>Add items with the buttons above: actions, decisions, roadblocks, feedback…</p>
      ${legacy.length?`<details class="team-legacy-entries"><summary>Other entries (${legacy.length})</summary>${legacy.map(legacyFields).join("")}</details>`:""}
      <details class="team-item-row team-notes-row" open>
        <summary class="team-item-row-summary"><span class="badge">Notes</span>
          <span class="team-item-row-title">${esc(note?.body||"Add a short note…")}</span>
          <span class="chip team-priority">Priority: ${esc(priorityLabel(note?.priority))}</span>
          <span class="team-notes-toggle" aria-hidden="true"><span class="team-notes-expand">&#9656; Expand</span><span class="team-notes-collapse">&#9662; Collapse</span></span></summary>
        <div class="team-notes-box"><label>Notes<textarea name="notes" rows="3" placeholder="Short freestyle notes (optional)…">${esc(note?.body||"")}</textarea></label>
          ${prioritySelect("notes_priority",note?.priority||"medium")}
          ${presentationEdit?"":visibilitySelect("notes_visibility",note?.visibility||"shared","Notes visibility")}
          ${trackingControls(member,note||{})}</div>
      </details>
      ${current?`<p class="cap">Removing or reclassifying a roadblock, feedback or status row deletes its linked record; saved action items remain in Tasks.</p>`:""}`,
      async (data,form)=>{
        const entries=[];
        if(data.get("notes") || note){
          entries.push({...(note?{entry_id:note.entry_id}:{}),tag:note?.tag||"notes",body:data.get("notes"),
            ...trackingValues(form.querySelector(".team-notes-row"),note),
            priority:data.get("notes_priority")||"medium",
            visibility:presentationEdit?"shared":data.get("notes_visibility")});
        }
        form.querySelectorAll(".team-item-rows .team-item-row").forEach(row=>entries.push(itemRowValue(row,entryById)));
        form.querySelectorAll(".team-entry").forEach(row=>{
          const original=legacy[Number(row.dataset.entryIndex)];
          entries.push({entry_id:original.entry_id,tag:row.querySelector('[name="tag"]').value,
            body:row.querySelector('[name="body"]').value,
            priority:row.querySelector('[name="priority"]').value,
            ...trackingValues(row,original.tag===row.querySelector('[name="tag"]').value?original:null),
            visibility:presentationEdit?"shared":row.querySelector('[name="visibility"]').value});
        });
        const fields={meeting_date:data.get("meeting_date"),session_type:data.get("session_type"),entries};
        if(!presentationEdit||showTopic){
          fields.title=data.get("title");
          fields.title_visibility=data.get("title_visibility");
        }
        await mutate(presentationEdit?"meeting-shared-save":current?"meeting-save":"meeting-create",current
          ?{id:current.meeting_id,revision:current.revision||current.edit_token,...fields}:{member_key:member,...fields});
      },{large:true,setup:form=>{
        const rows=form.querySelector(".team-item-rows");
        const sync=()=>{ form.querySelector(".team-item-empty").hidden=!!rows.children.length; };
        const noteRow=form.querySelector(".team-notes-row");
        const notes=noteRow.querySelector('[name="notes"]');
        const notePriority=noteRow.querySelector('[name="notes_priority"]');
        const updateNotesPreview=()=>{
          noteRow.querySelector(".team-item-row-title").textContent=notes.value||"Add a short note…";
          noteRow.querySelector(".team-priority").textContent=`Priority: ${priorityLabel(notePriority.value)}`;
        };
        notes.addEventListener("input",updateNotesPreview);
        notePriority.addEventListener("change",updateNotesPreview);
        const wire=row=>{
          row.querySelector(".team-item-remove").onclick=()=>{ row.remove(); sync(); };
          const category=row.querySelector('[name="item_tag"]');
          category.onchange=()=>{
            const oldTag=row.dataset.itemTag, oldSpec=itemTypes()[oldTag]||{summary:"body"}, newTag=category.value;
            const newSpec=itemTypes()[newTag];
            const oldSummary=row.querySelector(`[name="f:${oldSpec.summary}"]`)||row.querySelector('[name="body"]');
            const summary=oldSummary?.value||entryById[row.dataset.entryId]?.body||"";
            const priority=row.querySelector('[name="priority"]').value||"medium";
            const visibility=presentationEdit?"shared":row.querySelector('[name="visibility"]')?.value||"shared";
            const tracking=trackingValues(row);
            const entry={...(row.dataset.entryId?{entry_id:row.dataset.entryId}:{}),
              body:summary,fields:{[newSpec.summary]:summary},priority,visibility,
              tracking:{status:newTag==="action"?"todo":tracking.tracking_status,owner:tracking.tracking_owner,
                target_date:tracking.tracking_due,follow_up:tracking.follow_up}};
            const holder=document.createElement("div");
            holder.innerHTML=itemRow(member,newTag,entry,presentationEdit);
            const replacement=holder.firstElementChild;
            row.replaceWith(replacement); wire(replacement);
            replacement.open=true;
            replacement.querySelector(".team-item-summary-field input,.team-item-summary-field textarea")?.focus();
          };
          const updatePreview=()=>{
            const tag=row.dataset.itemTag, spec=itemTypes()[tag]||{summary:"body"};
            const linkedTask=tag==="status"?row.querySelector('[name="f:assignment_id"]')?.value:"";
            const activity=tag==="status"?(row.querySelector('[name="f:activity"]')?.value||"").trim():"";
            const summary=linkedTask?taskTitle(linkedTask):
              activity||row.querySelector(`[name="f:${spec.summary}"]`)?.value||
              row.querySelector('[name="body"]')?.value||"New item";
            row.querySelector(".team-item-row-title").textContent=summary;
            row.querySelector(".team-priority").textContent=
              `Priority: ${priorityLabel(row.querySelector('[name="priority"]').value)}`;
          };
          row.querySelectorAll(".team-item-summary-field input,.team-item-summary-field textarea")
            .forEach(el=>el.addEventListener("input",updatePreview));
          row.querySelector('[name="body"]')?.addEventListener("input",updatePreview);
          if(row.dataset.followUpItem){
            const dirty=()=>{ row.dataset.followUpDirty="true"; };
            row.addEventListener("input",dirty);
            row.addEventListener("change",dirty);
          }
          row.querySelector('[name="priority"]').addEventListener("change",updatePreview);
          if(row.dataset.itemTag==="status"){
            const task=row.querySelector('[name="f:assignment_id"]');
            const activity=row.querySelector('[name="f:activity"]');
            const taskStatus=row.querySelector('[name="f:status"]');
            const activityLabel=row.querySelector('[data-status-mode="standalone"]');
            const statusLabel=row.querySelector('[data-status-mode="linked"]');
            // Guards tolerate a served spec older/newer than this asset.
            const syncStatusFields=()=>{
              const linked=!!task.value;
              if(activityLabel) activityLabel.hidden=linked;
              if(activity){ activity.disabled=linked; if(linked) activity.value=""; }
              if(statusLabel) statusLabel.hidden=!linked;
              if(taskStatus){ taskStatus.disabled=!linked; if(!linked) taskStatus.value=""; }
              updatePreview();
            };
            if(task){ task.onchange=syncStatusFields; syncStatusFields(); }
          }
          const promote=row.querySelector(".team-promote-raid");
          if(promote) promote.onclick=()=>promoteRoadblock(entryById[promote.dataset.entry]);
        };
        rows.querySelectorAll(".team-item-row").forEach(wire);
        form.querySelectorAll(".team-add-item").forEach(btn=>btn.onclick=()=>{
          const holder=document.createElement("div");
          holder.innerHTML=itemRow(member,btn.dataset.itemType,null,presentationEdit);
          const row=holder.firstElementChild;
          rows.append(row); wire(row); sync();
          row.querySelector(".team-item-summary-field input,.team-item-summary-field textarea")?.focus();
        });
        if(!current&&TEAM.items){
          let prepareVersion=0;
          const dayInput=form.querySelector('[name="meeting_date"]');
          const typeInput=form.querySelector('[name="session_type"]');
          const save=form.querySelector('[type="submit"]');
          const message=form.querySelector(".ws-modal-msg");
          let previousDay=dayInput.value, previousType=typeInput.value;
          const reload=async()=>{
            const version=++prepareVersion;
            save.disabled=true;
            message.textContent="Loading follow-ups…";
            try{
              const day=window.PortalDates.parse(dayInput.value);
              if(dayInput.value.trim()&&!day) throw new Error("Enter a valid date as dd/mm/yyyy.");
              const result=await mutate("meeting-prepare",{member_key:member,meeting_date:day||"",
                session_type:typeInput.value});
              if(version!==prepareVersion||!form.isConnected||!isPrivate()) return;
              const carried=result.entity.entries;
              const ids=new Set(carried.map(e=>e.item_id));
              const oldRows=[...rows.querySelectorAll("[data-follow-up-item]")];
              const removed=oldRows.filter(row=>!ids.has(row.dataset.followUpItem));
              if(removed.some(row=>row.dataset.followUpDirty)&&
                !confirm("Changing the date/type removes edited follow-up rows from this draft. Continue?")){
                dayInput.value=previousDay; typeInput.value=previousType;
                message.textContent="Date/type change cancelled; your follow-up edits remain.";
                save.disabled=false;
                return;
              }
              removed.forEach(row=>row.remove());
              const retained=new Set(oldRows.filter(row=>ids.has(row.dataset.followUpItem)).map(row=>row.dataset.followUpItem));
              carried.filter(entry=>!retained.has(entry.item_id)).forEach(entry=>{
                entryById[entry.entry_id]={...entry};
                const holder=document.createElement("div");
                holder.innerHTML=itemRow(member,entry.tag,entry,false);
                const row=holder.firstElementChild;
                rows.prepend(row); wire(row);
              });
              previousDay=dayInput.value; previousType=typeInput.value;
              sync();
              message.textContent=`${carried.length} follow-up item${carried.length===1?"":"s"} available for this 1:1.`;
              save.disabled=false;
            }catch(error){
              if(version===prepareVersion&&form.isConnected){
                message.textContent=`Follow-ups could not be loaded: ${error.message}`;
              }
            }
          };
          dayInput.onchange=reload;
          typeInput.onchange=reload;
          form.querySelector(".team-reload-follow-ups").onclick=reload;
        }
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
      <label>Due${dateInput("due","")}</label></div>
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
    const activeFilterTab=view.subtab==="oneonone"?"log":view.subtab==="tasks"&&TEAM.items?"items":view.subtab;
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
    el.querySelectorAll("[data-team-open-only]").forEach(input=>input.onchange=()=>{
      filters[activeFilterTab].openOnly=input.checked;
      render();
    });
    el.querySelectorAll("[data-team-date]").forEach(input=>{
      input.oninput=()=>input.setCustomValidity("");
      input.onchange=()=>{
        const iso=window.PortalDates.parse(input.value);
        if(input.value.trim()&&!iso){
          input.setCustomValidity("Enter a valid date as dd/mm/yyyy.");
          input.reportValidity();
          return;
        }
        filters[activeFilterTab][input.dataset.teamDate]=iso;
      render();
      };
    });
    el.querySelectorAll("[data-team-clear]").forEach(button=>button.onclick=()=>{
      const tab=button.dataset.teamClear;
      Object.keys(filters[tab]).forEach(key=>{
        const value=filters[tab][key];
        filters[tab][key]=Array.isArray(value)?[]:typeof value==="boolean"?false:"";
      });
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
        else if(field.endsWith("_date")){
          const iso=window.PortalDates.parse(value);
          if(value.trim()&&!iso) throw new Error("Enter a valid date as dd/mm/yyyy.");
          data[field]=iso;
        }else data[field]=value;
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
      if(b.dataset.type==="assignment"){
        if(!isPrivate()) return alert("Enable private mode to edit team records.");
        return editAssignment(b.dataset.id, b.dataset.rev);
      }
      if(b.dataset.type==="meeting") return editMeeting(b.dataset.id, b.dataset.rev);
    }));
    el.querySelectorAll(".team-delete-meeting").forEach(b=>
      b.onclick=guarded(()=>deleteMeeting(b.dataset.id,b.dataset.rev)));
    el.querySelectorAll(".team-new-assignment").forEach(b=>b.onclick=guarded(()=>newAssignment(b.dataset.member)));
    el.querySelectorAll(".team-new-meeting").forEach(b=>b.onclick=guarded(()=>newMeeting(b.dataset.member)));
    el.querySelectorAll(".team-promote-raid").forEach(b=>b.onclick=guarded(()=>promoteRoadblock(findEntry(b.dataset.meeting,b.dataset.entry))));
    el.querySelectorAll(".team-item-task").forEach(a=>a.onclick=e=>{
      e.preventDefault();
      view.subtab="tasks";
      if(TEAM.items){
        const item=TEAM.items.find(i=>i.assignment_id===a.dataset.task);
        if(item) view.member=item.member_key;
        filters.items.query=a.dataset.task;
      }else filters.tasks.query=a.dataset.task;
      saveState(); render();
    });
    el.querySelectorAll(".team-item-meeting").forEach(b=>b.onclick=()=>{
      view.member=b.dataset.member; view.subtab="oneonone"; filters.log.query=b.dataset.meeting;
      saveState(); render();
    });
    el.querySelectorAll(".team-edit-item").forEach(b=>b.onclick=guarded(()=>editItem(b.dataset.id)));
    el.querySelectorAll(".team-item-note").forEach(b=>b.onclick=()=>{
      const item=(TEAM.items||[]).find(i=>i.item_id===b.dataset.id);
      formDialog("Add item update",`<label>Update<textarea name="note" required></textarea></label>${visibilitySelect("visibility")}`,
        async data=>mutate("item-note",{id:item.item_id,revision:item.revision,
          note:data.get("note"),visibility:data.get("visibility")}));
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
