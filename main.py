import asyncio
import os

from pydantic import BaseModel, field_validator, Field

from agent.llm import MockLLM, OpenAICompatLLM
from agent.tools import Tool, ToolRegistry
from agent.core import Agent
from db import run_sql, get_schema


FORBIDDEN_SQL_KEYWORDS = ("DELETE", "DROP", "INSERT", "UPDATE", "ALTER", "TRUNCATE")


class RunSQLArgs(BaseModel):
    """run_sql 工具的入参模型：既给模型生成 schema，又用来校验参数。"""
    sql: str = Field(max_length=500)

    @field_validator("sql")
    @classmethod
    def no_write_ops(cls, v: str) -> str:
        """拒绝任何含写操作关键字的 SQL。"""
        upper = v.upper()
        for kw in FORBIDDEN_SQL_KEYWORDS:
            if kw in upper:
                raise ValueError(f"只允许只读查询，SQL 里包含禁止关键字：{kw}")
        return v


def build_system_prompt() -> str:
    """动态读取数据库 schema，拼进 system prompt。

    比硬编码好的地方：表结构变了这里自动跟上，不用改代码。
    """
    schema = get_schema()
    return (
        "你是一个数据分析助手。你只能用 run_sql 工具执行只读查询。\n"
        f"数据库里有这些表：\n{schema}\n"
        "流程：先查数据，再基于【真实查询结果】用中文回答，不要凭空编造数字。"
    )


def build_agent() -> Agent:
    tools = ToolRegistry([
        Tool(
            name="run_sql",
            description="执行一条只读 SQL 查询并返回结果",
            args_model=RunSQLArgs,
            func=run_sql,
        ),
    ])

    # 有 key 用真实模型（DeepSeek），没 key 退回 Mock，方便离线跑
    if os.environ.get("DEEPSEEK_API_KEY"):
        llm = OpenAICompatLLM()
    else:
        llm = MockLLM()

    return Agent(llm=llm, tools=tools, max_steps=5)


async def main() -> None:
    agent = build_agent()
    answer = await agent.run("每个用户的订单总额分别是多少", system_prompt=build_system_prompt())
    print(answer)

    # Step 7：跑完顺手输出一份可观测总结 + 导出完整 trace
    print("\n--- trace 总结 ---")
    print(agent.trace.summary())
    with open("trace.json", "w", encoding="utf-8") as f:
        f.write(agent.trace.to_json())
    print("完整 trace 已写入 trace.json（排查 / 进日志系统用）")


if __name__ == "__main__":
    asyncio.run(main())
