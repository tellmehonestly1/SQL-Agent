"""CLI uses the same clarification, conversation and evidence pipeline as HTTP."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from analysis_service import AnalysisService
from conversations import ConversationStore
from db import open_async_pool, close_async_pool, close_pool
from graph.graph import build_graph
from graph.nodes import RetrievalWorker
from rag.ingest import close_vector_store
from rag.retrieve import cached_catalog
from schemas import QueryRequest


async def run(args):
    worker = RetrievalWorker()
    try:
        await open_async_pool()
        graph = build_graph(worker)
        path = Path(os.getenv("CLI_SESSION_DATABASE_PATH", ".data/cli-sessions.sqlite"))
        async with ConversationStore.open(path) as store:
            session_id = (
                args.session_id or (await store.create("cli-local"))["session_id"]
            )
            service = AnalysisService(graph, store)
            print("session_id:", session_id)
            question = args.question
            while True:
                result = await asyncio.wait_for(
                    service.run(
                        QueryRequest(question=question, session_id=session_id),
                        "cli-local",
                    ),
                    timeout=90,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if not args.chat:
                    break
                question = await asyncio.to_thread(input, "追问（输入 exit 退出）：")
                if question.strip().lower() in ("exit", "quit"):
                    break
    finally:
        await close_async_pool()
        await asyncio.to_thread(worker.close)
        close_pool()
        close_vector_store()
        cached_catalog.cache_clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default="每个用户的订单总额分别是多少")
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
