from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_split_narration_to_segments
from tools.utils import get_config


def storyboard_segments_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把口播文案按 chunk 分镜切段，输出严格 JSON 结构（但这里返回 python dict/list）。
    说明：不做任何“先分 chunk 再分镜”的逻辑，直接对整段口播文案分镜切段。
    """

    cfg = get_config(config)
    script = str(state.get("final_script_markdown") or "").strip()
    if not script:
        raise RuntimeError("storyboard_segments 口播文案为空：final_script_markdown 为空")

    proposal_id = str(state.get("selected_proposal_id") or "").strip()
    proposals = {str(p.get("proposal_id") or "").strip(): p for p in (state.get("proposals") or [])}
    title = str((proposals.get(proposal_id) or {}).get("title") or "").strip()
    if not title:
        title = "未命名视频"

    segs = llm_split_narration_to_segments(title=title, narration_text=script, cfg=cfg)
    if not segs:
        raise RuntimeError("storyboard_segments 未产出任何 segments")

    # 合并成全局 segments，并补充全局序号
    merged: list[dict[str, Any]] = []
    global_index = 1
    for seg in segs:
        spoken = str((seg or {}).get("spoken_text") or "").strip()
        if not spoken:
            raise RuntimeError("storyboard_segments 出现空 spoken_text（校验失败）")
        merged.append({"index": global_index, "spoken_text": spoken})
        global_index += 1

    return {
        "storyboard_segments": merged,
        "status": "completed",
    }
