from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_summarize_keyword_materials
from tools.utils import get_config


def summarize_keyword_materials_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：对每个 keyword 块里的“文章正文 + 视频字幕”做提炼，避免 final_script 输入过长。

    输入：
    - keyword_materials_by_keyword / keyword_materials_text

    输出：
    - keyword_materials_summary_by_keyword: {keyword: "...summary..."}
    - keyword_materials_summary_text: 合并后的可读文本（按 keyword 分块）
    """

    cfg = get_config(config)
    keywords = [str(x).strip() for x in (state.get("filtered_keywords") or []) if str(x).strip()]
    if state.get("selected_proposal_id") and not keywords:
        raise RuntimeError(
            "summarize_keyword_materials_selected 缺少 filtered_keywords（应由 prepare_selected_topic_materials 生成）"
        )

    by_kw = state.get("keyword_materials_by_keyword") or {}
    text = str(state.get("keyword_materials_text") or "").strip()
    if not by_kw and not text:
        # 已选题目阶段：聚合为空应显式失败，避免“看似继续跑但实际无素材”。
        if state.get("selected_proposal_id"):
            raise RuntimeError("summarize_keyword_materials_selected 缺少 keyword_materials（上游未聚合到任何资料）")
        return {"keyword_materials_summary_by_keyword": {}, "keyword_materials_summary_text": ""}

    result = llm_summarize_keyword_materials(
        keyword_materials_by_keyword=by_kw if isinstance(by_kw, dict) else {},
        keyword_materials_text=text,
        cfg=cfg,
    )
    return result
