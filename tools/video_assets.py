from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


def safe_slug(text: str, *, max_len: int = 80) -> str:
    s = (text or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[\\\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff _.-]+", "", s)
    s = s.strip(" ._-")
    if not s:
        s = "untitled"
    return s[:max_len]


def _run(cmd: list[str], *, timeout: int = 900) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "unknown error").strip()[:800])

def _parse_bool(raw: Any, *, default: bool = False) -> bool:
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if s == "":
        return default
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _bbdown_common_args(*, url: str, out_dir: Path, cfg: dict[str, Any]) -> list[str]:
    bbdown = str(cfg.get("bbdown_bin") or "BBDown").strip()
    cookie = str(cfg.get("bilibili_cookie") or "").strip()
    skip_ai = _parse_bool(cfg.get("bbdown_skip_ai"), default=False)

    common = [bbdown, url, "--work-dir", str(out_dir)]
    if cookie:
        common += ["-c", cookie]
    # BBDown 的 --skip-ai 在较新版本中默认开启；为避免“明明有 AI 字幕但下载不到”，这里显式传值。
    # 注意：BBDown 支持 `--skip-ai false/true` 形式（即使 help 中看起来像开关）。
    common += ["--skip-ai", "true" if skip_ai else "false"]
    return common


def download_subtitle_only(
    *,
    url: str,
    title: str,
    keyword: str,
    out_root: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    仅下载字幕（若可用）。用于“先字幕、后音频/ASR”的加速策略。
    """

    out_dir = Path(out_root) / safe_slug(keyword) / safe_slug(title)
    out_dir.mkdir(parents=True, exist_ok=True)

    common = _bbdown_common_args(url=url, out_dir=out_dir, cfg=cfg)
    subtitle_error: str | None = None
    try:
        # 仅字幕：显式跳过封面，避免产生无关资产。
        _run(common + ["--sub-only", "--skip-cover"], timeout=600)
    except Exception as e:
        # 字幕非强依赖：失败时仅返回 subtitle_path=None，由上游决定是否继续走其他手段。
        subtitle_error = repr(e)

    srt_files = sorted(list(out_dir.rglob("*.srt")))
    return {
        "url": url,
        "title": title,
        "keyword": keyword,
        "out_dir": str(out_dir),
        "subtitle_path": str(srt_files[0]) if srt_files else None,
        # 字幕下载节点不应输出音频路径（如需音频/ASR 请显式增加节点触发，避免静默兜底）。
        "subtitle_error": subtitle_error,
    }


def download_audio_only(
    *,
    url: str,
    title: str,
    keyword: str,
    out_root: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    仅下载音频（强依赖）。用于字幕不可得时的 ASR 兜底链路（显式触发，不做静默回退）。
    """

    out_dir = Path(out_root) / safe_slug(keyword) / safe_slug(title)
    out_dir.mkdir(parents=True, exist_ok=True)

    common = _bbdown_common_args(url=url, out_dir=out_dir, cfg=cfg)
    _run(common + ["--audio-only", "--audio-ascending", "--skip-mux"], timeout=900)

    audio_files: list[Path] = []
    for ext in ("*.m4a", "*.aac", "*.mp3", "*.opus", "*.ogg", "*.wav", "*.flac", "*.webm"):
        audio_files.extend(out_dir.rglob(ext))
    audio_files = sorted(audio_files)
    if not audio_files:
        raise RuntimeError("BBDown 未产出音频文件")
    return {
        "url": url,
        "title": title,
        "keyword": keyword,
        "out_dir": str(out_dir),
        "subtitle_path": None,
        "audio_path": str(audio_files[0]),
    }


def download_audio_only_in_dir(
    *,
    url: str,
    out_dir: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    仅下载音频到指定 out_dir（不重新计算目录）。

    设计目的：
    - 保证“音频下载节点”与字幕节点维护同一个文件夹（统一资产目录）。
    """

    p = Path(str(out_dir or "").strip())
    if not p:
        raise ValueError("missing out_dir")
    p.mkdir(parents=True, exist_ok=True)

    common = _bbdown_common_args(url=url, out_dir=p, cfg=cfg)
    _run(common + ["--audio-only", "--audio-ascending", "--skip-mux"], timeout=900)

    audio_files: list[Path] = []
    for ext in ("*.m4a", "*.aac", "*.mp3", "*.opus", "*.ogg", "*.wav", "*.flac", "*.webm"):
        audio_files.extend(p.rglob(ext))
    audio_files = sorted(audio_files)
    if not audio_files:
        raise RuntimeError("BBDown 未产出音频文件")
    return {"audio_path": str(audio_files[0])}


def download_subtitle_or_audio(
    *,
    url: str,
    title: str,
    keyword: str,
    out_root: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    下载字幕（若可用）并强制下载音频，供本地 ASR 转写使用。

    返回：
    - subtitle_path: str|None
    - audio_path: str
    - out_dir: str
    """

    out_dir = Path(out_root) / safe_slug(keyword) / safe_slug(title)
    out_dir.mkdir(parents=True, exist_ok=True)

    common = _bbdown_common_args(url=url, out_dir=out_dir, cfg=cfg)

    # 1) 先尝试字幕（含 AI 字幕）
    try:
        _run(common + ["--sub-only"], timeout=600)
    except Exception:
        # 字幕不是强依赖，失败时继续拉音频。
        pass

    srt_files = sorted(list(out_dir.rglob("*.srt")))

    # 2) 音频必须下载成功，否则后续无法进行本地 ASR。
    _run(common + ["--audio-only", "--audio-ascending", "--skip-mux"], timeout=900)

    audio_files: list[Path] = []
    for ext in ("*.m4a", "*.aac", "*.mp3", "*.opus", "*.ogg", "*.wav", "*.flac", "*.webm"):
        audio_files.extend(out_dir.rglob(ext))
    audio_files = sorted(audio_files)
    if not audio_files:
        raise RuntimeError("BBDown 未产出音频文件")
    return {
        "url": url,
        "title": title,
        "keyword": keyword,
        "out_dir": str(out_dir),
        "subtitle_path": str(srt_files[0]) if srt_files else None,
        "audio_path": str(audio_files[0]),
    }
