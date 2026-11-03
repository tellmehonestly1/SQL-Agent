"""Both sync CLI and native async API use the same guarded executor."""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from db import arun_sql, run_sql


class RunSQLArgs(BaseModel):
    sql: str = Field(
        description="One PostgreSQL SELECT over permitted users/orders columns",
        min_length=1,
        max_length=8000,
    )


run_sql_tool = StructuredTool.from_function(
    func=run_sql,
    coroutine=arun_sql,
    name="run_sql",
    args_schema=RunSQLArgs,
    description="Execute one read-only SELECT. Returns JSON with ok, columns, rows, truncated or error.",
)
