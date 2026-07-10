from __future__ import annotations

from typing import Any

from core.state import HotItem
from tools.hot_douyin import fetch_douyin_hot
from tools.hot_merge import merge_hot_items
from tools.hot_weibo import fetch_weibo_hot


def fetch_hot_items(cfg: dict[str, Any]) -> list[HotItem]:
    """
    旧版“单节点抓榜单”的兼容入口。

    注意：当前工作流已拆成 `fetch_weibo_hot` / `fetch_douyin_hot` 两路并行。
    这里保留一个聚合入口，便于 notebook / 旧代码复用。
    """

    weibo = fetch_weibo_hot(cfg)
    douyin = fetch_douyin_hot(cfg)
    return merge_hot_items(weibo=weibo, douyin=douyin, limit=int(cfg.get("hot_merge_limit") or 50))
