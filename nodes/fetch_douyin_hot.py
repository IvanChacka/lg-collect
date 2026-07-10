from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.hot_douyin import fetch_douyin_hot
from tools.utils import get_config


def fetch_douyin_hot_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：获取抖音热点榜。
    """

    cfg = get_config(config)
    items = fetch_douyin_hot(cfg)
    # 写入栅栏：标记“抖音热榜”这一路已完成
    return {"douyin_hot_items": items, "hot_sources_barrier": "douyin"}
