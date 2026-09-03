"""Semantic memory, vector edition — the Supabase pgvector upgrade path.

Same interface as SqliteFactStore, different retrieval: real embeddings and
cosine similarity instead of keyword BM25. Uses the exact schema and
`match_chunks` RPC from launch-rag / launch-agentic-rag
(github.com/ShenSeanChen/launch-agentic-rag) — if you followed those videos,
this is the same table. Run sql/init_supabase.sql on a fresh project, then:

    pip install 'waku-agent[supabase]'
    WAKU_SEMANTIC_STORE=supabase  SUPABASE_URL=...  SUPABASE_SERVICE_KEY=...
    OPENAI_API_KEY=...   # 仅嵌入（text-embedding-3-small，1536d）

When is this worth it over FTS5? When phrasing diverges from wording:
"my business partner" should find "Alex is my cofounder". Keywords can't;
vectors can. For a few hundred personal facts, both are instant.
"""

from __future__ import annotations

import os
import uuid

from waku.config import Settings
from waku.memory.semantic.base import env_or


class SupabaseFactStore:
    def __init__(self, settings: Settings):
        import openai
        from supabase import create_client

        self.supabase = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
        )
        self.openai = openai.OpenAI()  # 读取 OPENAI_API_KEY
        self.embed_model = env_or("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        self.top_k = settings.retrieval_top_k

    def _embed(self, text: str) -> list[float]:
        return self.openai.embeddings.create(model=self.embed_model, input=[text]).data[0].embedding

    def add(self, subject: str, content: str, source: str = "user") -> None:
        # launch-rag 列映射：源=主题，文本=事实
        self.supabase.table("rag_chunks").upsert(
            {
                "chunk_id": f"fact-{uuid.uuid4().hex[:12]}",
                "source": subject.lower().strip(),
                "text": content,
                "embedding": self._embed(f"{subject}: {content}"),
            },
            on_conflict="chunk_id",
        ).execute()

    def search(self, query: str, top_k: int = 4) -> list[str]:
        result = self.supabase.rpc(
            "match_chunks",
            {"query_embedding": self._embed(query), "match_count": top_k},
        ).execute()
        return [f"[{row['source']}] {row['text']}" for row in (result.data or [])]

    # --- 增删改查----------------------------------------------------------------
    # 这四人直到 2026 年 8 月 8 日才失踪，没有人注意到，因为
    # 没有任何内容描述事实商店欠其调用者什么。看
    # 语义/base.py：仪表板的内存页面和manage_memory的
    # update/delete 在这里引发了 AttributeError，而 search_with_ids 更糟 —
    # 上游的`hasattr`守卫将其变成“没有匹配的事实”，而
    # 事实就在这张桌子上。
    #
    # 两个模式注释，都继承自 launch-rag 并且都不明显：
    #
    #   * 这里的“来源”是主语，而不是出处。 SqliteFactStore 保留
    #     将它们分开（subject =“alex”，source =“consolidation”）； rag_chunks 有
    #     一列，add() 将主题放入其中。所以下面的`list()`不能
    #     报告声称事实的人。该协议仅承诺 id/subject/
    #     内容，所以这是诚实的而不是破碎的——但这就是为什么
    #     仪表板的出处列在此后端为空白。
    #   * ids：rag_chunks 具有 `id BIGSERIAL` 和 `chunk_id TEXT`。我们揭露
    #     整数，因为dashboard.py执行`int(payload["id"])`并且会
    #     彻底拒绝一个字符串。返回途中仍接受字符串
    #     在（与 chunk_id 匹配）中，以相同的方式 memory_admin 强制
    #     Notion 的页面 id — 通过形状，而不是通过解析。

    def _column_for(self, fact_id: int | str) -> str:
        return "id" if isinstance(fact_id, int) or str(fact_id).isdigit() else "chunk_id"

    def list(self, limit: int = 200) -> list[dict]:
        rows = (
            self.supabase.table("rag_chunks")
            .select("id, source, text, created_at")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return [
            {"id": r["id"], "subject": r["source"], "content": r["text"],
             "source": "", "created_at": r.get("created_at")}
            for r in (rows.data or [])
        ]

    def search_with_ids(self, query: str, top_k: int = 8) -> list[dict]:
        """Semantic search that also returns the ids update/delete need.

        match_chunks() returns chunk_id but not id, so this is two round trips:
        rank first, then resolve. Changing the SQL function would be one trip,
        but it ships in launch-rag and anyone who followed those videos already
        has it — silently requiring them to re-run a migration is a worse trade
        than an extra select on a query that already paid for an embedding."""
        ranked = self.supabase.rpc(
            "match_chunks",
            {"query_embedding": self._embed(query), "match_count": top_k},
        ).execute()
        chunk_ids = [r["chunk_id"] for r in (ranked.data or [])]
        if not chunk_ids:
            return []
        rows = (
            self.supabase.table("rag_chunks")
            .select("id, chunk_id, source, text")
            .in_("chunk_id", chunk_ids)
            .execute()
        )
        by_chunk = {r["chunk_id"]: r for r in (rows.data or [])}
        # 重新施加 RPC 的相似性顺序； `in_` 选择不保留它
        return [
            {"id": by_chunk[c]["id"], "subject": by_chunk[c]["source"],
             "content": by_chunk[c]["text"]}
            for c in chunk_ids
            if c in by_chunk
        ]

    def update(self, fact_id: int | str, content: str, subject: str | None = None) -> bool:
        """Re-embeds. A vector store where update() rewrote the text but left
        the old embedding would keep answering the old question correctly and
        the new one not at all — the silent-wrong-answer failure this whole
        file is a reaction to."""
        column = self._column_for(fact_id)
        current = (
            self.supabase.table("rag_chunks").select("source").eq(column, fact_id).execute()
        )
        if not current.data:
            return False
        new_subject = (subject or current.data[0]["source"]).lower().strip()
        result = (
            self.supabase.table("rag_chunks")
            .update({
                "source": new_subject,
                "text": content,
                "embedding": self._embed(f"{new_subject}: {content}"),
            })
            .eq(column, fact_id)
            .execute()
        )
        return bool(result.data)

    def delete(self, fact_id: int | str) -> bool:
        result = (
            self.supabase.table("rag_chunks")
            .delete()
            .eq(self._column_for(fact_id), fact_id)
            .execute()
        )
        return bool(result.data)

    def settle(self, timeout: float = 120.0) -> bool:
        """Already settled. The embedding is computed before the INSERT, so the
        row is searchable as soon as Postgres commits it. Nothing is inferred
        here and nothing happens later — that is the trade this backend makes."""
        return True
