from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState, Material
from tools.utils import get_config


def materialize_articles_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把抽取结果转成统一的 Material 结构，写入 materials。
    """

    _ = get_config(config)
    extracts = state.get("article_extracts") or []
    materials: list[Material] = []
    for ex in extracts:
        materials.append(
            {
                "kind": "article",
                "title": ex.get("title") or "未命名文章",
                "url": ex.get("url"),
                "snippet": None,
                "content": ex.get("content"),
                "source": "web",
                "meta": {},
            }
        )
    return {"materials": materials}

