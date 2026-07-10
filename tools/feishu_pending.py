from __future__ import annotations

import json
import os
import time
from typing import Any


PENDING_PATH = ".data/feishu_pending.json"


def _load() -> dict[str, Any]:
    if not os.path.exists(PENDING_PATH):
        return {"version": 1, "by_thread_id": {}, "by_chat_id": {}, "by_message_id": {}}
    with open(PENDING_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"version": 1, "by_thread_id": {}, "by_chat_id": {}, "by_message_id": {}}
    data.setdefault("version", 1)
    data.setdefault("by_thread_id", {})
    data.setdefault("by_chat_id", {})
    data.setdefault("by_message_id", {})
    return data


def _save(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(PENDING_PATH) or ".", exist_ok=True)
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_pending(
    *,
    chat_id: str,
    thread_id: str,
    proposals: list[dict[str, Any]],
    hot_keyword: str,
    hot_platform: str,
    message_id: str = "",
) -> None:
    """
    记录某个 thread_id 在某个 chat_id 下等待用户选择。
    用于把“用户回复的序号”映射回 proposal_id。
    """

    chat_id = (chat_id or "").strip()
    thread_id = (thread_id or "").strip()
    if not chat_id or not thread_id:
        return

    index_to_proposal_id: dict[str, str] = {}
    for idx, p in enumerate(proposals, start=1):
        pid = str(p.get("proposal_id") or "").strip()
        if pid:
            index_to_proposal_id[str(idx)] = pid

    data = _load()
    data["by_chat_id"][chat_id] = thread_id
    mid = (message_id or "").strip()
    if mid:
        data["by_message_id"][mid] = thread_id
    data["by_thread_id"][thread_id] = {
        "chat_id": chat_id,
        "thread_id": thread_id,
        "hot_keyword": hot_keyword,
        "hot_platform": hot_platform,
        "index_to_proposal_id": index_to_proposal_id,
        "updated_at": int(time.time()),
    }
    _save(data)


def get_latest_thread_id(*, chat_id: str) -> str:
    data = _load()
    return str((data.get("by_chat_id") or {}).get(chat_id) or "").strip()


def get_thread_id_by_message_id(*, message_id: str) -> str:
    data = _load()
    return str((data.get("by_message_id") or {}).get(str(message_id or "").strip()) or "").strip()


def get_pending_by_thread_id(*, thread_id: str) -> dict[str, Any]:
    data = _load()
    item = (data.get("by_thread_id") or {}).get(thread_id) or {}
    return item if isinstance(item, dict) else {}


def get_chat_id_by_thread_id(*, thread_id: str) -> str:
    pending = get_pending_by_thread_id(thread_id=thread_id)
    return str(pending.get("chat_id") or "").strip()


def is_ack_sent(*, thread_id: str) -> bool:
    pending = get_pending_by_thread_id(thread_id=thread_id)
    return bool(pending.get("ack_sent"))


def mark_ack_sent(*, thread_id: str) -> None:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return
    data = _load()
    item = (data.get("by_thread_id") or {}).get(thread_id)
    if not isinstance(item, dict) or not item:
        return
    item["ack_sent"] = True
    item["updated_at"] = int(time.time())
    _save(data)


def map_index_to_proposal_id(*, thread_id: str, index: str) -> str:
    pending = get_pending_by_thread_id(thread_id=thread_id)
    m = pending.get("index_to_proposal_id") or {}
    if not isinstance(m, dict):
        return ""
    return str(m.get(str(index).strip()) or "").strip()
