from core.graph import build_graph

# LangGraph Studio 需要一个“已编译”的 graph 对象
# 注意：LangGraph API（含 dev server）会自动处理持久化/Checkpointer
# 这里不要手动传入自定义 checkpointer，否则 dev server 会报错并拒绝加载
graph = build_graph().compile(name="hot-collect")
