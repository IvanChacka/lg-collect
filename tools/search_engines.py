from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tools.debug_log import debug_log

BING_WEB_SEARCH_URL = "https://cn.bing.com/search"
_BING_RESULT_SELECTOR = "#b_results > li.b_algo, #b_results li.b_algo, li.b_algo"
_BING_PARSER_VERSION = "2026-04-30-textcontent"
SOGOU_WEB_SEARCH_URL = "https://wap.sogou.com/web/searchList.jsp"
_SOGOU_PARSER_VERSION = "2026-06-05-wap"
_SOGOU_RESULT_SELECTOR = "div.vrResult"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SOGOU_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.6 Mobile/15E148 Safari/604.1"
)


def _sogou_looks_like_captcha(html: str) -> bool:
    """检测 Sogou 是否返回验证码/风控页（模块级，供 HTTP 和 Playwright 模式共用）。"""
    s = str(html or "")
    if not s:
        return False
    hints = [
        "seccodeInput",
        "请输入验证码",
        "安全验证",
        "验证后即可继续访问",
        "antispider",
    ]
    return any(h in s for h in hints)


def _sogou_page_url(*, query: str, page: int = 1) -> str:
    """Sogou WAP 搜索 URL。page=1 为第一页。"""
    p = int(page or 1)
    if p < 1:
        p = 1
    base = f"{SOGOU_WEB_SEARCH_URL}?keyword={quote_plus(query)}"
    if p > 1:
        base = f"{base}&page={p}"
    return base


def _extract_real_url_from_sogou_redirect(href: str) -> str:
    """从 Sogou WAP 重定向链接中提取真实目标 URL。

    WAP 版的 href 格式：
      ./id=<uuid>/keyword=.../sec=.../tc?clk=N&wml=1&url=<URL-encoded-real-url>&dp=1&...
    真实 URL 在 url= 查询参数中。
    """
    href = str(href or "").strip()
    if not href:
        return ""
    # 尝试从查询参数中提取 url=
    for separator in ("&url=", "?url="):
        if separator in href:
            try:
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                urls = qs.get("url", [])
                if urls:
                    return unquote(urls[0])
            except Exception:
                pass
    return ""


def search_web(
    *, query: str, cfg: dict[str, Any], limit: int = 8, engine: str = "bing_web", extract_snippet: bool = True
) -> list[dict]:
    """
    网页搜索统一入口。

    注意：不做任何"静默兜底/自动切源"。调用方必须显式指定 engine，
    或使用默认值（bing_web）。
    """

    name = str(engine or "bing_web").strip().lower()
    if name in ("bing", "bing_web"):
        return bing_web_search(query=query, cfg=cfg, limit=limit, extract_snippet=extract_snippet)
    if name in ("sogou", "sogou_web"):
        return sogou_web_search(query=query, cfg=cfg, limit=limit, extract_snippet=extract_snippet)
    raise ValueError(f"unknown search engine: {engine!r}")


def sogou_web_search(
    *, query: str, cfg: dict[str, Any] | None = None, limit: int = 8, extract_snippet: bool = True
) -> list[dict]:
    """
    Sogou 网页搜索 — 使用 WAP 版（https://wap.sogou.com）规避桌面版强风控。

    支持两种抓取模式（通过配置 SOGOU_FETCH_MODE 或 SEARCH_FETCH_MODE 控制）：
    - http（默认，推荐）：HTTP 抓取 + HTML 解析，WAP 版无风控
    - playwright：无头浏览器（仅作为兜底，WAP 版一般不需要）
    """

    cfg = cfg or {}
    requested = int(limit or 0)
    if requested <= 0:
        raise ValueError(f"sogou_web_search limit must be a positive integer, got: {limit!r}")

    fetch_mode = str(cfg.get("sogou_fetch_mode") or cfg.get("search_fetch_mode") or "http").strip().lower()

    debug_log(
        f"sogou_web_search parser={_SOGOU_PARSER_VERSION} module={__file__!r}"
        f" query={query!r} limit={requested} fetch_mode={fetch_mode!r}",
        cfg=cfg,
        prefix="search",
    )

    if fetch_mode not in ("http", "playwright"):
        raise ValueError(f"SOGOU_FETCH_MODE must be 'http' or 'playwright', got: {fetch_mode!r}")

    if fetch_mode == "playwright":
        return _sogou_web_search_playwright(
            query=query, cfg=cfg, limit=requested, extract_snippet=extract_snippet
        )
    return _sogou_web_search_http(
        query=query, cfg=cfg, limit=requested, extract_snippet=extract_snippet
    )


