// waku 仪表板 — 图形工作流程：拓扑图 + 实时动画。
// 拆分出来：经典的 <script>，共享全局范围。加载顺序：static/README.md。
//
// 该图表是数据驱动的：它呈现在 /api/data 中提供的 Graph.describe()
// (d.graph.workflows)，所以该图可以证明是引擎的拓扑
// 运行 — 从 archSVG 的字节冻结中学到的防漂移教训。绝不
// 在这里手动编辑工作流程的形状；更改工作流程，如下所示。
// ID 的命名空间为“g-”，因此它们永远不会与 archSVG 的 id 发生冲突。

// --- 分层布局：从左侧开始，每个节点在其后面一列
// 最远的前身。仅小图（分类为 5 个节点）——没有库。
function graphLayout(wf){
  const names = ["START", ...wf.nodes.map(n => n.name), "END"];
  const layer = {START: 0};
  for (let pass = 0; pass < names.length; pass++)     // 放松直至稳定
    wf.edges.forEach(e => {
      const src = layer[e.src] ?? 0;
      layer[e.dst] = Math.max(layer[e.dst] ?? 0, src + 1);
    });
  const cols = [];
  names.forEach(n => {
    if (layer[n] == null) layer[n] = 0;
    (cols[layer[n]] = cols[layer[n]] || []).push(n);
  });
  return {layer, cols: cols.filter(c => c && c.length)};
}

function graphSVG(wf, opts = {}){
  const {cols} = graphLayout(wf);
  const kinds = Object.fromEntries(wf.nodes.map(n => [n.name, n.kind]));
  const W = 168, H = 52, GX = 74, GY = 22, PAD = 14;
  const height = Math.max(...cols.map(c => c.length)) * (H + GY) - GY + PAD * 2;
  const width = cols.length * (W + GX) - GX + PAD * 2;
  const pos = {};
  cols.forEach((col, ci) => col.forEach((n, ri) => {
    const colH = col.length * (H + GY) - GY;
    pos[n] = {x: PAD + ci * (W + GX), y: PAD + (height - PAD * 2 - colH) / 2 + ri * (H + GY)};
  }));
  // Id 带有工作流程名称。 START 和 END 存在于每个工作流程中，因此
  // 显示两个图表的页面，未命名空间的“g-START”同时点亮了它们
  // — 并且 hot() 故意命中每场比赛，因此该错误看起来像是一个功能。
  const nid = n => `g-${wf.name}-${n}`;
  // 这两个标签都曾经夸大其词。 “本地阅读”对于分类日历来说是正确的
  // 收集的 scan_github （子进程）和 scan_web （子进程）的 peek 和 false
  // 网络）——工具节点共享的是没有模型运行。还有“小模特”
  // 分类的分类为真，而收集的合成为假，后者使用
  // 主要的一个；单独的种类不知道是哪一种，所以不要声称。
  const SUB = {llm: "one model call", agent: "THE loop, as a node", tool: "code, no model", fn: ""};
  const nodeBox = n => {
    const p = pos[n];
    if (n === "START" || n === "END")
      return `<g class="node" data-node="${nid(n)}">
        <rect class="bx" x="${p.x + W/2 - 34}" y="${p.y + H/2 - 15}" width="68" height="30" rx="15"/>
        <text class="nt" x="${p.x + W/2}" y="${p.y + H/2 + 5}" text-anchor="middle" style="font-size:12px">${n}</text></g>`;
    const sub = SUB[kinds[n]] || "";
    return `<g class="node" data-node="${nid(n)}">
      <rect class="bx" x="${p.x}" y="${p.y}" width="${W}" height="${H}" rx="9"/>
      <text class="nt" x="${p.x + 12}" y="${p.y + 22}">${esc(n)}</text>
      ${sub ? `<text class="ns" x="${p.x + 12}" y="${p.y + 39}">${sub}</text>` : ""}</g>`;
  };
  const edgeLine = e => {
    const a = pos[e.src], b = pos[e.dst];
    const x1 = a.x + (e.src === "START" ? W/2 + 34 : W), y1 = a.y + H/2;
    const x2 = b.x + (e.dst === "END" ? W/2 - 34 : 0), y2 = b.y + H/2;
    const mx = (x1 + x2) / 2;
    return `<path class="flow${e.conditional ? " dash" : ""}" data-edge="g-${wf.name}-${e.src}-${e.dst}"
      d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" marker-end="url(#garr)"/>`;
  };
  return `<div style="overflow-x:auto"><svg viewBox="0 0 ${width} ${height}" class="arch graphchart"
      style="max-width:${width}px" role="img">
    <defs><marker id="garr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
      orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" class="head"/></marker></defs>
    ${wf.edges.map(edgeLine).join("")}
    ${["START", ...wf.nodes.map(n => n.name), "END"].map(nodeBox).join("")}
  </svg></div>`;
}

