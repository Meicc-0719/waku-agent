// waku 仪表板 — 模型选择器/目录/引脚，以及其余的设置切换。
// 从 app.js 中分离出来：经典 <script>，共享全局范围（无构建
// 步骤，无模块）。加载顺序+规则：static/README.md。

async function saveSettings(){
  const experimental = document.getElementById("set-experimental")?.value;
  const graph_workflows = document.getElementById("set-graph-workflows")?.value;
  document.getElementById("set-msg").textContent = "switching…";
  const r = await postJSON("/api/settings", {experimental, graph_workflows});
  document.getElementById("set-msg").textContent = r.error ? ("Error: "+r.error) : "Saved.";
}
function markEditing(){ editing = true; }

// 模型选择器：从 /api/models 填充设置数据列表（活动的
// 端点的实时目录；在 OpenRouter 上，每个条目都表示免费/工具支持）。
// Waku 的循环需要工具调用，因此无工具模型被标记为此类。
let modelCatalog = null;
async function loadModelList(){
  const dl = document.getElementById("model-list");
  if (!dl) return;
  if (modelCatalog === null){
    try { modelCatalog = await (await fetch("/api/models")).json(); }
    catch(e){ modelCatalog = {models:[], listed:false}; }
  }
  const ms = modelCatalog.models || [];
  dl.innerHTML = ms.map(m => {
    const price = m.free ? "free" : (m.price_out != null ? `$${m.price_in}/$${m.price_out} per M` : "");
    const tags = [price, m.tools === false ? "chat-only" : "", m.reasoning ? "reasoning" : "",
                  m.context ? Math.round(m.context/1000) + "k ctx" : ""].filter(Boolean).join(" · ");
    return `<option value="${esc(m.id)}">${esc(tags)}</option>`;
  }).join("");
  const msg = document.getElementById("model-list-msg");
  if (!msg) return;
  if (modelCatalog.listed){
    const free = ms.filter(m=>m.free), freeTools = free.filter(m=>m.tools);
    msg.textContent = `${ms.length} models on ${modelCatalog.endpoint}` +
      (free.length ? ` · ${free.length} free, ${freeTools.length} of those tool-capable (Waku needs tool calling)` : "") +
      ` · type in the field above to search`;
  } else {
    msg.textContent = modelCatalog.error ? `model list unavailable: ${modelCatalog.error}` : "";
  }
  renderCatalog();
}

// 目录浏览器（当端点列出模型时显示，即 OpenRouter）：
// 每个老虎机的建议选择、搜索+免费/工具过滤器以及完整列表
// 按供应商分组。每行都可以进入任一插槽：“use”是循环模型
// （需要工具调用），“gate”是小模型（需要简洁的 JSON，所以
// 推理模型则远离它）。
let catFilter = {q: "", free: false, tools: false};

function modelRow(m, st){
  const cur = m.id === st.model, curGate = m.id === st.small_model;
  const isPinned = (st.pinned || []).some(p => p.provider === st.provider && p.model === m.id);
  const price = m.free ? "free" : (m.price_out != null ? `$${m.price_in}/$${m.price_out} per M` : "");
  const tags = [price, m.context ? Math.round(m.context/1000) + "k ctx" : ""]
               .filter(Boolean).join(" · ");
  return `<div class="tool" style="display:flex;align-items:center;gap:8px;padding:6px 8px">
    <a class="pinstar ${isPinned?"on":""}" title="${isPinned?"pinned to Your models — click to remove":"pin to Your models (shows in chat switcher)"}"
       onclick="pinModel('${esc(st.provider)}','${esc(m.id)}','${isPinned?"unpin":"pin"}')">${isPinned?"★":"☆"}</a>
    <code style="flex:1;word-break:break-all">${esc(m.id)}</code>
    <span class="meta" style="margin:0;white-space:nowrap">${esc(tags)}</span>
    ${m.reasoning ? `<span class="srcpill apple" title="thinks out loud before answering: fine for the loop, a poor fit for the gate's tiny token budget">reasoning</span>` : ""}
    ${curGate ? `<span class="srcpill">GATE</span>`
              : `<a class="reveal" data-id="${esc(m.id)}" onclick="switchModel(this.dataset.id,true)" title="use as the gate/summary model">gate</a>`}
    ${cur ? `<span class="srcpill" style="background:var(--good-soft);color:var(--good)">CURRENT</span>`
          : (m.tools === false ? `<span class="meta" style="margin:0" title="the loop needs tool calling">chat-only</span>`
                               : `<button class="save" data-id="${esc(m.id)}" onclick="switchModel(this.dataset.id)">use</button>`)}
  </div>`;
}