def _sogou_web_search_http(
    *, query: str, cfg: dict[str, Any], limit: int, extract_snippet: bool
) -> list[dict]:
    """Sogou WAP 网页搜索 — HTTP 模式。"""

    requested = limit
    max_attempts = int(cfg.get("search_max_attempts") or 3)
    backoff = float(cfg.get("search_retry_backoff_seconds") or 1.5)
    crawl_proxy = str(cfg.get("crawl_proxy") or "").strip()
    debug_dump = str(cfg.get("search_debug_dump") or "0").strip() == "1"
    debug_dump_mode = str(cfg.get("search_debug_dump_mode") or "on_failure").strip().lower()

    page_size = min(10, requested)
    max_pages = (requested + page_size - 1) // page_size
    max_pages = min(max_pages, 5)

    def _http_client() -> httpx.Client:
        timeout = float(cfg.get("search_timeout_seconds") or 30)
        headers = {
            "User-Agent": _SOGOU_MOBILE_USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Referer": "https://wap.sogou.com/",
        }

        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": timeout,
            "headers": headers,
        }
        if crawl_proxy:
            kwargs["proxies"] = crawl_proxy
            try:
                return httpx.Client(**kwargs)
            except TypeError as e:
                msg = str(e)
                if "proxies" in msg and "unexpected" in msg:
                    kwargs.pop("proxies", None)
                    kwargs["proxy"] = crawl_proxy
                    return httpx.Client(**kwargs)
                raise
        return httpx.Client(**kwargs)

    def _parse_rows(html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html or "", "html.parser")
        title_text = ""
        try:
            title_text = str(soup.title.get_text(" ", strip=True) if soup.title else "").strip()
        except Exception:
            title_text = ""
        if _sogou_looks_like_captcha(html) or ("安全" in title_text and "验证" in title_text):
            raise RuntimeError(f"Sogou 命中验证码/风控页: title={title_text!r}")

        out: list[dict[str, Any]] = []
        for blk in soup.select(_SOGOU_RESULT_SELECTOR):
            h3 = blk.select_one("h3.vr-tit")
            a = h3.select_one("a.resultLink") if h3 else None
            href = str(a.get("href") or "").strip() if a else ""
            title = a.get_text(" ", strip=True) if a else ""
            title = re.sub(r"\s+", " ", str(title or "")).strip()
            if not title or not href:
                continue

            # 优先从重定向链接中提取真实 URL
            url = _extract_real_url_from_sogou_redirect(href)
            if not url:
                url = urljoin("https://wap.sogou.com/web/", href)

            snippet = ""
            if extract_snippet:
                # 从 vrResult 文本中提取摘要（去掉标题前缀）
                full_text = blk.get_text(" ", strip=True)
                full_text = re.sub(r"\s+", " ", full_text).strip()
                if full_text.startswith(title):
                    full_text = full_text[len(title):].strip()
                snippet = full_text[:300]

            source = ""
            # 来源通常以 " - 来源名 时间" 的形式出现在文本中
            source_match = re.search(
                r"[—\-]\s*(\S+?)(?:\s+\d+[天前小时分钟秒]|\s+\d{4}[./-]\d{1,2}[./-]\d{1,2}|$)",
                snippet,
            )
            if source_match:
                source = source_match.group(1).strip()
                # 清理常见噪声
                if len(source) > 30 or not source:
                    source = ""

            out.append({"title": title, "url": url, "snippet": snippet, "source": source})
        return out

    def row_key(row: dict[str, Any]) -> str:
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip().lower()
        return url or title

    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_errors: list[str] = []
    last_page_url: str | None = None
    last_page_html: str | None = None

    with _http_client() as client:
        for page_idx in range(max_pages):
            if len(all_rows) >= requested:
                break
            page_url = _sogou_page_url(query=query, page=page_idx + 1)
            last_page_url = page_url

            last_error: Exception | None = None
            html: str | None = None
            rows: list[dict[str, Any]] = []
            for attempt in range(1, max_attempts + 1):
                try:
                    r = client.get(page_url)
                    r.raise_for_status()
                    html = r.text
                    last_page_html = html
                    rows = _parse_rows(html)
                    debug_log(
                        "sogou_web_search page"
                        f" mode='http' attempt={attempt}/{max_attempts} page={page_idx+1}/{max_pages}"
                        f" page_url={str(r.url)!r} rows={len(rows or [])}",
                        cfg=cfg,
                        prefix="search",
                    )
                    if rows:
                        break
                    last_error = RuntimeError(f"Sogou 解析为空: query={query!r}, page={page_idx+1}")
                except Exception as e:
                    last_error = e
                if attempt < max_attempts:
                    time.sleep(backoff * attempt)

            if debug_dump and debug_dump_mode == "always":
                _dump_sogou_debug(query=query, page_url=page_url, html=html, cfg=cfg)

            if not rows:
                if debug_dump and debug_dump_mode != "always":
                    _dump_sogou_debug(query=query, page_url=page_url, html=html, cfg=cfg)
                if last_error is not None:
                    page_errors.append(str(last_error))
                break

            added_this_page = 0
            for row in rows:
                k = row_key(row)
                if not k or k in seen:
                    continue
                seen.add(k)
                all_rows.append(
                    {
                        "title": str(row.get("title") or "").strip(),
                        "url": str(row.get("url") or "").strip(),
                        "snippet": str(row.get("snippet") or "").strip(),
                        "source": str(row.get("source") or "").strip(),
                        "engine": "sogou_web",
                    }
                )
                added_this_page += 1
                if len(all_rows) >= requested:
                    break

            if added_this_page == 0:
                break

    parsed = _dedupe_results(all_rows)
    if not parsed:
        err_hint = f", errors={page_errors[:1]!r}" if page_errors else ""
        url_hint = f", page_url={str(last_page_url or '').strip()!r}" if last_page_url else ""
        raise RuntimeError(f"Sogou 网页搜索未返回任何结果: query={query!r}{url_hint}{err_hint}")
    return parsed[:requested]


