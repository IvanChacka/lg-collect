from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_filter_keywords
from tools.utils import get_config


def filter_topics_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    hot_items = state.get("hot_items") or []
    keywords = [item.get("keyword", "") for item in hot_items if item.get("keyword")]

    filtered = llm_filter_keywords(keywords=keywords, cfg=cfg)

    if not filtered:
        return {"status": "no_topic"}
    return {"filtered_keywords": filtered}
