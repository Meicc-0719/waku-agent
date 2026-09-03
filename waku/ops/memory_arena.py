"""The Memory Arena — race several MEMORY backends through the same harness.

Sibling of waku/ops/arena.py. That one holds the harness constant and varies
the model; this one holds the harness AND the model constant and varies where
facts live. One dial each, so a result means something.

    seed conversation ──┬─→ waku + FTS5   ──┐
    (8 messages)        ├─→ waku + Mem0   ──┼─→ same 7 probes ─→ one scorer
                        └─→ waku + Zep    ──┘

Two things are deliberately borrowed from the model arena: every contestant
runs in its own throwaway home, so `.waku/` is never opened; and the results
land in their own JSONL, never state.db.

WHY FOUR OUTCOMES INSTEAD OF PASS/FAIL

Pass/fail hides the only interesting question. A system that says "I don't
know" is behaving correctly under uncertainty. A system that confidently
returns last month's answer, or invents one, is dangerous — and both look like
"fail" on a boolean.

    PASS      the expected answer is there
    STALE     the expected answer is missing and a SUPERSEDED one is asserted
              — "the launch is in March" after being told it moved to June
    INVENTED  a refusal was correct and it answered anyway — the fact was never
              given, so whatever it said, it made up
    MISS      the expected answer is missing and nothing wrong was asserted;
              the honest failure

INVENTED is the number the whole exercise exists to produce. On the business
track it is the difference between an unhelpful assistant and a legal agent
handing a client a filing deadline that does not exist.

Scoring lives here as PURE functions over strings so it can be tested offline
with no model, no keys, and no network — the runner below is the part that
costs money, and it is deliberately thin.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 发货的夹具是四个钝探针，其唯一的作用是记录
# 格式。有趣的问题是维护者带来的——记忆
# 基准只有针对用户存储的事实才有意义 - 所以
# 文件是可交换的，并且 waku 保留机制，而不是内容。
_EXAMPLE = Path(__file__).resolve().parents[2] / "evals" / "memory_arena.json"
PROBES_ENV = "WAKU_MEMORY_PROBES"

PASS, STALE, INVENTED, MISS = "pass", "stale", "invented", "miss"

# 参赛者什么也没被告知，然后又问了一切。它应该失败
# 每个探头；它通过的探针是模型无需记忆即可回答的探针，因此
# 该探针测量训练数据而不是存储数据。请参阅 run_arena。
CONTROL = "control"

# 当模型真正一无所有时，它们会如何衰落。刻意的关于
# 缺乏知识，而不是礼貌——“对不起”也能打开很多机会
# 自信地回答错误，所以它不在此列表中。
#
# 这是一种启发式方法，被视为一种：“score()”报告“certain=False”
# 每当它停留在这些上时，因此运行可以将这些探针准确地发送到
# 判断而不是用不需要的模型对每个探针进行评分。
_REFUSALS = (
    "don't know", "do not know", "not sure", "no information", "no record",
    "never told", "never mentioned", "never gave", "never shared",
    "didn't tell", "did not tell", "didn't mention", "did not mention",
    "didn't give", "did not give", "haven't told", "have not told",
    "haven't given", "have not given", "haven't mentioned",
    "you haven't", "you have not", "not in my memory",
    "don't have", "do not have", "nothing about", "no details", "wasn't specified",
    "not specified", "unable to find", "couldn't find", "could not find",
)
# 这份清单永远不会完整——模型衰退的方式比任何人都多
# 枚举，漏掉的措辞会被视为“发明”的诚实拒绝，即
# 最糟糕的方向是错误的。这正是“certain=False”的含义
# for：此列表中的每个判决都会被标记，以便法官可以解决。


def fixture_path() -> Path:
    """Where the probes are coming from. WAKU_MEMORY_PROBES wins, so a run can
    be pointed at a real question set without editing anything in the repo."""
    override = os.getenv(PROBES_ENV, "").strip()
    return Path(override).expanduser() if override else _EXAMPLE


def arena_models() -> list[dict]:
    """Your pinned shortlist, priced, CHEAPEST FIRST.

    The arena varies the store and holds the model fixed, so the model is a
    constant that cannot move the result — which makes the expensive default a
    pure donation. Sorting by price puts that in the picker itself rather than
    in a doc nobody reads, and the default pick is the cheapest one.
    """
    import json as _json

    from waku.config import load_settings
    from waku.ops import pricing

    home = load_settings().home
    try:
        pinned = _json.loads((home / "models.json").read_text(encoding="utf-8"))["pinned"]
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for spec in pinned:
        prov, _, mod = spec.partition(":")
        try:
            pin, pout = pricing.price_for(prov, mod)
        except Exception:
            pin = pout = 0.0
        out.append({"spec": spec, "provider": prov, "model": mod,
                    "price_in": pin, "price_out": pout})
    return sorted(out, key=lambda m: (m["price_in"] + m["price_out"], m["spec"]))


def probe_sets() -> list[dict]:
    """Every runnable question set, flat: one entry per TRACK, not per file.

    A file is a container, not a choice. Offering "which file" and then "which
    track inside it" made the user pick twice to answer one question, and the
    file name told them nothing the track label didn't say better. So the
    dinner-party file contributes two entries — "The dinner party" and "The
    business track" — and the picker reads like a list of experiments, which is
    what it is.
    """
    sets = []
    for f in probe_files():
        try:
            tracks = json.loads(Path(f["path"]).read_text(encoding="utf-8")).get("tracks", {})
        except (OSError, ValueError):
            continue
        for key, spec in tracks.items():
            sets.append({"id": f"{f['path']}::{key}", "path": f["path"], "track": key,
                         "label": spec.get("label") or f"{f['name']} / {key}",
                         "facts": len(spec.get("seed") or []),
                         "probes": len(spec.get("probes") or [])})
    return sets


def probe_files() -> list[dict]:
    """Every probe set the arena can offer, as {name, path}.

    Scanned from a directory, never taken as a path from the browser. The
    dashboard binds to localhost, but "read the JSON file at this path" is
    still a file-read primitive handed to a web page, and a benchmark tool has
    no reason to need one. Drop a file in `.waku/probes/` and it appears.
    """
    files = [{"name": "example (shipped)", "path": str(_EXAMPLE)}]
    from waku.config import load_settings

    folder = load_settings().home / "probes"
    if folder.is_dir():
        files += [{"name": f.stem, "path": str(f)} for f in sorted(folder.glob("*.json"))]
    override = os.getenv(PROBES_ENV, "").strip()
    if override and all(f["path"] != override for f in files):
        files.append({"name": f"{Path(override).stem} (env)", "path": override})
    return files


def load_fixture(path: Path | None = None) -> dict:
    """The probes, plus where they came from — the UI says so on screen, because
    'which questions was this scored against' is the first thing anyone should
    ask of a benchmark, and the answer must not be a guess."""
    source = path or fixture_path()
    fixture = json.loads(source.read_text(encoding="utf-8"))
    fixture["source"] = str(source)
    fixture["is_example"] = source == _EXAMPLE
    return fixture


def _has(haystack: str, needles) -> bool:
    low = haystack.casefold()
    return any(n.casefold() in low for n in needles)


# --- 记分员一直要求的裁判 ------------------------------------------
# 对于每个拒绝判决，score() 都会返回“certain=False”，因为该判决
# 依赖于一个不完整的短语列表。跑步者数了这些行
# 作为“needs_judge”，然后从未评判过它们，所以列表就是最终的决定。
#
# 在第一次诚实的比赛中它犯了一个错误：LangMem 回答“没有任何共享”
# 关于皮卡丘的食物偏好”——正确的拒绝——并得分“发明”，
# 因为 _REFUSALS 包含“没有关于”而不是“没有共享”。发明的是
# 头条新闻，这是对其他人的公开指控
# 做正确事情的产品。基准测试可能不会发布结论
# 无法防守。

_ADJUDICATE = """A user asked an assistant a question the assistant was never told the answer to.
The only correct behaviour is to DECLINE — to say it does not know or has nothing on file.

