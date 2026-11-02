"""LLM 统一接口 + 两种实现（Step 9 起全面异步化）。

- LLM: 抽象接口，给「消息历史 + 工具」返回「文本 或 工具调用请求」。
- OpenAICompatLLM: 真实实现，走 OpenAI 兼容接口的【异步】客户端 AsyncOpenAI。
- MockLLM: 假实现，无 API key 也能跑通循环，用于离线测试。

一个贯穿全程的原则：模型输出的东西（包括工具参数）都不可信，
所以真实实现里要把 arguments（JSON 字符串）解析成 dict，后续还会校验。
"""
import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """模型发起的一次工具调用请求。

    id 是真实 API 里每条 tool_call 的唯一标识，用来和工具结果配对。
    """
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None  # token 用量，用于成本/观测


class LLM:
    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatLLM(LLM):
    """OpenAI 兼容接口实现（异步），默认指向 DeepSeek。

    环境变量：
      DEEPSEEK_API_KEY  —— 必填
      DEEPSEEK_BASE_URL —— 默认 https://api.deepseek.com
      DEEPSEEK_MODEL    —— 默认 deepseek-chat
    """

    MAX_RETRIES = 3      # 最多重试 3 次（加上首次共 4 次尝试）
    BASE_DELAY = 1.0     # 首次重试前等 1 秒，之后指数翻倍

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        from openai import (  # 延迟导入：没装 openai 也能 import 本模块
            AsyncOpenAI,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        self._client = AsyncOpenAI(
            api_key=api_key or os.environ["DEEPSEEK_API_KEY"],
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        self._model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

        # 只有「临时性」错误才值得重试；401/400 之类重试也白搭
        self._retryable = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        last_err: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                )
                msg = resp.choices[0].message
                tool_calls = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        # arguments 是【JSON 字符串】，必须解析成 dict
                        tool_calls.append(ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=json.loads(tc.function.arguments or "{}"),
                        ))
                # 顺手记下 token 用量，供 trace 算成本
                usage = None
                if resp.usage:
                    usage = {
                        "prompt_tokens": resp.usage.prompt_tokens,
                        "completion_tokens": resp.usage.completion_tokens,
                        "total_tokens": resp.usage.total_tokens,
                    }
                return LLMResponse(text=msg.content, tool_calls=tool_calls, usage=usage)
            except self._retryable as e:
                last_err = e
                if attempt < self.MAX_RETRIES:
                    delay = self.BASE_DELAY * (2 ** attempt)  # 1s, 2s, 4s ... 指数退避
                    print(f"[llm] 调用失败（{type(e).__name__}），{delay:.1f}s 后重试")
                    await asyncio.sleep(delay)  # 异步世界里用 asyncio.sleep，不是 time.sleep
        raise last_err  # 重试耗尽仍失败，抛出交给上层处理


class MockLLM(LLM):
    """离线假模型：固定剧本，用于不联网跑通循环、写测试。"""

    def __init__(self) -> None:
        self._calls = 0

    async def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self._calls += 1
        if self._calls == 1:
            return LLMResponse(tool_calls=[
                ToolCall(id="call_1", name="run_sql", arguments={"sql": "SELECT count(*) FROM users"})
            ])
        return LLMResponse(text="用户总数是 42。")
