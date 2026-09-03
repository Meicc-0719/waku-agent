// Waku 仪表盘——对话会话/历史记录（loadThreadInto）、模型标签及统计开关。
// 从 app.js 拆分而来：经典 <script> 标签，共享全局作用域（无构建步骤、无模块）。
// 加载顺序和约定见 static/README.md。

// --- 对话会话（类似聊天应用的“新建对话”和历史记录选择器）。
let SESSION = "default";
async function newChat(){
  const r = await postJSON("/api/session", {action:"new"});
  if (r.session_id){ liveView = null; SESSION = r.session_id; CHAT.length = 0; syncChatLogs(); }
  closeSessMenu();
}
// 将会话记录载入停靠栏的唯一入口，避免各路径行为漂移（过去有些路径丢失 meta，
// 有些使用长度保护，有些没有）。
//   mode 'switch'  -> action:switch，同时切换代理的活动会话
//   mode 'history' -> action:history，只读（'__all__' 表示完整时间线）
// 替换 CHAT 并重绘；若设置 `guard` 且长度未变则不重绘（实时轮询情形，避免无谓重绘）。
// 返回会话项目或 null。
async function loadThreadInto(id, {mode = "history", setSession = false, guard = false} = {}){
  const r = await postJSON("/api/session", {action: mode, id});
  if (!r.ok) return null;
  const fresh = (r.history || []).map(histItem);
  if (guard && fresh.length === CHAT.length) return fresh;   // 未变化则跳过重绘。
  if (setSession) SESSION = r.session_id;
  CHAT.length = 0; fresh.forEach(m => CHAT.push(m)); syncChatLogs();
  return fresh;
}
async function switchSession(id){
  await loadThreadInto(id, {mode: "switch", setSession: true});
  closeSessMenu();
}
// 从渠道收件箱打开会话：将其加载到停靠栏（活动会话），保持实时同步（显示新的 Telegram/语音消息），
// 并确保停靠栏可见。
let liveView = null;   // 从收件箱打开并保持实时更新的会话。
async function openConversation(id){
  document.body.classList.remove("dock-closed");
  localStorage.setItem("dockClosed", "0");
  liveView = id;
  await switchSession(id);   // 切换代理会话，使回复继续在此会话中进行。
  render();                  // 在收件箱中反映活动会话高亮。
}
// 只读的“全部”视图：在停靠栏中以对话形式显示跨会话完整时间线，类似循环标签页。
// 不切换代理会话——下一条消息仍会发送到当前活动会话；此视图仅用于浏览完整历史。
async function viewAllHistory(){
  closeSessMenu();
  document.body.classList.remove("dock-closed");
  localStorage.setItem("dockClosed", "0");
  liveView = "__all__";
  await loadThreadInto("__all__");
}
// 每次刷新都重新拉取已打开的会话，使其他渠道（例如手机）的新消息实时出现；
// 若停靠栏有正在流式输出的轮次则不拉取。
async function syncLiveView(){
  if (!liveView || CHAT.some(m => m.pending)) return;
  await loadThreadInto(liveView, {guard: true});   // 守卫：仅在更改时重新绘制
}
function closeSessMenu(){ const m=document.getElementById("sessmenu"); if(m) m.remove(); }
function toggleSessMenu(ev){
  ev.stopPropagation();
  if (document.getElementById("sessmenu")){ closeSessMenu(); return; }
  const sessions = (D && D.sessions) || [];
  const menu = document.createElement("div");
  menu.className = "sessmenu"; menu.id = "sessmenu";
  // “全部消息”以对话形式显示跨会话完整时间线（类似循环标签页），使完整历史可在
  // 一处滚动浏览，而不必分散在各会话中。
  const allItem = `<div class="sessitem allitem ${liveView==='__all__'?'on':''}" onclick="viewAllHistory()">
      <div><b>全部消息</b>——完整时间线</div>
      <div class="sm">所有会话合并显示，最新消息在后</div></div>`;
  menu.innerHTML = allItem + (sessions.length ? sessions.map(s => {
    const tags = gwTags(s);
    return `<div class="sessitem ${s.id===SESSION?"on":""}" onclick="openConversation('${esc(s.id)}')">
      <div>${esc(s.title||s.id)} ${tags}</div>
      <div class="sm">${sessionMeta(s)}</div>
    </div>`;
  }).join("") : `<div class="sessitem">暂无历史对话</div>`);
  const r = ev.currentTarget.getBoundingClientRect();
  menu.style.top = (r.bottom+6)+"px";
  menu.style.left = Math.max(8, r.right-300)+"px";
  document.body.appendChild(menu);
}
document.addEventListener("click", e => {
  const m = document.getElementById("sessmenu");
  if (m && !m.contains(e.target)) closeSessMenu();
  const mm = document.getElementById("modelmenu");
  const chip = document.getElementById("modelchip");
  if (mm && !mm.contains(e.target) && e.target !== chip && !chip?.contains(e.target)) closeModelMenu();
});

