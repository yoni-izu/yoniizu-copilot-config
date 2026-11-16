/* AI chat panels shared by the portal drawer and the detached window
   (/ai-chat?chat=<rc-id> or /ai-chat?run=<ai-id>[&private=1]).

   * Review chat: the interactive /pr-review local conversation the server
     holds in a review workspace. Each message is one queued turn; Submit asks
     for a preview first and posts only after the user confirms that exact text.
   * Ask thread: a detached copy of the Ask drawer conversation (follow-ups
     continue the same Copilot session).

   Answers arrive as HTML rendered server-side (markdown-it, raw HTML off).
   State lives on the server, so the drawer and a popped-out window can show
   the same chat; each just polls it. */
(function(){
  "use strict";
  const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const when=s=>{ if(!s) return ""; const d=new Date(s); return isNaN(d)?String(s):d.toLocaleString(); };
  const POLL_BUSY_MS=2500, POLL_IDLE_MS=15000;

  async function api(url,opts,priv){
    const headers={"Content-Type":"application/json"};
    if(priv) headers["X-Team-Private"]="1";
    const r=await fetch(url,{cache:"no-store",...(opts||{}),headers});
    let j={}; try{ j=await r.json(); }catch(e){}
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    return j;
  }
  function popOut(query,name){
    const w=window.open("/ai-chat?"+query,name,"width=780,height=920");
    if(w) w.focus();
    return !!w;
  }
  function turnHtml(r,qLabel){
    const meta=[r.model, r.premium?`${r.premium} premium`:"", r.finished?when(r.finished):""].filter(Boolean).join(" · ");
    const body=r.status==="done"?`<div class="ai-answer">${r.answer_html||esc(r.answer||"")}</div>`
      :r.status==="error"?`<div class="ai-err">${esc(r.error||"failed")}</div>`
      :`<div class="ai-wait"><span class="aq-spin" aria-hidden="true"></span> ${esc(r.progress||"working…")}</div>`;
    return `<div class="ai-turn ai-role-${esc(r.role||"turn")}"><div class="ai-q">${esc(qLabel(r))}</div>${body}${meta?`<small class="ai-meta">${esc(meta)}</small>`:""}</div>`;
  }

  // ------------------------------------------------------------ review chat
  function mountReview(root,chatId,opts){
    opts=opts||{};
    const st={chat:null,timer:null,sending:false,dead:false,lastSig:""};
    root.classList.add("rc-panel");
    root.innerHTML=`<div class="ai-head"><b>✦ Interactive review <span class="rc-task"></span></b>
        <small class="rc-usage"></small>
        ${opts.detached?"":`<button type="button" class="ab-close rc-pop" title="Pop out into a window" aria-label="Pop out into a window">⧉</button>`}
        ${opts.onClose?`<button type="button" class="ab-close rc-close" aria-label="Close panel">&times;</button>`:""}</div>
      <div class="rc-bar"><span class="rc-state"></span>
        <label class="rc-free"><input type="checkbox" checked> release workspace</label>
        <button type="button" class="btn primary rc-retry" hidden title="Start the review again in the same workspace (counts as one interactive message)">Retry start</button>
        <button type="button" class="btn rc-submit" title="Preview the final review; nothing is posted until you confirm the preview">Submit…</button>
        <button type="button" class="btn rc-end">End review</button></div>
      <div class="ai-thread rc-thread" aria-live="polite"></div>
      <div class="rc-confirm" hidden></div>
      <form class="ai-ask rc-ask"><textarea rows="3" placeholder="Reply: accept / drop / reword a finding, ask about the code… (Ctrl+Enter to send)"></textarea>
        <button type="submit" class="btn primary">Send</button></form>
      <p class="ai-foot">Runs <code>/pr-review local</code> in the review workspace. Decisions are written to the draft as you go; nothing reaches GitHub unless you confirm a Submit preview.</p>`;
    const $=s=>root.querySelector(s);
    const ta=$(".rc-ask textarea");
    if(opts.onClose) $(".rc-close").addEventListener("click",()=>{ stop(); opts.onClose(); });
    if(!opts.detached) $(".rc-pop").addEventListener("click",()=>{
      if(popOut("chat="+encodeURIComponent(chatId),"aichat-"+chatId)&&opts.onPopped){ stop(); opts.onPopped(); }
    });
    $(".rc-ask").addEventListener("submit",e=>{ e.preventDefault(); send("turn",{message:ta.value}); });
    ta.addEventListener("keydown",e=>{ if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); send("turn",{message:ta.value}); } });
    $(".rc-submit").addEventListener("click",()=>send("preview",{}));
    $(".rc-retry").addEventListener("click",()=>send("retry",{}));
    $(".rc-end").addEventListener("click",()=>{
      const free=$(".rc-free input").checked;
      const c=st.chat||{};
      if(!confirm(`End the interactive review of ${c.task_id||""}?\n\n`+
        (free?`The workspace ${c.workspace||""} will be released (if the review occupied it). `
             :`The workspace ${c.workspace||""} stays occupied. `)+"The review draft is kept.")) return;
      send("end",{free});
    });

    async function send(op,body){
      if(st.sending) return;
      if(op==="turn"&&!(body.message||"").trim()){ ta.focus(); return; }
      st.sending=true; render();
      try{
        await api(`/api/ai/review-chat/${encodeURIComponent(chatId)}/${op}`,{method:"POST",body:JSON.stringify(body)});
        if(op==="turn") ta.value="";
        if(op==="end"&&opts.onEnded) opts.onEnded(chatId);
      }catch(e){ alert(String(e.message||e)); }
      st.sending=false; await refresh();
    }
    function label(r){
      if(r.role==="open") return "Start review";
      if(r.role==="preview") return "Submit → preview";
      if(r.role==="submit") return "Submit → post confirmed review";
      return r.question||"";
    }
    function render(){
      const c=st.chat; if(!c) return;
      $(".rc-task").textContent=`${c.task_id} · ${c.workspace||""}`;
      const u=c.usage||{};
      $(".rc-usage").textContent=u.interactive_cap!=null?`${u.interactive_used||0}/${u.interactive_cap} messages today · ${u.model||""}`:"";
      const ended=c.status==="ended", interrupted=c.status==="interrupted"&&!c.busy;
      $(".rc-state").textContent=ended?`Ended ${when(c.ended)} — ${c.end_reason||""}`
        :c.busy?(c.progress||"working…")
        :interrupted?`${c.interrupted||"Start interrupted"} — ${c.workspace||"the workspace"} is kept. Retry start or end the review.`
        :c.status==="starting"?"starting…":"Your turn";
      $(".rc-state").className="rc-state "+(ended?"muted":c.busy?"busy":interrupted?"warn":"ok");
      $(".rc-retry").hidden=!interrupted; $(".rc-retry").disabled=st.sending;
      const turns=(c.turns||[]).map((r,i)=>(i===c.turns.length-1&&c.busy&&r.status!=="done"&&r.status!=="error")?{...r,progress:c.progress}:r);
      const sig=JSON.stringify(turns.map(r=>[r.id,r.status,r.progress]))+c.status+c.busy;
      const th=$(".rc-thread");
      if(sig!==st.lastSig){
        st.lastSig=sig;
        const nearBottom=th.scrollHeight-th.scrollTop-th.clientHeight<80;
        th.innerHTML=turns.length?turns.map(r=>turnHtml(r,label)).join(""):`<p class="muted">Starting…</p>`;
        if(nearBottom||c.busy) th.scrollTop=th.scrollHeight;
      }
      const ready=!ended&&c.status==="open"&&!c.busy&&!st.sending;
      ta.disabled=!ready; $(".rc-ask button").disabled=!ready;
      $(".rc-submit").disabled=!ready; $(".rc-end").disabled=ended||c.busy||st.sending;
      $(".rc-free").hidden=ended;
      // The confirm box appears only for the latest turn being a finished preview.
      const last=(c.turns||[])[c.turns.length-1];
      const box=$(".rc-confirm");
      const showConfirm=ready&&last&&last.role==="preview"&&last.status==="done"&&c.preview_run===last.id;
      box.hidden=!showConfirm;
      if(showConfirm&&box.dataset.run!==last.id){
        box.dataset.run=last.id;
        box.innerHTML=`<b>Post exactly the preview above to GitHub?</b>
          <span>This publishes the review(s) and verdicts on the PRs. Keep chatting instead to change anything first.</span>
          <div><button type="button" class="btn primary rc-post">Confirm &amp; post</button>
          <button type="button" class="btn rc-cancel">Not yet</button></div>`;
        box.querySelector(".rc-post").addEventListener("click",()=>{
          if(!confirm("Final check: post this review to GitHub now?")) return;
          send("submit",{preview_run:last.id});
        });
        box.querySelector(".rc-cancel").addEventListener("click",()=>{ box.hidden=true; box.dataset.dismissed=last.id; });
      }
      if(box.dataset.dismissed===(last&&last.id)) box.hidden=true;
    }
    async function refresh(){
      if(st.dead) return;
      clearTimeout(st.timer);
      try{
        st.chat=await api("/api/ai/review-chat/"+encodeURIComponent(chatId));
        render();
      }catch(e){
        $(".rc-thread").innerHTML=`<div class="ai-err">${esc(e.message||e)}</div>`;
      }
      const busy=st.chat&&(st.chat.busy||st.chat.status==="starting");
      if(!(st.chat&&st.chat.status==="ended")) st.timer=setTimeout(refresh,busy?POLL_BUSY_MS:POLL_IDLE_MS);
    }
    function stop(){ st.dead=true; clearTimeout(st.timer); }
    refresh();
    return {stop,refresh};
  }

  // ------------------------------------------------------------ ask thread
  function mountThread(root,runId,opts){
    opts=opts||{};
    const priv=!!opts.private;
    const st={thread:[],busy:false};
    root.classList.add("rc-panel");
    root.innerHTML=`<div class="ai-head"><b>✦ AI · <span class="at-sec"></span></b><small class="at-usage"></small></div>
      <div class="ai-thread at-thread" aria-live="polite"></div>
      <form class="ai-ask at-ask"><textarea rows="3" placeholder="Follow-up question… (Ctrl+Enter to send)"></textarea>
        <button type="submit" class="btn primary">Ask</button></form>
      <p class="ai-foot">Read-only: the assistant answers from the snapshot it was given and cannot change anything.</p>`;
    const $=s=>root.querySelector(s);
    const ta=$(".at-ask textarea");
    $(".at-ask").addEventListener("submit",e=>{ e.preventDefault(); ask(); });
    ta.addEventListener("keydown",e=>{ if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); ask(); } });
    const label=r=>r.kind==="explain"?"Explain this view":r.kind==="brief"?"AI brief":(r.question||"");
    function render(){
      const th=$(".at-thread");
      th.innerHTML=st.thread.map(r=>turnHtml(r,label)).join("")||`<p class="muted">Loading…</p>`;
      th.scrollTop=th.scrollHeight;
      const last=st.thread[st.thread.length-1];
      $(".at-sec").textContent=(last&&last.section)||"";
      ta.disabled=$(".at-ask button").disabled=st.busy||!(last&&last.status==="done"&&last.session_id);
    }
    async function load(){
      const chain=[]; let id=runId;
      for(let i=0;id&&i<50;i++){
        const r=await api("/api/ai/run/"+encodeURIComponent(id),null,priv);
        chain.unshift(r); id=r.parent;
      }
      st.thread=chain; render();
      const last=chain[chain.length-1];
      if(last&&!["done","error"].includes(last.status)) await wait(last);
    }
    async function wait(turn){
      for(;;){
        await new Promise(res=>setTimeout(res,POLL_BUSY_MS));
        const r=await api("/api/ai/run/"+encodeURIComponent(turn.id),null,priv).catch(()=>null);
        if(r){ Object.assign(turn,r); render(); if(["done","error"].includes(r.status)) return; }
      }
    }
    async function ask(){
      const last=st.thread[st.thread.length-1];
      const q=ta.value.trim(); if(!q||st.busy||!last) return;
      st.busy=true;
      const turn={id:null,kind:"ask",question:q,status:"queued",section:last.section};
      st.thread.push(turn); render();
      try{
        const j=await api("/api/ai/run",{method:"POST",body:JSON.stringify({kind:"ask",section:last.section,question:q,parent:last.id})},priv);
        ta.value=""; turn.id=j.run_id; await wait(turn);
      }catch(e){ Object.assign(turn,{status:"error",error:String(e.message||e)}); }
      st.busy=false; render();
    }
    load().catch(e=>{ $(".at-thread").innerHTML=`<div class="ai-err">${esc(e.message||e)}</div>`; });
  }

  function mountFromQuery(root){
    const q=new URLSearchParams(location.search);
    const chat=q.get("chat"), run=q.get("run");
    if(chat&&/^rc-[0-9a-f]{32}$/.test(chat)){ document.title="Interactive review"; return mountReview(root,chat,{detached:true}); }
    if(run&&/^ai-[0-9a-f]{32}$/.test(run)){ document.title="Portal AI"; return mountThread(root,run,{private:q.get("private")==="1"}); }
    root.innerHTML=`<p class="ai-err">Nothing to show: open this window from the portal.</p>`;
  }

  window.PortalAIChat={mountReview,mountThread,mountFromQuery,popOut};
})();
