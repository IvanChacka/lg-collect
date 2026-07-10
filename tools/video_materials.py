from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.asr import srt_to_text
from tools.llm_client import llm_analyze_materials


def build_video_materials_from_subtitles(
    *,
    assets: list[dict],
    hot_titles: list[str],
    cfg: dict[str, Any],
) -> tuple[list[dict], list[dict], str]:
    """
    将 video_assets 中的 subtitle_path 转为 video_transcripts + materials，并做一次视频素材分析。

    约束：
    - 仅适用于字幕已齐备的场景（否则返回结果可能为空/不完整）。
    """

    transcripts: list[dict] = []
    materials: list[dict] = []

    for a in assets:
        subtitle_path = str(a.get("subtitle_path") or "").strip()
        subtitle_text = ""
        if subtitle_path and Path(subtitle_path).exists():
            raw = Path(subtitle_path).read_text(encoding="utf-8", errors="ignore")
            subtitle_text = srt_to_text(raw)

        transcripts.append(
            {
                "url": str(a.get("url") or "").strip(),
                "title": str(a.get("title") or "未命名视频").strip(),
                "keyword": str(a.get("keyword") or "").strip() or "unknown",
                "used": "subtitle",
                "text": subtitle_text,
                "subtitle_text": subtitle_text,
                "error": a.get("error"),
            }
        )

        if subtitle_text:
            materials.append(
                {
                    "kind": "video",
                    "title": str(a.get("title") or "").strip(),
                    "url": str(a.get("url") or "").strip(),
                    "snippet": None,
                    "content": subtitle_text,
                    "source": "bilibili",
                    "meta": {
                        "keyword": str(a.get("keyword") or ""),
                        "used": "subtitle",
                        "subtitle_text": subtitle_text,
                    },
                }
            )

    analysis = ""
    if materials:
        analysis = llm_analyze_materials(
            kind="video",
            hot_titles=hot_titles or [],
            materials=materials,
            cfg=cfg,
        )
    return transcripts, materials, analysis