def _sogou_web_search_playwright(
    *, query: str, cfg: dict[str, Any], limit: int, extract_snippet: bool
) -> list[dict]:
    """Sogou WAP 网页搜索 — Playwright 模式（无头浏览器兜底）。"""

    requested = limit
    max_attempts = int(cfg.get("search_max_attempts") or 3)
    backoff = float(cfg.get("search_retry_backoff_seconds") or 1.5)
    crawl_proxy = str(cfg.get("crawl_proxy") or "").strip()
    debug_dump = str(cfg.get("search_debug_dump") or "0").strip() == "1"
    debug_dump_mode = str(cfg.get("search_debug_dump_mode") or "on_failure").strip().lower()
    debug_dump_screenshot = str(cfg.get("search_debug_dump_screenshot") or "0").strip() == "1"

    page_size = min(10, requested)
    max_pages = (requested + page_size - 1) // page_size
    max_pages = min(max_pages, 5)

    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_errors: list[str] = []
    last_page_url: str | None = None
    last_page_html: str | None = None

    def _extract_rows(page) -> list[dict]:
        return page.locator(_SOGOU_RESULT_SELECTOR).evaluate_all(
            """
            els => els.map(el => {
                const h3 = el.querySelector('h3.vr-tit');
                const a = h3 ? h3.querySelector('a.resultLink') : null;
                const title = a ? (a.textContent || '').trim() : '';
                const href = a ? (a.href || '').trim() : '';

                // Extract real URL from Sogou redirect
                let url = '';
                const urlMatch = href.match(/[?&]url=([^&]+)/);
                if (urlMatch) {
                    try { url = decodeURIComponent(urlMatch[1]); } catch(e) {}
                }
                if (!url) url = href;

                let snippet = '';
                if (el.textContent) {
                    snippet = el.textContent.replace(/\\s+/g, ' ').trim();
                    if (snippet.startsWith(title)) {
                        snippet = snippet.slice(title.length).trim();
                    }
                    snippet = snippet.slice(0, 300);
                }

                let source = '';
                const sourceMatch = (snippet || '').match(/[—\\-]\\s*(\\S+?)(?:\\s+\\d+[天前小时分钟秒]|\\s+\\d{4}[./-]\\d{1,2}[./-]\\d{1,2}|$)/);
                if (sourceMatch) {
                    source = sourceMatch[1].trim();
                    if (source.length > 30) source = '';
                }

                return {title, url, snippet, source};
            }).filter(x => x.title && x.url)
            """
        )

    with sync_playwright() as p:
        launch_kwargs: dict[str, Any] = {"headless": True}
        if crawl_proxy:
            launch_kwargs["proxy"] = {"server": crawl_proxy}
        browser = p.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent=_SOGOU_MOBILE_USER_AGENT,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = context.new_page()
            page.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"})

            for page_idx in range(max_pages):
                if len(all_rows) >= requested:
                    break

                page_url = _sogou_page_url(query=query, page=page_idx + 1)
                last_page_url = page_url

                last_error: Exception | None = None
                rows: list[dict] = []
                for attempt in range(1, max_attempts + 1):
                    try:
                        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=12000)
                        except Exception:
                            pass

                        try:
                            page.locator(_SOGOU_RESULT_SELECTOR).first.wait_for(
                                state="attached", timeout=25000
                            )
                        except Exception:
                            page.wait_for_timeout(3000)

                        html = page.content()
                        last_page_html = html
                        title_text = ""
                        try:
                            title_text = str(page.title() or "").strip()
                        except Exception:
                            pass
                        if _sogou_looks_like_captcha(html) or ("安全" in title_text and "验证" in title_text):
                            raise RuntimeError(f"Sogou 命中验证码/风控页: title={title_text!r}")

                        rows = _extract_rows(page)

                        debug_log(
                            "sogou_web_search page"
                            f" mode='playwright' attempt={attempt}/{max_attempts}"
                            f" page={page_idx+1}/{max_pages}"
                            f" page_url={(page.url or page_url)!r}"
                            f" rows={len(rows or [])}",
                            cfg=cfg,
                            prefix="search",
                        )

                        if rows:
                            break
                        last_error = RuntimeError(
                            f"Sogou Playwright 解析为空: query={query!r}, page={page_idx+1}"
                        )
                    except PlaywrightTimeoutError:
                        last_error = RuntimeError(
                            f"Sogou 网页搜索超时: query={query!r}, attempt={attempt}/{max_attempts}, page={page_idx+1}"
                        )
                    except Exception as e:
                        last_error = e
                    if attempt < max_attempts:
                        try:
                            page.wait_for_timeout(int(backoff * attempt * 1000))
                        except Exception:
                            pass

                if debug_dump and debug_dump_mode == "always":
                    try:
                        html = page.content()
                    except Exception:
                        html = None
                    _dump_sogou_debug(query=query, page_url=last_page_url or page_url, html=html, cfg=cfg)
                    if debug_dump_screenshot:
                        try:
                            base_dir = str(cfg.get("search_debug_dir") or ".data/search_debug").strip()
                            os.makedirs(base_dir, exist_ok=True)
                            stamp = time.strftime("%Y%m%d_%H%M%S")
                            safe = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", str(query or "")).strip("_")[:80]
                            page.screenshot(
                                path=os.path.join(base_dir, f"{stamp}__sogou_playwright__{safe}.png"),
                                full_page=True,
                            )
                        except Exception:
                            pass

                if not rows:
                    if debug_dump and debug_dump_mode != "always":
                        try:
                            last_page_html = page.content()
                        except Exception:
                            last_page_html = None
                        _dump_sogou_debug(
                            query=query, page_url=last_page_url or page_url, html=last_page_html, cfg=cfg
                        )
                        if debug_dump_screenshot:
                            try:
                                base_dir = str(cfg.get("search_debug_dir") or ".data/search_debug").strip()
                                os.makedirs(base_dir, exist_ok=True)
                                stamp = time.strftime("%Y%m%d_%H%M%S")
                                safe = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", str(query or "")).strip("_")[:80]
                                page.screenshot(
                                    path=os.path.join(base_dir, f"{stamp}__sogou_playwright__{safe}.png"),
                                    full_page=True,
                                )
                            except Exception:
                                pass
                    if last_error is not None:
                        page_errors.append(str(last_error))
                    break

                added_this_page = 0
                for row in rows:
                    url = str(row.get("url") or "").strip()
                    title = str(row.get("title") or "").strip().lower()
                    k = url or title
                    if not k or k in seen:
                        continue
                    seen.add(k)
                    all_rows.append(
                        {
                            "title": str(row.get("title") or "").strip(),
                            "url": url,
                            "snippet": str(row.get("snippet") or "").strip() if extract_snippet else "",
                            "source": str(row.get("source") or "").strip(),
                            "engine": "sogou_web",
                        }
                    )
                    added_this_page += 1
                    if len(all_rows) >= requested:
                        break

                if added_this_page == 0:
                    break
        finally:
            browser.close()

    parsed = _dedupe_results(all_rows)
    if not parsed:
        err_hint = f", errors={page_errors[:1]!r}" if page_errors else ""
        url_hint = f", page_url={str(last_page_url or '').strip()!r}" if last_page_url else ""
        raise RuntimeError(f"Sogou 网页搜索未返回任何结果: query={query!r}{url_hint}{err_hint}")
    return parsed[:requested]


