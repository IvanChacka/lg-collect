from __future__ import annotations

import re

from langchain_core.runnables.graph import CurveStyle

from core.runtime import build_app


_NODE_LABEL_ZH: dict[str, str] = {
    "start_hot_fetch": "开始抓取热点",
    "fetch_weibo_hot": "获取微博热搜",
    "fetch_douyin_hot": "获取抖音热榜",
    "wait_hot_sources": "等待热点源齐备",
    "aggregate_hot_titles": "聚合热榜与候选",
    "pick_hotspot_llm": "AI 选择唯一热点",
    "check_hotspot_db": "数据库去重",
    "write_hotspot_db": "写入热点数据库",
    "search_articles": "搜索文章素材",
    "aggregate_article_text": "聚合文章正文",
    "search_videos": "搜索视频素材",
    "llm_filter_videos": "AI 筛选视频候选",
    "download_video_assets": "下载字幕/音频",
    "transcribe_videos": "字幕/语音转文字",
    "wait_materials_ready": "等待素材分析齐备",
    "aggregate_material_analysis": "聚合素材分析",
    "infer_account_topics_and_generate_proposals": "生成选题并排名",
    "send_feishu_card_and_wait_selection": "飞书发卡片并等待选择",
    "final_script": "生成终版脚本",
}


def mermaid(
    *,
    lang: str = "zh",
    direction: str = "TB",
    curve: str = "stepAfter",
    node_spacing: int = 60,
    rank_spacing: int = 80,
) -> str:
    """
    返回当前工作流的 Mermaid 图（用于开发期可视化）。

    lang:
    - zh：节点名称尽量显示中文
    - en：使用节点 id 原样输出

    direction:
    - TB/TD：自上而下
    - LR：自左向右

    curve:
    - linear/step/stepAfter/stepBefore...（Mermaid 曲线样式）
    """

    compiled = build_app(use_sqlite=False)
    graph = compiled.get_graph()
    curve_style = _curve_style(curve)
    frontmatter_config = {
        "config": {
            "flowchart": {
                "nodeSpacing": int(node_spacing),
                "rankSpacing": int(rank_spacing),
            }
        },
    }
    text = graph.draw_mermaid(curve_style=curve_style, frontmatter_config=frontmatter_config)
    if lang.lower() != "zh":
        return _mermaid_set_direction(text, direction=direction)
    zh_text = _mermaid_localize(text)
    return _mermaid_set_direction(zh_text, direction=direction)


def _mermaid_localize(text: str) -> str:
    """
    把 Mermaid 中的 node label 替换为中文（不改 node id，保证边连接不受影响）。
    """

    def repl(match: re.Match[str]) -> str:
        node_id = match.group(1)
        label = match.group(2)
        zh = _NODE_LABEL_ZH.get(node_id)
        if not zh:
            return match.group(0)
        return f"\t{node_id}({zh})"

    # 形如：\tfetch_weibo_hot(fetch_weibo_hot)
    return re.sub(r"^\t([A-Za-z0-9_]+)\(([^)]*)\)\s*$", repl, text, flags=re.MULTILINE)


def _mermaid_set_direction(text: str, *, direction: str) -> str:
    d = (direction or "TB").upper()
    if d not in ("TB", "TD", "LR", "RL"):
        d = "TB"
    # 形如：graph TD; / graph TB;
    return re.sub(r"^graph\s+(TD|TB|LR|RL);", f"graph {d};", text, flags=re.MULTILINE)


def _curve_style(curve: str) -> CurveStyle:
    name = (curve or "stepAfter").strip()
    for c in CurveStyle:
        if c.value == name:
            return c
    # 兼容大小写
    lowered = name.lower()
    for c in CurveStyle:
        if c.value.lower() == lowered:
            return c
    return CurveStyle.STEP_AFTER
