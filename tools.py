"""LangChain 工具：把 run_sql 包成标准 Tool，保留 4 层安全防御。

v1 里 Tool 是手写的类（name + description + args_model + func）；
v2 用 LangChain 的 @tool，职责一样，但能直接和 ChatOpenAI.bind_tools() 配合。

安全边界不变：模型只看到 name/description/schema，永远看不到函数体；
入参必须先过 Pydantic 校验（第 4 层关键字黑名单）才会执行。
"""
from pydantic import BaseModel, Field, field_validator
from langchain_core.tools import tool

from db import run_sql


FORBIDDEN_SQL_KEYWORDS = ("DELETE", "DROP", "INSERT", "UPDATE", "ALTER", "TRUNCATE")


class RunSQLArgs(BaseModel):
    """run_sql 入参：既生成给模型看的 schema，又用来校验参数。"""
    sql: str = Field(description="要执行的只读 SQL 查询", max_length=500)

    @field_validator("sql")
    @classmethod
    def no_write_ops(cls, v: str) -> str:
        """第 4 层防御：拒绝任何含写操作关键字的 SQL。"""
        upper = v.upper()
        for kw in FORBIDDEN_SQL_KEYWORDS:
            if kw in upper:
                raise ValueError(f"只允许只读查询，SQL 里包含禁止关键字：{kw}")
        return v


@tool("run_sql", args_schema=RunSQLArgs)
def run_sql_tool(sql: str) -> str:
    """执行一条只读 SQL 查询，返回格式化后的结果文本。只支持 SELECT 类只读语句。"""
    return run_sql(sql)
