"""LangGraph 组装：retrieve → agent ⇄ tools，直到模型不再调工具。

对照 v1 的 agent/core.py：那里是手写的 while 循环；这里用「状态图 + 条件边」
表达同一个循环——tools 执行完连回 agent，agent 判断不再调工具就 END。
"""
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from graph.nodes import build_agent_node, retrieve_node, should_continue
from graph.state import AgentState
from llm import build_chat_model
from tools import run_sql_tool


def build_graph():
    # 把工具绑到模型上：LangChain 会把 run_sql 转成 OpenAI 的 function calling 格式
    llm_with_tools = build_chat_model().bind_tools([run_sql_tool])

    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve_node)
    g.add_node("agent", build_agent_node(llm_with_tools))
    g.add_node("tools", ToolNode([run_sql_tool]))

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "agent")
    # 条件边：agent 还在调工具就去 tools，否则 END
    g.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "end": END}
    )
    g.add_edge("tools", "agent")  # 工具执行完回到 agent，让它观察结果再决定

    return g.compile()
