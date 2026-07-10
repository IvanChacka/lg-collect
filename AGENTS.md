# Agent Working Rules (hot_collect)

本仓库的工作流有明确约束：**不允许出现“假流程/假数据/静默兜底”**。

## Hard Rules

1) **禁止 Mock**
- 任何与热榜、搜索、LLM 相关的 mock 逻辑一律禁止。

2) **禁止静默兜底 / 回退**
- LLM 结构化输出不合规时，不允许“默认取前 N 条”继续跑。
- 外部数据源（热榜/搜索）不允许在运行时悄悄切换到其他来源作为兜底。
- 允许做“格式修复型重试”（例如要求模型把原始输出整理成严格 JSON），但最终必须是真实调用产生的有效输出。

3) **节点必须有效**
- 每个节点都必须承担明确职责（产出状态变更或推进控制流），禁止为了“走通流程”增加无意义节点。

4) **每次修改后至少跑通“改动节点”**
- 修改仅涉及某个节点/工具函数时，优先只跑该节点自检（避免全链路耗时）：`python cli.py node <node_name> --no-human`。
  - 热榜抓取推荐：`python cli.py node fetch_hot_sources --no-human`（会依次跑 `fetch_weibo_hot` + `fetch_douyin_hot`）。
- 推荐开启调试日志：`LLM_DEBUG=1 LLM_DEBUG_LOG_PATH=.data/llm_debug.log`，便于排查。
- 如改动影响到控制流/数据库/下游节点，再跑全链路：`python cli.py run --no-human`。
- 调试期如果只是为了反复重跑全链路，允许显式使用 `HOT_DB_DISABLE_DEDUP=1` 跳过“同一天热点去重”，不要为了节省时间反复手工清空数据库；默认仍应保持去重开启。

5）如果要新增项目的环境变量，直接写在/Users/mima0000/Desktop/hot_collect/.env这个里面，不要再创建一个.env.example，然后等我来复制，你直接改就行