def _dump_sogou_debug(*, query: str, page_url: str | None, html: str | None, cfg: dict[str, Any]) -> None:
    """
    仅用于排查 Sogou 搜索偶发风控/验证码/解析失败。
    不做任何兜底切换数据源；只把抓到的页面信息落盘以便人工排查。
    """

    base_dir = str(cfg.get("search_debug_dir") or ".data/search_debug").strip() or ".data/search_debug"
    thread_id = str(cfg.get("thread_id") or "").strip()
    run_date = str(cfg.get("run_date") or "").strip()
    stamp = time.strftime("%Y%m%d_%H%M%S")

    safe = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", str(query or "")).strip("_")
    safe = safe[:80] if safe else "query"
    parts = [stamp, "sogou_wap"]
    if run_date:
        parts.append(run_date)
    if thread_id:
        parts.append(thread_id)
    name = "__".join(parts) + f"__{safe}"

    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as e:
        debug_log(
            f"search_debug_dump skipped: cannot create dir {base_dir!r}: {e!r}",
            cfg=cfg,
            prefix="search",
        )
        return

    meta_path = os.path.join(base_dir, f"{name}.meta.txt")
    html_path = os.path.join(base_dir, f"{name}.html")
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"engine='sogou_web'\n")
            f.write(f"query={query!r}\n")
            if page_url:
                f.write(f"page_url={page_url!r}\n")
    except Exception:
        pass

    if html:
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass


