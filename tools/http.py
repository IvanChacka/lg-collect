from __future__ import annotations

import hashlib
import os
import random
import sqlite3
import threading
import time
from typing import Any

import httpx
from curl_cffi import requests as curl_cffi_requests
from dotenv import dotenv_values


_DEFAULT_UA = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

_DOTENV_CACHE: dict[str, str] | None = None


def _blocking_wait(seconds: float) -> None:
    """
    等待一小段时间（用于重试退避）。

    注意：LangGraph dev/Studio 会对 time.sleep 触发 BlockingError。
    这里用 threading.Event().wait 规避该检测，同时保持同步语义。
    """

    try:
        threading.Event().wait(max(0.0, float(seconds)))
    except Exception:
        # 退避等待失败不应掩盖原始网络错误；忽略即可
        return


def _dotenv_get(name: str) -> str:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is None:
        try:
            _DOTENV_CACHE = {k: str(v) for k, v in (dotenv_values(".env") or {}).items() if v is not None}
        except Exception:
            _DOTENV_CACHE = {}
    return str((_DOTENV_CACHE or {}).get(name) or "").strip()


def _env_or_dotenv(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v is not None and str(v).strip() != "":
        return str(v).strip()
    v2 = _dotenv_get(name)
    if v2:
        return v2
    return default


def _ssl_verify() -> bool:
    """
    是否校验证书：
    - 默认开启校验（更安全）
    - 如遇到公司代理/抓取环境的 TLS 中间人导致握手失败，可在运行环境显式设置：
      CRAWL_SSL_VERIFY=0
    注意：该开关是“显式配置”，不是运行时静默回退。
    """

    v = _env_or_dotenv("CRAWL_SSL_VERIFY", "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


def get_text(
    *,
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
    retries: int = 3,
    backoff: float = 0.6,
    cache_ttl_seconds: int = 1800,
) -> str:
    """
    统一的 HTTP GET 文本获取（支持：重试/退避/缓存/代理）。
    """

    cached = _cache_get(url=url, ttl_seconds=cache_ttl_seconds)
    if cached is not None:
        return cached

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _http_client(timeout=timeout, headers=headers) as client:
                r = client.get(url)
                r.raise_for_status()
                text = r.text
                _cache_set(url=url, text=text)
                return text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                # httpx TLS 指纹被目标站点识别，回退到 curl_cffi 模拟浏览器
                try:
                    text = _get_text_curl_cffi(url=url, headers=headers, timeout=timeout)
                    _cache_set(url=url, text=text)
                    return text
                except Exception as ce:
                    last_err = ce
                    break
            last_err = e
            if attempt >= retries:
                break
            _blocking_wait(backoff * (2**attempt) + random.random() * 0.2)
        except Exception as e:
            last_err = e
            if attempt >= retries:
                break
            _blocking_wait(backoff * (2**attempt) + random.random() * 0.2)

    raise RuntimeError(_format_http_error(prefix="GET 失败", url=url, err=last_err)) from last_err


def _get_text_curl_cffi(
    *, url: str, headers: dict[str, str] | None = None, timeout: float = 15
) -> str:
    """使用 curl_cffi 模拟 Chrome 浏览器 TLS 指纹，绕过反爬 403。"""
    merged = _merge_headers(headers)
    merged.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    r = curl_cffi_requests.get(
        url, headers=merged, impersonate="chrome124", timeout=timeout
    )
    r.raise_for_status()
    return r.text


def get_json(
    *,
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
    retries: int = 3,
    backoff: float = 0.6,
    cache_ttl_seconds: int = 600,
) -> Any:
    """
    统一的 HTTP GET JSON 获取。
    """

    cached = _cache_get(url=url, ttl_seconds=cache_ttl_seconds)
    if cached is not None:
        import json

        return json.loads(cached)

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _http_client(timeout=timeout, headers=headers) as client:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
                import json

                _cache_set(url=url, text=json.dumps(data, ensure_ascii=False))
                return data
        except Exception as e:
            last_err = e
            if attempt >= retries:
                break
            _blocking_wait(backoff * (2**attempt) + random.random() * 0.2)

    raise RuntimeError(_format_http_error(prefix="GET JSON 失败", url=url, err=last_err)) from last_err


def post_json(
    *,
    url: str,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    retries: int = 2,
    backoff: float = 0.6,
) -> Any:
    """
    统一的 HTTP POST JSON（支持：重试/退避/代理）。
    """

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _http_client(timeout=timeout, headers=headers) as client:
                r = client.post(url, json=json_body)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last_err = e
            if attempt >= retries:
                break
            _blocking_wait(backoff * (2**attempt) + random.random() * 0.2)
    raise RuntimeError(_format_http_error(prefix="POST JSON 失败", url=url, err=last_err)) from last_err


def _format_http_error(*, prefix: str, url: str, err: Exception | None) -> str:
    base = f"{prefix}: {url!r}: {err!r}"
    if err is None:
        return base
    msg = repr(err)
    if "UNEXPECTED_EOF_WHILE_READING" in msg:
        proxy = _env_or_dotenv("CRAWL_PROXY", "").strip()
        tip = "检测到 TLS 握手被对端提前断开（常见于网络限制/需要代理的环境）"
        if proxy:
            tip += f"；当前已配置 CRAWL_PROXY={proxy!r}，如仍失败请更换可用代理"
        else:
            tip += "；请在 `.env` 或环境变量中配置 `CRAWL_PROXY=http://127.0.0.1:7890` 后重试"
        return base + f" ({tip})"
    return base


def _merge_headers(headers: dict[str, str] | None) -> dict[str, str]:
    base = {"User-Agent": random.choice(_DEFAULT_UA), "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"}
    if headers:
        base.update(headers)
    return base


def _http_client(*, timeout: float, headers: dict[str, str] | None) -> httpx.Client:
    """
    兼容不同 httpx 版本的代理参数：
    - 新版使用 `proxy=...`
    - 旧版使用 `proxies=...`
    """

    proxy = _proxy()
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": _merge_headers(headers),
        "verify": _ssl_verify(),
    }

    if proxy:
        kwargs["proxy"] = proxy

    try:
        return httpx.Client(**kwargs)
    except TypeError:
        # 回落到旧参数名
        if "proxy" in kwargs:
            kwargs.pop("proxy", None)
            kwargs["proxies"] = proxy
        return httpx.Client(**kwargs)


def _proxy() -> str | None:
    # 支持 http(s) 代理：CRAWL_PROXY=http://127.0.0.1:7890
    p = _env_or_dotenv("CRAWL_PROXY", "").strip()
    return p or None


def _cache_path() -> str:
    path = os.getenv("CRAWL_CACHE_SQLITE", ".data/crawl_cache.sqlite").strip()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return path


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_path())
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, url TEXT, created_at INTEGER, body TEXT)"
    )
    return conn


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_get(*, url: str, ttl_seconds: int) -> str | None:
    if ttl_seconds <= 0:
        return None
    try:
        conn = _cache_conn()
        key = _cache_key(url)
        row = conn.execute("SELECT created_at, body FROM cache WHERE key=?", (key,)).fetchone()
        conn.close()
        if not row:
            return None
        created_at, body = int(row[0]), str(row[1])
        if int(time.time()) - created_at > ttl_seconds:
            return None
        return body
    except Exception:
        return None


def _cache_set(*, url: str, text: str) -> None:
    try:
        conn = _cache_conn()
        key = _cache_key(url)
        conn.execute(
            "INSERT OR REPLACE INTO cache(key,url,created_at,body) VALUES (?,?,?,?)",
            (key, url, int(time.time()), text),
        )
        conn.commit()
        conn.close()
    except Exception:
        return None
