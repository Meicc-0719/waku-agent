// Waku 仪表盘——模型竞技场：让多个模型同时处理一条消息。
// 从 app.js 拆分而来：经典 <script> 标签，共享全局作用域（无构建步骤、无模块）。
// 在 views.js 后加载，以便将页面挂载到 VIEWS。
//
// 每位参赛者均在服务端独立的临时目录中运行（见 dashboard.py 的 compare_models）。
// 这是基准测试而不是对话，因此不会触及真实记忆或日历。

// 状态可在 5 秒刷新后的重绘中保留（视图从这里重建），也会通过 localStorage 跨越标签切换和
// 完整重新加载，因此已完成的竞速不会丢失。它刻意不写入聊天记录：基准测试不是对话。
let compareState = { message: "Build a Kanto team around Pikachu — search current picks, remember it, and schedule two training sessions this week.",
                     picked: null, running: false, results: null, order: null, sortBy: "latency",
                     // 默认对每场竞速评分，使用不参赛的中立裁判。
                     judge: true, judgeModel: "openai:gpt-5.6-sol" };
try {
  const saved = JSON.parse(localStorage.getItem("waku_compare") || "null");
  if (saved){ compareState.message = saved.message ?? compareState.message;
              compareState.results = saved.results || null;
              // 仅恢复实际完成的列（丢弃过期的“竞速中…”列）。
              compareState.order = (saved.order || []).filter(s => saved.results && saved.results[s]); }
} catch(e){}
function saveCompare(){
  try { localStorage.setItem("waku_compare", JSON.stringify({
    message: compareState.message, order: compareState.order, results: compareState.results})); } catch(e){}
}

// --- 竞技场历史：过往竞速及按模型累计的记分板，来自服务端自己的 compare/history.jsonl，
// 从不使用代理真实状态。打开标签页时加载一次，每场竞速结束后刷新。
async function loadCompareHistory(){
  try {
    const h = await (await fetch("/api/compare/history")).json();
    compareState.history = h.runs || [];
    compareState.aggregate = h.aggregate || [];
  } catch(e){ compareState.history = []; compareState.aggregate = []; }
  editing = false;   // 确保编辑保护不会跳过记分板重绘。
  render();
}
// 按列对记分板排序：再次点击同列切换升/降序，新列从升序开始（耗时/Token/成本均以更低为优）。
function setBoardSort(key){
  const b = compareState.boardSort || {key: "total_cost_usd", dir: "asc"};
  compareState.boardSort = (b.key === key) ? {key, dir: b.dir === "asc" ? "desc" : "asc"} : {key, dir: "asc"};
  editing = false; render();
}
async function clearCompareHistory(){
  if (!confirm("Clear the compare scoreboard and race history? (Only the arena's own log — your real data is untouched.)")) return;
  const r = await postJSON("/api/compare/clear", {});
  compareState.history = r.runs || []; compareState.aggregate = r.aggregate || [];
  editing = false; render();
}
// 让裁判重新评测最近一场竞速中被跳过的模型（429）。更新已存储历史和可见卡片；
// only_missing 保持已评分模型不变。
async function regradeCompare(){
  if (compareState.regrading) return;
  compareState.regrading = true; editing = false; render();
  try {
    const r = await postJSON("/api/compare/regrade",
      {judge_model: compareState.judgeModel || "openai:gpt-5.6-sol", only_missing: false});
    compareState.history = r.runs || []; compareState.aggregate = r.aggregate || [];
    const last = (r.runs || [])[0];
    if (last && compareState.results){
      last.results.forEach(x => { if (compareState.results[x.spec]) compareState.results[x.spec].quality = x.quality; });
    }
  } catch(e){ compareState.raceError = "re-grade failed: " + e; }
  compareState.regrading = false; editing = false; render();
}
// 为单张卡片评分——裁判有时会因 429 跳过单个模型。仅评测最近一次运行中的该 spec，
// 并更新其标签和记分板。
async function gradeCard(spec){
  const R = compareState.results || {};
  if (!R[spec] || R[spec]._grading) return;
  R[spec]._grading = true; editing = false; render();
  try {
    const r = await postJSON("/api/compare/regrade",
      {spec, judge_model: compareState.judgeModel || "openai:gpt-5.6-sol"});
    compareState.history = r.runs || []; compareState.aggregate = r.aggregate || [];
    const row = ((r.runs || [])[0] || {}).results?.find(x => x.spec === spec);
    if (row && R[spec]) R[spec].quality = row.quality;
  } catch(e){ compareState.raceError = "grade failed: " + e; }
  if (R[spec]) R[spec]._grading = false;
  editing = false; render();
}
// 从记分板删除一场竞速（其模型退出累计统计），其他竞速保持不变；不同于会清空全部历史的“全部清除”。
async function deleteCompareRun(ts){
  if (!ts || !confirm("Delete just this run from the scoreboard? (Other races stay.)")) return;
  try {
    const r = await postJSON("/api/compare/delete_run", {ts});
    compareState.history = r.runs || []; compareState.aggregate = r.aggregate || [];
  } catch(e){ compareState.raceError = "delete failed: " + e; }
  editing = false; render();
}
// 仅清除竞速卡片（各模型列），不影响累计记分板/历史。便于在下一场竞速前恢复清爽界面。
function clearCards(){
  if (compareState.running) return;   // 不在竞速中途移除卡片。
  compareState.order = []; compareState.results = {}; compareState.raceError = null;
  saveCompare(); editing = false; render();
}
// 将存储的精简结果转换为 compareCol 所需结构（闸门对象、工具对象），使历史竞速与实时竞速一致渲染。
function adaptHistResult(r){
  return {...r, gate: r.gate ? {decision: r.gate} : null,
          tools: (r.tools || []).map(t => ({tool: t}))};
}
// 将一场历史竞速重新载入各列（该次运行的只读视图）。
function openCompareRun(idx){
  const run = (compareState.history || [])[idx];
  if (!run) return;
  compareState.order = run.results.map(r => r.spec);
  compareState.results = {}; run.results.forEach(r => { compareState.results[r.spec] = adaptHistResult(r); });
  compareState.message = run.message;
  render();
}

// 可选模型来自固定的候选列表（models.json）。默认全选，因此首次打开竞技场即比较全部候选；
// 可取消勾选以缩小范围。
function compareModels(d){
  const pinned = ((d.settings && d.settings.pinned) || []);
  if (compareState.picked === null){
    compareState.picked = new Set(pinned.map(p => `${p.provider}:${p.model}`));
  }
  return pinned;
}

function setCompareSort(key){
  compareState.sortBy = key;
  editing = false;   // 解除文本区域锁定，使重新排序后的重绘可见。
  render();
}
function toggleCompareModel(spec){
  const s = compareState.picked;
  s.has(spec) ? s.delete(spec) : s.add(spec);
  editing = false;   // 解除文本区域编辑锁，使保护逻辑不会跳过本次重绘。
  render();          // 否则数量和标签会保持过期。
}
// K3 评分开关：开启后由 kimi-k3 对每列回复评 0–10 分（每列额外一次 API 调用，因此默认按需启用）。
function toggleJudge(){
  compareState.judge = !compareState.judge;
  editing = false;
  render();
}
// 编程模式开关：为竞速注册 delegate_task 工具，使循环可将真实编程工作交给 pi 子代理
// （使用各卡片自身模型运行）。完整框架仍会运行（闸门、记忆和工具），delegate_task 仅是其中一个工具。
function toggleCoding(){
  compareState.coding = !compareState.coding;
  editing = false;
  render();
}
// 按需写入真实 Apple 日历（“Waku”日历）。默认关闭，避免一场竞速写入重复事件；开启后每个
// 参赛模型各写入一个事件。演示真实集成时应仅选择 1–2 个模型。
function toggleApple(){
  compareState.apple = !compareState.apple;
  editing = false;
  render();
}
// 质量评分模型。默认刻意不使用参赛模型——参赛者无法公平评判自身回合。gpt-5.6-sol 擅长文本评判，
// 却不适合作为工具调用参赛者，因此天然适合担任中立裁判。
const JUDGES = [
  {spec:"openai:gpt-5.6-sol",            label:"GPT-5.6 Sol"},
  {spec:"anthropic:claude-opus-4-8",     label:"Claude Opus 4.8"},
  {spec:"gemini:gemini-3.1-pro-preview", label:"Gemini 3.1 Pro"},
  {spec:"kimi:kimi-k3",                  label:"Kimi K3 (contestant)"},
];
function setJudgeModel(spec){ compareState.judgeModel = spec; editing = false; render(); }

