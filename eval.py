"""评测：检查 agent 的结果里是否包含「正确答案」。

为什么不用「结果集完全相等」：agent 经常返回比标准答案更多的列
（比如额外带上 name、order_count），这些「对但更详细」的答案不该被判错。
所以改成：期望的关键值，是否都出现在 agent 的结果里。

运行方式：在 sql_agent 目录下，设好 DEEPSEEK_API_KEY，执行
    python eval.py
"""
import asyncio
from decimal import Decimal

from db import execute_raw
from main import build_agent, build_system_prompt


EVAL_CASES = [
    {"id": "q1", "question": "用户总数是多少？", "expected": {3}},
    {"id": "q2", "question": "订单金额最高是多少？", "expected": {200.0}},
    {"id": "q3", "question": "李四的订单总额是多少？", "expected": {119.30}},
    {"id": "q4", "question": "王五有几笔订单？", "expected": {1}},
    {"id": "q5", "question": "所有订单的总金额是多少？", "expected": {569.20}},
]


def _norm(v):
    # 把 Decimal 转成 float，方便和 expected 里的数字比较
    return float(v) if isinstance(v, Decimal) else v


def values_of(sql: str) -> set:
    """执行 SQL，返回结果里出现的所有值（摊平成集合）。"""
    return {_norm(v) for row in execute_raw(sql) for v in row}


async def run_eval() -> None:
    passed = 0
    for case in EVAL_CASES:
        agent = build_agent()
        await agent.run(case["question"], system_prompt=build_system_prompt())

        # 收集 agent 执行过的【所有】run_sql 的结果值（多步 agent 会查多次）
        steps = [t for t in agent.trace.events if t.get("type") == "tool" and t.get("tool") == "run_sql"]
        if not steps:
            print(f"[FAIL] {case['id']}  没有调用 run_sql：{case['question']}")
            continue

        all_values: set = set()
        for t in steps:
            all_values |= values_of(t["arguments"]["sql"])

        # 关键值出现在任何一步的结果里 → 对
        if case["expected"].issubset(all_values):
            passed += 1
            print(f"[PASS] {case['id']}  {case['question']}")
        else:
            print(f"[FAIL] {case['id']}  {case['question']}")
            print(f"   期望值: {case['expected']}")
            print(f"   agent 所有结果值: {all_values}")

    print(f"\n=== 通过 {passed}/{len(EVAL_CASES)} ===")


if __name__ == "__main__":
    asyncio.run(run_eval())
