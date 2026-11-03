"""FastAPI 服务（v2）：把 LangGraph agent 包成 HTTP + WebSocket。

两个端点：
- POST /query   一次性：返回答案 + 检索到的上下文
- WS   /ws      WebSocket：流式推每一步（retrieve/agent/tools），支持取消

对比 v1：agent 从手写循环换成 LangGraph，但 WebSocket 的「三任务 + 取消」
架构不变——run_agent 用 graph.astream 流式产出，forward 消费推前端，watch 监听取消。
"""
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from graph.graph import build_graph

app = FastAPI(title="SQL 数据分析 Agent v2")


class QueryRequest(BaseModel):
    question: str


def _summarize(chunk: dict) -> dict:
    """把 astream 的一步更新转成可 JSON 化的摘要，推给前端。"""
    for node, update in chunk.items():
        if node == "retrieve":
            return {"node": "retrieve", "detail": (update.get("context") or "")[:200]}
        if node == "tools":
            msgs = update.get("messages", [])
            detail = getattr(msgs[-1], "content", "") if msgs else ""
            return {"node": "tools", "detail": str(detail)[:200]}
        if node == "agent":
            msgs = update.get("messages", [])
            if not msgs:
                return {"node": "agent", "detail": ""}
            m = msgs[-1]
            names = [
                tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                for tc in (m.tool_calls or [])
            ]
            return {"node": "agent", "detail": m.content or "", "tool_calls": names}
    return {"node": "?", "detail": str(chunk)[:200]}


@app.post("/query")
async def query(req: QueryRequest) -> dict:
    graph = build_graph()
    result = await graph.ainvoke(
        {"question": req.question, "context": "", "messages": [HumanMessage(req.question)]}
    )
    return {
        "answer": result["messages"][-1].content,
        "context": result["context"],
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    data = await websocket.receive_json()   # 第一帧：{"question": "..."}
    question = data["question"]

    graph = build_graph()
    q: asyncio.Queue = asyncio.Queue()

    async def run_agent() -> None:
        try:
            async for chunk in graph.astream(
                {"question": question, "context": "", "messages": [HumanMessage(question)]},
                stream_mode="updates",
            ):
                await q.put(_summarize(chunk))
        except asyncio.CancelledError:
            await q.put({"node": "cancelled"})
        else:
            await q.put({"node": "done"})
        await q.put(None)  # 哨兵：结束流

    async def forward() -> None:
        while True:
            item = await q.get()
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except WebSocketDisconnect:
                break

    async def watch() -> None:
        try:
            while True:
                msg = await websocket.receive_json()
                if msg.get("action") == "cancel":
                    agent_task.cancel()
        except WebSocketDisconnect:
            agent_task.cancel()

    agent_task = asyncio.create_task(run_agent())
    forward_task = asyncio.create_task(forward())
    watch_task = asyncio.create_task(watch())

    await agent_task
    await forward_task
    watch_task.cancel()
    await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
