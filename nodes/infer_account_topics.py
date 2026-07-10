from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState, TopicProposal
from tools.llm_client import llm_infer_account_topics
from tools.utils import get_config


def infer_account_topics_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：基于热点详细事件和账号主题，生成 10 套完整选题候选。
    """

    cfg = get_config(config)
    proposal_candidates: list[TopicProposal] = llm_infer_account_topics(
        hot_titles=state.get("filtered_keywords") or [],
        article_analysis=state.get("article_analysis") or "",
        video_analysis=state.get("video_analysis") or "",
        cfg=cfg,
    )
    return {"proposal_candidates": proposal_candidates}