Question: {question}
Reply: {answer}

Did the reply DECLINE, or did it assert an answer? Wording varies enormously;
judge the meaning, not the phrasing. A reply that declines and then adds general
knowledge clearly marked as general knowledge still counts as declining.

Reply with ONLY this JSON: {{"declined": true/false}}"""


def adjudicate_refusal(question: str, answer: str) -> bool | None:
    """Did this reply decline? True/False, or None if the judge is unreachable.

    None is deliberate and is NOT treated as either verdict — an unavailable
    judge must leave the heuristic's answer standing and say so, rather than
    silently converting "I could not check" into "it passed" or "it lied".
    """
    from waku.ops import judge as _judge

    try:
        client, model = _judge.judge_client()
        with _judge._JUDGE_SEM:   # 与模特场地相同的帽子：不要踩踏裁判
            reply = client.messages.create(
                model=model, max_tokens=200,
                messages=[{"role": "user",
                           "content": _ADJUDICATE.format(question=question, answer=answer)}])
        text = "".join(b.text for b in reply.content if b.type == "text")
        if "{" not in text:
            return None
        return bool(json.loads(text[text.index("{"): text.rindex("}") + 1])["declined"])
    except Exception:
        return None


def score(answer: str, probe: dict, retrieved: bool | None = None) -> tuple[str, bool, str]:
    """Grade one answer. Returns (outcome, certain, why).

    `certain` is False when the verdict rests on the refusal heuristic above —
    those are the probes worth spending a judge call on. Everything else is a
    substring check and needs no model at all.

    `retrieved` is whether the backend went to memory for this probe. Only waku
    reports it (the retrieval gate is observable), so probes that assert on it
    are simply not graded for backends that cannot answer the question — which
    is more honest than scoring them as a failure for lacking a feature.
    """
    answer = answer or ""

    # 断言检索行为的探测器：使算术正确
    # 而默默地搜索记忆仍然是错误的行为。
    if probe.get("expect_retrieval") is False and retrieved is True:
        return MISS, True, "retrieved memory for a question that needed none"

    if probe.get("expect_refusal"):
        if _has(answer, _REFUSALS):
            return PASS, False, "declined, as it should"
        return INVENTED, False, "answered a question it was never given the answer to"

    expected = probe.get("expect_any") or []
    if expected and not _has(answer, expected):
        stale = probe.get("stale_any") or []
        if stale and _has(answer, stale):
            return STALE, True, f"asserted the superseded answer ({stale[0]})"
        return MISS, True, "expected answer absent"

    # `expect_all` 用于多跳探测，其中命名一方是半个
    # 想法：“避免让汤姆坐在山姆旁边”需要两个名字，否则就不需要
    # 结合任何东西。
    required = probe.get("expect_all") or []
    if required and not all(_has(answer, [r]) for r in required):
        missing = [r for r in required if not _has(answer, [r])]
        return MISS, True, f"only half the reasoning — missing {missing}"

    if _has(answer, probe.get("stale_any") or []) and not expected:
        return STALE, True, "asserted a superseded answer"

    return PASS, True, "correct"


def scoreboard(results: list[dict]) -> list[dict]:
    """Per-contestant tallies, worst-behaviour-first so the interesting column
    is not buried: a system that invents answers ranks below one that misses."""
    by_name: dict[str, dict] = {}
    for r in results:
        row = by_name.setdefault(
            r["contestant"],
            {"contestant": r["contestant"], PASS: 0, STALE: 0, INVENTED: 0, MISS: 0,
             "tokens": 0, "probes": 0, "needs_judge": 0},
        )
        row[r["outcome"]] += 1
        row["tokens"] += r.get("tokens", 0)
        row["probes"] += 1
        row["needs_judge"] += 0 if r.get("certain", True) else 1
    return sorted(
        by_name.values(),
        key=lambda r: (-r[INVENTED], -r[STALE], -r[MISS], -r[PASS]),
    )


def render(rows: list[dict]) -> str:
    """The table, for a terminal and for a thumbnail. No emojis (CLAUDE.md)."""
    head = f"{'contestant':<24}{'pass':>6}{'stale':>7}{'invented':>10}{'miss':>6}{'tokens':>9}"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(
            f"{r['contestant']:<24}{r[PASS]:>6}{r[STALE]:>7}{r[INVENTED]:>10}"
            f"{r[MISS]:>6}{r['tokens']:>9}"
        )
    unsure = sum(r["needs_judge"] for r in rows)
    if unsure:
        lines.append(f"\n{unsure} verdict(s) rest on the refusal heuristic — worth a judge pass.")
    return "\n".join(lines)


# --- 跑步者------------------------------------------------------------------------
# 上面的一切都是纯粹的。这部分需要花钱：它可以承受真正的 Waku 每
# 参赛者并运行真正的循环，因为检索才有意义
# 通过实际调用它的东西——门决定是否搜索
# 无论如何，这个决定是这些系统之间的区别的一半。

def arena_home(backend: str, track: str, seed: list[str], model: str) -> Path:
    """A home NAMED for what it holds, so seeding once can serve many races.

    This replaced tempfile.mkdtemp, which was wrong twice over.

    It leaked: nothing ever removed the directories, and 656 of them had piled
    up by the time anyone counted. And it made seeding unrepeatable — a fresh
    random path every run meant every race re-seeded from empty, which is 53%
    of a race spent re-telling a store facts it was told a minute ago.

    The name is a hash of everything the seeded state depends on: the track,
    the model, and the seed lines themselves. That is the staleness guard, for
    free and by construction. Change the probe set, the track or the model and
    you address a different directory — so a race can never quietly probe a
    store that was seeded for a different question.

    Lives under `.waku-arena/`, which `.gitignore` already covers via `.waku-*/`
    — the glob that exists because a second agent home's SOUL.md and usage
    ledger are the files you least want pushed.
    """
    from waku.config import load_settings

    home = (load_settings().home.parent / ".waku-arena"
            / f"{backend}-{arena_key(track, seed, model)}")
    home.mkdir(parents=True, exist_ok=True)
    return home


def arena_key(track: str, seed: list[str], model: str) -> str:
    """The one hash. Names the local home AND the hosted partition."""
    import hashlib

    return hashlib.sha256("\n".join([track, model, *seed]).encode()).hexdigest()[:12]


def arena_partition(track: str, seed: list[str], model: str) -> str:
    """The user id the hosted stores write under during a race.

    Local isolation was solved by naming the home. The hosted half had no
    equivalent, and the consequence was concrete: mem0 and Zep read
    MEM0_USER_ID / ZEP_USER_ID with a default of "waku" — the SAME partition
    the live agent uses. So every race wrote its benchmark seed into the
    operator's real memory, and every probe set wrote into the same place as
    every other one. A working-week race read back `wedding party ballroom` and
    `guest in room 402` from the business track, because there was only ever
    one drawer.

    Same key as the home, so a race is isolated on both sides by construction
    and "clean up after this race" can name exactly what it means.
    """
    return f"waku-arena-{arena_key(track, seed, model)}"


@contextlib.contextmanager
def arena_partition_env(track: str, seed: list[str], model: str):
    """Point the hosted stores at this race's partition, then put it back.

    The stores read their user id from the environment at construction, so this
    has to wrap the Waku() call rather than be passed as a setting. Restoring
    matters more than setting: leaking MEM0_USER_ID into the process would move
    the LIVE agent's memory to a benchmark partition, which is the one failure
    worse than the bug this fixes.
    """
    partition = arena_partition(track, seed, model)
    before = {k: os.environ.get(k) for k in ("MEM0_USER_ID", "ZEP_USER_ID")}
    os.environ["MEM0_USER_ID"] = os.environ["ZEP_USER_ID"] = partition
    try:
        yield partition
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _is_seeded(home: Path) -> bool:
    """Written only after seeding AND settle() both finished.

    The marker goes last on purpose. A home that was half-filled when the
    process died must look unseeded, because the alternative is racing against
    a store holding four of eight facts and reporting the gaps as memory
    failure.
    """
    return (home / ".seeded").exists()


def run_arena(backends: list[str], track: str, emit, fixture: dict | None = None,
              model: str = "", seed_only: bool = False) -> None:
    """Seed the same conversation into each backend, ask the same probes, score.

    One agent, one model, one loop. The ONLY variable is `WAKU_SEMANTIC_STORE`.
    That is the whole design: a difference in the scoreboard can then only have
    come from where the facts live.

    Each contestant runs in its own throwaway home, like the model arena — so a
    run can store, consolidate and retrieve without ever opening `.waku/`. The
    live agent's own store is never switched; that would move a real user's
    memory sideways to answer a benchmark question.

    Seeding goes through `respond()` rather than `facts.add()` on purpose. The
    fixture's seed is a conversation, and pushing facts in through the side door
    would skip consolidation — the step that decides what is worth keeping —
    and then score retrieval as if that step had happened. If a fact never gets
    stored, that is a finding about the harness, and it is the same harness for
    every contestant.
    """
    import time

    from waku.app import Waku
    from waku.config import Settings
    from waku.memory import consolidation

    # `model` 是“提供者：模型”。竞技场保持模型不变并且变化
    # 只有商店，所以它是什么型号不能改变这个发现——
    # 使得在最昂贵的默认纯粹浪费上运行它。一顿有分量的晚餐
    # claude-fable-5 上的竞赛成本约为 4.36 美元（每 M 10 美元/50 美元）；同一场比赛
    # grok-4.3 ($1.25/$2.50) 是相同答案的一小部分。
    prov, _, mod = model.partition(":")

    fixture = fixture or load_fixture()
    if track not in fixture["tracks"]:
        emit("done", {"error": f"no track '{track}' in {fixture.get('source', 'the probe file')}"})
        return
    spec = fixture["tracks"][track]
    results: list[dict] = []
    lock = threading.Lock()
    raw_emit = emit

    def emit(kind, ev):
        # 一个SSE流，多个线程。并发写入交错中线
        # 并破坏框架，显示为默默停止的 UI
        # 更新而不是作为错误。
        with lock:
            raw_emit(kind, ev)

    def one(backend):
        emit("start", {"contestant": backend})
        # 控制。参赛者什么也没被告知，然后就问了一切。
        # 它应该让每个探测器都失败——并且它通过的任何探测器都不是内存
        # 无论如何，这是模型可以从训练数据中回答的问题。
        #
        # 这不是假设。晚餐曲目曾经问詹森什么
        # 总是穿保罗·格雷厄姆不喜欢的衣服；空荡荡的商店和
        # 真实的系统提示模型回答正确，引用了他的
        # 散文。七个探测器中的三个对模型进行评分，而不是对商店进行评分，
        # 屏幕上没有任何内容表明这一点。无法显示其性能的基准
        # 题目要求被测物是装饰品。
        seeding = [] if backend == CONTROL else spec["seed"]
        store = "sqlite" if backend == CONTROL else backend
        home = arena_home(backend, track, seeding, model)
        already = _is_seeded(home)
        try:
            opts = {"provider": prov, "model": mod} if prov and mod else {}
            # 分区环境在整个比赛中设置一次，如下所示。
            app = Waku(settings=Settings(home=home, semantic_store=store,
                                         apple_calendar=False, google_calendar=False,
                                         apple_tools=False, graph_workflows=False, **opts))
            # 种子赛占比赛的 53%，并且完全确定，因此主场
            # 已经持有这个确切种子的内容不会被重新告知。赛车就是现在
            # 便宜的一半：播种一次，询问多次。
            if already:
                # 一个事件，而不是 len(seeding) 幻像“种子”事件。伪造
                # 伯爵让一家无需告知的商店仍然充满活力
                # 通过一个有说服力的阶段，它没有做。
                emit("cached", {"contestant": backend, "facts": len(seeding)})
            for line in [] if already else seeding:
                app.respond(line, source="memory-arena")
                emit("seeded", {"contestant": backend, "line": line})

            # 播种完成。在第一次探测之前必须发生两件事，
            # 或者这测量的是记忆以外的东西。
            #
            # 1. 冲洗。合并每 N 个交易所运行一次，因此尾部
            #    种子对话仍处于未合并状态
            #    chat_log — 商店从未提供过的事实。 every_n=1 排水管
            #    它。如果事实仍然不成立，那就是关于
            #    安全带，每个参赛者都使用相同的安全带。
            # 2. 忘记谈话。 History_turns为12，所以提示
            #    携带最后 24 条消息 — 以及晚餐轨道种子 8
            #    交换，即 16。因此，每个种子事实仍然是
            #    当探测器运行时，位于上下文窗口中，并且
            #    模特根本不需要咨询店家就可以回答。三
            #    探测器确实做到了这一点：它们通过了登机门报告
            #    “没有查找”，这意味着参赛者从未被使用过。一个
            #    被测试的东西可以被绕过的基准不是
            #    测量它。
            #
            #    在大门修复之前这是不可见的（#94）。虽然
            #    门未能打开，每个探测器都报告“已搜查”，因此
            #    旁路从未出现在屏幕上。
            consolidation.consolidate_if_due(app.memory.conn, app.client,
                                             app.settings.small_model, 1,
                                             app.memory.facts, app.memory.episodes)

            # 3. 等待商店可供搜索。 sqlite 和 LangMem
            #    立即返回；托管的两个最终是一致的并且
            #    两者都低估了它。 mem0 无就绪信号并已测量
            #    14秒即可查询； Zep 的每次添加“已处理”等待正在过去
            #    而该图仍然包含零个匹配节点。在那里探测
            #    对网络进行评分，并将其评分为失忆症——商店
            #    回答了有关其仍在归档的事实的问题，因此
            #    结论是未命中，屏幕上没有任何内容说明原因。
            settled = app.memory.facts.settle()
            if not settled:
                emit("warn", {"contestant": backend,
                              "message": "store did not confirm readiness before probing; "
                                         "results for this contestant may understate it"})

            # 标记放在这里，而不是更早的地方：在种子行之后，
            # 合并冲水后，店家确认后
            # 可搜索。在 Settle() 之前标记为就绪的 Home 将被重用
            # 在下一场比赛中进行调查，同时仍在归档。
            if settled and not already:
                (home / ".seeded").write_text(f"{track}\n{model}\n{len(seeding)} lines\n",
                                              encoding="utf-8")

            if seed_only:
                emit("seed-done", {"contestant": backend, "home": str(home),
                                   "facts": len(seeding), "reused": already})
                return

            app.session.start_new("probes")

            # 账本是累积的，因此每个探测的成本是 DELTA。储存
            # 每行的运行总计将使记分板（）总和成为三角形
            # 多次累计，导致报告的 Token 消耗高于实际值——
            # 数字看似合理，却并不正确。
            spent, calls_at = _ledger(home)
            for probe in spec["probes"]:
                gate: dict = {}

                def watch(kind, ev, _g=gate):
                    if kind == "gate":
                        _g["retrieved"] = ev.get("decision") in (True, "retrieve", "yes")

                t0 = time.perf_counter()
                turn = app.respond(probe["question"], source="memory-arena", observer=watch)
                after, calls_now = _ledger(home)
                outcome, certain, why = score(turn.reply, probe, gate.get("retrieved"))

                # `certain=False` 表示判决来自拒绝短语
                # 列表，该列表不可能完整。询问裁判而不是让裁判
                # 缺少的短语会发布错误的发明。一个法官不能
                # 到达返回 None 且不改变任何内容 - 启发式
                # 站着，仍然标记为不确定，这是诚实的状态。
                if not certain:
                    declined = adjudicate_refusal(probe["question"], turn.reply)
                    if declined is True and outcome == INVENTED:
                        outcome, certain, why = PASS, True, "declined — judge overruled the phrase list"
                    elif declined is False and outcome == PASS:
                        outcome, certain, why = (INVENTED, True,
                                                 "asserted an answer — judge overruled the phrase list")
                    elif declined is not None:
                        certain, why = True, why + " (judge agreed)"

                row = {"contestant": backend, "probe": probe["id"], "test": probe["test"],
                       "question": probe["question"], "answer": turn.reply,
                       "outcome": outcome, "certain": certain, "why": why,
                       # 大门是否完全进入了记忆。计算自
                       # 第一个版本并在渲染时被丢弃，所以“
                       # 检索甚至发生”从结果中无法回答 -
                       # 这就是内存基准测试的大部分用途。
                       "retrieved": gate.get("retrieved"),
                       "tokens": after - spent,
                       # 这一问题实际调用了多少次 API。这
                       # 仅凭 Token 增量无法解释“为什么一个问题消耗 4,783
                       # 个 Token”；只能凭推测，或猜测为两次调用
                       # 且数值恰好接近，这与
                       # 会心。账本每次调用写入一行，因此计数
                       # rows 为每个探针免费解决了这个问题。
                       "calls": calls_now - calls_at,
                       "ms": int((time.perf_counter() - t0) * 1000)}
                spent, calls_at = after, calls_now
                with lock:
                    results.append(row)
                emit("probe", row)
        except Exception as exc:
            # 一个后端失败不得丢失另一个后端的结果——丢失
            # 密钥或服务中断是该参赛者的事实，而不是
            # 放弃跑步的理由。
            emit("failed", {"contestant": backend, "error": f"{type(exc).__name__}: {exc}"})

    # 参赛者是独立的：独立的住宅、独立的分区、以及
    # 唯一共享的状态是发出流和“结果”，两者都在上面锁定。
    # 顺序意味着比赛需要每个参赛者的总和，而 Zep 一人
    # 等待几分钟以进行图形摄取。同时它需要最慢的一个。
    #
    # 分区环境在整个比赛中设置一次，而不是每个
    # 选手。它是流程全局的，比赛中的每个参赛者都共享
    # 无论如何，相同的分区，每个参赛者的范围将具有第一个
    # 线程完成恢复旧值，而其他线程仍然不变
    # 写作——将它们发送到现场代理的记忆中。
    seed_lines = [] if not backends else (spec.get("seed") or [])
    with (arena_partition_env(track, seed_lines, model),
          ThreadPoolExecutor(max_workers=min(len(backends) or 1, 6)) as pool):
        list(pool.map(one, backends))

    # 明确指出泄漏，而不是让它们被注意到。一个探头
    # 无论其他如何，通过的控制在本次运行中都没有测试内存
    # 列在其上得分。
    # ...但仅适用于断言召回内容的探测器。两种是
    # 应该在没有存储任何内容的情况下负责，并且标记它们是
    # 控件本身捕获的第一件事是：
    #   *expect_retrieval=False(“17乘以4等于多少”)被设计为不需要
    #     记忆;不通过它是正确的行为，而不是泄漏。
    #   * Expect_refusal（“提交截止日期是多少”）被拒绝通过，
    #     而没有记忆的参赛者每次都会被拒绝。这将是
    #     每次运行都会被标记，永远，毫无意义。
    def _asserts_recall(probe_id: str) -> bool:
        probe = next((q for q in spec["probes"] if q["id"] == probe_id), {})
        return bool(probe.get("expect_any") or probe.get("expect_all")) \
            and not probe.get("expect_refusal") \
            and probe.get("expect_retrieval") is not False

    leaked = sorted({r["probe"] for r in results
                     if r["contestant"] == CONTROL and r["outcome"] == PASS
                     and _asserts_recall(r["probe"])})
    emit("done", {"scoreboard": scoreboard(results), "results": results, "leaked": leaked})


def _ledger(home) -> tuple[int, int]:
    """(tokens, calls) this contestant has spent, from its own throwaway ledger.
    Both cumulative — callers take the difference across a turn. One ledger ROW
    is one API call, which is the only honest way to answer "how many round
    trips did that question take"."""
    ledger = home / "usage.jsonl"
    if not ledger.exists():
        return (0, 0)
    total = calls = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            # 账本的键是“in”和“out”——不是 input_tokens /
            # output_tokens，这是第一次读取的内容，以及每个探针
            # 报告了 0 个令牌，完全没有错误，因为 `.get(name, 0)`
            # 将错误的字段名称变成合理的数字。一个基准
            # 默默地报告零成本比崩溃更糟糕。
            total += int(row["in"]) + int(row["out"])
            calls += 1
        except (KeyError, ValueError, TypeError):
            continue
    return (total, calls)


