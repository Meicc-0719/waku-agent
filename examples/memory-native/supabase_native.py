"""Supabase pgvector on its own terms: roll your own, and see what you get.

    ON THE CHART (26.08.15 Memory System Anatomy)
    ─────────────────────────────────────────────
    THE STORE · Q2 RETRIEVAL (vector)  —  and NO MANAGER AT ALL
    This is the baseline that makes the manager visible by its absence: real
    embeddings, good retrieval, and nothing that ever decides a fact died.


    pip install supabase openai   # 或者，从存储库根目录： uv pip install -e '.[arena]'
    export SUPABASE_URL=... SUPABASE_KEY=... OPENAI_API_KEY=...
    python examples/memory-native/supabase_native.py

SDK: supabase-py 2.x + openai 2.51.0. NOT RUN BY US, and deliberately so: the
only Supabase project on this account is a production database, and a demo
table does not belong in one. Kept because plenty of readers already have a
spare project, and this is the honest baseline the other three are measured
against. Run it yourself and the header should say so.

WHY THIS FILE EXISTS

Most people reading this already have a Supabase project open in another tab,
and the honest question about the three products in this folder is: what do
they give you that thirty lines of pgvector does not?

This file is the baseline that makes that question answerable. It is a table,
an embedding call, and a cosine search. It works, and it will answer the
paraphrase and the Chinese question better than a keyword index will.

What it does NOT do is the whole point:

  - nothing decides what is worth keeping (mem0 does)
  - nothing notices that fact 3 contradicts fact 2 (Zep does)
  - nothing ever forgets, so contradictions accumulate as neighbours forever

A vector table is retrieval. A memory system is retrieval plus judgement about
what to store and what is no longer true. Step 4 shows the gap.

SETUP -- run this once in the Supabase SQL editor:

    create extension if not exists vector;
    create table quickstart_memories (
      id bigserial primary key,
      content text not null,
      embedding vector(1536),
      created_at timestamptz default now()
    );
    create or replace function match_quickstart_memories(
      query_embedding vector(1536), match_count int
    ) returns table (id bigint, content text, similarity float)
    language sql stable as $$
      select id, content, 1 - (embedding <=> query_embedding) as similarity
      from quickstart_memories
      order by embedding <=> query_embedding
      limit match_count;
    $$;

Nothing here imports waku.
"""

from __future__ import annotations

import os

import openai
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()  # 您的 .env 位于存储库根目录，waku 使用相同的密钥

TABLE = os.environ.get("SUPABASE_QUICKSTART_TABLE", "quickstart_memories")
EMBED_MODEL = "text-embedding-3-small"

# 所有四个快速入门中都有相同的三句话。第三个矛盾
# 第二——这就是整个测试。
FACTS = [
    "I met Alex at the Lisbon AI meetup in March. He runs a robotics startup.",
    "Our product launch is scheduled for May.",
    "Actually, the launch moved to June.",
]

QUESTIONS = [
    ("exact     ", "When is the product launch?"),
    ("paraphrase", "What date did we push the ship date to?"),
    ("chinese   ", "发布会是什么时候?"),
]


def main() -> None:
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    oai = openai.OpenAI()
    print(f"table     : {TABLE}\n")

    def embed(text: str) -> list[float]:
        return oai.embeddings.create(model=EMBED_MODEL, input=[text]).data[0].embedding

    # 开始清理，这样重新运行就不会堆叠重复项并使搜索更顺利。
    supabase.table(TABLE).delete().neq("id", 0).execute()

    # 1. 写。每个句子都逐字存储。没有提取步骤
    #    因为这里没有什么可以做的——这就是你所做的交易。
    print("-- telling it three things ------------------------------------")
    for fact in FACTS:
        supabase.table(TABLE).insert({"content": fact, "embedding": embed(fact)}).execute()
        print(f"  said : {fact}")

    # 2. 读回原始数据。与 mem0 和 Zep 不同，此列表与该列表相同
    #    上面——同样的话，同样的计数。没有什么可以评判任何事情。
    print("\n-- what it actually kept --------------------------------------")
    for row in supabase.table(TABLE).select("content").order("id").execute().data:
        print(f"  kept : {row['content']}")

    # 3. 搜索，三种方式。这就是 pgvector 赢得一席之地的地方：real
    #    嵌入处理释义和中文问题
    #    关键字索引（waku 的 FTS5）必须重写查询。
    print("\n-- asking ------------------------------------------------------")
    for label, question in QUESTIONS:
        hits = supabase.rpc(
            f"match_{TABLE}", {"query_embedding": embed(question), "match_count": 3}
        ).execute().data or []
        top = f"{hits[0]['content']}  (sim {hits[0]['similarity']:.3f})" if hits else "(nothing found)"
        print(f"  {label} : {question}\n              -> {top}")

    # 4. 矛盾——以及差距。两个发布日期仍然在这里，
    #    两者仍然可以回收，几乎坐在一起
    #    相同的相似性。无论谁获胜都是嵌入的意外，
    #    不是关于什么是真实的决定。
    print("\n-- the superseded fact ----------------------------------------")
    hits = supabase.rpc(
        f"match_{TABLE}", {"query_embedding": embed("product launch date"), "match_count": 5}
    ).execute().data or []
    for hit in hits:
        print(f"  sim {hit['similarity']:.3f}  {hit['content']}")
    print("  Both dates survive, ranked by similarity. Nothing here knows one")
    print("  of them stopped being true. That judgement is what you are buying")
    print("  when you pay for a memory layer instead of a vector table.")

    # 5. 去哪里看。
    print("\n-- see it yourself --------------------------------------------")
    print(f"  Supabase -> your project -> Table editor -> {TABLE}")
    print("  Rows and embeddings, exactly as written. The most transparent of")
    print("  the four, and the least opinionated -- those are the same fact.")


if __name__ == "__main__":
    main()
