"""Reset .waku to a clean, curated state for a demo / recording.

    python scripts/demo_seed.py                 # 清零，保留支出账本
    python scripts/demo_seed.py --reset-spend   # 同时清除 usage.jsonl（费用/Token）

What it does (your old state is backed up first, never just deleted):
  1. moves the current .waku aside to .waku.bak-<timestamp>
  2. creates a fresh state.db + calendar.ics
  3. seeds a small, clean memory (a few facts + one episode) and ONE calendar
     event — Sergey's standing Saturday 5 PM swim
  4. clears the loop/tool traces AND the Ops eval history, so the Loop, Tools and
     Ops tabs start empty and fill up live in front of the viewer as you type

The money/token spend ledger (usage.jsonl) is treated as a permanent record and
is KEPT by default — it's only wiped when you explicitly pass --reset-spend.

Everything it writes is the same data the app writes — open state.db afterwards
and it looks exactly like real use, just tidy.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime

from waku.config import load_settings
from waku.db import connect
from waku.memory.episodic.store import SqliteEpisodeStore
from waku.memory.semantic.store import SqliteFactStore
from waku.tools.calendar import make_tool

# 精选种子——干净，无重复。在录制之前编辑这些内容以供品味。
FACTS = [
    # 每个多行字符串周围的括号是承载的，而不是样式：
    # 在集合中，缺失的逗号会默默地将两个条目粘合成一个
    # 而不是出错。 ruff 的 ISC004 恰好标记了这种形状。
    ("user", ("The user runs the YouTube channel 'Sean's AI Stories' and films implementation "
              "walkthroughs. His X account is @ShenSeanChen. All of his Chinese social media "
              "accounts are called 肖恩君Sean.")),
    ("raj", ("Raj is a close friend who plays really great tennis and always teaches me great "
             "British slangs!")),
    ("sergey", "Sergey is the close friend who loves swimming and often cooks delicious food!"),
]
EPISODE = ("2026-07-11", "Confirmed the standing Saturday 5 PM swim with Sergey.")
EVENT = {"title": "Swim with Sergey", "start": "2026-07-11T17:00",
         "end": "2026-07-11T18:00", "attendees": "Sergey"}


def main(reset_spend: bool = False) -> None:
    settings = load_settings()
    home = settings.home

    if home.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = home.with_name(f"{home.name}.bak-{stamp}")
        shutil.copytree(home, backup)
        print(f"backed up {home} -> {backup}")
        # calendar.ics + 这些目录是普通文件，没有进程保持打开状态。
        # traces/ 保存循环与工具历史记录；清除后这些选项卡会从空状态开始。
        (home / "calendar.ics").unlink(missing_ok=True)
        for sub in ("outbox", "skills", "traces"):
            d = home / sub
            if d.exists():
                shutil.rmtree(d)
        # 操作评估历史记录 - 从空开始，因此实时“make gateway”添加可见行。
        (home / "eval_runs.jsonl").unlink(missing_ok=True)
        (home / "eval_report.json").unlink(missing_ok=True)
        # 支出账本是永久记录——只有在明确要求时才会被擦除。
        if reset_spend:
            (home / "usage.jsonl").unlink(missing_ok=True)

    settings.ensure_home()
    conn = connect(home)

    # 就地清除数据库行 — 切勿删除 state.db。删除文件
    # 会留下任何实时网关（正在运行的“make telegram”、仪表板、
    # 一个开放的 CLI）持有与旧 inode 的损坏的只读连接。
    for table in ("chat_log", "calendar_events", "facts", "episodes"):
        conn.execute(f"DELETE FROM {table}")   # 触发器使 FTS 索引保持同步
    conn.commit()

    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    for subject, content in FACTS:
        facts.add(subject, content, source="user")
    episodes.add(EPISODE[1], happened_at=EPISODE[0])

    create_event = make_tool(conn, home).fn
    print(create_event(**EVENT))

    # 重新生成人类可读的 MEMORY.md 镜像以达到新状态
    from waku.memory import Memory

    Memory(conn, settings, None).export_markdown()

    print(f"\nclean demo state ready in {home}")
    print(f"  facts: {len(FACTS)}  ·  episodes: 1  ·  events: 1  ·  chat log: cleared")
    print("  CLEARED: loop/tool traces, Ops eval history, outbox, skills.")
    if reset_spend:
        print("  CLEARED: usage.jsonl (money/token spend) — you approved this.")
    else:
        print("  KEPT: SOUL.md and usage.jsonl (your real spend — pass --reset-spend to wipe).")
    print("  Run `waku dashboard` and start filming.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset .waku to a clean demo state.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="required confirmation: yes, wipe .waku (it is backed up first)")
    parser.add_argument("--reset-spend", action="store_true",
                        help="also wipe usage.jsonl (the money/token spend ledger)")
    args = parser.parse_args()
    if not args.yes:
        # 安全门：这会破坏实时内存/日历/痕迹。拒绝，除非
        # human 明确确认 --yes。请参阅 CLAUDE.md（“永远不要擦除运行时
        # 数据无需先询问”）。它会备份，但恢复很麻烦。
        print("REFUSING to run: demo_seed clears .waku (memory, calendar, chat, traces"
              + (", AND spend" if args.reset_spend else "") + ").")
        print("This is destructive. If you truly mean it, re-run with --yes:")
        print("    python scripts/demo_seed.py --yes"
              + (" --reset-spend" if args.reset_spend else ""))
        raise SystemExit(2)
    main(reset_spend=args.reset_spend)
