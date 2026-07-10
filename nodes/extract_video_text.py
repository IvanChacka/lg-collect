from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def extract_video_text_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：抽取/整理视频文本（占位）。
    """

    _ = get_config(config)
    return {}

