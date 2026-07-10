from datetime import date
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.hot_sources import fetch_hot_items
from tools.utils import get_config


def fetch_hot_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    run_date = state.get("run_date") or cfg.get("run_date") or date.today().isoformat()

    hot_items = fetch_hot_items(cfg)
    return {"run_date": run_date, "hot_items": hot_items}