// 现在正在运行哪个工作流程，由 graph_start 设置并由
// 图结束。概述面板用于读取最后一次完成的运行，因此在
// 聚集它仍然显示分类 - 并且由于 animateGraphStage 只点亮
// 屏幕上的图表也没有亮起。一张错误的图表导致了这两种情况
// bugs：你看到了旧的形状，你看到它保持黑暗。
let GRAPH_LIVE = null;
// 运行结束后继续显示的工作流程。没有它，面板会回落
// 到 d.graph.runs[0] 即时 graph_end 被触发 - 并且该有效负载仅
// 由 /api/data 轮询刷新，因此它仍然命名为 PREVIOUS
// 跑步。观察为：交换以收集，快速返回分类，然后返回收集
// 当民意调查赶上时。记住本地意味着面板永远不会显示
// 工作流程比刚刚观看的工作流程旧。
let GRAPH_SHOWN = null;

// --- 紧凑的概述面板：线束自动决定，这反映了它。
function graphPanel(d){
  const g = d.graph || {enabled: false, workflows: [], runs: [], stats: {quick: 0, full: 0}};
  // 概览是一个状态界面——“刚刚发生了什么”——而图表选项卡
  // 是一个参考：“存在什么形状”。这显示了工作流程
  // 最近的 RAN，来自跟踪。将其固定到工作流程 [0] 意味着概述
  // 在收集后几秒钟永远显示分类，这就是面板显示的原因
  // 作为剩菜而不是新闻。
  // 飞行中的奔跑胜过最后完成的——在你想要的聚集期间
  // 观看聚集，而不是阅读之前的分类。
  const showing = GRAPH_LIVE || GRAPH_SHOWN || ((g.runs || [])[0] || {}).workflow;
  const last = (g.runs || [])[0];
  const wf = (g.workflows || []).find(w => w && w.name === showing)
             || (g.workflows || [])[0];
  const tot = g.stats.quick + g.stats.full;
  const seg = (cls, n, label, pct) =>
    `<div class="${cls}" style="width:${pct}%">${pct >= 14 ? `${n} ${label}` : ""}</div>`;
  const split = !tot
    ? `<div class="meta" style="margin:6px 0 10px">no graph turns yet — every message will route here once it's on</div>`
    : `<div class="splitbar">
        ${seg("seg-skip", g.stats.quick, "quick", Math.round(g.stats.quick / tot * 100))}
        ${seg("seg-ret", g.stats.full, "full", 100 - Math.round(g.stats.quick / tot * 100))}
      </div><div class="meta" style="margin:6px 0 10px">${g.stats.quick} answered by the small model alone — the loop never woke</div>`;
  // 标志门 TRIAGE——每条消息的门——除此之外别无其他。 `瓦库
  // Gather` 是一个你自己启动并运行的例程，所以旧的
  // 复制（“关闭=每回合都运行经典循环”）是悄悄地错误的
  // 当第二个工作流程存在时。
  if (!g.enabled && !last)
    return `<div class="card"><div class="meta">The per-message graph door is <b>off</b> — every chat turn
      runs the classic loop above. Switch on <b>graph workflows</b> in
      <a class="reveal" onclick="location.hash='settings'">Behaviour</a> to triage each message first.
      Workflows you run yourself, like <code>make gather</code>, do not need the flag —
      <a class="reveal" onclick="location.hash='graph'">see them here</a>.</div></div>`;
  const when = GRAPH_LIVE
    ? `<span class="live-dot"></span><b>${esc(GRAPH_LIVE)}</b> running now`
    : last
    ? `last run: <b>${esc(last.workflow || "")}</b>${last.ms ? ` · ${(last.ms/1000).toFixed(1)}s` : ""}${
        last.steps ? ` · ${last.steps} nodes` : ""}`
    : "live — nodes light up as a turn flows through";
  return `<div class="card" style="cursor:pointer" onclick="location.hash='graph'">
    ${g.enabled ? split : ""}${wf ? graphSVG(wf) : ""}
    <div class="meta" style="margin-top:8px">${when} · click for the full story</div></div>`;
}