// 通过 SSE 竞速，使模型完成的瞬间立即填充对应列；缓慢或失效的参赛者（例如无密钥供应商）
// 不会阻塞其他参赛者。结果按 spec 写入 compareState.results，网格按事件重绘。
async function runCompare(){
  const specs = [...compareState.picked];
  if (!compareState.message.trim() || !specs.length || compareState.running) return;
  editing = false;   // 解除输入锁定，使竞速/结果重绘可见。
  compareState.running = true;
  compareState.order = specs;      // 按选中顺序显示的列。
  compareState.results = {};       // spec -> 结果，按完成顺序填入。
  compareState.raceError = null;
  compareState.grading = null;      // 在竞速结束后的裁判评测期间设置。
  render();
  const R = compareState.results;
  try {
    const res = await fetch("/api/compare/stream", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({message: compareState.message, models: specs, judge: !!compareState.judge,
        judge_model: compareState.judgeModel || "openai:gpt-5.6-sol", coding: !!compareState.coding,
        apple: !!compareState.apple})});
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for(;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0){
        const line = buf.slice(0, i); buf = buf.slice(i+2);
        if (!line.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(line.slice(5).trim()); } catch(e){ continue; }
        const s = ev.spec;
        // 线束实时播放：开始 -> 门 -> 工具，然后是最终的
        // 结果与收据。 （我们不会对回复进行令牌流传输 - 请参阅
        // 原因是在dashboard.py中compare_stream。）
        if (ev.kind === "start"){ R[s] = {spec:s, provider:ev.provider, model:ev.model, streaming:true, tools:[], gate:null}; render(); }
        else if (ev.kind === "gate" && R[s]){ R[s].gate = {decision:ev.decision, reason:ev.reason}; render(); }
        else if (ev.kind === "tool" && R[s]){ (R[s].tools = R[s].tools||[]).push({tool:ev.tool}); render(); }
        // 子代理 (pi) 通过 delegate_task 流式传输自己的事件 —
        // 将它们累积到每张卡上，以便可以实时观看承包商的工作
        // 而不是返回摘要的黑匣子。
        else if (ev.kind === "subagent" && R[s]){
          const sub = R[s].sub = R[s].sub || {text:"", tools:[], tokens_in:0, tokens_out:0};
          if (ev.type === "text" && ev.delta) sub.text = (sub.text + ev.delta).slice(-4000);
          else if (ev.type === "tool") sub.tools.push(ev.tool);
          else if (ev.type === "turn_end"){ sub.tokens_in = ev.tokens_in||0; sub.tokens_out = ev.tokens_out||0; }
          render();
        }
        else if (ev.kind === "result" && s){ const sub = R[s] && R[s].sub; R[s] = ev; if (sub) R[s].sub = sub; saveCompare(); render(); }
        else if (ev.kind === "grading"){ compareState.grading = ev; render(); }   // 赛后裁判传票开始
        else if (ev.kind === "grade" && R[s]){ R[s].quality = ev.quality; if (compareState.grading) compareState.grading.done = (compareState.grading.done||0)+1; saveCompare(); render(); }
        else if (ev.kind === "done"){ compareState.grading = null; if (ev.error) compareState.raceError = ev.error; }
      }
    }
  } catch(e){ compareState.raceError = String(e); }
  compareState.running = false; saveCompare();
  // 服务器刚刚记录了这场比赛； loadCompareHistory 重新呈现
  // 新鲜（包括比赛）总计。没有中间的 render()，所以 live-folded
  // 行数无闪烁地传递给服务器总计。
  loadCompareHistory();
}

