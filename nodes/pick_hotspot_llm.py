from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.hot_db import ensure_schema, list_recent_keywords
from tools.llm_client import llm_pick_hotspot
from tools.utils import get_config


def pick_hotspot_llm_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：调用 LLM 从 candidates 中挑选唯一热点，并产出理由。
    输出写入 state["hot_picked"]：
      {id, keyword, platform, platform_rank, reason}
    """

    cfg = get_config(config)
    candidates = state.get("hot_candidates") or []
    excluded = state.get("hot_excluded_keywords") or []

    db_path = str(cfg.get("hot_db_path") or ".data/hot_history.sqlite").strip()
    ensure_schema(db_path=db_path)
    recent_kws = list_recent_keywords(db_path=db_path, limit=3)
    excluded_for_llm: list[str] = []
    seen_excluded: set[str] = set()
    for kw in [*excluded, *recent_kws]:
        kw = str(kw or "").strip()
        if not kw or kw in seen_excluded:
            continue
        seen_excluded.add(kw)
        excluded_for_llm.append(kw)
    t0 = time.time()
    debug_log(
        f"pick_hotspot_llm start candidates={len(candidates)} excluded={len(excluded_for_llm)}",
        cfg=cfg,
        prefix="node",
    )
    try:
        picked = llm_pick_hotspot(
            candidates=candidates,
            excluded_keywords=excluded_for_llm,
            cfg=cfg,
        )
        return {"hot_picked": picked}
    finally:
        debug_log(f"pick_hotspot_llm elapsed={time.time()-t0:.2f}s", cfg=cfg, prefix="node")
