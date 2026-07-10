from typing import Any

from langchain_core.runnables import RunnableConfig

from core.state import HotCollectState
from tools.utils import get_config


def wait_hot_sources_node(
    state: HotCollectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    节点：热点源并行抓取的“汇合栅栏”。

    设计目的：
    - 微博热搜与抖音热榜必须并行抓取
    - 只有两路都完成后，才允许进入“聚合热点标题”

    实现方式：
    - 通过 State 中的 `hot_sources_barrier`（NamedBarrierValue）实现“等两路都写入后才可用”
    - 该节点本身不做业务逻辑，只负责把工作流推进到下一步
    """

    _ = get_config(config)
    return {}
