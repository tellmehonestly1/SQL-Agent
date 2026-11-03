"""图的三个节点：retrieve（检索）、agent（LLM 决策），以及路由判断。"""
from langchain_core.messages import AIMessage, SystemMessage

from graph.state import AgentState
from rag.retrieve import retrieve_context


def build_system_prompt(context: str) -> str:
    return (
        "你是一个数据分析助手。你只能用 run_sql 工具执行只读查询。\n"
        "以下是和用户问题相关的数据库信息（从知识库检索而来）：\n"
        f"{context or '（未检索到，请先查询表结构）'}\n"
        "流程：先查数据，再基于【真实查询结果】用中文回答，不要凭空编造数字。"
    )


def retrieve_node(state: AgentState) -> dict:
    """RAG 检索：用问题去 pgvector 里找相关 schema，填进 context。"""
    return {"context": retrieve_context(state["question"])}


def build_agent_node(llm_with_tools):
    """返回 agent 节点。llm_with_tools = build_chat_model().bind_tools([run_sql_tool])。"""

    def agent_node(state: AgentState) -> dict:
        system = build_system_prompt(state["context"])
        # 每次调用都重新拼 system prompt（不写进 state，避免循环里重复累积）
        response = llm_with_tools.invoke([SystemMessage(system)] + state["messages"])
        return {"messages": [response]}

    return agent_node


def should_continue(state: AgentState) -> str:
    """条件路由：最后一条消息还在调工具 → 去 tools；否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"
