// Waku 仪表盘——子标签/数据库辅助函数、SQL 控制台、记忆/工具子视图及 VIEWS。
// 从 app.js 拆分而来：经典 <script> 标签，共享全局作用域（无构建步骤、无模块）。
// 加载顺序和约定见 static/README.md。

// --- 子标签：通过哈希路由子标签（#memory/semantic、#database/facts）拆分长页面，
// 保持页面简洁。每个标签都是普通链接，因此可收藏，架构卡片也能直接深链到对应标签。
function subtabBar(view, tabs, active){
  return `<div class="subtabs">${tabs.map(([key,label,n]) =>
    `<a class="subtab ${key===active?"on":""}" href="#${view}/${key}">${esc(label)}${
      n!=null?`<span class="n">${n}</span>`:""}</a>`).join("")}</div>`;
}

// 可滚动的原始 SQLite 表。列名作为靛蓝色粘性表头，使架构信息与数据列对齐，
// 而非悬浮在数据上方。
function dbTable(t){
  if (!t.sample.length) return `<div class="card empty">暂无数据</div>`;
  const head = t.columns.map(c => `<th class="dbcol">${esc(c)}${
    t.types&&t.types[c]?`<small>${esc(t.types[c].toLowerCase())}</small>`:""}</th>`).join("");
  const body = t.sample.map(r => `<tr>${t.columns.map(c =>
    `<td class="dbcell">${esc(String(r[c]??"").slice(0,120))}</td>`).join("")}</tr>`).join("");
  return `<div class="scrolly"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="meta" style="margin-top:6px">显示 ${t.count} 行中的 ${t.sample.length} 行（最新在前）</div>`;
}
const DB_DESC = {
  calendar_events: "create_event 工具写入的事件（核心任务）",
  facts: "语义记忆——持久事实（记忆 ▸ 语义）",
  episodes: "情景记忆——带日期的摘要（记忆 ▸ 情景）",
  chat_log: "每条消息均带有 session_id 标签；巩固流程从此读取",
};
const QUERY_EXAMPLES = [
  "SELECT role, content FROM chat_log ORDER BY id DESC LIMIT 10",
  "SELECT subject, content FROM facts",
  "SELECT session_id, COUNT(*) FROM chat_log GROUP BY session_id",
];
function dbQueryView(){
  return `<div class="meta" style="margin-bottom:10px"><code>state.db</code> 的只读 SQL 控制台。
      它借鉴了 Supabase 编辑器的理念，但范围更小。仅允许执行 <code>SELECT</code>；文件以只读方式打开，
      因此这里的操作不会更改你的数据。</div>
    <textarea class="sqlbox" id="sqlbox" spellcheck="false" onfocus="markEditing()" oninput="markEditing()">${esc(QUERY_EXAMPLES[0])}</textarea>
    <div style="margin:8px 0"><button class="save" onclick="runQuery()">执行</button>
      <span class="meta" style="margin-left:12px">示例：${QUERY_EXAMPLES.map(q=>`<span class="qexample" onclick="qFill(this.textContent)">${esc(q)}</span>`).join(" &nbsp; ")}</span></div>
    <div id="qout"></div>`;
}

