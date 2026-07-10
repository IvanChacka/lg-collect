from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def wait_materials_ready_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：素材分析并行链路的“汇合栅栏”。

    设计目的：
    - 大模型筛选出热点后，同时走两路并行：
      1) 搜索/抓取/抽取/整理文章 -> 文章分析
      2) 搜索/抓取/整理视频 -> 视频分析
    - 只有两路分析都完成后，才进入“聚合素材分析”与后续选题推理

    实现方式：
    - 通过 State 中的 `materials_barrier`（NamedBarrierValue）实现并行汇合
    - 该节点只负责控制流程推进，不负责合并业务数据
    """

    _ = get_config(config)
    return {}
