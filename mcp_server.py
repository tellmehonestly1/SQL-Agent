"""MCP shares the exact same SQL execution boundary as the agent."""

from mcp.server.mcpserver import MCPServer
from db import run_sql as execute

mcp = MCPServer("sql-agent", version="2.2.0")


@mcp.tool()
def run_sql(sql: str) -> str:
    """Execute a guarded read-only SELECT and return JSON."""
    return execute(sql)


if __name__ == "__main__":
    mcp.run()