// --- 只读 SQL 控制台（“类似 Supabase 的简易查询编辑器”）。
function qFill(sql){ const b=document.getElementById("sqlbox"); if(b){ b.value=sql; runQuery(); } }
async function runQuery(){
  editing = true;   // keep the 5s refresh from wiping the query + results
  const sql = (document.getElementById("sqlbox")||{}).value || "";
  const out = document.getElementById("qout");
  out.innerHTML = `<div class="meta">正在执行…</div>`;
  const r = await postJSON("/api/query", {sql});
  if (r.error){ out.innerHTML = `<div class="card empty" style="color:var(--bad)">${esc(r.error)}</div>`; return; }
  if (!r.rows.length){ out.innerHTML = `<div class="card empty">0 行</div>`; return; }
  out.innerHTML = `<div class="scrolly"><table><thead><tr>${
    r.columns.map(c=>`<th class="dbcol">${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    r.rows.map(row=>`<tr>${row.map(v=>`<td class="dbcell">${esc(String(v).slice(0,120))}</td>`).join("")}</tr>`).join("")
    }</tbody></table></div><div class="meta" style="margin-top:6px">${r.rows.length} 行</div>`;
}

// --- 记忆子标签。记忆以友好的、按支柱划分的视图展示持久化内容；数据标签页则以原始
// SQLite 表展示完全相同的行（详见说明）。
function memOverview(d){
  const s = d.stats;
  const pillars = [
    ["语义","semantic",d.facts.length+" 条事实","关于你和相关人员的精炼持久事实"],
    ["情景","episodic",d.episodes.length+" 条情景记录","每次巩固生成一条带日期的摘要，刻意保持精简"],
    ["程序性","skills",d.skills.length+" 项技能","仅在相关时加载的 SKILL.md 文件，定义如何行动"],
  ].map(([t,sub,n,desc]) => `<div class="box" style="min-width:0" onclick="location.hash='memory/${sub}'">
      <b>${t} <span class="meta" style="font-weight:400">· ${n}</span></b><span>${desc}</span></div>`).join("");
  return `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>记忆与数据库：同一文件的两种视图。</b>
      <div class="r">此标签页按支柱呈现 Waku 记住的内容；
      <a class="reveal" onclick="location.hash='database'">数据库标签页</a>则以原始 SQLite 表（以及 FTS5 关键字索引）展示完全相同的数据。
      同一个 <code>.waku/state.db</code>，不同的观察层级。
      <br><br>部分助手（如 Hermes）将记忆保存在单个 <code>MEMORY.md</code> 文件中。Waku 将可查询的数据源保存在
      <code>state.db</code> 中（事实和情景记录，支持 FTS5 搜索），并在每轮结束后写入一份供人阅读的
      ${reveal("MEMORY.md","MEMORY.md")} 镜像文件。因此你同时拥有可直接打开的文件和可靠的数据库支撑。</div></div>
    <h2>三类记忆支柱</h2>
    <div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">${pillars}</div>
    <h2>检索闸门——本轮是否真的需要记忆？</h2>${gateSplit(s)}
    <div class="meta" style="margin-top:8px">在进行任何查找前，一个低成本模型先判断本轮是否需要记忆——
      这是记忆<i>检索</i>的关键决策。（运维标签页会将相同的跳过/检索数据作为运行指标展示；决策本身属于记忆系统。）</div>
    <div class="meta" style="margin-top:14px">Files: ${reveal("state.db","state.db")} · ${reveal("MEMORY.md","MEMORY.md")} · ${reveal("SOUL.md","SOUL.md")} · ${reveal("skills","skills/")}</div>`;
}
function memSemantic(d){
  let h = `<div class="meta" style="margin-bottom:12px">从你告诉 Waku 的内容中提炼出的持久事实——
    这是最精简、复用率最高的存储。你可以编辑或遗忘任意一项，改动会在下一轮生效。</div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>主题</th><th>事实</th><th>来源</th><th></th></tr>${
    d.facts.map(f => `<tr id="fact-${f.id}">
      <td><code>${esc(f.subject)}</code></td>
      <td class="fc">${esc(f.content)}</td>
      <td class="meta">${esc(f.source)}</td>
      <td style="white-space:nowrap"><a class="reveal" onclick="editFact(${f.id})">编辑</a> · <a class="reveal del" onclick="delMem('delete_fact',${f.id})">删除</a></td>
    </tr>`).join("")}</table></div>`;
  return h;
}
function memEpisodic(d){
  const src = d.episodes_source || "sqlite";
  let h = `<div class="meta" style="margin-bottom:8px">后端：<span class="srcpill">${esc(src)}</span></div>`;
  if (d.episodes_error) h += `<div class="card empty">无法从 Notion 读取情景记录：${esc(d.episodes_error)}</div>`;
  h += `<div class="card" style="background:var(--accent-soft);border-color:var(--line2)">
    <b>为什么它如此精简？</b> <span class="r">情景记忆每次巩固只保存一条<i>提炼后</i>的摘要，
    而不是保存每一条消息。完整的原始对话位于数据库标签页的
    <a class="reveal" onclick="location.hash='database/chat_log'"><code>chat_log</code> 表</a>
    （更大的那一份）中；情景记录只是其中的要点。</span></div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>日期</th><th>情景记录</th><th></th></tr>${
    d.episodes.map(e => `<tr><td class="meta">${esc(e.happened_at)}</td><td>${esc(e.summary)}</td>
      <td><a class="reveal del" onclick="delMem('delete_episode','${e.id}')">删除</a></td></tr>`).join("")}</table></div>`;
  return h;
}
function memSkills(d){
  let h = `<div class="meta" style="margin-bottom:12px">程序性记忆——仅在消息匹配时加载的 Markdown 指令。
    你可以通过三种方式添加自己的指令：在对话中教 Waku（它会调用 <code>create_skill</code>）、
    编辑下方技能，或将 <code>SKILL.md</code> 放入 ${reveal("skills","技能文件夹")}。</div>`;
  h += d.skills.map((sk,i) => {
    const full = `---
name: ${sk.name}
description: ${sk.description}
---

${sk.body}`;
    return `<div class="card">
      <div class="u"><code>${esc(sk.name)}</code> <span class="meta" style="font-weight:400">· ${esc(sk.description)}</span>
        <span class="srcpill ${sk.editable?"":"apple"}" style="margin-left:6px">${sk.editable?"本地":"内置"}</span></div>
      <textarea class="editor" id="sk-${i}" style="min-height:150px;margin-top:8px" data-path="${esc(sk.path)}"
        oninput="dirty('sksave-${i}')" onfocus="markEditing()">${esc(full)}</textarea>
      <div style="margin-top:8px"><button class="save" id="sksave-${i}" disabled onclick="saveSkill(${i})">保存 SKILL.md</button>
        <span class="meta" id="skmsg-${i}" style="margin-left:10px">${esc(sk.rel)}</span></div></div>`;
  }).join("") || `<div class="card empty">未加载任何技能</div>`;
  return h;
}
function memSoul(d){
  return `<div class="meta" style="margin-bottom:12px">SOUL.md 是 Waku 的人格设定——每轮都会加载的系统提示词。
    编辑它会改变你的 Waku 是什么样的助手，改动会在下一轮生效。</div>
    <div class="card"><textarea id="soul" class="editor" style="min-height:260px"
      oninput="dirty('soul-save')" onfocus="markEditing()">${esc(d.soul||"")}</textarea>
    <div style="margin-top:8px"><button class="save" id="soul-save" disabled onclick="saveSoul()">保存 SOUL.md</button>
      <span class="meta" id="soul-msg" style="margin-left:10px"></span></div></div>
    <div class="meta" style="margin-top:10px">${reveal("SOUL.md","在编辑器中打开 SOUL.md")}</div>`;
}
function memConsolidation(d){
  const distilled = d.facts.filter(f => f.source==="consolidation");
  let h = `<div class="card"><b>工作原理。</b> <span class="r">每进行 ${d.consolidate_every} 轮交流，
    一个低成本模型会读取尚未巩固的 ${"<code>chat_log</code>"}，并将其提炼为持久的
    <b>事实</b>（语义记忆）和一条<b>情景记录</b>（情景记忆）。批处理既能控制成本，也能让
    摘要器获得足够上下文来判断哪些内容值得保留。</span></div>`;
  h += `<div class="tiles" style="margin-top:12px">
    <div class="tile"><b>${d.chat_pending}</b><span>待处理消息</span></div>
    <div class="tile"><b>${d.consolidate_every*2}</b><span>触发阈值</span></div>
    <div class="tile"><b>${distilled.length}</b><span>巩固生成的事实</span></div>
    <div class="tile"><b>${d.episodes.length}</b><span>情景记录总数</span></div></div>`;
  h += `<h2>已提炼的事实</h2>`;
  h += table(["主题","事实","时间"], distilled.map(f =>
    `<tr><td><code>${esc(f.subject)}</code></td><td>${esc(f.content)}</td><td class="meta">${esc((f.created_at||"").slice(0,10))}</td></tr>`));
  h += `<div class="meta" style="margin-top:10px">这是在此处展示的一项记忆操作。每次运行也会在
    <a class="reveal" onclick="location.hash='ops'">运维</a>中留下追踪记录，并可由评审评测打分。</div>`;
  return h;
}

// 工具 ▸ 结果：工具调用产生的工件（与工具本身区分；旧标签页曾混淆能力和输出）。
function toolsResults(d){
  let h = `<div class="meta" style="margin-bottom:10px">工具调用实际写入的内容。这些是结果，而不是工具本身。</div>`;
  h += `<h2>日历事件 <span class="meta" style="font-weight:400">· 来自 create_event</span></h2>`;
  h += table(["事件","开始时间","结束时间","参与者"], d.calendar.map(e =>
    `<tr><td>${esc(e.title)}</td><td class="meta">${esc(e.start)}</td><td class="meta">${esc(e.end)}</td><td>${esc(e.attendees)}</td></tr>`));
  h += `<div class="meta" style="margin-bottom:16px">同时写入 <code>calendar.ics</code>——${reveal("calendar.ics","在 Finder 中显示 calendar.ics")}（双击即可导入 Calendar.app）</div>`;
  h += `<h2>发件箱——已起草消息 <span style="font-weight:400;text-transform:none;letter-spacing:0">· ${reveal("outbox","打开 outbox 文件夹")}</span></h2>`;
  h += d.outbox.length ? d.outbox.map(o=>`<div class="card"><span class="u">${esc(o.name)}</span><div class="r">${esc(o.text)}</div></div>`).join("")
                       : `<div class="card empty">暂无已起草消息</div>`;
  return h;
}
// 工具 ▸ MCP：外部连接器。显示实时状态和可复制粘贴的配置，让任何人均可接入自己的服务器
// （可扩展，而非一次性方案）。
function toolsMCP(t){
  const m = t.mcp;
  let h = `<div class="card ${m.configured?"":""}" style="border-color:${m.live?"var(--good)":"var(--line2)"}">
    <b>Model Context Protocol${m.live?" — connected":m.configured?" — configured":" — not set up"}.</b>
    <div class="r">MCP lets Waku borrow tools from any external server (files, GitHub, a database, …),
    namespaced <code>&lt;server&gt;_&lt;tool&gt;</code>. ${m.configured
      ? `Configured servers: ${m.servers.map(s=>`<code>${esc(s)}</code>`).join(" ")}${m.live?"":" — start a chat to connect them."}`
      : "None configured yet."}</div></div>`;
  h += `<h2>Connect one (30 seconds)</h2><div class="card">
    <div class="meta">1 — install the extra: <code>pip install -e '.[mcp]'</code></div>
    <div class="meta" style="margin-top:6px">2 — create ${reveal("","the .waku folder")}<code>/mcp.json</code>:</div>
    <pre style="font-family:var(--mono);font-size:11.5px;color:var(--ink2);white-space:pre-wrap;margin-top:8px">{"servers": [
  {"name": "fs", "command": "npx",
   "args": ["-y", "@modelcontextprotocol/server-filesystem", "${esc(D&&D.home||"")}"]}
]}</pre>
    <div class="meta" style="margin-top:8px">3 — restart the dashboard. The server's tools appear above under
      <a class="reveal" onclick="location.hash='tools/available'">Available ▸ MCP servers</a>, callable in chat.</div></div>`;
  h += `<div class="meta" style="margin-top:12px">The same pattern scales: any MCP server (yours or a vendor's)
    plugs in the same way — no code changes to Waku. Skills work the same way — drop a <code>SKILL.md</code>
    in ${reveal("skills","skills/")}.</div>`;
  return h;
}

function connectionField(key, field, prefix="connection"){
  const id = `${prefix}-${key}-${field.name}`;
  const label = `${esc(field.label)}${field.required?" *":""}`;
  const help = field.help ? `<div class="conn-field-help">${esc(field.help)}</div>` : "";
  if (field.kind === "bool") return `<div class="conn-field">
    <label class="conn-check" for="${id}"><input id="${id}" data-field="${esc(field.name)}" type="checkbox" ${field.value?"checked":""}>
      <span>${label}</span></label>${help}</div>`;
  if (field.kind === "choice") return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}</span>
    <select id="${id}" data-field="${esc(field.name)}">${field.options.map(o=>`<option value="${esc(o)}" ${o===field.value?"selected":""}>${esc(o)}</option>`).join("")}</select>
    </label>${help}</div>`;
  const configured = field.secret && field.configured
    ? ` <span class="conn-secret-state">set ····${esc(field.last4)}</span>` : "";
  const clear = field.secret && field.configured
    ? `<label class="conn-clear"><input type="checkbox" data-clear="${esc(field.name)}"> Clear saved value</label>` : "";
  return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}${configured}</span>
    <input id="${id}" data-field="${esc(field.name)}" type="${field.secret?"password":"text"}"
      value="${field.secret?"":esc(field.value)}" placeholder="${field.secret?(field.configured?"Blank keeps the saved value":"Not configured"):""}">
    </label>${clear}${help}</div>`;
}
async function saveConnection(key, force){
  const modal = document.querySelector(`.connmodal[data-connection="${key}"]`), values = {}, clear = [];
  if (!modal) return;
  modal.querySelectorAll("[data-field]").forEach(el => values[el.dataset.field] = el.type === "checkbox" ? (el.checked ? "1" : "") : el.value);
  modal.querySelectorAll("[data-clear]").forEach(el => { if (el.checked) clear.push(el.dataset.clear); });
  const msg = document.getElementById(`connection-msg-${key}`);
  msg.textContent = force ? "saving without a successful test…" : "saving…";
  const r = await postJSON("/api/connections", {key, values, clear, force:!!force});
  if (!r.ok && r.can_force) {
    msg.innerHTML = `${esc(r.error)} <button class="save ghost conn-force" onclick="saveConnection('${esc(key)}',true)">Save anyway</button>`;
  } else if (!r.ok) {
    msg.textContent = r.error || "failed";
  } else {
    closeConnectionModal();
    await refresh();
  }
}
async function testConnection(key){
  const msg = document.getElementById(`connection-msg-${key}`);
  if (msg) msg.textContent = "testing…";
  const r = await postJSON("/api/connections/test", {key});
  if (!r.status) {
    if (msg) msg.textContent = r.error || "failed";
    return;
  }
  const display = connectionStatusDisplay(r.status);
  const status = document.getElementById("connection-modal-status");
  if (status) {
    status.className = `connstatus ${display.className}`;
    status.innerHTML = `<span class="conndot"></span>${esc(display.label)}`;
  }
  const detail = document.getElementById("connection-modal-status-detail");
  if (detail) detail.textContent = r.status.message || "";
  const checked = document.getElementById("connection-modal-checked");
  if (checked) checked.textContent = r.status.checked_at ? `Last checked ${r.status.checked_at}` : "";
  if (msg) msg.textContent = r.status.message || display.label;
  await refresh();
}
async function saveProvider(provider){
  const info = (D.providers || []).find(x => x.key === provider);
  const field = info && info.fields[0] && document.getElementById(`provider-${provider}-${info.fields[0].name}`);
  const payload = {provider};
  if (field && field.value) payload.key = field.value;
  // 模型是*当前*供应商的全局字段。切换卡片时必须省略这些字段，以便 apply_provider
  // 选择新供应商自己的默认值。
  if (provider === stProvider()) {
    const model = document.getElementById("provider-model"), small = document.getElementById("provider-small-model"), base = document.getElementById("provider-base-url"), custom = document.getElementById("provider-custom-key");
    if (model) payload.model = model.value;
    if (small) payload.small_model = small.value;
    if (base) payload.base_url = base.value;
    if (custom && custom.value) payload.custom_key = custom.value;
    if (document.getElementById("provider-clear-custom-key")?.checked) payload.custom_key = "";
  }
  const r = await postJSON("/api/providers", payload);
  if (!r.ok) alert(r.error || "Provider update failed"); else refresh();
}
function stProvider(){ return (D.settings || {}).provider || "anthropic"; }

const CONNECTION_GROUPS = ["Channels", "Productivity", "Memory", "Tools"];
// 使用“记忆”而不是“存储”。注册表已将此组称为“Memory & Storage”，而显示映射曾丢失
// 描述其实际作用的部分。Notion 是情景记忆存储，Supabase 是语义记忆存储，加入此组的
// 每项托管记忆服务也是语义记忆服务——没有任何一项是泛化存储，而记忆正是仪表盘其余部分
// 所围绕的四大支柱之一。
const CONNECTION_GROUP_MAP = {
  "Channels": "Channels",
  "Calendar & Productivity": "Productivity",
  "Memory & Storage": "Memory",
  "Search & Observability": "Tools",
};

function connectionDisplayGroup(item){
  if (item.key === "apple_tools") return "Tools";
  return CONNECTION_GROUP_MAP[item.group] || "Tools";
}

function connectionStatusDisplay(status){
  const state = (status && status.state) || "not_configured";
  if (state === "connected") return {label:"connected", className:"connected"};
  if (state === "error") return {label:"error", className:"error"};
  // “已配置”表示所有必填字段均已填写且附加包已安装，只是尚未探测。这并不是警告，
  // 因此不能显示琥珀色的“需要设置”标签：首次访问时多数可用配置均处于此状态，若将其
  // 着色为问题，会误导每位新用户以为本来正常的 Telegram、Notion 和 Tavily 需要修复。
  if (state === "configured") return {label:"configured · not tested", className:"configured"};
  if (state === "installed_but_unconfigured") return {label:"needs setup", className:"needs-setup"};
  return {label:"not configured", className:"not-configured"};
}

function connectionCard(item){
  const display = connectionStatusDisplay(item.status);
  const action = item.status && item.status.state !== "not_configured" ? "Edit" : "Configure";
  // 在卡片上说明原因。“需要设置”涵盖两种无关问题：缺少值（“缺少 NOTION_TOKEN”）和
  // 缺少软件包（“缺少 notion extra”，需要 pip install 而不是密钥）。此前必须打开弹窗
  // 才能看到原因。对已连接/已配置状态，此消息只是重复标签，因此只在能提供额外信息时显示。
  const why = (item.status && item.status.message
    && (item.status.state === "installed_but_unconfigured" || item.status.state === "error"))
    ? `<div class="connwhy">${esc(item.status.message)}</div>` : "";
  return `<article class="provcard conncard" data-connection-card="${esc(item.key)}">
    <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
    <div class="provname">${esc(item.name)}</div>
    <div class="connstatus ${display.className}"><span class="conndot"></span>${esc(display.label)}</div>
    ${why}
    <div class="conndesc">${esc(item.what)}</div>
    <div class="provactions connactions">
      <button class="save ghost" onclick="openConnectionModal('${esc(item.key)}')">${action}</button>
    </div>
  </article>`;
}

function connectionsGrid(items){
  const grouped = Object.fromEntries(CONNECTION_GROUPS.map(group => [group, []]));
  items.forEach(item => grouped[connectionDisplayGroup(item)].push(item));
  return CONNECTION_GROUPS.map(group => `<section class="connsection">
    <h2>${group}</h2>
    <div class="provgrid conngrid">${grouped[group].map(connectionCard).join("")}</div>
  </section>`).join("") + `<div id="connection-modal-root"></div>`;
}

function openConnectionModal(key){
  const item = ((D && D.connections) || []).find(connection => connection.key === key);
  const root = document.getElementById("connection-modal-root");
  if (!item || !root) return;
  markEditing();
  const display = connectionStatusDisplay(item.status);
  const status = item.status || {};
  const fields = item.fields.map(field => connectionField(item.key, field)).join("");
  root.innerHTML = `<div class="connmodal-back" onclick="closeConnectionModal()" onkeydown="connectionModalKeydown(event)">
    <section class="connmodal" data-connection="${esc(item.key)}" role="dialog" aria-modal="true" aria-labelledby="connection-modal-title" onclick="event.stopPropagation()">
      <header class="connmodal-head">
        <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
        <div class="connmodal-title">
          <h3 id="connection-modal-title">${esc(item.name)}</h3>
          <div class="connstatus ${display.className}" id="connection-modal-status"><span class="conndot"></span>${esc(display.label)}</div>
        </div>
        <button class="connmodal-close" type="button" onclick="closeConnectionModal()" aria-label="Close">Close</button>
      </header>
      <p class="conndesc connmodal-desc">${esc(item.what)}</p>
      <div class="connmodal-meta">
        <span id="connection-modal-status-detail">${esc(status.message || "")}</span>
        <span id="connection-modal-checked">${status.checked_at?`Last checked ${esc(status.checked_at)}`:""}</span>
      </div>
      ${(item.install_command || item.setup_url) ? `<div class="connsetup">
        ${item.install_command?`<code>${esc(item.install_command)}</code>`:""}
        ${item.setup_url?`<a href="${esc(item.setup_url)}" target="_blank" rel="noopener noreferrer">Setup guide ↗</a>`:""}
      </div>` : ""}
      <div class="connection-fields">${fields}</div>
      <footer class="connmodal-actions">
        <button class="save" onclick="saveConnection('${esc(item.key)}')">Save</button>
        <button class="save ghost" onclick="testConnection('${esc(item.key)}')">Test connection</button>
        <span class="connmodal-message" id="connection-msg-${esc(item.key)}" aria-live="polite"></span>
      </footer>
    </section>
  </div>`;
  setTimeout(() => {
    const target = root.querySelector(".connection-fields input, .connection-fields select")
      || root.querySelector(".connmodal-close");
    target?.focus();
  }, 0);
}

function closeConnectionModal(){
  editing = false;
  const root = document.getElementById("connection-modal-root");
  if (root) root.innerHTML = "";
  if (activeView === "connections") render();
}

function connectionModalKeydown(event){
  if (event.key === "Escape") closeConnectionModal();
}

const VIEWS = {
  models(d){
    // 供应商卡片网格（徽标/状态点/编辑/启用-禁用）。编辑操作在从卡片打开的弹窗中进行；
    // 二者均位于 js/models.js。
    return modelsGrid(d);
  },
  connections(d){
    const items = d.connections || [];
    return items.length ? connectionsGrid(items) : `<div class="card empty">No integrations registered.</div>`;
  },
  // 渠道：跨所有渠道（仪表盘、Telegram、语音、CLI）的一条统一对话，由同一循环和记忆
  // 回答。每条消息均以 Hermes 风格标注其接入来源，用户在右侧停靠栏输入消息。
  // 渠道即会话收件箱（类似 Slack/Intercom）：每个会话一行，附带来源渠道标签。点击即可在
  // 对话停靠栏（活动会话）打开，不再显示与停靠栏重复的扁平消息流。
  gateway(d){
    const sessions = d.sessions || [];
    let h = `<div class="meta" style="margin-bottom:14px">Every conversation across every channel —
      web, phone (Telegram), voice, terminal — answered by the same brain. Click one to open it in the
      chat dock &rarr;. This is the inbox; the dock is the open thread.</div>`;
    if (!sessions.length)
      return h + `<div class="card empty">no conversations yet — say something in the chat dock &rarr;</div>`;
    h += sessions.map(s => {
      const tags = gwTags(s);
      const on = s.id === SESSION;
      return `<div class="toolcard" style="cursor:pointer${on?';border-color:var(--accent)':''}" onclick="openConversation('${esc(s.id)}')">
        <div class="tn" style="display:flex;justify-content:space-between;align-items:baseline;gap:10px">
          <span>${esc(s.title||s.id)} ${tags}</span>
          <span class="meta" style="font-weight:400;white-space:nowrap">${sessionMeta(s)}</span></div>
        <div class="td">${esc(s.last||"")}</div></div>`;
    }).join("");
    return h;
  },
  overview(d){
    const s = d.stats;
    const u = d.usage || {total_cost:0};
    const tiles = [
        [money(u.total_cost),"spent · all-time","money"],[secs(s.latency_avg),"avg turn",""],
        [s.turns,"turns",""],[s.tool_calls,"tool calls",""],
        [d.facts.length,"facts",""],[d.calendar.length,"events",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("");
    return `<div class="tiles">${tiles}</div>
    <h2>Retrieval gate — the hero decision</h2>${gateSplit(s)}
    <h2 style="margin-top:26px">Architecture — click any box <span class="arch-status"></span></h2>
    ${archSVG(d)}
    <h2>Graph workflows — when a turn needs shape</h2>
    ${graphPanel(d)}
    <h2>Latest turn</h2>${d.turns.length?turnCard(d.turns[0]):'<div class="card empty">no turns yet — talk to Waku first</div>'}`;
  },
  loop(d){
    return d.turns.length ? d.turns.map(turnCard).join("") : `<div class="card empty">no turns yet</div>`;
  },
  // 图工作流：循环的同级能力。图表从引擎自身的 describe()（通过 d.graph.workflows
  // 提供）渲染，因此绝不会展示引擎没有运行的结构。这里没有模式切换——框架会自行路由每条
  // 消息，此标签页仅负责说明过程。
  graph(d){
    const g = d.graph || {enabled:false, workflows:[], stats:{quick:0, full:0}};
    let h = `<div class="meta" style="margin-bottom:14px">The loop is one agent turn: the model picks tools until
      it stops. Some work has <b>shape</b> — steps that can run at the same time, and explicit "if this, go
      there" routing. A <b>graph workflow</b> makes that shape first-class: nodes (each does one job) connected
      by edges (what happens next). The loop did not change one line — the <code>full_agent</code> node below
      IS the same loop, running as one step. The harness routes every message itself — and workflows you
      can also call BY NAME from the chat box: type <code>/graphs</code> to see them.</div>`;
    if (!g.enabled)
      h += `<div class="card"><b>Off</b> — every turn currently runs the classic loop.
        <div class="meta" style="margin-top:6px">Switch on <b>graph workflows</b> in
        <a class="reveal" onclick="location.hash='settings'">Behaviour</a>, or set
        <code>WAKU_GRAPH_WORKFLOWS=1</code> in <code>.env</code>. Any failure anywhere fails open to the
        plain loop — this can never lose a reply, only save time and tokens.</div></div>`;
    // 两个工作流对应触发条件不同的两类任务；页面必须清楚地表达这一点，否则上下堆叠的
    // 两张图会看似两个供用户选择的选项。
    const NOTE = {
      triage: `<b>Runs itself, on every message.</b> Gated by the graph-workflows flag.
        Solid arrows = always, dashed = the router's choice. <code>full_agent</code> is the
        ordinary loop running as one node — a graph does not replace the loop, it arranges calls to it.`,
      gather: `<b>Runs when you start it</b> — <code>make gather</code> or the button below — and
        ignores the flag entirely. The four scans have no dependencies on each other, so the engine
        runs them in ONE WAVE: together, not in turn. It proposes and never acts; the digest lands
        in the outbox for you to read.`,
    };
    (g.workflows || []).forEach(w => {
      if (!w) return;
      h += `<h2>${esc(w.name)} — live topology <span class="arch-status"></span></h2>`;
      const tot = g.stats.quick + g.stats.full;
      const extra = w.name === "triage" && tot
        ? ` · ${g.stats.quick} quick / ${g.stats.full} full so far` : "";
      h += `<div class="card">${graphSVG(w)}
        <div class="meta" style="margin-top:8px">${NOTE[w.name] || ""}${extra} ·
        drawn from the engine's own <code>describe()</code>, so this picture cannot drift from the code</div></div>`;
      if (w.name === "gather") h += graphRunPanel();
    });
    const gturns = (d.turns||[]).filter(t => t.graph && t.graph.route);
    h += `<h2>Graph turns</h2>`;
    h += gturns.length
      ? gturns.slice(0,20).map(t => `<div class="card">
          <div class="u">${esc(t.user_message)}</div>
          <div class="meta" style="margin-top:4px"><span class="badge ${t.graph.route==="quick"?"":"retrieve"}">graph · ${esc(t.graph.route)}</span>
            <span class="meta" style="margin:0">${esc(t.graph.reason||"")}</span></div>
          <div class="r">${renderMarkdown(t.reply||"")}</div></div>`).join("")
      : `<div class="card empty">no graph turns yet — ${g.enabled
          ? 'say "thanks!" in the chat and watch it take the quick door'
          : "switch the flag on first"}</div>`;
    return h;
  },
  memory(d, sub){
    sub = sub || "overview";
    const tabs = [["overview","Overview"],["semantic","Semantic",d.facts.length],
      ["episodic","Episodic",d.episodes.length],["skills","Skills",d.skills.length],
      ["soul","SOUL"],["consolidation","Consolidation",d.chat_pending]];
    let h = subtabBar("memory", tabs, sub);
    if (sub==="semantic") return h + memSemantic(d);
    if (sub==="episodic") return h + memEpisodic(d);
    if (sub==="skills") return h + memSkills(d);
    if (sub==="soul") return h + memSoul(d);
    if (sub==="consolidation") return h + memConsolidation(d);
    return h + memOverview(d);
  },
  settings(d){
    const st = d.settings || {providers:[]};
    return `<h2>Experimental tools</h2><div class="card">
      <div class="meta" style="margin-bottom:8px">Opt in to local coding delegation for chat.</div>
      <label class="fld">Sub-agent delegation<select id="set-experimental" onfocus="markEditing()">
        <option value="" ${!st.experimental?"selected":""}>off</option>
        <option value="1" ${st.experimental?"selected":""}>on</option>
      </select></label>
      <button class="save" onclick="saveSettings()">Save</button><span class="meta" id="set-msg"></span></div>
    <h2>Graph workflows</h2><div class="card">
      <div class="meta" style="margin-bottom:8px">Off by default. When on, <b>every</b> message is triaged
        through a graph first: a small model classifies it while today's calendar loads in parallel — trivial
        messages get a fast small-model reply, real tasks run the exact same loop as a node. This flag governs
        the AUTOMATIC door only — workflows you call by name (<code>/gather</code>) run either way. Any
        failure fails open to the plain loop. Watch it live on the
        <a class="reveal" onclick="location.hash='graph'">Graph</a> tab.</div>
      <label class="fld">Triage-first turns
        <select id="set-graph-workflows" onfocus="markEditing()">
          <option value="" ${!st.graph_workflows?"selected":""}>off — every turn runs the classic loop (default)</option>
          <option value="1" ${st.graph_workflows?"selected":""}>on — triage graph routes each message</option>
        </select></label>
      <div style="margin-top:12px"><button class="save" onclick="saveSettings()">Save &amp; switch</button>
        <span class="meta" style="margin-left:10px">rebuilds the agent in-process — no restart</span></div>
    </div>`;
  },
  tools(d, sub){
    const t = d.tools || {catalog:[], mcp:{configured:false,servers:[],live:false}, apple_on:false};
    sub = sub || "available";
    const tabs = [["available","Available",t.catalog.length],["results","Results"],
      ["mcp","MCP",t.mcp.servers.length||null]];
    let h = subtabBar("tools", tabs, sub);
    if (sub === "results") return h + toolsResults(d);
    if (sub === "mcp") return h + toolsMCP(t);
    // 可用：代理本轮*能够*执行的能力（按来源分组），而非仅显示已经执行过的能力。
    h += `<div class="meta" style="margin-bottom:12px">The capabilities the agent can call this turn.
      A tool is a name + description the model reads, a JSON schema, and a Python function — that's it.
      ${t.apple_on?"":"Apple tools are off (set <code>WAKU_APPLE_TOOLS=1</code>). "}Connect more via
      <a class="reveal" onclick="location.hash='tools/mcp'">MCP</a>.</div>`;
    const SRC = [["flagship","Flagship task — scheduling"],["web","Web search"],
      ["self-management","Self-management — it edits its own memory"],
      ["apple","Apple ecosystem"],["mcp","MCP servers"],["other","Other"]];
    SRC.forEach(([key,label]) => {
      const items = t.catalog.filter(c => c.source === key);
      if (!items.length) return;
      h += `<h2>${label}</h2>`;
      h += items.map(c => `<div class="toolcard">
        <div class="tn">${esc(c.name)}<span class="srcpill ${key==="mcp"?"mcp":key==="apple"?"apple":""}">${esc(key)}</span></div>
        <div class="td">${esc(c.description)}</div></div>`).join("");
    });
    // 路线图：白板中的方块尚未接入实现——设置合理预期，不作过度承诺。
    if ((t.planned||[]).length){
      h += `<h2>Coming soon <span class="meta" style="font-weight:400">· on the architecture chart, not wired in yet (opt in with <code>WAKU_EXPERIMENTAL=1</code>)</span></h2>`;
      h += t.planned.map(p => `<div class="toolcard" style="opacity:.7">
        <div class="tn">${esc(p.name)}<span class="srcpill apple">soon · ${esc(p.box)}</span></div>
        <div class="td">${esc(p.description)}</div></div>`).join("");
    }
    return h;
  },
  database(d, sub){
    // 持久化层本身：一个 SQLite 文件、真实数据表和 FTS5 索引。导航中使用“数据”（比
    // “state.db”更直观），但仍保留 state.db 名称，因为那正是用户可以直接打开的文件名。
    const db = d.db || {tables:[], all_tables:[], fts:[], size:0, path:""};
    const tables = db.tables || [];
    sub = sub || "overview";
    const tabs = [["overview","Overview"],
      ...tables.map(t => [t.name, t.name, t.count]),
      ["query","SQL console"]];
    let h = subtabBar("database", tabs, sub);
    if (sub === "query") return h + dbQueryView();
    if (sub !== "overview"){
      const t = tables.find(x => x.name === sub);
      if (!t) return h + `<div class="card empty">no such table</div>`;
      const notionNote = (t.name === "episodes" && d.episodes_source === "notion")
        ? `<div class="meta" style="margin-bottom:10px">Episodes currently live in Notion — see
            <a class="reveal" onclick="location.hash='memory/episodic'">Memory ▸ Episodic</a>.
            The rows below are the old local copy in state.db.</div>` : "";
      return h + notionNote + `<div class="meta" style="margin-bottom:10px">${DB_DESC[t.name]||""}</div>` + dbTable(t);
    }
    const kb = (db.size/1024).toFixed(1);
    h += `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>Database vs Memory.</b> <span class="r">This is the raw persistence layer — the literal SQLite
      tables. The <a class="reveal" onclick="location.hash='memory'">Memory tab</a> is the friendly
      view of the same rows (facts, episodes, skills, persona). One file, two altitudes. Where Hermes
      uses a <code>MEMORY.md</code> file, Waku uses these queryable tables — and mirrors them to a
      readable <code>MEMORY.md</code> too.</span></div>`;
    h += `<div class="card">
      <div class="u" style="font-family:var(--mono);font-size:12.5px;word-break:break-all">${esc(db.path)}</div>
      <div class="meta">${kb} KB on disk · SQLite + FTS5 · open it yourself: <code>sqlite3 .waku/state.db</code></div>
      <div class="meta" style="margin-top:8px">${reveal("state.db","reveal state.db in Finder")} &nbsp;·&nbsp; ${reveal("","open the .waku folder")}</div></div>`;
    h += `<h2>Tables — click a tab above, or a row here</h2>`;
    h += table(["table","rows","what it holds"], tables.map(t =>
      `<tr><td><a class="reveal" onclick="location.hash='database/${esc(t.name)}'"><code>${esc(t.name)}</code></a></td>
        <td class="meta">${t.count}</td><td class="meta">${DB_DESC[t.name]||""}</td></tr>`));
    h += `<h2>FTS5 — the keyword index</h2><div class="card">The <code>*_fts</code> virtual tables (and their
      <code>*_fts_data</code>/<code>*_fts_idx</code> shadows) make memory searchable by keyword — no embeddings,
      no vector DB. This is the "keyword top-k" the retrieval gate queries.
      <div class="meta" style="margin-top:8px">all ${db.all_tables.length} tables: ${db.all_tables.map(t=>`<code>${esc(t)}</code>`).join(" ")}</div></div>`;
    return h;
  },
  ops(d){
    const s = d.stats;
    const u = d.usage || {calls:0,total_in:0,total_out:0,total_cost:0,by_day:[],by_provider:[]};
    let h = `<div class="tiles">${[
        [money(u.total_cost),"spent · all-time","money"],[u.total_in.toLocaleString(),"tokens in · all-time",""],
        [u.total_out.toLocaleString(),"tokens out · all-time",""],[u.calls.toLocaleString(),"LLM calls",""],
        [secs(s.latency_avg),"avg turn",""],[`${s.tool_errors}`,"tool errors",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("")}</div>`;

    h += `<h2>Spend <span class="meta" style="font-weight:400">· permanent ledger — survives a demo reset</span></h2>`;
    h += `<div class="card"><span class="r">Every LLM call's tokens are logged to
      <code>.waku/usage.jsonl</code> (append-only, never wiped). Dollar cost is estimated from tokens
      × current pricing — the tokens are the ground truth. ${reveal("usage.jsonl","open usage.jsonl")}</span></div>`;
    if ((u.by_provider||[]).length){
      h += table(["provider","LLM calls","tokens in","tokens out","cost (est)"], u.by_provider.map(p =>
        `<tr><td><code>${esc(p.provider)}</code></td><td class="meta">${p.calls}</td>
          <td class="meta">${p.in.toLocaleString()}</td><td class="meta">${p.out.toLocaleString()}</td>
          <td class="meta">${money(p.cost)}</td></tr>`));
    }
    if ((u.by_day||[]).length){
      h += `<h2>Spend per day</h2>`;
      h += table(["day","LLM calls","tokens in","tokens out","cost (est)"], u.by_day.map(r =>
        `<tr><td class="meta">${esc(r.date)}</td><td class="meta">${r.calls}</td>
          <td class="meta">${r.in.toLocaleString()}</td><td class="meta">${r.out.toLocaleString()}</td>
          <td class="meta">${money(r.cost)}</td></tr>`));
    }

    h += `<h2>Retrieval gate — which turns used memory</h2>${gateSplit(s)}`;
    const decided = d.turns.filter(t => t.gate);
    if (decided.length){
      h += `<div class="meta" style="margin:8px 0">The actual decisions (what was skipped vs retrieved), most recent first:</div>`;
      h += table(["turn","decision","why"], decided.slice(0,10).map(t =>
        `<tr><td>${esc((t.user_message||"").slice(0,44))}</td>
          <td><span class="pill ${t.gate.decision==="skip"?"skip":"pass"}">${esc(t.gate.decision)}</span></td>
          <td class="meta">${esc(t.gate.reason||"")}</td></tr>`));
    }

    h += `<h2>Release gate <span class="meta" style="font-weight:400">· the ship/no-ship check</span></h2>`;
    h += `<div class="card"><span class="r">Before you ship a change (new prompt, swapped model, tuned
      retrieval), <code>make gate</code> runs both eval suites: deterministic must pass 100%, the judge must
      clear its threshold. It's manual — you run it — so there's one record per run. The history below grows
      each time you run it.</span></div>`;
    h += d.eval_report ? `<div class="card">
        <span class="pill ${d.eval_report.deterministic}">deterministic · ${d.eval_report.deterministic}</span>
        <span class="pill ${d.eval_report.judge==="pass"?"pass":d.eval_report.judge==="fail"?"fail":"skip"}" style="margin-left:8px">llm-judge · ${d.eval_report.judge}</span>
        <div class="meta">last run ${esc(d.eval_report.ran_at)} — re-run with <code>make gate</code></div></div>`
      : `<div class="card empty">never run yet — run <code>make gate</code> to populate this</div>`;

    if ((d.eval_history||[]).length){
      const cnt = s => s ? `${s.passed||0} pass · ${s.failed||0} fail` : "—";
      h += `<h2>Eval history</h2>`;
      h += table(["when","deterministic","llm-judge","counts"], d.eval_history.map(r =>
        `<tr><td class="meta">${esc((r.ran_at||"").replace("T"," ").slice(0,19))}</td>
         <td><span class="pill ${r.deterministic}">${esc(r.deterministic)}</span></td>
         <td><span class="pill ${r.judge==="pass"?"pass":r.judge==="fail"?"fail":"skip"}">${esc(r.judge)}</span></td>
         <td class="meta">det ${cnt(r.suites&&r.suites.deterministic)} · judge ${cnt(r.suites&&r.suites.judge)}</td></tr>`));
    }

    h += `<h2>Slowest turns</h2>`;
    const slow = [...d.turns].filter(t=>t.latency_ms!=null).sort((a,b)=>b.latency_ms-a.latency_ms).slice(0,6);
    h += table(["turn","latency","cost","tools"], slow.map(t =>
      `<tr><td>${esc((t.user_message||"").slice(0,48))}</td><td class="meta">${secs(t.latency_ms)}</td><td class="meta">${money(t.cost||0)}</td><td class="meta">${(t.tools||[]).map(x=>x.tool).join(", ")||"—"}</td></tr>`));

    h += `<h2>Tracing <span class="meta" style="font-weight:400">· every turn as JSONL, always on</span></h2>`;
    if ((d.trace_errors||[]).length){
      h += d.trace_errors.map(e => `<div class="card"><span class="pill fail">trace encoding error</span>
        <div class="meta" style="margin-top:8px"><code>${esc(e.file)}</code> — ${esc(e.error)}</div></div>`).join("");
    }
    h += `<div class="card"><span class="r">${s.trace_files} trace file(s) in <code>traces/</code>${
      d.trace_file?` (newest: <code>${esc(d.trace_file)}</code>)`:""}. ${reveal("traces","open the traces folder")}.
      A trace is just "what happened, in order" — here are the most recent lines:</span></div>`;
    h += (d.trace_tail||[]).length ? table(["event","detail","when"], d.trace_tail.map(e =>
        `<tr><td><code>${esc(e.type)}</code></td><td class="meta">${esc(String(e.detail).slice(0,60))}</td>
          <td class="meta">${esc((e.ts||"").replace("T"," ").slice(0,19))}</td></tr>`))
      : `<div class="card empty">no trace lines yet — talk to Waku</div>`;
    h += `<div class="meta" style="margin-top:8px">Span waterfalls: <code>make trace</code> + <code>OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317</code>.</div>`;

    if (d.wake_scans.length){
      h += `<h2>Voice — wake near-misses</h2>`;
      h += table(["heard","when"], d.wake_scans.map(w =>
        `<tr><td>${esc(w.heard)}</td><td class="meta">${esc((w.ts||"").replace("T"," ").slice(0,19))}</td></tr>`));
    }
    return h;
  },
};
