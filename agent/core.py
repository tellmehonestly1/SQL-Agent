"""Agent 核心循环（Step 9 起全面异步化）。

一句话理解 agent：LLM 只负责「决策」，你的代码负责「执行」。

async 化的意义：agent.run() 变成可 await 的协程，在事件循环里不再阻塞。
FastAPI 里直接 await 即可，之前「线程 + 队列」的 workaround 整个删掉。

工具并行执行：之前用 ThreadPoolExecutor，现在用 asyncio.gather + asyncio.to_thread
——后者把同步的工具执行丢进线程池，同时不阻塞事件循环。
"""
import asyncio
import json
import time

from .llm import LLM, LLMResponse
from .tools import ToolRegistry
from .trace import Trace


class Agent:
    def __init__(self, llm: LLM, tools: ToolRegistry, max_steps: int = 8) -> None:
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps  # 第一道闸：防止死循环
        self.trace = Trace()  # 结构化轨迹，排查/评测/流式推送都用它

    async def run(self, user_query: str, system_prompt: str = "你是一个数据分析助手。") -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]

        steps = 0
        tool_calls_count = 0
        last_call: tuple | None = None  # 上一次的工具调用，用于检测「原地打转」

        for _ in range(self.max_steps):
            steps += 1
            t0 = time.perf_counter()
            response: LLMResponse = await self.llm.chat(messages, self.tools.schemas())
            latency_ms = int((time.perf_counter() - t0) * 1000)

            # 记一条「LLM 决策」事件：这次模型决定调用哪些工具、耗时、token
            self.trace.record(
                type="llm",
                step=steps,
                latency_ms=latency_ms,
                usage=response.usage,
                tool_calls=[tc.name for tc in response.tool_calls],
            )

            # 分支 1：没有工具调用 → 最终答案
            if not response.tool_calls:
                return response.text or "（模型没有返回内容）"

            # 分支 2：把模型的工具调用请求写进历史（真实格式，带 id + type + function）
            messages.append({
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ],
            })

            # 重复调用检测：在真正执行前，先拦下「原地打转」
            for tc in response.tool_calls:
                this_call = (tc.name, json.dumps(tc.arguments, sort_keys=True))
                if this_call == last_call:
                    print(f"[step {steps}] 检测到重复调用 {tc.name}，停止循环")
                    return "检测到模型重复发起相同的工具调用，已停止。请换一种方式提问。"
                last_call = this_call

            # 并行执行所有工具：asyncio.gather + asyncio.to_thread
            # to_thread 把同步工具丢进线程池跑，不阻塞事件循环；gather 并发等全部完成
            t0 = time.perf_counter()
            results = await asyncio.gather(*[
                asyncio.to_thread(self.tools.execute, tc.name, tc.arguments)
                for tc in response.tool_calls
            ])
            latency_ms = int((time.perf_counter() - t0) * 1000)

            # 结果按顺序写回历史 + trace（顺序不能乱：tool_call_id 要和结果配对）
            for tc, result in zip(response.tool_calls, results):
                # 结果可能很长，trace 里只存截断版，避免爆内存
                preview = result if len(result) <= 300 else result[:300] + "...(截断)"
                self.trace.record(
                    type="tool",
                    step=steps,
                    tool=tc.name,
                    arguments=tc.arguments,
                    result=preview,
                    latency_ms=latency_ms,
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                tool_calls_count += 1
                print(f"[step {steps}] 模型请求调用 {tc.name}，参数: {tc.arguments}")
                print(f"[step {steps}] 工具返回:\n{preview}\n")

        print(f"本次共用了 {steps} 步、调用了 {tool_calls_count} 次工具")
        return "抱歉，这个问题我没能在限定步数内完成。"
