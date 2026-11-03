import asyncio
import pytest
from langchain_core.messages import AIMessage
from conversations import ConversationStore
from analysis_service import AnalysisService
from schemas import QueryRequest


class FakeGraph:
    def __init__(self):
        self.seen = []
        self.fail = False
        self.block = False
        self.started = asyncio.Event()

    async def ainvoke(self, state, config):
        self.seen.append(state)
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        if self.fail:
            raise RuntimeError("failure")
        return {
            "messages": [AIMessage(content="rendered")],
            "context": "schema",
            "structured_answer": {"status": "completed", "answer": "rendered"},
            "trace": [],
        }


def test_clarification_skips_llm_and_followup_uses_fresh_evidence(tmp_path):
    async def run():
        async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
            graph = FakeGraph()
            service = AnalysisService(graph, store)
            sid = (await store.create("alice"))["session_id"]
            first = await service.run(
                QueryRequest(question="销售额是多少", session_id=sid), "alice"
            )
            assert first["status"] == "clarification_required" and graph.seen == []
            await service.run(
                QueryRequest(
                    question="按订单金额，全部时间",
                    session_id=sid,
                    metric_id="order_amount",
                    period="all_time",
                ),
                "alice",
            )
            await service.run(
                QueryRequest(question="只看上个月", session_id=sid), "alice"
            )
            assert len(graph.seen) == 2
            assert graph.seen[-1]["semantics"]["period"] == "last_month"
            assert len(graph.seen[-1]["messages"]) == 3
            assert all(m.type != "tool" for m in graph.seen[-1]["messages"])
            assert graph.seen[-1]["tool_count"] == 0

    asyncio.run(run())


def test_failed_and_cancelled_turns_do_not_overwrite_history(tmp_path):
    async def run():
        async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
            graph = FakeGraph()
            service = AnalysisService(graph, store)
            sid = (await store.create("alice"))["session_id"]
            await service.run(
                QueryRequest(question="用户总数", session_id=sid), "alice"
            )
            graph.fail = True
            with pytest.raises(RuntimeError):
                await service.run(
                    QueryRequest(question="用户总数", session_id=sid), "alice"
                )
            graph.fail = False
            graph.block = True
            graph.started.clear()
            task = asyncio.create_task(
                service.run(QueryRequest(question="用户总数", session_id=sid), "alice")
            )
            await graph.started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            state = await store.checkout(sid, "alice")
            assert len(state["history"]) == 1
            await store.release(sid)

    asyncio.run(run())


def test_same_session_cannot_run_two_turns_concurrently(tmp_path):
    from conversations import SessionError

    async def run():
        async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
            graph = FakeGraph()
            graph.block = True
            service = AnalysisService(graph, store)
            sid = (await store.create("alice"))["session_id"]
            task = asyncio.create_task(
                service.run(QueryRequest(question="用户总数", session_id=sid), "alice")
            )
            await graph.started.wait()
            with pytest.raises(SessionError, match="session_busy"):
                await service.run(
                    QueryRequest(question="订单总额", session_id=sid), "alice"
                )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