# ---里面到底是什么 ----------------------------------------------------------
# “内存”选项卡用于解释基准测试，但没有显示任何内容。这是
# 另一半：对于每个配置的商店，它现在保存着什么。
# 按需阅读，绝不是 5 秒投票——每次通话都是一次实时往返
# 付费服务，以及一个仪表板，可以悄悄地向您收取坐在选项卡上的费用
# 任何人都不应该运送。

def store_contents(limit: int = 8, only: str = "", track: str = "",
                   model: str = "", fixture: dict | None = None) -> list[dict]:
    """Per-backend: how many facts it holds and a sample of them.

    A backend that errors reports the error rather than an empty list — "0
    facts" and "I could not reach the service" look identical on screen and
    mean opposite things, which is the confusion this whole page exists to
    stop.

    WHICH sqlite. Given a track and model, this reads the ARENA's own copy —
    the same `.waku-arena/` home the race seeded — and not the live agent's
    `.waku/state.db`.

    It used to read the live one, with a paragraph above the cards explaining
    that "53 vs 0" was not a comparison. That paragraph was the tell. The panel
    sits under a benchmark whose entire promise is that every store was told
    the same thing, and the first card was a store that had been told something
    else entirely, for weeks. Apologising for a comparison in prose is worse
    than not making it.

    Two things fall out for free. The cards become genuinely comparable, so the
    explanatory banner can go. And the page stops putting the operator's home
    address, colleagues and work email on screen — which mattered, because this
    tab gets filmed.

    The live store is not hidden; it has its own page. It is just not a
    contestant.
    """
    from waku.config import Settings, load_settings
    from waku.memory import Memory

    spec = ((fixture or load_fixture()).get("tracks") or {}).get(track) or {}
    seed = spec.get("seed") or []

    out = []
    for key in _available_backends():
        if only and key != only:
            continue
        # 三种，不是两种。 “连接帐户”是关于控制的谎言
        # — 它没有帐户，没有服务，没有行，并打印该行
        # 上面写着“设计中没有透露任何内容”的注释使卡片争论不休
        # 与它自己。
        # 一场比赛的范围意味着所有比赛，而不仅仅是局部比赛。第一个
        # 版本范围为 sqlite 并让托管商店阅读他们的
        # 默认分区——即实时代理的“waku”。所以比赛
        # 写入 waku-arena-<key>，面板读取“waku”，并 Clean 删除
        # waku-arena-<key>：三个不同的抽屉，并且卡片永远不会
        # 无论你清洁什么，都会改变。更糟糕的是，面板显示
        # 运营商的真实托管内存始终存在。
        arena_copy = bool(seed)
        kind = ("control" if key == CONTROL else
                "arena" if arena_copy else
                "live" if key == "sqlite" else "connected")
        row = {"store": key, "count": 0, "facts": [], "error": "", "span": "",
               "kind": kind, "note": _store_note(key)}
        if row["note"]:
            out.append(row)   # 没有什么值得阅读的内容 — 说出原因，不要报告 0
            continue
        try:
            # 该控件没有自己的存储；比赛给了它一个 sqlite
            # 在它自己的家中并且什么也不告诉它，所以读取那个家是
            # 事实证明它确实是空的，而不仅仅是声称的。
            home = (arena_home(key, track, seed, model) if arena_copy
                    else load_settings().home)
            store = "sqlite" if key == CONTROL else key
            settings = Settings(home=home, semantic_store=store)
            # 托管商店从环境中读取其分区
            # 施工，就像他们在比赛中所做的那样——所以阅读了
            # 以与写入相同的方式进行包装。
            with (arena_partition_env(track, seed, model) if arena_copy
                  else contextlib.nullcontext()):
                facts = Memory._make_fact_store(_conn_for(store, settings), settings)
                rows = facts.list(200)
            row["count"] = len(rows)
            row["span"] = _span(rows)
            row["facts"] = [{"subject": r.get("subject", ""), "content": r.get("content", "")}
                            for r in rows[:limit]]
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"[:160]
        out.append(row)
    return out