// 一位参赛者的专栏。当模型运行时（res.streaming）它会播放
// 像聊天码头一样生活 — 门牌、工具芯片亮起、回复类型为
// 插入符号。当它完成时（res.result），它会翻转到完整的收据卡。
// 重用共享格式化程序（renderMarkdown/secs/money）。
// 常见的、预期的故障模式的通俗易懂的原因——所以竞技场
// 在相机上诚实地读取（原始错误保持在下面，静音）。
function compareErrorReason(err){
  const e = (err || "").toLowerCase();
  if (e.includes("reasoning_effort") || e.includes("/v1/responses")) return "can't call tools — reasoning model, needs the /v1/responses API";
  if (e.includes("not a chat model") || e.includes("v1/completions")) return "not a chat model — needs the completions/responses API, not chat";
  if (e.includes("thought_signature")) return "can't call tools — missing thought_signature echo";
  if (e.includes("credit") || e.includes("permission-denied") || e.includes("license")) return "no credits/licenses on this provider";
  if (e.includes("max_tokens")) return "token-parameter mismatch";
  if (e.includes("not found") || e.includes("no longer available")) return "model id not available";
  return null;
}
// 模型名称旁边的“知道到2026-01”——每个大脑的知识截止点，
// 因此，对最近发生的事件做出错误的回答会被视为陈旧的知识，
// 能力不低（模型在截止后无法了解版本）。
// 服务器提供（dashboard.py 中的 MODEL_CUTOFF）；缺席 = 供应商未发布。
function cutoffTag(cutoff){
  return cutoff ? ` <span class="meta" style="font-size:11px;white-space:nowrap"
    title="knowledge cutoff — this model's world knowledge ends here; it cannot know releases after this date">knows to ${esc(cutoff)}</span>` : "";
}
// 子代理的收据：在该卡内工作的 pi。活=小
// 终端窗口（最后一行，清除了降价栅栏）。完成=一
// 点击后展开的安静摘要行 - 下面的回复已经显示了
// 最终代码，因此窗格不得重复。完整流：pi-transcript-events.jsonl。
function subPane(sub, live, spec){
  const seq = [];   // 折叠重复：write、bash、bash -> write · bash ×2
  for (const t of sub.tools||[]){
    const last = seq[seq.length-1];
    if (last && last.t === t) last.n++; else seq.push({t, n:1});
  }
  const toolStr = seq.map(x => x.n>1 ? `${x.t} ×${x.n}` : x.t).join(" · ");
  const tok = sub.tokens_in ? `${(sub.tokens_in/1000).toFixed(1)}k tok` : "";
  const clean = (sub.text||"").replace(/```[a-zA-Z]*\n?/g, "").trim();
  const tail = clean.split("\n").filter(Boolean).slice(-4).map(esc).join("\n");
  const head = `<span class="mm-prov">pi</span><span class="subterm-t">${esc(toolStr)||"…"}</span>` +
    (tok ? `<span class="meta" title="the sub-agent's tokens — billed to this card's cost">${tok}</span>` : "");
  if (live) return `<div class="subterm live"><div class="subterm-h">${head}<span class="live-dot"></span></div><pre>${tail||"spawning…"}</pre></div>`;
  // 记住重新渲染时的打开/关闭 — render() 每隔一段时间就会重建 DOM
  // 刷新勾选，否则会在读取过程中关闭展开的窗格
  return `<details class="subterm"${sub.open?" open":""} ontoggle="const r=compareState.results['${esc(spec)}']; if(r&&r.sub) r.sub.open=this.open">
    <summary class="subterm-h">${head}</summary><pre>${tail}</pre></details>`;
}
// 长回复是卡片的垂直占据（编码答案粘贴整个
// 程序）。使用淡入淡出 + 切换来钳制超过阈值；开放状态继续存在
// 卡的状态，因此刷新勾号不会将其关闭。
function replyBlock(res){
  const r = res.reply||"";
  // 高度来自行，而不是字符 — 20 条短代码行更高
  // 超过 400 个字符的段落
  const long = r.length > 400 || (r.match(/\n/g)||[]).length > 8;
  if (!long) return `<div class="r cmp-reply">${renderMarkdown(res.reply||"")}</div>`;
  return `<div class="r cmp-reply ${res.replyOpen?"":"clamped"}">${renderMarkdown(res.reply||"")}</div>
    <a class="reveal cmp-more" onclick="const r=compareState.results['${esc(res.spec)}']; if(r){r.replyOpen=!r.replyOpen; render();}">${res.replyOpen?"show less":"show full reply"}</a>`;
}
function compareCol(res){
  if (res.error){
    const why = compareErrorReason(res.error);
    return `<div class="cmp-col err"><div class="cmp-h"><span class="mm-prov">${esc(res.provider)}</span> <code>${esc(res.model)}</code>${cutoffTag(res.cutoff)}
      <span class="srcpill apple">error</span></div>
      ${why?`<div class="meta" style="color:var(--bad)"><b>${esc(why)}</b></div>`:""}
      <div class="meta" style="opacity:.7">${esc(res.error)}</div></div>`;
  }
  const tools = (res.tools||[]).map(t => t.tool === "delegate_task"
    ? `<span class="stage done subagent" title="the loop spawned a pi sub-agent on ${esc(res.model)} to write &amp; run the code">delegate_task → pi · ${esc(res.model)}</span>`
    : toolChip(t.tool)).join("");
  const gateBadgeHtml = `<span class="badge ${res.gate&&res.gate.decision==="retrieve"?"retrieve":""}">gate · ${esc(res.gate?res.gate.decision:"…")}</span>`;
  if (res.streaming){
    return `<div class="cmp-col">
      <div class="cmp-h"><span class="mm-prov">${esc(res.provider)}</span> <code>${esc(res.model)}</code>${cutoffTag(res.cutoff)}
        <span class="live-dot"></span></div>
      <div class="cmp-stats">${gateBadgeHtml}</div>
      ${tools?`<div class="stages" style="flex-wrap:wrap">${tools}</div>`:""}
      ${res.sub?subPane(res.sub, true):""}
      <div class="meta">${(res.tools||[]).length?"running tools…":"thinking…"} <span class="caret"></span></div>
    </div>`;
  }
  const c = res.completion;
  const completionBadge = c ? `<span class="cmp-score ${c.passed?"pass":"fail"}" title="${esc(c.why||"")}">${c.passed?"solved":"failed"}${c.passed?"":" · "+esc(c.why||"")}</span>` : "";
  const q = res.quality;
  const qualityBadge = q && q.score!=null ? `<span class="cmp-q ${q.score>=7?"hi":q.score>=4?"mid":"lo"}" title="graded ${q.score}/10 by ${esc(q.judge||"referee")} — ${esc(q.reason||"")}">${q.score}/10</span>` : "";
  // 每张卡评分按钮 - 如果裁判跳过了这张卡，则仅对该卡进行评分 (429)
  const gradeBtn = `<a class="reveal cmp-grade1" title="grade this card with the referee" onclick="gradeCard('${esc(res.spec)}')">${res._grading?"grading…":(q&&q.score!=null?"re-grade":"grade")}</a>`;
  return `<div class="cmp-col${c?(c.passed?" solved":" failed"):""}">
    <div class="cmp-h"><span class="mm-prov">${esc(res.provider)}</span> <code>${esc(res.model)}</code>${cutoffTag(res.cutoff)}${completionBadge}${qualityBadge}${gradeBtn}</div>
    <div class="cmp-stats">
      ${gateBadgeHtml}
      <span class="chip ${compareState.sortBy==="latency"?"sorted":""}">${secs(res.latency_ms)}</span>
      <span class="chip">${res.iterations??"?"} iter</span>
      <span class="chip ${compareState.sortBy==="cost"?"money":""}">${money(res.cost_usd||0)}</span>
      <span class="chip ${compareState.sortBy==="tokens"?"sorted":""}">${(res.tokens_in||0)+(res.tokens_out||0)} tok</span>
    </div>
    ${tools?`<div class="stages" style="flex-wrap:wrap">${tools}</div>`:""}
    ${res.sub?subPane(res.sub, false, res.spec):""}
    ${replyBlock(res)}
  </div>`;
}

// 竞技场举办两场比赛，它们是同一台机器，但配备不同
// 表盘：模型保持安全带不变并改变大脑；记忆保留
// 事实存在的地方，线束和大脑是恒定的和变化的。子选项卡
// 而不是两个侧边栏行，因此“模型”不会在导航中出现两次
// （一次作为比赛，一次作为配置）——与记忆保留的原因相同
// 选项卡背后的语义/情景/技能而不是四个侧边栏条目。
VIEWS.compare = function(d, sub){
  // 不再有子选项卡栏：侧边栏直接命名两个种族，并且一行
  // 标签重复突出显示的导航条目已经说过的家具。
  sub = sub === "memory" ? "memory" : "models";
  return sub === "memory" ? memoryArenaView() : modelArenaView(d);
};

function modelArenaView(d){
  const pinned = compareModels(d);
  const chips = pinned.length ? pinned.map(p => {
    const spec = `${p.provider}:${p.model}`, on = compareState.picked.has(spec);
    return `<label class="cmp-pick ${on?"on":""}"><input type="checkbox" ${on?"checked":""}
      onchange="toggleCompareModel('${esc(spec)}')"> <span class="mm-prov">${esc(p.provider)}</span> ${esc(p.model)}</label>`;
  }).join("") : `<div class="meta">No models pinned yet — star some in <a class="reveal" onclick="location.hash='#models'">Models</a>.</div>`;
  const n = compareState.picked ? compareState.picked.size : 0;

  // 每个比赛模型一列，按顺序排列。每个显示“赛车……”直到结果
  // 通过流到达，然后翻转到收据卡。
  let grid = "";
  const order = compareState.order || [];
  if (order.length){
    const results = compareState.results || {};
    // “done” = 成功完成（不流式传输，不出错）——仅限这些
    // 具有最快/最便宜的摘要的延迟/成本。
    const done = order.map(s => results[s]).filter(Boolean).filter(r => !r.error && !r.streaming);
    // 排序键：完成的卡片按所选指标升序排列（最小的是
    // 最优——最快、最便宜或 Token 最少）；获胜者显示在左上角。
    const metric = { latency: r => r.latency_ms || 0,
                     cost:    r => r.cost_usd || 0,
                     tokens:  r => (r.tokens_in || 0) + (r.tokens_out || 0) };
    const key = metric[compareState.sortBy] || metric.latency;
    const sorters = [["latency", "seconds"], ["tokens", "tokens"], ["cost", "money"]];
    // 排序选项卡右侧，卡片上方：“重新评分运行”重新运行裁判
    // 本次运行中的每个模型（下面的卡片）； “清晰的卡片”只是驳回
    // 列。两者都对当前运行起作用。
    const regradeBtn = (done.length && !compareState.running)
      ? `<a class="reveal" style="margin-left:auto;font-size:12px" title="Re-run the referee on every model in this run (fills a skipped/429'd grade, or re-scores)" onclick="regradeCompare()">${compareState.regrading?"re-grading…":"re-grade run"}</a>` : "";
    const clearBtn = (order.length && !compareState.running)
      ? `<a class="reveal" style="${regradeBtn?"":"margin-left:auto;"}font-size:12px" onclick="clearCards()">clear cards</a>` : "";
    // 突出的、类似选项卡的排序按钮 - 所选的按钮会突出显示。
    const sortBar = (done.length || clearBtn) ? `<div class="cmp-sortbar">${done.length
      ? `sort by ${sorters.map(([k, label]) => `<button class="cmp-sortbtn ${compareState.sortBy === k ? "on" : ""}" onclick="setCompareSort('${k}')">${label}</button>`).join("")}`
      : ""}${regradeBtn}${clearBtn}</div>` : "";
    // 比赛仍在进行时只有进度线；一旦每一列都是
    // 其中，排序选项卡+卡片+记分​​板说明了一切（没有多余的摘要）。
    const g = compareState.grading;
    const summary = done.length < order.length
      ? `Racing ${order.length} models — ${done.length}/${order.length} done`
      : (g ? `Referee ${esc(g.judge||"")} grading — ${g.done||0}/${g.n} scored` : "");
    // 首先对已完成的模型进行排名（根据所选指标），然后对仍在运行的模型进行排名，
    // 然后是错误——所以当比赛结束时，最好的会出现在左上角。
    const rank = s => {
      const r = results[s];
      if (!r) return [2, 0];                       // 未开始
      if (r.error) return [3, 0];                  // 失败->结束
      if (r.streaming) return [1, 0];              // 运行 -> 中间
      return [0, key(r)];                          // 完成 -> 前面，最佳指标优先
    };
    const shown = [...order].sort((a, b) => { const ra = rank(a), rb = rank(b); return ra[0] - rb[0] || ra[1] - rb[1]; });
    const cols = shown.map(s => {
      const r = results[s];
      if (r) return compareCol(r);
      return `<div class="cmp-col"><div class="cmp-h"><span class="mm-prov">${esc(s.split(":")[0])}</span> <code>${esc(s.split(":").slice(1).join(":"))}</code></div>
        <div class="meta">racing… <span class="caret"></span></div></div>`;
    }).join("");
    grid = `${summary ? `<div class="meta" style="margin:2px 0 6px">${summary}</div>` : ""}${sortBar}<div class="cmp-grid">${cols}</div>`
      + (compareState.raceError ? `<div class="meta" style="color:var(--bad)">${esc(compareState.raceError)}</div>` : "");
  }

  // 选项卡首次打开时加载历史记录一次（设置 [] 首先停止
  // 重新触发后刷新5s）； loadCompareHistory 落地时会重新渲染。
  if (compareState.history === undefined){ compareState.history = []; setTimeout(loadCompareHistory, 0); }

  return `<div class="card">
    <div class="cmp-controls">
      <span class="meta cmp-blurb">One message, every brain at once — same harness, isolated homes, real receipts (gate · latency · cost · tools). Compare, don't guess.</span>
      <label class="cmp-judge ${compareState.apple?"on":""}" style="margin-left:auto" title="Write create_event results to your REAL Apple Calendar (the 'Waku' calendar). Off by default so a race doesn't spam duplicates — when on, EACH model writes its own event (use 1-2 models).">
        <input type="checkbox" ${compareState.apple?"checked":""} onchange="toggleApple()"> write to calendar</label>
      <label class="cmp-judge ${compareState.coding?"on":""}" title="Coding task: enables the delegate_task tool so the loop can hand real coding work to a pi sub-agent on this card's own model — the full harness runs (gate, tools), delegate_task is one of them">
        <input type="checkbox" ${compareState.coding?"checked":""} onchange="toggleCoding()"> coding (pi)</label>
      <label class="cmp-judge ${compareState.judge?"on":""}" title="Grade each reply 0-10 for how well it serves the request (correctness, honesty, concision). One extra API call per column, by a referee that isn't racing.">
        <input type="checkbox" ${compareState.judge?"checked":""} onchange="toggleJudge()"> grade &mdash; referee
        <select onchange="setJudgeModel(this.value)" onclick="event.stopPropagation()" ${compareState.judge?"":"disabled"}>
          ${JUDGES.map(j=>`<option value="${j.spec}" ${(compareState.judgeModel||"openai:gpt-5.6-sol")===j.spec?"selected":""}>${esc(j.label)}</option>`).join("")}
        </select></label>
      <button class="save cmp-race" onclick="runCompare()" ${(!n||compareState.running)?"disabled":""}>
        ${compareState.running?"Racing…":`Race ${n} model${n===1?"":"s"}`}</button>
    </div>
    <textarea id="cmp-msg" class="cmp-input" rows="2" onfocus="markEditing()"
      oninput="compareState.message=this.value">${esc(compareState.message)}</textarea>
    <div class="cmp-picks">${chips}</div>
  </div>${grid}${compareHistoryHtml()}`;
}

// --- 内存竞技场 -----------------------------------------------------------
// 同样的四次测试，两条赛道。在存在跑步者之前，此选项卡显示
// 固定装置：每位参赛者被告知的内容以及随后被问到的内容。那是
// 故意不是占位符——一个你无法阅读其问题的基准
// 是你必须采取的信仰基准，而这个基准的全部要点是
// 这些数字是可以检查的。
let memoryArenaFixture;   // 未定义 = 未获取，null = 此处不可用
let maFile = null, maTrack = null;   // 选择的探针组； null = 加载的任何内容
let maModel = null;                  // “提供商：模型”； null = 最便宜的报价
function pickArenaModel(spec){ maModel = spec; render(); }
function maModels(){ return (memoryArenaFixture && memoryArenaFixture.models) || []; }
// 最便宜的首先从服务器开始，因此“不做选择”的成本最低。这
// arena 保持模型不变并且仅改变商店，因此昂贵
// 默认是不买任何东西：《寓言 5》中一场精确的晚餐比赛花费约为 4.36 美元
// （每 M 10 美元/50 美元）与 grok-4.3 上的 ~0.55 美元（1.25 美元/2.50 美元）相比，结果相同。
function maModelSpec(){ const m = maModels(); return maModel || (m[0] && m[0].spec) || ""; }

async function pickProbeFile(id){
  maFile = id;
  maTrack = (id.split("::")[1]) || null;   // id有自己的轨迹
  maPicked = null;
  memoryArenaFixture = undefined;   // 重新获取以使问题与选择匹配
  maRun = {running:false, rows:[], board:null, log:"", error:null};
  editing = false; render();
}
function pickTrack(name){ maTrack = name; editing = false; render(); }

async function loadMemoryArena(){
  try {
    const r = await fetch("/api/memory-arena" + (maFile ? `?probes=${encodeURIComponent(maFile)}` : ""));
    const j = await r.json();
    memoryArenaFixture = j && j.available ? j : null;
  } catch { memoryArenaFixture = null; }
  render();
}

const OUTCOME_HELP = [
  ["pass", "the expected answer is there"],
  ["stale", "the expected answer is missing and a SUPERSEDED one is asserted"],
  ["invented", "a refusal was correct and it answered anyway — the fact was never given"],
  ["miss", "the expected answer is missing and nothing wrong was asserted"],
];

function probeRow(p){
  const expect = p.expect_refusal
    ? `<span class="ma-expect">must decline</span>`
    : `<span class="ma-expect">${esc((p.expect_any||[]).join(" / "))}</span>`
      + ((p.expect_all||[]).length ? ` <span class="meta">+ ${esc(p.expect_all.join(", "))}</span>` : "");
  const forbid = (p.stale_any||[]).length
    ? `<span class="ma-forbid">${esc(p.stale_any.join(" / "))}</span>` : '<span class="meta">—</span>';
  return `<tr>
    <td><code>${esc(p.test)}</code></td>
    <td>${esc(p.question)}</td>
    <td>${expect}</td>
    <td>${forbid}</td>
    <td class="meta">${esc(p.note||"")}</td></tr>`;
}

// --- 运行它------------------------------------------------------------
let maRun = {running:false, rows:[], board:null, log:"", error:null};

async function seedMemoryArena(){ return runMemoryArena(true); }

async function runMemoryArena(seedOnly){
  if (maRun.running) return;
  const fx = memoryArenaFixture;
  const track = (maTrack && fx.tracks[maTrack]) ? maTrack : Object.keys(fx.tracks)[0];
  // 记录前面的网格。每个参赛者的种子轮数为五轮，因此
  // 第一个结果大约需要 40 秒——并且一个表仅由具有以下内容的行构建
  // landed 在整个第一分钟呈现一个空标题，读作
  // 破碎而不是忙碌。在我们开始之前，列和行都是已知的；
  // 只有单元格处于待处理状态。
  maRun = {running:true, rows:[], board:null, log:"telling…", error:null,
           backends: maPicks(), probes: fx.tracks[track].probes.map(p=>p.id),
           seedTotal: (fx.tracks[track].seed || []).length,
           seeded: {}, seedOnly: !!seedOnly};
  editing = false; render();
  try {
    const res = await fetch("/api/memory-arena/stream", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({backends: maPicks(), track, probes: maFile || "",
                            model: maModelSpec(), seed_only: !!seedOnly})});
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for(;;){
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      const chunks = buf.split("\n\n"); buf = chunks.pop();
      for (const c of chunks){
        if (!c.startsWith("data: ")) continue;
        const ev = JSON.parse(c.slice(6));
        if (ev.kind === "start"){ maRun.log = `${ev.contestant}: telling…`;
                                  maRun.seeded[ev.contestant] = 0; }
        // 在之前的一场比赛中已经讲过——没有什么可重复的，所以直接跳到
        // 询问而不是动画一个没有发生的有说服力的阶段。
        if (ev.kind === "cached"){ maRun.seeded[ev.contestant] = maRun.seedTotal;
                                   maRun.log = `${ev.contestant}: already told ${ev.facts}`; }
        if (ev.kind === "seed-done"){ maRun.log = `${ev.contestant}: ${
                                        ev.reused ? "already told" : "told"} ${ev.facts}`; }
        if (ev.kind === "seeded"){ maRun.log = `${ev.contestant}: ${ev.line}`;
                                   maRun.seeded[ev.contestant] = (maRun.seeded[ev.contestant]||0) + 1; }
        if (ev.kind === "probe"){ maRun.rows.push(ev); maRun.log = `${ev.contestant}: ${ev.probe}`; }
        if (ev.kind === "failed") maRun.error = `${ev.contestant} — ${ev.error}`;
        if (ev.kind === "done"){ maRun.board = ev.scoreboard || null;
                                 maRun.leaked = ev.leaked || [];
                                 if (ev.error) maRun.error = ev.error; }
        editing = false; render();
      }
    }
  } catch(e){ maRun.error = String(e); }
  maRun.running = false; maRun.log = ""; editing = false; render();
}

// 哪些商店要比赛——询问服务器，而不是在这里决定。
// 这曾经是一个硬编码的[“mem0”，“supabase”]，根据
// 连接列表，在 Zep 和 LangMem 存在之前编写。两人都是
// 配置好后，两者都会从每场比赛中默默掉落，并且按钮
// 兴高采烈地说道“Race 2 商店”，仿佛那就是整个赛场一样。一个清单
// 添加一种语言后，用两种语言维护的后端就会发生变化；
// waku/ops/memory_arena.py::_available_backends 现在是唯一的一个。
function maBackends(){
  return (memoryArenaFixture && memoryArenaFixture.backends) || ["sqlite"];
}

// 他们中的哪一个实际上在比赛。 Zep 开始 UNCHECKED：它等待图表
// 每次写入时的摄取，针对一个文件执行十次操作需要 16 分钟
// 真实账户。默认情况下，赛车将四分钟的实验变成了
// 四十分钟的一集让整个标签感觉破碎了。只需点击一下即可，
// 故意的，而不是一点击就可以避免。
const MA_NOTE = {control: "Told nothing, asked everything. It should fail every probe — one it passes is a probe the model can answer without memory."};
const MA_SLOW = {zep: "waits for graph ingestion — minutes per fact"};
let maPicked = null;

function maPickedSet(){
  if (maPicked === null) maPicked = new Set(maBackends().filter(k => !MA_SLOW[k]));
  return maPicked;
}
function maTogglePick(key){
  const s = maPickedSet();
  s.has(key) ? s.delete(key) : s.add(key);
  editing = false; render();
}
function maPicks(){
  return maBackends().filter(k => maPickedSet().has(k));
}


function maProbe(id){
  const tracks = (memoryArenaFixture && memoryArenaFixture.tracks) || {};
  for (const t of Object.values(tracks)){
    const hit = (t.probes || []).find(p => p.id === id);
    if (hit) return hit;
  }
  return null;
}

const OUTCOME_CELL = (r) => `<span class="ma-o ma-${r.outcome}" title="${esc(r.why||"")}">${r.outcome}</span>`;

function maResultsHtml(){
  if (!maRun.rows.length && !maRun.running && !maRun.error) return "";
  // 网格来自所问的内容，而不是来自已回答的内容 - 所以每个
  // 列和行从第一秒就出现在屏幕上，每个单元格都显示
  // 无论是播种、排队还是完成。 “运行”下的空表
  // 标题与损坏的标题没有什么区别。
  // 从正在比赛的赛道开始，在比赛开始时记录。曾经读过
  // Object.values(tracks)[0] — 始终是文件中的第一个轨道，无论哪个轨道
  // 你选的。在 6 个事实的商业赛道上与 8 个事实的晚餐赛跑
  // 轨道的总数使单元格停留在“告知 8 中的 6”，并且从未达到“询问”。
  const seedTotal = maRun.seedTotal || 0;
  const names = maRun.backends || [...new Set(maRun.rows.map(r => r.contestant))];
  const probes = maRun.probes || [...new Set(maRun.rows.map(r => r.probe))];
  // “首先”，因为讲述是针对每个参赛者的，而不是针对每个问题。重复
  // 每个探测行中的“告诉 8 中的 2”使其看起来像是所有四个问题
  // 我们已经同时被问到了——他们不是；参赛者有
  // 还没有被问到什么。
  const cell = (p, n, first) => {
    const r = maRun.rows.find(x => x.probe === p && x.contestant === n);
    if (r) return `<td>${OUTCOME_CELL(r)}
      <div class="ma-facts-meta">${(r.ms/1000).toFixed(1)}s &middot; ${r.tokens} tok${
        r.calls ? " in " + r.calls + (r.calls === 1 ? " call" : " calls") : ""} &middot; ${
        r.retrieved === true ? "searched memory" : r.retrieved === false ? "no lookup" : "gate unknown"}</div>
      <div class="ma-ans">${esc((r.answer||"").slice(0,140))}</div></td>`;
    if (!maRun.running) return `<td class="meta">—</td>`;
    const seeded = (maRun.seeded || {})[n];
    if (seeded === undefined) return `<td class="meta">queued</td>`;
    // “播种 4/8”读起来就像是观众尚未完成的进度条
    // 已显示。 “told 4 of 8”命名了实际事件：这家商店现已被
    // 讲述了即将接受质询的八个事实中的四个。
    if (seeded < seedTotal) return first
      ? `<td class="meta">being told ${seeded} of ${seedTotal}<span class="caret"></span></td>`
      : `<td class="meta"></td>`;
    return first ? `<td class="meta">asking<span class="caret"></span></td>`
                 : `<td class="meta">waiting</td>`;
  };
  const board = maRun.board ? `<div class="card" style="padding:4px 8px"><div class="tablescroll"><table>
      <tr><th>store</th><th>pass</th><th>stale</th><th>invented</th><th>miss</th><th>tokens</th></tr>
      ${maRun.board.map(b=>`<tr><td><code>${esc(b.contestant)}</code></td>
        <td>${b.pass}</td><td>${b.stale}</td><td>${b.invented}</td><td>${b.miss}</td>
        <td class="meta">${b.tokens}</td></tr>`).join("")}
    </table></div></div>` : "";
  return `<h2 style="margin-top:22px">Results${maRun.running?' <span class="meta" style="font-weight:400">— running…</span>':""}</h2>
    ${maRun.error?`<div class="card" style="color:var(--bad)">${esc(maRun.error)}</div>`:""}
    ${board}
    <div class="card" style="padding:4px 8px"><div class="tablescroll"><table>
      <tr><th>probe</th>${names.map(n=>`<th>${esc(n)}</th>`).join("")}</tr>
      ${probes.map((p, i)=>{
        const any = maRun.rows.find(x => x.probe === p);
        const fx = maProbe(p);
        const leaked = (maRun.leaked || []).includes(p);
        return `<tr>
          <td><span class="ma-test">${esc((any && any.test) || (fx && fx.test) || "")}</span>${
            leaked ? ' <span class="ma-o ma-invented" title="The no-memory control answered this correctly, so this question did not require the store in this run.">leaked</span>' : ""}
            <div class="ma-q">${esc((any && any.question) || (fx && fx.question) || p)}</div>
            <div class="ma-facts-meta">${fx ? (fx.expect_refusal
                ? "must decline" : "wants: " + esc((fx.expect_any||[]).join(" / "))) : ""}${
              fx && (fx.stale_any||[]).length ? " &middot; not: " + esc(fx.stale_any.join(" / ")) : ""}</div>
          </td>${names.map(n=>cell(p, n, i === 0)).join("")}</tr>`;}).join("")}
    </table></div></div>`;
}

async function maSeeAll(store){
  // 就地扩展此商店。永远不要导航——内存页面是 sqlite 的
  // 并向那里发送 mem0 的“查看全部”显示了错误的商店事实。
  const cards = Array.isArray(maStores) ? maStores : null;   // “正在加载”是一个字符串
  const i = cards ? cards.findIndex(c => c.store === store) : -1;
  if (i < 0) return;
  cards[i] = Object.assign({}, cards[i], {loading: true}); render();
  try {
    const r = await fetch(`/api/memory-arena/stores?store=${encodeURIComponent(store)}&${maStoreQuery()}`);
    const full = (await r.json())[0];
    if (full) cards[i] = full;
  } catch (e){ cards[i].error = String(e); }
  cards[i].loading = false; render();
}

function memoryArenaView(){
  if (memoryArenaFixture === undefined){
    setTimeout(loadMemoryArena, 0);
    return `<div class="card empty">loading…</div>`;
  }
  if (memoryArenaFixture === null){
    return `<div class="card empty">The probe file lives in <code>evals/memory_arena.json</code>,
      which a packaged install does not ship. Run Waku from a clone to see it.</div>`;
  }
  const track = memoryArenaFixture.tracks[(maTrack && memoryArenaFixture.tracks[maTrack])
    ? maTrack : Object.keys(memoryArenaFixture.tracks)[0]];
  const picks = maPicks();
  const chips = maBackends().map(k => {
    const on = maPickedSet().has(k);
    const tip = MA_SLOW[k] || MA_NOTE[k];
    return `<label class="cmp-pick ${on?"on":""}" ${tip?`title="${esc(tip)}"`:""}>
      <input type="checkbox" ${on?"checked":""} onchange="maTogglePick('${esc(k)}')"> ${esc(k)}${
      MA_SLOW[k] ? ' <span class="meta">slow</span>' : ""}${
      k === "control" ? ' <span class="meta">no memory</span>' : ""}</label>`;
  }).join("");
  // 一个下拉菜单。文件是一个容器，而不是一个选择——选择“哪个文件”
  // 然后“里面的哪一首曲目”让你回答一个问题两次，并且
  // 文件名告诉你的信息比曲目标签已经告诉你的要少。
  const sets = (memoryArenaFixture.sets || []);
  const chosen = maFile || memoryArenaFixture.chosen || (sets[0] && sets[0].id);
  // 三行，无散文。曾经坐在这里的每句话都已移入
  // 它所描述的控件上的 title= ：需要它的读者悬停，
  // 以及无法获得存储卡垂直空间的读者。
  // 在这一页上，空间是最稀缺的东西。
  const picker = `<div class="ma-race ma-pickers" style="margin-bottom:10px">
      <label class="fld" style="margin:0">Questions
        <select onchange="pickProbeFile(this.value)"
                title="Drop a JSON file in .waku/probes/ to add your own question sets.">
          ${sets.map(s=>`<option value="${esc(s.id)}" ${s.id===chosen?"selected":""}>${
            esc(s.label)} — ${s.facts} facts, ${s.probes} questions</option>`).join("")}
        </select></label>
      ${maModels().length ? `<label class="fld" style="margin:0">Model
        <select onchange="pickArenaModel(this.value)">
          ${maModels().map(m=>`<option value="${esc(m.spec)}" ${
            m.spec===maModelSpec()?"selected":""}>${esc(m.spec)} — $${m.price_in}/$${m.price_out} per M</option>`).join("")}
        </select></label>` : ""}
    </div>`;
  const race = `<div class="card">
    ${picker}
    <div class="cmp-picks" style="margin-bottom:10px">${chips}</div>
    <div class="ma-race">
      <button class="save ghost" onclick="seedMemoryArena()"
              title="Telling never changes, so it is its own button — do it once and ask as many times as you like."
              ${maRun.running||!picks.length?"disabled":""}>
        ${maRun.running && maRun.seedOnly ? "Telling…"
          : `Tell ${picks.length} store${picks.length===1?"":"s"}`}</button>
      <button class="save" onclick="runMemoryArena()"
              title="Asks the same questions of every store and scores the answers. Tells anything not yet told. Each store runs in its own copy; your real memory is never touched."
              ${maRun.running||!picks.length?"disabled":""}>
        ${maRun.running && !maRun.seedOnly ? "Asking…"
          : `Ask ${picks.length} store${picks.length===1?"":"s"}`}</button>
      ${maRun.running ? `<span class="meta">${esc(maRun.log)}</span>` : ""}
    </div></div>`;
  // 订单很重要，它曾经是错误的：比赛、结果、商店、询问。这
  // 问题是最后的，所以你可以开始一场比赛——并拍摄一场比赛——
  // 从未见过商店被告知或询问的内容。一个基准
  // 判决后提出的问题是要求您接受判决
  // 关于信任，这是此页面存在不做的一件事。
  //
  // 选择，然后看看他们会被告知和询问什么，然后是判决，然后是
  // 内容。商店卡故意排在最后：它们读取速度最慢，而且
  // 唯一需要按下按钮的部分。
  return race + maAsksHtml(track) + maResultsHtml() + maStoresHtml();
}

// --- 现在每家商店都在卖什么 ----------------------------------
// 这是选项卡从一开始就应该打开的屏幕：不是
// 关于基准的文章，而是您连接的每个商店的内容。
// 按需获取，而不是通过 5 秒投票获取。
let maStores;   // 未定义 = 未获取，[] = 已获取且为空

// 卡片应该描述哪些种子。没有这个服务器就没有办法
// 知道要打开哪个 .waku-arena 主页，并返回到现场代理的
// store——这正是该小组用来道歉的无与伦比的卡片
// 在其上方的段落中。
// 选择器使用相同的后备链。 maFile 为空，直到您更改
// 下拉列表，因此读取原始数据会发送“”作为默认设置 - 以及服务器，
// 如果没有探针集，则既不能命名主目录也不能命名分区。读会
// 悄悄地回到现场商店，Clean 会拒绝。该错误会
// 只对那些从未接触过下拉菜单的人出现，这是他们中的大多数。
function maChosenSet(){
  const fx = memoryArenaFixture || {};
  const sets = fx.sets || [];
  return maFile || fx.chosen || (sets[0] && sets[0].id) || "";
}

function maStoreQuery(){
  return `probes=${encodeURIComponent(maChosenSet())}&model=${encodeURIComponent(maModelSpec())}`;
}

let maClean = "";   // 最后一次清理做了什么，显示了提示通常所在的位置

// 故意不在确认对话框后面：它只能到达本场比赛自己的
// 划痕。这个按钮的危险版本是存在的
// 在按比赛分区之前，当“商店”包括现场代理的商店时。
async function cleanMemoryStores(){
  maClean = "cleaning…"; editing = false; render();
  try {
    const r = await fetch("/api/memory-arena/clean", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({probes: maChosenSet(), model: maModelSpec()})});
    const out = await r.json();
    maClean = out.error ? `clean failed — ${out.error}`
      : `cleaned ${(out.removed||[]).length} — ${(out.removed||[]).join(", ") || "nothing to remove"}`
        + ((out.errors||[]).length ? ` &middot; ${out.errors.length} failed` : "");
    maStores = undefined;              // 屏幕上的卡片现在描述已删除的数据
  } catch(e){ maClean = `clean failed — ${e}`; }
  editing = false; render();
}

async function loadMemoryStores(){
  maStores = "loading";
  editing = false; render();
  try { maStores = await (await fetch(`/api/memory-arena/stores?${maStoreQuery()}`)).json(); }
  catch(e){ maStores = [{store:"?", error:String(e)}]; }
  editing = false; render();
}

function maStoresHtml(){
  const btn = `<button class="save ghost" onclick="loadMemoryStores()"
    ${maStores === "loading" ? "disabled" : ""}>${maStores === "loading" ? "reading…" : "Read stores"}</button>`;
  // 标题带有按钮。这曾经是一整张卡片包装的
  // 控件和一个段落 — 相当于一张卡片的垂直空间来表示“按
  // 这个”。在有用内容是五张存储卡的页面上
  // 下来，那是银幕上最贵的家具。
  const head = `<h2 class="ma-head">What each store is holding ${btn}
    <button class="save ghost" onclick="cleanMemoryStores()"
      title="Deletes only what this race wrote: its .waku-arena copies and its waku-arena partition. Your own memory and the waku partition are never named, so they cannot be reached.">Clean</button>
    <span class="meta">${maClean || "what each made of the SAME facts &middot; live call, on demand"}</span></h2>`;
  if (!Array.isArray(maStores)) return head;
  const cards = maStores.map(s => `<div class="card ma-store">
      <div class="ma-store-h"><code>${esc(s.store)}</code>
        ${s.error ? `<span class="ma-o ma-invented">error</span>`
                  : `<span class="meta">${s.count} fact${s.count===1?"":"s"}</span>`}</div>
      <div class="ma-prov">${s.kind === "arena"
        ? `this race's own copy &middot; <code>.waku-arena/</code>`
        : s.kind === "live"
        ? `your live agent &middot; <code>.waku/state.db</code>`
        : s.kind === "control"
        ? `not a store &middot; the integrity check`
        : `connected account &middot; only what waku wrote`}${
        s.span ? ` &middot; ${esc(s.span)}` : ""}</div>
      ${s.error ? `<div class="ma-ans">${esc(s.error)}</div>`
        : s.note ? `<div class="ma-ans">${esc(s.note)}</div>`
        : (s.facts||[]).length
          ? `<ul class="ma-facts">${s.facts.map(f=>`<li>${f.subject
              ? `<b>${esc(f.subject)}</b> — ` : ""}${esc(f.content)}</li>`).join("")}</ul>${
              s.count > s.facts.length
                ? `<div class="meta" style="margin-top:6px">showing ${s.facts.length} of ${s.count}
                     &middot; <a class="reveal" onclick="maSeeAll('${esc(s.store)}')">see all</a></div>`
                : ""}`
          : `<div class="meta">empty</div>`}
    </div>`).join("");
  // 这曾经带有一个警告，即计数不是比较 -
  // 因为 sqlite 是实时代理，实际使用了数周，坐在旁边
  // 只运行过一项基准测试的商店。警告是正确的
  // 并且设计是错误的：基准测试下的面板不应该需要
  // 解释为什么第一张牌不算数的段落。
  //
  // sqlite 现在读取比赛自己的副本，因此每张卡都描述相同
  // 播种和比较是真实的。剩下要说的就是一件事
  // 仍然值得一提的是——这是现场阅读，而且需要往返。
  return head + `<div class="ma-stores">${cards}</div>`;
}

// --- 他们被问到的问题----------------------------------------------------
// 狠狠地修剪了一下。每个探测器的完整注释各为三行散文，
// 埋葬了任何人真正想要的东西：问题，以及什么算作
// 正确的。将鼠标悬停一行以进行推理。
function maAsksHtml(track){
  return `<h2 style="margin-top:22px">What they get asked
      <span class="meta" style="font-weight:400">— ${track.seed.length} facts in, ${track.probes.length} questions</span></h2>
    ${/* ONE table, two sections. They were two cards, which read as two
          unrelated lists — and they are the opposite of unrelated: the second
          only means anything BECAUSE of the first. A section row inside a
          single table says "same subject, two halves" in a way two cards with
          a gap between them cannot, and it saves a card's worth of height on
          a page where that is the scarce resource. */""}
    <div class="card" style="padding:4px 8px"><div class="tablescroll"><table>
      <tr><th>told</th><th></th><th></th></tr>
      ${track.seed.map(s=>`<tr><td class="meta" colspan="3">${esc(s)}</td></tr>`).join("")}
      <tr><th>then asked</th><th>right answer</th><th>wrong answer</th></tr>
      ${track.probes.map(p=>`<tr title="${esc(p.note||"")}">
        <td>${esc(p.question)}</td>
        <td><span class="ma-expect">${p.expect_refusal ? "must decline"
            : esc((p.expect_any||[]).join(" / "))}</span></td>
        <td>${(p.stale_any||[]).length ? `<span class="ma-forbid">${esc(p.stale_any.join(" / "))}</span>`
            : p.expect_refusal ? `<span class="ma-forbid">any specific answer</span>`
            : '<span class="meta">—</span>'}</td></tr>`).join("")}
    </table></div></div>
    <div class="meta" style="margin-top:8px">${memoryArenaFixture.is_example
      ? `Example probes. Point <code>WAKU_MEMORY_PROBES</code> at your own file.`
      : `From <code>${esc(memoryArenaFixture.source)}</code>`}
      &nbsp;·&nbsp; <span class="ma-o ma-pass">pass</span> right
      <span class="ma-o ma-stale">stale</span> gave a superseded answer
      <span class="ma-o ma-invented">invented</span> made it up
      <span class="ma-o ma-miss">miss</span> said it did not know</div>`;
}


// 当前比赛下的累积视图：每个模型的平均记分牌
// 在每场记录的比赛中，然后是最近比赛的列表（单击以重新打开）。
// 数据来自 GET /api/compare/history （arena 自己的 JSONL，绝不是
// 代理的真实状态）。
// 棋盘显示的记分牌 = 服务器的总数（已完成的比赛）加上，
// 当比赛仍在进行时，其已经完成的柱子折叠起来——所以
// 模型的数字在 ITS 列完成时立即着陆，而不是等待
// 比赛中最慢的车型。不重复计算：跑步比赛不属于
// 服务器总计尚未完成，一旦完成 running=false，我们就停止折叠它。
function boardAggregate(){
  const map = {};
  (compareState.aggregate || []).forEach(a => { map[a.spec] = {...a}; });
  if (compareState.running){
    (compareState.order || []).forEach(spec => {
      const r = (compareState.results || {})[spec];
      if (!r || r.streaming) return;   // 专栏尚未完成
      const a = map[spec] || (map[spec] = {spec, provider: r.provider, model: r.model,
        cutoff: r.cutoff, runs: 0, ok: 0, total_latency_ms: 0, total_tokens_in: 0, total_tokens_out: 0,
        total_tokens: 0, total_cost_usd: 0, cases_passed: 0, cases_scored: 0,
        _qsum: 0, quality_n: 0, quality_avg: null});
      a.runs += 1;
      if (!r.error){
        a.ok += 1;
        a.total_latency_ms += r.latency_ms || 0;
        a.total_tokens_in += r.tokens_in || 0;
        a.total_tokens_out += r.tokens_out || 0;
        a.total_tokens = a.total_tokens_in + a.total_tokens_out;
        a.total_cost_usd = Math.round((a.total_cost_usd + (r.cost_usd || 0)) * 10000) / 10000;
      }
      if (r.completion){ a.cases_scored += 1; a.cases_passed += r.completion.passed ? 1 : 0; }
      if (r.quality && r.quality.score!=null){
        // 根据第一次折叠时服务器行的平均值重建运行总和
        if (a._qsum===undefined){ a._qsum = (a.quality_avg||0) * (a.quality_n||0); }
        a._qsum += r.quality.score; a.quality_n = (a.quality_n||0) + 1;
        a.quality_avg = Math.round(a._qsum / a.quality_n * 10) / 10;
      }
    });
  }
  return Object.values(map);
}
// 散点悬停区域的小样式工具提示（即时+可靠，
// 与原生 SVG <title> 不同）。一个元素，重复使用；跟随光标。
function _scTip(){
  let el = document.getElementById("sc-tip");
  if (!el){ el = document.createElement("div"); el.id = "sc-tip"; el.className = "sc-tip"; document.body.appendChild(el); }
  return el;
}
function showScatterTip(e){ const el = _scTip(); el.textContent = e.currentTarget.getAttribute("data-tip") || ""; el.style.display = "block"; moveScatterTip(e); }
function moveScatterTip(e){ const el = _scTip(); el.style.left = (e.clientX + 14) + "px"; el.style.top = (e.clientY + 12) + "px"; }
function hideScatterTip(){ const el = document.getElementById("sc-tip"); if (el) el.style.display = "none"; }
// 揭示：总成本 (x) 与好坏程度 (y)。 Y 是 K3 的成绩，
// 否则就是完成通过率——所以“便宜又好”位于左上角。这是
// 想象整个竞技场都是为了绘画而建造的（“opus 20 倍的价格是 20 倍更好吗？”）。
function costQualityScatter(agg){
  const useQ = agg.some(a => a.quality_avg != null);
  const pts = agg.map(a => {
    let y = null;
    if (useQ && a.quality_avg != null) y = a.quality_avg;                       // 0-10
    else if (!useQ && a.cases_scored) y = a.cases_passed / a.cases_scored * 10; // 0-10
    return {a, x: a.total_cost_usd || 0, y};
  }).filter(p => p.y != null && p.x > 0);
  if (pts.length < 2) return "";
  const W = 640, H = 300, L = 46, R = 150, T = 16, B = 36;
  const xmax = Math.max(...pts.map(p => p.x)) * 1.08 || 1;
  const px = x => L + (x / xmax) * (W - L - R);
  const py = y => H - B - (y / 10) * (H - T - B);
  const gr = [0,2,4,6,8,10].map(v => `<line x1="${L}" y1="${py(v)}" x2="${W-R}" y2="${py(v)}" class="sc-grid"/>
    <text x="${L-6}" y="${py(v)+3}" class="sc-tick" text-anchor="end">${v}</text>`).join("");
  const dots = pts.sort((a,b)=>a.x-b.x).map(p => {
    const good = p.y >= 7, mid = p.y >= 4;
    const cls = good ? "hi" : mid ? "mid" : "lo";
    return `<circle cx="${px(p.x).toFixed(1)}" cy="${py(p.y).toFixed(1)}" r="5" class="sc-dot ${cls}"/>
      <text x="${(px(p.x)+9).toFixed(1)}" y="${(py(p.y)+3).toFixed(1)}" class="sc-lbl">${esc(p.a.model)} · ${money(p.x)}</text>`;
  }).join("");
  // 将鼠标悬停在 y 轴标签上以读取条件（原生 SVG <title> 工具提示）。
  const yCriteria = useQ
    ? "Referee grade — 0-10, scored by a model that isn't racing, given the tools that actually fired:\n"
      + "9-10  fully addresses the request — correct, concise, honest\n"
      + "5-8   mostly there — minor gaps, padding, or small errors\n"
      + "1-4   partial, vague, or partly wrong\n"
      + "0     ignores it, or claims an action it didn't take"
    : "Completion — fraction of the task's checklist met (right tool, right args, enough calls). Deterministic, no judge.";
  const yLabel = useQ ? "referee grade" : "completion";
  return `<div class="card" style="padding:12px 14px;margin-top:14px">
    <div class="meta" style="margin-bottom:4px">Cost vs ${useQ?"quality (referee grade)":"completion"} — cheap &amp; good is top-left</div>
    <svg viewBox="0 0 ${W} ${H}" class="scatter" preserveAspectRatio="xMidYMid meet">
      <line x1="${L}" y1="${T}" x2="${L}" y2="${H-B}" class="sc-axis"/>
      <line x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}" class="sc-axis"/>
      ${gr}${dots}
      <text x="${(L+(W-R-L)/2).toFixed(0)}" y="${H-6}" class="sc-tick" text-anchor="middle">total cost →</text>
      <text x="14" y="${(T+(H-B-T)/2).toFixed(0)}" class="sc-tick sc-ylabel" text-anchor="middle" transform="rotate(-90 14 ${(T+(H-B-T)/2).toFixed(0)})">${yLabel} →</text>
      <rect class="sc-yhit" x="0" y="${T}" width="26" height="${H-B-T}" data-tip="${esc(yCriteria)}"
        onmouseenter="showScatterTip(event)" onmousemove="moveScatterTip(event)" onmouseleave="hideScatterTip()"/>
    </svg></div>`;
}
function compareHistoryHtml(){
  const agg = boardAggregate();
  const hist = compareState.history || [];
  const raceCount = hist.length + (compareState.running ? 1 : 0);
  if (!agg.length && !hist.length) return "";
  // 所有比赛的累计总成绩。单击列标题可按其排序 —
  // 先升序，再次单击可翻转（箭头显示活动列+目录）。
  const bs = compareState.boardSort || (compareState.boardSort = {key: "total_cost_usd", dir: "asc"});
  const arrow = k => bs.key === k ? (bs.dir === "asc" ? " ▲" : " ▼") : "";
  const th = (k, label) => `<th class="cmp-th ${bs.key===k?"on":""}" onclick="setBoardSort('${k}')">${label}${arrow(k)}</th>`;
  const rows = [...agg].sort((x, y) => ((x[bs.key] ?? 0) - (y[bs.key] ?? 0)) * (bs.dir === "asc" ? 1 : -1));
  const scoreboard = agg.length ? `
    <h2 style="margin-top:22px;display:flex;align-items:center;gap:10px">Scoreboard
      <span class="meta" style="font-weight:400">— totals across ${raceCount} race${raceCount===1?"":"s"}</span>
      <a class="reveal" style="margin-left:auto;font-size:12px" onclick="clearCompareHistory()">clear all</a></h2>
    ${costQualityScatter(agg)}
    <div class="card" style="padding:4px 8px"><div class="tablescroll"><table>
      <tr><th>model</th><th title="knowledge cutoff — when each model's world knowledge ends; it cannot know releases after this date">cutoff</th>${th("cases_passed","solved")}<th class="cmp-th ${bs.key==="quality_avg"?"on":""}" onclick="setBoardSort('quality_avg')" title="referee's mean 0-10 grade on the replies (correctness, honesty, concision) — referee is not a racing model">grade${arrow("quality_avg")}</th>${th("runs","races")}<th>ok</th>${th("total_latency_ms","total time")}${th("total_tokens_in","in tok")}${th("total_tokens_out","out tok")}${th("total_tokens","total tok")}<th title="list price per million tokens, input / output">rate $/M</th>${th("total_cost_usd","total cost")}</tr>
      ${rows.map(a=>`<tr>
        <td><span class="mm-prov">${esc(a.provider)}</span> <code>${esc(a.model)}</code></td>
        <td class="meta">${a.cutoff?esc(a.cutoff):"—"}</td>
        <td>${a.cases_scored?`<span class="cmp-score ${a.cases_passed===a.cases_scored?"pass":(a.cases_passed?"part":"fail")}">${a.cases_passed}/${a.cases_scored}</span>`:'<span class="meta">—</span>'}</td>
        <td>${a.quality_avg!=null?`<span class="cmp-q ${a.quality_avg>=7?"hi":a.quality_avg>=4?"mid":"lo"}">${a.quality_avg}</span>`:'<span class="meta">—</span>'}</td>
        <td class="meta">${a.runs}</td><td class="meta">${a.ok}/${a.runs}</td>
        <td class="meta">${secs(a.total_latency_ms)}</td>
        <td class="meta">${a.total_tokens_in}</td><td class="meta">${a.total_tokens_out}</td>
        <td class="meta">${a.total_tokens}</td>
        <td class="meta">${a.rate_in!=null?`$${a.rate_in}/$${a.rate_out}`:"—"}</td>
        <td class="meta" style="color:var(--good)">${money(a.total_cost_usd)}</td></tr>`).join("")}
    </table></div></div>` : "";
  const recent = hist.length ? `
    <h2 style="margin-top:18px">Recent races <span class="meta" style="font-weight:400">— click to reopen</span></h2>
    <div class="card">${hist.map((run,i)=>`
      <div class="pinrow" style="cursor:pointer" onclick="openCompareRun(${i})">
        <code style="flex:1;word-break:break-all">${esc((run.message||"").slice(0,90))}</code>
        <span class="meta" style="white-space:nowrap">${(run.results||[]).length} models · ${esc((run.ts||"").slice(0,16).replace("T"," "))}</span>
        <a class="reveal del" style="margin-left:8px;font-size:14px" title="delete just this run" onclick="event.stopPropagation(); deleteCompareRun('${esc(run.ts||"")}')">×</a>
      </div>`).join("")}</div>` : "";
  return scoreboard + recent;
}