// 插槽建议是对目录元数据（工具、
// 价格、背景、推理），而不是质量排行榜。循环：工具可用，
// 首先是免费，然后是最大的背景。 Gate：廉价的非推理指令式。
const GATE_HINT = /instruct|gemma|haiku|flash|mini|nano|lite|small/;
function loopPicks(ms){
  return ms.filter(m => m.tools)
           .sort((a,b) => (b.free - a.free) || ((b.context||0) - (a.context||0))).slice(0, 4);
}
function gatePicks(ms){
  return ms.filter(m => m.tools !== false && m.reasoning !== true
                        && (m.free || (m.price_out != null && m.price_out <= 1.5)))
           .sort((a,b) => (GATE_HINT.test(b.id) - GATE_HINT.test(a.id))
                        || (b.free - a.free) || ((a.price_out||99) - (b.price_out||99))).slice(0, 4);
}

function renderCatalog(){
  const box = document.getElementById("catalog");
  if (!box || !modelCatalog) return;
  const all = modelCatalog.models || [];
  const head = document.getElementById("catalog-h");
  if (!modelCatalog.listed || !all.length){
    box.style.display = "none"; if (head) head.style.display = "none"; return;
  }
  box.style.display = ""; if (head) head.style.display = "";
  box.innerHTML = `
    <div class="cat-controls">
      <input id="cat-q" type="text" placeholder="filter models…" value="${esc(catFilter.q)}"
        onfocus="markEditing()" oninput="catFilter.q=this.value;renderCatalogList()">
      <label class="meta" style="margin:0"><input type="checkbox" id="cat-free" ${catFilter.free?"checked":""}
        onchange="catFilter.free=this.checked;renderCatalogList()"> free only</label>
      <label class="meta" style="margin:0"><input type="checkbox" id="cat-tools" ${catFilter.tools?"checked":""}
        onchange="catFilter.tools=this.checked;renderCatalogList()"> tool-capable only</label>
    </div>
    <div id="cat-list"></div>
    <div class="meta" id="free-switch-msg" style="margin-top:6px"></div>`;
  renderCatalogList();
}

function renderCatalogList(){
  const list = document.getElementById("cat-list");
  if (!list || !modelCatalog) return;
  const st = (D && D.settings) || {};
  const all = modelCatalog.models || [];
  const q = catFilter.q.trim().toLowerCase();
  const shown = all.filter(m => (!q || m.id.toLowerCase().includes(q))
                             && (!catFilter.free || m.free)
                             && (!catFilter.tools || m.tools));
  let h = "";
  if (!q && !catFilter.free && !catFilter.tools){
    h += `<div class="meta" style="margin:4px 0">Suggested picks: transparent heuristics from catalog metadata (tools, price, context), not a quality leaderboard</div>`;
    h += `<div class="meta" style="margin:6px 0 2px"><b>For the loop</b> (needs tool calling; free first, biggest context)</div>`;
    h += loopPicks(all).map(m => modelRow(m, st)).join("");
    h += `<div class="meta" style="margin:10px 0 2px"><b>For the gate</b> (cheap, terse, non-reasoning)</div>`;
    h += gatePicks(all).map(m => modelRow(m, st)).join("");
    h += `<div class="meta" style="margin:12px 0 2px"><b>Everything</b> (${all.length} models, by vendor)</div>`;
  } else {
    h += `<div class="meta" style="margin:4px 0">${shown.length} of ${all.length} models</div>`;
  }
  const vendors = {};
  shown.forEach(m => (vendors[m.id.split("/")[0]] ??= []).push(m));
  const expand = q || catFilter.free || catFilter.tools;
  h += Object.keys(vendors).sort().map(v => `
    <details ${expand ? "open" : ""}><summary><code>${esc(v)}</code>
      <span class="meta" style="margin-left:6px">${vendors[v].length}${vendors[v].some(m=>m.free) ? " · has free" : ""}</span></summary>
      ${vendors[v].map(m => modelRow(m, st)).join("")}
    </details>`).join("");
  list.innerHTML = h;
}

