from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.hot_merge import merge_hot_items
from tools.utils import get_config


def merge_hot_items_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：合并微博/抖音榜单并去重排序。
    """

    cfg = get_config(config)
    limit = int(cfg.get("hot_merge_limit") or 50)
    merged = merge_hot_items(
        weibo=state.get("weibo_hot_items") or [],
        douyin=state.get("douyin_hot_items") or [],
        limit=limit,
    )
    return {"hot_items": merged}

