"""Ephemeral Agent Run — assembles working memory for each turn.

The inner box on the whiteboard: everything here is rebuilt per run and thrown
away. What persists lives in waku/memory. Working memory =

    system prompt (SOUL.md)            ← who Waku is
  + durable facts & episodes           ← what Waku remembers (gated!)
  + current chat history               ← this conversation
  + the user's new message
"""

from __future__ import annotations

from waku.config import Settings

DEFAULT_SOUL = """\
You are Waku, a personal assistant running locally on your user's laptop.
You are concise, warm, and proactive. You remember what your user tells you.

Rules:
- When the user wants to schedule something, use create_event. Resolve relative
  dates and times ("next Tuesday", "in 30 minutes") to ISO timestamps yourself;
  the current date and time are given below — trust them, never ask the user
  what time it is.
- When the user asks what's on their calendar (a day, a week, "yesterday"), use
  list_events — you CAN read the calendar, not just write to it.
- When the user shares something durable about a person, project, or preference,
  use save_note to remember it.
- When asked to message someone, use send_message (it drafts to a local outbox).
- If memory context is provided below, trust it — it came from your own store.
- Call each tool at most once per request. Your history shows [tools used: ...]
  lines for past turns — if a tool already ran, do NOT run it again; answer
  from that record instead.
- Be honest about where things live. Every tool's output states exactly where
  its artifact landed (local calendar file, Apple Calendar, memory database at
  .waku/state.db) — relay that truthfully, and never claim something synced
  anywhere the tool output doesn't say.
- You can manage your own memory: use manage_memory to correct or forget facts,
  update_soul to save a standing preference the user gives you, and create_skill
  to save a repeatable workflow the user teaches you (only after they say yes).
"""


def load_soul(settings: Settings) -> str:
    """SOUL.md is the editable persona file, created on first run. Changing it
    changes who your Waku is — that's procedural memory at its simplest."""
    soul_path = settings.home / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL, encoding="utf-8")
    return soul_path.read_text(encoding="utf-8")


class Session:
    """Holds one conversation: the chat history plus the recipe for the
    system prompt. One Session per gateway connection."""

    def __init__(self, settings: Settings, memory=None, session_id: str = "default"):
        self.settings = settings
        self.memory = memory  # waku.memory.Memory（第 2 阶段接线之前无）
        self.session_id = session_id
        self.history: list[dict] = []

    def build_system(self, user_message: str, notify=None) -> str:
        from datetime import datetime

        # 该代理在您的笔记本电脑上运行，因此它应该知道您笔记本电脑的时钟。
        # 带有时区名称的本地时间 — 足以解决“30 分钟内”的问题。
        now = datetime.now().astimezone()
        parts = [load_soul(self.settings),
                 f"\nRight now it is {now:%A, %Y-%m-%d %H:%M} ({now:%Z}, UTC{now:%z}).",
                 # 智能体应该了解自己的大脑——“你是什么模型？”
                 # 这是每个好奇的用户问的第一个问题
                 (f"Your model: you are running on '{self.settings.model}' via the "
                 f"'{self.settings.provider}' provider, inside Waku, a local-first "
                 f"open-source agent harness (github.com/ShenSeanChen/waku-agent).")]

        if self.memory is not None:
            # 英雄时刻＃1：一个廉价的法官决定我们是否能检索到 -
            # 默认检索速度很慢并且会使答案产生偏差（请参阅
            # memory/retrieval_gate.py 了解原因）。
            retrieved = self.memory.gated_retrieve(user_message, notify=notify)
            if retrieved:
                parts.append("\nRelevant memory:\n" + retrieved)
            skills = self.memory.matching_skills(user_message)
            if skills:
                parts.append("\nRelevant skill instructions:\n" + skills)

        return "\n".join(parts)

    def add_exchange(self, user_message: str, reply: str, tool_calls: list | None = None,
                     source: str = "cli", meta: dict | None = None) -> None:
        """Record the turn in history (working memory) and, if memory is wired,
        in the chat log (so consolidation can distill it later).

        Tool activity is folded into the assistant's history entry as a compact
        [tools used: ...] line. Without it, the model forgets it already acted
        and happily re-runs the same tool next turn (the triple-booked-meeting
        bug from the first live test)."""
        record = reply
        if tool_calls:
            summary = "; ".join(f"{c['tool']}({c['args']}) -> {c['output']}" for c in tool_calls)
            record = f"{reply}\n[tools used: {summary}]"
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": record})
        if self.memory is not None:
            self.memory.log_chat(user_message, record, session_id=self.session_id,
                                 source=source, meta=meta)

    # ---- 会话生命周期（“新聊天”/历史记录功能）
    # 会话只是 chat_log 行上的一个标签。开始新的工作可以清理工作
    # 记忆;切换会重新加载过去的对话历史记录，以便回复
    # 语境。无论如何，合并仍会读取所有未合并的行。
    def start_new(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = []

    def switch(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = []
        if self.memory is None:
            return
        # 只有过去谈话的最近的尾声才能恢复工作
        # 内存（respond() 也打开它，但不保留整个线程）
        turns = self.settings.history_turns
        for user_msg, reply in list(self.memory.session_history(session_id))[-turns:]:
            self.history.append({"role": "user", "content": user_msg})
            self.history.append({"role": "assistant", "content": reply})
