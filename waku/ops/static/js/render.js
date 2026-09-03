// Waku 仪表盘——格式化器、对话卡片渲染、聊天记录、流式输出和发送功能。
// 从 app.js 拆分而来：经典 <script> 标签，共享全局作用域（无构建步骤、无模块）。
// 加载顺序和约定见 static/README.md。

const money = n => "$" + (n < 0.01 ? n.toFixed(4) : n.toFixed(2));
const secs = ms => ms==null ? "—" : (ms/1000).toFixed(1)+"s";

const gateBadge = g => !g ? "" :
  `<span class="badge ${g.decision==="retrieve"?"retrieve":""}">gate · ${esc(g.decision)}</span><span class="meta" style="margin:0">${esc(g.reason||"")}</span>`;

// 工具调用渲染为状态行（圆点加单行摘要）；原始输出放在可展开区域中，避免难看的
// osascript 错误淹没整个页面。
const toolRow = x => `<div class="tool ${x.status||"ok"}">
  <div class="tool-head"><span class="dot ${x.status||"ok"}"></span><code>${esc(x.tool)}</code>
    ${x.summary?`<span style="color:var(--ink2)">${esc(x.summary)}</span>`:""}</div>
  ${x.output!==undefined?`<details><summary>参数与原始输出</summary>
    <pre>${esc(x.tool)}(${esc(JSON.stringify(x.args,null,1))})\n\n${esc(x.output)}</pre>
  </details>`:""}
</div>`;

// 将存储的历史行转换为 CHAT 项目。带已保存遥测信息（meta：闸门/延迟/迭代次数/工具）的
// 助手行渲染为完整任务卡片，因此重新打开的会话与实时显示时一致。缺少 meta 的行
// （保存此信息之前的记录或其他渠道的记录）回退为普通卡片。
function histItem(m){
  if (m.role === "user") return {role:"user", text:m.content};
  if (m.meta) return {role:"waku", reply:m.content, gate:m.meta.gate,
                      graph:m.meta.graph,
                      tools:m.meta.tools, iterations:m.meta.iterations,
                      latency_ms:m.meta.latency_ms, model:m.meta.model};
  return {role:"waku", reply:m.content, historical:true};
}

const turnCard = t => `<div class="card">
  <div class="u">${esc(t.user_message)}</div>
  <div class="meta" style="margin-top:4px">${gateBadge(t.gate)}</div>
  ${(t.tools||[]).map(toolRow).join("")}
  <div class="r">${renderMarkdown(t.reply)}</div>
  <div class="meta">${esc((t.ts||"").replace("T"," ").slice(0,19))} · ${secs(t.latency_ms)} · ${t.iterations??"?"} 次迭代 · ${money(t.cost||0)}${t.consolidation?` · 已巩固 ${t.consolidation.new_facts} 条事实`:""}</div>
</div>`;

const table = (heads, rows) => rows.length
  ? `<div class="card" style="padding:4px 8px"><table><tr>${heads.map(h=>`<th>${h}</th>`).join("")}</tr>${rows.join("")}</table></div>`
  : `<div class="card empty">这里暂时没有内容</div>`;

const gateSplit = s => {
  if (!(s.gate_skips + s.gate_retrieves))
    return `<div class="splitbar"><div class="seg-skip" style="width:100%;opacity:.35"></div></div>
      <div class="meta" style="margin-top:6px">暂无任务轮次——发送一条消息后，检索闸门便会开始决策</div>`;
  const tot = s.gate_skips + s.gate_retrieves;
  const skipPct = Math.round(s.gate_skips/tot*100), retPct = 100-skipPct;
  // 仅当区段足够宽以容纳文字时才显示标签；否则 0% 或极窄区段会让标签溢出进度条
  // （即“0 retri”问题）。
  const seg = (cls, n, label, pct) =>
    `<div class="${cls}" style="width:${pct}%">${pct>=14?`${n} ${label}`:""}</div>`;
  return `<div class="splitbar">
    ${seg("seg-skip", s.gate_skips, "skipped", skipPct)}
    ${seg("seg-ret", s.gate_retrieves, "retrieved", retPct)}
  </div><div class="meta" style="margin-top:6px">检索闸门在 ${skipPct}% 的轮次中跳过了记忆，从而节省了延迟并减少偏差</div>`;
};

