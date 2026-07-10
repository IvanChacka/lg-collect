from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.asr import srt_to_text
from tools.utils import get_config
from tools.video_asset_store import find_existing_subtitle, iter_video_items
from tools.video_materials import build_video_materials_from_subtitles


def aggregate_video_subtitles_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：聚合已下载到本地的字幕文本（不做 ASR、不做 LLM）。

    设计目的：
    - download_subtitles_bbdown / download_subtitles_cc 只负责“把字幕文件拿到本地”
    - transcribe_videos 可能会优先使用字幕或走 ASR，但下游有时需要“所有字幕文本”的统一视图
    - 这里把 video_assets 里的 subtitle_path 全量读取并转为纯文本，写入 state 便于后续使用/排查
    """

    cfg = get_config(config)
    thread_id = state.get("thread_id") or "thread"
    out_root = os.path.join(".data", "video_assets", thread_id)
    assets = iter_video_items(
        candidates=list(state.get("video_candidates") or []),
        assets=list(state.get("video_assets") or []),
        out_root=out_root,
    )
    if not assets:
        return {"video_subtitles": [], "video_subtitles_text": ""}

    subtitles: list[dict[str, Any]] = []
    parts: list[str] = []
    missing_files: list[str] = []
    for a in assets:
        url = str((a or {}).get("url") or "").strip()
        title = str((a or {}).get("title") or "").strip()
        keyword = str((a or {}).get("keyword") or "").strip()
        existing = find_existing_subtitle(out_dir=str((a or {}).get("out_dir") or ""))
        subtitle_path = existing.subtitle_path or ""

        raw = ""
        if subtitle_path:
            p = Path(subtitle_path)
            if p.exists():
                raw = p.read_text(encoding="utf-8", errors="ignore")
            else:
                missing_files.append(subtitle_path)
        text = srt_to_text(raw) if raw else ""

        item = {
            "url": url,
            "title": title,
            "keyword": keyword,
            "subtitle_path": subtitle_path or None,
            "subtitle_text": text,
            "subtitle_chars": len(text),
        }
        subtitles.append(item)

        if text:
            header = " ".join([x for x in [keyword, title] if x]).strip()
            if header:
                parts.append(f"## {header}\n{text}")
            else:
                parts.append(text)

    patch: dict[str, Any] = {
        "video_subtitles": subtitles,
        "video_subtitles_text": "\n\n".join(parts).strip(),
    }

    # 若当前节点是“字幕齐备”支路（通常来自 download_subtitles_* 直接跳转过来），
    # 且此前尚未产出视频 transcripts/materials/analysis，则在这里基于字幕生成这些输出。
    # 注意：当上游走过 transcribe_videos（可能包含 ASR 结果）时，这里不覆盖其输出。
    has_video_outputs = bool(state.get("video_transcripts") or state.get("materials") or state.get("video_analysis"))
    all_subtitles_ready = (not missing_files) and all(
        find_existing_subtitle(out_dir=str((a or {}).get("out_dir") or "")).ok for a in assets
    )
    if (not has_video_outputs) and all_subtitles_ready:
        material_assets = [
            {
                **a,
                "subtitle_path": find_existing_subtitle(out_dir=str((a or {}).get("out_dir") or "")).subtitle_path,
            }
            for a in assets
        ]
        transcripts, materials, analysis = build_video_materials_from_subtitles(
            assets=material_assets,
            hot_titles=state.get("filtered_keywords") or [],
            cfg=cfg,
        )
        patch.update(
            {
                "video_transcripts": transcripts,
                "materials": materials,
                "video_analysis": analysis,
                "materials_barrier": "videos",
            }
        )
    return patch
