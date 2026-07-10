from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.llm_client import llm_filter_videos
from tools.utils import get_config


def llm_filter_videos_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点 2：对每个词条的 100 条视频做 LLM 筛选，留下“研究与扩展有收益”的视频。
    """

    cfg = get_config(config)
    keep_limit = int(cfg.get("video_llm_keep_limit", 3))
    if keep_limit <= 0:
        raise ValueError("VIDEO_LLM_KEEP_LIMIT 必须为正整数（每个 query 保留多少条视频）")

    results = state.get("video_search_results") or []
    if not results:
        return {"video_candidates": []}

    debug_log(
        f"node=llm_filter_videos start keywords={len({(r.get('keyword') or '').strip() for r in results if isinstance(r, dict)})} results={len(results)} keep={keep_limit}",
        cfg=cfg,
        prefix="node",
    )

    grouped: dict[str, list[dict]] = {}
    for r in results:
        kw = (r.get("keyword") or "").strip() or "unknown"
        grouped.setdefault(kw, []).append(r)

    candidates: list[dict] = []
    try:
        for kw, videos in grouped.items():
            debug_log(
                f"node=llm_filter_videos keyword={kw!r} in={len(videos)}",
                cfg=cfg,
                prefix="node",
            )
            filtered = llm_filter_videos(keyword=kw, videos=videos, cfg=cfg, keep_limit=keep_limit)
            for v in filtered:
                candidates.append({**v, "selected": True})
    except BaseException as e:
        debug_log(
            f"node=llm_filter_videos exception={type(e).__name__}: {e!r}",
            cfg=cfg,
            prefix="node",
        )
        raise

    debug_log(
        f"node=llm_filter_videos done candidates={len(candidates)}",
        cfg=cfg,
        prefix="node",
    )

    return {"video_candidates": candidates}