// --- 对话渠道：在此输入并观察框架运行（轮次保存在内存中）。
const CHAT = [];
// “闸门 → 工具 → 回复”阶段条由实时卡片与完成/回放卡片共用，确保标记结构不会漂移。
// `live` 会点亮阶段（闸门决策后变为 done，文本流出后回复变为 on）；否则所有阶段均完成，
// 阶段条携带 .tele 类（由统计开关隐藏）。（.stages 使用 flexbox，span 之间的空白不影响布局。）
function stagesRow(t, live){
  const gateCls = live ? (t.gate ? "done" : "on") : "done";
  const replyCls = live ? (t.stream ? "on" : "") : "done";
  const tools = (t.tools||[]).map(x => toolChip(x.tool)).join("");
  // 图工作流标记位于最前，代表入口。快速图工作流轮次没有闸门阶段（从未运行记忆检索），
  // 因此闸门标记会如实隐藏。
  const graph = (t.graph && t.graph.route)
    ? `<span class="stage done">graph · ${esc(t.graph.route)}</span>` : "";
  const gate = (t.graph && t.graph.route === "quick") ? ""
    : `<span class="stage ${gateCls}">gate${t.gate?` · ${esc(t.gate.decision)}`:""}</span>`;
  return `<div class="stages${live?"":" tele"}">`
    + graph + gate + tools + `<span class="stage ${replyCls}">回复</span></div>`;
}
// 每轮遥测信息页脚：秒数 · 迭代次数 · 模型 · 巩固情况。
const teleFooter = t => `<div class="meta tele">${secs(t.latency_ms)} · ${t.iterations??"?"} 次迭代${
  t.model?` · ${esc(t.model)}`:""}${t.consolidation?` · 已巩固 ${t.consolidation.new_facts} 条事实`:""}</div>`;

const chatTurnCard = t => `<div class="card">
  <button class="msg-copy" onclick="copyMsg(this)" data-text="${esc(t.reply)}" title="复制回复">复制</button>
  ${(t.gate||t.graph)?`${stagesRow(t, false)}
    <div class="meta tele" style="margin:0 0 6px">${esc((t.gate&&t.gate.reason)||(t.graph&&t.graph.reason)||"")}</div>`:""}
  ${nodesRow(t)}
  ${(t.tools||[]).length?`<div class="tele">${(t.tools||[]).map(toolRow).join("")}</div>`:""}
  <div class="r" style="margin-top:8px">${renderMarkdown(t.reply)}</div>
  ${teleFooter(t)}
</div>`;

// 任务轮次运行时进行实时流式显示：框架到达各阶段时点亮对应标记，回复文本按 token 出现
// （带闪烁光标）。图节点以标签形式显示：运行时点亮，完成后显示实测耗时。多个标签同时
// 点亮即代表扇出并发，这是“正在思考…”无法表达的信息。
const nodesRow = m => {
  const names = Object.keys(m.nodes || {});
  if (!names.length) return "";
  return `<div class="cmp-stats" style="margin:0 0 6px">` + names.map(n => {
    const s = m.nodes[n];
    const cls = s.status === "running" ? "chip live" : s.status === "error" ? "chip err" : "chip";
    const suffix = s.status === "running" ? "" : s.ms != null ? ` ${s.ms}ms` : "";
    return `<span class="${cls}">${esc(n)}${suffix}</span>`;
  }).join("") + `</div>`;
};

const streamingCard = m => `<div class="card">
  ${stagesRow(m, true)}
  ${nodesRow(m)}
  ${m.gate&&m.gate.reason?`<div class="meta" style="margin:0 0 6px">${esc(m.gate.reason)}</div>`:""}
  ${(m.tools||[]).map(toolRow).join("")}
  ${m.stream
     ? `<div class="r" style="margin-top:8px">${renderMarkdown(m.stream)}<span class="caret"></span></div>`
     : `<div class="meta" style="margin:0">正在思考&hellip;${m.started?` ${Math.round((Date.now()-m.started)/1000)} 秒`:""}${
         m.started && Date.now()-m.started > 20000
         ? `<br>仍在等待：较慢的模型（尤其是免费层）可能会排队一段时间；到达 WAKU_LLM_TIMEOUT 限制后会报错，而不会永久卡住`
         : ""}</div>`}
</div>`;

// 从历史记录加载的消息（切换或打开的会话）没有实时延迟/迭代数据，其存储形式还带有内部
// “［已用工具：…］”标注；移除两者以保持会话阅读整洁。
const stripTools = t => (t || "").replace(/\s*\[tools used:[\s\S]*\]\s*$/, "").trim();
const historicalCard = m => `<div class="card">
  <button class="msg-copy" onclick="copyMsg(this)" data-text="${esc(stripTools(m.reply))}" title="复制回复">复制</button>
  <div class="r">${renderMarkdown(stripTools(m.reply))}</div>
</div>`;

