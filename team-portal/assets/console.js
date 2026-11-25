// Team Portal multi-pane console (/console?c=<json>). Each pane is one
// validated /api/term/open session on its own WebSocket, like term.js. With
// Shared input on, keystrokes typed in a selected pane are sent to every
// selected, connected pane (SAT-style broadcast).
(function(){
  "use strict";
  let cfg={panes:[],layout:"grid",title:""};
  try{ cfg=Object.assign(cfg,JSON.parse(new URLSearchParams(location.search).get("c")||"{}")); }catch(_){}
  const MAX=16, enc=new TextEncoder();
  const bar=document.getElementById("con-bar"), grid=document.getElementById("con-grid"),
        banner=document.getElementById("con-banner");
  const st={shared:false, font:13, layout:["grid","rows","columns"].includes(cfg.layout)?cfg.layout:"grid",
            idleMin:30, uartConfirm:true, maximized:null};
  const panes=[];
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const label=p=>p.kind==="shell"?p.target+(p.session&&p.session!=="portal"?" #"+p.session.replace(/^portal-/,""):"")
    :p.kind==="bmc"?p.target+" BMC":p.target+" "+(p.label||p.console);

  bar.innerHTML='<b class="con-title">'+esc(cfg.title||"Portal console")+'</b>'
    +'<span class="con-count" aria-live="polite"></span>'
    +'<label>Idle close <select data-c="idle" aria-label="Idle close">'
      +[0,15,30,60,120,240].map(m=>'<option value="'+m+'">'+(m?m+" min":"never")+'</option>').join("")+'</select></label>'
    +'<span class="con-grp" role="group" aria-label="Layout">'
      +["grid","rows","columns"].map(l=>'<button type="button" data-layout="'+l+'">'+l[0].toUpperCase()+l.slice(1)+'</button>').join("")+'</span>'
    +'<span class="con-grp" role="group" aria-label="Font size"><button type="button" data-c="font-">A\u2212</button>'
      +'<button type="button" data-c="font+">A+</button></span>'
    +'<span class="con-grp" role="group" aria-label="Select panes">Select '
      +'<button type="button" data-sel="all">All</button><button type="button" data-sel="connected">Connected</button>'
      +'<button type="button" data-sel="none">None</button></span>'
    +'<button type="button" data-c="shared" aria-pressed="false" class="con-shared">Shared input: off</button>'
    +'<button type="button" data-c="logs" title="Download every pane\u2019s scrollback as a zip">\u2913 Save logs</button>'
    +'<button type="button" data-c="reconnect">\u21bb Reconnect dropped</button>'
    +'<button type="button" data-c="disconnect" class="con-danger">Disconnect all</button>';

  function count(){
    const n=panes.filter(p=>p.state==="on").length;
    bar.querySelector(".con-count").textContent=n+"/"+panes.length+" connected";
    bar.querySelectorAll("[data-layout]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.layout===st.layout)));
    const sh=bar.querySelector('[data-c="shared"]');
    sh.textContent="Shared input: "+(st.shared?"on":"off"); sh.setAttribute("aria-pressed",String(st.shared));
    sh.classList.toggle("on",st.shared);
    const sel=panes.filter(p=>p.sel&&p.state==="on");
    banner.hidden=!st.shared;
    banner.textContent="Shared input is ON \u2014 typing in a selected pane goes to "+sel.length
      +" selected pane(s): "+sel.map(p=>p.name).join(", ");
    panes.forEach(p=>p.el.classList.toggle("shared",st.shared&&p.sel));
  }
  function layout(){
    const n=panes.filter(p=>!p.closed).length||1;
    let cols=st.layout==="rows"?1:st.layout==="columns"?n:Math.ceil(Math.sqrt(n));
    if(st.maximized) cols=1;
    grid.style.gridTemplateColumns="repeat("+cols+",minmax(0,1fr))";
    grid.dataset.layout=st.layout;
    requestAnimationFrame(()=>panes.forEach(fitPane));
  }
  function fitPane(p){
    if(p.closed||p.el.hidden) return;
    try{ p.fit.fit(); }catch(_){}
    if(p.ws&&p.ws.readyState===1) p.ws.send(JSON.stringify({t:"r",c:p.term.cols,r:p.term.rows}));
  }
  function setState(p,state,text){
    p.state=state; p.dot.className="con-dot "+state; p.dot.title=text||state;
    p.status.textContent=text||""; count();
  }
  function send(p,d){ if(p.ws&&p.ws.readyState===1){ p.ws.send(JSON.stringify({t:"i",d})); p.last=Date.now(); } }
  function input(p,d){
    if(st.shared&&p.sel) panes.forEach(q=>{ if(q.sel&&!q.closed) send(q,d); });
    else send(p,d);
  }
  async function connect(p){
    if(p.ws){ try{ p.ws.close(); }catch(_){} }
    p.ended=false; setState(p,"wait","Connecting\u2026");
    let data;
    try{
      const r=await fetch("/api/term/open",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify(p.spec)});
      data=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(data.error||("HTTP "+r.status));
    }catch(error){ setState(p,"bad","Could not open: "+error.message); return; }
    p.titleEl.textContent=data.title||p.name;
    const ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws/term?token="+encodeURIComponent(data.token));
    ws.binaryType="arraybuffer"; p.ws=ws;
    ws.onopen=()=>{ p.last=Date.now(); setState(p,"on",""); fitPane(p); };
    ws.onmessage=ev=>{
      if(typeof ev.data==="string"){
        try{ const m=JSON.parse(ev.data); if(m.t==="x"){ p.ended=true; setState(p,m.code?"bad":"off",m.msg||"Session ended"); } }catch(_){}
        return;
      }
      p.last=Date.now(); p.term.write(new Uint8Array(ev.data));
    };
    ws.onclose=()=>{ if(p.ws===ws&&!p.ended) setState(p,"off","Disconnected"); };
  }
  function disconnect(p,text){
    if(p.ws){ p.ended=true; try{ p.ws.close(); }catch(_){} p.ws=null; }
    setState(p,"off",text||"Disconnected");
  }
  function bufferText(p){
    const b=p.term.buffer.active, out=[];
    for(let i=0;i<b.length;i++){ const l=b.getLine(i); if(l) out.push(l.translateToString(true)); }
    while(out.length&&!out[out.length-1]) out.pop();
    return out.join("\n")+"\n";
  }
  function download(name,blob){
    const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),5000);
  }
  const stamp=()=>new Date().toISOString().replace(/[:T]/g,"-").slice(0,19);
  const fname=p=>p.name.replace(/[^A-Za-z0-9_.-]+/g,"_")+".log";
  // Store-only zip (no compression) so logs download as one file without a library.
  const CRC=(()=>{ const t=new Uint32Array(256); for(let n=0;n<256;n++){ let c=n; for(let k=0;k<8;k++) c=c&1?0xedb88320^(c>>>1):c>>>1; t[n]=c>>>0; } return t; })();
  function crc32(u8){ let c=0xffffffff; for(let i=0;i<u8.length;i++) c=CRC[(c^u8[i])&255]^(c>>>8); return (c^0xffffffff)>>>0; }
  function zip(files){
    const parts=[], central=[]; let off=0;
    const used=new Set();
    files.forEach(({name,data})=>{
      let n=name, i=2; while(used.has(n)) n=name.replace(/\.log$/,"_"+(i++)+".log"); used.add(n);
      const nm=enc.encode(n), crc=crc32(data), h=new DataView(new ArrayBuffer(30));
      [[0,0x04034b50,4],[4,20,2],[8,0,2],[14,crc,4],[18,data.length,4],[22,data.length,4],[26,nm.length,2]]
        .forEach(([o,v,s])=>s===4?h.setUint32(o,v,true):h.setUint16(o,v,true));
      const c=new DataView(new ArrayBuffer(46));
      [[0,0x02014b50,4],[4,20,2],[6,20,2],[16,crc,4],[20,data.length,4],[24,data.length,4],[28,nm.length,2],[42,off,4]]
        .forEach(([o,v,s])=>s===4?c.setUint32(o,v,true):c.setUint16(o,v,true));
      parts.push(h,nm,data); central.push(c,nm); off+=30+nm.length+data.length;
    });
    const size=central.reduce((a,x)=>a+(x.byteLength),0), e=new DataView(new ArrayBuffer(22));
    [[0,0x06054b50,4],[8,files.length,2],[10,files.length,2],[12,size,4],[16,off,4]]
      .forEach(([o,v,s])=>s===4?e.setUint32(o,v,true):e.setUint16(o,v,true));
    return new Blob([...parts,...central,e],{type:"application/zip"});
  }
  function confirmUart(sel){
    if(!st.uartConfirm||!sel.some(p=>p.spec.kind==="uart")) return true;
    return confirm("Shared input will type into UART console(s): "
      +sel.filter(p=>p.spec.kind==="uart").map(p=>p.name).join(", ")+".\nKeystrokes reach the device. Continue?");
  }
  function addPane(spec){
    const p={spec, name:label(spec), sel:spec.kind!=="uart", state:"off", last:Date.now(), closed:false};
    p.el=document.createElement("section"); p.el.className="con-pane"; p.el.setAttribute("aria-label",p.name);
    p.el.innerHTML='<div class="con-head"><input type="checkbox" class="con-sel" aria-label="Include '+esc(p.name)+' in shared input"'
      +(p.sel?" checked":"")+'><span class="con-dot"></span><span class="con-ptitle"></span>'
      +'<span class="con-badge">SHARED</span><span class="con-pstatus"></span>'
      +'<span class="con-tools"><button type="button" data-p="max" title="Maximize">\u2922</button>'
      +'<button type="button" data-p="clear" title="Clear">\u232b</button>'
      +'<button type="button" data-p="log" title="Download this pane\u2019s log">\u2913</button>'
      +'<button type="button" data-p="re" title="Reconnect">\u21bb</button>'
      +'<button type="button" data-p="x" title="Close pane">\u00d7</button></span></div><div class="con-term"></div>';
    grid.appendChild(p.el);
    p.dot=p.el.querySelector(".con-dot"); p.titleEl=p.el.querySelector(".con-ptitle");
    p.status=p.el.querySelector(".con-pstatus"); p.titleEl.textContent=p.name;
    p.term=new Terminal({cursorBlink:true, scrollback:20000,
      fontFamily:'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace', fontSize:st.font, theme:{background:"#0f1419"}});
    p.fit=new FitAddon.FitAddon(); p.term.loadAddon(p.fit); p.term.open(p.el.querySelector(".con-term"));
    p.term.onData(d=>input(p,d));
    p.el.querySelector(".con-sel").addEventListener("change",e=>{
      if(e.target.checked&&st.shared&&!confirmUart([p])){ e.target.checked=false; return; }
      p.sel=e.target.checked; count(); });
    p.el.querySelector(".con-tools").addEventListener("click",e=>{
      const a=e.target.closest("button"); if(!a) return;
      if(a.dataset.p==="max"){
        st.maximized=st.maximized===p?null:p;
        panes.forEach(q=>{ q.el.hidden=q.closed||(st.maximized&&q!==st.maximized); });
        layout(); p.term.focus();
      }else if(a.dataset.p==="clear") p.term.clear();
      else if(a.dataset.p==="log") download(stamp()+"_"+fname(p),new Blob([bufferText(p)],{type:"text/plain"}));
      else if(a.dataset.p==="re"){ p.term.reset(); connect(p); }
      else if(a.dataset.p==="x"){
        disconnect(p,"Closed"); p.closed=true; p.el.hidden=true;
        if(st.maximized===p){ st.maximized=null; panes.forEach(q=>q.el.hidden=q.closed); }
        layout();
      }
    });
    panes.push(p);
    return p;
  }

  bar.addEventListener("click",e=>{
    const b=e.target.closest("button"); if(!b) return;
    if(b.dataset.layout){ st.layout=b.dataset.layout; count(); layout(); return; }
    if(b.dataset.sel){
      const want=b.dataset.sel, next=panes.filter(p=>!p.closed&&(want==="all"||(want==="connected"&&p.state==="on")));
      if(want!=="none"&&st.shared&&!confirmUart(next.filter(p=>!p.sel))) return;
      panes.forEach(p=>{ p.sel=next.includes(p); p.el.querySelector(".con-sel").checked=p.sel; });
      count(); return;
    }
    const c=b.dataset.c;
    if(c==="font-"||c==="font+"){
      st.font=Math.max(8,Math.min(24,st.font+(c==="font+"?1:-1)));
      panes.forEach(p=>{ p.term.options.fontSize=st.font; }); layout();
    }else if(c==="shared"){
      if(!st.shared&&!confirmUart(panes.filter(p=>p.sel&&!p.closed))) return;
      st.shared=!st.shared; count();
    }else if(c==="logs"){
      const files=panes.filter(p=>!p.closed).map(p=>({name:fname(p),data:enc.encode(bufferText(p))}));
      if(files.length) download("console_"+stamp()+".zip",zip(files));
    }else if(c==="reconnect") panes.forEach(p=>{ if(!p.closed&&p.state!=="on"&&p.state!=="wait"){ p.term.reset(); connect(p); } });
    else if(c==="disconnect"){ if(confirm("Disconnect every pane?")) panes.forEach(p=>{ if(!p.closed) disconnect(p); }); }
  });
  bar.querySelector('[data-c="idle"]').addEventListener("change",e=>{ st.idleMin=parseInt(e.target.value,10)||0; });
  setInterval(()=>{
    if(!st.idleMin) return;
    const cut=Date.now()-st.idleMin*60000;
    panes.forEach(p=>{ if(p.state==="on"&&p.last<cut) disconnect(p,"Idle \u2014 closed after "+st.idleMin+" min"); });
  },30000);
  window.addEventListener("resize",()=>{ clearTimeout(window.__rt); window.__rt=setTimeout(()=>panes.forEach(fitPane),80); });
  window.addEventListener("beforeunload",e=>{ if(panes.some(p=>p.state==="on")){ e.preventDefault(); e.returnValue=""; } });

  async function start(){
    try{
      const r=await fetch("/api/settings",{cache:"no-store"});
      const t=((await r.json()).settings||{}).terminals||{};
      if(Number.isInteger(t.idle_close_min)) st.idleMin=t.idle_close_min;
      if(typeof t.uart_shared_confirm==="boolean") st.uartConfirm=t.uart_shared_confirm;
    }catch(_){}
    const idle=bar.querySelector('[data-c="idle"]');
    if(![...idle.options].some(o=>+o.value===st.idleMin)) idle.add(new Option(st.idleMin+" min",st.idleMin));
    idle.value=String(st.idleMin);
    const specs=(Array.isArray(cfg.panes)?cfg.panes:[]).slice(0,MAX);
    if(!specs.length){ grid.innerHTML='<p class="con-empty">No panes. Open the console from Machines \u203a \u229e Console.</p>'; count(); return; }
    document.title=(cfg.title||specs.map(label).join(", "))+" \u00b7 Portal console";
    specs.forEach(addPane); count(); layout();
    for(const p of panes){ connect(p); await new Promise(r=>setTimeout(r,150)); }
    panes[0].term.focus();
  }
  start();
})();
