"""Tool registry — the 'Agentic Tools' box on the whiteboard.

A tool is three things: a name+description the model reads, a JSON schema for
its arguments, and a Python function that runs. That's it. (Registry pattern
adapted from launch-agentic-rag's app/agents/tools/registry.py.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]  # 工具返回模型观察到的字符串
    # 长时间运行的工具可以在工作时选择加入 STREAM 进度：设置
    # Wants_notify=True 并接受 `_notify(kind, event)` 关键字。循环的
    # 观察者被传递，因此网关/痕迹可以看到工具内部
    # （delegate_task 使用它来中继 pi 的实时事件）。下划线保留
    # 它脱离了面向模型的模式——模型从不提供它。
    wants_notify: bool = False

    def to_api(self) -> dict[str, Any]:
        """The shape the Messages API expects in its `tools=` parameter."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_api() for t in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any], notify=None) -> str:
        """Run one tool call safely: the model observes errors as text instead
        of crashing the loop (execute_tool_safely pattern)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            if tool.wants_notify:
                return tool.fn(**args, _notify=notify or (lambda kind, ev: None))
            return tool.fn(**args)
        except Exception as exc:  # 浮出水面，不要崩溃——模型可以重试
            return f"Error running {name}: {exc}"
