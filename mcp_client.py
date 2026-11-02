"""演示：用一个 MCP 客户端连上我们的 MCP server，调用 run_sql 工具。

这就是 MCP 的价值：client 不需要知道 server 内部怎么实现，
只要通过标准协议就能发现工具（list_tools）并调用（call_tool）。
"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    # 通过 stdio 启动 server 子进程并连接（用当前 Python，避免路径不一致）
    server = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. 发现工具：client 不用预先知道有什么，问一下就知道
            tools = await session.list_tools()
            print("可用工具:", [t.name for t in tools.tools])

            # 2. 调用工具：参数是普通 dict，和 HTTP 一样简单
            result = await session.call_tool("run_sql", {"sql": "SELECT count(*) FROM users"})
            for item in result.content:
                print("结果:", item.text)


if __name__ == "__main__":
    asyncio.run(main())
