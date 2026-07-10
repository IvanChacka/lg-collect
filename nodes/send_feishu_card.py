from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.feishu import send_topic_choice_message
from tools.utils import get_config


def send_feishu_card_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    proposals = state.get("proposals") or []
    thread_id = state.get("thread_id") or cfg.get("thread_id") or ""

    human_mode = cfg.get("human_mode", True)

    if not proposals:
        return {"status": "error", "errors": ["No proposals generated."]}

    if not human_mode:
        selected = proposals[0]["proposal_id"]
        return {"selected_proposal_id": selected, "status": "selected"}

    try:
        hot_keyword = str(state.get("selected_hot_keyword") or "").strip()
        hot_platform = str((state.get("hot_picked") or {}).get("platform") or "").strip()
        message_id = send_topic_choice_message(
            cfg=cfg,
            thread_id=thread_id,
            hot_keyword=hot_keyword,
            hot_platform=hot_platform,
            proposals=proposals,
        )
        return {"feishu_message_id": message_id, "status": "waiting_selection"}
    except Exception as e:
        # 禁止静默兜底：发送失败必须显式报错，避免“看似在等选择但实际上没发出去”
        return {"status": "error", "errors": [f"Feishu send failed: {e!r}"]}
