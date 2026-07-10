from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.llm_client import _normalize_minimax_model, _strip_think_blocks
from tools.utils import get_config


def main() -> int:
    cfg = get_config(None)

    api_key = str(cfg.get("minimax_api_key") or "").strip()
    base_url = str(cfg.get("minimax_base_url") or "https://api.minimaxi.com/v1").strip()
    model = _normalize_minimax_model(str(cfg.get("minimax_model") or "").strip()) or "MiniMax-M2.7-highspeed"
    timeout_seconds = float(cfg.get("minimax_timeout_seconds") or 300)

    if not api_key:
        print("Missing MINIMAX_API_KEY in .env", file=sys.stderr)
        return 2

    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {
                "role": "system",
                "content": "你是一个只输出 JSON 的程序。不要解释，不要 Markdown，不要 <think>。",
            },
            {
                "role": "user",
                "content": (
                    "只输出 JSON："
                    '{"ok":true,"items":[{"id":"T01","title":"测试标题","thesis":"测试论点"}]}'
                ),
            },
        ],
        "reasoning_split": False,
        "response_format": {"type": "json_object"},
        "max_tokens": 256,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    message = (((data or {}).get("choices") or [{}])[0].get("message") or {})
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    reasoning_details = message.get("reasoning_details")

    print("OK")
    print("url:", url)
    print("model:", model)
    print("timeout_seconds:", timeout_seconds)
    print("request_reasoning_split:", payload["reasoning_split"])
    print("content_snippet:", repr(str(content)[:300]))
    print("cleaned_content_snippet:", repr(_strip_think_blocks(str(content or ""))[:300]))
    print("reasoning_content_len:", len(str(reasoning_content or "")))
    print("reasoning_details_count:", len(reasoning_details or []) if isinstance(reasoning_details, list) else 0)
    print("raw_response:", json.dumps(data, ensure_ascii=False)[:1000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
