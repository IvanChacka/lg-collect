from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_infer_account_topics_and_generate_proposals
from tools.utils import get_config


def infer_account_topics_and_generate_proposals_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：融合 infer_account_topics + generate_proposals。

    产出：
    - proposal_candidates: 10 条候选（带 rank/score/selection_reason）
    - proposals: Top5（保持下游字段需求：proposal_id/title/thesis/outline/...）
    """

    cfg = get_config(config)
    proposal_candidates, proposals = llm_infer_account_topics_and_generate_proposals(
        hot_titles=state.get("filtered_keywords") or [],
        article_analysis=state.get("article_analysis") or "",
        video_analysis=state.get("video_analysis") or "",
        materials=state.get("materials") or [],
        cfg=cfg,
    )
    return {"proposal_candidates": proposal_candidates, "proposals": proposals}

