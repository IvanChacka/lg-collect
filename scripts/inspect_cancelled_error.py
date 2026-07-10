from __future__ import annotations

import pathlib


def _find_all(haystack: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    i = 0
    while True:
        j = haystack.find(needle, i)
        if j == -1:
            return out
        out.append(j)
        i = j + len(needle)


def main() -> int:
    root = pathlib.Path(".langgraph_api")
    if not root.exists():
        print("No .langgraph_api directory found.")
        return 0

    files = sorted(root.glob("*.pckl"))
    if not files:
        print("No .pckl files found in .langgraph_api.")
        return 0

    needle = b"CancelledError"
    for fp in files:
        data = fp.read_bytes()
        hits = _find_all(data, needle)
        if not hits:
            continue
        print(f"\n== {fp} ==")
        for idx in hits[-5:]:
            start = max(0, idx - 350)
            end = min(len(data), idx + 350)
            snippet = data[start:end]
            # 尝试把附近的 ASCII 可读片段提取出来（避免反序列化依赖）
            printable = bytes(ch if 32 <= ch <= 126 else 10 for ch in snippet)
            # 收敛连续空行
            text = "\n".join([ln for ln in printable.decode("utf-8", errors="ignore").splitlines() if ln.strip()])
            print(f"-- hit@{idx} --")
            print(text[:1200])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

