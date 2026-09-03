"""配置——每项开关均为环境变量，并在 .env.example 中说明。

不使用配置框架：启动时只读取一次 dataclass。读懂此文件即可了解 Waku 的全部可配置能力。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _load_env() -> str:
    """按用户预期查找 .env：从当前所在位置开始。

    直接调用 `load_dotenv()` 会从调用它的文件而非工作目录向上搜索。在 Git 检出中该问题
    不明显——config.py 位于项目内，向上搜索会找到项目的 .env，一切正常。通过 PyPI 安装后，
    它会从 site-packages 向上搜索到文件系统根目录而一无所获：用户所在文件夹明明有有效 .env，
    Waku 却报告“没有 API 密钥”。该问题于 2026-07-31 在仓库目录内的全新安装中被报告。

    完整修复就是 `usecwd=True`。有意保留向上搜索，因此在项目子目录中运行 `waku` 仍可找到
    根目录的 .env——这是 Git、npm 和 pytest 已经让用户熟悉的规则。

    返回已加载文件的路径（若没有则为空字符串），以便 `waku doctor` 和首次运行错误明确指出
    读取了*哪个*文件，而不是让用户在多个 .env 文件之间猜测。
    """
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path)
    return path


DOTENV_PATH = _load_env()


@dataclass
class Settings:
    # --- LLM：选择供应商并配置其密钥。见 waku/loop/models.py 的 PROVIDERS。
    provider: str = field(default_factory=lambda: os.getenv("WAKU_PROVIDER", "anthropic"))
    # 显式覆盖项（可选）：密钥、端点和模型 ID。留空时使用供应商自身的密钥环境变量和默认模型。
    api_key: str = field(default_factory=lambda: os.getenv("WAKU_API_KEY", ""))
    base_url: str | None = field(default_factory=lambda: os.getenv("WAKU_BASE_URL") or None)
    model: str = field(default_factory=lambda: os.getenv("WAKU_MODEL", ""))
    # 供检索闸门和巩固摘要器使用的低成本模型。
    small_model: str = field(default_factory=lambda: os.getenv("WAKU_SMALL_MODEL", ""))
    # 用户在仪表盘中关闭的供应商（以逗号分隔的 ID）。已禁用供应商在选择器/切换器中隐藏；
    # 当前活动供应商不能被禁用（由 integrations.apply_provider_disabled 保护）。
    disabled_providers: frozenset[str] = field(default_factory=lambda: frozenset(
        p.strip() for p in os.getenv("WAKU_DISABLED_PROVIDERS", "").split(",") if p.strip()))

    # --- 主目录：Waku 保存状态的位置（记忆数据库、日历、发件箱、追踪记录）。默认位于运行目录
    # 下的 ./.waku，因此用户可打开它写入的每个文件。本地优先意味着内容始终可见。
    home: Path = field(default_factory=lambda: Path(os.getenv("WAKU_HOME", ".waku")))

    # --- 循环护栏
    max_iterations: int = field(default_factory=lambda: int(os.getenv("WAKU_MAX_ITERATIONS", "10")))
    # 为推理模型（kimi-k3、gpt-5.x、gemini-*-pro）预留空间很重要：它们会在回答前消耗
    # 输出 token 思考，过低上限会使它们在思考中途触发 stop_reason=max_tokens 并返回空回复
    # （kimi-k3 在 2048 时确实发生过）。8192 同时为思考和回答留出空间；它是上限而非目标，
    # 高效模型成本不变。
    max_tokens: int = field(default_factory=lambda: int(os.getenv("WAKU_MAX_TOKENS", "8192")))
    # 工作记忆是滑动窗口（类似上下文 RAM）：仅最近 N 轮进入提示词。较早轮次不会丢失——它们
    # 位于 state.db 中，由巩固流程提炼为事实，并在相关时由检索闸门取回。若没有上限，长会话
    # （尤其是常驻 Telegram 会话）会在每轮重复发送全部历史，直至上下文溢出。
    history_turns: int = field(default_factory=lambda: int(os.getenv("WAKU_HISTORY_TURNS", "12")))

    # --- 记忆
    # 仅在新增 N 轮交流后进行巩固（将对话提炼为持久事实）。
    consolidate_every: int = field(default_factory=lambda: int(os.getenv("WAKU_CONSOLIDATE_EVERY", "6")))
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("WAKU_RETRIEVAL_TOP_K", "4")))
    # 'sqlite'（默认，零配置）或 'supabase'（pgvector 升级路径，见 launch-rag）。
    semantic_store: str = field(default_factory=lambda: os.getenv("WAKU_SEMANTIC_STORE", "sqlite"))
    # 'sqlite'（默认，零配置）或 'notion'（情景记录存放在 Notion 数据库）。
    episodic_store: str = field(default_factory=lambda: os.getenv("WAKU_EPISODIC_STORE", "sqlite"))

    # --- 工具
    # 通过 AppleScript 将创建的事件同步到 Apple 日历（专用“Waku”日历）。此项需主动启用，
    # 因为它会写入真实日历应用。
    apple_calendar: bool = field(
        default_factory=lambda: os.getenv("WAKU_APPLE_CALENDAR", "") in ("1", "true", "yes")
    )
    # 将本地创建的事件镜像到 Google 日历。SQLite + ICS 仍是事实来源；这仅是可选写入目标。
    google_calendar: bool = field(
        default_factory=lambda: os.getenv("WAKU_GOOGLE_CALENDAR", "") in ("1", "true", "yes")
    )
    google_calendar_id: str = field(
        default_factory=lambda: os.getenv("WAKU_GOOGLE_CALENDAR_ID", "") or "primary"
    )
    # 授予代理对 Apple 日历、邮件、提醒事项、备忘录的读写权限（macOS；首次使用会触发
    # 系统自动化权限提示）。
    apple_tools: bool = field(
        default_factory=lambda: os.getenv("WAKU_APPLE_TOOLS", "") in ("1", "true", "yes")
    )
    # 通过 `gh` CLI 自身认证提供只读 GitHub 访问（此处不使用令牌）。默认关闭是刻意设计：
    # 每个已注册工具都会进入每个提示词，读取 PR 是维护者能力而非助手默认能力。gather 工作流
    # 以库方式调用 waku/tools/github.py，不需要开启此项——该开关只决定模型能否访问该工具。
    gh_tool: bool = field(
        default_factory=lambda: os.getenv("WAKU_GH_TOOL", "") in ("1", "true", "yes")
    )
    # 调用中省略 owner/name 时使用的默认值，适用于在检出目录外运行 Waku、`gh` 无远端可推断的情况。
    gh_repo: str = field(default_factory=lambda: os.getenv("WAKU_GH_REPO", ""))
    # 注册实验性工具（delegate_task -> pi 子代理等）。环境变量是全局开关；竞技场会按场次设置，
    # 因此编程竞速可将工作交给 pi，而无需为整个进程开启此项。
    experimental: bool = field(
        default_factory=lambda: os.getenv("WAKU_EXPERIMENTAL", "") in ("1", "true", "yes")
    )
    # 先将每条消息路由到分诊图工作流（小模型分类；简单消息得到快速小模型回复，真实任务以图节点
    # 运行正常循环）。任何位置失败都会回退至普通循环，因此该功能不会降低 Waku 表现，只会更快或更省。
    graph_workflows: bool = field(
        default_factory=lambda: os.getenv("WAKU_GRAPH_WORKFLOWS", "") in ("1", "true", "yes")
    )

    # --- 可选渠道
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    whatsapp_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_TOKEN", ""))
    whatsapp_phone_number_id: str = field(
        default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    )

    # --- 追踪（始终记录 JSONL；设置端点时导出 OTel）
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "traces").mkdir(exist_ok=True)
        (self.home / "outbox").mkdir(exist_ok=True)
        return self.home


def load_settings() -> Settings:
    return Settings()
