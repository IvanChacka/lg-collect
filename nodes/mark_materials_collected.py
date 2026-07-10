from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def mark_materials_collected_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：显式标记“素材收集已完成”（便于路由与可视化观察）。
    """

    _ = get_config(config)
    return {"status": "materials_collected"}

