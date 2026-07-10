from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def fetch_video_pages_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：抓取视频页面/信息（占位）。

    说明：
    - 目前视频素材只抓“标题/链接/简介”
    - 后续可在这里接：字幕/文案解析、Whisper 转写等
    """

    _ = get_config(config)
    return {}

