"""The wake-word matcher is a pure function — so it gets deterministic evals.
Whisper mangles phrases in predictable ways; these cases pin the fuzziness."""

import pytest

from waku.gateway.voice import matches_wake

SHOULD_WAKE = [
    ("waku waku", "waku waku"),
    ("Waku, waku!", "waku waku"),            # 标点
    ("wakuwaku", "waku waku"),               # 耳语掉落空间
    ("so anyway waku waku schedule it", "waku waku"),  # 嵌入言语中
    ("walku waku", "waku waku"),             # 单字母损坏 → 模糊匹配
    ("Hey Waku", "hey waku"),
    ("hey computer, what's up", "hey computer"),
    # 第一次现场会议的回归：耳语将唤醒词写在
    # 假名 — 逗号后的变体涵盖其他脚本
    ("わくわく", "waku waku,わくわく"),
    ("わくわくわく", "waku waku,わくわく"),
    ("小助手你好", "waku waku,小助手"),
]

SHOULD_NOT_WAKE = [
    ("what a nice day", "waku waku"),
    ("wake up call at nine", "waku waku"),
    ("", "waku waku"),
    ("waku waku", ""),                        # 没有配置唤醒词
    ("walk to work", "waku waku"),
]


@pytest.mark.parametrize("heard,wake", SHOULD_WAKE, ids=[h for h, _ in SHOULD_WAKE])
def test_wakes(heard, wake):
    assert matches_wake(heard, wake)


@pytest.mark.parametrize("heard,wake", SHOULD_NOT_WAKE, ids=[h or "empty" for h, _ in SHOULD_NOT_WAKE])
def test_stays_asleep(heard, wake):
    assert not matches_wake(heard, wake)
