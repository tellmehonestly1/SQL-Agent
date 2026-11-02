"""命令行测试 WebSocket 端点（流式接收 agent 每一步）。

运行前先启动服务（sql_agent 目录，另开一个终端）：
    uvicorn api:app --reload

再在本目录跑：
    python test_ws.py
"""
import asyncio
import json
import os

import websockets

WS_URL = os.environ.get("TEST_WS_URL", "ws://127.0.0.1:8000/ws")


async def main() -> None:
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"question": "用户总数是多少"}))
        print("已发送问题，等待流式返回...\n")
        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")
            if t == "llm":
                print(f"  [llm] 模型决策，要调用工具: {msg.get('tool_calls')}")
            elif t == "tool":
                print(f"  [tool] 执行 {msg.get('tool')}，耗时 {msg.get('latency_ms')}ms")
            elif t in ("done", "cancelled"):
                print(f"\n  [{t}] {msg.get('answer', '')}")
                break


if __name__ == "__main__":
    asyncio.run(main())
