from __future__ import annotations

from typing import Annotated, Literal, Optional, TypedDict

from langgraph.channels import NamedBarrierValue



def list_extend(old: Optional[list], new: Optional[list]) -> list:
    return (old or []) + (new or [])


def list_replace(old: Optional[list], new: Optional[list]) -> list:
    _ = old
    return list(new or [])


class HotItem(TypedDict):
    platform: Literal["weibo", "douyin", "other"]
    rank: int
    keyword: str
    raw: dict


class Material(TypedDict, total=False):
    kind: Literal["article", "video", "other"]
    title: str
    url: Optional[str]
    snippet: Optional[str]
    content: Optional[str]
    source: Optional[str]
    meta: dict


class DeepInsight(TypedDict):
    angle: str
    details: str


class TopicProposal(TypedDict, total=False):
    proposal_id: str  # 例如：A / B / C
    title: str
    thesis: str
    outline: list[str]
    candidate_id: str  # 原始候选 ID，例如：T01
    rank: int
    score: int
    selection_reason: str


class HotCollectState(TypedDict, total=False):
    thread_id: str
    run_date: str
    human_mode: bool
    status: Literal[
        "init",
        "no_topic",
        "materials_collected",
        "waiting_selection",
        "selected",
        "completed",
        "error",
    ]

    direct_hotspot_keyword: str
    weibo_hot_items: list[HotItem]
    douyin_hot_items: list[HotItem]
    # 栅栏：用于确保“微博 + 抖音”两路都完成后才进入聚合
    hot_sources_barrier: Annotated[None, NamedBarrierValue(str, {"weibo", "douyin"})]
    hot_titles: list[str]
    filtered_keywords: Annotated[list[str], list_replace]
    # 本次运行最终选中的唯一热点（用于去重、记录与下游文案）
    selected_hot_keyword: str
    selected_hot_reason: str
    # 热点候选（供 LLM 选择 + 数据库去重）
    hot_candidates: list[dict]
    hot_excluded_keywords: Annotated[list[str], list_extend]
    hot_picked: dict
    hot_db_ok: bool

    article_search_results: Annotated[list[dict], list_replace]
    article_candidates: Annotated[list[dict], list_replace]
    article_pages: Annotated[list[dict], list_replace]
    article_extracts: Annotated[list[dict], list_replace]
    video_search_results: Annotated[list[dict], list_replace]
    # 视频链路：LLM 从搜索结果中筛选出的候选视频（用于下载/转写）
    video_candidates: Annotated[list[dict], list_replace]
    # 视频链路：下载得到的资产（字幕/音频文件路径等）
    video_assets: Annotated[list[dict], list_replace]
    # 视频链路：最终的文本（字幕 or 语音转写）
    video_transcripts: Annotated[list[dict], list_replace]
    # 视频链路：聚合字幕文本（用于下游统一视图/排查）
    video_subtitles: Annotated[list[dict], list_replace]
    video_subtitles_text: str
    # 已选题目阶段：按 filtered_keywords（最多 3 个 query）聚合的资料块（文章正文 + 视频字幕）
    keyword_materials_by_keyword: dict
    keyword_materials_text: str
    # 对 keyword_materials 再做提炼，避免过长
    keyword_materials_summary_by_keyword: dict
    keyword_materials_summary_text: str

    materials: Annotated[list[Material], list_replace]
    article_analysis: str
    video_analysis: str
    # 栅栏：用于确保“文章分析 + 视频分析”两路都完成后才进入聚合/推理
    materials_barrier: Annotated[None, NamedBarrierValue(str, {"articles", "videos"})]
    deep_insights: Annotated[list[DeepInsight], list_replace]
    errors: Annotated[list[str], list_extend]

    proposal_candidates: Annotated[list[TopicProposal], list_replace]
    proposals: Annotated[list[TopicProposal], list_replace]
    selected_proposal_id: str
    final_script_markdown: str
    # 可观测性：final_script 发给 LLM 的原始提示词（完全展开后的文字）
    final_script_llm_system_prompt: str
    final_script_llm_user_prompt: str
    # 口播文案 -> 分段 -> 分镜 JSON
    script_chunks: Annotated[list[dict], list_replace]
    storyboard_segments: Annotated[list[dict], list_replace]
    storyboard: dict
    storyboard_json: str
    feishu_message_id: str
    feishu_doc_url: str


def initial_state(*, thread_id: str, run_date: str) -> HotCollectState:
    return {
        "thread_id": thread_id,
        "run_date": run_date,
        "status": "init",
        "weibo_hot_items": [],
        "douyin_hot_items": [],
        "hot_titles": [],
        "filtered_keywords": [],
        "article_search_results": [],
        "article_candidates": [],
        "article_pages": [],
        "article_extracts": [],
        "video_search_results": [],
        "video_candidates": [],
        "video_assets": [],
        "video_transcripts": [],
        "video_subtitles": [],
        "video_subtitles_text": "",
        "keyword_materials_by_keyword": {},
        "keyword_materials_text": "",
        "keyword_materials_summary_by_keyword": {},
        "keyword_materials_summary_text": "",
        "materials": [],
        "article_analysis": "",
        "video_analysis": "",
        "deep_insights": [],
        "proposal_candidates": [],
        "errors": [],
        "script_chunks": [],
        "storyboard_segments": [],
    }