def bing_web_search(
    *, query: str, cfg: dict[str, Any] | None = None, limit: int = 8, extract_snippet: bool = True
) -> list[dict]:
    cfg = cfg or {}

    requested = int(limit or 0)
    if requested <= 0:
        raise ValueError(f"bing_web_search limit must be a positive integer, got: {limit!r}")

    # 用于排查"CLI 正常但 Studio/可视化界面仍旧解析异常"的问题：
    # 打印当前加载的模块路径与解析器版本，方便判断是否仍在跑旧代码/旧容器。
    debug_log(
        f"bing_web_search parser={_BING_PARSER_VERSION} module={__file__!r} query={query!r} limit={requested}",
        cfg=cfg,
        prefix="search",
    )

    max_attempts = int(cfg.get("search_max_attempts") or 3)
    backoff = float(cfg.get("search_retry_backoff_seconds") or 1.5)
    debug_dump = str(cfg.get("search_debug_dump") or "0").strip() == "1"
    debug_dump_mode = str(cfg.get("search_debug_dump_mode") or "on_failure").strip().lower()
    debug_dump_screenshot = str(cfg.get("search_debug_dump_screenshot") or "0").strip() == "1"
    crawl_proxy = str(cfg.get("crawl_proxy") or "").strip()
    fetch_mode = str(cfg.get("search_fetch_mode") or "http").strip().lower()

    # Bing 默认每页约 10 条结果；当 requested>10 时，按页翻页抓取直到满足 requested 或没有更多结果。
    page_size = min(10, requested)
    max_pages = (requested + page_size - 1) // page_size
    # 防止误配导致无限翻页
    max_pages = min(max_pages, 5)

    all_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    page_errors: list[str] = []
    last_page_title: str | None = None
    last_page_url: str | None = None
    last_page_html: str | None = None

    def _host_from_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        try:
            host = urlparse(raw).netloc.strip().lower().strip(".")
        except Exception:
            return ""
        if host.startswith("www."):
            host = host[4:]
        return host

    def _host_from_source(source: str) -> str:
        """
        Bing 的 cite 文本有时不是纯域名（可能包含面包屑/路径），这里尽量抽取域名。
        例如：
        - "zhihu.com"
        - "zhihu.com › question"
        - "www.zhihu.com"
        """

        raw = re.sub(r"\s+", " ", str(source or "")).strip().lower()
        if not raw:
            return ""
        # 常见分隔符：› / > / | / -
        raw = re.split(r"[›>|\\-|｜|/]", raw, maxsplit=1)[0].strip()
        m = re.search(r"([a-z0-9-]+(?:\\.[a-z0-9-]+)+)", raw)
        if not m:
            return ""
        host = m.group(1).strip(".")
        if host.startswith("www."):
            host = host[4:]
        return host

    def row_key(row: dict[str, Any]) -> str:
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip().lower()
        return url or title

    def _http_client() -> httpx.Client:
        timeout = float(cfg.get("search_timeout_seconds") or 30)
        headers = {
            "User-Agent": _DEFAULT_USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }

        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": timeout,
            "headers": headers,
        }

        # httpx 在不同版本中对代理参数兼容性略有差异（proxy vs proxies）。
        # 这里做一次兼容，避免因为参数名差异导致搜索链路直接失败。
        if crawl_proxy:
            kwargs["proxies"] = crawl_proxy
            try:
                return httpx.Client(**kwargs)
            except TypeError as e:
                msg = str(e)
                if "proxies" in msg and "unexpected" in msg:
                    kwargs.pop("proxies", None)
                    kwargs["proxy"] = crawl_proxy
                    return httpx.Client(**kwargs)
                raise

        return httpx.Client(**kwargs)

    def _parse_rows_from_html(html: str) -> list[dict[str, Any]]:
        """
        用静态 HTML 解析 Bing 结果。相比 headless 浏览器，更接近手动访问的排序与内容，
        且避免部分情况下 headless 被风控/喂"低质量结果"。
        """

        soup = BeautifulSoup(html or "", "lxml")
        out: list[dict[str, Any]] = []
        container = soup.select_one("#b_results") or soup
        for li in container.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a:
                continue
            url = str(a.get("href") or "").strip()
            title = a.get_text(" ", strip=True)
            if not url or not title:
                continue
            snippet = ""
            if extract_snippet:
                cap = li.select_one(".b_caption p") or li.select_one(".b_snippet") or li.select_one("p")
                snippet = cap.get_text(" ", strip=True) if cap else ""
            cite = li.select_one("cite")
            source = cite.get_text(" ", strip=True) if cite else ""
            out.append({"title": title, "url": url, "snippet": snippet, "source": source})
        return out

    def _bing_web_search_http() -> list[dict[str, Any]]:
        nonlocal last_page_title, last_page_url, last_page_html
        all_rows_http: list[dict[str, Any]] = []
        seen_keys_http: set[str] = set()

        with _http_client() as client:
            for page_idx in range(max_pages):
                if len(all_rows_http) >= requested:
                    break
                first = page_idx * page_size + 1
                page_url = _bing_web_page_url(query, first=first, count=page_size)

                last_error: Exception | None = None
                html: str | None = None
                rows: list[dict[str, Any]] = []
                for attempt in range(1, max_attempts + 1):
                    try:
                        r = client.get(page_url)
                        r.raise_for_status()
                        html = r.text
                        last_page_html = html
                        last_page_url = str(r.url)
                        last_page_title = None
                        rows = _parse_rows_from_html(html)
                        debug_log(
                            "bing_web_search page"
                            f" mode='http' attempt={attempt}/{max_attempts}"
                            f" page={page_idx+1}/{max_pages} page_url={last_page_url!r}"
                            f" rows={len(rows or [])}",
                            cfg=cfg,
                            prefix="search",
                        )
                        if rows:
                            break
                        last_error = RuntimeError(f"Bing HTTP 解析为空: query={query!r}, page={page_idx+1}")
                    except Exception as e:
                        last_error = e
                    if attempt < max_attempts:
                        time.sleep(backoff * attempt)

                if debug_dump and debug_dump_mode == "always":
                    info = _dump_bing_debug(
                        query=query,
                        page_url=last_page_url or page_url,
                        page_title=last_page_title,
                        html=html,
                        cfg=cfg,
                    )
                    if info and debug_dump_screenshot:
                        # HTTP 模式无浏览器截图；保持接口一致但不写 png。
                        pass

                if not rows:
                    if debug_dump and debug_dump_mode != "always":
                        _dump_bing_debug(
                            query=query,
                            page_url=last_page_url or page_url,
                            page_title=last_page_title,
                            html=html,
                            cfg=cfg,
                        )
                    if last_error is not None:
                        page_errors.append(str(last_error))
                    break

                for row in rows:
                    k = row_key(row)
                    if not k or k in seen_keys_http:
                        continue
                    seen_keys_http.add(k)
                    all_rows_http.append(
                        {
                            "title": str(row.get("title") or "").strip(),
                            "url": str(row.get("url") or "").strip(),
                            "snippet": str(row.get("snippet") or "").strip(),
                            "source": str(row.get("source") or "").strip(),
                            "engine": "bing_web",
                        }
                    )
                    if len(all_rows_http) >= requested:
                        break

        return all_rows_http

    def fetch_one_page(*, page, page_url: str, attempt: int) -> list[dict]:
        def extract_rows() -> list[dict]:
            return page.locator(_BING_RESULT_SELECTOR).evaluate_all(
                """
                els => els.map(el => {
                  const a = el.querySelector('h2 a');
                  const caption = el.querySelector('.b_caption p, .b_lineclamp2, .b_snippet, p');
                  const cite = el.querySelector('cite, .tptt');
                  return {
                    // 注意：Bing CN 页在首屏经常给 #b_content 设置 `visibility:hidden`，
                    // 这会导致 `innerText` 为空（即使 DOM 已有结果项），从而解析不到任何结果。
                    // 用 `textContent` 可以绕开"是否可见"的影响，稳定获取文本。
                    title: (a?.textContent || '').trim(),
                    url: (a?.href || '').trim(),
                    snippet: (caption?.textContent || '').trim(),
                    source: (cite?.textContent || '').trim()
                  };
                }).filter(x => x.title && x.url)
                """
            )

        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass

        try:
            page.locator(_BING_RESULT_SELECTOR).first.wait_for(state="attached", timeout=25000)
        except Exception:
            page.wait_for_timeout(3000)

        nonlocal last_page_title, last_page_url
        last_page_title = page.title()
        last_page_url = page.url
        rows = extract_rows()

        # 有时 #b_results 已出现但首屏仍没渲染出 .b_algo，多等一会再取一次。
        if not rows:
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            rows = extract_rows()

        # 轻量重试：Bing 有时首屏没渲染出结果（或被插屏打断），重载一次再取。
        if not rows:
            page.goto(page_url, wait_until="load", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            try:
                page.locator(_BING_RESULT_SELECTOR).first.wait_for(state="attached", timeout=25000)
            except Exception:
                page.wait_for_timeout(4000)
            last_page_title = page.title()
            last_page_url = page.url
            rows = extract_rows()
            if not rows:
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
                rows = extract_rows()
        return rows

    if fetch_mode not in ("http", "playwright"):
        raise ValueError(f"SEARCH_FETCH_MODE must be 'http' or 'playwright', got: {fetch_mode!r}")

    if fetch_mode == "http":
        all_rows = _bing_web_search_http()
        parsed = _dedupe_results(all_rows)
        if not parsed:
            err_hint = f", errors={page_errors[:1]!r}" if page_errors else ""
            raise RuntimeError(f"Bing 网页搜索未返回任何结果: query={query!r}{err_hint}")
        return parsed[:requested]

    with sync_playwright() as p:
        launch_kwargs: dict[str, Any] = {"headless": True}
        if crawl_proxy:
            launch_kwargs["proxy"] = {"server": crawl_proxy}
        browser = p.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 2200},
                user_agent=_DEFAULT_USER_AGENT,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = context.new_page()
            page.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"})

            next_page_url: str | None = _bing_web_page_url(query, first=1, count=page_size)
            for page_idx in range(max_pages):
                if not next_page_url:
                    break

                page_url = next_page_url
                last_error: Exception | None = None
                rows: list[dict] = []
                for attempt in range(1, max_attempts + 1):
                    try:
                        rows = fetch_one_page(page=page, page_url=page_url, attempt=attempt)
                        debug_log(
                            "bing_web_search page"
                            f" attempt={attempt}/{max_attempts} page={page_idx+1}/{max_pages}"
                            f" page_url={(last_page_url or page_url)!r}"
                            f" page_title={str(last_page_title or '').strip()!r}"
                            f" rows={len(rows or [])}",
                            cfg=cfg,
                            prefix="search",
                        )
                        if debug_dump and debug_dump_mode == "always":
                            try:
                                html = page.content()
                            except Exception:
                                html = None
                            info = _dump_bing_debug(
                                query=query,
                                page_url=last_page_url or page_url,
                                page_title=last_page_title,
                                html=html,
                                cfg=cfg,
                            )
                            if info and debug_dump_screenshot:
                                base_dir, name = info
                                try:
                                    page.screenshot(path=os.path.join(base_dir, f"{name}.png"), full_page=True)
                                except Exception:
                                    pass
                        if rows:
                            break
                    except PlaywrightTimeoutError:
                        last_error = RuntimeError(
                            f"Bing 网页搜索超时: query={query!r}, attempt={attempt}/{max_attempts}, page={page_idx+1}"
                        )
                    except Exception as e:
                        last_error = RuntimeError(
                            f"Bing 网页搜索失败: query={query!r}, attempt={attempt}/{max_attempts}, page={page_idx+1}, error={e!r}"
                        )
                    if attempt < max_attempts:
                        # LangGraph dev/Studio 会对 time.sleep 这类同步阻塞调用报 BlockingError；
                        # 这里改用 Playwright 自带的 wait_for_timeout（由浏览器驱动调度），
                        # 避免在 ASGI 环境触发阻塞检测。
                        try:
                            page.wait_for_timeout(int(backoff * attempt * 1000))
                        except Exception:
                            pass

                if not rows:
                    if debug_dump:
                        try:
                            last_page_html = page.content()
                        except Exception:
                            last_page_html = None
                        info = _dump_bing_debug(
                            query=query,
                            page_url=last_page_url or page_url,
                            page_title=last_page_title,
                            html=last_page_html,
                            cfg=cfg,
                        )
                        if info and debug_dump_screenshot:
                            base_dir, name = info
                            try:
                                page.screenshot(path=os.path.join(base_dir, f"{name}.png"), full_page=True)
                            except Exception:
                                pass
                    if last_error is not None:
                        page_errors.append(str(last_error))
                    # 首页拿不到结果：不要立刻失败；有时第一页是插屏/风控页但"下一页"仍可返回结果。
                    # 若已累计到结果则直接停止；否则尝试跟随下一页链接继续。
                    if page_idx == 0 and not all_rows:
                        next_links = page.locator("a.sb_pagN")
                        try:
                            href = next_links.first.get_attribute("href") if next_links.count() else None
                        except Exception:
                            href = None
                        if href:
                            next_page_url = urljoin(BING_WEB_SEARCH_URL, href)
                            continue
                        raise last_error or RuntimeError(f"Bing 网页搜索未返回任何结果: query={query!r}")
                    break

                added_this_page = 0
                for row in rows:
                    k = row_key(row)
                    if not k or k in seen_keys:
                        continue
                    url = str(row.get("url") or "").strip()
                    source = str(row.get("source") or "").strip()
                    seen_keys.add(k)
                    all_rows.append(
                        {
                            "title": str(row.get("title") or "").strip(),
                            "url": url,
                            "snippet": str(row.get("snippet") or "").strip(),
                            "source": source,
                            "engine": "bing_web",
                        }
                    )
                    added_this_page += 1
                    if len(all_rows) >= requested:
                        break

                if len(all_rows) >= requested:
                    break

                # 用"下一页"链接推进分页（比手工拼 first= 更稳，避免 Bing 忽略参数返回同一页）。
                next_links = page.locator("a.sb_pagN")
                try:
                    href = next_links.first.get_attribute("href") if next_links.count() else None
                except Exception:
                    href = None
                if not href:
                    break
                next_page_url = urljoin(BING_WEB_SEARCH_URL, href)

                # 翻页没有带来任何新结果，通常意味着被去重/风控或已到末尾，停止。
                if added_this_page == 0:
                    break
        finally:
            browser.close()

    parsed = _dedupe_results(all_rows)
    if not parsed:
        page_title = str(last_page_title or "").strip()
        last_url = str(last_page_url or "").strip()
        title_hint = f", page_title={page_title!r}" if page_title else ""
        url_hint = f", page_url={last_url!r}" if last_url else ""
        err_hint = f", errors={page_errors[:1]!r}" if page_errors else ""
        raise RuntimeError(f"Bing 网页搜索未返回任何结果: query={query!r}{title_hint}{url_hint}{err_hint}")
    return parsed[:requested]


