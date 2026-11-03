"""评测（v2）：检查 agent 结果里是否包含「正确答案」。

判定方法同 v1：execution accuracy + 「关键值包含」，避免「对但多列」被判错。
区别：v1 从 agent.trace.events 取 SQL；v2 从 LangGraph 的最终 messages 里取
所有 run_sql 的 tool_call 参数。

运行：设好 DEEPSEEK_API_KEY，先 python -m rag.ingest 建索引，再 python eval.py
"""
import json
from decimal import Decimal

from langchain_core.messages import AIMessage, HumanMessage

from db import execute_raw
from graph.graph import build_graph


EVAL_CASES = [
    {"id": "q1", "question": "用户总数是多少？", "expected": {3}},
    {"id": "q2", "question": "订单金额最高是多少？", "expected": {200.0}},
    {"id": "q3", "question": "李四的订单总额是多少？", "expected": {119.30}},
    {"id": "q4", "question": "王五有几笔订单？", "expected": {1}},
    {"id": "q5", "question": "所有订单的总金额是多少？", "expected": {569.20}},
]


def _norm(v):
    return float(v) if isinstance(v, Decimal) else v


def values_of(sql: str) -> set:
    """执行 SQL，返回结果里出现的所有值（摊平成集合）。"""
    return {_norm(v) for row in execute_raw(sql) for v in row}


def _sqls_of(result: dict) -> list[str]:
    """从 graph 最终消息里，提取所有 run_sql 工具调用的 SQL。"""
    sqls = []
    for m in result["messages"]:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                if isinstance(tc, dict):  # 新版 langchain-core 里 tool_calls 是 dict
                    name, args = tc.get("name"), tc.get("args")
                    if isinstance(args, str):
                        args = json.loads(args)
                else:  # 旧版是 ToolCall 对象
                    name, args = getattr(tc, "name", None), getattr(tc, "args", {})
                if name == "run_sql" and args:
                    sqls.append(args["sql"])
    return sqls


def run_eval() -> None:
    graph = build_graph()
    passed = 0
    for case in EVAL_CASES:
        result = graph.invoke(
            {"question": case["question"], "context": "", "messages": [HumanMessage(case["question"])]}
        )
        sqls = _sqls_of(result)
        if not sqls:
            print(f"[FAIL] {case['id']}  没有调用 run_sql：{case['question']}")
            continue

        all_values: set = set()
        for sql in sqls:
            all_values |= values_of(sql)

        if case["expected"].issubset(all_values):
            passed += 1
            print(f"[PASS] {case['id']}  {case['question']}")
        else:
            print(f"[FAIL] {case['id']}  {case['question']}")
            print(f"   期望值: {case['expected']}")
            print(f"   agent 所有结果值: {all_values}")

    print(f"\n=== 通过 {passed}/{len(EVAL_CASES)} ===")


if __name__ == "__main__":
    run_eval()
