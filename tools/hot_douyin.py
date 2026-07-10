from __future__ import annotations

import os
from urllib.parse import urlencode
from typing import Any

from bs4 import BeautifulSoup

from core.state import HotItem
from tools.debug_log import debug_log
from tools.http import get_json, get_text


def _clean_url(value: Any, default: str) -> str:
    url = str(value or "").strip()
    return url or default


def fetch_douyin_hot(cfg: dict[str, Any]) -> list[HotItem]:
    """
    获取抖音热点榜列表。

    说明：
    - 本项目不允许“兜底/回退链路”在运行时悄悄切换数据源。
    - 你可以通过配置显式选择数据源；不支持的值会直接报错。
    """

    source = (
        cfg.get("douyin_hot_source")
        or os.getenv("DOUYIN_HOT_SOURCE", "").strip()
        or cfg.get("hot_source")
        or os.getenv("HOT_SOURCE", "official")
    ).lower()
    if source == "mock":
        raise RuntimeError("已禁用 mock 热榜数据：请设置 DOUYIN_HOT_SOURCE=official 或 DOUYIN_HOT_SOURCE=tophub_html")

    if source in ("tophub_html", "tophub"):
        return _fetch_from_tophub(cfg)
    if source in ("official", "platform", "douyin_official"):
        return _fetch_from_douyin_official(
            entry_url=cfg.get("douyin_hot_url") or os.getenv("DOUYIN_HOT_URL") or "",
            api_url=cfg.get("douyin_hot_api_url") or os.getenv("DOUYIN_HOT_API_URL") or "",
        )

    raise NotImplementedError(f"Unsupported HOT_SOURCE: {source}")


def _fetch_from_douyin_official(*, entry_url: str, api_url: str) -> list[HotItem]:
    """
    抖音官方热榜（Web）抓取。

    说明：
    - 当前使用的接口为 `aweme/v1/web/hot/search/list/`，返回 `data.word_list`。
    - 每条包含 `word/hot_value/position/sentence_id/event_time/...` 等字段。
    - 这里不做“内容加工”，尽量把官方字段完整塞进 raw，供后续聚合/展示使用。
    """

    api_url = (api_url or "").strip() or "https://www.douyin.com/aweme/v1/web/hot/search/list/"
    # 这些参数在多数情况下无需动态 token 也可拿到完整 word_list
    default_params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "detail_list": "1",
        "source": "6",
        "pc_client_type": "1",
    }
    url = api_url if "?" in api_url else f"{api_url}?{urlencode(default_params)}"
    data = get_json(
        url=url,
        headers={
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*",
        },
        cache_ttl_seconds=10,
        timeout=float(os.getenv("DOUYIN_HOT_TIMEOUT_SECONDS", "15") or 15),
        retries=int(os.getenv("DOUYIN_HOT_RETRIES", "3") or 3),
        backoff=float(os.getenv("DOUYIN_HOT_BACKOFF", "0.6") or 0.6),
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"抖音官方热榜接口返回异常: url={url!r}, body={data!r}")

    status_code = int(data.get("status_code") or 0)
    if status_code not in (0, 200):
        raise RuntimeError(
            f"抖音官方热榜接口 status_code 异常: url={url!r}, status_code={status_code}, body={data!r}"
        )

    word_list = []
    d = data.get("data")
    if isinstance(d, dict) and isinstance(d.get("word_list"), list):
        word_list = d["word_list"]
    if not word_list:
        raise RuntimeError(f"抖音官方热榜 word_list 为空: url={url!r}, entry={entry_url!r}")

    # 只取前 20 条即可（避免下游处理过重）
    limit = 20
    items: list[HotItem] = []
    for idx, it in enumerate(word_list, start=1):
        if not isinstance(it, dict):
            continue
        word = (it.get("word") or "").strip()
        if not word:
            continue
        rank = int(it.get("position") or idx)
        items.append(
            {
                "platform": "douyin",
                "rank": rank,
                "keyword": word,
                "raw": {
                    **it,
                    "_platform_rank": rank,
                    "_source": "douyin/aweme/v1/web/hot/search/list",
                    # 用户配置的入口页（例如 https://www.douyin.com/hot）。该页面本身是 JS 壳，
                    # 实际数据由本接口返回；这里保留入口信息便于排查/对齐需求。
                    "source_page": entry_url,
                },
            }
        )
        if len(items) >= limit:
            break
    # position 可能不连续，按 position 排序后再重排 rank 以保持展示一致
    items_sorted = sorted(items, key=lambda x: int(x.get("rank") or 0))
    out: list[HotItem] = []
    for i, it in enumerate(items_sorted, start=1):
        out.append({**it, "rank": i})
    return out


def _fetch_from_tophub(cfg: dict[str, Any]) -> list[HotItem]:
    url = _clean_url(
        cfg.get("douyin_hot_url") or os.getenv("DOUYIN_HOT_URL"),
        "https://tophub.today/n/DpQvNABoNE",
    )
    debug_log(f"fetch douyin hot (tophub): start url={url}", cfg=cfg, prefix="hot")
    html = get_text(
        url=url,
        cache_ttl_seconds=60,
        timeout=float(os.getenv("DOUYIN_HOT_TOPHUB_TIMEOUT_SECONDS", "25") or 25),
        retries=int(os.getenv("DOUYIN_HOT_TOPHUB_RETRIES", "4") or 4),
        backoff=float(os.getenv("DOUYIN_HOT_TOPHUB_BACKOFF", "0.8") or 0.8),
    )
    debug_log(f"fetch douyin hot (tophub): ok bytes={len(html)}", cfg=cfg, prefix="hot")
    return _parse_tophub_hot(html, platform="douyin")


def _parse_tophub_hot(html: str, *, platform: str) -> list[HotItem]:
    soup = BeautifulSoup(html, "lxml")
    items: list[HotItem] = []

    limit = 20
    for idx, a in enumerate(soup.select("a"), start=1):
        text = (a.get_text() or "").strip()
        if not text:
            continue
        if len(text) > 60:
            continue
        if text in ("更多", "查看", "详情"):
            continue
        if "http" in text or "/" in text:
            continue

        items.append({"platform": platform, "rank": idx, "keyword": text, "raw": {}})
        if len(items) >= limit:
            break

    seen: set[str] = set()
    deduped: list[HotItem] = []
    for it in items:
        k = it["keyword"]
        if k in seen:
            continue
        seen.add(k)
        deduped.append(it)
    return deduped
