"""把 SQL 分析能力暴露成 MCP（Model Context Protocol）工具。

MCP 是「给 LLM 提供工具/资源/提示词」的行业标准协议（Anthropic 2024 提出）。
之前 Step 2-3 我们手写了 Tool + ToolRegistry + JSON schema + tool_call_id 配对；
MCP 就是把这件事标准化——写成标准 server，任何支持 MCP 的客户端都能连。

安全提醒：不管用哪套协议暴露，防线不能丢。这里复用 db.run_sql
（只读事务 + 超时 + 行数上限），再叠一层关键字黑名单。
"""
from mcp.server.mcpserver import MCPServer

from db import run_sql as _run_sql
from main import FORBIDDEN_SQL_KEYWORDS

mcp = MCPServer("sql-agent")


@mcp.tool()
def run_sql(sql: str) -> str:
    """执行一条只读 SQL 查询并返回结果。

    只允许 SELECT 等只读查询，DELETE/UPDATE/INSERT 等写操作会被拒绝。
    """
    upper = sql.upper()
    for kw in FORBIDDEN_SQL_KEYWORDS:
        if kw in upper:
            return f"拒绝执行：SQL 含禁止关键字 {kw}，只允许只读查询。"
    return _run_sql(sql)


if __name__ == "__main__":
    mcp.run()  # 默认走 stdio（标准输入输出），供 MCP 客户端调用
