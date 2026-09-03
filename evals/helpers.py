"""Shared eval plumbing: a scripted fake LLM client for offline tests, and a
real-Waku factory for live ones."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace


def _has_key() -> bool:
    """True when the ACTIVE provider (WAKU_PROVIDER) has its key set, so live
    evals run on whatever the user actually configured (anthropic, openrouter,
    gemini, ...), not only on ANTHROPIC_API_KEY."""
    from waku.config import load_settings
    from waku.loop.models import PROVIDERS

    settings = load_settings()
    provider = PROVIDERS.get(settings.provider)
    return bool(settings.api_key or (provider and os.getenv(provider.key_env)))


HAS_KEY = _has_key()


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, args: dict, call_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        content=blocks,
    )


class ScriptedClient:
    """Plays back a fixed list of responses — the 'model' for offline tests."""

    def __init__(self, script: list):
        self._script = list(script)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return self._script.pop(0)


def make_waku(home: Path, client=None, **settings_overrides):
    """Build a Waku with an isolated home dir; optionally swap in a fake client."""
    from waku.app import Waku
    from waku.config import Settings

    # 测试必须描述它自己的世界。 `waku/config.py` 调用 load_dotenv()
    # 在导入时，因此每个 Settings() 默认值都是从任何内容中悄悄播种的
    # 在维护者的 .env 中 - 以及读取开发人员机器的测试
    # 不是确定性的。下面的每个条目都是一个开关，可以更改
    # 转 DOES，除非测试要求，否则被锁定：
    #   apple/google_calendar 到达真实日历（网络+Mac）
    #   apple_tools 注册另外四个工具并进入 macOS
    #   graph_workflows 通过分类图路由每条消息，
    #                          额外花费一次模型调用 — 2026 年 7 月 31 日
    #                          陈旧的 WAKU_GRAPH_WORKFLOWS=1 吃了脚本
    #                          响应并未通过 CI 中通过的 8 项测试
    # 未固定在这里：“实验性”。 test_delegate.py 驱动它
    # Monkeypatch.setenv 来证明环境变量确实可以进行注册，并且
    # 此处硬编码 False 会使该接线无法测试。仅固定开关
    # 当没有测试需要观察环境到达设置时。
    #
    # 仅固定实际具有的开关设置。传递一个未知的名字
    # 是一个类型错误，因此硬编码列表会在标志出现时中断
    # 重命名或仅存在于功能分支上 - 这正是
    # 这里发生了“graph_workflows”。过滤使该列表成为超集
    # 当条目不存在时，这不需要任何费用。
    known = {f.name for f in dataclasses.fields(Settings)}
    for switch in ("apple_calendar", "google_calendar", "apple_tools", "graph_workflows"):
        if switch in known:
            settings_overrides.setdefault(switch, False)
    settings = Settings(home=home, **settings_overrides)
    if client is not None and not settings.api_key:
        settings.api_key = "offline"  # 永远不要读取脚本运行的真正密钥
    return Waku(settings=settings, client=client)
