"""One SQLite file (state.db) holds everything Waku remembers and does.

This mirrors the Hermes approach on the whiteboard: SQLite + FTS5, no server.
Open it yourself anytime:  sqlite3 .waku/state.db '.tables'
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- Flagship-task artifact: events the calendar tool creates. The deterministic
-- eval asserts directly on rows in this table ("did the meeting trigger?").
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    start TEXT NOT NULL,           -- ISO 8601
    "end" TEXT,
    attendees TEXT DEFAULT '',     -- comma-separated
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

-- Semantic memory: durable facts about you, your people, your projects.
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,         -- who/what the fact is about, e.g. 'alex'
    content TEXT NOT NULL,         -- the fact itself
    source TEXT DEFAULT 'user',    -- 'user' (told directly) or 'consolidation'
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, content, content=facts, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;

-- Episodic memory: dated things that happened (past chats, distilled).
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    happened_at TEXT NOT NULL,     -- ISO 8601 date of the episode
    summary TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    summary, content=episodes, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, summary) VALUES (new.id, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, summary) VALUES ('delete', old.id, old.summary);
END;

-- Raw chat log ("save the messages" box). Consolidation reads from here.
-- session_id tags each row with which conversation it belongs to, so the
-- dashboard can offer "New chat" and switch between past sessions (like a
-- chat app). Everything shares this one table — sessions are just a label.
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,            -- 'user' | 'assistant'
    content TEXT NOT NULL,
    consolidated INTEGER DEFAULT 0,
    session_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent column upgrades for databases created before a
    column existed. SQLite has no 'ADD COLUMN IF NOT EXISTS', so we check."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_log)").fetchall()}
    if "session_id" not in cols:
        conn.execute("ALTER TABLE chat_log ADD COLUMN session_id TEXT DEFAULT 'default'")
        conn.commit()
    if "source" not in cols:
        # 消息通过哪个网关传入（cli / 语音 / 电报 / 仪表板）
        conn.execute("ALTER TABLE chat_log ADD COLUMN source TEXT DEFAULT 'cli'")
        conn.commit()
    if "meta" not in cols:
        # 辅助行上的每圈遥测数据为 JSON（门决策、
        # 延迟、迭代、工具）——因此重新打开线程仍然可以显示如何
        # 每个答案都已生成，而不仅仅是纯文本。
        conn.execute("ALTER TABLE chat_log ADD COLUMN meta TEXT")
        conn.commit()


def connect(home: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    # check_same_thread=False 让仪表板的线程 HTTP 服务器重用
    # 跨工作线程的一个代理连接（由锁保护）。繁忙超时
    # 避免在聊天写入时仪表板读取时“数据库被锁定”。
    conn = sqlite3.connect(home / "state.db", check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
