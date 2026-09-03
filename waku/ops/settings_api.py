"""The small Settings surface left after Connections owns integrations.

Provider credentials, models, memory backends, search and gateways are managed
by :mod:`waku.integrations`. This module retains the Experimental and
Graph-workflows toggles and the model pin action; catalog remains the sole
owner of pin persistence.
"""

from __future__ import annotations

import shutil

from waku.config import load_settings
from waku.loop.models import PROVIDERS
from waku.ops import catalog


def pin_action(payload: dict) -> dict:
    """Manage the curated model shortlist: pin / unpin / make-default."""
    action = payload.get("action")
    provider, model = payload.get("provider", ""), payload.get("model", "")
    if not provider or not model:
        return {"error": "provider and model required"}
    spec = f"{provider}:{model}"
    specs = [s for s in catalog.pinned_specs() if s != spec]
    if action == "pin":
        specs.append(spec)
    elif action == "default":
        # 移动到其提供者组的前面 -> 成为该提供者的默认值
        idx = next((i for i, s in enumerate(specs) if s.split(":", 1)[0] == provider), len(specs))
        specs.insert(idx, spec)
    elif action != "unpin":
        return {"error": f"unknown action {action}"}
    catalog.save_pinned(specs)
    return {"ok": True, **settings_info()}


def settings_info() -> dict:
    """Current provider/model + which keys are set — masked to last-4, never
    the full key. `pinned` is the user's curated model shortlist (the chat
    switcher shows exactly these, across providers)."""
    s = load_settings()
    # 按顺序策划的入围名单；每个提供商的第一个固定模型是
    # 该提供商的默认值（在您切换提供商时使用）。
    pinned, seen = [], set()
    for spec in catalog.pinned_specs():
        p, _, m = spec.partition(":")
        if m:
            pinned.append({"provider": p, "model": m, "default": p not in seen})
            seen.add(p)
    # 按提供者分组进行显示（因此一个实验室的所有模型都坐在一起，
    # 例如后期添加的 claude-fable-5 加入了其他人择行）。稳定
    # 按提供商的首次出现顺序排序保留每个提供商自己的顺序 —
    # 所以它的默认值（第一个固定的）保留在顶部，并且上面的“默认”标志
    # 还在排队。
    prov_order: dict = {}
    for row in pinned:
        prov_order.setdefault(row["provider"], len(prov_order))
    pinned.sort(key=lambda row: prov_order[row["provider"]])
    # 以与循环相同的方式求解模型。 `waku/loop/models.py` 填充
    # 在构建时将提供程序的默认值设置为空白 WAKU_MODEL，因此代理是
    # 总是在运行一些东西——但这就是导航丸和模型的意思
    # 页面渲染，并报告“”进行了全新安装，显示“人性化·”，
    # 没有型号名称的尾随分隔符。显示器不得声称少
    # 比代理人实际拥有的多。
    prov = PROVIDERS.get(s.provider)
    return {
        "provider": s.provider,
        "model": s.model or (prov.model if prov else ""),
        "small_model": s.small_model or (prov.small_model if prov else ""),
        "base_url": s.base_url or "",
        "custom_key_set": bool(s.api_key),
        # 用户在模型网格中禁用的提供商的 ID；前端
        # 获取每张卡的状态（未配置/配置/启用）并
        # 从聊天切换器中隐藏禁用的提供商。
        "disabled_providers": sorted(s.disabled_providers),
        "pinned": pinned,
        "providers": [{"name": name} for name in PROVIDERS],
        # 实验工具（delegate_task -> pi）。 ARENA 可以打开此功能
        # 每场比赛，但聊天代理从环境中读取它 - 所以没有
        # 此处切换，侧边栏聊天永远无法委托。请参阅设置_保存。
        "experimental": s.experimental,
        "pi_installed": bool(shutil.which("pi")),
        # 图形工作流程（分类优先轮次）——与以下相同的切换合约
        # 实验性的：UI 渲染它，apply_settings 写入它。
        "graph_workflows": s.graph_workflows,
    }


def apply_settings(payload: dict) -> dict:
    """Save the remaining Settings concerns: the experimental and graph-workflows
    toggles.

    Connection fields and provider changes deliberately live in integrations.
    """
    import os

    from dotenv import find_dotenv, set_key

    if "episodic_store" in payload:
        return {"error": "episodic_store is managed in Connections"}
    env_path = find_dotenv(usecwd=True) or ".env"
    # 不是“iftoggle:”——将其关闭会发送“”，这是错误的。缺席（无）
    # 意思是“不要碰”； “”的意思是“关掉它”。
    toggles = (("experimental", "WAKU_EXPERIMENTAL"), ("graph_workflows", "WAKU_GRAPH_WORKFLOWS"))
    for field, env_name in toggles:
        value = payload.get(field)
        if value is not None:
            value = "1" if str(value).strip() else ""
            set_key(env_path, env_name, value)
            os.environ[env_name] = value
    return {"ok": True, **settings_info()}
