/* Overview "Live pulse": pure metrics shared by the page (portal.js, team.js),
 * the node tests and the daily routine (node pulse.js --snapshot < data.json).
 * No DOM, no fetches: every function takes the section payloads and a clock. */
(function(root){
  "use strict";
  const DAY = 864e5;
  const PH_CLOSED = ["closed","not-a-bug","not-ours","merged"];
  const PH_ALARM = ["escaped","regressed","unexplained-recurring"];
  const STALE_PR_DAYS = 7;
  const REVIEW_SLA_DAYS = 3;
  const ACTIVITY_DAYS = 7;
  const ACTIVITY_LIMIT = 100;       // team_activity.activity_payload default
  const STALE_HOURS = 26;
  const CARD_IDS = ["tasks","bugs","prs","reviews","workspaces","vm","raid","team","portal"];

  // ---- shared domain rules (portal.js delegates to these) ----
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
  function bugNeedsAttention(b){
    return !!b.open && (["stalled","dormant"].includes(b.signal)
      || (b.triage||{}).state==="untriaged" || !b.assignee
      || (b.flags||[]).includes("pinged"));
  }
  function phIsOpen(p){ return !PH_CLOSED.includes(p.status); }

  // ---- clock helpers (local calendar, injectable now) ----
  function isoDay(ms){
    const d=new Date(ms);
    return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");
  }
  function dayAge(iso, now){             // whole days since a local YYYY-MM-DD
    if(!iso) return null;
    const d=new Date(String(iso).slice(0,10)+"T00:00:00");
    return isNaN(d) ? null : Math.floor((now-d)/DAY);
  }
  // "2026-11-15 20:19" / "2026-11-15T20:19:05" are local; anything with a zone is absolute.
  function parseWhen(s){
    if(!s) return null;
    const t=String(s).trim().replace(" ","T");
    const ms=Date.parse(t.length<=10 ? t+"T00:00:00" : t);
    return isNaN(ms) ? null : ms;
  }
  const taskOpen = t => !["done","cancelled"].includes(t.status);
  const prs = t => t.reviews&&t.reviews.length ? t.reviews : (t.review?[t.review]:[]);

  // ---- development hygiene: each rule is "bad out of total" ----
  function hygieneRules(d, now){
    const feats=((d.prs||{}).features||[]).filter(f=>!f.complete);
    const live=[].concat(...feats.map(f=>f.prs||[])).filter(p=>!["merged","closed"].includes(p.status));
    const tasks=d.tasks||[];
    const rv=tasks.filter(t=>t.category==="code-review"&&taskOpen(t)&&prs(t).length);
    const rvWaiting=rv.filter(t=>!["approved","merged","waiting-for-author"].includes(consolidateReviewStatus(prs(t))));
    const wip=tasks.filter(t=>t.status==="in-progress");
    const rule=(id,label,bad,total,hint)=>({id,label,bad,total,hint});
    return [
      rule("ci","CI green on open PRs", live.filter(p=>p.status==="ci-fail").length, live.length,
           "red checks block every reviewer behind you"),
      rule("rebase","Rebased on target", live.filter(p=>p.status==="needs-rebase").length, live.length,
           "behind or conflicting with the base branch"),
      rule("landed","Approved PRs landed", live.filter(p=>p.status==="approved").length, live.length,
           "approved but still not merged"),
      rule("moving","PRs moving", live.filter(p=>{const a=dayAge(p.updated,now); return a!=null&&a>=STALE_PR_DAYS;}).length,
           live.length, "no activity for "+STALE_PR_DAYS+"+ days"),
      rule("pldm","Features PLDM-tested", feats.filter(f=>!f.pldm||f.pldm==="not-tested").length, feats.length,
           "never exercised on Palladium"),
      rule("sla","Reviews answered in "+REVIEW_SLA_DAYS+"d",
           rvWaiting.filter(t=>{const a=dayAge(t.updated,now); return a!=null&&a>REVIEW_SLA_DAYS;}).length,
           rvWaiting.length, "waiting on me past the turnaround target"),
      rule("wip","WIP bound to a workspace", wip.filter(t=>!t.workspace).length, wip.length,
           "in-progress with no checkout assigned"),
    ];
  }
  function hygieneScore(rules){
    const live=rules.filter(r=>r.total>0);
    if(!live.length) return null;
    const bad=live.reduce((a,r)=>a+r.bad,0), all=live.reduce((a,r)=>a+r.total,0);
    return {pct:Math.round(100*(1-bad/Math.max(1,all))), bad, all};
  }

  // ---- per-member activity (Overview Team card + Team section cards) ----
  function memberActivity(team, key, now){
    team=team||{};
    const m=(team.members||[]).find(x=>x.member_key===key)||{member_key:key};
    const act=((team.activity||{}).members||{})[key]||{};
    const obs=act.observations||[];
    const since=now-ACTIVITY_DAYS*DAY;
    const recent=obs.filter(o=>{const t=parseWhen(o.observed_at); return t!=null&&t>=since;});
    const kind=k=>recent.filter(o=>o.kind===k).length;
    const today=isoDay(now);
    const asg=(team.assignments||[]).filter(a=>a.member_key===key&&taskOpen(a));
    const items=(team.items||[]).filter(i=>i.member_key===key&&!["done","cancelled","closed","resolved"].includes(i.status));
    const onb=(team.onboarding||{})[key];
    const leave=(m.leave||{}).current_leave;
    const evLast=(((team.evidence||{}).per_member||{})[key]||{}).last_activity_at;
    const times=[obs.length?parseWhen(obs[0].observed_at):null, parseWhen(evLast)].filter(x=>x!=null);
    const gaps=Object.entries(act.sources||{}).filter(([,s])=>!s||!s.last_success_at).map(([src])=>src);
    return {
      key, name:m.display_name||key, lead:m.roster_role==="lead", days:ACTIVITY_DAYS,
      known:act.known!==false && !!act.sources,
      capped:obs.length>=ACTIVITY_LIMIT && recent.length===obs.length,
      prs:kind("github-pr-authored"), reviews:kind("github-pr-review"),
      jira:kind("jira-assigned")+kind("jira-reported"), events:recent.length,
      last:times.length?Math.max(...times):null,
      openTasks:asg.length, overdueTasks:asg.filter(a=>a.target_date&&String(a.target_date).slice(0,10)<today).length,
      openItems:items.length,
      onboarding:onb&&onb.total&&!onb.complete?{done:onb.done,total:onb.total,overdue:onb.overdue||0}:null,
      onLeave:leave?{until:leave.end_date}:null,
      gaps,
    };
  }
  function teamActivity(team, now){
    return ((team||{}).members||[]).map(m=>memberActivity(team, m.member_key, now));
  }
  function escHtml(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
  function agoText(ms, now){
    if(ms==null) return "never";
    const h=(now-ms)/36e5;
    if(h<1) return Math.max(0,Math.round(h*60))+"m ago";
    if(h<48) return Math.round(h)+"h ago";
    return Math.round(h/24)+"d ago";
  }
  // One summary, rendered identically on the Overview and on Team member cards.
  function memberSummaryHtml(a, now){
    const n=v=>a.capped?v+"+":String(v);
    const chip=(label,v,tone,title)=>'<span class="ma-chip'+(tone?" "+tone:"")+'"'+(title?' title="'+escHtml(title)+'"':"")+'>'
      +escHtml(label)+(v==null?"":' <b>'+escHtml(v)+'</b>')+'</span>';
    const quiet=a.known&&!a.events&&!a.onLeave;
    const out=[
      chip("PRs",n(a.prs),"","Pull requests authored in the last "+a.days+" days"),
      chip("Reviews",n(a.reviews),"","Pull-request reviews given in the last "+a.days+" days"),
      chip("Jira",n(a.jira),"","Jira issues assigned or reported in the last "+a.days+" days"),
      chip("Tasks",a.openTasks+(a.overdueTasks?" · "+a.overdueTasks+" late":""),a.overdueTasks?"bad":"",
           "Open team tasks"+(a.overdueTasks?", "+a.overdueTasks+" past their due date":"")),
    ];
    if(a.openItems) out.push(chip("Items",a.openItems,"","Open log items"));
    if(a.onboarding) out.push(chip("Onboarding",a.onboarding.done+"/"+a.onboarding.total+(a.onboarding.overdue?" · "+a.onboarding.overdue+" late":""),
      a.onboarding.overdue?"warn":"","Onboarding checklist progress"));
    if(a.onLeave) out.push(chip("On leave",a.onLeave.until?"until "+a.onLeave.until:"","info"));
    if(quiet) out.push(chip("Quiet "+a.days+"d",null,"warn","No Jira or GitHub activity recorded in the last "+a.days+" days"));
    if(a.gaps.length) out.push(chip("No "+a.gaps.join("/")+" link",null,"muted","This source has never been scanned successfully for this member"));
    if(!a.known) out.push(chip("Activity not scanned",null,"muted"));
    return '<span class="ma">'+out.join("")+'<span class="ma-last" title="Most recent recorded activity">'
      +escHtml(agoText(a.last, now))+'</span></span>';
  }

  // ---- cards ----
  const stat=(k,label,v,o={})=>({k,label,v,better:o.better||"down",tone:o.tone||"",title:o.title||"",text:o.text});
  const worst=tones=>tones.includes("bad")?"bad":tones.includes("warn")?"warn":"ok";

  function cards(d, now){
    d=d||{}; now=now==null?Date.now():now;
    const today=isoDay(now), out=[];
    const rules=hygieneRules(d, now), rule=id=>rules.find(r=>r.id===id);

    // Tasks (reviews have their own card)
    const tasks=(d.tasks||[]).filter(t=>taskOpen(t)&&t.category!=="code-review");
    const overdue=tasks.filter(t=>t.due&&t.due<today&&t.status!=="backlog").length;
    const blocked=tasks.filter(t=>t.status==="blocked").length;
    const soon=tasks.filter(t=>t.due&&t.due>=today&&dayAge(t.due,now)>=-7).length;
    out.push({id:"tasks", label:"Tasks", icon:"▤", section:"work", value:tasks.length, sub:"open, excluding reviews",
      tone:overdue?"bad":blocked?"warn":"ok",
      stats:[stat("progress","In progress",tasks.filter(t=>t.status==="in-progress").length,{better:null}),
             stat("blocked","Blocked",blocked,{tone:blocked?"warn":""}),
             stat("overdue","Overdue",overdue,{tone:overdue?"bad":""}),
             stat("week","Due ≤ 7 days",soon,{better:null}),
             stat("wip","WIP without workspace",rule("wip").bad,{tone:rule("wip").bad?"warn":"",title:rule("wip").hint})]});

    // Bugs + phenomena
    const bugs=((d.bugs||{}).bugs||[]).filter(b=>b.open);
    const att=bugs.filter(bugNeedsAttention).length;
    const untri=bugs.filter(b=>((b.triage||{}).state||"untriaged")==="untriaged").length;
    const ph=((d.triage||{}).phenomena||[]).filter(phIsOpen);
    const alarm=ph.filter(p=>(p.flags||[]).some(f=>PH_ALARM.includes(f))).length;
    out.push({id:"bugs", label:"Bugs", icon:"⚑", section:"bugs", value:bugs.length, sub:"open in the mirror",
      tone:alarm||(bugs.length&&att/bugs.length>0.25)?"bad":att?"warn":"ok",
      stats:[stat("attention","Need attention",att,{tone:att?"warn":"",title:"stalled, dormant, untriaged, unassigned or pinged"}),
             stat("untriaged","Untriaged",untri,{tone:untri?"warn":""}),
             stat("new","New",bugs.filter(b=>b.is_new).length,{better:null}),
             stat("phenomena","Open phenomena",ph.length,{better:null}),
             stat("alarm","Escaped / regressed",alarm,{tone:alarm?"bad":"",title:PH_ALARM.join(", ")})]});

    // My PRs + development hygiene
    const feats=((d.prs||{}).features||[]).filter(f=>!f.complete);
    const score=hygieneScore(rules);
    const prRule=(k,id,label,tone)=>{const r=rule(id); return stat(k,label,r.bad,{tone:r.bad?tone:"",title:r.hint+" ("+r.bad+" of "+r.total+")"});};
    const ci=rule("ci").bad;
    out.push({id:"prs", label:"My PRs", icon:"↗", section:"prs", value:feats.length, sub:"features in flight",
      tone:ci?"bad":["rebase","landed","moving","pldm"].some(id=>rule(id).bad)?"warn":"ok",
      note:score?"Pipeline clean "+score.pct+"% · "+score.bad+" of "+score.all+" hygiene checks failing":"Nothing in flight to measure",
      noteTitle:rules.filter(r=>r.total).map(r=>r.label+": "+(r.total-r.bad)+"/"+r.total).join("\n"),
      stats:[prRule("ci","ci","CI failing","bad"), prRule("rebase","rebase","Needs rebase","warn"),
             prRule("landed","landed","Approved, not landed","warn"), prRule("stale","moving","Idle "+STALE_PR_DAYS+"d+","warn"),
             prRule("pldm","pldm","Not PLDM-tested","warn"),
             stat("score","Hygiene score",score?score.pct:null,{better:"up",text:score?score.pct+"%":"—"})]});

    // Reviews on me
    const rvAll=(d.tasks||[]).filter(t=>t.category==="code-review"&&taskOpen(t)&&prs(t).length);
    const st=t=>consolidateReviewStatus(prs(t));
    const waiting=rvAll.filter(t=>!["approved","merged","waiting-for-author"].includes(st(t))).length;
    const sla=rule("sla").bad;
    out.push({id:"reviews", label:"Reviews on me", icon:"✎", section:"work", value:waiting, sub:"waiting on me",
      tone:sla?"bad":waiting?"warn":"ok",
      stats:[stat("queue","In queue",rvAll.length,{better:null}),
             stat("author","Waiting on author",rvAll.filter(t=>st(t)==="waiting-for-author").length,{better:null}),
             stat("sla","Past "+REVIEW_SLA_DAYS+"d turnaround",sla,{tone:sla?"bad":""})]});

    // Workspaces
    const ws=d.workspaces||{}, slots=ws.slots||{}, roster=ws.roster||[];
    const names=[...new Set([...roster.map(r=>r.slot),...Object.keys(slots)])];
    const bySlot=new Map(roster.map(r=>[r.slot,r]));
    const busy=k=>!!((slots[k]||{}).holder||((bySlot.get(k)||{}).status||{}).mirror);
    const repos=k=>(((bySlot.get(k)||{}).status||{}).repos)||[];
    const dirty=names.filter(k=>repos(k).some(r=>r.dirty)).length;
    const strayFree=names.filter(k=>!busy(k)&&repos(k).some(r=>r.branch&&r.revision&&r.branch!==r.revision)).length;
    const unchecked=names.filter(k=>{const c=parseWhen(((bySlot.get(k)||{}).status||{}).checked); return c==null||now-c>STALE_HOURS*36e5;}).length;
    const nBusy=names.filter(busy).length;
    out.push({id:"workspaces", label:"Workspaces", icon:"⌂", section:"ws", value:nBusy, total:names.length, sub:"slots in use",
      tone:dirty||strayFree?"warn":"ok",
      stats:[stat("free","Free",names.length-nBusy,{better:"up"}),
             stat("dirty","With dirty repos",dirty,{tone:dirty?"warn":""}),
             stat("stray","Free but off baseline",strayFree,{tone:strayFree?"warn":"",title:"Not in use, yet a repo is on a branch other than its baseline"}),
             stat("unchecked","Not checked "+STALE_HOURS+"h+",unchecked,{better:null})]});

    // Machines; the running count still reflects confirmed active jobs.
    const vm=d.vmlog||{}, jobs=vm.jobs||[], nowS=now/1000;
    const day=jobs.filter(j=>j.started_ts&&nowS-j.started_ts<=86400);
    const failed=day.filter(j=>["failed","died"].includes(j.status)).length;
    const unreachable=(vm.unreachable||[]).length;
    out.push({id:"vm", label:"Machines", icon:"⌁", section:"vm", value:(vm.running||[]).length, sub:"running now",
      tone:unreachable||vm.stale_n?"warn":failed?"warn":"ok",
      stats:[stat("passed","Passed 24h",day.filter(j=>j.status==="passed").length,{better:"up"}),
             stat("failed","Failed 24h",failed,{tone:failed?"warn":""}),
             stat("stale","Stale",vm.stale_n||0,{tone:vm.stale_n?"warn":""}),
             stat("unreachable","Machines unreachable",unreachable,{tone:unreachable?"warn":"",title:(vm.unreachable||[]).join(", ")})]});

    // RAID
    const rows=((d.raid||{}).rows||[]).filter(r=>!r.closed);
    const hot=rows.filter(r=>["red","orange"].includes(r.rag)).length;
    const late=rows.filter(r=>r.due&&r.due<today).length;
    out.push({id:"raid", label:"RAID", icon:"◇", section:"raid", value:rows.length, sub:"open rows",
      tone:hot?"bad":late?"warn":"ok",
      stats:[stat("hot","Red / orange",hot,{tone:hot?"bad":""}),
             stat("overdue","Overdue",late,{tone:late?"warn":""}),
             stat("risks","Risks",rows.filter(r=>r.type==="Risk").length,{better:null})]});

    // Team
    const members=teamActivity(d.team, now);
    const active=members.filter(a=>a.events>0).length;
    const quiet=members.filter(a=>a.known&&!a.events&&!a.onLeave).length;
    const late2=members.reduce((s,a)=>s+a.overdueTasks,0);
    const onbLate=members.reduce((s,a)=>s+(a.onboarding?a.onboarding.overdue:0),0);
    out.push({id:"team", label:"Team", icon:"✧", section:"team", value:active, total:members.length,
      sub:"active in the last "+ACTIVITY_DAYS+" days", wide:true,
      tone:late2||onbLate?"warn":quiet?"warn":"ok",
      stats:[stat("prs","PRs "+ACTIVITY_DAYS+"d",members.reduce((s,a)=>s+a.prs,0),{better:"up"}),
             stat("reviews","Reviews "+ACTIVITY_DAYS+"d",members.reduce((s,a)=>s+a.reviews,0),{better:"up"}),
             stat("overdue","Overdue tasks",late2,{tone:late2?"warn":""}),
             stat("onboarding","Onboarding overdue",onbLate,{tone:onbLate?"warn":""}),
             stat("leave","On leave",members.filter(a=>a.onLeave).length,{better:null})],
      members});

    // Portal itself
    const acts=(d.actions||[]);
    const running=acts.filter(a=>["running","queued"].includes(a.status)).length;
    const status=(d.settings||{}).status||{};
    const last=status.last_run||(d.brief&&d.brief.run?{status:d.brief.run.status,generated_at:d.brief.generated_at}:null);
    const timer=status.timer||{};
    out.push({id:"portal", label:"Portal", icon:"✦", section:"settings", value:running, sub:"actions running or queued",
      tone:(last&&last.status==="failed")||status.problem?"bad":(last&&last.status==="degraded")||(timer.active&&timer.active!=="active")?"warn":"ok",
      stats:[stat("last","Last routine",null,{better:null,text:last?(last.status+" · "+agoText(parseWhen(last.generated_at),now)):"never",
               tone:last&&last.status!=="ok"?(last.status==="failed"?"bad":"warn"):""}),
             stat("next","Next routine",null,{better:null,text:timer.next||(timer.active?"not scheduled":"—")})]});
    return out;
  }

  function snapshot(list, now){
    const values={};
    list.forEach(c=>{
      if(typeof c.value==="number") values[c.id]=c.value;
      c.stats.forEach(s=>{ if(typeof s.v==="number") values[c.id+"."+s.k]=s.v; });
    });
    return {taken_at:new Date(now).toISOString(), values};
  }
  // Signed change against a saved snapshot; null when there is nothing to compare.
  function delta(base, key, v){
    if(!base||!base.values||typeof v!=="number"||typeof base.values[key]!=="number") return null;
    return v-base.values[key];
  }

  function freshness(d, now){
    d=d||{};
    const teamAt=Object.values(((d.team||{}).activity||{}).members||{})
      .flatMap(m=>Object.values(m.sources||{}).map(s=>parseWhen(s&&s.last_success_at))).filter(x=>x!=null);
    const roster=((d.workspaces||{}).roster||[]).filter(r=>r.location!=="remote")
      .map(r=>parseWhen((r.status||{}).checked)).filter(x=>x!=null);
    const src=[
      ["bugs","Bugs",parseWhen((d.bugs||{}).last_scan),"bugs"],
      ["prs","PRs",parseWhen((d.prs||{}).last_scan),"prs"],
      ["team","Team activity",teamAt.length?Math.max(...teamAt):null,"team"],
      ["vm","Machines",parseWhen((d.vmlog||{}).last_refresh),"vm"],
      ["ws","Local workspaces",roster.length?Math.min(...roster):null,"ws"],
      ["routine","Daily routine",parseWhen((d.brief||{}).generated_at),"settings"],
    ];
    return src.map(([id,label,at,section])=>({id,label,at,section,
      ago:agoText(at,now), stale:at==null||now-at>STALE_HOURS*36e5}));
  }

  const api={CARD_IDS, PH_CLOSED, PH_ALARM, STALE_PR_DAYS, REVIEW_SLA_DAYS, ACTIVITY_DAYS, STALE_HOURS,
    consolidateReviewStatus, bugNeedsAttention, phIsOpen, hygieneRules, hygieneScore,
    memberActivity, teamActivity, memberSummaryHtml, cards, snapshot, delta, freshness, agoText, parseWhen};
  root.PortalPulse=api;
  if(typeof window!=="undefined" && window!==root) window.PortalPulse=api;
  if(typeof module!=="undefined" && module.exports){
    module.exports=api;
    if(typeof require!=="undefined" && require.main===module && process.argv.includes("--snapshot")){
      let raw=""; process.stdin.setEncoding("utf8");
      process.stdin.on("data",c=>raw+=c);
      process.stdin.on("end",()=>{
        const now=Date.now();
        process.stdout.write(JSON.stringify(snapshot(cards(JSON.parse(raw||"{}"),now),now)));
      });
    }
  }
})(globalThis);
