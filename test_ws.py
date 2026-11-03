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

        answer = ""  # 最后一轮 agent 节点的 content 就是最终答案
        while True:
            msg = json.loads(await ws.recv())
            node = msg.get("node")

            if node == "retrieve":
                print(f"  [retrieve] 检索上下文: {msg.get('detail', '')[:80]}...")
            elif node == "agent":
                calls = msg.get("tool_calls") or []
                if calls:
                    print(f"  [agent] 模型决策，要调用工具: {calls}")
                if msg.get("detail"):
                    answer = msg["detail"]
            elif node == "tools":
                print(f"  [tools] 工具返回: {msg.get('detail', '')[:80]}...")
            elif node == "done":
                print(f"\n  [done] 最终答案: {answer}")
                break
            elif node == "cancelled":
                print("\n  [cancelled] 已取消")
                break


if __name__ == "__main__":
    asyncio.run(main())
