from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_analyze_materials
from tools.utils import get_config


def analyze_articles_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：对文章素材进行分析提炼（输出给后续聚合与选题推理使用）。
    """

    cfg = get_config(config)
    materials = [m for m in (state.get("materials") or []) if m.get("kind") == "article"]
    try:
        text = llm_analyze_materials(
            kind="article",
            hot_titles=state.get("filtered_keywords") or [],
            materials=materials,
            cfg=cfg,
        )
        return {"article_analysis": text, "materials_barrier": "articles"}
    except Exception as e:
        # 即使失败也要写入栅栏，避免并行汇合永远等不到
        return {
            "article_analysis": "",
            "materials_barrier": "articles",
            "errors": [f"文章素材分析失败: {e!r}"],
        }
