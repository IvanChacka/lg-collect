from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_generate_proposals
from tools.utils import get_config


def generate_proposals_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    proposals = llm_generate_proposals(
        keywords=state.get("filtered_keywords") or [],
        materials=state.get("materials") or [],
        proposal_candidates=state.get("proposal_candidates") or [],
        article_analysis=state.get("article_analysis") or "",
        cfg=cfg,
    )
    return {"proposals": proposals}