// --- 对话停靠栏中的小型模型切换器：标签显示当前模型，点击后展示实时目录，
// 无需离开对话即可切换。请求发送至 /api/providers（与模型页面共用的接口）。
function syncModelChip(){
  const el = document.getElementById("modelchip");
  if (!el || !D || !D.settings) return;
  const st = D.settings;
  el.innerHTML = `<span class="mc-dot"></span><span class="mc-name">${esc(st.model || st.provider || "model")}</span><span class="mc-caret">&#9662;</span>`;
}
function closeModelMenu(){ const m = document.getElementById("modelmenu"); if (m) m.remove(); }

// --- 每轮统计信息开关（闸门/秒数/迭代次数/工具）。默认开启，选择会持久保存到 localStorage。
// 通过 body 类隐藏 .tele 区块，因此对已渲染的轮次也生效。
function applyTele(){
  const off = localStorage.getItem("waku_tele") === "0";
  document.body.classList.toggle("no-tele", off);
  const b = document.getElementById("teletoggle");
  if (b) b.classList.toggle("on", !off);
}
function toggleTele(){
  const off = localStorage.getItem("waku_tele") === "0";
  localStorage.setItem("waku_tele", off ? "1" : "0");   // 切换状态。
  applyTele();
}
function toggleModelMenu(ev){
  ev.stopPropagation();
  if (document.getElementById("modelmenu")){ closeModelMenu(); return; }
  const st = (D && D.settings) || {};
  // 已禁用的供应商不会出现在切换器中（由模型网格的禁用按钮控制）；
  // 它们的固定记录仍会保留，重新启用后再次显示。
  const disabled = st.disabled_providers || [];
  const pinned = (st.pinned || []).filter(p => !disabled.includes(p.provider));
  const items = pinned.length ? pinned.map(p =>
    `<div class="sessitem ${(p.provider===st.provider && p.model===st.model)?"on":""}"
          onclick="switchTo('${esc(p.provider)}','${esc(p.model)}')">
       <span class="mm-prov">${esc(p.provider)}</span> <span class="mm-id">${esc(p.model)}</span>${
       p.default?'<span class="mm-def">默认</span>':""}</div>`
  ).join("") : `<div class="sessitem">尚未固定模型。</div>`;
  const menu = document.createElement("div");
  menu.className = "sessmenu modelmenu"; menu.id = "modelmenu";
  menu.innerHTML = `<div class="mm-h">你的模型</div>${items}`
    + `<div class="mm-f"><a href="#models" onclick="closeModelMenu()">+ 在“模型”中添加模型 &rsaquo;</a></div>`;
  const r = ev.currentTarget.getBoundingClientRect();
  menu.style.top = (r.bottom + 6) + "px";
  menu.style.left = Math.max(8, r.right - 250) + "px";
  document.body.appendChild(menu);
}
// 一次点击同时切换供应商和模型（固定模型可来自任意供应商）。同供应商切换保留闸门模型；
// 跨供应商切换则使用新供应商的默认闸门模型。
async function switchTo(provider, model){
  const st = (D && D.settings) || {};
  const chip = document.getElementById("modelchip");
  const name = chip && chip.querySelector(".mc-name");
  closeModelMenu();
  if (name) name.textContent = "switching…";
  await postJSON("/api/providers", {provider, model,
    small_model: provider === st.provider ? st.small_model : ""});
  await refresh();
}