def clean_stores(track: str = "", model: str = "", fixture: dict | None = None) -> dict:
    """Delete everything THIS race wrote, and nothing else.

    Safe only because of arena_partition. Before that, the arena wrote its seed
    into MEM0_USER_ID / ZEP_USER_ID's default of "waku" — the live agent's own
    partition — so "clean the stores" would have deleted the operator's real
    memory. A cleanup button is exactly as safe as the isolation underneath it,
    and there was none.

    Now every race owns a partition named for its own key, so this can name
    precisely what it means: the `.waku-arena/` homes for this seed, and the
    hosted partition with the matching name. It never touches `.waku/state.db`
    and never touches the `waku` partition, because it does not know their
    names — it only ever asks for `waku-arena-<key>`.

    Reports per store rather than raising: a cleanup that half-worked and said
    nothing is how you end up racing against data you believe is gone.
    """
    import shutil

    from waku.config import load_settings

    spec = ((fixture or load_fixture()).get("tracks") or {}).get(track) or {}
    seed = spec.get("seed") or []
    if not seed:
        return {"error": "no track chosen — nothing can be named, so nothing is deleted"}

    key, partition = arena_key(track, seed, model), arena_partition(track, seed, model)
    out: dict = {"partition": partition, "removed": [], "errors": []}

    base = load_settings().home.parent / ".waku-arena"
    for home in sorted(base.glob(f"*-{key}")) if base.exists() else []:
        try:
            shutil.rmtree(home)
            out["removed"].append(home.name)
        except Exception as exc:
            out["errors"].append(f"{home.name}: {type(exc).__name__}: {exc}")

    for store, wipe in (("mem0", _wipe_mem0), ("zep", _wipe_zep)):
        try:
            if wipe(partition):
                out["removed"].append(f"{store}:{partition}")
        except Exception as exc:
            out["errors"].append(f"{store}: {type(exc).__name__}: {exc}")
    return out


