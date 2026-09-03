"""The browser gateway's agent — one Waku, shared by every tab.

The dashboard is a gateway like the CLI or Telegram: it only moves text. But
unlike those it is multi-threaded (a stdlib ThreadingHTTPServer) and long-lived
across page refreshes, so it needs two things the other gateways don't:

  ONE agent, built lazily on the first chat and reused, behind `agent_lock` so
  turns run one at a time. Correct for a single-user local tool, and required:
  the SQLite connection is shared across worker threads.

  ONE chat thread per dashboard RUN, dated, resumed on restart. Never the
  eternal "default" session — a returning user should see their conversation,
  not an infinite scroll of every chat they have ever had. `maybe_rotate_session`
  handles the other half of that: come back after an idle gap and you get a new
  thread, with the old one one click away in History. (Live bug this fixes: a
  tester came back days later and their new message landed in a week-old
  32-message thread.)

This lives in its own module because two callers mutate the singleton —
dashboard.py builds it for a chat, settings_api rebuilds it when you switch
provider — and a module global can only be rebound by the module that owns it.
Import the MODULE, not the name: `browser_agent.current()` sees the swap,
`from browser_agent import _agent` freezes None forever.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime

from waku.config import load_settings
from waku.db import connect

# 浏览器网关的一个共享代理。懒惰地构建（第一次聊天），重用
# 通过跨线程连接+锁跨线程服务器的工作人员
# 因此聊天一次运行一个——对于单用户本地工具来说是正确的。
_agent = None
agent_lock = threading.Lock()
_dashboard_session = None  # 此仪表板运行的聊天线程（已过时；刷新后稳定）


def dash_session() -> str:
    """The thread new dashboard chats belong to. Resolved once per process:
    RESUME the most recent recent dashboard thread (so a restart keeps the chat
    on screen), else start a fresh dated one. Never the eternal 'default'."""
    global _dashboard_session
    if _dashboard_session is None:
        try:
            conn = connect(load_settings().home)
            _dashboard_session = resume_or_new_session(conn)
            conn.close()
        except Exception:
            _dashboard_session = datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")
    return _dashboard_session


def resume_or_new_session(conn) -> str:
    """Pick this run's thread: RESUME the most recent dashboard thread if its
    last message is still fresh (within the idle window), else start a new dated
    one. Without this, every server restart minted a brand-new empty thread and
    the visible chat 'vanished' (it was only parked under the old id). An idle
    gap still rotates — that's maybe_rotate_session's job once we're running."""
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    # 按源匹配，而不是 id 前缀：“+ New chat”生成 's-...' ids，因此
    # “dashboard-%”过滤器将在重新启动时孤立这些线程。每个仪表板
    # 消息被标记为 source='dashboard' — 这是可靠的信号。
    row = conn.execute(
        "SELECT session_id, MAX(created_at) AS last_at FROM chat_log "
        "WHERE source='dashboard' GROUP BY session_id "
        "ORDER BY last_at DESC LIMIT 1"
    ).fetchone()
    if row and row["last_at"]:
        try:
            last = datetime.strptime(row["last_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if idle_min <= 0 or (datetime.now(UTC) - last).total_seconds() <= idle_min * 60:
                return row["session_id"]
        except ValueError:
            pass
    return datetime.now().strftime("dashboard-%Y%m%d-%H%M%S")


def get_agent():
    global _agent, _dashboard_session
    if _agent is None:
        from waku.app import Waku

        settings = load_settings()
        settings.ensure_home()
        conn = connect(settings.home, check_same_thread=False)
        _agent = Waku(settings=settings, conn=conn)
        # 仪表板运行恢复其最近的最后一个线程（因此重新启动/刷新
        # 将聊天保持在屏幕上），或者如果该线程空闲则重新开始。
        # 相同的 idcollect() 报告，因此扩展坞恢复正确的对话。
        _dashboard_session = resume_or_new_session(conn)
        _agent.session.session_id = _dashboard_session
    return _agent


def maybe_rotate_session(agent) -> None:
    """A returning user should get a FRESH thread, not last week's. If the
    current session's newest message is older than WAKU_SESSION_IDLE_MINUTES
    (default 60), rotate to a new dated session id — the old thread stays one
    click away in History. Live bug: a tester came back days later and their
    new chat landed in a week-old 32-message thread."""
    idle_min = int(os.getenv("WAKU_SESSION_IDLE_MINUTES", "60"))
    if idle_min <= 0:
        return
    row = agent.conn.execute("SELECT MAX(created_at) FROM chat_log WHERE session_id=?",
                             (agent.session.session_id,)).fetchone()
    if not row or not row[0]:
        return
    try:  # sqlite datetime('now') 是 UTC "YYYY-MM-DD HH:MM:SS"
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return
    if (datetime.now(UTC) - last).total_seconds() > idle_min * 60:
        agent.session.start_new(datetime.now().strftime("dashboard-%Y%m%d-%H%M%S"))


def current():
    """The shared agent, or None if nobody has chatted yet. Read-only peek —
    use this for status (collect, tools_info) so a page load never pays for
    building an agent nobody asked for."""
    return _agent


def rebuild() -> str | None:
    """Rebuild the shared agent from the CURRENT environment, so a provider or
    model switch is live on the next message. Returns an error string on
    failure — in which case the OLD agent is kept running, because a broken key
    should not also take away the working agent you already had.

    The global is rebound here rather than by the caller: this module owns it,
    and a `global` statement in someone else's file is how two writers end up
    disagreeing about which agent is live."""
    global _agent, _dashboard_session
    with agent_lock:
        old = _agent
        try:
            from waku.app import Waku

            settings = load_settings()
            settings.ensure_home()
            conn = connect(settings.home, check_same_thread=False)
            fresh = Waku(settings=settings, conn=conn)
            # 在交换过程中进行对话。设置更改会交换
            # 大脑，不是线程——而是一个全新的瓦库从永恒开始
            # “默认”会话，因此无需此线路默默切换提供商
            # 将您转入另一个聊天室，并且您的历史记录从
            # 码头。 get_agent() 使用相同的分辨率，因此两扇门都同意。
            fresh.session.session_id = (
                old.session.session_id if old is not None else resume_or_new_session(conn)
            )
            _dashboard_session = fresh.session.session_id
            _agent = fresh
        except (Exception, SystemExit) as exc:   # get_client 引发 SystemExit
            _agent = old
            return str(exc)
    if old is not None:
        old.close()
    return None
