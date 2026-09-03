"""The Arena — race several models through the SAME harness, live.

This is the Eval/LLM-Ops pillar made visible. One message goes to N models at
once; each contestant runs the REAL loop — retrieval gate, tools, memory — in
its own throwaway home, so a race can create events and save notes without ever
touching your actual data. That isolation is the whole reason this is safe to
demo: `.waku/` is never opened here.

    prompt ──┬─→ model A ─→ own temp home ─→ gate · tools · reply ─┐
             ├─→ model B ─→ own temp home ─→ gate · tools · reply ─┼─→ SSE
             └─→ model C ─→ own temp home ─→ gate · tools · reply ─┘

Two scores, deliberately separate:
  Completion  deterministic — did the right tool fire, with the right args?
              (waku.ops.scoring, only for prompts in the battery)
  Quality     an LLM referee's grade (waku.ops.judge), run AFTER the race as one
              gentle pass so a burst of concurrent calls can't 429 half of them.

Results land in the arena's own JSONL scoreboard (waku.ops.compare_history) —
never state.db. dashboard.py owns the HTTP/SSE plumbing; this module owns the
race.
"""

from __future__ import annotations

import json
from pathlib import Path

from waku.config import load_settings
from waku.ops import compare_history, scoring
from waku.ops import judge as judge_mod
from waku.ops.pricing import cutoff_for, price_for


def compare_stream(message: str, specs: list, emit, judge: bool = False,
                   coding: bool = False, judge_spec: str = "", apple: bool = False) -> None:
    """Race the models and stream each one's harness LIVE — gate decision and
    tool calls, per model — so every column plays out like the chat dock instead
    of a static 'racing…'. Each contestant runs the REAL loop (tools included) in
    its own isolated temp home, so it can create events / save notes / search
    without touching your real data. Parallel threads share one SSE socket, so
    emit() is serialized behind a lock; each event is tagged with its `spec` so
    the browser routes it to the right column."""
    import tempfile
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from waku.app import Waku
    from waku.config import Settings

    if not message or not specs:
        emit("done", {"error": "message and models required"})
        return

    lock = threading.Lock()
    collected: list = []   # 每个模型的结果，最后保存到比较历史记录中
    # 如果此提示是已知的电池情况，则每一列都会得到确定性的
    # 完成分数（是否使用正确的工具，使用正确的参数，足够
    # 次）。自由形式的提示仍在竞争——它们只是得不到分数。
    case = scoring.case_for_message(message)

    def send(kind, ev):
        with lock:
            emit(kind, ev)
            if kind == "result":
                collected.append(ev)

    def run(spec):
        provider, _, model = spec.partition(":")
        send("start", {"spec": spec, "provider": provider, "model": model,
                       "cutoff": cutoff_for(model)})
        home = Path(tempfile.mkdtemp(prefix=f"compare-{provider}-"))
        gate: dict = {}

        # 实时传输结构线束（门决策、工具调用）——这些
        # 观察者在没有stream=True的情况下开火。我们故意不
        # token-stream 回复：stream=True 建立了一些推理模型（gemini
        # 使用工具）需要一个thought_signature和400，这是简单的路径
        # 没有。因此，线束会实时播放，并且回复会完成。
        def obs(kind, ev):
            if kind == "gate":
                gate.update(decision=ev.get("decision"), reason=ev.get("reason"))
                send("gate", {"spec": spec, "decision": ev.get("decision"), "reason": ev.get("reason")})
            elif kind == "tool":
                send("tool", {"spec": spec, "tool": ev.get("tool")})
            elif kind == "subagent":
                # delegate_task 中继 pi 的实时事件流（参见experimental.py）
                # — 转发它，以便卡片可以显示子代理的工作情况
                # 的黑匣子。文本增量被修剪；这是一个窥视，而不是日志。
                out = {"spec": spec, **ev}
                if out.get("type") == "text" and len(out.get("delta", "")) > 200:
                    out["delta"] = out["delta"][:200]
                send("subagent", out)

        try:
            # 编码模式注册 delegate_task （pi 子代理），因此循环
            # 可以将真正的编程工作交给 pi — 运行完整的工具
            # （门、内存、工具），而不是旁路。 pi 在此卡的型号上运行。
            # apple_calendar 默认关闭（隔离），选择加入每场比赛 - 当打开时，
            # 每个模型都将自己的事件写入真正的“Waku”日历中。
            settings = Settings(
                provider=provider,
                model=model,
                small_model="",
                home=home,
                apple_calendar=apple,
                google_calendar=False,
                experimental=coding,
            )
            app = Waku(settings=settings)
            # 评分案例可能会预加载一个事实（例如“应用内存”），因此每个
            # 模型从清单假设的相同状态开始。
            if case and case.get("setup_fact"):
                app.memory.facts.add(case["setup_fact"]["subject"], case["setup_fact"]["content"])
            t0 = time.perf_counter()
            result = app.respond(message, source="compare", observer=obs)
            ms = int((time.perf_counter() - t0) * 1000)
            tin = tout = 0
            ledger = home / "usage.jsonl"
            if ledger.exists():
                for line in ledger.read_text(encoding="utf-8").splitlines():
                    try:
                        r = json.loads(line)
                        tin, tout = tin + r.get("in", 0), tout + r.get("out", 0)
                    except json.JSONDecodeError:
                        pass
            pin, pout = price_for(provider, settings.model)
            cost = round(tin / 1e6 * pin + tout / 1e6 * pout, 4)
            completion = None
            if case:
                passed, why = scoring.check_case(case, result.tool_calls)
                completion = {"passed": passed, "why": why, "case": case["id"]}
            # 质量（裁判等级）不是在这里完成的——它是作为一个受控的运行的
            # 在每一列完成后通过（见下文），因此裁判不会
            # 获得大量并发呼叫并跳过一些。
            send("result", {"spec": spec, "provider": provider, "model": settings.model,
                            "reply": result.reply, "gate": (gate or None),
                            "iterations": result.iterations, "latency_ms": ms,
                            "tools": [{"tool": c["tool"]} for c in result.tool_calls],
                            "tokens_in": tin, "tokens_out": tout, "cost_usd": cost,
                            "cutoff": cutoff_for(settings.model),
                            "completion": completion, "quality": None})
        except (Exception, SystemExit) as exc:
            # SystemExit （不是 Exception 子类）是 get_client 引发的
            # 钥匙丢失/配置错误。也抓住它，或者无钥匙提供商
            # 会默默地从比赛中消失，而不是表明失败的原因。
            send("result", {"spec": spec, "provider": provider, "model": model, "error": str(exc)[:200]})

    with ThreadPoolExecutor(max_workers=min(len(specs), 6)) as ex:
        list(ex.map(run, specs))

    # 赛后评分，作为一次温和的传球——这样裁判员就能获得稳定的评分
    # 缓慢的调用 (max_workers=2)，而不是每列瞬间的爆发
    # 饰面，过去为 429，有些型号未分级。各年级
    # 更新其卡片（“成绩”事件）和存储的结果，因此历史记录+
    # 记分牌最终显示每个模型的得分。
    if judge:
        jp, _, jm = (judge_spec or "").partition(":")
        gradable = [r for r in collected if not r.get("error") and (r.get("reply") or "").strip()]
        emit("grading", {"n": len(gradable), "judge": jm or judge_mod.JUDGE_MODEL})

        def grade(r):
            if r.get("error") or not (r.get("reply") or "").strip():
                return
            q = judge_mod.judge_reply(message, r["reply"], jp or None, jm or None,
                                      tools=[t.get("tool") for t in (r.get("tools") or [])])
            r["quality"] = q                       # 折叠成持久化的内容
            send("grade", {"spec": r.get("spec"), "quality": q})

        with ThreadPoolExecutor(max_workers=2) as jex:
            list(jex.map(grade, list(collected)))

    # 将竞赛保留到竞技场自身的历史（而不是代理的真实状态）。
    try:
        compare_history.append_run(load_settings().home, message, collected)
    except Exception:
        pass   # 载入史册的小问题决不能让比赛失败
    emit("done", {})


