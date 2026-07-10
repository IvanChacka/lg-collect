from __future__ import annotations

from langgraph.graph import END, StateGraph

from core.state import HotCollectState
from nodes.final_script import final_script_node
from nodes.aggregate_hot_titles import aggregate_hot_titles_node
from nodes.download_subtitles_bbdown import download_subtitles_bbdown_node
from nodes.aggregate_article_text import aggregate_article_text_node
from nodes.fetch_douyin_hot import fetch_douyin_hot_node
from nodes.fetch_weibo_hot import fetch_weibo_hot_node
from nodes.infer_account_topics_and_generate_proposals import (
    infer_account_topics_and_generate_proposals_node,
)
from nodes.llm_filter_articles import llm_filter_articles_node
from nodes.llm_filter_videos import llm_filter_videos_node
from nodes.pick_hotspot_llm import pick_hotspot_llm_node
from nodes.check_hotspot_db import check_hotspot_db_node
from nodes.write_hotspot_db import write_hotspot_db_node
from nodes.search_articles import search_articles_node, search_articles_selected_node
from nodes.search_videos import search_videos_node
from nodes.send_feishu_card_and_wait_selection import send_feishu_card_and_wait_selection_node
from nodes.send_feishu_final_script import send_feishu_final_script_node
from nodes.start_hot_fetch import start_hot_fetch_node
from nodes.init_direct_hotspot import init_direct_hotspot_node
from nodes.prepare_selected_topic_materials import prepare_selected_topic_materials_node
from nodes.wait_hot_sources import wait_hot_sources_node
from nodes.aggregate_keyword_materials import aggregate_keyword_materials_node
from nodes.summarize_keyword_materials import summarize_keyword_materials_node
from nodes.fetch_article_contents_selected import fetch_article_contents_selected_node


def _route_after_start(state: HotCollectState) -> str:
    if (state.get("direct_hotspot_keyword") or "").strip():
        return "init_direct_hotspot"
    return "_fanout_to_hot_sources"


def _fanout_to_hot_sources_node(state: HotCollectState) -> dict:
    return {}


def _route_after_wait_hot_sources(state: HotCollectState) -> str:
    """
    等微博+抖音两路都写入 hot_sources_barrier 后才进入聚合。
    NamedBarrierValue 的 key 在未就绪时不可读（KeyError），这里用条件路由实现等待。
    """

    try:
        _ = state["hot_sources_barrier"]
        return "aggregate_hot_titles"
    except KeyError:
        return "wait_hot_sources"


def _route_after_hotspot_db(state: HotCollectState) -> str:
    """
    数据库去重节点之后：
    - 如果已经产出唯一热点（filtered_keywords 非空），进入素材搜索
    - 如果命中重复热点，需要回到 LLM 重新选择
    """

    if state.get("hot_db_ok") is True:
        return "write_hotspot_db"
    return "pick_hotspot_llm"


"""
视频素材链路说明（串行，无条件路由）：
- 统一维护同一个 `.data/video_assets/<thread_id>/...` 目录树。
- 每个节点在执行前会检查 out_dir 是否已存在字幕文件，若存在则跳过当前工具。
- 因此不再需要根据 `subtitle_path` 做条件跳转，按顺序串行即可。
"""


