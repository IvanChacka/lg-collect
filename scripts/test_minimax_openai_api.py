from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv


def _normalize_model(model: str) -> str:
    m = (model or "").strip()
    if not m:
        return ""
    if "/" in m:
        m = m.split("/", 1)[1].strip()
    return m.replace("HighSpeed", "highspeed")


def main() -> int:
    load_dotenv(override=True)

    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    base_url = (os.getenv("MINIMAX_BASE_URL", "") or "https://api.minimaxi.com/v1").strip()
    model = _normalize_model(os.getenv("MINIMAX_MODEL", "").strip()) or "MiniMax-M2.7-highspeed"

    if not api_key:
        print("Missing MINIMAX_API_KEY in .env", file=sys.stderr)
        return 2

    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "只回复一个字：好"},
        ],
        # MiniMax 文档示例：把思考内容分离到 reasoning_details（可选）
        "reasoning_split": True,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        with httpx.Client(timeout=None) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"Request failed: {e!r}", file=sys.stderr)
        return 1

    # 打印最关键信息（不要打印 api_key）
    print("OK")
    print("url:", url)
    print("model:", model)
    print("keys:", list(data.keys())[:20])
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = None
    print("content:", repr(content)[:300])
    print("raw_response:", json.dumps(data, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

