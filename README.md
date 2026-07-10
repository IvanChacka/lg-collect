# langgraph-hotcollect — 智能热点采集与口播脚本生成

**自动抓取微博/抖音热点榜单，经 LLM 选题、搜索素材、生成口播脚本，通过飞书卡片交互式确认选题方向。**

基于 LangGraph 编排的 20+ 节点工作流，结合 DeepSeek LLM 推理、搜索引擎采集、飞书通知，实现从热点发现到口播脚本的全链路自动化。

## 技术栈

| 层 | 技术 | 用途 |
|---|---|---|
| 工作流编排 | LangGraph (Python) | 状态管理、节点编排、条件路由、并行 Fan-out |
| LLM 推理 | DeepSeek / MiniMax | 热点筛选、素材分析、脚本生成 |
| 热点获取 | 微博 API / 抖音 API / TopHub | 多源热榜数据抓取 |
| 搜索 | Bing Web Search | 相关文章搜索 |
| 视频下载 | BBDown | B站视频字幕/音频下载 |
| 语音转写 | 讯飞极速语音转写 | B站视频语音转文字 |
| 通知 | 飞书 SDK (WebSocket) | 选题卡片推送、群内 @机器人 交互等待 |
| 服务端 | FastAPI | REST API + Mermaid 工作流可视化 |
| 存储 | SQLite (LangGraph Checkpoint) | 状态持久化、断点续跑 |

## 工作流概览

```
start_hot_fetch
    ├── fetch_weibo_hot    (并行)  微博热搜
    └── fetch_douyin_hot   (并行)  抖音热榜
            │
    aggregate_hot_titles          聚合热榜标题
            │
    pick_hotspot_llm              LLM 筛选最佳选题
            │
    search_articles               搜索相关文章
            │
    aggregate_article_text        聚合文章内容
            │
    infer_account_topics          推断账号定位 + 生成选题提案
            │
    send_feishu_card              ✋ 飞书卡片交互：等待用户选择
            │
    prepare_selected_materials    准备选中选题的素材
            │
    ├── search_articles_selected  深度搜索文章 → llm_filter_articles → fetch_article_contents
    └── search_videos_selected    深度搜索视频 → llm_filter_videos → download_subtitles
            │
    aggregate_keyword_materials   关键词维度聚合素材
            │
    summarize_keyword_materials   LLM 总结素材
            │
    final_script                  生成最终口播脚本
            │
    send_feishu_final_script      推送最终脚本到飞书
```

## 项目结构

```
langgraph-hotcollect/
├── core/              # 运行时核心（StateGraph构建 / 状态定义 / Mermaid可视化）
├── nodes/             # 工作流节点（每个节点一个文件，50+ 节点）
├── tools/             # 工具函数（飞书 / 搜索 / BBDown / LLM / 热点源等）
├── vendor/            # 外部项目（git submodule）
│   ├── BBDown/        # B站命令行下载器
│   ├── bilibili-video-downloader/  # B站GUI下载器（参考）
│   └── ms-ra-forwarder/  # 微软TTS转发服务（参考）
├── scripts/           # 辅助脚本（含飞书监听独立运行脚本）
├── notebooks/         # Jupyter（工作流可视化）
├── doc/               # 文档（含 macOS launchd 定时任务指南）
├── .data/             # 运行时数据（checkpoints.sqlite / llm_debug.log）
├── cli.py             # CLI 入口（run / resume / node / graph 子命令）
├── server.py          # FastAPI 服务入口
├── studio_graph.py    # LangGraph Studio 图导出
├── langgraph.json     # LangGraph 部署配置
├── requirements.txt
└── .env               # 环境变量（LLM / TTS / 飞书 / 热点源等全部超参数）
```

## 快速启动

### 环境要求

- Python >= 3.10
- pip

### 克隆（含 vendor 子模块）

```bash
# 方式 A（推荐）
git clone --recurse-submodules https://gitee.com/a-fool-gets-his-own-life/langgraph-hotcollect.git

# 方式 B
git clone https://gitee.com/a-fool-gets-his-own-life/langgraph-hotcollect.git
cd langgraph-hotcollect
git submodule update --init --recursive
```

### 安装

```bash
cd langgraph-hotcollect
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置 .env

项目已有 `.env` 文件（已通过 `.gitignore` 忽略）。核心配置项：

```bash
# ---- 大模型 ----
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

# ---- 热点源 ----
HOT_SOURCE=official              # official / tophub_html / cenguigui_api
WEIBO_HOT_SOURCE=tophub_html     # 微博热搜数据源
DOUYIN_HOT_SOURCE=official       # 抖音热榜数据源

# ---- 搜索 ----
SEARCH_PROVIDER=bing_web

# ---- 视频（B站）----
SELECTED_VIDEO_QUERIES_LIMIT=3
VIDEOS_PER_KEYWORD=30
VIDEO_LLM_KEEP_LIMIT=3

# ---- 语音转写（讯飞）----
IFLYTEK_APPID=xxx
IFLYTEK_API_KEY=xxx
IFLYTEK_API_SECRET=xxx

# ---- 飞书（Human-in-the-loop）----
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CHAT_ID=oc_xxx
FEISHU_WS_ENABLE=1
FEISHU_SELECTION_TIMEOUT_SECONDS=3600

