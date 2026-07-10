from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def aggregate_hot_titles_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：聚合热榜标题，并构建结构化热点候选（供 LLM 选择与数据库去重使用）。

    注意：
    - “等待两路完成”的逻辑由 `wait_hot_sources` 节点负责
    """

    cfg = get_config(config)
    per_platform_limit = int(cfg.get("hot_candidate_per_platform_limit") or 10)

    weibo_items = state.get("weibo_hot_items") or []
    douyin_items = state.get("douyin_hot_items") or []

    titles: list[str] = []
    weibo_candidates: list[dict[str, Any]] = []
    douyin_candidates: list[dict[str, Any]] = []

    def _build_candidates(platform: str, items: list[dict]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in items:
            kw = str(it.get("keyword") or "").strip()
            if not kw:
                continue
            try:
                r = int(it.get("rank") or 0)
            except Exception:
                r = 0
            r = max(r, len(out) + 1)

            hot = None
            raw = it.get("raw") or {}
            if isinstance(raw, dict):
                hot = raw.get("hot") or raw.get("hot_value") or raw.get("raw_hot")

            out.append(
                {
                    "id": f"{platform}-{r}",
                    "platform": platform,
                    "platform_rank": r,
                    "keyword": kw,
                    "hot": hot,
                }
            )
            if len(out) >= max(1, per_platform_limit):
                break
        return out

    for it in weibo_items:
        t = (it.get("keyword") or "").strip()
        if t:
            titles.append(t)
    weibo_candidates = _build_candidates("weibo", weibo_items)
    for it in douyin_items:
        t = (it.get("keyword") or "").strip()
        if t:
            titles.append(t)
    douyin_candidates = _build_candidates("douyin", douyin_items)

    if not titles:
        return {"status": "no_topic", "hot_titles": [], "hot_candidates": []}
    candidates = weibo_candidates + douyin_candidates
    return {"hot_titles": titles, "hot_candidates": candidates}
