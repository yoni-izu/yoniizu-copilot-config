// Team Portal browser terminal (pop-up window). The page URL names WHAT to
// open (?kind=shell&target=vm or ?kind=log&job=<id>); the server validates it
// and hands back a one-time token for the WebSocket.
(function(){
  "use strict";
  const params=new URLSearchParams(location.search);
  const spec={kind:params.get("kind")||"shell", target:params.get("target")||"", job:params.get("job")||""};
  if(spec.kind==="workspace"){
    spec.workspace=params.get("workspace")||"";
    spec.read_only_confirmed=params.get("read_only_confirmed")==="true";
  }
  const statusEl=document.getElementById("term-status");
  const titleEl=document.getElementById("term-title");
  const againBtn=document.getElementById("term-again");
  const term=new Terminal({cursorBlink:true, scrollback:20000, convertEol:false,
    fontFamily:'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace', fontSize:13,
    theme:{background:"#0f1419"}});
  const fit=new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(document.getElementById("term"));
  let ws=null, ended=false;

  function setStatus(text, tone){ statusEl.textContent=text; statusEl.className="term-status "+(tone||""); }
  function sendSize(){
    try{ fit.fit(); }catch(_){}
    if(ws&&ws.readyState===1) ws.send(JSON.stringify({t:"r",c:term.cols,r:term.rows}));
  }

  async function connect(){
    ended=false; againBtn.hidden=true;
    setStatus("Connecting…","");
    let data;
    try{
      const r=await fetch("/api/term/open",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify(spec)});
      data=await r.json();
      if(!r.ok) throw new Error(data.error||("HTTP "+r.status));
    }catch(error){
      setStatus("Could not open: "+error.message,"bad"); againBtn.hidden=false; return;
    }
    document.title=data.title+" · Portal terminal";
    titleEl.textContent=data.title;
    ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws/term?token="+encodeURIComponent(data.token));
    ws.binaryType="arraybuffer";
    ws.onopen=()=>{ setStatus(spec.kind==="log"?"Tailing the log on the machine (Ctrl-C stops a live tail)":"Connected","ok"); sendSize(); term.focus(); };
    ws.onmessage=(ev)=>{
      if(typeof ev.data==="string"){
        try{
          const m=JSON.parse(ev.data);
          if(m.t==="x"){ ended=true; setStatus(m.msg||"Session ended",m.code?"bad":""); againBtn.hidden=false; }
        }catch(_){}
        return;
      }
      term.write(new Uint8Array(ev.data));
    };
    ws.onclose=()=>{ if(!ended){ setStatus("Disconnected","bad"); } againBtn.hidden=false; };
  }

  term.onData(d=>{ if(ws&&ws.readyState===1) ws.send(JSON.stringify({t:"i",d})); });
  window.addEventListener("resize",()=>{ clearTimeout(window.__rt); window.__rt=setTimeout(sendSize,80); });
  againBtn.addEventListener("click",()=>{ term.reset(); connect(); });
  connect();
})();
