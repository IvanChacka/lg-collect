from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许 `python scripts/xxx.py` 直接运行（无需额外设置 PYTHONPATH）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.utils import get_config  # noqa: E402
from tools.video_assets import download_subtitle_only  # noqa: E402


DEFAULT_URLS = [
    "http://www.bilibili.com/video/av116485334767646",
    "http://www.bilibili.com/video/av116490300818480",
    "http://www.bilibili.com/video/av116487012484837",
    "http://www.bilibili.com/video/av116046526682948",
    "http://www.bilibili.com/video/av979159808",
    "http://www.bilibili.com/video/av112942775338022",
    "http://www.bilibili.com/video/av116486593059366",
]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Smoke test: verify BBDown can download (AI) subtitles into .srt."
    )
    ap.add_argument("--url", default="", help="single bilibili video url (BV/av/url)")
    ap.add_argument(
        "--out-root",
        default=".data/bbdown_smoke",
        help="output root directory",
    )
    ap.add_argument("--keyword", default="debug", help="keyword folder name")
    ap.add_argument("--title", default="debug_video", help="title folder name")
    ap.add_argument(
        "--urls-json",
        default="",
        help="JSON list[str] of urls (overrides --url and defaults)",
    )
    args = ap.parse_args(argv)

    cfg = get_config(None)
    urls: list[str]
    if str(args.urls_json or "").strip():
        urls = json.loads(str(args.urls_json))
        if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
            raise SystemExit("--urls-json must be a JSON list[str]")
    elif str(args.url or "").strip():
        urls = [str(args.url).strip()]
    else:
        urls = DEFAULT_URLS

    out_root = str(args.out_root).strip() or ".data/bbdown_smoke"
    Path(out_root).mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    ok_any = False
    for i, u in enumerate(urls, start=1):
        asset = download_subtitle_only(
            url=u,
            title=f"{str(args.title)}_{i}",
            keyword=str(args.keyword),
            out_root=out_root,
            cfg=cfg,
        )
        subtitle_path = str(asset.get("subtitle_path") or "").strip()
        ok = bool(subtitle_path) and Path(subtitle_path).exists()
        ok_any = ok_any or ok
        results.append(
            {
                "url": u,
                "ok": ok,
                "subtitle_path": subtitle_path or None,
                "subtitle_error": asset.get("subtitle_error"),
            }
        )

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok_any else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
