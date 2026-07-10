from __future__ import annotations

import re
from typing import Any

from core.state import TopicProposal
from tools.feishu_api import send_interactive_card_message, send_text_message
from tools.feishu_pending import set_pending


def _platform_label(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p == "douyin":
        return "抖音"
    if p == "weibo":
        return "微博"
    if p:
        return p
    return "未知"


def _short(s: str, n: int = 80) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def build_topic_choice_markdown(
    *,
    thread_id: str,
    hot_keyword: str,
    hot_platform: str,
    proposals: list[TopicProposal],
) -> str:
    lines: list[str] = []
    lines.append(f"**当前热点**：{hot_keyword or '（未知）'}")
    lines.append(f"**来源**：{_platform_label(hot_platform)}")
    lines.append("")
    # Thread_id 仅用于系统内部 resume，不在群消息中展示（避免影响观感）
    lines.append("请在群里 **@我** 并回复你选择的 **数字序号**（例如：`1`）：")
    lines.append("")
    for idx, p in enumerate(proposals, start=1):
        title = _short(str(p.get("title") or ""), 60)
        thesis = _short(str(p.get("thesis") or ""), 100)
        # 两个空格 + 换行：在 markdown 里强制换行，避免“题目+描述”挤成一段
        lines.append(f"{idx}. **{title}**  ")
        if thesis:
            # 不缩进，确保在飞书 markdown 里按“下一行”显示
            lines.append(f"{thesis}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_topic_choice_card(
    *,
    thread_id: str,
    hot_keyword: str,
    hot_platform: str,
    proposals: list[TopicProposal],
) -> dict[str, Any]:
    """
    构造飞书 interactive card（schema 2.0）。

    说明：
    - 这里用 card 的 markdown element 展示文本（比 post 更符合“卡片”语义）。
    - 用户仍通过群内 @机器人 + 序号/字母回复完成选择，避免依赖按钮回传（更稳）。
    """

    lines: list[str] = []
    lines.append(f"**当前热点**：{hot_keyword or '（未知）'}")
    lines.append(f"**来源**：{_platform_label(hot_platform)}")
    lines.append("")
    # thread_id 不展示：用户只需回复序号，thread 用 pending 映射定位
    lines.append("请在群里 **@我** 并回复你选择的 **数字序号**（例如：`1`）：")
    lines.append("")
    for idx, p in enumerate(proposals, start=1):
        title = _short(str(p.get("title") or ""), 60)
        thesis = _short(str(p.get("thesis") or ""), 100)
        # 两个空格 + 换行：在 markdown 里强制换行，避免“题目+描述”挤成一段
        lines.append(f"{idx}. **{title}**  ")
        if thesis:
            # 不缩进，确保在飞书 markdown 里按“下一行”显示
            lines.append(f"{thesis}")
        lines.append("")
    content = "\n".join(lines).strip()

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "今日选题（请 @我 回复序号）"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": content,
                }
            ]
        },
    }


def send_topic_choice_message(
    *, cfg: dict[str, Any], thread_id: str, hot_keyword: str, hot_platform: str, proposals: list[TopicProposal]
) -> str:
    """
    发送“选题选择”消息到飞书群（interactive card），并记录 pending 映射。
    """

    chat_id = str(cfg.get("feishu_chat_id") or "").strip()
    if not chat_id:
        raise RuntimeError("FEISHU_CHAT_ID 为空，无法发送选题消息")

    card = build_topic_choice_card(
        thread_id=thread_id,
        hot_keyword=hot_keyword,
        hot_platform=hot_platform,
        proposals=proposals,
    )
    msg_id = send_interactive_card_message(cfg=cfg, card=card, chat_id=chat_id)

    set_pending(
        chat_id=chat_id,
        thread_id=thread_id,
        message_id=msg_id,
        proposals=[dict(p) for p in proposals],
        hot_keyword=hot_keyword,
        hot_platform=hot_platform,
    )
    return msg_id


def send_final_script_message(*, cfg: dict[str, Any], thread_id: str, script_markdown: str) -> None:
    """
    发送终版脚本到飞书群（文本消息）。
    """

    text = f"Thread: {thread_id}\n\n{script_markdown}"[:15000]
    send_text_message(cfg=cfg, text=text)


_THREAD_RE = re.compile(r"Thread:\\s*`([^`]+)`|Thread:\\s*([\\w\\-\\.]+)")


def parse_thread_id_from_text(text: str) -> str:
    m = _THREAD_RE.search(text or "")
    if not m:
        return ""
    return str(m.group(1) or m.group(2) or "").strip()
