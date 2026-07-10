from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState, Material
from tools.utils import get_config


def materialize_videos_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把视频搜索结果转成 Material（先只存标题/链接，后续可接字幕/文案抓取）。
    """

    _ = get_config(config)
    # 优先使用 LLM 筛选后的候选；若为空则回退到搜索结果
    results = state.get("video_candidates") or state.get("video_search_results") or []
    materials: list[Material] = []
    for r in results:
        materials.append(
            {
                "kind": "video",
                "title": r.get("title") or "未命名视频",
                "url": r.get("url"),
                "snippet": r.get("snippet"),
                "content": None,
                "source": r.get("engine") or "web",
                "meta": {"engine": r.get("engine")},
            }
        )
    return {"materials": materials}
