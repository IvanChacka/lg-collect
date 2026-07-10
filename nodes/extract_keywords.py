from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def extract_keywords_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：从合并后的热点条目中抽取候选关键词（不做智能过滤，只做整理/截断）。
    """

    cfg = get_config(config)
    top_n = int(cfg.get("hot_keyword_top_n") or 30)
    items = state.get("hot_items") or []
    keywords: list[str] = []
    for it in items:
        kw = (it.get("keyword") or "").strip()
        if not kw:
            continue
        keywords.append(kw)
        if len(keywords) >= top_n:
            break
    return {"candidate_keywords": keywords}