def compare_clear(payload: dict) -> dict:
    """Wipe the Compare scoreboard/history (the Clear button). Only the arena's
    own log; nothing else is touched."""
    compare_history.clear(load_settings().home)
    return {"ok": True, "runs": [], "aggregate": []}


def history_response(runs: list[dict]) -> dict:
    """Reprice each stored result from its tokens with the CURRENT price table (so
    a pricing fix corrects past races), aggregate, and tag each row with the rate
    and knowledge cutoff (also from the current table, so a cutoff fix corrects
    past races too). The shared shape returned by /api/compare/history and the
    re-grade endpoint."""
    for run in runs:
        for r in run.get("results", []):
            r["cutoff"] = cutoff_for(r.get("model", ""))
            if r.get("error"):
                continue
            pin, pout = price_for(r.get("provider", ""), r.get("model", ""))
            r["cost_usd"] = round((r.get("tokens_in") or 0) / 1e6 * pin
                                  + (r.get("tokens_out") or 0) / 1e6 * pout, 4)
    agg = compare_history.aggregate(runs)
    for row in agg:
        row["rate_in"], row["rate_out"] = price_for(row["provider"], row["model"])
        row["cutoff"] = cutoff_for(row["model"])
    return {"runs": runs[-20:][::-1], "aggregate": agg}


def compare_regrade(payload: dict) -> dict:
    """Re-run the referee on the most recent race — for models the grader skipped
    (429'd) the first time. `only_missing` (default true) grades only the ungraded
    ones; pass false to re-grade everyone. Returns the refreshed history +
    scoreboard, same shape as /api/compare/history."""
    home = load_settings().home
    runs = compare_history.load_runs(home)
    if not runs:
        return {"runs": [], "aggregate": []}
    jp, _, jm = (payload.get("judge_model") or "").partition(":")
    only_missing = payload.get("only_missing", True)
    spec = payload.get("spec")   # 仅对一张卡进行评分（每张卡按钮）
    last = runs[-1]
    for r in last.get("results", []):
        if r.get("error") or not (r.get("reply") or "").strip():
            continue
        if spec is not None and r.get("spec") != spec:
            continue
        if spec is None and only_missing and r.get("quality") is not None:
            continue
        q = judge_mod.judge_reply(last.get("message", ""), r["reply"], jp or None, jm or None,
                                  tools=r.get("tools"))   # 历史记录将工具存储为[名称]
        if q is not None:
            r["quality"] = q
    compare_history.save_runs(home, runs)
    return history_response(runs)


def compare_delete_run(payload: dict) -> dict:
    """Delete ONE race (by timestamp) from the scoreboard — its models drop out of
    the totals — leaving every other race intact. Returns the refreshed history."""
    home = load_settings().home
    ts = payload.get("ts")
    runs = [r for r in compare_history.load_runs(home) if r.get("ts") != ts]
    compare_history.save_runs(home, runs)
    return history_response(runs)