// --- 实时动画：与循环的 STAGE 地图相同的机制。热()灯
// 页面上的每个副本，因此“概述”面板和“图表”选项卡一起发光。
const GRAPH_KINDS = new Set(["graph_start", "node_start", "node_end", "route", "graph_end"]);
function animateGraphStage(ev){
  if (!document.querySelector(".graphchart")) return;
  const status = t => document.querySelectorAll(".arch-status").forEach(
    st => st.innerHTML = `<span class="live-dot"></span>${t}`);
  // 每个图形事件都带有“workflow”，因此 ids 的范围可以限定在图表中
  // 它实际上正在运行，而不是点亮页面上的每个图表。
  const w = ev.workflow || "";
  if (ev.type === "graph_start"){
    // 在运行任何内容之前将概述图表交换到此工作流程，因此
    // 即将点亮的节点是屏幕上的节点。
    graphLive(w);
    status(`${w} starts`);
    hot(`[data-node="g-${w}-START"]`, "hot", 1000);
  }
  else if (ev.type === "node_start"){
    status(`${w} · ${ev.node}`);
    // 保持，而非脉冲：只要节点正在工作，它就会一直亮着。脉动开启
    // node_end 只显示已经完成的内容，即
    // 与观看它发生相反——并且一波中有四个节点
    // 看到扇出和看到四次闪烁之间的区别。
    document.querySelectorAll(`[data-node="g-${w}-${ev.node}"]`)
      .forEach(el => el.classList.add("hot"));
  }
  else if (ev.type === "node_end"){
    document.querySelectorAll(`[data-node="g-${w}-${ev.node}"]`)
      .forEach(el => el.classList.remove("hot"));
    hot(`[data-node="g-${w}-${ev.node}"]`, "done", 900);
  }
  else if (ev.type === "route"){
    status(`route → ${ev.target}`);
    hot(`[data-edge="g-${w}-${ev.router}-${ev.target}"]`, "live", 1400);
    hot(`[data-node="g-${w}-${ev.target}"]`, "hot", 1400);
  }
  else if (ev.type === "graph_end"){
    hot(`[data-node="g-${w}-END"]`, "hot", 1000);
    graphLive(null);
  }
}

// 设置实时工作流程重新渲染，以便面板在运行时交换
// 开始而不是在下一次投票时。
function graphLive(name){
  if (GRAPH_LIVE === name) return;
  if (name) GRAPH_SHOWN = name;   // 粘性：比运行更持久，因此不会弹回
  GRAPH_LIVE = name;
  if (typeof render === "function") render();
}

// ---------------------------------------------------------------------------
// THE RUNNER — N 个节点作为一排活牌。
//
// 上面的拓扑图显示了 SHAPE：“这四个是独立的”。它
// 无法证明他们实际上是一起跑的，因为照片没有时间
// 轴，观察者无法区分同时点亮的四个盒子和快速点亮的四个盒子
// 按顺序。所以卡片承载着时间：它们一起开始，滴答作响
// 运行，并无序完成。看着三个沉稳而一个旋转是
// 图表只能承诺的证明。
//
// 故意与竞技场形状相同 - arena.py 用“spec”标记每个事件
// 并将其路由到卡；图形引擎已经标记了每个事件
// `节点`。交换密钥，重用 .cmp-grid/.cmp-col，它作为同级读取
// 因为它是一个。
let graphRun = {running: false, workflow: "", nodes: {}, order: [], waves: [],
                digest: "", draft: "", error: "", ticker: null};

function graphResetRun(workflow){
  graphRun = {running: true, workflow, nodes: {}, order: [], waves: [],
              digest: "", draft: "", error: "", ticker: graphRun.ticker};
}

function graphApplyEvent(ev){
  const R = graphRun;
  const k = ev.kind;
  if (k === "graph_start"){
    R.order = ev.nodes || [];
    R.order.forEach(n => R.nodes[n] = {status: "waiting"});
  } else if (k === "node_start"){
    // 波是“在任何节点完成之前开始的节点”。那是
    // 这正是引擎所说的波浪的含义，也是行分组的依据。
    const open = R.waves[R.waves.length - 1];
    if (open && !open.closed) open.nodes.push(ev.node);
    else R.waves.push({nodes: [ev.node], closed: false});
    R.nodes[ev.node] = {status: "running", startedAt: performance.now()};
  } else if (k === "node_end"){
    const w = R.waves[R.waves.length - 1];
    if (w) w.closed = true;   // 第一次完成结束了新成员的浪潮
    R.nodes[ev.node] = {status: ev.error ? "error" : "done", ms: ev.ms,
                        keys: ev.keys || [], error: ev.error || ""};
  } else if (k === "route"){
    R.route = {target: ev.target, reason: ev.reason};
  } else if (k === "graph_end"){
    R.running = false; R.totalMs = ev.ms;
  } else if (k === "done"){
    R.running = false;
    R.digest = ev.digest || ""; R.draft = ev.draft_path || ""; R.error = ev.error || "";
  }
}

