from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.search import collect_materials_for_keywords
from tools.utils import get_config


def collect_materials_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    keywords = state.get("filtered_keywords") or []
    materials = collect_materials_for_keywords(keywords=keywords, cfg=cfg)
    return {"materials": materials, "status": "materials_collected"}