def _absent(exc: Exception) -> bool:
    """Did this delete fail because the thing was already gone?

    Zep 404s when the partition does not exist, which is the NORMAL case:
    cleaning twice, or cleaning a race that only ever ran locally. Reporting
    that as an error trains you to ignore the error line, and the day it says
    something real you will ignore that too.
    """
    text = f"{getattr(exc, 'status_code', '')} {exc}".lower()
    return "404" in text or "not found" in text or "not_found" in text


def _wipe_mem0(partition: str) -> bool:
    from mem0 import MemoryClient

    try:
        MemoryClient().delete_all(user_id=partition)
    except Exception as exc:
        if not _absent(exc):
            raise
        return False
    return True


def _wipe_zep(partition: str) -> bool:
    from zep_cloud import Zep

    try:
        Zep(api_key=os.environ["ZEP_API_KEY"]).user.delete(user_id=partition)
    except Exception as exc:
        if not _absent(exc):
            raise
        return False
    return True


def _span(rows: list[dict]) -> str:
    """Oldest to newest, as plain dates. The most honest single fact about a
    store's contents: three weeks of real use and one afternoon of benchmark
    data look identical as a count, and completely different as a span."""
    stamps = sorted(str(r.get("created_at") or "")[:10] for r in rows if r.get("created_at"))
    stamps = [s for s in stamps if s]
    if not stamps:
        return ""
    return stamps[0] if stamps[0] == stamps[-1] else f"{stamps[0]} to {stamps[-1]}"


