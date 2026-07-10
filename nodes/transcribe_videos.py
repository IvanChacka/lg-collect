from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.asr import transcribe_audio
from tools.utils import get_config
from tools.video_asset_store import find_existing_audio, find_existing_subtitle, iter_video_items


def transcribe_videos_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：对统一目录里仍无字幕文本的视频执行 ASR，并把文本保存到该视频目录。

    说明：
    - 使用讯飞“极速语音转写”（HTTP 任务式）转写下载到的音频
    - 若目录里已有字幕/转写文本，本节点跳过且不输出到结果
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
        return {"video_assets": []}

    errors: list[str] = []
    fail_policy = str(cfg.get("video_transcribe_fail_policy") or "partial").strip().lower()

    concurrency = int(cfg.get("iflytek_speed_concurrency") or 2)
    if concurrency <= 0:
        raise ValueError("IFLYTEK_SPEED_CONCURRENCY 必须为正整数")

    def _task(a: dict) -> dict[str, Any]:
        url = (a.get("url") or "").strip()
        title = (a.get("title") or "未命名视频").strip()
        keyword = (a.get("keyword") or "").strip() or "unknown"
        existing_subtitle = find_existing_subtitle(out_dir=str(a.get("out_dir") or ""))
        if existing_subtitle.ok and existing_subtitle.subtitle_path:
            return {}
        existing_audio = find_existing_audio(out_dir=str(a.get("out_dir") or ""))
        audio_path = (a.get("audio_path") or "").strip() or (existing_audio.audio_path or "")

        if audio_path and Path(audio_path).exists():
            debug_log(
                f"transcribe_videos asr start keyword={keyword!r} title={title!r} audio={audio_path!r}",
                cfg=cfg,
                prefix="node",
            )
            text = transcribe_audio(audio_path=audio_path, cfg=cfg)
            out_dir = Path(str(a.get("out_dir") or "").strip())
            out_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = out_dir / "asr_transcript.txt"
            transcript_path.write_text(text, encoding="utf-8")
            debug_log(
                f"transcribe_videos asr done keyword={keyword!r} title={title!r} chars={len(text)}",
                cfg=cfg,
                prefix="node",
            )
        else:
            raise RuntimeError("缺少可转写的本地音频文件")

        return {
            "url": url,
            "title": title,
            "keyword": keyword,
            "out_dir": str(a.get("out_dir") or "").strip(),
            "subtitle_generated": True,
            "saved_dir": str(a.get("out_dir") or "").strip(),
        }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(assets)))) as ex:
        fut_map = {ex.submit(_task, a): a for a in assets}
        for fut in as_completed(fut_map):
            a = fut_map[fut]
            url = (a.get("url") or "").strip()
            title = (a.get("title") or "未命名视频").strip()
            try:
                result = fut.result()
                if result:
                    results.append(result)
            except Exception as e:
                msg = f"{title} <{url}> 转写失败: {e!r}"
                errors.append(msg)
                results.append(
                    {
                        "url": url,
                        "title": title,
                        "keyword": (a.get("keyword") or "").strip() or "unknown",
                        "out_dir": str(a.get("out_dir") or "").strip(),
                        "subtitle_generated": False,
                        "reason": f"转写失败: {e!r}",
                    }
                )

    # 判断“是否至少有可用字幕文本”（包含：原本就有字幕/转写文本 + 本次 ASR 新生成）
    has_any_subtitle = False
    for a in assets:
        if find_existing_subtitle(out_dir=str(a.get("out_dir") or "")).ok:
            has_any_subtitle = True
            break
    if not has_any_subtitle:
        has_any_subtitle = any(r.get("subtitle_generated") is True for r in results)

    if errors:
        if fail_policy in {"raise", "strict", "fail"}:
            raise RuntimeError("transcribe_videos 存在失败项: " + " | ".join(errors[:5]))
        if not has_any_subtitle:
            raise RuntimeError("transcribe_videos 全部失败: " + " | ".join(errors[:5]))
    return {"video_assets": results}
