"""THE LOOP — observe → reason → act → repeat. This file is the whole trick.

Every agent framework is ultimately this while-loop with more indirection:

    while not done:
        response = llm(messages, tools)          # 原因
        if response asks for tools:
            results = run(tool_calls)            # 行为
            messages += results                  # 观察
        else:
            done                                 # 回复人类

End-loop guardrails (the orange box's exit conditions):
  1. the model stops asking for tools  → natural end of turn
  2. max_iterations reached            → hard stop, never spin forever
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

from waku.tools.registry import ToolRegistry

# 观察者让网关实时显示工具调用并让操作/跟踪记录
# 它们——没有被连接到循环的逻辑中。
LoopEvent = dict[str, Any]
Observer = Callable[[str, LoopEvent], None]


@dataclass
class LoopResult:
    reply: str
    tool_calls: list[LoopEvent] = field(default_factory=list)
    iterations: int = 0


def run_loop(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    messages: list[dict],
    tools: ToolRegistry,
    max_iterations: int = 10,
    max_tokens: int = 2048,
    observer: Observer | None = None,
    stream: bool = False,
) -> LoopResult:
    """Run one agent turn. `messages` is mutated in place — after the call it
    contains the full working memory of the turn (assistant thoughts, tool
    calls, tool results), which is exactly what gets traced.

    stream=True emits the assistant's text as it's generated (notify("text",
    {"delta": ...})) so a gateway can show it appear token by token — used by
    the dashboard. Falls back to a single call for clients without streaming."""
    notify = observer or (lambda kind, ev: None)
    result = LoopResult(reply="")
    can_stream = stream and hasattr(client.messages, "stream")

    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration

        # ---- 原因：使用当前工作内存进行一次 LLM 调用
        response = None
        if can_stream:
            try:
                with client.messages.stream(
                    model=model, system=system, messages=messages,
                    tools=tools.schemas(), max_tokens=max_tokens,
                ) as s:
                    for delta in s.text_stream:
                        notify("text", {"delta": delta})
                    response = s.get_final_message()
            except Exception:
                response = None  # 任何流媒体中断 → 退回到一个呼叫
        if response is None:
            response = client.messages.create(
                model=model,
                system=system,
                messages=messages,
                tools=tools.schemas(),
                max_tokens=max_tokens,
            )
        notify("llm", {"iteration": iteration, "stop_reason": response.stop_reason,
                       "usage": {"in": response.usage.input_tokens, "out": response.usage.output_tokens}})

        # 助理的轮流（文本和/或工具请求）加入工作记忆
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]

        # ---- 护栏 1：没有工具调用 → 模型正在与人类对话
        if not tool_uses:
            result.reply = "".join(b.text for b in response.content if b.type == "text")
            return result

        # ---- act：执行每个请求的工具；观察：反馈结果
        tool_results = []
        for call in tool_uses:
            output = tools.execute(call.name, call.input, notify=notify)
            event = {"tool": call.name, "args": call.input, "output": output}
            result.tool_calls.append(event)
            notify("tool", event)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": output}
            )
        messages.append({"role": "user", "content": tool_results})

    # ---- 护栏 2：迭代次数耗尽
    result.reply = "(I hit my iteration limit before finishing — try breaking the request into smaller steps.)"
    return result