def _store_note(key: str) -> str:
    """Why a store's contents cannot be listed, when that is the case.

    LangMem without Postgres is LangGraph's InMemoryStore, and every read here
    constructs a fresh one — so it would report "0 facts" forever. That is a
    false statement about an empty store rather than a true one about an
    unreadable one, and the difference is the whole point of this page.
    """
    if key == CONTROL:
        # 控件是参赛者，而不是后端。什么也没告诉，并且
        # 问了一切，所以后面没有商店可以阅读——而且
        # _conn_for 对于不是 sqlite 的任何内容都返回 None，其中
        # sqlite 路径然后调用 .execute() 。页面上显示为
        # “AttributeError：'NoneType'对象没有属性'execute'”，其中
        # 读作“你的控制选手被打破了”，而事实是这样的
        # 什么都不做就是工作的全部。
        return ("told nothing, by design — there is no store behind this one. "
                "It exists so a probe it still passes can be flagged as a "
                "question that never needed memory.")
    if key == "langmem" and not os.getenv("WAKU_LANGMEM_POSTGRES", "").strip():
        return ("in-memory store — contents live inside the process that wrote them "
                "and cannot be read back here. Set WAKU_LANGMEM_POSTGRES to persist.")
    return ""


def _conn_for(key: str, settings):
    """Only the sqlite store needs a connection; the hosted ones ignore it. The
    live .waku/state.db is opened READ-ONLY here — this page reports, it never
    writes, and the arena's own runs happen in throwaway homes."""
    if key != "sqlite":
        return None
    from waku.db import connect

    return connect(settings.home)


def _available_backends() -> list[str]:
    """sqlite always; a hosted store only when it is configured AND installed,
    so this page never reports an error that just means "you have not set this
    up", which is what the Connections tab is for."""
    from waku.integrations import IntegrationState, list_integrations

    ready = {v.key for v in list_integrations()
             if v.status.state in (IntegrationState.CONFIGURED, IntegrationState.CONNECTED)}
    # 慢的最后是故意的。 Zep 等待每个图的摄取
    # 写——分钟，而其他人则需要毫秒——所以把它放在
    # 中间意味着当它完成时，快速列在其后面处于未读状态。
    # 这里的顺序是列出现的顺序。
    order = ("mem0", "langmem", "supabase", "zep")
    # CONTROL 最后：这是完整性检查，而不是您排名的参赛者。
    return ["sqlite", *[k for k in order if k in ready], CONTROL]
