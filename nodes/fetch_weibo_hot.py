from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.hot_weibo import fetch_weibo_hot
from tools.utils import get_config


def fetch_weibo_hot_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：获取微博热搜。
    """

    cfg = get_config(config)
    items = fetch_weibo_hot(cfg)
    # 写入栅栏：标记“微博热搜”这一路已完成
    return {"weibo_hot_items": items, "hot_sources_barrier": "weibo"}
