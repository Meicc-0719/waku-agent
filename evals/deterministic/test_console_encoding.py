"""The CLI must survive a legacy-codepage console (issue #140).

Windows consoles default to cp1252, which cannot encode the arrows and
middots in our output. Before the fix, `waku dashboard` crashed with
UnicodeEncodeError while printing the startup banner — before the server
even started. The entrypoint now reconfigures stdout/stderr with
errors="replace", so unencodable characters degrade to "?" instead of
killing the process. This test forces the failing encoding on any OS.
"""

import os
import subprocess
import sys


def test_usage_text_survives_a_cp1252_console():
    """`waku <unknown>` prints the usage text, which contains a real arrow.
    Under a cp1252 stdout that print must degrade, not raise."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    # 比较字节：子进程的输出是 cp1252，并用
    # 父级自己的 stdio 编码将使此测试依赖于主机。
    result = subprocess.run(
        [sys.executable, "-m", "waku", "definitely-not-a-command"],
        capture_output=True, env=env, timeout=60, check=False,
    )
    assert b"UnicodeEncodeError" not in result.stderr
    assert result.returncode == 1  # 使用路径正常退出
    assert b"waku dashboard" in result.stdout  # 实际打印的使用文本
