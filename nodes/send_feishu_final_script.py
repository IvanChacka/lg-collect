from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.feishu import send_final_script_message
from tools.utils import get_config


def send_feishu_final_script_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把 final_script 生成的 Markdown 发送到飞书群。

    说明：不做静默兜底。若飞书配置缺失或发送失败，直接返回 error，便于发现问题。
    """

    cfg = get_config(config)
    thread_id = str(state.get("thread_id") or cfg.get("thread_id") or "").strip()
    script_markdown = str(state.get("final_script_markdown") or "").strip()
    if not script_markdown:
        return {"status": "error", "errors": ["No final_script_markdown to send."]}

    try:
        send_final_script_message(cfg=cfg, thread_id=thread_id, script_markdown=script_markdown)
        return {"status": state.get("status") or "completed"}
    except Exception as e:
        return {"status": "error", "errors": [f"Feishu send final script failed: {e!r}"]}

