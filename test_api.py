"""命令行测试两个 API 端点（不需要浏览器，绕开 /docs 的 CDN 问题）。

运行前先启动服务（另开一个终端，在 sql_agent 目录）：
    uvicorn api:app --reload

再在本目录开另一个终端跑：
    python test_api.py
"""
import json
import os
import urllib.request

BASE = os.environ.get("TEST_API_BASE", "http://127.0.0.1:8000")


def post(path: str, data: dict):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req)


def test_query() -> None:
    print("=== POST /query ===")
    with post("/query", {"question": "用户总数是多少"}) as r:
        data = json.loads(r.read().decode("utf-8"))
    print("答案:", data["answer"])
    print("trace 事件数:", len(data["trace"]))


def test_stream() -> None:
    print("\n=== POST /query/stream（逐条收到）===")
    with post("/query/stream", {"question": "用户总数是多少"}) as r:
        print("content-type:", r.headers.get("content-type"))
        for raw in r:  # 逐行读，这就是「流式」：收到一行立刻处理，不攒到最后
            line = raw.decode("utf-8").strip()
            if line.startswith("data: "):
                print("收到:", line[6:])  # 去掉 "data: " 前缀


if __name__ == "__main__":
    test_query()
    test_stream()
