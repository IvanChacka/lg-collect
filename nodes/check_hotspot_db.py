from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.hot_db import ensure_schema, has_selected
from tools.utils import get_config


def check_hotspot_db_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：数据库去重（同一天多次运行避免重复选同一个热点）。

    行为：
    - 如果 picked.keyword 已存在：把它加入 hot_excluded_keywords，并清空 hot_picked，让图回到 LLM 重新选择
    - 如果不存在：标记 hot_db_ok=True，交给后续“写入热点数据库”节点落库并进入分叉链路
    """

    cfg = get_config(config)
    run_date = (state.get("run_date") or str(cfg.get("run_date") or "")).strip()
    thread_id = (state.get("thread_id") or str(cfg.get("thread_id") or "")).strip()
    picked = state.get("hot_picked") or {}
    keyword = str(picked.get("keyword") or "").strip()
    reason = str(picked.get("reason") or "").strip()
    platform = str(picked.get("platform") or "").strip() or None
    platform_rank = int(picked.get("platform_rank") or 0) or None

    db_path = str(cfg.get("hot_db_path") or ".data/hot_history.sqlite").strip()
    ensure_schema(db_path=db_path)
    disable_dedup = str(cfg.get("hot_db_disable_dedup") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    t0 = time.time()
    debug_log(
        f"check_hotspot_db start run_date={run_date!r} keyword={keyword!r}",
        cfg=cfg,
        prefix="node",
    )
    try:
        if not run_date:
            raise RuntimeError("run_date 为空，无法进行按天去重（请在启动 state 或 configurable 中提供 run_date）")
        if not keyword or not reason:
            raise RuntimeError(f"picked keyword/reason 为空: {picked!r}")
        if disable_dedup:
            debug_log(
                f"check_hotspot_db bypassed by HOT_DB_DISABLE_DEDUP for keyword={keyword!r}",
                cfg=cfg,
                prefix="node",
            )
            return {"hot_db_ok": True}

        if has_selected(db_path=db_path, run_date=run_date, keyword=keyword):
            debug_log(
                f"check_hotspot_db duplicated keyword={keyword!r}",
                cfg=cfg,
                prefix="node",
            )
            return {
                "hot_excluded_keywords": [keyword],
                "hot_picked": {},
                "hot_db_ok": False,
                "errors": [f"热点已存在于数据库，触发重选: {keyword}"],
            }

        return {
            "hot_db_ok": True,
        }
    finally:
        debug_log(f"check_hotspot_db elapsed={time.time()-t0:.2f}s", cfg=cfg, prefix="node")