# ---- 存储 ----
CHECKPOINT_SQLITE_PATH=.data/checkpoints.sqlite
```

### 运行方式

**方式一：CLI 命令行**

```bash
# 运行当日完整工作流
python -m cli run

# 指定日期
python -m cli run --run-date 2026-07-06

# 直接指定热点关键词（跳过微博/抖音抓取和LLM选题）
python -m cli run --hotspot "某热点关键词"

# 全自动模式（跳过飞书人工确认，适合自动化流水线）
python -m cli run --no-human

# 从人工选择后恢复运行
python -m cli resume --thread-id <thread_id> --proposal-id <proposal_id>

# 调试单个节点
python -m cli node fetch_weibo_hot
python -m cli node pick_hotspot_llm

# 输出 Mermaid 工作流图
python -m cli graph --output workflow.mmd
```

**方式二：FastAPI 服务**

```bash
uvicorn server:app --reload --port 8000
```

API 端点：
- `POST /run/daily` — 触发每日工作流
- `GET /graph/mermaid` — 获取 Mermaid 流程图源码
- `GET /graph` — 工作流可视化页面

触发运行：
```bash
curl -X POST localhost:8000/run/daily -H 'Content-Type: application/json' -d '{}'
```

**方式三：LangGraph Studio（开发调试）**

```bash
langgraph dev
```

在 LangGraph Studio 中可视化调试每个节点的输入输出、编辑历史状态、重放节点。

**方式四：飞书监听独立运行**

```bash
python scripts/feishu_listen_and_resume.py
```

## Human-in-the-loop（飞书交互）

完整流程需要飞书机器人支持：

1. 配置飞书内部应用（开启 WebSocket 模式，订阅"接收消息"事件）
2. 在 `.env` 中配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_CHAT_ID`
3. 设置 `FEISHU_WS_ENABLE=1`
4. 运行工作流，`send_feishu_card_and_wait_selection` 节点会向目标群发送选题卡片
5. 在飞书群内 **@机器人 + 数字序号** 回复（例如 `@机器人 1`）
6. 工作流收到选择后自动继续执行

## 关键节点说明

| 节点 | 功能 | 输入 | 输出 |
|---|---|---|---|
| `fetch_weibo_hot` | 抓取微博热搜榜 | 热搜源配置 | 微博热榜条目列表 |
| `fetch_douyin_hot` | 抓取抖音热榜 | 热搜源配置 | 抖音热榜条目列表 |
| `aggregate_hot_titles` | 多源热榜去重合并 | 微博+抖音热榜 | 统一热榜列表 |
| `pick_hotspot_llm` | LLM 筛选最优选题 | 热榜列表 | 选中热点 + 理由 |
| `search_articles` | Bing 搜索相关文章 | 热点关键词 | 候选文章列表 |
| `aggregate_article_text` | 抓取并聚合文章正文 | 文章URL列表 | 文章全文内容 |
| `infer_account_topics_and_generate_proposals` | LLM推断账号定位+生成选题提案 | 文章素材 | 多个选题方案+深度洞察 |
| `send_feishu_card_and_wait_selection` | 飞书卡片推送给用户选择 | 选题方案 | **阻塞等待用户@回复** |
| `prepare_selected_topic_materials` | 根据用户选择准备素材 | 选中方案 | 素材列表+筛选关键词 |
| `search_articles_selected` | 深度搜索文章 | 关键词 | 文章搜索结果 |
| `llm_filter_articles_selected` | LLM 筛选文章质量 | 文章列表 | 筛选后的文章 |
| `search_videos_selected` | B站搜索视频 | 关键词 | 视频搜索结果 |
| `llm_filter_videos_selected` | LLM 筛选视频质量 | 视频列表 | 筛选后的视频 |
| `download_subtitles_bbdown_selected` | BBDown下载字幕+讯飞转写 | B站视频 | 字幕文本 |
| `aggregate_keyword_materials_selected` | 按关键词聚合素材 | 文章+视频素材 | 关键词维度的素材汇总 |
| `summarize_keyword_materials_selected` | LLM总结素材要点 | 聚合素材 | 素材总结 |
| `final_script` | 生成最终口播脚本 | 素材总结 | 完整口播文案+分镜段 |
| `send_feishu_final_script` | 推送最终脚本到飞书 | 口播脚本 | 飞书消息 |

## macOS 定时运行（launchd）

```bash
# 参考 doc/mac_launchd.md 配置每天固定时间自动运行
```

## 断点续跑

LangGraph 通过 SQLite checkpoint 自动持久化状态。使用相同 `thread_id` 即可恢复：

```bash
python -m cli run --thread-id <上次的thread_id>
```

## 调试

- **LLM 调试**：设置 `.env` 中 `LLM_DEBUG=1`，交互日志写入 `.data/llm_debug.log`
- **工作流可视化**：`GET http://localhost:8000/graph` 或 Jupyter Notebook `notebooks/工作流可视化.ipynb`
- **单节点调试**：`python -m cli node <节点名>`

## 维护指南

- 添加新热点源：在 `nodes/` 创建节点文件 → 在 `core/runtime.py` 注册 → 在 `cli.py` 添加子命令
- 修改 LLM Prompt：编辑对应节点文件中的 prompt 模板
- 调整搜索范围：修改 `.env` 中 `*_QUERIES_LIMIT` / `*_PER_KEYWORD` / `*_KEEP_LIMIT` 等参数