def build_graph() -> StateGraph:
    graph = StateGraph(HotCollectState)

    # 阶段一：热点发现与素材收集（拆成小节点）
    graph.add_node("start_hot_fetch", start_hot_fetch_node, metadata={"label_zh": "开始抓取热点"})
    graph.add_node(
        "_fanout_to_hot_sources",
        _fanout_to_hot_sources_node,
        metadata={"label_zh": "扇出到微博/抖音"},
    )
    graph.add_node(
        "init_direct_hotspot",
        init_direct_hotspot_node,
        metadata={"label_zh": "初始化直接热点"},
    )
    graph.add_node("fetch_weibo_hot", fetch_weibo_hot_node, metadata={"label_zh": "获取微博热搜"})
    graph.add_node("fetch_douyin_hot", fetch_douyin_hot_node, metadata={"label_zh": "获取抖音热榜"})
    graph.add_node("wait_hot_sources", wait_hot_sources_node, metadata={"label_zh": "等待热点源齐备"})
    graph.add_node(
        "aggregate_hot_titles",
        aggregate_hot_titles_node,
        metadata={"label_zh": "聚合热榜与候选"},
    )
    graph.add_node(
        "pick_hotspot_llm",
        pick_hotspot_llm_node,
        metadata={"label_zh": "AI 选择唯一热点"},
    )
    graph.add_node(
        "check_hotspot_db",
        check_hotspot_db_node,
        metadata={"label_zh": "数据库去重"},
    )
    graph.add_node(
        "write_hotspot_db",
        write_hotspot_db_node,
        metadata={"label_zh": "写入热点数据库"},
    )

    # 第一阶段：热点全貌理解
    graph.add_node("search_articles", search_articles_node, metadata={"label_zh": "搜索文章素材"})
    graph.add_node(
        "aggregate_article_text",
        aggregate_article_text_node,
        metadata={"label_zh": "聚合文章正文"},
    )

    # 阶段二~五
    graph.add_node(
        "infer_account_topics_and_generate_proposals",
        infer_account_topics_and_generate_proposals_node,
        metadata={"label_zh": "生成选题并排名"},
    )
    graph.add_node(
        "send_feishu_card_and_wait_selection",
        send_feishu_card_and_wait_selection_node,
        metadata={"label_zh": "飞书发卡片并等待选择"},
    )
    graph.add_node(
        "send_feishu_final_script",
        send_feishu_final_script_node,
        metadata={"label_zh": "发送终版脚本到飞书"},
    )
    graph.add_node(
        "prepare_selected_topic_materials",
        prepare_selected_topic_materials_node,
        metadata={"label_zh": "按已选题目重建素材任务"},
    )
    graph.add_node(
        "search_articles_selected",
        search_articles_selected_node,
        metadata={"label_zh": "按选题搜索文章素材"},
    )
    graph.add_node(
        "llm_filter_articles_selected",
        llm_filter_articles_node,
        metadata={"label_zh": "按选题筛选文章候选"},
    )
    graph.add_node(
        "fetch_article_contents_selected",
        fetch_article_contents_selected_node,
        metadata={"label_zh": "抓取文章正文(按选题)"},
    )
    graph.add_node(
        "search_videos_selected",
        search_videos_node,
        metadata={"label_zh": "按选题搜索视频素材"},
    )
    graph.add_node(
        "llm_filter_videos_selected",
        llm_filter_videos_node,
        metadata={"label_zh": "按选题筛选视频候选"},
    )
    graph.add_node(
        "download_subtitles_bbdown_selected",
        download_subtitles_bbdown_node,
        metadata={"label_zh": "BBDown 下载字幕(按选题)"},
    )
    graph.add_node(
        "aggregate_keyword_materials_selected",
        aggregate_keyword_materials_node,
        metadata={"label_zh": "按选题聚合关键词资料(文章+视频)"},
    )
    graph.add_node(
        "summarize_keyword_materials_selected",
        summarize_keyword_materials_node,
        metadata={"label_zh": "AI 提炼关键词资料"},
    )
    graph.add_node("final_script", final_script_node, metadata={"label_zh": "生成终版脚本"})

    graph.set_entry_point("start_hot_fetch")

    # 条件路由：直接热点模式 vs 正常抓取模式
    graph.add_conditional_edges(
        "start_hot_fetch",
        _route_after_start,
        {
            "init_direct_hotspot": "init_direct_hotspot",
            "_fanout_to_hot_sources": "_fanout_to_hot_sources",
        },
    )

    # 正常模式：扇出到微博 + 抖音并行抓取
    graph.add_edge("_fanout_to_hot_sources", "fetch_weibo_hot")
    graph.add_edge("_fanout_to_hot_sources", "fetch_douyin_hot")

    # 直接热点模式：跳过抓取/聚合/LLM选热点/去重，直接写入热点 DB
    graph.add_edge("init_direct_hotspot", "write_hotspot_db")

    # 两路并行抓取 -> 栅栏汇合 -> 聚合热点标题 -> LLM 初筛
    graph.add_edge("fetch_weibo_hot", "wait_hot_sources")
    graph.add_edge("fetch_douyin_hot", "wait_hot_sources")
    graph.add_conditional_edges(
        "wait_hot_sources",
        _route_after_wait_hot_sources,
        {
            "wait_hot_sources": "wait_hot_sources",
            "aggregate_hot_titles": "aggregate_hot_titles",
        },
    )
    graph.add_edge("aggregate_hot_titles", "pick_hotspot_llm")
    graph.add_edge("pick_hotspot_llm", "check_hotspot_db")
    graph.add_conditional_edges(
        "check_hotspot_db",
        _route_after_hotspot_db,
        {
            "pick_hotspot_llm": "pick_hotspot_llm",
            "write_hotspot_db": "write_hotspot_db",
        },
    )
    graph.add_edge("write_hotspot_db", "search_articles")

    # 第一阶段：只走文章链路，先弄清热点全貌
    graph.add_edge("search_articles", "aggregate_article_text")
    graph.add_edge("aggregate_article_text", "infer_account_topics_and_generate_proposals")
    graph.add_edge(
        "infer_account_topics_and_generate_proposals",
        "send_feishu_card_and_wait_selection",
    )
    graph.add_edge("send_feishu_card_and_wait_selection", "prepare_selected_topic_materials")

    # 第二阶段：基于已选新题目，复用原有文章/视频两条素材线
    graph.add_edge("prepare_selected_topic_materials", "search_articles_selected")
    graph.add_edge("prepare_selected_topic_materials", "search_videos_selected")

    graph.add_edge("search_articles_selected", "llm_filter_articles_selected")
    graph.add_edge("llm_filter_articles_selected", "fetch_article_contents_selected")

    graph.add_edge("search_videos_selected", "llm_filter_videos_selected")
    graph.add_edge("llm_filter_videos_selected", "download_subtitles_bbdown_selected")

    # 汇合：把三条 query 的“文章 + 字幕”按 keyword 组织成大段文本
    graph.add_edge(
        ["fetch_article_contents_selected", "download_subtitles_bbdown_selected"],
        "aggregate_keyword_materials_selected",
    )
    # 再做一次提炼，避免信息过长
    graph.add_edge("aggregate_keyword_materials_selected", "summarize_keyword_materials_selected")
    graph.add_edge("summarize_keyword_materials_selected", "final_script")

    graph.add_edge("final_script", "send_feishu_final_script")
    graph.add_edge("send_feishu_final_script", END)

    return graph
