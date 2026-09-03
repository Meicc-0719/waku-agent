"""Semantic memory — durable facts, keyword-searched with SQLite FTS5.

The Hermes insight from the whiteboard: "keyword top-k, no embedding". For a
single user's facts, ranked keyword search (BM25) is fast, fully local, and —
crucially for teaching — you can read the whole index with sqlite3.
Want vectors? Set WAKU_SEMANTIC_STORE=supabase (see supabase_store.py).
"""

from __future__ import annotations

import re
import sqlite3

# unicode61 脚本不分段。它将这些视为字母数字，但是
# puts no word boundary between them, so a whole run — 「阿历克斯喜欢游泳」 — is
# 索引为一个术语。因此，该运行中的名称完全匹配可以
# 从未命中，这就是为什么这些标记，并且只有这些标记，被搜索为
# 前缀。将*每个*标记转换为“标记*”会悄悄地使“汽车”匹配
# “地毯”：比这个更糟糕的失败，因为它看起来仍然有效。
_UNSEGMENTED = re.compile(
    "["
    "\u3040-\u309f"  # 平假名
    "\u30a0-\u30ff"  # 片假名
    "\u3400-\u4dbf"  # 中日韩统一表意文字扩展 A
    "\u4e00-\u9fff"  # 中日韩统一表意文字
    "\uf900-\ufaff"  # CJK 兼容性表意文字
    "]"
)


def _fts_query(text: str) -> str:
    r"""User text isn't a valid FTS5 query (quotes/punctuation break MATCH).
    Reduce it to `word OR word OR ...` over alphanumeric tokens.

    "Alphanumeric" has to mean what the *index* means by it. facts_fts and
    episodes_fts declare no tokenizer, so they get FTS5's default — unicode61,
    which keeps every Unicode alphanumeric and folds diacritics ("München" is
    stored as `munchen`). An ASCII-only `[a-zA-Z0-9]` disagreed with that
    index, and the index was not the side that was wrong:

      * accented Latin was truncated to a fragment — "Müller" became `ller`,
        which matches nothing, so German, French, Spanish, Portuguese, Turkish
        and Vietnamese users got silently wrong answers;
      * every non-Latin script reduced to "" — and an empty query is not a
        no-op. SqliteEpisodeStore.search() reads it as "just give me the recent
        ones", so a user asking about Сергей got an unrelated English episode
        handed to the model under the heading "Relevant memory". Waku wasn't
        skipping memory for those users, it was confidently supplying someone
        else's.

    `[^\W_]` is the same set unicode61 keeps. Underscore is excluded because
    unicode61 treats it as a separator, so `waku_agent` is two terms in the
    index and has to be two terms here too, or it matches nothing.

    Everything stays lowercased, and that is load-bearing beyond tidiness:
    FTS5's AND/OR/NOT/NEAR are operators only in UPPERCASE, so lowercasing is
    what stops a user's "Alex and Bob" from parsing as a boolean expression.
    """
    words = re.findall(r"[^\W_]{2,}", text.lower())
    if not words:
        return ""
    return " OR ".join(
        f"{w}*" if _UNSEGMENTED.search(w) else w for w in dict.fromkeys(words)
    )


class SqliteFactStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, subject: str, content: str, source: str = "user") -> None:
        self.conn.execute(
            "INSERT INTO facts (subject, content, source) VALUES (?,?,?)",
            (subject.lower().strip(), content, source),
        )
        self.conn.commit()

    def search(self, query: str, top_k: int = 4) -> list[str]:
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self.conn.execute(
            "SELECT f.subject, f.content FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts, top_k),
        ).fetchall()
        return [f"[{r['subject']}] {r['content']}" for r in rows]

    # --- CRUD：人类（仪表板）和代理（manage_memory 工具）编辑内存。
    # facts_au /facts_ad 触发器自动保持 FTS 索引同步。
    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, content, source, created_at FROM facts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_with_ids(self, query: str, top_k: int = 8) -> list[dict]:
        fts = _fts_query(query)
        if not fts:
            return self.list(top_k)
        rows = self.conn.execute(
            "SELECT f.id, f.subject, f.content FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts, top_k),
        ).fetchall()
        return [dict(r) for r in rows]

    def update(self, fact_id: int, content: str, subject: str | None = None) -> bool:
        if subject is None:
            cur = self.conn.execute("UPDATE facts SET content=? WHERE id=?", (content, fact_id))
        else:
            cur = self.conn.execute(
                "UPDATE facts SET content=?, subject=? WHERE id=?",
                (content, subject.lower().strip(), fact_id),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def delete(self, fact_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def settle(self, timeout: float = 120.0) -> bool:
        """Already settled. The row and its FTS5 index land in one transaction,
        so a fact is searchable the instant add() returns. The hosted backends
        have to work for this."""
        return True
