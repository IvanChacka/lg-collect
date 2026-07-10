from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.hot_db import add_selected, ensure_schema
from tools.utils import get_config


def write_hotspot_db_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把本次最终选中的唯一热点写入数据库（用于同一天多次运行去重）。

    说明：
    - 前置条件：check_hotspot_db 已确认不重复（hot_db_ok=True）
    - 本节点负责“真正落库”，并把 filtered_keywords 设置为 [keyword]（唯一）
    """

    cfg = get_config(config)
    run_date = (state.get("run_date") or str(cfg.get("run_date") or "")).strip()
    thread_id = (state.get("thread_id") or str(cfg.get("thread_id") or "")).strip()
    picked = state.get("hot_picked") or {}

    keyword = str(picked.get("keyword") or "").strip()
    reason = str(picked.get("reason") or "").strip()
    platform = str(picked.get("platform") or "").strip() or None
    platform_rank = int(picked.get("platform_rank") or 0) or None

    if not run_date:
        raise RuntimeError("run_date 为空，无法写入热点数据库")
    if not keyword or not reason:
        raise RuntimeError(f"picked keyword/reason 为空，无法写入热点数据库: {picked!r}")

    db_path = str(cfg.get("hot_db_path") or ".data/hot_history.sqlite").strip()
    ensure_schema(db_path=db_path)

    t0 = time.time()
    debug_log(
        f"write_hotspot_db start run_date={run_date!r} keyword={keyword!r}",
        cfg=cfg,
        prefix="node",
    )
    try:
        add_selected(
            db_path=db_path,
            run_date=run_date,
            keyword=keyword,
            platform=platform,
            platform_rank=platform_rank,
            thread_id=thread_id or None,
            reason=reason,
        )
        return {
            "filtered_keywords": [keyword],
            "selected_hot_keyword": keyword,
            "selected_hot_reason": reason,
        }
    finally:
        debug_log(f"write_hotspot_db elapsed={time.time()-t0:.2f}s", cfg=cfg, prefix="node")
