from __future__ import annotations

import os
from typing import Any

from bs4 import BeautifulSoup

from core.state import HotItem
from tools.debug_log import debug_log
from tools.http import get_json, get_text


def _clean_url(value: Any, default: str) -> str:
    url = str(value or "").strip()
    return url or default


def fetch_weibo_hot(cfg: dict[str, Any]) -> list[HotItem]:
    """
    获取微博热搜列表。

    说明：
    - 本项目不允许“兜底/回退链路”在运行时悄悄切换数据源。
    - 你可以通过配置显式选择数据源；不支持的值会直接报错。
    """

    source = (
        cfg.get("weibo_hot_source")
        or os.getenv("WEIBO_HOT_SOURCE", "").strip()
        or cfg.get("hot_source")
        or os.getenv("HOT_SOURCE", "official")
    ).lower()
    if source == "mock":
        raise RuntimeError("已禁用 mock 热榜数据：请设置 WEIBO_HOT_SOURCE=tophub_html 或 WEIBO_HOT_SOURCE=official")

    if source in ("tophub_html", "tophub"):
        return _fetch_from_tophub(cfg)
    if source in ("official", "platform", "weibo_official"):
        return _fetch_from_weibo_official(cfg)

    raise NotImplementedError(f"Unsupported HOT_SOURCE: {source}")


def _fetch_from_weibo_official(cfg: dict[str, Any] | None = None) -> list[HotItem]:
    """
    微博官方热榜（Web）抓取。

    说明：
    - 该接口返回结构中包含 `band_list`，每一项包含 word/raw_hot/num/word_scheme 等字段。
    - 这里不做“内容加工”，尽量把官方字段完整塞进 raw，供后续聚合/展示使用。
    """

    headers = {
        "Referer": "https://weibo.com/",
        "Accept": "application/json, text/plain, */*",
    }

    # 避免单接口偶发失败导致“看起来像卡住”：同属微博官方 Web JSON 的两个端点都尝试。
    # 这不是切换外部数据源，仍然是微博官方数据。
    endpoints = [
        ("weibo/ajax/statuses/hot_band", "https://weibo.com/ajax/statuses/hot_band"),
        ("weibo/ajax/side/hotSearch", "https://weibo.com/ajax/side/hotSearch"),
    ]

    timeout = float(os.getenv("WEIBO_HOT_TIMEOUT_SECONDS", "15") or 15)
    retries = int(os.getenv("WEIBO_HOT_RETRIES", "3") or 3)
    backoff = float(os.getenv("WEIBO_HOT_BACKOFF", "0.6") or 0.6)

    last_err: Exception | None = None
    for source_id, url in endpoints:
        debug_log(f"fetch weibo hot: start url={url}", cfg=cfg, prefix="hot")
        try:
            data = get_json(
                url=url,
                headers=headers,
                cache_ttl_seconds=30,
                timeout=timeout,
                retries=retries,
                backoff=backoff,
            )
            if not isinstance(data, dict) or int(data.get("ok") or 0) != 1:
                raise RuntimeError(f"微博官方热榜接口返回异常: url={url!r}, body={data!r}")

            items = _parse_weibo_official_json(data, source_id=source_id)
            if not items:
                raise RuntimeError(f"微博官方热榜解析结果为空: url={url!r}")
            debug_log(f"fetch weibo hot: ok url={url} items={len(items)}", cfg=cfg, prefix="hot")
            return items
        except Exception as e:
            last_err = e
            debug_log(f"fetch weibo hot: fail url={url} err={e!r}", cfg=cfg, prefix="hot")
            continue

    raise RuntimeError(f"微博官方热榜抓取失败（已尝试 {len(endpoints)} 个官方端点）: {last_err!r}") from last_err


