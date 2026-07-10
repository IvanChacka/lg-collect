from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config
from tools.video_asset_store import find_existing_subtitle, iter_video_items


def _html_to_text(html: str) -> str:
    raw = str(html or "").strip()
    if not raw:
        return ""
    try:
        return BeautifulSoup(raw, "lxml").get_text("\n", strip=True)
    except Exception:
        return raw


def _extract_to_text(extract: dict[str, Any]) -> str:
    """
    兼容两种来源：
    - 新版 fetch_article_contents_selected 写入的 extract.text（已是纯文本）
    - 旧版/其他链路写入的 extract.html（需要再转成文本）
    """

    t = str((extract or {}).get("text") or "").strip()
    if t:
        return t
    html = str((extract or {}).get("html") or "").strip()
    if html:
        return _html_to_text(html)
    return ""


def _compact(text: str, max_chars: int) -> str:
    t = str(text or "").strip()
    if max_chars <= 0:
        return t
    if len(t) <= max_chars:
        return t
    return t[:max_chars].rstrip() + "\n...(truncated)...\n"


def aggregate_keyword_materials_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：把“已选题目”阶段的 3 个 query（state.filtered_keywords）检索到的全部资料按 keyword 分块聚合。

    输出：
    - keyword_materials_text: 超长正文（每个 keyword 一个块：标题 + 文章正文 + 视频字幕）
    - keyword_materials_by_keyword: {keyword: {articles:[], videos:[], text:"..."}}
    """

    cfg = get_config(config)
    keywords = [str(x).strip() for x in (state.get("filtered_keywords") or []) if str(x).strip()]
    if not keywords:
        # 禁止静默兜底：已选题目阶段缺少关键词应直接报错，避免下游继续跑但实际没收集到资料。
        if state.get("selected_proposal_id"):
            raise RuntimeError("aggregate_keyword_materials_selected 缺少 filtered_keywords（应由 prepare_selected_topic_materials 生成）")
        return {"keyword_materials_text": "", "keyword_materials_by_keyword": {}}

    per_article_chars = int(cfg.get("keyword_materials_article_max_chars") or 12000)
    per_video_chars = int(cfg.get("keyword_materials_video_max_chars") or 20000)
    per_keyword_chars = int(cfg.get("keyword_materials_per_keyword_max_chars") or 80000)
    total_chars = int(cfg.get("keyword_materials_total_max_chars") or 200000)

    # 文章：优先用 aggregate_article_text 产物（extracts），其次用 LLM 预抓取的 prefetched_html
    article_extracts = [x for x in (state.get("article_extracts") or []) if isinstance(x, dict)]
    article_candidates = [x for x in (state.get("article_candidates") or []) if isinstance(x, dict)]

    # 视频：直接从 download_subtitles_bbdown 写入的 video_assets 目录读取字幕文件（不依赖独立聚合节点）
    thread_id = state.get("thread_id") or "thread"
    out_root = os.path.join(".data", "video_assets", thread_id)
    assets = iter_video_items(
        candidates=list(state.get("video_candidates") or []),
        assets=list(state.get("video_assets") or []),
        out_root=out_root,
    )

    by_kw: dict[str, dict[str, Any]] = {}
    for kw in keywords:
        by_kw[kw] = {"keyword": kw, "articles": [], "videos": [], "text": ""}

    def _kw_of(item: dict[str, Any]) -> str:
        return str(item.get("keyword") or "").strip()

    # 文章聚合
    for a in article_extracts:
        kw = _kw_of(a)
        if kw and kw in by_kw:
            url = str(a.get("url") or "").strip()
            title = str(a.get("title") or "").strip() or "未命名文章"
            text = _compact(_extract_to_text(a), per_article_chars)
            by_kw[kw]["articles"].append({"title": title, "url": url, "text": text})

    # 若 extract 不含 keyword（旧数据兼容），尝试从候选里补齐 keyword+正文
    if article_candidates and any(not _kw_of(a) for a in article_extracts):
        for a in article_candidates:
            kw = _kw_of(a)
            if not kw or kw not in by_kw:
                continue
            url = str(a.get("url") or "").strip()
            title = str(a.get("title") or "").strip() or "未命名文章"
            html = str(a.get("prefetched_html") or "")
            if not html:
                continue
            text = _compact(_html_to_text(html), per_article_chars)
            by_kw[kw]["articles"].append({"title": title, "url": url, "text": text})

    # 视频聚合：读取每个视频目录下已下载的字幕文件
    for a in assets:
        kw = str((a or {}).get("keyword") or "").strip()
        if not kw or kw not in by_kw:
            continue
        url = str((a or {}).get("url") or "").strip()
        title = str((a or {}).get("title") or "").strip() or "未命名视频"
        existing = find_existing_subtitle(out_dir=str((a or {}).get("out_dir") or ""))
        subtitle_path = str(existing.subtitle_path or "").strip()
        if not subtitle_path:
            continue
        p = Path(subtitle_path)
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8", errors="ignore")
        text = _compact(raw, per_video_chars)
        if not text:
            continue
        by_kw[kw]["videos"].append({"title": title, "url": url, "text": text})

    # 拼接成最终大段文本
    blocks: list[str] = []
    used_total = 0
    for kw in keywords:
        group = by_kw.get(kw) or {}
        parts: list[str] = [f"# {kw}"]

        articles = group.get("articles") or []
        for i, a in enumerate(articles, start=1):
            title = str(a.get("title") or "").strip()
            url = str(a.get("url") or "").strip()
            text = str(a.get("text") or "").strip()
            if not text:
                continue
            head = f"## 文章 {i}：{title}" + (f"\n来源：{url}" if url else "")
            parts.append(f"{head}\n{text}")

        videos = group.get("videos") or []
        for i, v in enumerate(videos, start=1):
            title = str(v.get("title") or "").strip()
            url = str(v.get("url") or "").strip()
            text = str(v.get("text") or "").strip()
            if not text:
                continue
            head = f"## 视频字幕 {i}：{title}" + (f"\n来源：{url}" if url else "")
            parts.append(f"{head}\n{text}")

        block = "\n\n".join([p for p in parts if str(p).strip()]).strip()
        block = _compact(block, per_keyword_chars)
        if not block:
            continue
        if total_chars > 0 and used_total >= total_chars:
            break
        if total_chars > 0 and used_total + len(block) > total_chars:
            remain = max(0, total_chars - used_total)
            if remain <= 0:
                break
            block = _compact(block, remain)
        used_total += len(block)
        blocks.append(block)
        by_kw[kw]["text"] = block

    return {
        "keyword_materials_by_keyword": by_kw,
        "keyword_materials_text": "\n\n".join(blocks).strip(),
    }
