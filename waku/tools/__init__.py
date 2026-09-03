"""The agent's tools. Flagship-task tools (calendar/notes/messages), memory
self-management (manage_memory/update_soul/create_skill), and opt-in adapters:
Apple ecosystem (WAKU_APPLE_TOOLS=1) and MCP servers (.waku/mcp.json)."""

from __future__ import annotations

import sqlite3

from waku.config import Settings
from waku.tools import calendar, memory_admin, messages, notes, search
from waku.tools.registry import ToolRegistry


def build_registry(conn: sqlite3.Connection, settings: Settings, memory=None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        calendar.make_tool(
            conn,
            settings.home,
            apple_calendar=settings.apple_calendar,
            google_calendar=settings.google_calendar,
            google_calendar_id=settings.google_calendar_id,
        )
    )
    # 读边：“我的日历上有什么？” — 一种工具可用于所有连接
    # 来源（登录时的 Google，加上 waku 自己的），因此该模型从来没有
    # 猜测用户指的是哪个日历。
    registry.register(calendar.make_list_tool(conn, settings.home))
    registry.register(notes.make_tool(conn))
    registry.register(messages.make_tool(settings.home))
    # 网络搜索 — 与 create_event 配对进行多工具循环演示
    # （“找到剩下的世界杯比赛并将它们添加到我的日历中”）。
    registry.register(search.make_tool())

    # 记忆自我管理——智能体可以纠正/忘记记忆，学习规则，
    # 并编写自己的技能（感觉就像个人代理，而不是黑匣子）。
    if memory is not None:
        registry.register(memory_admin.make_manage_memory_tool(memory))
        registry.register(memory_admin.make_update_soul_tool(settings))
        registry.register(memory_admin.make_create_skill_tool(settings, memory))

    # 实验工具——默认关闭；选择使用 WAKU_EXPERIMENTAL=1。
    # delegate_task（通过 pi 的子代理）已上线；终端/浏览器/cron 是
    # 仍然有骷髅报告“即将推出”。
    #
    # 信任设置。单独实验。 load_settings() 已经默认它
    # WAKU_EXPERIMENTAL，因此重新检查此处的环境将使全局切换
    # 覆盖一个显式的 False — 并且竞技场通过experimental=False for
    # 每场非编码竞赛。一旦仪表板可以写入 WAKU_EXPERIMENTAL=1，
    # 或者默默地迫使 delegate_task 进入从未要求它的竞赛。
    if getattr(settings, "experimental", False):
        from waku.tools import experimental

        for t in experimental.make_tools(settings):
            registry.register(t)

    # Apple 生态系统读者/作者（选择加入；首次使用会触发 macOS 提示）。
    if settings.apple_tools:
        from waku.tools import apple

        for t in apple.make_tools():
            registry.register(t)

    # 通过 gh CLI 的只读 GitHub（选择加入；使用 gh 自己的身份验证，此处没有令牌）。
    if getattr(settings, "gh_tool", False):
        from waku.tools import github

        registry.register(github.make_tool(default_repo=getattr(settings, "gh_repo", "")))

    # MCP 服务器（通过 .waku/mcp.json 选择加入）。
    mcp_config = settings.home / "mcp.json"
    if mcp_config.exists():
        try:
            from waku.tools.mcp_client import MCPBridge

            bridge = MCPBridge(mcp_config)
            for t in bridge.start():
                registry.register(t)
            registry.mcp_bridge = bridge  # 所以 Waku.close() 可以停止服务器
        except ImportError:
            print("mcp.json found but the 'mcp' package is missing — pip install 'waku-agent[mcp]'")

    return registry
