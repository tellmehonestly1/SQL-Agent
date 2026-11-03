"""Execution-result evaluation; never count a coincidental scalar as success."""

import argparse
import asyncio
import json
import math
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from langchain_core.messages import AIMessage, ToolMessage
from db import execute_raw, open_async_pool, close_async_pool, close_pool
from graph.graph import build_graph
from graph.nodes import RetrievalWorker
from provenance import citations_valid


def cell_equal(actual, expected):
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        try:
            number = Decimal(str(actual))
            return number.is_finite() and abs(
                number - Decimal(str(expected))
            ) <= Decimal("0.000001")
        except (InvalidOperation, ValueError):
            return False
    return type(actual) is type(expected) and actual == expected


def rows_equal(actual, expected, ordered=False):
    if len(actual) != len(expected):
        return False

    def equal(a, b):
        return len(a) == len(b) and all(cell_equal(x, y) for x, y in zip(a, b))

    if ordered:
        return all(equal(a, b) for a, b in zip(actual, expected))
    remaining = list(expected)
    for row in actual:
        match = next(
            (i for i, candidate in enumerate(remaining) if equal(row, candidate)), None
        )
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


def final_query_result(result):
    calls = {}
    last = None
    for message in result["messages"]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["name"] == "run_sql":
                    calls[call["id"]] = call["args"]["sql"]
        elif isinstance(message, ToolMessage) and message.tool_call_id in calls:
            last = (calls[message.tool_call_id], json.loads(message.content))
    return last


async def evaluate(cases, modes):
    worker = RetrievalWorker()
    records = []
    from request_state import initial

    try:
        await open_async_pool()
        for mode in modes:
            graph = build_graph(worker, retrieval_mode=mode)
            for case in cases:
                started = time.perf_counter()
                record = {
                    "id": case["id"],
                    "category": case["category"],
                    "mode": mode,
                    "passed": False,
                }
                try:
                    expected = await asyncio.to_thread(
                        execute_raw, case["reference_sql"]
                    )
                    # Do not include oracle DB time in agent latency.
                    started = time.perf_counter()
                    result = await asyncio.wait_for(
                        graph.ainvoke(
                            initial(case["question"]), config={"recursion_limit": 20}
                        ),
                        timeout=90,
                    )
                    record["citation_integrity"] = citations_valid(
                        result.get("structured_answer", {})
                    )
                    final = final_query_result(result)
                    record.update(
                        tokens=result.get("tokens", 0),
                        tool_calls=result.get("tool_count", 0),
                        trace=result.get("trace", []),
                    )
                    if final:
                        sql, data = final
                        record["sql"] = sql
                        record["passed"] = bool(
                            data.get("ok")
                            and not data.get("truncated")
                            and rows_equal(
                                data["rows"], expected, case.get("ordered", False)
                            )
                        )
                except Exception as exc:
                    record["error"] = type(exc).__name__
                record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                records.append(record)
                print(f"{mode} {case['id']}: {'PASS' if record['passed'] else 'FAIL'}")
    finally:
        await close_async_pool()
        await asyncio.to_thread(worker.close)
        close_pool()
        from rag.ingest import close_vector_store
        from rag.retrieve import cached_catalog

        close_vector_store()
        cached_catalog.cache_clear()
    summary = {}
    for mode in modes:
        group = [r for r in records if r["mode"] == mode]
        latencies = sorted(r["latency_ms"] for r in group)
        summary[mode] = {
            "cases": len(group),
            "execution_accuracy": sum(r["passed"] for r in group) / len(group),
            "p95_ms": latencies[max(0, math.ceil(len(group) * 0.95) - 1)],
            "total_tokens": sum(r.get("tokens", 0) for r in group),
            "citation_integrity_rate": sum(
                r.get("citation_integrity", False) for r in group
            )
            / len(group),
        }
    return {
        "metric": "final SQL execution equivalence; not natural-language answer correctness",
        "summary": summary,
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=["none", "vector", "hybrid", "all"], default="hybrid"
    )
    parser.add_argument("--output", default="eval-results.json")
    parser.add_argument(
        "--cases", default=str(Path(__file__).parent / "evaluation/cases.json")
    )
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    modes = ["none", "vector", "hybrid"] if args.mode == "all" else [args.mode]
    report = asyncio.run(evaluate(cases, modes))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if any(s["execution_accuracy"] < 1 for s in report["summary"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
