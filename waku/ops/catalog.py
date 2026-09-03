"""The model catalog — what you can actually run, and the shortlist you curated.

Two related jobs, both feeding the settings model picker:

1. **What exists.** `list_models()` asks a provider what it can serve. There is
   no single way to ask: some endpoints publish an explicit catalog URL (kimi
   chats on the Anthropic wire but lists on its OpenAI-compatible one), most
   OpenAI-compatible endpoints answer `GET {base_url}/models`, and some — the
   Anthropic wire among them — have no listing at all, so we fall back to that
   provider's own known defaults. Cached 5 minutes; failures are cached ~1
   minute WITH the reason, so an unreachable catalog can't stall the
   dashboard's 5-second poll and still tells you why.

2. **What you chose.** `.waku/models.json` holds an ordered `provider:model`
   shortlist. The chat switcher shows exactly these — the built-in defaults are
   a starting point, never the menu. The first pinned model for a provider is
   that provider's default when you switch to it.

Writing the shortlist lives here (`save_pinned`); the pin/unpin HTTP action
lives in settings_api, because its reply is a whole settings payload. That
keeps the dependency pointing one way: settings_api -> catalog, never back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from waku.config import load_settings
from waku.ops.pricing import remember_price

_models_cache: dict[str, tuple[float, list]] = {}


def _known_default_ids(prov, out: dict, is_active: bool) -> list[dict]:
    """Best-effort model list when the live catalog is unreachable: the provider's
    flagship + fast + loop/gate defaults — so the showcase model (e.g. opus-4.8)
    is offered too, not just the two loop defaults — plus the active model when
    this is the active provider."""
    ids = [*(prov.default_pair() if prov else []),
           prov.model if prov else "", prov.small_model if prov else ""]
    if is_active:
        ids = [out.get("model"), out.get("small_model"), *ids]
    return [{"id": m} for m in dict.fromkeys(m for m in ids if m)]


def list_models(provider: str | None = None, *, use_cache: bool = True) -> dict:
    """Model ids available on a provider, for the settings model picker — the
    defaults are starting points, never the menu. Pass `provider` to list ANY
    provider's catalog (the "Your models" add-row picks a provider first);
    without it, the ACTIVE provider is used. Three sources: an explicit
    Provider.catalog_url (anthropic, kimi), GET {base_url}/models on
    OpenAI-compatible endpoints (OpenRouter, Gemini, any WAKU_BASE_URL), or the
    two known defaults when no catalog exists. OpenRouter entries carry free /
    tool-support / context metadata so the picker can surface the $0
    tool-capable models. Cached 5 minutes."""
    import time
    import urllib.request

    from waku.loop.models import PROVIDERS

    s = load_settings()
    # 显式提供程序会覆盖活动提供程序（及其自定义的 base_url：
    # WAKU_BASE_URL 仅适用于为其设置的提供商）。
    name = provider or s.provider
    prov = PROVIDERS.get(name)
    base = ((s.base_url if name == s.provider else None)
            or (prov.configured_base_url() if prov else None))
    out = {
        "provider": name,
        "model": s.model or (prov.model if prov else ""),
        "small_model": s.small_model or (prov.small_model if prov else ""),
        "endpoint": base or name,
    }
    # 哪里可以列出该提供商的型号？显式的catalog_url 获胜
    # （kimi 在人类线上聊天，但在其 OpenAI 兼容 API 上列出；
    # anthropic 本身有 GET /v1/models);否则 openai-wire 端点得到
    # {base_url}/模型；否则回退到两个已知的默认值。
    catalog_url = prov.catalog_for(base) if prov is not None else None
    if catalog_url:
        url = catalog_url
    elif prov is not None and prov.kind == "openai" and base:
        url = base.rstrip("/") + "/models"
    else:
        # 无目录端点：回退到提供商自己的已知默认值
        # （旗舰+快速+循环/门），不仅仅是活动模型。
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider)}

    cached = _models_cache.get(url) if use_cache else None
    if cached and time.time() - cached[0] < 300:
        _ts, cmodels, cerr = cached          # cerr 真实列表中没有
        r = {**out, "listed": cerr is None, "models": cmodels}
        if cerr:
            r["error"] = cerr
        return r
    # 使用该提供商自己的密钥； s.api_key 仅保存 ACTIVE 提供者的。
    key = ((s.api_key if name == s.provider else "") or os.getenv(prov.key_env, "")).strip()
    # HTTP 标头必须是 latin-1；带有杂散非 ASCII 字符的密钥（智能
    # 箭头/引用或错误粘贴的换行符）否则会使
    # 整个列表带有不透明的编解码器错误，并默默地回落到两个
    # 默认值。在这里抓住它，并附上一条实际上说明如何修复它的消息。
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        msg = (f"{prov.key_env} contains a non-ASCII character — re-paste the key "
               f"(no spaces, line breaks, or arrows).")
        return {**out, "listed": False,
                "models": _known_default_ids(prov, out, name == s.provider), "error": msg}
    # 发送两种身份验证样式 - OpenAI 兼容目录的 Bearer，x-api-key +
    # Anthropic 的版本；每个服务器读取它知道的标头。
    # 设置类似浏览器的用户代理：一些与 OpenAI 兼容的代理（例如
    # opencode.ai) 阻止 Python-urllib/3.x，并显示 403/错误代码 1010。
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "User-Agent": "Mozilla/5.0 (compatible; Waku)",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        # 显示服务器的实际原因（例如 xAI 的 403“无积分”），而不是
        # 只是“HTTP Error 403”——HTTPError 在 .read() 上携带主体。
        msg = str(exc)
        try:
            msg = f"{msg} — {exc.read().decode()[:160]}"
        except Exception:
            pass
        # 仍然提供提供商的已知默认值，因此选择器不为空
        known = _known_default_ids(prov, out, name == s.provider)
        # 将失败（默认值 + 原因）缓存约 1 分钟，因此无法访问
        # 目录不会每 5 秒仪表板轮询就停止 10 秒 — 因此
        # 缓存命中仍然显示默认值和原因，而不是空白列表。
        _models_cache[url] = (time.time() - 240, known, msg)
        return {**out, "listed": False, "models": known, "error": msg}
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        params = m.get("supported_parameters")
        entry = {
            "id": mid,
            "free": mid.endswith(":free") or pricing.get("prompt") == "0",
            # None 表示端点没有说明（只有 OpenRouter 报告这一点）
            "tools": ("tools" in params) if params is not None else None,
            # 推理模型会消耗 Token 进行思考，这会突破
            # Gate 较小的预算；界面会引导用户避开该模型槽位。
            "reasoning": ("reasoning" in params) if params is not None else None,
            "context": m.get("context_length"),
        }
        try:
            # OpenRouter 的价格以 $/Token 字符串提供；保留 $/M 以供展示和成本计算。
            pin, pout = float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
            remember_price(mid, pin, pout)
            entry["price_in"], entry["price_out"] = round(pin, 3), round(pout, 3)
        except (KeyError, TypeError, ValueError):
            pass
        models.append(entry)
    models.sort(key=lambda x: (not x["free"], x["tools"] is False, x["id"]))
    _models_cache[url] = (time.time(), models, None)   # 无错误 = 真实列表
    return {**out, "listed": True, "models": models}


def _models_json() -> Path:
    return load_settings().home / "models.json"


def default_pinned_specs() -> list[str]:
    """Starter shortlist before the user has curated their own: flagship + fast
    for every provider that has a key set (so the switcher only shows models you
    can actually use). Flagship comes first, so it's that provider's default."""
    from waku.loop.models import PROVIDERS

    specs = []
    for name, prov in PROVIDERS.items():
        if os.getenv(prov.key_env):
            specs += [f"{name}:{m}" for m in prov.default_pair()]
    return specs


def pinned_specs() -> list[str]:
    """The user's curated 'provider:model' shortlist (ordered), from
    .waku/models.json. The chat switcher shows exactly these. Before they've
    saved anything, fall back to the flagship+fast defaults."""
    p = _models_json()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("pinned", [])
        except (json.JSONDecodeError, OSError):
            pass
    return default_pinned_specs()


def default_model_for(provider: str) -> str:
    """A provider's default model = the FIRST one the user pinned for it.
    Empty string means 'use the provider's built-in default'."""
    for spec in pinned_specs():
        p, _, m = spec.partition(":")
        if p == provider and m:
            return m
    return ""


def save_pinned(specs: list[str]) -> None:
    """Persist the curated shortlist, in order. The ONLY writer of models.json —
    keep it that way so the file has one shape and one owner."""
    path = _models_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pinned": specs}, indent=1))
