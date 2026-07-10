from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.utils import get_config
from tools.utils import new_thread_id


def _today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def start_hot_fetch_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：热点抓取的扇出起点。

    说明：
    - LangGraph Studio 触发时，用户可能以“空 state”启动图；
      这里负责补齐运行必需字段（thread_id/run_date），避免后续节点（尤其是数据库去重）报错。
    - 该节点不是兜底业务逻辑，而是运行时的必需初始化。
    """

    cfg = get_config(config)
    thread_id = (state.get("thread_id") or str(cfg.get("thread_id") or "").strip()).strip()
    run_date = (state.get("run_date") or str(cfg.get("run_date") or "").strip()).strip()

    out: dict[str, Any] = {}
    if not run_date:
        run_date = _today_iso()
        out["errors"] = [f"run_date 缺失，已自动设置为今日: {run_date}"]
    if not thread_id:
        thread_id = new_thread_id(run_date=run_date)
        out.setdefault("errors", []).append(f"thread_id 缺失，已自动生成: {thread_id}")

    # 仅在需要时写入，避免覆盖已有状态
    out["thread_id"] = thread_id
    out["run_date"] = run_date

    try:
        debug_log(f"start_hot_fetch init thread_id={thread_id!r} run_date={run_date!r}", cfg=cfg, prefix="node")
    except Exception:
        pass

    return out
