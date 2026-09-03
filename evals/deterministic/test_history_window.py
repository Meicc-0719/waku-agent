"""DETERMINISTIC EVAL — working memory is a bounded sliding window.

Sean's insight while testing Telegram: its one always-on session accumulated
history forever, and every turn resent the whole thing (unbounded context ->
cost/latency climb -> eventual context-limit break). Working memory must be a
fixed window; older turns live in state.db + consolidation, not the prompt."""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_waku, response, text_block


def _gate_skip():
    return response([text_block('{"retrieve": false, "query": "", "reason": "t"}')])


def test_prompt_history_is_windowed(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKU_HISTORY_TURNS", "3")   # 只保留最后3回合
    sent = []

    class Recorder(ScriptedClient):
        def _create(self, **kwargs):
            # 现在截取消息计数——run_loop 改变同一个列表
            # （附助理回复）此电话返回后
            sent.append(list(kwargs.get("messages", [])))
            return self._script.pop(0)

    # 5 圈；每转 = 门调用（跳过）+ 一次循环调用
    script = []
    for _ in range(5):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_waku(tmp_path / "home", client=Recorder(script))
    for i in range(5):
        app.respond(f"message number {i}")

    # 最后一次循环调用的消息：最多 3 轮 * 2 行 + 新用户
    # message = 7，并且它不能包含最旧的回合
    last = sent[-1]
    assert len(last) <= 3 * 2 + 1, f"window not applied: {len(last)} messages"
    text_blob = " ".join(str(m.get("content", "")) for m in last)
    assert "message number 0" not in text_blob
    assert "message number 4" in text_blob   # 最新的转弯出现了


def test_default_window_is_generous_but_finite(tmp_path, monkeypatch):
    monkeypatch.delenv("WAKU_HISTORY_TURNS", raising=False)
    app = make_waku(tmp_path / "home", client=ScriptedClient([]))
    assert app.settings.history_turns == 12
