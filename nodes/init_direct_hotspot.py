from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState


def init_direct_hotspot_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    keyword = (state.get("direct_hotspot_keyword") or "").strip()
    if not keyword:
        raise RuntimeError("direct_hotspot_keyword 为空，无法初始化直接热点模式")

    return {
        "hot_picked": {
            "keyword": keyword,
            "reason": "用户直接指定热点",
            "platform": "direct",
            "platform_rank": None,
        },
        "selected_hot_keyword": keyword,
        "selected_hot_reason": "用户直接指定热点",
    }
