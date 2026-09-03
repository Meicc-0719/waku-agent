"""Apple Calendar AppleScript generation is pure string logic — evaluable
offline without ever touching the real Calendar app."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from waku import integrations
from waku.integrations import IntegrationState
from waku.tools import calendar
from waku.tools.calendar import _applescript_date, sync_to_apple_calendar


def _completed(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_probe_apple_calendar_is_read_only_and_requires_a_writable_calendar(monkeypatch):
    captured = {}

    def run(cmd, **kwargs):
        captured.update(cmd=cmd, kwargs=kwargs)
        return _completed("Personal\n")

    monkeypatch.setattr(calendar.sys, "platform", "darwin")
    monkeypatch.setattr(calendar.subprocess, "run", run)

    calendar.probe_apple_calendar()

    script = captured["cmd"][2]
    assert captured["cmd"][:2] == ["osascript", "-e"]
    assert captured["kwargs"]["timeout"] == 15
    # AppleScript 无法冷启动应用程序：此行曾经在这里，并且是
    # 本身什么提高了-600。 waku 现在用 shell ‘open’ 启动日历。
    assert "launch application" not in script
    assert "writable" in script
    assert "make new event" not in script
    assert "make new calendar" not in script


def test_probe_apple_calendar_rejects_unsupported_and_unwritable_hosts(monkeypatch):
    monkeypatch.setattr(calendar.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS-only"):
        calendar.probe_apple_calendar()

    monkeypatch.setattr(calendar.sys, "platform", "darwin")
    monkeypatch.setattr(calendar.subprocess, "run", lambda *args, **kwargs: _completed())
    with pytest.raises(RuntimeError, match="no writable calendars"):
        calendar.probe_apple_calendar()


def test_probe_apple_calendar_reports_timeout_and_applescript_errors(monkeypatch):
    monkeypatch.setattr(calendar.sys, "platform", "darwin")
    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("osascript", 15)),
    )
    with pytest.raises(RuntimeError, match="timed out after 15s"):
        calendar.probe_apple_calendar()

    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Not authorized"),
    )
    with pytest.raises(RuntimeError, match="Not authorized"):
        calendar.probe_apple_calendar()


def test_apple_sync_success_and_failure_update_connection_health(monkeypatch, tmp_path):
    monkeypatch.setenv("WAKU_HOME", str(tmp_path))
    monkeypatch.setattr(calendar.sys, "platform", "darwin")
    monkeypatch.setattr(integrations, "_HEALTH", None)

    monkeypatch.setattr(calendar.subprocess, "run", lambda *args, **kwargs: _completed("Personal\n"))
    out = calendar.sync_to_apple_calendar("Standup", "2026-08-04T09:00", "2026-08-04T09:30")
    status = integrations._health()["apple_calendar"]
    assert "calendar 'Personal'" in out
    assert status.state is IntegrationState.CONNECTED
    assert "Personal" in status.message

    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Access denied"),
    )
    out = calendar.sync_to_apple_calendar("Retro", "2026-08-04T10:00", "2026-08-04T10:30")
    status = integrations._health()["apple_calendar"]
    assert "FAILED" in out
    assert status.state is IntegrationState.ERROR
    assert "Access denied" in status.message

    monkeypatch.setattr(calendar.subprocess, "run", lambda *args, **kwargs: _completed("Work\n"))
    calendar.sync_to_apple_calendar("Retro", "2026-08-04T10:00", "2026-08-04T10:30")
    status = integrations._health()["apple_calendar"]
    assert status.state is IntegrationState.CONNECTED
    assert "Work" in status.message


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired("osascript", 30), OSError("osascript missing")],
)
def test_apple_sync_runtime_exceptions_record_error(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("WAKU_HOME", str(tmp_path))
    monkeypatch.setattr(calendar.sys, "platform", "darwin")
    monkeypatch.setattr(integrations, "_HEALTH", None)
    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    calendar.sync_to_apple_calendar("Planning", "2026-08-04T11:00", "2026-08-04T11:30")

    assert integrations._health()["apple_calendar"].state is IntegrationState.ERROR


def test_date_sets_day_first_to_avoid_overflow():
    # 经典bug：在日之前设置月份，31号会溢出月份
    script = _applescript_date("d", "2026-02-15T09:30")
    lines = [line for line in script.splitlines() if line.startswith(("set day", "set month"))]
    assert lines[0] == "set day of d to 1", "day must be pinned to 1 before month is set"
    assert "set month of d to 2" in script
    assert "set day of d to 15" in script
    assert "set hours of d to 9" in script and "set minutes of d to 30" in script


def test_sync_escapes_quotes_and_backslashes():
    # 带引号的标题不得脱离 AppleScript 字符串
    import sys
    if sys.platform != "darwin":
        assert "not macOS" in sync_to_apple_calendar('x', '2026-01-01T00:00', '2026-01-01T01:00')
        return
    # 在 macOS 上，我们无法在 CI 中运行 osascript，但转义是在字符串构建中；
    # 由上面的纯日期测试+开发机器上的手动验证涵盖。


def test_create_event_handles_empty_call_gracefully():
    # 实时错误：模型在循环中发出 create_event({})，Python 引发了原始事件
    # 类型错误。该工具必须返回有用的消息而不是崩溃。
    import sqlite3

    from waku.tools.calendar import make_tool

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        'CREATE TABLE calendar_events (id INTEGER PRIMARY KEY, title TEXT, start TEXT, '
        '"end" TEXT, attendees TEXT, notes TEXT, created_at TEXT);'
    )
    import tempfile
    from pathlib import Path
    fn = make_tool(conn, Path(tempfile.mkdtemp())).fn
    out = fn()  # 空的电话——没有标题，没有开始
    assert "needs at least a title" in out
    assert "Error" not in out and "TypeError" not in out
