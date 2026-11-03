"""Real-model evaluation of clarification, follow-ups and citation consistency."""

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from analysis_service import AnalysisService
from conversations import ConversationStore
from db import open_async_pool, close_async_pool, close_pool, execute_raw
from eval import rows_equal
from graph.graph import build_graph
from graph.nodes import RetrievalWorker
from provenance import citations_valid
from rag.ingest import close_vector_store
from rag.retrieve import cached_catalog
from schemas import QueryRequest


async def evaluate(episodes, mode):
    records = []
    worker = RetrievalWorker()
    try:
        await open_async_pool()
        graph = build_graph(worker, retrieval_mode=mode)
        with tempfile.TemporaryDirectory(prefix="sqlagent-eval-") as folder:
            async with ConversationStore.open(
                Path(folder) / "sessions.sqlite"
            ) as store:
                service = AnalysisService(graph, store)
                for episode in episodes:
                    sid = (await store.create("evaluation"))["session_id"]
                    for index, turn in enumerate(episode["turns"], 1):
                        record = {
                            "episode": episode["id"],
                            "turn": index,
                            "passed": False,
                        }
                        started = time.perf_counter()
                        try:
                            req = QueryRequest(**turn["request"], session_id=sid)
                            answer = await asyncio.wait_for(
                                service.run(req, "evaluation"), timeout=90
                            )
                            record["status"] = answer["status"]
                            if turn["expected_status"] == "clarification_required":
                                fields = {
                                    x["field"] for x in answer.get("clarification", [])
                                }
                                record["passed"] = answer["status"] == turn[
                                    "expected_status"
                                ] and fields == set(turn["expected_fields"])
                            elif answer["status"] == "completed":
                                expected = await asyncio.to_thread(
                                    execute_raw, turn["reference_sql"]
                                )
                                record["execution_correct"] = rows_equal(
                                    answer["data"]["rows"],
                                    expected,
                                    turn.get("ordered", False),
                                )
                                record["citation_integrity"] = citations_valid(answer)
                                record["passed"] = (
                                    record["execution_correct"]
                                    and record["citation_integrity"]
                                )
                                record["sql"] = answer["sources"][0]["sql"]
                            record["trace"] = answer.get("trace", [])
                        except Exception as exc:
                            record["error"] = type(exc).__name__
                        record["latency_ms"] = round(
                            (time.perf_counter() - started) * 1000, 2
                        )
                        records.append(record)
                    await store.delete(sid, "evaluation")
    finally:
        await close_async_pool()
        await asyncio.to_thread(worker.close)
        close_pool()
        close_vector_store()
        cached_catalog.cache_clear()
    return {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "retrieval_mode": mode,
        "registry_version": "2.2.0",
        "metric_note": "Citation integrity is not proof of correct SQL/business semantics",
        "turns": len(records),
        "passed": sum(r["passed"] for r in records),
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=["none", "vector", "hybrid"], default="hybrid"
    )
    parser.add_argument(
        "--cases", default=str(Path(__file__).parent / "evaluation/conversations.json")
    )
    parser.add_argument("--output", default="eval-results-conversations.json")
    args = parser.parse_args()
    if not os.getenv("DEEPSEEK_API_KEY"):
        parser.error("DEEPSEEK_API_KEY is not configured; no model calls were made")
    episodes = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = asyncio.run(evaluate(episodes, args.mode))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "records"},
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["passed"] != report["turns"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