// 一键模型切换：发布到 /api/providers，以便提供者/模型对
// 由集成层验证和应用。保留另一个插槽
// （主与门）按原样。为下一个回合而活。
async function switchModel(id, asGate){
  const st = (D && D.settings) || {};
  const msg = document.getElementById("free-switch-msg");
  if (msg) msg.textContent = "switching…";
  const payload = {provider: st.provider,
    model: asGate ? st.model : id, small_model: asGate ? id : st.small_model};
  const r = await postJSON("/api/providers", payload);
  if (!r.error){ editing = false; modelCatalog = null; await refresh(); }
  if (msg) msg.textContent = r.error ? ("Error: " + r.error)
                                     : (asGate ? "Gate model is now " : "Model is now ") + id + ". Applies from your next message.";
}

// “你的模特”——聊天药丸显示的精选候选名单，涵盖每个
// 提供者。每个提供商的第一个固定模型是该提供商的默认模型
// （切换到它时使用）。固定/取消固定/默认全部 POST /api/pin.
function yourModelsCard(st){
  const pinned = st.pinned || [];
  const providers = (st.providers || []).map(p => p.name);
  const rows = pinned.map(p => `
    <div class="pinrow ${(p.provider===st.provider && p.model===st.model)?"on":""}">
      <span class="mm-prov">${esc(p.provider)}</span>
      <code style="flex:1;word-break:break-all">${esc(p.model)}</code>
      ${p.default ? `<span class="srcpill" title="this provider's default model">default</span>`
                  : `<a class="reveal" onclick="pinModel('${esc(p.provider)}','${esc(p.model)}','default')" title="make this ${esc(p.provider)}'s default">make default</a>`}
      <a class="reveal" onclick="pinModel('${esc(p.provider)}','${esc(p.model)}','unpin')" title="remove from your list">remove</a>
    </div>`).join("") || `<div class="meta">No models pinned yet — add one below.</div>`;
  // 添加行是独立的：选择任何提供商 + 类型/选择模型 ID，
  // 然后添加。即使对于没有实时目录的提供商也适用。数据列表
  // 建议当前提供商的模型（我们获取的唯一一个）。
  const provOpts = providers.map(n => `<option value="${esc(n)}" ${n===st.provider?"selected":""}>${esc(n)}</option>`).join("");
  // 填充模型 <select> 为最初选择的提供者一旦
  // 卡位于 DOM 中（该提供商目录的最新获取）。
  setTimeout(() => loadAddModels(st.provider), 0);
  return `<h2>Your models <span class="meta" style="font-weight:400">— what the chat switcher shows</span></h2>
    <div class="card">
      ${rows}
      <div class="addmodel">
        <select id="add-prov" onfocus="markEditing()" onchange="loadAddModels(this.value)">${provOpts}</select>
        <select id="add-model"><option value="">loading models…</option></select>
        <button class="save" onclick="addPinnedModel()">Add</button>
      </div>
      <div class="meta" style="margin-top:6px" id="add-msg">Pick a provider, choose a model, then Add.</div>
    </div>`;
}

// 使用提供者的目录（任何提供者，而不是
// 只是活动的 - 后端采用 ?provider= 覆盖）。
async function loadAddModels(provider){
  const sel = document.getElementById("add-model");
  const msg = document.getElementById("add-msg");
  if (!sel) return;
  sel.innerHTML = `<option value="">loading ${esc(provider)} models…</option>`;
  let data;
  try { data = await (await fetch("/api/models?provider=" + encodeURIComponent(provider))).json(); }
  catch(e){ sel.innerHTML = `<option value="">couldn't load — pick another provider</option>`; return; }
  const ms = data.models || [];
  sel.innerHTML = `<option value="">choose a model…</option>` + ms.map(m => {
    const meta = [m.free ? "free" : (m.price_out != null ? `$${m.price_in}/$${m.price_out}` : ""),
                  m.context ? Math.round(m.context/1000) + "k" : ""].filter(Boolean).join(" · ");
    return `<option value="${esc(m.id)}">${esc(m.id)}${meta ? "  ("+esc(meta)+")" : ""}</option>`;
  }).join("");
  if (msg) msg.innerHTML = data.listed
    ? `${ms.length} models on <b>${esc(provider)}</b>. Choose one and Add — or star models in the catalog below.`
    : data.error
      ? `Couldn't list <b>${esc(provider)}</b>: <span style="color:var(--bad)">${esc(data.error)}</span> — showing its defaults only.`
      : `No live catalog for <b>${esc(provider)}</b> (only its defaults shown). Set its API key to list more.`;
}

