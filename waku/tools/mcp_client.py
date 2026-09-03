"""MCP connector — plug any Model Context Protocol server into Waku's tools.

Waku's loop is synchronous; the MCP SDK is async. The bridge below runs one
asyncio event loop on a daemon thread, holds every server's session on that loop
via a single AsyncExitStack (anyio requires the stack be entered/exited on the
same task), and lets the sync loop call tools via run_coroutine_threadsafe.

Config: WAKU_HOME/mcp.json — two transports, picked by which key is present.

  stdio: the client launches the server as a local subprocess.
  {"servers": [{"name": "fs", "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "env": {}}]}

  Streamable HTTP: the server is already running, somewhere else.
  {"servers": [{"name": "waku_memory", "url": "https://host/mcp",
                "auth_env": "WAKU_MEMORY_API_KEY"}]}

A remote server is authorised one of two ways, and naming both is refused:

  "auth_env": "VAR"   a long-lived key, held in an environment variable
  "oauth": true       the browser flow — no key to issue, none to hand over

`auth_env` names an environment variable, and never holds the credential
itself: mcp.json is a config file people paste into bug reports, and a
bearer token in one is a leaked credential. The variable's value is sent as
`Authorization: Bearer <value>`.

`oauth` signs in through the server's own page on first use and keeps the
result under WAKU_HOME/mcp-auth/. Nothing has to be issued out of band, which
is what makes a remote server installable by someone who does not know us.
See mcp_oauth.py.

Streamable HTTP is the MCP spec's transport for remote servers. The older
HTTP+SSE transport is deprecated and is deliberately not supported here.

Each server's tools register as `<server>_<tool>` on the ToolRegistry. A server
that fails to connect is skipped with a warning — Waku still starts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from waku.tools.registry import Tool


def _model_safe_name(server: str, tool: str) -> str:
    """`<server>_<tool>`, reduced to what a model provider will accept.

    Tool names reach Anthropic and OpenAI under `^[a-zA-Z0-9_-]{1,64}$`. MCP
    itself places no such limit, and dotted names are a common convention --
    waku-memory publishes `memory.remember`, `memory.recall` and four more --
    so a server whose names are perfectly legal MCP produced a request the
    provider rejected, on the first turn, before the model saw anything.

    Only the name the model reads is rewritten. The name sent back to the
    server is the original, so this cannot break dispatch.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}_{tool}")
    if len(safe) <= 64:
        return safe
    # 保留尾部：该工具自己的名称可以消除歧义，并且
    # 服务器前缀是读者可以承受丢失的部分。
    return safe[-64:].lstrip("_")


def _auth_hint(spec: dict, auth_dir: Path) -> str:
    """What to tell someone whose remote server would not connect.

    The SDK reports a rejected HTTP request as a generic "Server returned an
    error response" with no status on the exception, so a 401 and a 500 are
    indistinguishable by the time we see it. Name what the caller can actually
    check rather than inventing a cause — and name the right thing: an `oauth`
    server has no `auth_env`, so pointing its user at one sends them to a
    setting their config does not contain.
    """
    if spec.get("oauth"):
        return (
            f"  {spec['url']} — sign-in did not complete. Run waku again to use a token "
            f"that was saved, or delete {auth_dir} to start over"
        )
    return (
        f"  {spec['url']} — if the server requires auth, check that "
        f"{spec.get('auth_env') or 'auth_env'} holds a current credential"
    )


