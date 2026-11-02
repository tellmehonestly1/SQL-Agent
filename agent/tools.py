"""工具：agent 能力的抽象，以及「可信边界」所在。

一个工具 = 名字 + 描述 + 入参校验模型（Pydantic）+ 真实函数。
模型只能看到 name/description/schema，永远看不到 func。
执行前必须：解析参数 → Pydantic 校验 → 通过才执行。这就是安全的第一道闸。
"""
import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]  # 入参校验模型，同时用于生成给模型看的 schema
    func: Callable[..., Any]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._by_name = {t.name: t for t in tools}

    def schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._by_name.values()]

    def execute(self, name: str, arguments: dict) -> str:
        tool = self._by_name.get(name)
        if tool is None:
            return f"错误：未知工具 {name}"

        # 第 1 步：解析。模型可能给 JSON 字符串，解析成 dict
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return f"错误：工具参数不是合法 JSON: {arguments!r}"

        # 第 2 步：校验。参数是「不可信输入」，必须过 Pydantic 这关
        try:
            args = tool.args_model.model_validate(arguments)
        except ValidationError as e:
            return f"错误：工具参数不合法，已拒绝执行。{e}"

        # 第 3 步：执行。校验通过才真正调用真实函数
        try:
            return str(tool.func(**args.model_dump()))
        except Exception as e:
            return f"工具执行出错：{e}"