async function addPinnedModel(){
  const provider = document.getElementById("add-prov")?.value;
  const model = document.getElementById("add-model")?.value;
  if (!provider || !model) return;
  await pinModel(provider, model, "pin");   // 刷新；该行出现在列表中
}

async function pinModel(provider, model, action){
  const r = await postJSON("/api/pin", {provider, model, action});
  if (!r.error){ editing = false; await refresh(); }
}

// --- 模型页面：提供商卡网格（徽标、名称、状态点、操作）
// 加上一个编辑模式。状态是派生的，从不存储：未配置=无密钥，
// 配置 = 按键设置但已禁用，启用 = 按键设置且可用。这
// 无法禁用 ACTIVE 提供程序 (settings.provider)（服务器防护也是如此）。
function providerCardStatus(p, st){
  const keySet = !!(p.fields && p.fields[0] && p.fields[0].configured);
  if (!keySet) return "unconfigured";
  return (st.disabled_providers || []).includes(p.key) ? "configured" : "enabled";
}

function modelsGrid(d){
  const st = d.settings || {};
  const rank = p => p.key === st.provider ? 0
    : providerCardStatus(p, st) === "enabled" ? 1
    : providerCardStatus(p, st) === "configured" ? 2 : 3;
  const providers = (d.providers || []).slice()
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  return `<div class="provgrid">` + providers.map(p => providerCard(p, st)).join("") +
    `</div><div id="prov-modal-root"></div>`;
}

function providerCard(p, st){
  const status = providerCardStatus(p, st);
  const current = p.key === st.provider;
  const dot = status === "enabled" ? "var(--good)" : status === "configured" ? "#4c9aff" : "var(--bad)";
  return `<div class="provcard" data-provider="${esc(p.key)}">
    ${current ? `<span class="srcpill prov-current" style="background:var(--good-soft);color:var(--good)">current</span>` : ""}
    <img class="provlogo" src="/static/logos/${esc(p.key)}.svg" alt="" onerror="this.style.display='none'">
    <div class="provname">${esc(p.name)}</div>
    <div class="provstatus"><span class="provdot" style="background:${dot}"></span>${status}</div>
    <div class="provactions">
      <button class="save ghost" onclick="openProviderModal('${esc(p.key)}')">edit</button>
      ${status === "configured" ? `<button class="save ghost" onclick="toggleProvider('${esc(p.key)}',false)">enable</button>` : ""}
      ${status === "enabled" && !current ? `<button class="save ghost" onclick="toggleProvider('${esc(p.key)}',true)">disable</button>` : ""}
    </div></div>`;
}

// 启用/禁用提供程序（网格按钮）。服务器保管密钥；这
// 提供者只需离开/进入可用列表。
async function toggleProvider(provider, disabled){
  const r = await postJSON("/api/providers", {provider, disabled});
  if (!r.ok) alert(r.error || "update failed");
  else { editing = false; await refresh(); }
}

