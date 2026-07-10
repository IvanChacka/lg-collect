from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class FeishuBotConfig:
    api_base: str
    app_id: str
    app_secret: str
    chat_id: str | None = None


_TOKEN_CACHE: dict[str, Any] = {"token": "", "expire_at": 0.0}


def _require_cfg(cfg: dict[str, Any]) -> FeishuBotConfig:
    api_base = str(cfg.get("feishu_api_base") or "").strip() or "https://open.feishu.cn"
    app_id = str(cfg.get("feishu_app_id") or "").strip()
    app_secret = str(cfg.get("feishu_app_secret") or "").strip()
    chat_id = str(cfg.get("feishu_chat_id") or "").strip() or None
    if not app_id or not app_secret:
        raise RuntimeError(
            "Feishu 配置缺失：请在 .env 或 runnable config 中提供 FEISHU_APP_ID / FEISHU_APP_SECRET"
        )
    return FeishuBotConfig(api_base=api_base, app_id=app_id, app_secret=app_secret, chat_id=chat_id)


def get_tenant_access_token(*, cfg: dict[str, Any]) -> str:
    """
    获取 tenant_access_token（内部应用）。

    返回值会做进程内缓存（按 expire 过期）。
    """

    bot = _require_cfg(cfg)
    now = time.time()
    cached = str(_TOKEN_CACHE.get("token") or "")
    expire_at = float(_TOKEN_CACHE.get("expire_at") or 0)
    if cached and now < expire_at - 30:
        return cached

    url = f"{bot.api_base}/open-apis/auth/v3/tenant_access_token/internal"
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json={"app_id": bot.app_id, "app_secret": bot.app_secret})
        resp.raise_for_status()
        data = resp.json()

    code = data.get("code", -1)
    if int(code) != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data!r}")

    token = str(data.get("tenant_access_token") or "").strip()
    expire = int(data.get("expire") or 0)
    if not token or expire <= 0:
        raise RuntimeError(f"tenant_access_token 返回异常: {data!r}")

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expire_at"] = now + expire
    return token


def get_bot_name(*, cfg: dict[str, Any]) -> str:
    """
    获取机器人名称（用于识别用户 @我 的消息）。
    """

    bot = _require_cfg(cfg)
    token = get_tenant_access_token(cfg=cfg)
    url = f"{bot.api_base}/open-apis/bot/v3/info"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()

    code = data.get("code", -1)
    if int(code) != 0:
        raise RuntimeError(f"获取 bot info 失败: {data!r}")

    return str((data.get("bot") or {}).get("app_name") or "").strip()


def send_text_message(*, cfg: dict[str, Any], text: str, chat_id: str | None = None) -> str:
    """
    发送文本消息到群聊，返回 message_id。
    """

    bot = _require_cfg(cfg)
    if not (chat_id or bot.chat_id):
        raise RuntimeError("FEISHU_CHAT_ID 为空，无法发送消息")
    token = get_tenant_access_token(cfg=cfg)
    target_chat_id = (chat_id or bot.chat_id).strip()
    url = f"{bot.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
    payload = {
        "receive_id": target_chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload)
        resp.raise_for_status()
        data = resp.json()

    code = data.get("code", -1)
    if int(code) != 0:
        raise RuntimeError(f"发送文本消息失败: {data!r}")

    return str(((data.get("data") or {}).get("message_id") or "")).strip()


def send_post_markdown_message(
    *, cfg: dict[str, Any], title: str, markdown: str, chat_id: str | None = None
) -> str:
    """
    发送可渲染 Markdown 的 post 消息到群聊（Feishu 富文本）。

    注意：这里使用 post 的 md tag；如果你的租户不支持该 tag，会直接报错（不做静默降级）。
    """

    bot = _require_cfg(cfg)
    if not (chat_id or bot.chat_id):
        raise RuntimeError("FEISHU_CHAT_ID 为空，无法发送消息")
    token = get_tenant_access_token(cfg=cfg)
    target_chat_id = (chat_id or bot.chat_id).strip()
    url = f"{bot.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
    post = {
        "zh_cn": {
            "title": title,
            "content": [[{"tag": "md", "text": markdown}]],
        }
    }
    payload = {
        "receive_id": target_chat_id,
        "msg_type": "post",
        "content": json.dumps(post, ensure_ascii=False),
    }
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload)
        resp.raise_for_status()
        data = resp.json()

    code = data.get("code", -1)
    if int(code) != 0:
        raise RuntimeError(f"发送 post Markdown 失败: {data!r}")

    return str(((data.get("data") or {}).get("message_id") or "")).strip()


def send_interactive_card_message(
    *, cfg: dict[str, Any], card: dict[str, Any], chat_id: str | None = None
) -> str:
    """
    发送飞书 interactive card（卡片消息），返回 message_id。
    """

    bot = _require_cfg(cfg)
    if not (chat_id or bot.chat_id):
        raise RuntimeError("FEISHU_CHAT_ID 为空，无法发送消息")
    token = get_tenant_access_token(cfg=cfg)
    target_chat_id = (chat_id or bot.chat_id).strip()
    url = f"{bot.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
    payload = {
        "receive_id": target_chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload)
        resp.raise_for_status()
        data = resp.json()

    code = data.get("code", -1)
    if int(code) != 0:
        raise RuntimeError(f"发送 interactive card 失败: {data!r}")

    return str(((data.get("data") or {}).get("message_id") or "")).strip()