function renderChatLog(){
  if (!CHAT.length)
    return `<div class="empty" style="padding:6px 2px">可在任何标签页中向 Waku 发送消息。打开“概览”可查看消息如何流经框架，打开“渠道”可汇总查看所有来源的消息。</div>`;
  return CHAT.map(m => m.role==="user"
      ? `<div class="bubble">${esc(m.text)}</div>`
      : m.pending ? streamingCard(m)
      : m.historical ? historicalCard(m)
      : chatTurnCard(m)).join("");
}

function syncChatLogs(){
  // 一个会话、两个界面：对话与监看标签页以及侧边停靠栏。
  document.querySelectorAll(".chatlog").forEach(el => {
    el.innerHTML = renderChatLog();
    el.scrollTop = el.scrollHeight;      // 停靠栏滚动其自身容器。
  });
}

// 单个流式框架事件会原地更新实时卡片。
function applyStreamEvent(pending, ev){
  // 从对话框调用工作流时，图事件也会到达此处。追踪轮询器无论如何都会为图表制作动画，
  // 但它每 450 毫秒运行一次且阶段动画间隔为 620 毫秒；直接调用 graphLive() 可让概览面板
  // 在点击发送后立即切换为正在运行的工作流。
  if (ev.kind === "graph_start" && typeof graphLive === "function") graphLive(ev.workflow);
  else if (ev.kind === "graph_end" && typeof graphLive === "function") graphLive(null);
  // 图节点不是 ToolRegistry 工具，因此不会进入工具标签；一次 /gather 运行十三秒时只会
  // 显示“正在思考…”。单独追踪并以相同方式渲染它们，因为“此刻哪四件事正在进行”正是
  // 并发波次的核心信息。
  if (ev.kind === "node_start"){
    (pending.nodes = pending.nodes || {})[ev.node] = {status: "running"};
  } else if (ev.kind === "node_end"){
    (pending.nodes = pending.nodes || {})[ev.node] =
      {status: ev.error ? "error" : "done", ms: ev.ms};
  }
  if (ev.kind === "gate") pending.gate = {decision: ev.decision, reason: ev.reason};
  else if (ev.kind === "route")
    pending.graph = {route: ev.target === "quick_reply" ? "quick" : "full",
                     reason: (pending.graph || {}).reason};
  else if (ev.kind === "triage") (pending.graph = pending.graph || {}).reason = ev.reason;
  else if (ev.kind === "text") pending.stream = (pending.stream || "") + (ev.delta || "");
  else if (ev.kind === "tool"){
    (pending.tools = pending.tools || []).push({
      tool: ev.tool, args: ev.args, output: ev.output,
      status: (ev.output||"").toLowerCase().startsWith("error") ? "error" : "ok",
      summary: (ev.output || "").split(". ")[0].slice(0,120)});
    pending.stream = "";   // 工具结果返回后，开始新一轮助手输出。
  } else if (ev.kind === "done"){
    pending.pending = false; pending.stream = "";
    if (ev.error) pending.reply = "Error: " + ev.error;
    else Object.assign(pending, ev);   // 回复、工具、闸门、迭代次数、延迟和巩固信息。
  }
}

async function sendChat(fromInput){
  const input = fromInput || document.getElementById("msg") || document.getElementById("dmsg");
  const text = (input && input.value || "").trim();
  if (!text) return;
  input.value = "";
  CHAT.push({role:"user", text});
  const pending = {role:"waku", pending:true, stream:"", started: Date.now()};
  CHAT.push(pending);
  syncChatLogs();
  // 等待首个 token 时刷新已用时间计数器。
  const ticker = setInterval(() => { if (pending.pending && !pending.stream) syncChatLogs(); }, 1000);
  try {
    const res = await fetch("/api/chat/stream", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:text})});
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0){
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        try { applyStreamEvent(pending, JSON.parse(line.slice(5).trim())); } catch(e){}
        syncChatLogs();
      }
    }
  } catch(e){ Object.assign(pending, {pending:false, reply:"Error: "+e}); }
  clearInterval(ticker);
  if (pending.pending) pending.pending = false;   // 流在未收到 'done' 事件时结束。
  syncChatLogs();
  input.focus();
}
function wireDock(){
  const b = document.getElementById("dsend"), i = document.getElementById("dmsg");
  if (b) b.onclick = () => sendChat(i);
  if (i) i.onkeydown = e => { if (e.key==="Enter") sendChat(i); };
  const close = document.getElementById("dock-close"), reopen = document.getElementById("dock-reopen");
  const setClosed = v => { document.body.classList.toggle("dock-closed", v); localStorage.setItem("dockClosed", v?"1":"0"); };
  if (close) close.onclick = () => setClosed(true);
  if (reopen) reopen.onclick = () => setClosed(false);
  const saved = localStorage.getItem("dockClosed");
  setClosed(saved === null ? window.innerWidth < 1180 : saved === "1");
  syncChatLogs();
}