// --- 编辑模式：API 密钥（+ 主/小模型，当该提供商当前存在时，
// 带有可搜索的实时目录）和“设置为当前”操作。
function openProviderModal(provider){
  markEditing();   // 防止 5 秒刷新循环擦除此模式
  const st = (D && D.settings) || {};
  const p = (D.providers || []).find(x => x.key === provider);
  if (!p) return;
  const current = provider === st.provider;
  const f = (p.fields || [])[0] || {};
  const baseField = (p.fields || []).find(field => field.name.endsWith("_BASE_URL"));
  const selectedBaseUrl = current && st.base_url ? st.base_url : (baseField?.value || "");
  const root = document.getElementById("prov-modal-root");
  root.innerHTML = `<div class="provmodal-back" onclick="closeProviderModal()">
    <div class="provmodal${current ? " provmodal-models" : ""}" onclick="event.stopPropagation()">
      <div class="u" style="display:flex;justify-content:space-between;align-items:center">
        <b>${esc(p.name)}</b><a class="reveal" onclick="closeProviderModal()">✕</a></div>
      <label class="fld"><span>API key <span class="meta">(${esc(f.name || "")})</span>
        ${f.configured ? `<span class="srcpill" style="background:var(--good-soft);color:var(--good)">set ····${esc(f.last4 || "")}</span>`
                       : `<span class="srcpill apple">not set</span>`}</span>
        <input type="password" id="pm-key" placeholder="${f.configured ? "key on file — blank keeps it" : "paste key"}"></label>
      ${baseField ? `<label class="fld"><span>Base URL <span class="meta">(select the API key's region)</span></span>
        <select id="pm-base-url" onfocus="markEditing()">
          ${(baseField.options || []).map((url, index) => {
            const label = (baseField.option_labels || [])[index];
            return `<option value="${escAttr(url)}" ${url===selectedBaseUrl?"selected":""}>${label?esc(label)+" — ":""}${esc(url)}</option>`;
          }).join("")}
        </select></label>` : ""}
      ${current ? `
      ${renderModelPicker("pm-model", "Main model (runs the loop; needs tool calling)", st.model || "")}
      ${renderModelPicker("pm-small-model", "Gate / summary model", st.small_model || "")}` : ""}
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="save" id="pm-save" onclick="saveProviderModal('${esc(provider)}')">Save</button>
        ${!current ? `<button class="save ghost" id="pm-make-current" onclick="makeCurrentProvider('${esc(provider)}')">Set as current provider</button>` : ""}
      </div>
      <span class="meta" id="pm-msg"></span>
    </div></div>`;
  if (current) loadModalModels(provider);
}

function closeProviderModal(){
  editing = false;
  const root = document.getElementById("prov-modal-root");
  if (root) root.innerHTML = "";
}

// 根据一个请求填充两个模式选择器：该提供商的实时目录，
// 或没有目录时的默认值。手动打字仍然有效。
async function loadModalModels(provider){
  setupModelPickers([], provider);
  setModelPickerMeta("Loading models…");
  let data;
  try {
    const response = await fetch("/api/models?provider=" + encodeURIComponent(provider));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch(e){
    data = {models: [], listed: false, error: e.message || String(e)};
  }
  setupModelPickers(data.models || [], provider);
  if (!data.listed){
    setModelPickerMeta(data.error && !(data.models || []).length
      ? "Could not load catalog — you can still type any model id."
      : data.error
        ? "Could not load catalog — showing defaults only."
      : "Live catalog unavailable — showing defaults.");
  }
}

// 当前打开模态的共享模型列表。
let _modalModels = [];
let _activeModelPicker = null;
let _outsidePickerListener = false;

function escAttr(s){
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function renderModelPicker(id, label, value){
  return `<label class="fld">${esc(label)}
    <div class="model-picker" id="${escAttr(id)}-picker">
      <div class="model-picker-input">
        <input type="text" id="${escAttr(id)}" value="${escAttr(value || "")}" autocomplete="off" onfocus="markEditing()" onclick="event.stopPropagation()">
        <button type="button" class="model-picker-toggle" onclick="toggleModelPicker('${escAttr(id)}'); event.stopPropagation();" aria-label="toggle models" aria-controls="${escAttr(id)}-list" aria-expanded="false">▾</button>
      </div>
      <div class="model-picker-list" id="${escAttr(id)}-list" role="listbox">
        <input type="text" class="model-picker-search" id="${escAttr(id)}-search" placeholder="filter models..." autocomplete="off" aria-label="filter models" oninput="filterModelPicker('${escAttr(id)}')" onfocus="markEditing()" onclick="event.stopPropagation()">
        <div class="model-picker-items" id="${escAttr(id)}-items"></div>
        <div class="model-picker-meta" id="${escAttr(id)}-meta" aria-live="polite"></div>
      </div>
    </div>
  </label>`;
}

function setupModelPickers(models, provider){
  _modalModels = Array.isArray(models) ? models : [];
  ["pm-model", "pm-small-model"].forEach(id => {
    const input = document.getElementById(id);
    if (!input) return;
    const itemsBox = document.getElementById(id + "-items");
    const search = document.getElementById(id + "-search");
    if (itemsBox && !itemsBox.dataset.modelPickerBound){
      itemsBox.dataset.modelPickerBound = "true";
      itemsBox.addEventListener("click", e => {
        const item = e.target.closest(".model-picker-item");
        if (!item) return;
        e.stopPropagation();
        selectModelPicker(id, item.dataset.model || "");
      });
      search?.addEventListener("keydown", e => {
        if (e.key !== "Enter") return;
        const query = search.value.toLowerCase();
        const first = _modalModels.find(m => (m.id || "").toLowerCase().includes(query));
        if (!first) return;
        e.preventDefault();
        selectModelPicker(id, first.id || "");
      });
    }
    renderModelPickerItems(id, (search?.value || "").toLowerCase());
  });
  if (!_outsidePickerListener){
    _outsidePickerListener = true;
    document.addEventListener("click", e => {
      if (!e.target.closest?.(".model-picker")) closeAllModelPickers();
    }, true);
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeAllModelPickers(); });
  }
}

function setModelPickerMeta(message){
  ["pm-model", "pm-small-model"].forEach(id => {
    const meta = document.getElementById(id + "-meta");
    if (meta) meta.textContent = message;
  });
}

function toggleModelPicker(id){
  const list = document.getElementById(id + "-list");
  if (!list) return;
  const isOpen = list.classList.contains("open");
  closeAllModelPickers();
  if (!isOpen){
    list.classList.add("open");
    list.parentElement.querySelector(".model-picker-toggle")?.setAttribute("aria-expanded", "true");
    _activeModelPicker = id;
    const search = document.getElementById(id + "-search");
    if (search) search.focus();
  }
}

function closeAllModelPickers(){
  document.querySelectorAll(".model-picker-list.open").forEach(el => {
    el.classList.remove("open");
    el.parentElement.querySelector(".model-picker-toggle")?.setAttribute("aria-expanded", "false");
  });
  _activeModelPicker = null;
}

function closeModelPicker(id){
  const list = document.getElementById(id + "-list");
  if (list){
    list.classList.remove("open");
    list.parentElement.querySelector(".model-picker-toggle")?.setAttribute("aria-expanded", "false");
  }
  if (_activeModelPicker === id) _activeModelPicker = null;
}

function filterModelPicker(id){
  const query = (document.getElementById(id + "-search")?.value || "").toLowerCase();
  renderModelPickerItems(id, query);
}

function renderModelPickerItems(id, query){
  const itemsBox = document.getElementById(id + "-items");
  const metaBox = document.getElementById(id + "-meta");
  if (!itemsBox) return;
  const filtered = _modalModels.filter(m => (m.id || "").toLowerCase().includes(query));
  itemsBox.innerHTML = filtered.map((m, index) => `<div class="model-picker-item${index === 0 ? " active" : ""}" role="option" data-model="${escAttr(m.id)}">${esc(m.id)}</div>`).join("");
  if (metaBox){
    if (_modalModels.length === 0) metaBox.textContent = "No models loaded — you can still type any model id.";
    else if (filtered.length === 0) metaBox.textContent = `No models match "${query}".`;
    else metaBox.textContent = "";
  }
}

function selectModelPicker(id, value){
  const input = document.getElementById(id);
  if (input){
    input.value = value;
    input.focus();
  }
  closeModelPicker(id);
}

function modalKeyPayload(provider){
  const key = document.getElementById("pm-key")?.value;
  const payload = {provider};
  if (key) payload.key = key;
  const baseUrl = document.getElementById("pm-base-url")?.value;
  if (baseUrl) payload.base_url = baseUrl;
  return payload;
}

function setProviderModalBusy(activeId, busy){
  ["pm-save", "pm-make-current"].forEach(id => {
    const button = document.getElementById(id);
    if (!button) return;
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = busy;
    button.textContent = busy && id === activeId
      ? (id === "pm-make-current" ? "Switching…" : "Saving…")
      : button.dataset.label;
  });
  if (busy){
    const msg = document.getElementById("pm-msg");
    if (msg) msg.textContent = activeId === "pm-make-current"
      ? "Switching provider…" : "Saving and validating changes…";
  }
}

async function submitProviderModal(provider, payload, activeId){
  setProviderModalBusy(activeId, true);
  let r;
  try { r = await postJSON("/api/providers", payload); }
  catch(e){ r = {ok:false, error:e.message || String(e)}; }
  if (!r.ok){
    setProviderModalBusy(activeId, false);
    const msg = document.getElementById("pm-msg");
    if (msg) msg.textContent = r.error || "update failed";
    return;
  }
  editing = false;
  closeProviderModal();
  await refresh();
}

async function saveProviderModal(provider){
  const st = (D && D.settings) || {};
  const payload = modalKeyPayload(provider);
  payload.activate = false;
  if (provider === st.provider){
    payload.model = document.getElementById("pm-model")?.value ?? "";
    payload.small_model = document.getElementById("pm-small-model")?.value ?? "";
  }
  await submitProviderModal(provider, payload, "pm-save");
}

// “设置为当前”：apply_provider 切换提供程序并选择其默认值
// 未传递任何内容时的模型（如果刚刚键入，则保留关键字段）。
async function makeCurrentProvider(provider){
  await submitProviderModal(provider, modalKeyPayload(provider), "pm-make-current");
}