class MCPBridge:
    def __init__(self, config_path: Path, timeout: float = 30.0):
        self.config_path = config_path
        self.timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._stack: AsyncExitStack | None = None
        self._sessions: dict = {}

    def _deadline(self, servers: list[dict]) -> float:
        """How long to wait for every server to connect.

        Normally twice the per-call timeout. But a server configured for oauth
        with no token yet is about to open a browser and wait for a person to
        read a consent screen, and that is minutes. Waiting the ordinary amount
        means giving up while the browser is still open — which looks like a
        broken server and leaves the sign-in half-finished.

        The margin over SIGN_IN_TIMEOUT matters: expiring first would mean the
        callback is still patiently listening on a connection nobody is holding
        any more.
        """
        from waku.tools.mcp_oauth import SIGN_IN_TIMEOUT

        if any(s.get("oauth") and not self._token_exists(s) for s in servers):
            return SIGN_IN_TIMEOUT + 60.0
        return self.timeout * 2

    def start(self) -> list[Tool]:
        """Connect every configured server and return their tools (as Tools)."""
        self._thread.start()
        servers = json.loads(self.config_path.read_text(encoding="utf-8")).get("servers", [])
        fut = asyncio.run_coroutine_threadsafe(self._connect_all(servers), self._loop)
        listed = fut.result(self._deadline(servers))  # {服务器：[工具元]}
        tools: list[Tool] = []
        for srv, metas in listed.items():
            for meta in metas:
                tools.append(Tool(
                    name=_model_safe_name(srv, meta["name"]),
                    description=f"[MCP:{srv}] {meta.get('description','') or ''}",
                    input_schema=meta.get("inputSchema") or {"type": "object", "properties": {}},
                    # `tname` 是服务器自己的名称，未经消毒 -
                    # 上面的重命名只是模型引用工具的方式，
                    # 永远不会通过电线传回什么。
                    fn=(lambda srv=srv, tname=meta["name"], **kw: self.call(srv, tname, kw)),
                ))
        return tools

    async def _open_streams(self, spec: dict):
        """Connect one server and return its (read, write) streams.

        The transport is chosen by the config's shape rather than by a
        `transport` field: a server entry either names a local command or a
        remote url, and one that somehow names both is a mistake worth
        refusing rather than resolving by precedence.
        """
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        url = spec.get("url")
        if url and spec.get("command"):
            raise ValueError("server has both 'url' and 'command' — pick one transport")

        if not url:
            params = StdioServerParameters(
                command=spec["command"], args=spec.get("args", []), env=spec.get("env") or None
            )
            return await self._stack.enter_async_context(stdio_client(params))

        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        headers = {}
        auth = None
        auth_env = spec.get("auth_env")
        use_oauth = bool(spec.get("oauth"))

        if auth_env and use_oauth:
            # 与上面的 command-vs-url 的推理相同：两个名为的凭据，
            # 没有明确的优先顺序，因此拒绝击败默默地选择一个。
            raise ValueError("server names both 'auth_env' and 'oauth' — pick one")

        if auth_env:
            token = os.environ.get(auth_env)
            if not token:
                # 此处失败而不是匿名连接：服务器
                # 会回答 401 并且工具会丢失，
                # 它读作“服务器已关闭”而不是“你做了
                # 不导出密钥”。
                raise ValueError(f"{auth_env} is not set (named by 'auth_env' in mcp.json)")
            headers["Authorization"] = f"Bearer {token}"
        elif use_oauth:
            from waku.tools.mcp_oauth import build_provider

            # httpx 身份验证流程，而不是标头：SDK 驱动 401，
            # 发现和刷新本身，所以这是交给
            # 客户曾经有过一次，再也没有想过。
            auth = build_provider(url, spec["name"], self.config_path.parent)

        # create_mcp_http_client 应用 SDK 自己的超时并
        # 跟随_重定向；标头或身份验证流程是两种受支持的方式
        # 来验证此传输。因为我们构建的是客户端
        # 我们拥有它的生命周期，而不是让交通工具建造它——因此
        # 我们自己将其输入堆栈。
        client = await self._stack.enter_async_context(
            create_mcp_http_client(headers=headers, auth=auth)
        )
        return await self._stack.enter_async_context(
            streamable_http_client(url, http_client=client)
        )

    def _token_exists(self, spec: dict) -> bool:
        """Whether a sign-in has already produced a token for this server.

        Read from disk rather than remembered, because the token is written by
        the SDK's auth flow on a different task than this one.
        """
        if not spec.get("oauth"):
            return False
        from waku.tools.mcp_oauth import FileTokenStorage

        return FileTokenStorage(self.config_path.parent, spec["name"])._path.exists()

    async def _connect_one(self, spec: dict) -> list[dict]:
        """Open one server's session and return its tool metadata."""
        from mcp import ClientSession

        name = spec["name"]
        read, write = await self._open_streams(spec)
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[name] = session
        tools = (await session.list_tools()).tools
        # SDK 2.x 行中的“input_schema”；它是 1.x 中的“inputSchema”。
        # getattr 涵盖​​了两者，因此任一引脚上的用户都可以获得工作连接器
        # 而不是报告为“连接失败”的 AttributeError。
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None),
            }
            for t in tools
        ]

    async def _connect_all(self, servers) -> dict:
        self._stack = AsyncExitStack()
        listed: dict = {}
        for spec in servers:
            name = spec["name"]
            had_token = self._token_exists(spec)
            try:
                listed[name] = await self._connect_one(spec)
            except Exception as first_exc:  # 一台坏服务器不应该阻止其余服务器
                # 绑定到我们自己的名称：Python 解除绑定“ except … as”
                # 变量位于块的末尾，因此下面的重试不能
                # 重新分配它。
                error = first_exc
                # 首次登录会在浏览器中花费更长的时间
                # 比触发它的连接愿意等待。经过
                # 写入令牌时尝试已经失败，
                # 所以让你登录的那次跑步似乎不是
                # 去工作。仅当现在存在令牌时才重试一次
                # 之前没有：该条件恰好在 a 之后为真
                # 登录，其他所有失败则为 false。
                if not had_token and self._token_exists(spec):
                    print(f"  signed in — reconnecting to '{name}'")
                    try:
                        listed[name] = await self._connect_one(spec)
                        continue
                    except Exception as retry_exc:
                        error = retry_exc

                print(f"MCP server '{name}' failed to connect: {error}")
                # ValueError是上面这个模块自己的配置拒绝；它
                # 已经准确说明出了什么问题，并添加了身份验证提示
                # 它会指向错误的东西。
                if spec.get("url") and not isinstance(error, ValueError):
                    print(_auth_hint(spec, self.config_path.parent / "mcp-auth"))
        return listed

    def call(self, server: str, tool: str, args: dict) -> str:
        try:
            fut = asyncio.run_coroutine_threadsafe(self._acall(server, tool, args), self._loop)
            return fut.result(self.timeout)
        except Exception as exc:
            return f"MCP call {server}_{tool} failed: {exc}"

    async def _acall(self, server: str, tool: str, args: dict) -> str:
        session = self._sessions.get(server)
        if session is None:
            return f"MCP server '{server}' is not connected."
        result = await session.call_tool(tool, args)
        parts = []
        for block in result.content:
            parts.append(getattr(block, "text", None) or "[non-text content]")
        return "\n".join(parts) or "(no output)"

    def close(self) -> None:
        if self._stack is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop).result(10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
