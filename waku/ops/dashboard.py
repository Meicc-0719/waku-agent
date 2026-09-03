"""Dashboard — every pillar on one local page. Zero new dependencies.

    make dashboard        # → http://localhost:7777

One stdlib HTTP server reading the files Waku already writes:
  loop + harness   traces/*.jsonl   (turns, gate decisions, tool calls, tokens)
  memory           state.db         (facts, episodes, chat log, consolidation)
  tools            state.db + calendar.ics + outbox/
  eval             eval_report.json (written by `make gate`)

The overview mirrors the architecture diagram — every box is clickable and
opens that section's live data. The chat dock is a real gateway: type (or speak)
a message and watch the same harness (gate, loop, tools, memory) that the CLI/
voice/telegram gateways drive light up in the browser as it runs.

The frontend is plain static files (static/index.html + style.css + app.js)
served as-is — no build step, no framework. This file is just the server + API.
Bound to 127.0.0.1 only. For deep trace waterfalls use Phoenix (`make trace`).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from waku.config import load_settings
from waku.db import connect
from waku.integrations import (
    apply_integration,
    apply_provider,
    apply_provider_disabled,
    list_connections,
    list_providers,
    test_integration,
)
from waku.ops import browser_agent, commands, compare_history
from waku.ops.arena import (
    compare_clear,
    compare_delete_run,
    compare_regrade,
    compare_stream,
    history_response,
)
from waku.ops.browser_agent import agent_lock, dash_session, get_agent, maybe_rotate_session
from waku.ops.catalog import list_models
from waku.ops.pricing import price_for, usage_summary
from waku.ops.settings_api import apply_settings, pin_action, settings_info
from waku.ops.tracing import TraceEncodingError, iter_trace_lines

PORT = 7777
# 前端位于自己的文件中（static/index.html + style.css + app.js），
# 此 stdlib 服务器按原样提供服务 — 没有构建步骤，没有框架。编辑那些
# to change the UI;编辑此文件以更改服务器/API。
STATIC = Path(__file__).resolve().parent / "static"


def chat(message: str) -> dict:
    """One turn, one JSON result — the non-streaming door to the same room.

    The dashboard itself uses /api/chat/stream; this exists for scripts and for
    `curl`. It deliberately does NOT reimplement the turn: it drives chat_stream
    and keeps the final "done" payload, because the two used to be separate
    copies of the same 25 lines and had already drifted (the streaming one
    reported which model answered, this one didn't). One implementation means
    they cannot disagree again.
    """
    final: dict = {}

    def collect_done(kind: str, ev: dict) -> None:
        if kind == "done":
            final.update(ev)

    chat_stream(message, collect_done)
    return final


def chat_stream(message: str, emit) -> None:
    """Run one turn, calling emit(kind, event) for every harness event AS it
    happens — gate decision, tool calls, and the reply text token by token —
    so the browser can show thinking stream in (like the CLI/voice do). Ends
    with a 'done' event carrying the final structured result.

    A leading slash calls a graph workflow BY NAME instead of running a turn.
    Both doors end in the same 'done' event, so the chat renders the answer the
    same way whether the harness routed it or you named the shape yourself."""
    command = commands.parse(message)
    if command is not None:
        _run_command(command, emit)
        return

    events: list[dict] = []

    def observer(kind, ev):
        if kind in ("gate", "consolidation", "route", "triage"):
            events.append({"kind": kind, **ev})
        emit(kind, ev)

    with agent_lock:
        agent = get_agent()
        maybe_rotate_session(agent)
        start = datetime.now(UTC)
        result = agent.respond(message, observer=observer, source="dashboard", stream=True)
        latency_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)

    gate = next((e for e in events if e["kind"] == "gate"), None)
    cons = next((e for e in events if e["kind"] == "consolidation"), None)
    route = next((e for e in events if e["kind"] == "route"), None)
    triage = next((e for e in events if e["kind"] == "triage"), None)
    quick = bool(route) and route.get("target") == "quick_reply"
    emit("done", {
        "reply": result.reply,
        "gate": {"decision": gate["decision"], "reason": gate.get("reason")} if gate else None,
        "graph": ({"workflow": route.get("workflow", "triage"),
                   "route": "quick" if quick else "full",
                   "reason": (triage or {}).get("reason", "")} if route else None),
        "tools": [{"tool": c["tool"], "args": c["args"], "output": c["output"],
                   "status": _tool_status(c["output"]),
                   "summary": (c["output"] or "").split(". ")[0][:120]} for c in result.tool_calls],
        "consolidation": {"new_facts": cons["new_facts"]} if cons else None,
        "iterations": result.iterations,
        "latency_ms": latency_ms,
        # 哪个大脑回答了——每张卡片显示；快速图形转向是小模型
        "model": agent.settings.small_model if quick else agent.settings.model,
    })


# 名称 -> 跑步者表，永远不会动态导入浏览器发送的任何内容。
# “运行客户端命名的工作流程”是对“导入”的一次粗心重构
# 并调用到达的任何字符串”，因此间接寻址是有意的字典。
def WORKFLOW_RUNNERS() -> dict[str, str]:  # noqa: N802 — reads as a table
    """Discovered, not hand-listed. A hardcoded table and a slash-command list
    are two registries of the same fact, and they drift."""
    return commands.discover()


def graph_stream(payload: dict, emit) -> None:
    """Run a graph workflow, streaming its node events as SSE.

    Only the engine's own events go out — graph_start / node_start / node_end /
    route / graph_end. They already carry `workflow` and `node`, which is all a
    card needs, and they carry no node OUTPUT, so a digest can never leak into
    a frame. Unlike the Arena this needs no lock of its own: run_graph already
    serialises notify() behind one (engine.py), so events arrive whole.
    """
    name = (payload.get("workflow") or "").strip()
    target = WORKFLOW_RUNNERS().get(name)
    if target is None:
        emit("done", {"error": f"unknown workflow '{name}'"})
        return
    module_name, _, fn_name = target.partition(":")
    try:
        import importlib

        run = getattr(importlib.import_module(module_name), fn_name)
        state = run(observer=lambda kind, ev: emit(kind, ev))
        emit("done", {
            "workflow": name,
            "digest": (state.get("digest") or "")[:4000],
            "draft_path": state.get("draft_path", ""),
            "errors": state.get("errors") or {},
        })
    except Exception as exc:
        # 包括 GraphStateCollision，其中 run_graph 会引发 OUT（与节点不同）
        # 错误）——更好地显示在卡片上而不是掉在地板上。
        emit("done", {"error": f"{type(exc).__name__}: {exc}"})


def _run_command(command: tuple[str, str], emit) -> None:
    """Handle `/name` from the chat box.

    The node events go out exactly as the engine emits them, so the topology
    chart animates from the same trace poll that animates a normal turn — a
    named workflow lights the picture as readily as a routed one.
    """
    name, arg = command
    start = datetime.now(UTC)
    if name in ("graphs", "help", "?"):
        emit("done", {"reply": commands.describe(), "tools": [], "iterations": 0,
                      "latency_ms": 0, "gate": None})
        return
    try:
        state = commands.run(name, emit, arg)
    except Exception as exc:
        emit("done", {"reply": f"`/{name}` failed: {type(exc).__name__}: {exc}",
                      "tools": [], "iterations": 0, "latency_ms": 0, "gate": None})
        return
    if state is None:
        emit("done", {"reply": commands.unknown_reply(name), "tools": [],
                      "iterations": 0, "latency_ms": 0, "gate": None})
        return
    reply = state.get("digest") or "(the workflow produced no text)"
    if state.get("ignored_argument"):
        reply = (f"*`/{name}` takes no input, so \u201c{state['ignored_argument']}\u201d "
                 f"was not used — a fixed shape always fetches the same sources. "
                 f"Ask a normal question to use the loop instead.*\n\n") + reply
    if state.get("draft_path"):
        reply += f"\n\n*saved to `{state['draft_path']}`*"
    for node, err in (state.get("errors") or {}).items():
        reply += f"\n\n*{node}: {err}*"
    emit("done", {
        "reply": reply, "tools": [], "gate": None, "consolidation": None,
        "iterations": 0,
        "latency_ms": int((datetime.now(UTC) - start).total_seconds() * 1000),
        "workflow": name,
    })


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _tool_status(output: str) -> str:
    """Classify a tool result for the UI: ok / warn / error — from the output
    string alone (tools already report honestly, so trust their words)."""
    low = (output or "").lower()
    if "failed" in low or "timed out" in low or low.startswith("error"):
        return "error"
    if "already exists" in low or "not synced" in low or "skipped" in low:
        return "warn"
    return "ok"


# 概念支持的剧集通过网络直播，因此客户端和结果
# 使用短 TTL 进行缓存 —collect() 在每个仪表板自动刷新时运行
# 并且不得每隔几秒往返一次 Notion（速率限制 + 延迟）。
# sqlite 路径是本地查询，不需要这个。
_NOTION_EPISODES_TTL = 30.0   # 秒；页面轮询〜每 5 秒一次
_notion_lock = threading.Lock()
_notion_store = None                       # 构建一次（其构造函数调用 Notion）
_notion_episodes: tuple[float, list] | None = None   # （fetch_at，项目）


def invalidate_notion_cache() -> None:
    """Forget cached Notion clients/results after connection settings change."""
    global _notion_store, _notion_episodes
    with _notion_lock:
        _notion_store = None
        _notion_episodes = None


def _get_notion_store():
    """The ONE NotionEpisodeStore for the whole dashboard process. Its
    constructor round-trips to Notion (data-source resolution), so it's built
    lazily and cached. Callers must hold _notion_lock."""
    global _notion_store
    if _notion_store is None:
        from waku.memory.episodic.notion_store import NotionEpisodeStore

        _notion_store = NotionEpisodeStore()
    return _notion_store


def collect() -> dict:
    """Everything the page shows, in one JSON blob."""
    settings = load_settings()
    info = settings_info()
    settings.ensure_home()
    home = settings.home
    conn = connect(home)

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql).fetchall()]

    def episodes_payload() -> dict:
        """Episodes from the active backend: sqlite (default) or notion.
        A Notion outage must not take down the whole dashboard payload."""
        if settings.episodic_store != "notion":
            return {
                "source": "sqlite",
                "error": "",
                "items": rows(
                    "SELECT id, happened_at, summary FROM episodes ORDER BY happened_at DESC"
                ),
            }
        try:
            global _notion_episodes
            with _notion_lock:
                store = _get_notion_store()
                if _notion_episodes and time.time() - _notion_episodes[0] < _NOTION_EPISODES_TTL:
                    return {"source": "notion", "error": "", "items": _notion_episodes[1]}
                items = store.list()
                _notion_episodes = (time.time(), items)
                return {"source": "notion", "error": "", "items": items}
        except Exception as exc:
            # 优雅地降级：永远不要降低有效负载，并服务于
            # 如果我们有的话，最后一次良好的获取（中断不应使选项卡空白）。
            stale = _notion_episodes[1] if _notion_episodes else []
            return {"source": "notion", "error": str(exc), "items": stale}

    episodes_data = episodes_payload()

    # --- 轨迹 → 转弯（turn_start 和turn_end 之间的组事件）
    events = []
    trace_errors = []
    trace_files = sorted((home / "traces").glob("*.jsonl"))
    for path in trace_files:
        try:
            lines = list(iter_trace_lines(path))
        except TraceEncodingError as exc:
            trace_errors.append({"file": path.name, "error": str(exc)})
            continue
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    turns, current, wake_scans = [], None, []
    for ev in events:
        kind = ev.get("type")
        if kind == "turn_start":
            current = {"user_message": ev.get("user_message"), "ts": ev.get("ts"),
                       "gate": None, "llm_calls": [], "tools": [], "reply": None}
        elif kind == "wake_scan":
            wake_scans.append(ev)
        elif current is not None:
            if kind == "gate":
                current["gate"] = ev
            elif kind == "route":
                current["graph"] = {"workflow": ev.get("workflow"),
                                    "route": "quick" if ev.get("target") == "quick_reply" else "full",
                                    "reason": (current.get("graph") or {}).get("reason", "")}
            elif kind == "triage":
                current.setdefault("graph", {})["reason"] = ev.get("reason", "")
            elif kind == "llm":
                current["llm_calls"].append(ev)
            elif kind == "tool":
                current["tools"].append(ev)
            elif kind == "consolidation":
                current["consolidation"] = ev
            elif kind == "turn_end":
                current["reply"] = ev.get("reply")
                current["iterations"] = ev.get("iterations")
                turns.append(current)
                current = None
    if current is not None:  # 一个永无止境的转弯 = 悬吊的确凿证据
        current["reply"] = "TURN NEVER FINISHED — check for a hang after this point"
        current["unfinished"] = True
        turns.append(current)

    # --- 导出每轮延迟 + 美元成本（人类感受到的操作数）
    if settings.base_url or settings.provider == "openrouter":
        list_models()  # 预热每个模型的价格缓存（5 分钟缓存获取）
    price_in, price_out = price_for(settings.provider, settings.model or "")
    for t in turns:
        start, end = _parse_ts(t["ts"]), None
        last = t["llm_calls"][-1]["ts"] if t["llm_calls"] else None
        end = _parse_ts(last)
        t["latency_ms"] = int((end - start).total_seconds() * 1000) if start and end else None
        tin = sum(c.get("usage", {}).get("in", 0) for c in t["llm_calls"])
        tout = sum(c.get("usage", {}).get("out", 0) for c in t["llm_calls"])
        t["cost"] = tin / 1e6 * price_in + tout / 1e6 * price_out
        for x in t["tools"]:
            x["status"] = _tool_status(x.get("output", ""))
            x["summary"] = (x.get("output", "") or "").split(". ")[0][:120]

    latencies = sorted(t["latency_ms"] for t in turns if t["latency_ms"] is not None)
    total_cost = sum(t["cost"] for t in turns)

    def pct(p: float) -> int:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else 0

    from waku.memory import bundled_skill_dirs
    from waku.memory.procedural.loader import SkillLoader

    skills = [{"name": s.name, "description": s.description, "body": s.body,
               "path": str(s.path),
               # 相对路径（用于显示）+它是否位于可编辑的主目录中
               "rel": _rel_to_home(s.path, home),
               "editable": str((home / "skills").resolve()) in str(s.path.resolve())}
              for s in SkillLoader([*bundled_skill_dirs(), home / "skills"]).skills]

    eval_report = None
    report_path = home / "eval_report.json"
    if report_path.exists():
        eval_report = json.loads(report_path.read_text(encoding="utf-8"))

    eval_history = []
    hist_path = home / "eval_runs.jsonl"
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                eval_history.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    eval_history.reverse()

    outbox = [{"name": p.name, "text": p.read_text(encoding="utf-8")[:400]}
              for p in sorted((home / "outbox").glob("*.txt"), reverse=True)[:20]]

    # --- state.db自省：实际的SQLite表，因此持久化
    # 图层是可见的（不仅仅是其内容）。表名是硬编码的，所以
    # f 字符串 SQL 是安全的。
    def table_info(name):
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()
        cols = [r["name"] for r in info]
        types = {r["name"]: r["type"] for r in info}
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        # 最多 200 行最新行，因此每个表都有自己的可滚动选项卡
        sample = [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY rowid DESC LIMIT 200").fetchall()]
        return {"name": name, "columns": cols, "types": types, "count": count, "sample": sample}

    db_path = home / "state.db"
    all_tables = [r["name"] for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    db_info = {
        "path": str(db_path.resolve()),
        "size": db_path.stat().st_size if db_path.exists() else 0,
        "tables": [table_info(n) for n in ("calendar_events", "facts", "episodes", "chat_log")],
        "fts": [t for t in all_tables if t.endswith("_fts")],
        "all_tables": all_tables,
    }

    # 在不构建共享代理的情况下查看共享代理 - 页面加载永远不应该
    # 为尚未与任何人交谈过的代理付费。
    live = browser_agent.current()

    # --- 图形工作流程：直接来自引擎的拓扑（从不手绘，
    # 所以图片不会漂移）+从跟踪事件中快速/完全分离
    from waku.graph.workflows.gather import gather_topology
    from waku.graph.workflows.triage import triage_topology
    graph_routes = [e.get("target") for e in events if e.get("type") == "route"]
    # 最后几次完成的运行，最新的在前。概述需要这个，因为
    # 两个工作流程是具有不同触发器的两个不同作业 - 分类运行
    # 当您询问时，收集会在每条消息上运行 - 所以“哪个图表是
    # 现在相关”是一个只有痕迹才能回答的问题。
    # 固定的工作流程在收集运行后几秒钟内永远显示分类。
    graph_runs = [{"workflow": e.get("workflow"), "ms": e.get("ms"),
                   "at": e.get("ts"), "steps": e.get("steps"),
                   "path": e.get("path") or [], "error": e.get("error")}
                  for e in events if e.get("type") == "graph_end"][-8:][::-1]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home": str(home.resolve()),
        "provider": settings.provider,
        "model": info["model"],
        "stats": {
            "turns": len(turns),
            "tool_calls": sum(len(t["tools"]) for t in turns),
            "tool_errors": sum(1 for t in turns for x in t["tools"] if x["status"] == "error"),
            "gate_skips": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "skip"),
            "gate_retrieves": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "retrieve"),
            "tokens_in": sum(c.get("usage", {}).get("in", 0) for t in turns for c in t["llm_calls"]),
            "tokens_out": sum(c.get("usage", {}).get("out", 0) for t in turns for c in t["llm_calls"]),
            "cost": round(total_cost, 4),
            "latency_avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "latency_p95": pct(0.95),
            "trace_files": len(trace_files),
        },
        "turns": turns[::-1][:50],
        "wake_scans": wake_scans[::-1][:25],
        # 最后的原始跟踪行，因此 Ops 显示内联跟踪（不需要文件夹）
        "trace_tail": [{"type": e.get("type"), "ts": e.get("ts"),
                        "detail": (e.get("user_message") or e.get("decision") or e.get("tool")
                                   or e.get("reply") or "")}
                       for e in events[-18:]][::-1],
        "trace_file": (trace_files[-1].name if trace_files else None),
        "trace_errors": trace_errors,
        "facts": rows("SELECT id, subject, content, source, created_at FROM facts ORDER BY id DESC"),
        "episodes": episodes_data["items"],
        "episodes_source": episodes_data["source"],
        "episodes_error": episodes_data["error"],
        "soul": (home / "SOUL.md").read_text(encoding="utf-8") if (home / "SOUL.md").exists() else "",
        "chat_pending": conn.execute("SELECT COUNT(*) FROM chat_log WHERE consolidated=0").fetchone()[0],
        "chat_log": rows("SELECT role, content, consolidated, source, session_id, created_at FROM chat_log ORDER BY id DESC LIMIT 80")[::-1],
        "sessions": session_list(conn),
        "current_session": (live.session.session_id if live is not None else dash_session()),
        "consolidate_every": settings.consolidate_every,
        "calendar": rows('SELECT title, start, "end", attendees, created_at FROM calendar_events ORDER BY start'),
        "outbox": outbox,
        "skills": skills,
        "eval_report": eval_report,
        "eval_history": eval_history,
        "graph": {
            # 注意：“启用”门仅用于分类 — 每个消息的前门。
            # `waku Gather` 是您自己运行的例程，并忽略此标志
            # 完全如此，因此 UI 不得显示“关闭 = 不运行图表”。
            "enabled": settings.graph_workflows,
            "workflows": [triage_topology(), gather_topology()],
            "runs": graph_runs,
            "stats": {"quick": sum(1 for t in graph_routes if t == "quick_reply"),
                      "full": sum(1 for t in graph_routes if t == "full_agent")},
        },
        "db": db_info,
        "settings": info,
        "providers": [asdict(view) for view in list_providers()],
        "connections": [asdict(view) for view in list_connections()],
        "tools": tools_info(),
        "usage": usage_summary(home),
    }


def _rel_to_home(path, home) -> str:
    """Path relative to WAKU_HOME if it lives there, else the repo-relative
    'skills/...' path — either way something reveal_path can open."""
    try:
        return str(path.resolve().relative_to(home.resolve()))
    except ValueError:
        return str(path)


def session_list(conn) -> list[dict]:
    """One row per conversation for the chat-history picker: id, its first user
    message (the title), message count, newest first. Sessions are just a
    session_id label on chat_log rows — the same table, no new storage."""
    groups = conn.execute(
        """SELECT session_id, COUNT(*) AS messages, MAX(created_at) AS last_at
           FROM chat_log GROUP BY session_id ORDER BY last_at DESC"""
    ).fetchall()
    out = []
    for g in groups:
        sid = g["session_id"]
        first = conn.execute(
            "SELECT content FROM chat_log WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
            (sid,),
        ).fetchone()
        last = conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)
        ).fetchone()
        sources = [r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM chat_log WHERE session_id=?", (sid,)).fetchall()]
        preview = ""
        if last:
            preview = ("you: " if last["role"] == "user" else "waku: ") + last["content"][:80]
        out.append({"id": sid,
                    "title": (first["content"][:60] if first else "(empty)"),
                    "last": preview,
                    "sources": sources,
                    "messages": g["messages"],
                    "last_at": g["last_at"]})
    return out


# 工具的来源，用于在“工具”选项卡中分组（名称→类别）。
_FLAGSHIP = {"create_event", "list_events", "save_note", "send_message"}
_SELFMGMT = {"manage_memory", "update_soul", "create_skill"}
_APPLE = {"read_apple_calendar", "read_apple_mail", "create_reminder", "create_note"}
_WEB = {"search_web"}


def _tool_source(name: str, mcp_servers: list[str]) -> str:
    if name in _FLAGSHIP:
        return "flagship"
    if name in _WEB:
        return "web"
    if name in _SELFMGMT:
        return "self-management"
    if name in _APPLE:
        return "apple"
    if any(name.startswith(f"{s}_") for s in mcp_servers):
        return "mcp"
    return "other"


def tools_info() -> dict:
    """The agent's available tools + any configured MCP servers — so the Tools
    tab shows CAPABILITIES, not just the artifacts tool calls produced. Reflects
    the live agent's registry when one exists (exact), else builds a display-only
    catalog (no MCP subprocess is spawned just to render the page)."""
    settings = load_settings()
    settings.ensure_home()
    mcp = {"configured": False, "servers": [], "live": False}
    mcp_path = settings.home / "mcp.json"
    if mcp_path.exists():
        mcp["configured"] = True
        try:
            mcp["servers"] = [s.get("name", "?") for s in json.loads(mcp_path.read_text(encoding="utf-8")).get("servers", [])]
        except (json.JSONDecodeError, OSError):
            pass

    catalog = []
    live = browser_agent.current()
    if live is not None:
        mcp["live"] = getattr(live, "mcp_bridge", None) is not None
        tools = list(live.tools._tools.values())
    else:
        # 仅显示：相同的工具减去 MCP（构建真实的注册表将
        # 启动 MCP 服务器，我们不希望进行 5 秒轮询）。
        from waku.memory import Memory
        from waku.tools import calendar, memory_admin, messages, notes, search

        conn = connect(settings.home)
        try:
            # 概念模式：重用仪表板的一个缓存客户端而不是
            # 让 Memory() 在每次民意调查中构建一个新的民意调查（第 20 期）。
            episode_store = None
            if settings.episodic_store == "notion":
                with _notion_lock:
                    episode_store = _get_notion_store()
            mem = Memory(conn, settings, None, episode_store=episode_store)
        except Exception:
            # 配置错误的可选后端（概念/supabase）不得采用
            # 仪表板向下 — 从仪表板中删除内存管理工具
            # 改为仅显示目录。
            mem = None
        tools = [calendar.make_tool(
                     conn,
                     settings.home,
                     apple_calendar=settings.apple_calendar,
                     google_calendar=settings.google_calendar,
                     google_calendar_id=settings.google_calendar_id,
                 ),
                 calendar.make_list_tool(conn),
                 notes.make_tool(conn), messages.make_tool(settings.home),
                 search.make_tool(),
                 memory_admin.make_update_soul_tool(settings)]
        if mem is not None:
            tools += [memory_admin.make_manage_memory_tool(mem),
                      memory_admin.make_create_skill_tool(settings, mem)]
        if settings.apple_tools:
            from waku.tools import apple

            tools += apple.make_tools()
        if settings.experimental:
            # 镜像 build_registry：如果没有这个，目录就会在你之后
            # 翻转实验开关 - delegate_task 丢失，直到
            # 第一个聊天回合构建了真正的代理，所以它看起来像
            # 开关什么也没做。
            from waku.tools import experimental as experimental_tools

            tools += experimental_tools.make_tools(settings)
    for t in tools:
        catalog.append({"name": t.name, "description": t.description,
                        "source": _tool_source(t.name, mcp["servers"])})
    catalog.sort(key=lambda c: (c["source"], c["name"]))
    from waku.tools.experimental import PLANNED

    return {"catalog": catalog, "mcp": mcp, "apple_on": settings.apple_tools,
            "planned": PLANNED}   # 白板盒尚未接线（即将推出）


def run_query(payload: dict) -> dict:
    """A tiny read-only SQL console (the Supabase-editor idea, scoped down).
    Opens state.db in read-only mode so a write can't slip through, and only
    accepts a single SELECT/WITH statement. Caps at 200 rows."""
    sql = (payload.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return {"error": "Type a SELECT query."}
    low = sql.lower()
    if not (low.startswith(("select", "with"))):
        return {"error": "Only SELECT (or WITH … SELECT) queries are allowed."}
    if ";" in sql:
        return {"error": "One statement at a time (no semicolons)."}
    import sqlite3

    settings = load_settings()
    settings.ensure_home()
    db = (settings.home / "state.db").resolve()
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = [[str(r[i]) if r[i] is not None else "" for i in range(len(cols))]
                for r in cur.fetchmany(200)]
        c.close()
        return {"columns": cols, "rows": data}
    except sqlite3.Error as exc:
        return {"error": str(exc)}


_whisper = None
_whisper_lock = threading.Lock()


def transcribe_audio(raw: bytes) -> dict:
    """Server-side speech-to-text for the dashboard mic button — the SAME local
    Whisper (`make voice` uses it), so voice works in the browser without any
    cloud. Needs the [voice] extra. Returns {text} or a friendly {error}."""
    if not raw:
        return {"error": "no audio received"}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"error": "voice isn't installed — run: pip install -e '.[voice]'"}
    global _whisper
    import os as _os
    import tempfile

    with _whisper_lock:
        if _whisper is None:
            _whisper = WhisperModel(os.getenv("WAKU_WHISPER_MODEL", "base"), compute_type="int8")
    # 浏览器发送 WAV (PCM) — Whisper/PyAV 对其进行可靠解码 (WebM/Opus
    # 来自 MediaRecorder 的解码经常失败）。
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
    try:
        segments, _ = _whisper.transcribe(tmp.name)
        return {"text": " ".join(s.text for s in segments).strip()}
    except Exception as exc:
        return {"error": f"transcription failed: {exc}"}
    finally:
        try:
            _os.unlink(tmp.name)
        except OSError:
            pass


def _thread_history(conn, sid: str) -> list[dict]:
    """The ONE way to load a thread for the chat dock: role + content + the
    per-turn meta (gate/stats/tools/model) so every card renders in full.
    id '__all__' returns the whole cross-thread timeline (like the Loop tab,
    but as chat). Every history-loading path goes through here so they can't
    drift apart (they used to: 'switch' dropped meta and showed only text)."""
    if sid == "__all__":
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log ORDER BY id DESC LIMIT 200"
        ).fetchall()[::-1]
    else:
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log WHERE session_id=? ORDER BY id",
            (sid,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"],
             "meta": json.loads(r["meta"]) if r["meta"] else None} for r in rows]


def session_action(payload: dict) -> dict:
    """Chat history control: start a new conversation, switch to a past one, or
    read a conversation's history (read-only, for the live inbox). Sessions live
    in chat_log."""
    action = payload.get("action")
    if action == "history":
        # 对话的只读视图 - 永远不会触及代理，因此
        # 仪表板可以实时轮询（例如显示新的 Telegram 消息到达）。
        settings = load_settings()
        settings.ensure_home()
        conn = connect(settings.home)
        sid = payload.get("id") or "default"
        return {"ok": True, "session_id": sid, "history": _thread_history(conn, sid)}
    with agent_lock:
        agent = get_agent()
        if action == "new":
            sid = datetime.now().strftime("s-%Y%m%d-%H%M%S")
            agent.session.start_new(sid)
            return {"ok": True, "session_id": sid, "history": []}
        if action == "switch":
            sid = payload.get("id") or "default"
            agent.session.switch(sid)
            # 与只读“历史”操作相同的元丰富行，因此
            # 切换线程渲染其完整回合卡（gate/stats/tools/
            # 模型）——不仅仅是文本。 （这两条道路过去常常不一致。）
            return {"ok": True, "session_id": sid, "history": _thread_history(agent.conn, sid)}
    return {"error": f"unknown action {action}"}


def _editor_cmd() -> list[str] | None:
    """The user's code editor CLI: $WAKU_EDITOR, then cursor, then code."""

    custom = os.getenv("WAKU_EDITOR")
    if custom and shutil.which(custom):
        return [custom]
    for cli in ("cursor", "code"):
        if shutil.which(cli):
            return [cli]
    return None


def reveal_path(rel: str) -> dict:
    """Open a file/folder under WAKU_HOME — in the user's code editor if one
    is on PATH (cursor/code/$WAKU_EDITOR), otherwise reveal in Finder.
    Restricted to paths inside WAKU_HOME."""
    import subprocess
    import sys

    settings = load_settings()
    settings.ensure_home()
    home = settings.home.resolve()
    target = (home / (rel or ".")).resolve()
    if target != home and home not in target.parents:
        return {"error": "path is outside the .waku home"}
    if not target.exists():
        return {"error": f"not found: {target}"}

    editor = _editor_cmd()
    if editor and target.is_file() and target.suffix != ".db":  # 编辑们被 sqlite 噎住了
        subprocess.run([*editor, str(target)], check=False)
        return {"ok": True, "opened_in": editor[0], "path": str(target)}
    if sys.platform != "darwin":
        return {"error": f"no editor found and reveal is macOS-only — the path is {target}"}
    subprocess.run(
        ["open", "-R", str(target)] if target.is_file() else ["open", str(target)],
        check=False,
    )
    return {"ok": True, "revealed": str(target)}


def memory_action(payload: dict) -> dict:
    """Human CRUD on memory from the dashboard: update/delete facts & episodes,
    rewrite SOUL.md. Writes the same sqlite file the agent uses (busy_timeout
    covers contention); changes are live for the next agent turn."""
    from waku.memory.episodic.store import SqliteEpisodeStore
    from waku.memory.semantic.store import SqliteFactStore

    settings = load_settings()
    settings.ensure_home()
    action = payload.get("action")
    if action == "save_soul":
        text = (payload.get("content") or "").strip()
        if not text:
            return {"error": "SOUL cannot be empty"}
        (settings.home / "SOUL.md").write_text(text + "\n")
        return {"ok": True}
    if action == "save_skill":
        # 手动编辑任何加载的 SKILL.md（与代理的 create_skill 相同的文件
        # 写道）——回购技能和家庭技能一样。沙盒到两个
        # 技能文件夹；在写作之前验证 frontmatter。
        from pathlib import Path

        from waku.memory import bundled_skill_dirs
        from waku.memory.procedural.loader import _parse_text

        text = (payload.get("content") or "").strip()
        dest = Path(payload.get("path") or "").resolve()
        allowed = [d.resolve() for d in bundled_skill_dirs()] + [(settings.home / "skills").resolve()]
        if dest.name != "SKILL.md" or not any(a in dest.parents for a in allowed):
            return {"error": "can only edit SKILL.md files inside the skills folders"}
        if _parse_text(text, dest) is None:
            return {"error": "invalid SKILL.md — needs a name and description in the frontmatter"}
        dest.write_text(text.rstrip() + "\n", encoding="utf-8")
        return {"ok": True}

    conn = connect(settings.home)
    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    if action == "delete_episode" and settings.episodic_store == "notion":
        global _notion_episodes
        with _notion_lock:
            ok = _get_notion_store().delete(str(payload.get("id", "")))
            # 破坏 TTL 缓存，以便下一个collect()重新获取——否则
            # 已删除的剧集将在页面上停留长达 30 秒
            _notion_episodes = None
        return {"ok": ok}
    try:
        rid = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return {"error": "bad id"}
    if action == "update_fact":
        return {"ok": facts.update(rid, payload.get("content", ""), payload.get("subject") or None)}
    if action == "delete_fact":
        return {"ok": facts.delete(rid)}
    if action == "delete_episode":
        return {"ok": episodes.delete(rid)}
    return {"error": f"unknown action {action}"}




def events_since(cursor):
    """New trace events past `cursor` (a line count in today's trace file).
    Any gateway — browser, CLI, voice, Telegram — appends to this same file,
    so the live diagram lights up for all of them. cursor=None returns just
    the current tail so the browser starts fresh instead of replaying history."""
    settings = load_settings()
    settings.ensure_home()
    path = settings.home / "traces" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    if not path.exists():
        return {"events": [], "cursor": 0}
    try:
        lines = list(iter_trace_lines(path))
    except TraceEncodingError as exc:
        return {"events": [], "cursor": 0, "error": str(exc)}
    if cursor is None or cursor < 0 or cursor > len(lines):
        return {"events": [], "cursor": len(lines)}
    out = []
    for ln in lines[cursor:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return {"events": out, "cursor": len(lines)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, *, no_cache: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 前端文件（app.js/style.css）随着我们的开发而变化；没有
        # 浏览器提供陈旧的缓存副本，并且编辑看起来“丢失”。
        if no_cache:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/data":
            self._send(json.dumps(collect(), default=str).encode(), "application/json")
        elif self.path == "/api/compare/history":
            runs = compare_history.load_runs(load_settings().home)
            self._send(json.dumps(history_response(runs)).encode(), "application/json")
        elif self.path.startswith("/api/memory-arena/stores"):
            # 每个配置的商店现在拥有什么。仅按需提供 —
            # 这里的每个托管后端都是实时往返和仪表板
            # 因打开标签页而向您收取费用的产品并不需要发货。
            from urllib.parse import parse_qs, urlparse

            from waku.ops import memory_arena

            try:
                q = parse_qs(urlparse(self.path).query)
                # “查看全部”要求查看完整的一家商店。它曾经是一个链接
                # 内存页面，它只渲染 sqlite - 所以点击
                # mem0 下的它向您展示了 waku 的本地事实，但什么也没说。
                only = (q.get("store", [""])[0] or "").strip()
                limit = 500 if only else 8
                # 跟踪和模型识别哪个竞技场首页要读取 sqlite，
                # 所以这些牌会比较相同的种子，而不是把
                # 实时代理在基准运行旁边的实际使用数月。
                # 与比赛相同的路径安全规则：只有探针设置此规则
                # 服务器提供的路径，而不是浏览器提供的路径。
                from pathlib import Path as _P

                wanted = (q.get("probes", [""])[0] or "").strip()
                hit = next((s for s in memory_arena.probe_sets() if s["id"] == wanted), None)
                fixture = memory_arena.load_fixture(_P(hit["path"])) if hit else None
                track = hit["track"] if hit else (q.get("track", [""])[0] or "").strip()
                model = (q.get("model", [""])[0] or "").strip()
                self._send(json.dumps(memory_arena.store_contents(
                    limit, only, track=track, model=model, fixture=fixture)).encode(),
                    "application/json")
            except Exception as exc:
                self._send(json.dumps([{"store": "?", "error": f"{type(exc).__name__}: {exc}"}]).encode(),
                           "application/json")
        elif self.path.startswith("/api/memory-arena?") or self.path == "/api/memory-arena":
            # 烘焙赛装置，因此竞技场的“内存”选项卡可以显示内容
            # 在运行之前被询问。它存在于 evals/ 中，
            # 轮子不运送 — pip 安装的 Waku 回答为
            # `available: false` 而不是对从未存在过的文件进行 500 处理
            # 本来就应该在那里。
            from waku.ops import memory_arena

            try:
                from pathlib import Path as _P
                from urllib.parse import parse_qs, unquote, urlparse

                wanted = unquote(parse_qs(urlparse(self.path).query).get("probes", [""])[0]).strip()
                sets = memory_arena.probe_sets()
                hit = next((s for s in sets if s["id"] == wanted), None)
                payload = {"available": True, "backends": memory_arena._available_backends(),
                           "sets": sets, "chosen": hit["id"] if hit else "",
                           "models": memory_arena.arena_models(),
                           **memory_arena.load_fixture(_P(hit["path"]) if hit else None)}
                self._send(json.dumps(payload).encode(), "application/json")
            except (OSError, ValueError):
                self._send(json.dumps({"available": False}).encode(), "application/json")
        elif self.path.startswith("/api/models"):
            from urllib.parse import parse_qs, urlparse

            prov = parse_qs(urlparse(self.path).query).get("provider", [None])[0]
            self._send(json.dumps(list_models(prov)).encode(), "application/json")
        elif self.path.startswith("/api/events"):
            from urllib.parse import parse_qs, urlparse

            raw = parse_qs(urlparse(self.path).query).get("cursor", [None])[0]
            cursor = int(raw) if raw and raw.lstrip("-").isdigit() else None
            self._send(json.dumps(events_since(cursor)).encode(), "application/json")
        elif self.path.startswith("/api/reveal"):
            from urllib.parse import parse_qs, unquote, urlparse

            rel = unquote(parse_qs(urlparse(self.path).query).get("path", [""])[0])
            self._send(json.dumps(reveal_path(rel)).encode(), "application/json")
        elif self.path.startswith("/static/"):
            self._serve_static(self.path)
        else:
            self._send((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, path: str) -> None:  # 前端文件
        name = path.split("/static/", 1)[1].split("?")[0]
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ctype = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
                 ".html": "text/html; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype, no_cache=True)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        # /api/voice 采用原始音频 blob，而不是 JSON — 首先处理它。
        if self.path == "/api/voice":
            raw = self.rfile.read(length)
            self._send(json.dumps(transcribe_audio(raw)).encode(), "application/json")
            return
        # /api/chat/stream 在回合运行时流利用事件 (SSE)。
        if self.path == "/api/chat/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            message = (payload.get("message") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # 浏览器中途导航离开——很好

            if not message:
                emit("done", {"error": "empty message"})
                return
            try:
                chat_stream(message, emit)
            except Exception as exc:  # 表面作为终端事件，不要 500
                emit("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        # /api/compare/stream 与多个模型进行竞赛，在每个结果落地时发出它。
        if self.path == "/api/compare/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            try:
                compare_stream((payload.get("message") or "").strip(), payload.get("models") or [],
                               emit, judge=bool(payload.get("judge")), coding=bool(payload.get("coding")),
                               judge_spec=(payload.get("judge_model") or ""), apple=bool(payload.get("apple")))
            except Exception as exc:
                emit("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        # /api/memory-arena/stream — 与上面的模型竞赛形状相同，一个表盘
        # over：每个参赛者都是同一个模特的同一个经纪人，并且只是
        # 语义存储发生变化。
        if self.path == "/api/memory-arena/clean":
            # 仅删除比赛所写的内容：.waku-arena 的家园
            # 种子和 waku-arena-<key> 分区。无法到达直播
            # store 或 `waku` 分区，因为它从不要求它们
            # 姓名。与比赛相同的探针组验证 - 浏览器提供
            # 路径绝不能到达文件系统。
            from pathlib import Path as _P

            from waku.ops import memory_arena

            payload = json.loads(self.rfile.read(length) or "{}")
            wanted = (payload.get("probes") or "").strip()
            hit = next((s for s in memory_arena.probe_sets() if s["id"] == wanted), None)
            fixture = memory_arena.load_fixture(_P(hit["path"])) if hit else None
            track = hit["track"] if hit else (payload.get("track") or "")
            try:
                out = memory_arena.clean_stores(
                    track=track, model=(payload.get("model") or "").strip(), fixture=fixture)
            except Exception as exc:
                out = {"error": f"{type(exc).__name__}: {exc}"}
            self._send(json.dumps(out).encode(), "application/json")
            return
        if self.path == "/api/memory-arena/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit_mem(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            try:
                # 所选文件必须是该服务器提供的文件。回响着一个
                # 浏览器提供的路径直接进入 read_text() 将变成
                # 基准页面进入任意本地文件阅读器。
                from pathlib import Path as _P

                from waku.ops import memory_arena

                wanted = (payload.get("probes") or "").strip()
                hit = next((s for s in memory_arena.probe_sets() if s["id"] == wanted), None)
                fixture = memory_arena.load_fixture(_P(hit["path"])) if hit else None
                track = hit["track"] if hit else (payload.get("track") or "example")
                memory_arena.run_arena(payload.get("backends") or ["sqlite"],
                                       track, emit_mem, fixture=fixture,
                                       model=(payload.get("model") or "").strip(),
                                       seed_only=bool(payload.get("seed_only")))
            except Exception as exc:
                emit_mem("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        if self.path == "/api/graph/stream":
            payload = json.loads(self.rfile.read(length) or "{}")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            graph_stream(payload, emit)
            return
        routes = {"/api/chat": None, "/api/memory": memory_action, "/api/settings": apply_settings,
                  "/api/query": run_query, "/api/session": session_action, "/api/pin": pin_action,
                  "/api/connections": None, "/api/connections/test": None,
                  "/api/providers": None,
                  "/api/compare/clear": compare_clear,
                  "/api/compare/regrade": compare_regrade, "/api/compare/delete_run": compare_delete_run}
        if self.path not in routes:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.loads(self.rfile.read(length) or "{}")
        try:
            if self.path == "/api/chat":
                message = (payload.get("message") or "").strip()
                out = chat(message) if message else {"error": "empty message"}
            elif self.path == "/api/connections":
                result = apply_integration(payload.get("key", ""), payload.get("values") or {},
                                           tuple(payload.get("clear") or ()), force=bool(payload.get("force")))
                if result.ok and payload.get("key") == "notion":
                    invalidate_notion_cache()
                out = asdict(result)
            elif self.path == "/api/connections/test":
                out = asdict(test_integration(payload.get("key", "")))
            elif self.path == "/api/providers":
                # 仅切换可用性的有效负载进入启用/
                # 禁用路径；其他一切都是现有提供商适用的。
                if "disabled" in payload and set(payload) <= {"provider", "disabled"}:
                    out = asdict(apply_provider_disabled(payload.get("provider", ""),
                                                         disabled=bool(payload["disabled"])))
                else:
                    out = asdict(apply_provider(**payload))
            else:
                out = routes[self.path](payload)
        except Exception as exc:  # 表面，不要 500 — 浏览器显示它
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(json.dumps(out, default=str).encode(), "application/json")

    def log_message(self, *args):  # 保持终端安静
        pass


def main() -> None:
    # 端口优先级：WAKU_DASHBOARD_PORT，然后是常规 PORT（由
    # 部署平台和 IDE 预览窗格），然后是 7777。如果被占用，请继续。
    base = int(os.getenv("WAKU_DASHBOARD_PORT") or os.getenv("PORT") or PORT)
    for port in range(base, base + 10):  # 走过繁忙的港口而不是撞车
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            print(f"port {port} busy, trying {port + 1}…")
            continue
        # 网关生命周期的一个所有者：配置保存现在可以停止并
        # 在进程中重新启动机器人，而不需要重新启动仪表板。
        from waku.gateway.discord import start_in_background as start_discord
        from waku.gateway.supervisor import GatewaySupervisor
        from waku.gateway.telegram import start_in_background as start_telegram
        from waku.gateway.whatsapp import start_in_background as start_whatsapp
        from waku.integrations import (
            INTEGRATIONS,
            register_gateway_reloader,
            register_gateway_status_provider,
        )

        gateway_items = [item for item in INTEGRATIONS if item.reload.value == "gateway"]
        supervisor = GatewaySupervisor(
            {"telegram": start_telegram, "discord": start_discord, "whatsapp": start_whatsapp},
            {item.key: tuple(field.name for field in item.env) for item in gateway_items},
        )
        register_gateway_status_provider(supervisor.status)
        register_gateway_reloader(supervisor.reconcile)
        supervisor.reconcile()
        print(f"Waku dashboard → http://localhost:{port}  (Ctrl-C to stop)")
        try:
            server.serve_forever()
        finally:
            supervisor.shutdown()
        return
    raise SystemExit(f"no free port in {base}–{base + 9}")


if __name__ == "__main__":
    main()