def _bing_web_page_url(query: str, *, first: int = 1, count: int = 10) -> str:
    f = int(first or 1)
    c = int(count or 10)
    if f < 1:
        f = 1
    if c < 1:
        c = 10
    # Bing: first=1 表示第一页第一条，first=11 表示第二页第一条（在常见 count=10 时）
    return f"{BING_WEB_SEARCH_URL}?q={quote_plus(query)}&mkt=zh-CN&count={c}&first={f}"

def _dump_bing_debug(
    *,
    query: str,
    page_url: str | None,
    page_title: str | None,
    html: str | None,
    cfg: dict[str, Any],
) -> tuple[str, str] | None:
    """
    仅用于排查 Bing 搜索"偶发解析不到结果"的问题。
    不做任何兜底切换数据源；只是把实际抓到的页面信息落盘，方便后续人工判断是否验证码/风控页。
    """

    base_dir = str(cfg.get("search_debug_dir") or ".data/search_debug").strip() or ".data/search_debug"
    thread_id = str(cfg.get("thread_id") or "").strip()
    run_date = str(cfg.get("run_date") or "").strip()
    stamp = time.strftime("%Y%m%d_%H%M%S")

    safe = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", str(query or "")).strip("_")
    safe = safe[:80] if safe else "query"
    parts = [stamp]
    if run_date:
        parts.append(run_date)
    if thread_id:
        parts.append(thread_id)
    name = "__".join(parts) + f"__{safe}"

    # LangGraph dev/Studio 在 ASGI 环境会对 os.mkdir/os.makedirs 这类同步 IO 抛 BlockingError。
    # 该 debug dump 仅用于排障，不应导致搜索主流程失败；因此这里容错并显式记录跳过原因。
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as e:
        debug_log(
            f"search_debug_dump skipped: cannot create dir {base_dir!r}: {e!r}",
            cfg=cfg,
            prefix="search",
        )
        return None
    meta_path = os.path.join(base_dir, f"{name}.meta.txt")
    html_path = os.path.join(base_dir, f"{name}.html")

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"query={query!r}\n")
            if page_url:
                f.write(f"page_url={page_url!r}\n")
            if page_title:
                f.write(f"page_title={page_title!r}\n")
    except Exception:
        pass

    if html:
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass
    return (base_dir, name)


def _dedupe_results(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in results:
        title = str(row.get("title") or "").strip().lower()
        url = str(row.get("url") or "").strip()
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out
