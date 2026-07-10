from __future__ import annotations

import re


def parse_choice_token(text: str) -> str:
    """
    从用户 @消息中解析选择：仅支持数字序号（1, 2, 3...）。
    """

    s = (text or "").strip()
    if not s:
        return ""
    # 支持 1-99 的序号（避免误把年份/大数字当作选项）
    #
    # 注意：飞书里“回复卡片/转发消息”时，text 里可能包含卡片正文，
    # 例如带有 “1. ... 2. ...” 的列表，导致“取第一个数字”误判为 1。
    # 因此优先取“结尾处的序号”，否则取最后一个匹配到的序号。
    tail = re.search(r"(?<!\d)([1-9]\d?)(?!\d)\s*$", s)
    if tail:
        return tail.group(1)

    matches = re.findall(r"(?<!\d)([1-9]\d?)(?!\d)", s)
    if matches:
        return matches[-1]
    return ""


def is_bot_mentioned(*, bot_name: str, text: str, mention_names: tuple[str, ...]) -> bool:
    """
    判断该消息是否 @ 了机器人。

    说明：
    - 飞书 text 消息的 content 不一定包含可直接匹配的 '@机器人名' 文本；
      更稳妥的方法是用 message.mentions。
    - bot_name 获取失败时，保守处理：要求出现 mentions（避免仅凭文本里的 '@' 误触发）。
    """

    bot_name = (bot_name or "").strip()
    if bot_name:
        if bot_name in (mention_names or ()):
            return True
        if f"@{bot_name}" in (text or ""):
            return True
        return False
    return bool(mention_names)
