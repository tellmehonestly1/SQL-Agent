"""CLI 入口：问一个问题，走 LangGraph 得到答案（含 RAG 检索）。

运行：先 python -m rag.ingest 建索引，再 python main.py
"""
from langchain_core.messages import HumanMessage

from graph.graph import build_graph


def ask(question: str) -> dict:
    graph = build_graph()
    return graph.invoke(
        {"question": question, "context": "", "messages": [HumanMessage(question)]}
    )


def main() -> None:
    question = "每个用户的订单总额分别是多少"
    result = ask(question)

    print("问题：", question)
    print("\n--- RAG 检索到的上下文 ---")
    print(result["context"])
    print("\n--- 最终答案 ---")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
