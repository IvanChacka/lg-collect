from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.feishu import build_topic_choice_card  # noqa: E402
from tools.feishu_reply import is_bot_mentioned, parse_choice_token  # noqa: E402


def main() -> None:
    proposals = [
        {
            "proposal_id": "A",
            "title": "下载量≠实力：国产AI大模型的真实边界",
            "thesis": "百亿下载量背后哪些场景真落地，哪些仍是概念？",
        },
        {
            "proposal_id": "B",
            "title": "百亿下载量背后：哪些行业在真正用AI",
            "thesis": "下载不等于应用，工业/医疗落地现状拆解。",
        },
    ]

    card = build_topic_choice_card(
        thread_id="thread-should-not-appear",
        hot_keyword="国产开源大模型下载量破100亿次",
        hot_platform="weibo",
        proposals=proposals,
    )
    print("=== card.body.elements[0].content ===")
    print(card["body"]["elements"][0]["content"])
    print("\n=== card.json (snippet) ===")
    print(json.dumps(card, ensure_ascii=False, indent=2)[:800] + "...\n")

    # 模拟“@机器人 + 序号”文本与 mentions（真实事件中 mentions 由飞书给出）
    bot_name = "hot-collect-bot"
    msg_texts = [
        "@hot-collect-bot 1",
        "选项2",
        "1",
        "@xxx 1",  # 没有 mentions 的情况下不应触发
    ]
    for t in msg_texts:
        token = parse_choice_token(t)
        mentioned = is_bot_mentioned(bot_name=bot_name, text=t, mention_names=(bot_name,) if "hot-collect-bot" in t else ())
        print(f"text={t!r} mentioned={mentioned} token={token!r}")


if __name__ == "__main__":
    main()
