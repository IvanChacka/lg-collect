from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.video_bilibili import bilibili_search
from tools.utils import get_config


def search_videos_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点 1：将热门词条在 B 站站内搜索，抓取视频标题 + 链接。

    约定：
    - 默认每个词条抓取 100 条（可用 .env 的 VIDEOS_PER_KEYWORD 调整）
    - “已选题目”阶段允许最多 N 个 query（由 SELECTED_VIDEO_QUERIES_LIMIT 控制，且强制上限 3）
    - 为了稳定性，优先使用 B 站站内搜索（`tools/video_bilibili.py`）
    """

    cfg = get_config(config)
    per_kw = int(cfg.get("videos_per_keyword", 100))
    selected_limit = int(cfg.get("selected_video_queries_limit", 3))
    if selected_limit <= 0:
        raise ValueError(f"SELECTED_VIDEO_QUERIES_LIMIT 必须为正整数，但得到: {selected_limit!r}")
    # 选题后默认 3 个 query 足够；过大会显著拖慢下载/转写等下游。
    selected_limit = min(selected_limit, 3)
    keywords = [str(x).strip() for x in (state.get("filtered_keywords") or []) if str(x).strip()]

    # 仅在“已选题目”之后才允许调用视频链路。
    # 约束：若要下载/转写 B 站素材，意味着已经选完题目，不在选题之前做视频研究。
    if not state.get("selected_proposal_id"):
        raise RuntimeError("search_videos 仅允许在已选题目后调用（缺少 selected_proposal_id）")
    if not keywords:
        raise RuntimeError("search_videos_selected 缺少 filtered_keywords（应由 prepare_selected_topic_materials 生成）")

    # 选题阶段：prepare_selected_topic_materials 会生成最多 3 个 query 写入 filtered_keywords。
    if len(keywords) > 1:
        effective_keywords = keywords[:selected_limit]
    else:
        effective_keywords = keywords[:1]

    results: list[dict] = []
    errors: list[str] = []
    for kw in effective_keywords:
        query = kw
        try:
            rows = bilibili_search(query=query, cfg=cfg, limit=per_kw)
        except Exception as e:
            errors.append(f"bilibili_search failed for {kw!r}: {e!r}")
            continue
        for i, r in enumerate(rows, start=1):
            results.append({**r, "keyword": kw, "rank": i})

    if errors:
        raise RuntimeError(" ; ".join(errors))
    if keywords and not results:
        raise RuntimeError("search_videos 未返回任何视频候选")
    return {"video_search_results": results}
