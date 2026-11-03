"""LangChain 的 LLM 封装：用 ChatOpenAI 走 DeepSeek 的 OpenAI 兼容接口。

v1 是手写 AsyncOpenAI + 指数退避重试；v2 换成 LangChain 的 ChatOpenAI：
- 重试（max_retries）由 SDK 内部处理，不用自己写 1s→2s→4s 退避循环
- temperature 设 0：agent 决策要稳定，不需要发散

环境变量：
  DEEPSEEK_API_KEY   —— 必填
  DEEPSEEK_BASE_URL  —— 默认 https://api.deepseek.com
  DEEPSEEK_MODEL     —— 默认 deepseek-chat
"""

import os

from langchain_openai import ChatOpenAI


def build_chat_model() -> ChatOpenAI:
    """DeepSeek 提供 OpenAI 兼容接口，用 base_url 指向它即可复用 ChatOpenAI。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        # 没 key 就立刻报错，别让 SDK 发一个没鉴权的请求然后干等超时
        raise RuntimeError(
            "未设置 DEEPSEEK_API_KEY。请先 export / set DEEPSEEK_API_KEY=sk-xxx 再运行。"
        )
    return ChatOpenAI(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        max_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "1024")),
        temperature=0,  # agent 决策要稳定：温度低，输出更确定
        max_retries=3,  # SDK 层自动重试临时性错误，替代 v1 手写的退避
        timeout=60,  # 请求超时：网络慢/被墙就 60 秒报错，别默认挂 10 分钟
    )
