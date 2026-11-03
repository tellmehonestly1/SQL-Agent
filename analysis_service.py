"""One turn lifecycle shared by HTTP and WebSocket."""

import asyncio
from request_state import initial
from semantics import prepare_turn
from graph.nodes import AgentExecutionError


class AnalysisService:
    def __init__(self, graph, conversations):
        self.graph, self.conversations = graph, conversations

    async def run(self, req, owner, on_update=None):
        session_id = str(req.session_id) if req.session_id else None
        previous = (
            await self.conversations.checkout(session_id, owner)
            if session_id
            else {"history": [], "slots": {}, "pending": None}
        )
        try:
            if req.new_topic:
                previous = {"history": [], "slots": {}, "pending": None}
            plan = prepare_turn(req, previous)
            if plan["status"] == "clarification_required":
                if session_id:
                    await self.conversations.commit(
                        session_id, owner, {**previous, "pending": plan["pending"]}
                    )
                return {
                    "status": plan["status"],
                    "answer": plan["answer"],
                    "clarification": plan["clarification"],
                    "session_id": session_id,
                    "trace": [],
                }
            state = initial(
                plan["question"], previous.get("history"), plan["semantics"]
            )
            if on_update:
                result = {**state, "messages": list(state["messages"]), "trace": []}
                async for chunk in self.graph.astream(
                    state, config={"recursion_limit": 20}, stream_mode="updates"
                ):
                    for update in chunk.values():
                        for key, value in update.items():
                            if key in ("messages", "trace"):
                                result[key].extend(value)
                            else:
                                result[key] = value
                    await on_update(chunk)
            else:
                result = await self.graph.ainvoke(state, config={"recursion_limit": 20})
            payload = result.get("structured_answer")
            if payload is None:
                raise AgentExecutionError("missing_structured_answer")
            if session_id:
                history = previous.get("history", []) + [
                    {
                        "question": req.question,
                        "answer": payload["answer"][:1000],
                        "topic": plan["topic"],
                    }
                ]
                await self.conversations.commit(
                    session_id,
                    owner,
                    {"history": history, "slots": plan["slots"], "pending": None},
                )
            return {
                **payload,
                "context": result.get("context", ""),
                "trace": result.get("trace", []),
                "session_id": session_id,
            }
        finally:
            if session_id:
                task = asyncio.create_task(self.conversations.release(session_id))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
