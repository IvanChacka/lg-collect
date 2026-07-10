from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState


def download_video_assets_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    _ = (state, config)
    raise RuntimeError(
        "download_video_assets_selected 已拆分为多个节点："
        "download_subtitles_bbdown_selected。"
    )