async function runGraph(workflow){
  if (graphRun.running) return;
  graphResetRun(workflow);
  // 如果没有股票行情记录器，经过的数字会冻结，并且卡片看起来与
  // 顺序运行——这是这个观点所要反驳的一件事。
  clearInterval(graphRun.ticker);
  graphRun.ticker = setInterval(() => { if (graphRun.running) render(); }, 100);
  render();
  try {
    const res = await fetch("/api/graph/stream", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({workflow}),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const p of parts){
        const line = p.trim();
        if (!line.startsWith("data:")) continue;
        try { graphApplyEvent(JSON.parse(line.slice(5))); } catch (e) { /* partial frame */ }
        render();
      }
    }
  } catch (e){
    graphRun.error = String(e);
  } finally {
    graphRun.running = false;
    clearInterval(graphRun.ticker);
    render();
  }
}

function graphCol(name){
  const n = graphRun.nodes[name] || {status: "waiting"};
  if (n.status === "waiting")
    return `<div class="cmp-col" style="opacity:.5"><div class="cmp-h"><b>${esc(name)}</b></div>
      <div class="meta">queued</div></div>`;
  if (n.status === "running"){
    const el = ((performance.now() - n.startedAt) / 1000).toFixed(1);
    return `<div class="cmp-col"><div class="cmp-h"><b>${esc(name)}</b></div>
      <div class="meta"><span class="live-dot"></span>${el}s</div></div>`;
  }
  if (n.status === "error")
    return `<div class="cmp-col err"><div class="cmp-h"><b>${esc(name)}</b></div>
      <div class="meta" style="color:var(--bad)">${esc(n.error)}</div></div>`;
  // 条形图缩放至该节点波中最慢的节点，并且速度更快
  // 节点打印它在屏障处等待所花费的时间。这个数字是诚实的
  // 波次执行的成本——打印它比隐藏它能学到更多。
  const wave = graphRun.waves.find(w => w.nodes.includes(name));
  const peers = (wave ? wave.nodes : [name]).map(x => (graphRun.nodes[x] || {}).ms || 0);
  const slowest = Math.max(...peers, 1);
  const pct = Math.round((n.ms || 0) / slowest * 100);
  const waited = slowest - (n.ms || 0);
  return `<div class="cmp-col"><div class="cmp-h"><b>${esc(name)}</b>
      <span class="chip">${n.ms}ms</span></div>
    <div class="wavebar"><i style="width:${pct}%"></i></div>
    <div class="meta">${waited > 20 && peers.length > 1
      ? `waited ${(waited/1000).toFixed(1)}s at the barrier`
      : (peers.length > 1 ? "set the pace for this wave" : "")}</div>
    <div class="meta">${(n.keys || []).map(k => `<span class="chip">${esc(k)}</span>`).join(" ")}</div>
  </div>`;
}

function graphRunPanel(){
  const R = graphRun;
  const btn = `<button class="btn" onclick="runGraph('gather')" ${R.running ? "disabled" : ""}>
    ${R.running ? "running…" : "Run gather"}</button>`;
  let h = `<h2>Run it — watch the wave <span class="meta" style="font-weight:400">
    the chart shows the shape; these cards show it happening</span></h2>
    <div class="card">${btn}
    <span class="meta" style="margin-left:10px">fetches GitHub, the web, your calendar and your
    memory — together. Proposes only: the digest lands in the outbox.</span>`;
  if (R.error) h += `<div class="meta" style="color:var(--bad);margin-top:10px">${esc(R.error)}</div>`;
  R.waves.forEach((w, i) => {
    const done = w.nodes.filter(n => (R.nodes[n] || {}).ms != null);
    const slowest = done.length ? Math.max(...done.map(n => R.nodes[n].ms)) : 0;
    const sum = done.reduce((a, n) => a + R.nodes[n].ms, 0);
    h += `<div class="meta" style="margin:14px 0 6px">wave ${i + 1} · ${w.nodes.length}
      node${w.nodes.length > 1 ? "s" : ""}${slowest ? ` · ${(slowest/1000).toFixed(1)}s`
      + (w.nodes.length > 1 ? ` (in sequence it would be ${(sum/1000).toFixed(1)}s)` : "") : ""}</div>
      <div class="cmp-grid">${w.nodes.map(graphCol).join("")}</div>`;
  });
  if (R.totalMs) h += `<div class="meta" style="margin-top:12px">finished in
    ${(R.totalMs/1000).toFixed(1)}s${R.draft ? ` · saved to <code>${esc(R.draft)}</code>` : ""}</div>`;
  if (R.digest) h += `<div class="card" style="margin-top:10px">${renderMarkdown(R.digest)}</div>`;
  return h + `</div>`;
}
