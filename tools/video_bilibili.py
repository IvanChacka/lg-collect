from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from tools.http import get_json, get_text


def bilibili_search(*, query: str, cfg: dict[str, Any], limit: int = 8) -> list[dict]:
    """
    B 站站内搜索（优先 API，必要时回退 HTML）。

    设计目标：最大化“可用性”（减少页面结构变化/握手不稳定带来的失败）。
    - 优先使用 B 站 JSON 搜索接口（更稳定、解析更可靠）
    - 如果接口失败，再尝试 HTML 抓取（同一数据源内的备选实现，并非切换到其他来源）
    - 两种方式都失败时，抛出包含两段错误信息的异常（不静默兜底）
    """

    cookie = str(cfg.get("bilibili_cookie") or "").strip()
    headers: dict[str, str] = {
        "Referer": "https://search.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie:
        headers["Cookie"] = cookie

    api_err: Exception | None = None
    try:
        rows = _bilibili_search_api(query=query, headers=headers, limit=limit)
        if rows:
            return rows
    except Exception as e:
        api_err = e

    html_err: Exception | None = None
    try:
        rows = _bilibili_search_html(query=query, headers=headers, limit=limit)
        if rows:
            return rows
        return rows
    except Exception as e:
        html_err = e

    raise RuntimeError(
        "bilibili_search failed: "
        + f"api_err={api_err!r}; "
        + f"html_err={html_err!r}"
    )


def _bilibili_search_api(*, query: str, headers: dict[str, str], limit: int) -> list[dict]:
    """
    使用 B 站 JSON 搜索接口抓取。
    """

    results: list[dict] = []
    seen: set[str] = set()

    page_size = 30
    max_pages = max(1, (limit + page_size - 1) // page_size)
    for page in range(1, max_pages + 1):
        url = (
            "https://api.bilibili.com/x/web-interface/search/type?"
            + f"search_type=video&keyword={_urlencode(query)}&page={page}&page_size={page_size}"
        )
        data = get_json(url=url, headers=headers, cache_ttl_seconds=300, timeout=15, retries=4)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected api response type: {type(data)}")
        code = int(data.get("code") or 0)
        if code != 0:
            raise RuntimeError(f"api code!=0: {data!r}")
        payload = data.get("data") or {}
        items = payload.get("result") or []
        if not isinstance(items, list):
            raise RuntimeError(f"unexpected api result: {type(items)}")
        if not items:
            break

        for it in items:
            if not isinstance(it, dict):
                continue
            title_html = str(it.get("title") or "").strip()
            title = _strip_html(title_html)
            bvid = str(it.get("bvid") or "").strip()
            url2 = str(it.get("arcurl") or "").strip()
            if not url2 and bvid:
                url2 = f"https://www.bilibili.com/video/{bvid}"
            if not title or not url2:
                continue
            if url2 in seen:
                continue
            seen.add(url2)
            desc = str(it.get("description") or "").strip()
            results.append({"title": title, "url": url2, "snippet": desc, "engine": "bilibili"})
            if len(results) >= limit:
                return results
    return results


def _bilibili_search_html(*, query: str, headers: dict[str, str], limit: int) -> list[dict]:
    """
    HTML 抓取版（作为 API 的同源备选实现）。
    """

    results: list[dict] = []
    seen: set[str] = set()

    max_pages = max(1, (limit + 29) // 30)
    for page in range(1, max_pages + 1):
        url = (
            "https://search.bilibili.com/all?keyword=" + _urlencode(query) + f"&page={page}"
        )
        html = get_text(url=url, headers=headers, cache_ttl_seconds=300, timeout=15, retries=4)
        soup = BeautifulSoup(html, "lxml")

        page_items = 0
        for item in soup.select(".bili-video-card"):
            a = item.select_one("a")
            if not a:
                continue
            href = (a.get("href") or "").strip()
            if href.startswith("//"):
                href = "https:" + href
            title_el = item.select_one(".bili-video-card__info--tit") or item.select_one("h3")
            title = (title_el.get_text() or "").strip() if title_el else ""
            if not href or not title:
                continue
            if href in seen:
                continue
            seen.add(href)
            page_items += 1
            results.append({"title": title, "url": href, "snippet": "", "engine": "bilibili"})
            if len(results) >= limit:
                return results

        if page_items == 0:
            break
    return results


def _urlencode(text: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(text)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        return BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:
        return html.replace("<em", " ").replace("</em>", " ").strip()
