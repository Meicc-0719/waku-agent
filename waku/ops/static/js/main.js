// waku dashboard — render/refresh loop, resizers/chrome, voice, bootstrap (LOADS LAST).
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

let activeView = null, activeSub = null;
// The hash keys stay as they are — #settings is linked from graph.js, views.js,
// the README and DEMO-CHECKLIST, and from anyone's bookmark. Only the LABEL
// moved: after the Connections registry took keys, providers and integrations
// out of that page, what remained was two switches that change how a turn runs,
// which is a behaviour, not a setting.
const TITLES = {chat:"对话与监看", ops:"LLM 运维",
                graph:"图工作流——为循环赋予结构",
                // Keyed by view AND sub for the Arena, now that the sidebar
                // names the two races separately. A single title covering both
                // was right while they hid behind sub-tabs; with two nav rows
                // it reads as a page that does not know which one you clicked.
                compare:"竞技场——在同一循环中竞速模型与记忆",
                "compare/models":"模型竞速——十个大脑，一个框架",
                "compare/memory":"记忆竞速——一个大脑，五种事实存放位置",
                settings:"行为——一轮任务如何运行",
                database:"数据库——Waku 存储的全部内容（state.db）"};
function render(){
  if (!D) return;
  const [v, subRaw] = (location.hash||"#overview").slice(1).split("/");
  const sub = subRaw || null;
  const view = VIEWS[v] ? v : "overview";
  const subChanged = sub !== activeSub || view !== activeView;
  // Two nav rows can share a view, so a row that names a sub only lights up
  // for that sub. Without the fallback, landing on bare #compare would light
  // NEITHER race and the sidebar would show no current page at all.
  const effSub = sub || (view === "compare" ? "models" : null);
  document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("on",
    a.dataset.v === view && (!a.dataset.sub || a.dataset.sub === effSub)));
  document.getElementById("title").textContent =
    TITLES[`${view}/${effSub}`] || TITLES[view] || view[0].toUpperCase()+view.slice(1);
  if (view === "overview" || view === "graph"){
    // don't rebuild mid-animation or the glowing SVG gets wiped
    if (activeView !== view || !animating){ document.getElementById("view").innerHTML = VIEWS[view](D); }
  } else if ((view === "memory" || view === "settings" || view === "database" || view === "compare" || view === "models" || view === "connections") && editing && !subChanged){
    // don't wipe an in-progress edit on the 5s refresh — but DO switch sub-tabs
  } else {
    editing = false;
    // Rebuilding #view innerHTML resets the scroll. On a same-view refresh (the
    // 5s poll, a sort click) keep the reader where they were — only jump to top
    // on an actual navigation (subChanged), where top is correct.
    const main = document.querySelector("main");
    const keepScroll = !subChanged && main;
    const y = keepScroll ? main.scrollTop : 0;
    document.getElementById("view").innerHTML = VIEWS[view](D, sub);
    if (keepScroll) main.scrollTop = y;
  }
  activeView = view; activeSub = sub;
  document.getElementById("model").textContent = `${D.provider} · ${D.model}`;
  document.getElementById("n-gw").textContent = (D.chat_log||[]).length;
  document.getElementById("n-loop").textContent = D.stats.turns;
  document.getElementById("n-graph").textContent =
    (D.graph && (D.graph.stats.quick + D.graph.stats.full)) || "";
  document.getElementById("n-mem").textContent = D.facts.length + D.episodes.length;
  document.getElementById("n-tools").textContent = D.calendar.length + D.outbox.length;
  document.getElementById("n-db").textContent = (D.db && D.db.all_tables.length) || "";
  document.getElementById("n-ops").textContent = D.stats.tool_errors || (D.eval_report ? "" : "!");
}
let lastFetch = Date.now();
let lastCompareLoad = 0;   // throttle the Compare scoreboard self-heal to ~5s
function tickLive(){
  if (!D) return;
  const ago = Math.round((Date.now()-lastFetch)/1000);
  document.getElementById("sub").innerHTML =
    `<span class="live"><span class="dot"></span>实时</span> · ${ago} 秒前更新 · ${esc(D.home)}`;
}
let dockRestored = false;
async function restoreDock(){
  // On page load the dock is empty even though the current thread has messages
  // — restore them so a refresh never looks like it lost the chat.
  dockRestored = true;
  const sid = D && D.current_session;
  if (!sid || CHAT.length) return;
  await loadThreadInto(sid, {setSession: true});
}
async function refresh(){
  try {
    D = await (await fetch("/api/data")).json(); lastFetch = Date.now();
    render(); tickLive();
    syncModelChip();  // keep the dock's model pill in sync with the active brain
    applyTele();      // reflect the stats on/off choice (default on)
    syncLiveView();   // live-update an opened conversation (e.g. new phone messages)
    if (!dockRestored) restoreDock();
    // Self-heal the Compare scoreboard: it otherwise only loads on tab-open and
    // after a race, so a slow/interrupted race (or a server blip) can leave it
    // showing a partial set. Re-pull the server totals while viewing the tab —
    // but never mid-race (that's the live fold's job) or mid-edit, and at most
    // every ~5s so we don't hammer the endpoint on the faster render ticks.
    if (activeView === "compare" && !compareState.running && !editing
        && Date.now() - lastCompareLoad > 5000){
      lastCompareLoad = Date.now();
      loadCompareHistory();
    }
  } catch(e){ /* server restarting — keep showing last data */ }
}
// --- resizable columns: drag the thin handle between nav|main and main|dock.
// Width lives in a CSS var + localStorage, so it survives refreshes.
function wireResizer(id, cssVar, key, fromRight, min, max){
  const el = document.getElementById(id);
  if (!el) return;
  el.onmousedown = e => {
    e.preventDefault();
    document.body.classList.add("resizing");
    const move = ev => {
      let w = fromRight ? (window.innerWidth - ev.clientX) : ev.clientX;
      w = Math.max(min, Math.min(max, w));
      document.documentElement.style.setProperty(cssVar, w + "px");
      localStorage.setItem(key, w);
    };
    const up = () => { document.body.classList.remove("resizing");
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };
}
function wireChrome(){
  // restore saved widths
  const nw = localStorage.getItem("navW"); if (nw) document.documentElement.style.setProperty("--nav-w", nw+"px");
  const dw = localStorage.getItem("dockW"); if (dw) document.documentElement.style.setProperty("--dock-w", dw+"px");
  wireResizer("nav-resizer", "--nav-w", "navW", false, 150, 380);
  wireResizer("dock-resizer", "--dock-w", "dockW", true, 260, 680);
  // hide / show the sidebar
  const setNav = v => { document.body.classList.toggle("nav-hidden", v); localStorage.setItem("navHidden", v?"1":"0"); };
  const nt = document.getElementById("nav-toggle"), nr = document.getElementById("nav-reopen");
  if (nt) nt.onclick = () => setNav(true);
  if (nr) nr.onclick = () => setNav(false);
  setNav(localStorage.getItem("navHidden") === "1");
}

// --- voice on the dashboard: record in the browser, transcribe on the server
// with the SAME local Whisper `make voice` uses. Text lands in the input for
// you to review, then Send — nothing leaves the machine.
// Voice capture records WAV (uncompressed PCM) via the Web Audio API — NOT
// MediaRecorder's WebM/Opus, which faster-whisper/PyAV often can't decode
// ("transcription failed [Errno …]"). WAV is trivially decodable server-side.
let micCtx = null, micStream = null, micNode = null, micBuf = [], micOn = false;
const micHint = (msg) => { const i = document.getElementById("dmsg");
  if (i){ i.placeholder = msg; setTimeout(()=>{ i.placeholder = "向 Waku 发送消息…"; }, 8000); } };

async function toggleMic(){
  const btn = document.getElementById("mic");
  if (micOn){ await stopMic(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    micHint("语音功能需要在 localhost:7777 的普通浏览器标签页中使用，不能使用 IDE 预览窗格");
    return;
  }
  try {
    micStream = await navigator.mediaDevices.getUserMedia({audio:true});
    micCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = micCtx.createMediaStreamSource(micStream);
    micNode = micCtx.createScriptProcessor(4096, 1, 1);
    micBuf = [];
    micNode.onaudioprocess = e => micBuf.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(micNode); micNode.connect(micCtx.destination);
    micOn = true; btn.classList.add("rec");
  } catch(e){
    console.warn("mic error:", e);
    micHint(e && e.name === "NotAllowedError"
      ? "麦克风被阻止——点击地址栏中的锁图标，允许使用麦克风后重新加载（macOS：还需在“系统设置 ▸ 隐私与安全性 ▸ 麦克风”中允许浏览器访问）"
      : "麦克风不可用：" + (e && e.message || e));
  }
}

async function stopMic(){
  const btn = document.getElementById("mic"), input = document.getElementById("dmsg");
  micOn = false; btn.classList.remove("rec");
  try { micNode.disconnect(); } catch(e){}
  micStream.getTracks().forEach(t => t.stop());
  const rate = micCtx.sampleRate;
  micCtx.close();
  const wav = encodeWAV(micBuf, rate);
  const hold = input.placeholder; input.placeholder = "正在转写…";
  let r; try { r = await (await fetch("/api/voice", {method:"POST", body:wav})).json(); }
  catch(e){ r = {error:String(e)}; }
  input.placeholder = hold;
  if (r.error){ input.value = ""; micHint("语音：" + r.error); return; }
  if (r.text){ input.value = r.text; input.focus(); }
}

// float32 chunks → 16-bit PCM mono WAV blob
function encodeWAV(chunks, rate){
  let n = 0; chunks.forEach(c => n += c.length);
  const pcm = new Float32Array(n); let off = 0; chunks.forEach(c => { pcm.set(c, off); off += c.length; });
  const buf = new ArrayBuffer(44 + pcm.length * 2), view = new DataView(buf);
  const str = (o, s) => { for (let i=0;i<s.length;i++) view.setUint8(o+i, s.charCodeAt(i)); };
  str(0,"RIFF"); view.setUint32(4, 36 + pcm.length*2, true); str(8,"WAVE"); str(12,"fmt ");
  view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
  view.setUint32(24,rate,true); view.setUint32(28,rate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
  str(36,"data"); view.setUint32(40, pcm.length*2, true);
  let o = 44; for (let i=0;i<pcm.length;i++){ const s = Math.max(-1, Math.min(1, pcm[i])); view.setInt16(o, s<0 ? s*0x8000 : s*0x7FFF, true); o += 2; }
  return new Blob([view], {type:"audio/wav"});
}
function wireMic(){ const b = document.getElementById("mic"); if (b) b.onclick = toggleMic; }

window.addEventListener("hashchange", render);
window.__hold = (v)=>{ animating = v; };   // test hook: freeze the diagram
wireDock(); wireChrome(); wireMic();
refresh(); setInterval(refresh, 5000); setInterval(tickLive, 1000);
pollEvents(); setInterval(pollEvents, 450);   // live harness animation
