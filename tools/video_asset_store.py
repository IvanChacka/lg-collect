from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.video_assets import safe_slug


@dataclass(frozen=True)
class ExistingSubtitle:
    ok: bool
    subtitle_path: str | None


@dataclass(frozen=True)
class ExistingAudio:
    ok: bool
    audio_path: str | None


def find_existing_subtitle(*, out_dir: str) -> ExistingSubtitle:
    """
    检查统一资产目录中是否已经存在字幕文件（.srt）。

    Hard rule:
    - 只做“存在性检查”，不做 mock，不做静默兜底。
    """

    p = Path(str(out_dir or "").strip())
    if not p:
        return ExistingSubtitle(ok=False, subtitle_path=None)
    if not p.exists() or not p.is_dir():
        return ExistingSubtitle(ok=False, subtitle_path=None)
    subtitle_files = sorted(list(p.rglob("*.srt"))) or sorted(list(p.rglob("asr_transcript.txt")))
    if not subtitle_files:
        return ExistingSubtitle(ok=False, subtitle_path=None)
    return ExistingSubtitle(ok=True, subtitle_path=str(subtitle_files[0]))


def find_existing_audio(*, out_dir: str) -> ExistingAudio:
    p = Path(str(out_dir or "").strip())
    if not p:
        return ExistingAudio(ok=False, audio_path=None)
    if not p.exists() or not p.is_dir():
        return ExistingAudio(ok=False, audio_path=None)
    audio_files: list[Path] = []
    for ext in ("*.m4a", "*.aac", "*.mp3", "*.opus", "*.ogg", "*.wav", "*.flac", "*.webm"):
        audio_files.extend(p.rglob(ext))
    audio_files = sorted(audio_files)
    if not audio_files:
        return ExistingAudio(ok=False, audio_path=None)
    return ExistingAudio(ok=True, audio_path=str(audio_files[0]))


def normalize_video_asset(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": str(a.get("url") or "").strip(),
        "title": str(a.get("title") or "").strip(),
        "keyword": str(a.get("keyword") or "").strip(),
        "out_dir": str(a.get("out_dir") or "").strip(),
        "subtitle_path": str(a.get("subtitle_path") or "").strip() or None,
        "subtitle_error": a.get("subtitle_error"),
        "audio_path": str(a.get("audio_path") or "").strip() or None,
    }


def video_out_dir(*, out_root: str, keyword: str, title: str) -> str:
    return str(Path(out_root) / safe_slug(keyword or "unknown") / safe_slug(title or "未命名视频"))


def iter_video_items(*, candidates: list[dict], assets: list[dict], out_root: str) -> list[dict[str, str]]:
    source = candidates or assets
    items: list[dict[str, str]] = []
    for item in source:
        url = str((item or {}).get("url") or "").strip()
        if not url:
            continue
        title = str((item or {}).get("title") or "未命名视频").strip() or "未命名视频"
        keyword = str((item or {}).get("keyword") or "").strip() or "unknown"
        out_dir = str((item or {}).get("out_dir") or "").strip() or video_out_dir(
            out_root=out_root,
            keyword=keyword,
            title=title,
        )
        audio_path = str((item or {}).get("audio_path") or "").strip()
        items.append(
            {
                "url": url,
                "title": title,
                "keyword": keyword,
                "out_dir": out_dir,
                "audio_path": audio_path,
            }
        )
    return items


def subtitle_tool_result(
    *,
    item: dict[str, str],
    subtitle_downloaded: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": item["url"],
        "title": item["title"],
        "keyword": item["keyword"],
        "out_dir": item["out_dir"],
        "subtitle_downloaded": subtitle_downloaded,
    }
    if subtitle_downloaded:
        result["saved_dir"] = item["out_dir"]
    else:
        result["reason"] = reason or "no_srt"
    return result
