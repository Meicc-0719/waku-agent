// Waku 仪表盘——渲染/刷新循环、尺寸调整、界面控制、语音和启动入口（最后加载）。
// 从 app.js 拆分而来：经典 <script> 标签，共享全局作用域（无构建步骤、无模块）。
// 加载顺序和约定见 static/README.md。

let activeView = null, activeSub = null;
// 哈希键保持不变：graph.js、views.js、README、DEMO-CHECKLIST 及用户书签均会链接到
// #settings。仅调整显示标签：连接注册表将密钥、供应商和集成移出此页后，剩余内容是
// 两个影响任务轮次运行方式的开关，因此应称为“行为”而非“设置”。
const TITLES = {chat:"对话与监看", ops:"LLM 运维",
                graph:"图工作流——为循环赋予结构",
                // 竞技场标题同时按视图和子标签区分，因为侧边栏现在单独列出两种竞速。
                // 当两者藏在子标签下时，共用标题是合理的；现在有两行导航，共用标题会让
                // 页面看起来像是不知道用户点击了哪一项。
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
  // 两行导航可共用同一视图，因此指定子标签的行仅在对应子标签激活时高亮。
  // 若没有回退逻辑，直接访问 #compare 时两种竞速均不会高亮，侧边栏将没有当前页面。
  const effSub = sub || (view === "compare" ? "models" : null);
  document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("on",
    a.dataset.v === view && (!a.dataset.sub || a.dataset.sub === effSub)));
  document.getElementById("title").textContent =
    TITLES[`${view}/${effSub}`] || TITLES[view] || view[0].toUpperCase()+view.slice(1);
  if (view === "overview" || view === "graph"){
    // 动画进行中不重建，否则会清除发光的 SVG。
    if (activeView !== view || !animating){ document.getElementById("view").innerHTML = VIEWS[view](D); }
  } else if ((view === "memory" || view === "settings" || view === "database" || view === "compare" || view === "models" || view === "connections") && editing && !subChanged){
    // 不在 5 秒刷新时清除正在进行的编辑；但仍允许切换子标签。
  } else {
    editing = false;
    // 重建 #view 的 innerHTML 会重置滚动位置。同一视图刷新时（5 秒轮询或排序点击）
    // 保留阅读位置；只有真正导航（subChanged）时才回到顶部。
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
let lastCompareLoad = 0;   // 将竞技场记分板的自我修复节流到约 5 秒一次。
function tickLive(){
  if (!D) return;
  const ago = Math.round((Date.now()-lastFetch)/1000);
  document.getElementById("sub").innerHTML =
    `<span class="live"><span class="dot"></span>实时</span> · ${ago} 秒前更新 · ${esc(D.home)}`;
}
let dockRestored = false;
async function restoreDock(){
  // 页面加载时，对话停靠栏虽为空但当前会话可能已有消息；恢复这些消息，避免刷新后看似丢失对话。
  dockRestored = true;
  const sid = D && D.current_session;
  if (!sid || CHAT.length) return;
  await loadThreadInto(sid, {setSession: true});
}
async function refresh(){
  try {
    D = await (await fetch("/api/data")).json(); lastFetch = Date.now();
    render(); tickLive();
    syncModelChip();  // 保持停靠栏模型标签与当前模型一致。
    applyTele();      // 应用统计信息的开关状态（默认开启）。
    syncLiveView();   // 实时更新已打开的会话（例如新手机消息）。
    if (!dockRestored) restoreDock();
    // 自我修复竞技场记分板：它原本只在打开标签页或竞速结束后加载，缓慢/中断的竞速
    // （或短暂的服务端故障）可能留下不完整结果。查看标签页期间重新拉取服务端总计，
    // 但不在竞速或编辑过程中拉取，并限制为约每 5 秒一次，避免在更快的渲染周期中频繁请求接口。
    if (activeView === "compare" && !compareState.running && !editing
        && Date.now() - lastCompareLoad > 5000){
      lastCompareLoad = Date.now();
      loadCompareHistory();
    }
  } catch(e){ /* 服务端正在重启——继续显示上一次的数据。 */ }
}
// --- 可调整尺寸的列：拖动 nav|main 及 main|dock 之间的细手柄。
// 宽度保存在 CSS 变量和 localStorage 中，因此刷新后仍可保留。
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
  // 恢复已保存的宽度。
  const nw = localStorage.getItem("navW"); if (nw) document.documentElement.style.setProperty("--nav-w", nw+"px");
  const dw = localStorage.getItem("dockW"); if (dw) document.documentElement.style.setProperty("--dock-w", dw+"px");
  wireResizer("nav-resizer", "--nav-w", "navW", false, 150, 380);
  wireResizer("dock-resizer", "--dock-w", "dockW", true, 260, 680);
  // 显示/隐藏侧边栏。
  const setNav = v => { document.body.classList.toggle("nav-hidden", v); localStorage.setItem("navHidden", v?"1":"0"); };
  const nt = document.getElementById("nav-toggle"), nr = document.getElementById("nav-reopen");
  if (nt) nt.onclick = () => setNav(true);
  if (nr) nr.onclick = () => setNav(false);
  setNav(localStorage.getItem("navHidden") === "1");
}

// --- 仪表盘语音：在浏览器录音，由服务端使用与 `make voice` 相同的本地 Whisper 转写。
// 文本会先写入输入框供用户检查，再手动发送；数据不会离开本机。
// 语音采集通过 Web Audio API 录制 WAV（未压缩 PCM），而非 faster-whisper/PyAV 常难以
// 解码的 MediaRecorder WebM/Opus（会产生“transcription failed [Errno …]”）；服务端可直接解码 WAV。
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

// 将 float32 分片转换为 16 位 PCM 单声道 WAV Blob。
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
window.__hold = (v)=>{ animating = v; };   // 测试钩子：冻结示意图动画。
wireDock(); wireChrome(); wireMic();
refresh(); setInterval(refresh, 5000); setInterval(tickLive, 1000);
pollEvents(); setInterval(pollEvents, 450);   // 实时框架动画。
