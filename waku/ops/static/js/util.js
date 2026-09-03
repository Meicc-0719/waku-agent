// waku 仪表板 — 转义、markdown、核心全局变量（D/编辑）、postJSON、reveal。
// 从 app.js 中分离出来：经典 <script>，共享全局范围（无构建
// 步骤，无模块）。加载顺序+规则：static/README.md。

// 也转义引号，而不仅仅是 &<>。文本节点从来不需要它，并且很长一段时间
// 没有任何东西将模型输出放入属性中 - 因此间隙是不可见的。
// 复制按钮（#58）是第一个发生的地方，然后是回复
// 包含一个双引号足以关闭 data-text="..." 并附加
// 它自己的事件处理程序。该模型的输出并不完全是我们的：search_web 和
// browser_web 从开放网络中提取文本，此仪表板保存内存，
// 痕迹和设置。每个呼叫者都向帮助者逃脱一次。
const esc = s => (s??"").toString().replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// --- 用于聊天回复的小型 Markdown 渲染器（无依赖性，XSS 安全：我们
// 首先转义，然后应用 LLM 实际使用的一小组转换：
// 粗体/斜体/代码、链接、有序/无序列表和表格）。
function mdInline(s){   // s 已经是 HTML 转义的
  return s
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|message:\/\/[^\s)]+)\)/g,
             (m, text, url) => `<a href="${url}" target="_blank" rel="noopener">${text}</a>`)
    .replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*_`])[*_]([^*_`\s][^*_`]*?)[*_](?![\w*])/g, "$1<em>$2</em>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>");
}
function renderMarkdown(text){
  const lines = esc(text).split(/\r?\n/);
  const row = l => /^\s*\|.*\|\s*$/.test(l);
  const sep = l => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  const out = [];
  let i = 0;
  while (i < lines.length){
    const l = lines[i];
    if (row(l) && i+1 < lines.length && sep(lines[i+1])){          // 桌子
      const head = cells(l); i += 2; const body = [];
      while (i < lines.length && row(lines[i])){ body.push(cells(lines[i])); i++; }
      out.push(`<table class="mdtable"><thead><tr>${head.map(h=>`<th>${mdInline(h)}</th>`).join("")}</tr></thead><tbody>${
        body.map(r=>`<tr>${r.map(c=>`<td>${mdInline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const h = l.match(/^\s*#{1,6}\s+(.*)$/);
    if (h){ out.push(`<div class="mdh">${mdInline(h[1])}</div>`); i++; continue; }
    if (/^\s*[-*]\s+/.test(l)){                                     // 无序列表
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])){ items.push(mdInline(lines[i].replace(/^\s*[-*]\s+/,""))); i++; }
      out.push(`<ul class="mdlist">${items.map(x=>`<li>${x}</li>`).join("")}</ul>`); continue;
    }
    if (/^\s*\d+\.\s+/.test(l)){                                    // 有序列表
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])){ items.push(mdInline(lines[i].replace(/^\s*\d+\.\s+/,""))); i++; }
      out.push(`<ol class="mdlist">${items.map(x=>`<li>${x}</li>`).join("")}</ol>`); continue;
    }
    if (/^\s*`{3,}/.test(l)){                                       // 围栏代码块
      const lang = l.replace(/^\s*`{3,}/, "").trim();
      i++;
      const codeLines = [];
      while (i < lines.length && !/^\s*`{3,}\s*$/.test(lines[i])){ codeLines.push(lines[i]); i++; }
      if (i < lines.length) i++;   // 跳过关闭```
      const langLabel = lang ? `<span class="mdcode-lang">${lang}</span>` : "";
      out.push(`<div class="mdcode"><div class="mdcode-head">${langLabel}<button class="mdcode-copy" onclick="copyCode(this)">Copy</button></div><pre><code>${codeLines.join("\n")}</code></pre></div>`);
      continue;
    }
    if (/^\s*[-*_]{3,}\s*$/.test(l)){ out.push("<hr class='mdhr'>"); i++; continue; } // hr
    if (/^\s*$/.test(l)){ i++; continue; }
    const para = [];                                                // 段落
    while (i < lines.length && lines[i].trim() && !/^\s*[-*]\s|^\s*\d+\.\s|^\s*#{1,6}\s/.test(lines[i])
           && !(row(lines[i]) && i+1<lines.length && sep(lines[i+1]))){
      para.push(mdInline(lines[i])); i++;
    }
    out.push(`<div class="mdp">${para.join("<br>")}</div>`);
  }
  return out.join("");
}
function copyCode(btn){
  const code = btn.closest(".mdcode").querySelector("pre code");
  navigator.clipboard.writeText(code.textContent).then(() => {
    const orig = btn.textContent;
    btn.textContent = "Copied!"; btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 2000);
  });
}
function copyMsg(btn){
  const text = btn.getAttribute("data-text") || "";
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "Copied!"; btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 2000);
  });
}
let D = null;

// 单击某个部分的数据可打开真实的本地文件/文件夹（编辑器或 Finder）。
function revealFile(p){ fetch("/api/reveal?path=" + encodeURIComponent(p)); }
const reveal = (path, label) => `<a class="reveal" onclick="revealFile('${path}')">${esc(label)}</a>`;

// --- 内存 CRUD（仪表板端）。 `editing` 暂停 5 秒重建，因此
// 正在进行的编辑不会被擦除（与动画防护相同的想法）。
let editing = false;
async function postJSON(url, body){ return (await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json(); }

// --- 共享行原子。
//
// 故意使用三个小片段，而不是一个大的 sessionRow()/pinnedRow()。
// 会话收件箱 (views.js) 和扩展坞的线程菜单 (dock.js) 绘制
// 真正不同的东西——选项卡中的卡片与下拉菜单中的项目——
// 因此，共享行组件需要为每个差异提供一个参数，并且
// 会比重复更糟糕。他们真正分享的是这三个
// 有关会话的事实，这些是漂移的部分：添加网关并
// 标签条在两个地方发生变化；更改日期格式和元行
// 两个地方的变化。这种情况在本仓库中已经发生过一次。

// 对话上的频道标签（网络/电报/语音/cli/discord）。
const gwTags = s => (s.sources||[]).map(src =>
  `<span class="gwtag ${esc(src)}">${esc(src)}</span>`).join("");

// "12 msg · 2026-07-26 21:56" — 会话的大小以及上次移动的时间。
const sessionMeta = s =>
  `${s.messages} msg · ${esc((s.last_at||"").slice(0,16).replace("T"," "))}`;

// 舞台带中的一种工具。由聊天底座的线束条共享
// (render.js) 和竞技场的每张卡条 (compare.js) — 这两个条带
// 故意不同（竞技场没有门/回复阶段并且
// 包装），但芯片本身在两个或同一个工具中必须看起来相同
// 调用似乎是两个不同的事情。
const toolChip = name => `<span class="stage done">tool · ${esc(name)}</span>`;
