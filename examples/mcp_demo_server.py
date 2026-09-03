"""A tiny, self-contained MCP server — the demo connector for waku-agent.

Most MCP examples need Node/npx. This one is pure Python (only the `mcp` extra),
so the connector story runs with zero extra installs:

    pip install -e '.[mcp]'
    cp examples/mcp.demo.json .waku/mcp.json
    make dashboard          # 其工具显示在工具 > 可用 > MCP 服务器下

Its tools register as `demo_word_count` and `demo_reverse_text`. Swap in your own
@mcp.tool() functions, or point mcp.json at any real MCP server the same way —
that's the whole point: connectors plug in without changing Waku's code.
"""

from __future__ import annotations

# `MCPServer` 是 SDK 的 2.x 系列对 1.x 类的调用
# `mcp.server.fastmcp.FastMCP`。旧的导入路径已消失，但并未弃用，
# 所以这个文件在安装的 SDK 下引发了 ModuleNotFoundError 直到
# 2026年8月26日。下面的装饰器 API 没有变化。
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo")


@mcp.tool()
def word_count(text: str) -> str:
    """Count the words and characters in a piece of text."""
    return f"{len(text.split())} words, {len(text)} characters"


@mcp.tool()
def reverse_text(text: str) -> str:
    """Reverse a string (handy for proving the connector round-trips)."""
    return text[::-1]


if __name__ == "__main__":
    # 默认情况下为 stdio — Waku 的 MCPBridge 如何与本地服务器通信。
    # `--http` 通过 Streamable HTTP 运行相同的两个工具，即
    # 它如何与远程人交谈。相同的工具，相同的代码：仅传输
    # 不同，这是值得一看的。
    #
    #   python examples/mcp_demo_server.py --http --port 8931
    #   # 然后在 .waku/mcp.json 中：
    #   {"servers": [{"name": "demo", "url": "http://127.0.0.1:8931/mcp"}]}
    import argparse

    parser = argparse.ArgumentParser(description="waku-agent's demo MCP server")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP")
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()
