from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_analyze_materials
from tools.utils import get_config


def analyze_videos_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：对视频素材进行分析提炼（先用标题/简介占位，后续可接字幕/文案）。
    """

    cfg = get_config(config)
    materials = [m for m in (state.get("materials") or []) if m.get("kind") == "video"]
    try:
        text = llm_analyze_materials(
            kind="video",
            hot_titles=state.get("filtered_keywords") or [],
            materials=materials,
            cfg=cfg,
        )
        return {"video_analysis": text, "materials_barrier": "videos"}
    except Exception as e:
        return {
            "video_analysis": "",
            "materials_barrier": "videos",
            "errors": [f"视频素材分析失败: {e!r}"],
        }
