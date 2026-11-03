"""Compile once; state, budgets and traces stay isolated per invocation."""

from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableLambda
from graph.nodes import (
    build_agent_node,
    build_retrieve_node,
    should_continue,
    tools_node,
    atools_node,
)
from graph.state import AgentState
from llm import build_chat_model
from tools import run_sql_tool


def build_graph(worker=None, retrieval_mode="hybrid", model=None):
    llm = (model if model is not None else build_chat_model()).bind_tools(
        [run_sql_tool]
    )
    graph = StateGraph(AgentState)
    graph.add_node("retrieve", build_retrieve_node(worker, retrieval_mode))
    graph.add_node("agent", build_agent_node(llm))
    graph.add_node("tools", RunnableLambda(tools_node, afunc=atools_node))
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "agent")
    graph.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "end": END}
    )
    graph.add_edge("tools", "agent")
    return graph.compile()
