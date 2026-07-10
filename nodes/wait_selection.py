from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from core.state import HotCollectState
from tools.utils import get_config


def wait_selection_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    proposals = state.get("proposals") or []
    thread_id = state.get("thread_id") or cfg.get("thread_id") or ""

    already_selected = state.get("selected_proposal_id")
    if already_selected:
        return {"status": "selected"}

    payload = {
        "type": "topic_selection",
        "thread_id": thread_id,
        "options": [
            {"proposal_id": p["proposal_id"], "title": p["title"], "thesis": p["thesis"]}
            for p in proposals
        ],
    }

    selection = interrupt(payload)
    if isinstance(selection, str):
        proposal_id = selection
    elif isinstance(selection, dict):
        proposal_id = selection.get("proposal_id") or selection.get("id") or ""
    else:
        proposal_id = ""

    # 支持用户/Studio 直接用“数字序号(1,2,3...)”resume：
    # - 飞书群聊场景一般是 @机器人 1（server 会映射到 proposal_id）
    # - Studio 可视化界面里也更自然输入 1
    # 这里做确定性的索引映射，不做“默认选第一条”的静默兜底。
    pid = str(proposal_id or "").strip()
    if pid.isdigit():
        idx = int(pid)
        if 1 <= idx <= len(proposals):
            mapped = str((proposals[idx - 1] or {}).get("proposal_id") or "").strip()
            if mapped:
                proposal_id = mapped

    return {"selected_proposal_id": proposal_id, "status": "selected"}
