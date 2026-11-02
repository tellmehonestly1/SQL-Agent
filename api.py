"""FastAPI 服务：把 agent 包成 HTTP + WebSocket 接口（Step 9 起全面异步化）。

两个端点：
- POST /query     一次性：返回答案 + 完整 trace（调试/评测用）
- WS   /ws        WebSocket：流式推每一步，且支持客户端发「cancel」中途取消

为什么从 SSE 换成 WebSocket：SSE 是单向的，客户端只能收不能发。
agent 场景有个真实需求——用户看到中间步骤错了，想「点取消」打断 agent。
这需要双向通信，只有 WebSocket 能在一个连接里既收（步骤）又发（取消）。

运行：在 sql_agent 目录下
    uvicorn api:app --reload
"""
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from main import build_agent, build_system_prompt

app = FastAPI(title="SQL 数据分析 Agent")


class QueryRequest(BaseModel):
    question: str


@app.post("/query")
async def query(req: QueryRequest) -> dict:
    """一次性：返回最终答案 + 完整 trace。agent.run 已是协程，直接 await。"""
    agent = build_agent()
    answer = await agent.run(req.question, system_prompt=build_system_prompt())
    return {"answer": answer, "trace": agent.trace.events}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    data = await websocket.receive_json()   # 第一帧：客户端发 {"question": "..."}
    question = data["question"]

    agent = build_agent()
    q: asyncio.Queue = asyncio.Queue()
    agent.trace.on_event = q.put_nowait  # 每步事件塞进异步队列（单线程，put_nowait 安全）

    async def run_agent() -> None:
        try:
            answer = await agent.run(question, system_prompt=build_system_prompt())
        except asyncio.CancelledError:
            await q.put({"type": "cancelled"})   # 被取消：通知前端
        else:
            await q.put({"type": "done", "answer": answer})
        await q.put(None)  # 哨兵：正常/取消两条路都会走到，结束流

    async def forward() -> None:
        # 从队列取事件推给前端，拿到哨兵（None）就结束
        while True:
            item = await q.get()
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except WebSocketDisconnect:
                break

    async def watch() -> None:
        # 监听客户端消息：收到 cancel 就取消正在跑的 agent
        try:
            while True:
                msg = await websocket.receive_json()
                if msg.get("action") == "cancel":
                    agent_task.cancel()
        except WebSocketDisconnect:
            agent_task.cancel()   # 前端断开也取消

    agent_task = asyncio.create_task(run_agent())   # 后台跑 agent
    forward_task = asyncio.create_task(forward())   # 并发推事件
    watch_task = asyncio.create_task(watch())       # 并发监听取消

    await agent_task       # run_agent 内部消化了取消，正常返回
    await forward_task     # 拿到哨兵自然退出
    watch_task.cancel()    # 停掉还在等客户端消息的 watch
    await websocket.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
