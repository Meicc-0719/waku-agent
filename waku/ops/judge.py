"""K3-as-referee quality scoring for the Compare arena.

Completion (waku.ops.scoring) is deterministic — did the right tool fire. Quality
is the other half: *how good was the answer*, for the open-ended part a checklist
can't see. There's no single right answer, so we do what the market does for that
axis (MT-Bench / Chatbot-Arena style): an LLM grades the transcript against a
rubric, 0-10 + a one-line reason.

The referee must be a model that ISN'T racing — otherwise it grades itself, which
is neither fair nor credible (you can't test K3 with K3 as the judge). Default is
**gpt-5.6-sol**: a strong reasoning model that happens to be a poor *contestant*
here (it can't call tools on the chat endpoint) but a fine *judge* (grading is
pure text, no tools). Switchable per-race from the arena, or via WAKU_JUDGE_*.
Any provider works — Waku's OpenAI-compat client exposes the same
`.messages.create` shape as the anthropic wire, so the judge is provider-agnostic.
"""

from __future__ import annotations

import json
import os
import threading
import time

from waku.config import Settings, load_settings
from waku.loop.models import get_client

JUDGE_PROVIDER = os.getenv("WAKU_JUDGE_PROVIDER", "openai")
JUDGE_MODEL = os.getenv("WAKU_JUDGE_MODEL", "gpt-5.6-sol")

# 一场比赛会同时对每一栏进行评分——8 个评委呼叫到达一个终点
# 同时得到一些 429'd，这些列显示“—”。法官人数上限
# 调用同时运行（在比赛的线程之间共享），因此裁判不会
# 踩踏；其余的排队并仍然获得评分。
_JUDGE_SEM = threading.Semaphore(int(os.getenv("WAKU_JUDGE_CONCURRENCY", "2")))

_RUBRIC = """You are a strict, fair judge scoring an AI assistant's reply.

The user asked:
{task}

The assistant replied:
{reply}
{actions}
Score how well the reply serves the user's request on a 0-10 scale:
- 9-10: fully addresses the request, correct, concise, honest about any limits.
- 5-8: mostly addresses it, minor gaps, padding, or small errors.
- 1-4: partial, vague, or partly wrong.
- 0: ignores the request, or claims an action that is NOT in the tool list above.

IMPORTANT: the tools listed above REALLY ran — this assistant can take those
actions. Do NOT penalize the reply for saying it did something that appears in
that list; those claims are true. Only "hallucinating" counts against it when it
claims an action with no matching tool call.

Reply with ONLY a JSON object, no prose:
{{"score": <int 0-10>, "reason": "<one short sentence>"}}"""


def judge_client(provider: str | None = None, model: str | None = None):
    """The referee's client and resolved model id, for callers whose question is
    not the 0-10 rubric — the memory arena asks a binary "did this reply
    decline?". Sharing this keeps one definition of who the referee is: a model
    that isn't racing, configured in one place.

    Returns (client, model_id). get_client fills in the provider's default when
    the id is blank, so read the id back off settings rather than the argument.
    """
    settings = Settings(provider=provider or JUDGE_PROVIDER, model=model or JUDGE_MODEL,
                        small_model="", home=load_settings().home, apple_calendar=False)
    client = get_client(settings)
    return client, settings.model


def judge_reply(task: str, reply: str, provider: str | None = None,
                model: str | None = None, tools: list | None = None) -> dict | None:
    """Grade one reply. `tools` is the list of tool names that ACTUALLY fired this
    turn — passed to the judge as ground truth so a truthful "I saved that" (with
    save_note in the list) isn't mistaken for a hallucination. Returns
    {"score": 0-10, "reason": str, "judge": model} or None if there's nothing to
    grade or the judge is unreachable (a judge hiccup must never fail a race)."""
    if not (reply or "").strip():
        return None
    provider = provider or JUDGE_PROVIDER
    model = model or JUDGE_MODEL
    actions = (f"\nTools the assistant actually ran this turn (ground truth): "
               f"{', '.join(tools)}.\n" if tools else
               "\nThe assistant ran no tools this turn.\n")
    prompt = _RUBRIC.format(task=task[:2000], reply=reply[:4000], actions=actions)
    settings = Settings(provider=provider, model=model, small_model="",
                        home=load_settings().home, apple_calendar=False)
    # 比赛会同时判断每一列，因此终点会出现爆发，并且可能会
    # 429. 仅重试 API 调用（回退越来越大）；信号量上限如何
    # 许多同时运行。到达但无法解析的响应不是
    # 瞬态——不要浪费重试的机会。
    resp = None
    for attempt in range(4):
        try:
            client = get_client(settings)   # 填写提供者默认id
            with _JUDGE_SEM:
                resp = client.messages.create(
                    model=settings.model, max_tokens=300,
                    messages=[{"role": "user", "content": prompt}])
            break
        except Exception:
            if attempt < 3:
                time.sleep(1.2 * (attempt + 1))   # 1.2s、2.4s、3.6s — 让 429 清除
    if resp is None:
        return None
    try:
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        obj = json.loads(text[text.index("{"): text.rindex("}") + 1])
        score = max(0, min(10, int(obj["score"])))
        return {"score": score, "reason": str(obj.get("reason", ""))[:200], "judge": settings.model}
    except Exception:
        return None   # 收到响应，只是 JSON 无效 - 重试无济于事
