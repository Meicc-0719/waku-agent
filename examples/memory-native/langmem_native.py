"""LangMem on its own terms: a library, not a service. There is no console.

    ON THE CHART (26.08.15 Memory System Anatomy)
    ─────────────────────────────────────────────
    THE MANAGER · Job 1 DECIDE
    The store here is a dict. The MANAGER is the entire product: it reads the
    whole conversation and decides three sentences are worth two memories.


    pip install langmem langgraph   # 或者： uv pip install -e '.[arena]'
    export ANTHROPIC_API_KEY=...    # 提取器是一个 LLM 调用
    python examples/memory-native/langmem_native.py

SDK: langmem 0.0.30 + langgraph 1.2.10. Verified live 2026-08-12.

WHY THIS FILE EXISTS

LangMem sits in every "AI memory tools" listicle next to two hosted products,
and that placement is misleading. It is a set of LangGraph primitives you run
inside your own process. There is no dashboard to open, no account, and by
default no persistence at all -- the store lives in RAM and dies when Python
exits. Step 5 demonstrates that rather than asserting it.

This is also why waku's arena reports LangMem as "unreadable" rather than
"0 facts": every read through the adapter constructs a fresh empty store, and
calling that zero would be a true statement about the wrong object.

Nothing here imports waku.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langgraph.store.memory import InMemoryStore

load_dotenv()  # 您的 .env 位于存储库根目录，waku 使用相同的密钥

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

NAMESPACE = ("quickstart", "facts")
MODEL = os.environ.get("LANGMEM_MODEL", "anthropic:claude-sonnet-4-5")


def main() -> None:
    # 1. 商店由您提供。这是岔路口：
    #    InMemoryStore 是一个带有搜索方法的字典，PostgresStore 是
    #    由您运行的数据库支持的相同界面。 LangMem 本身
    #    不存储任何内容。
    store = InMemoryStore(index={"dims": 1536, "embed": "openai:text-embedding-3-small"})
    print("store     : InMemoryStore (in this process, gone when it exits)")
    print(f"model     : {MODEL}\n")

    # 2. 提取。 create_memory_manager是LangMem的实际产品：LLM
    #    它读取对话并确定持久的事实是什么。
    #    这与 waku 的solidity.py 所做的工作相同。
    from langmem import create_memory_manager

    manager = create_memory_manager(MODEL)
    conversation = [{"role": "user", "content": f} for f in FACTS]

    print("-- telling it three things ------------------------------------")
    for fact in FACTS:
        print(f"  said : {fact}")

    extracted = manager.invoke({"messages": conversation})

    # 3. 读回原始数据。与上面三句话进行比较。请注意
    #    经理立即获得整个对话，因此可以解决问题
    #    存储任何内容之前的矛盾——真正的优势
    #    一次摄取一个句子的商店。
    print("\n-- what it actually kept --------------------------------------")
    for item in extracted:
        content = _text(item)
        print(f"  kept : {content}")
        store.put(NAMESPACE, _key(content), {"text": content})

    # 4. 搜索。这是 STORE 的搜索，而不是 LangMem 的搜索——这是另一个迹象
    #    图书馆的终点和基础设施的起点。
    print("\n-- asking ------------------------------------------------------")
    for label, question in QUESTIONS:
        hits = store.search(NAMESPACE, query=question, limit=3)
        top = hits[0].value.get("text") if hits else "(nothing found)"
        print(f"  {label} : {question}\n              -> {top}")

    # 5.没有控制台。证明短暂性而不是声称它。
    print("\n-- see it yourself --------------------------------------------")
    print("  There is no dashboard. This is the entire storage layer:")
    print(f"    {len(store.search(NAMESPACE, limit=100))} item(s) in a Python dict at {hex(id(store))}")
    print("  Restart this script and the count is the same, not cumulative --")
    print("  nothing persisted. For persistence you bring your own Postgres:")
    print("    from langgraph.store.postgres import PostgresStore")


def _text(item) -> str:
    """Unwrap ExtractedMemory(id=..., content=Memory(content='...')).

    Two layers deep, and printing the outer one gives you `content='...'`
    instead of a sentence -- which is fine in a REPL and embarrassing on a
    slide.
    """
    node = getattr(item, "content", item)
    return str(getattr(node, "content", node))


def _key(content) -> str:
    """Stable-ish key so re-running does not silently double every fact."""
    return str(abs(hash(str(content))))[:12]


if __name__ == "__main__":
    main()
