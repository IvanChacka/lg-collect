from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_deep_dive
from tools.utils import get_config


def deep_dive_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    insights = llm_deep_dive(
        keywords=state.get("filtered_keywords") or [],
        materials=state.get("materials") or [],
        cfg=cfg,
    )
    return {"deep_insights": insights}
