"""26.08.15 Memory System Anatomy — the model behind the memory-layer comparison.

The abstract board. Its companion is the harness chart (26.08.08), which shows
the SAME two components sitting inside Waku's own diagram — this one strips
everything else away so the shape is arguable on its own.

The claim it has to carry: a memory system is a STORE and a MANAGER inside a
SCOPE, and every product in the video differs only in how it answers three
questions. The vendors crowd into the store. The manager is where the empty
space is, and Job 4 is empty for everyone.

    .venv/bin/python -m waku.ops.whiteboard.build_memory_anatomy
"""

from __future__ import annotations

import json
import pathlib

from waku.ops.whiteboard import style as S

OUT = pathlib.Path("docs/whiteboards/memory-anatomy.excalidraw")


def build() -> list:
    e: list = []

    #  -  -  标题  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  - -
    e.append(S.text(60, 40, "26.08.15  Memory System Anatomy", size=S.FS_TITLE))
    e.append(S.underline(64, 96, 660, color=S.PAL["red"][1]))
    e.append(S.text(60, 116,
                    "a memory system is a STORE and a MANAGER inside a SCOPE.\n"
                    "every product in this video differs only in how it answers three questions.",
                    size=S.FS_BODY))
    e += S.socials_block(2210, 44)

    # ---- 范围：外框 ------------------------------------------------------
    e += S.boundary(60, 200, 1560, 1130,
                    "SCOPE  —  if I have two users, what do they share?", color="red")

    # 顶部有一个短暂的转弯，因此第二季度有真正的流量需要标记。
    e += S.chip(150, 255, 220, 70, "a user turn")
    e += S.labeled_box(560, 245, 320, 90, "working memory")
    e += S.ellipse(1060, 245, 280, 90, "the agent", color="pink")

    e.append(S.arrow(375, 290, 555, 290))
    e.append(S.arrow(885, 290, 1055, 290))

    # Q2 是商店外面的三个箭头——这就是整个问题。
    e.append(S.arrow(360, 535, 610, 340))
    e.append(S.arrow(830, 535, 710, 340))
    e.append(S.arrow(1300, 535, 810, 340))
    e.append(S.text(880, 380, "Q2   how do I FIND it?\n"
                              "keyword  ·  vector (RAG)  ·  graph traversal  ·  don't",
                    size=S.FS_SMALL))

    # ---- 商店 ---------------------------------------------------------
    e += S.boundary(110, 470, 1460, 320, "THE STORE   ·   Q1  what SHAPE?", color="plain")

    e += S.labeled_box(150, 540, 430, 110, "Procedural\nhow to act · skills")
    e += S.labeled_box(620, 540, 430, 110, "Semantic\ndurable facts")
    e += S.labeled_box(1090, 540, 430, 110, "Episodic\ndated events")

    e.append(S.text(155, 668, "FILE\n~/.waku/skills/*/SKILL.md", size=S.FS_SMALL))
    e.append(S.text(625, 668, "FILE  +  ROWS\nMEMORY.md  ·  state.db", size=S.FS_SMALL))
    e.append(S.text(1095, 668, "ROWS\nSQLite + FTS5", size=S.FS_SMALL))

    # ---- 重要的两个箭头 --------------------------------------
    e += S.labeled_arrow(620, 900, 620, 800, "write · supersede · merge · retire")
    e += S.labeled_arrow(1060, 800, 1060, 900, "\"what's already here?\"")

    # ---- 经理 -------------------------------------------------------
    e += S.boundary(110, 910, 1460, 215,
                    "THE MANAGER   ·   Q3  what happens to what is no longer true?",
                    color="plain")

    e += S.card(150, 985, 330, "Job 1  DECIDE",
                "add / update / delete / noop\nwaku: ADDS ONLY")
    e += S.card(505, 985, 330, "Job 2  RETIRE",
                "what stopped being true\nwaku: nothing automatic")
    e += S.card(860, 985, 330, "Job 3  ATTRIBUTE",
                "which turn produced it?\nwaku: no link back")
    e += S.card(1215, 985, 330, "Job 4  REFLECT",
                "when does it think?\nEVERYONE: per-turn only")

    # 作业按顺序运行 - 绘制运动
    e.append(S.arrow(485, 1038, 500, 1038))
    e.append(S.arrow(840, 1038, 855, 1038))
    e.append(S.arrow(1195, 1038, 1210, 1038))

    # 作业 4 缺少的后边缘：虚线和红色，因为它是间隙
    e += S.labeled_arrow(1380, 985, 1380, 815, "off the critical path",
                         color=S.PAL["red"][1], dashed=True)
    e.append(S.red_note(1405, 850, "nobody does this"))

    # ---- 范围页脚 ------------------------------------------------------
    e.append(S.text(150, 1262,
                    "waku: one user, one machine     ·     mem0: shared across agents in an org",
                    size=S.FS_SMALL))
    e.append(S.red_note(150, 1292,
                        "Zep: PROJECT-WIDE ONTOLOGY — a schema set in a signup wizard "
                        "shapes every user's graph, and deleting users never clears it"))

    # ----网格：同样的三个问题，四个系统--------------------
    e += S.pill_header(1680, 200, 980, "THE SAME THREE QUESTIONS, FOUR ANSWERS", color="plain")

    e += S.card(1680, 275, 980, "waku  /  Hermes",
                "shape        files + rows (SQLite)\n"
                "retrieval    keyword FTS5 — zero embeddings\n"
                "maintenance  consolidator adds only")
    e += S.card(1680, 435, 980, "mem0",
                "shape        rows  (graph is a separate feature we did not enable)\n"
                "retrieval    vector + rerank\n"
                "maintenance  add / update / delete / noop, then lifecycle_state")
    e += S.card(1680, 595, 980, "Zep",
                "shape        TEMPORAL GRAPH — entities, edges, validity intervals\n"
                "retrieval    vector over nodes + traversal\n"
                "maintenance  invalidate with a time range — never delete")
    e += S.card(1680, 755, 980, "Supabase pgvector",
                "shape        rows\n"
                "retrieval    vector similarity (textbook RAG)\n"
                "maintenance  none — append forever")

    e += S.card(1680, 915, 980, "and the one nobody does",
                "Every system here consolidates FORWARD-ONLY, on the critical path.\n"
                "So it only ever sees the recent window — it cannot merge duplicates\n"
                "written months apart, or notice a contradiction it never saw together.",
                color="red")

    # 网格中的间隙指向管理器中的间隙
    e.append(S.arrow(1670, 985, 1420, 1010, color=S.PAL["red"][1], dashed=True))

    # ---- 这个发现让我们花了一天的时间 ------------------------------------
    e += S.card(1680, 1110, 980, "measured, not read off a docs page",
                "Both mem0 and Zep mark the dead fact dead — and BOTH still return it\n"
                "from a plain search. mem0 scored the superseded row 0.3474 against\n"
                "0.3429 for the live one. Read the lifecycle field, or ship the wrong date.")

    # Q2 列出了它的选项，但从未透露何时会选择一个。那就是
    # 观众实际上有的问题，“关键字 vs RAG vs 图表”是
    # 作为一个排名是无法回答的——它只根据你拥有的数量来决定
    # 以及你问它什么样的问题。
    e += S.pill_header(1680, 1290, 980, "SO WHEN DO YOU ACTUALLY NEED EMBEDDINGS?",
                       color="plain")

    e += S.card(1680, 1360, 980, "the honest ladder — climb only when you have to",
                "A FEW DOZEN facts, one user\n"
                "     no retrieval at all. MEMORY.md fits in the prompt, and a\n"
                "     search costs more than it saves.\n"
                "\n"
                "HUNDREDS, and you know the words that will be used\n"
                "     keyword (FTS5). No embeddings, no vector bill, no extra\n"
                "     service — and exact terms beat similarity.\n"
                "\n"
                "THOUSANDS — or the question arrives paraphrased, or in 中文\n"
                "     vector = RAG. This is the rung where embeddings start\n"
                "     paying for themselves. Not before.\n"
                "\n"
                "facts that RELATE to each other\n"
                "     graph. \"who else was at that meeting\" is a traversal,\n"
                "     not a search.\n"
                "\n"
                "facts that CHANGE\n"
                "     graph + validity intervals. Superseded is not deleted.")

    e += S.card(1680, 1810, 980, "and \"graph RAG\" is not a fourth thing",
                "It is both rungs at once: vector search to find the entry nodes,\n"
                "then traversal to expand from them. Anyone selling it as a\n"
                "separate category is selling you the combination.")

    e += S.card(1680, 1950, 980, "why waku and Hermes both skipped embeddings",
                "Deliberate, not an omission — and it has a visible consequence.\n"
                "FTS5 cannot match 发布会 to \"product launch\", so waku bolts on a\n"
                "RETRIEVAL GATE that rewrites the question into keywords before\n"
                "searching. The gate exists BECAUSE there are no embeddings.\n"
                "That is the trade: one small model call per turn, instead of an\n"
                "embedding service, a vector store, and a bill.",
                color="red")

    e.append(S.source_label(60, 1360,
                            "measured 2026-08-13 against mem0ai 2.0.17 · zep-cloud 3.27.0 · "
                            "langmem 0.0.30 · langgraph 1.2.10"))
    e.append(S.watermark(2440, 2150))
    return e


if __name__ == "__main__":
    from waku.ops.whiteboard.build_waku_architecture import write_svg_proxy

    els = build()
    S.validate(els)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(S.document(els), indent=2), encoding="utf-8")
    write_svg_proxy(els, pathlib.Path("/tmp/memory-anatomy.svg"), w=2760, h=2200)
    print("svg proxy /tmp/memory-anatomy.svg")
    shapes = [x for x in els if x["type"] in ("rectangle", "ellipse", "diamond")]
    arrows = [x for x in els if x["type"] == "arrow"]
    black = [x for x in shapes if x["strokeColor"] == S.INK]
    print(f"wrote {OUT}")
    print(f"  shapes {len(shapes)} · arrows {len(arrows)} "
          f"· ratio {len(arrows)/max(len(shapes),1):.2f}  (masters 0.88)")
    print(f"  black strokes {len(black)}/{len(shapes)} "
          f"= {100*len(black)//max(len(shapes),1)}%  (masters 96%)")
