"""DETERMINISTIC EVAL — working-memory assembly is pure string logic.

Regression net for a live bug found on the dashboard: the agent had the date
but not the time, so it asked the user "what time is it?" before scheduling
something "in 30 minutes." The system prompt must carry a real clock.
"""

from __future__ import annotations

import re

from waku.config import load_settings
from waku.runtime.session import Session


def test_system_prompt_includes_current_time():
    settings = load_settings()
    settings.ensure_home()
    system = Session(settings, memory=None).build_system("what should I do in 30 minutes?")
    # 必须存在 HH:MM 时钟——而不仅仅是日期——因此模型永远不会有
    # 询问用户时间（实时错误）。
    assert re.search(r"\b\d{2}:\d{2}\b", system), "system prompt is missing a HH:MM time"
    assert "Right now it is" in system


def test_session_tags_history_with_its_session_id():
    # session只是一个session_id标签；新的会话带有默认值。
    settings = load_settings()
    assert Session(settings, memory=None).session_id == "default"
    s = Session(settings, memory=None)
    s.start_new("s-test")
    assert s.session_id == "s-test" and s.history == []


def test_system_prompt_includes_own_model_identity():
    """Live bug on the dashboard (K3 launch day): asked "what's ur model", the
    agent said it had no idea what it was running on. The system prompt must
    name the model + provider so the agent can answer honestly."""
    settings = load_settings()
    settings.ensure_home()
    settings.provider, settings.model = "kimi", "kimi-k3"
    system = Session(settings, memory=None).build_system("what model are you?")
    assert "kimi-k3" in system and "'kimi' provider" in system
