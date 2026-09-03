"""DETERMINISTIC EVAL — the cross-model coding runner (waku.ops.coding_eval).

We can't call a real model in a hermetic test, so we stub pi with /bin/true (a
no-op that exits 0) and let the seeded files + the real `verify` command decide
the verdict. That exercises everything that isn't the model: file seeding,
provider/key guards, and — the whole point — that the score is the verify exit
code, not pi's prose."""

from __future__ import annotations

import shutil

from waku.ops import coding_eval as ce

_TRUE = shutil.which("true") or "/usr/bin/true"   # 真正的无操作二进制文件，退出 0


def _stub_pi(monkeypatch):
    monkeypatch.setattr(ce.shutil, "which", lambda name: _TRUE)


def test_verify_pass_is_the_verdict(tmp_path, monkeypatch):
    _stub_pi(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    case = {"id": "ok", "input": "n/a",
            "files": {"fizzbuzz.py": "def fizzbuzz(n):\n"
                      "    return 'FizzBuzz' if n%15==0 else 'Fizz' if n%3==0 else 'Buzz' if n%5==0 else str(n)\n"},
            "verify": "python3 -c \"from fizzbuzz import fizzbuzz; assert fizzbuzz(15)=='FizzBuzz'; print('ok')\""}
    passed, why, _secs = ce.run_coding_case("anthropic", "claude-opus-4-8", case)
    assert passed and why == "tests pass"


def test_verify_fail_when_code_is_wrong(tmp_path, monkeypatch):
    _stub_pi(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    case = {"id": "bad", "input": "n/a",
            "files": {"fizzbuzz.py": "def fizzbuzz(n):\n    return 'wrong'\n"},
            "verify": "python3 -c \"from fizzbuzz import fizzbuzz; assert fizzbuzz(3)=='Fizz'\""}
    passed, _why, _ = ce.run_coding_case("anthropic", "claude-opus-4-8", case)
    assert not passed                       # pi“跑”但代码验证失败


def test_missing_key_is_reported(tmp_path, monkeypatch):
    _stub_pi(monkeypatch)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    passed, why, _ = ce.run_coding_case("xai", "grok-4.5", {"id": "x", "input": "n/a"})
    assert not passed and "api key" in why


def test_unmapped_provider_is_reported(tmp_path, monkeypatch):
    _stub_pi(monkeypatch)
    passed, why, _ = ce.run_coding_case("nope", "whatever", {"id": "x", "input": "n/a"})
    assert not passed and "provider mapping" in why


def test_every_pinned_provider_maps_to_a_pi_provider():
    # 竞技场的固定提供者必须全部达到 pi，否则编码轮无法运行它们
    for prov in ("anthropic", "openai", "gemini", "kimi", "xai", "glm"):
        assert prov in ce.PI_PROVIDER


def test_coding_cases_load_and_have_verify():
    cases = ce.load_coding_cases()
    assert len(cases) >= 2
    for c in cases:
        assert "id" in c and "input" in c and "verify" in c


def test_coding_case_for_message_matches_trimmed():
    cases = [{"id": "fizz", "input": "Create fizzbuzz.py"}]
    assert ce.coding_case_for_message("  Create fizzbuzz.py ", cases)["id"] == "fizz"
    assert ce.coding_case_for_message("build a website", cases) is None


def test_stream_runner_scores_by_verify_and_emits_lines(tmp_path, monkeypatch):
    _stub_pi(monkeypatch)                       # pi = 无操作；种子文件提供代码
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    lines = []
    passed, why, _secs = ce.run_coding_stream(
        "anthropic", "claude-opus-4-8",
        task="n/a",
        files={"fizzbuzz.py": "def fizzbuzz(n):\n    return 'FizzBuzz' if n%15==0 else str(n)\n"},
        verify="python3 -c \"from fizzbuzz import fizzbuzz; assert fizzbuzz(15)=='FizzBuzz'\"",
        on_line=lines.append)
    assert passed is True and why == "tests pass"
    assert any(ln.startswith("$ pi") for ln in lines)   # 发射线流式传输


def test_stream_runner_free_form_has_no_verdict(tmp_path, monkeypatch):
    _stub_pi(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    passed, _why, _ = ce.run_coding_stream("anthropic", "claude-opus-4-8",
                                          task="build snake", files=None, verify=None,
                                          on_line=lambda _ln: None)
    assert passed is None            # 没有得分 -> 没有通过/失败，它只是运行
