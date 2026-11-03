"""Agent 状态：LangGraph 里状态在节点间流转，是所有节点共享的输入/输出。

messages 用 add_messages 这个 reducer：节点返回新消息时是「追加」而不是「覆盖」。
这样 agent ⇄ tools 循环里，历史消息会一直累积，模型能看到完整上下文——
这替代了 v1 里手写维护 messages 列表的代码。
"""
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    question: str                                       # 用户原始问题
    context: str                                        # RAG 检索到的 schema 上下文
    messages: Annotated[list[BaseMessage], add_messages]  # 对话消息，追加累积