def _parse_weibo_official_json(data: dict[str, Any], *, source_id: str) -> list[HotItem]:
    limit = 20

    if source_id.endswith("hot_band"):
        band_list: list[Any] = []
        d = data.get("data")
        if isinstance(d, dict) and isinstance(d.get("band_list"), list):
            band_list = d["band_list"]

        items: list[HotItem] = []
        for idx, it in enumerate(band_list, start=1):
            if not isinstance(it, dict):
                continue
            word = (it.get("word") or "").strip()
            if not word:
                continue
            items.append(
                {
                    "platform": "weibo",
                    "rank": idx,
                    "keyword": word,
                    "raw": {**it, "_platform_rank": idx, "_source": source_id},
                }
            )
            if len(items) >= limit:
                break
        return items

    # ajax/side/hotSearch
    realtime: list[Any] = []
    d = data.get("data")
    if isinstance(d, dict) and isinstance(d.get("realtime"), list):
        realtime = d["realtime"]

    items: list[HotItem] = []
    for idx, it in enumerate(realtime, start=1):
        if not isinstance(it, dict):
            continue
        word = (it.get("word") or "").strip()
        if not word:
            continue
        rank = int(it.get("realpos") or idx)
        items.append(
            {
                "platform": "weibo",
                "rank": rank,
                "keyword": word,
                "raw": {**it, "_platform_rank": rank, "_source": source_id},
            }
        )
        if len(items) >= limit:
            break

    # realpos 可能不连续，排序后重排 rank
    items_sorted = sorted(items, key=lambda x: int(x.get("rank") or 0))
    out: list[HotItem] = []
    for i, it in enumerate(items_sorted, start=1):
        out.append({**it, "rank": i})
    return out


def _fetch_from_tophub(cfg: dict[str, Any]) -> list[HotItem]:
    url = _clean_url(
        cfg.get("weibo_hot_url") or os.getenv("WEIBO_HOT_URL"),
        "https://tophub.today/n/KqndgxeLl9",
    )
    headers = {
        "Referer": "https://tophub.today/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    # 榜单类页面建议短缓存，尽量接近实时
    debug_log(f"fetch weibo hot (tophub): start url={url}", cfg=cfg, prefix="hot")
    html = get_text(
        url=url,
        headers=headers,
        cache_ttl_seconds=20,
        timeout=float(os.getenv("WEIBO_HOT_TOPHUB_TIMEOUT_SECONDS", "25") or 25),
        retries=int(os.getenv("WEIBO_HOT_TOPHUB_RETRIES", "4") or 4),
        backoff=float(os.getenv("WEIBO_HOT_TOPHUB_BACKOFF", "0.8") or 0.8),
    )
    debug_log(f"fetch weibo hot (tophub): ok bytes={len(html)}", cfg=cfg, prefix="hot")
    return _parse_tophub_hot(html, platform="weibo", page_url=url)


def _parse_tophub_hot(html: str, *, platform: str, page_url: str) -> list[HotItem]:
    """
    解析 tophub.today 的榜单页面（结构可能会变，这里做尽量宽松的提取）。
    """

    soup = BeautifulSoup(html, "lxml")
    items: list[HotItem] = []

    # 优先解析榜单 table（更稳定，避免把页面上的“导航/按钮”等 a 标签当成热词）
    limit = 20
    for tr in soup.select("table tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        rank_text = (tds[0].get_text() or "").strip()
        title_td = tds[1]
        title = (title_td.get_text(" ", strip=True) or "").strip()
        if not title:
            continue
        # rank 通常形如 "1." / "2."
        try:
            rank = int(rank_text.strip().rstrip("."))
        except Exception:
            rank = len(items) + 1

        hot_text = ""
        if len(tds) >= 3:
            hot_text = (tds[2].get_text(" ", strip=True) or "").strip()

        a = title_td.select_one("a")
        href = (a.get("href") or "").strip() if a else ""

        items.append(
            {
                "platform": platform,
                "rank": rank,
                "keyword": title,
                "raw": {
                    "hot": hot_text,
                    "href": href,
                    "source_page": page_url,
                    "_source": "tophub.today",
                },
            }
        )
        if len(items) >= limit:
            break

    if not items:
        snippet = (soup.get_text(" ", strip=True) or "")[:200]
        raise RuntimeError(f"tophub 解析结果为空: url={page_url!r}, snippet={snippet!r}")

    # 去重：保留首次出现
    seen: set[str] = set()
    deduped: list[HotItem] = []
    for it in items:
        k = it["keyword"]
        if k in seen:
            continue
        seen.add(k)
        deduped.append(it)
    return deduped
