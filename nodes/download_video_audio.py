from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.debug_log import debug_log
from tools.utils import get_config
from tools.video_asset_store import find_existing_audio, find_existing_subtitle, iter_video_items
from tools.video_assets import download_audio_only_in_dir


def download_video_audio_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：对“仍然缺字幕”的视频下载音频（BBDown --audio-only），供下游 ASR。

    输入：video_candidates 或 video_assets
    输出：video_assets（当前工具实际尝试过的音频下载结果）
    """

    cfg = get_config(config)
    thread_id = state.get("thread_id") or "thread"
    out_root = os.path.join(".data", "video_assets", thread_id)
    items = iter_video_items(
        candidates=list(state.get("video_candidates") or []),
        assets=list(state.get("video_assets") or []),
        out_root=out_root,
    )
    if not items:
        return {"video_assets": []}

    fail_policy = str(cfg.get("video_download_fail_policy") or "partial").strip().lower()
    concurrency = int(cfg.get("video_download_concurrency") or 3)
    if concurrency <= 0:
        raise ValueError("VIDEO_DOWNLOAD_CONCURRENCY 必须为正整数")

    targets = [item for item in items if not find_existing_subtitle(out_dir=item["out_dir"]).ok]
    if not targets:
        return {"video_assets": []}

    errors: list[str] = []
    results: list[dict[str, Any]] = []

    def _task(item: dict[str, str]) -> dict[str, Any]:
        audio = find_existing_audio(out_dir=item["out_dir"])
        if audio.ok and audio.audio_path:
            return {
                "url": item["url"],
                "title": item["title"],
                "keyword": item["keyword"],
                "out_dir": item["out_dir"],
                "audio_downloaded": False,
                "audio_path": audio.audio_path,
                "reason": "audio_exists",
            }
        debug_log(
            f"download_video_audio start keyword={item['keyword']!r} title={item['title']!r} url={item['url']!r}",
            cfg=cfg,
            prefix="node",
        )
        r = download_audio_only_in_dir(url=item["url"], out_dir=item["out_dir"], cfg=cfg)
        audio_path = str(r.get("audio_path") or "").strip()
        debug_log(
            f"download_video_audio done keyword={item['keyword']!r} audio={audio_path!r}",
            cfg=cfg,
            prefix="node",
        )
        result: dict[str, Any] = {
            "url": item["url"],
            "title": item["title"],
            "keyword": item["keyword"],
            "out_dir": item["out_dir"],
            "audio_downloaded": bool(audio_path),
        }
        if audio_path:
            result["audio_path"] = audio_path
        else:
            result["reason"] = "audio_not_found"
        return result

    with ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(targets)))) as ex:
        fut_map = {ex.submit(_task, item): item for item in targets}
        for fut in as_completed(fut_map):
            item = fut_map[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                msg = f"{item['title']} <{item['url']}> 音频下载失败(BBDown): {e!r}"
                errors.append(msg)
                debug_log(
                    f"download_video_audio error keyword={item['keyword']!r} title={item['title']!r} error={e!r}",
                    cfg=cfg,
                    prefix="node",
                )
                results.append(
                    {
                        "url": item["url"],
                        "title": item["title"],
                        "keyword": item["keyword"],
                        "out_dir": item["out_dir"],
                        "audio_downloaded": False,
                        "reason": repr(e),
                    }
                )

    if errors:
        if fail_policy in {"raise", "strict", "fail"}:
            raise RuntimeError("download_video_audio 存在失败项: " + " | ".join(errors[:5]))
        ok_audio = [item for item in results if str(item.get("audio_path") or "").strip()]
        if not ok_audio:
            raise RuntimeError("download_video_audio 全部失败: " + " | ".join(errors[:5]))

    return {"video_assets": results}
