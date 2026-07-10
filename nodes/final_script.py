from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.llm_client import llm_generate_final_script, llm_get_final_script_prompt_text
from tools.utils import get_config


def final_script_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    cfg = get_config(config)
    proposal_id = state.get("selected_proposal_id") or ""

    proposals = {p["proposal_id"]: p for p in (state.get("proposals") or [])}
    proposal = proposals.get(proposal_id) or next(iter(proposals.values()), None)
    if proposal is None:
        return {"status": "error", "errors": [f"Unknown proposal_id: {proposal_id}"]}

    keyword_summary_text = str(state.get("keyword_materials_summary_text") or "").strip()
    if keyword_summary_text:
        merged_article_analysis = (
            (state.get("article_analysis") or "").strip()
            + "\n\n"
            + "以下是补充知识延展资料（来自三条 keyword 检索的文章与视频字幕，已做提炼）：\n"
            + keyword_summary_text
        ).strip()
    else:
        merged_article_analysis = (state.get("article_analysis") or "").strip()

    script_md = llm_generate_final_script(
        proposal=proposal,
        materials=state.get("materials") or [],
        article_analysis=merged_article_analysis,
        video_analysis=state.get("video_analysis") or "",
        article_extracts=state.get("article_extracts") or [],
        video_transcripts=state.get("video_transcripts") or [],
        cfg=cfg,
    )

    prompt_text = llm_get_final_script_prompt_text(
        proposal=proposal,
        materials=state.get("materials") or [],
        article_analysis=merged_article_analysis,
        video_analysis=state.get("video_analysis") or "",
        article_extracts=state.get("article_extracts") or [],
        video_transcripts=state.get("video_transcripts") or [],
    )

    return {
        "final_script_markdown": script_md,
        "final_script_llm_system_prompt": prompt_text.get("system_prompt") or "",
        "final_script_llm_user_prompt": prompt_text.get("user_prompt") or "",
        "status": "completed",
    }
