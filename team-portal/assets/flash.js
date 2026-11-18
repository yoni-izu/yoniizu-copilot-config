/* Flash FW progress window (/flash?id=<job>).
   Polls /api/flash/status, which folds fw_flash.py's events file and the job
   log. The job itself is a vm-delegate local-runner job, so the same run is
   also in the machine's Jobs list; closing this window does not affect it. */
"use strict";
(function(){
const ID=new URLSearchParams(location.search).get("id")||"";
const root=document.getElementById("flash-root");
const PHASES=[["check","Check"],["preflight","Preflight"],["upload","Upload"],
  ["write","Write"],["verify","Read-back"],["boot","Boot verify"],["done","Done"]];
const STATE_TONE={verified:"ok",written:"run",writing:"run",queued:"run",resetting:"run",
  ready:"",pending:"",unverified:"bad",failed:"bad",cancelled:"warn"};
let last=null, timer=null, follow=true, cancelling=false;

function esc(s){ return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function dur(s){ s=Math.max(0,Math.round(s)); const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
  return (h?h+"h ":"")+(h||m?m+"m ":"")+x+"s"; }
function since(iso){ const t=Date.parse(iso); return isNaN(t)?"":dur((Date.now()-t)/1000); }

function verdict(d){
  if(d.status==="running") return ["run","\u21bb Flashing \u2014 "+(d.phase||"starting")];
  if(d.status==="passed") return ["ok","\u2713 All OAMs flashed and verified"];
  if(d.status==="cancelled") return ["warn","\u25a0 Cancelled before writing"];
  if(d.rc===2) return ["bad","\u2717 Written but NOT verified \u2014 see the OAM table"];
  if(d.status==="died") return ["bad","\u2717 The flash process died without a result"];
  return ["bad","\u2717 Flash failed"+(d.result&&d.result.error?": "+d.result.error:"")];
}
function stepper(d){
  const seen=new Set((d.phases||[]).map(p=>p.phase));
  const skip=new Set(d.reset_verify?[]:["boot"]);
  const cur=d.status==="running"?d.phase:"";
  const failed=d.status!=="running"&&d.status!=="passed";
  return '<ol class="flash-steps">'+PHASES.filter(([k])=>!skip.has(k)).map(([k,l])=>{
    let cls=seen.has(k)?"done":"";
    if(k===cur) cls="cur";
    if(failed&&k===d.phase) cls="bad";
    if(k==="done"&&d.status==="passed") cls="done";
    return '<li class="'+cls+'">'+esc(l)+'</li>';
  }).join("")+'</ol>';
}
function oamRows(d){
  return '<table class="flash-oamtab"><thead><tr><th>OAM</th><th>State</th><th>Progress</th>'
    +'<th>Before</th><th>After</th><th>Detail</th></tr></thead><tbody>'
    +(d.oams||[]).map(o=>{
      const tone=STATE_TONE[o.state]??"";
      const pct=typeof o.pct==="number"?Math.max(0,Math.min(100,o.pct)):null;
      const bar=pct!==null?'<div class="flash-bar" role="progressbar" aria-valuenow="'+pct+'" aria-valuemin="0" aria-valuemax="100">'
          +'<span style="width:'+pct+'%"></span></div><small>'+pct+'%</small>'
        :(tone==="run"?'<span class="spin">\u21bb</span>':"");
      return '<tr><td><b>'+esc(o.oam)+'</b></td><td><span class="vm-st '+esc(tone==="ok"?"passed":tone==="bad"?"failed":tone==="run"?"running":tone==="warn"?"cancelled":"")+'">'
        +esc(o.state)+'</span></td><td>'+bar+'</td><td><code>'+esc(o.before||"")+'</code></td>'
        +'<td><code>'+esc(o.after||o.booted||"")+'</code></td><td>'+esc(o.message||"")+'</td></tr>';
    }).join("")+'</tbody></table>';
}
function render(d){
  const [tone,word]=verdict(d);
  const run=d.status==="running";
  const pre=root.querySelector(".flash-log");
  const keep=pre&&!follow?pre.scrollTop:null;
  document.title=(run?"\u21bb ":tone==="ok"?"\u2713 ":"\u2717 ")+"Flash "+(d.target||"");
  root.innerHTML='<header class="flash-head"><h1>\u26a1 Flash FW \u00b7 '+esc(d.target)+'</h1>'
    +'<div class="flash-verdict '+tone+'" role="status">'+esc(word)+'</div></header>'
    +'<dl class="vm-kv flash-meta"><dt>Image</dt><dd>'+esc(d.image||"")+'</dd>'
    +'<dt>SHA-256</dt><dd><code>'+esc(d.sha256||"")+'</code></dd>'
    +'<dt>Method</dt><dd>'+esc(d.method||"")+(d.reset_verify?" \u00b7 reset &amp; verify boot":"")+'</dd>'
    +'<dt>Started</dt><dd>'+esc((d.started||"").replace("T"," "))+' \u00b7 '+(run?esc(since(d.started))+" elapsed":"finished "+esc((d.finished||"").replace("T"," ")))+'</dd>'
    +'<dt>Job</dt><dd><code>'+esc(d.id)+'</code>'+(d.unit?' \u00b7 <code title="journalctl --user -u '+esc(d.unit)+'">'+esc(d.unit)+'</code>':"")+'</dd></dl>'
    +stepper(d)+oamRows(d)
    +'<div class="flash-act">'
      +'<button type="button" class="flash-term">\u2317 Open full log in terminal</button>'
      +(run?'<button type="button" class="flash-cancel"'+(d.cancellable&&!cancelling?"":" disabled")
        +' title="'+(d.cancellable?"Stop before the write starts":"A write in progress cannot be stopped safely")+'">\u25a0 Cancel</button>':"")
      +'<label class="flash-follow"><input type="checkbox"'+(follow?" checked":"")+'> follow log</label>'
      +'<span class="flash-msg" role="status"></span></div>'
    +'<pre class="flash-log" aria-label="Job log (last 200 lines, including the BMC orchestrator journal)">'+esc(d.log||"")+'</pre>';
  const p=root.querySelector(".flash-log");
  if(follow) p.scrollTop=p.scrollHeight; else if(keep!==null) p.scrollTop=keep;
  root.querySelector(".flash-follow input").addEventListener("change",e=>{ follow=e.target.checked; });
  root.querySelector(".flash-term").addEventListener("click",()=>{
    const w=window.open("/term?"+new URLSearchParams({kind:"log",job:d.id}).toString(),"log-"+d.id,"popup=yes,width=1100,height=700");
    if(!w) alert("Allow pop-ups to open the log terminal.");
  });
  const c=root.querySelector(".flash-cancel");
  if(c) c.addEventListener("click",cancel);
}
async function cancel(){
  if(!confirm("Cancel this flash? Only possible before the write phase starts.")) return;
  cancelling=true;
  const msg=root.querySelector(".flash-msg");
  try{
    const r=await fetch("/api/vm-cancel",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:ID})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||"cancel failed");
    if(msg) msg.textContent="Cancel requested\u2026";
  }catch(e){ if(msg) msg.textContent="Cancel failed: "+e.message; cancelling=false; }
}
async function poll(){
  try{
    const r=await fetch("/api/flash/status?"+new URLSearchParams({id:ID}).toString(),{cache:"no-store"});
    const d=await r.json();
    if(!r.ok) throw new Error(d.error||("HTTP "+r.status));
    const sig=JSON.stringify(d);
    if(sig!==last){ last=sig; render(d); }
    if(d.status!=="running"){ clearInterval(timer); timer=null; }
  }catch(e){
    if(!last) root.innerHTML='<p class="vm-t-err">\u26a0 '+esc(e.message)+'</p>';
  }
}
if(!ID){ root.innerHTML='<p class="vm-t-err">No flash job id.</p>'; return; }
root.innerHTML='<p class="muted">Loading flash job\u2026</p>';
poll();
timer=setInterval(poll,2000);
})();
