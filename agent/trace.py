"""可观测性：把 agent 的每一步（LLM 决策 + 工具执行）记成结构化事件。

三个排查问题，一个结构全答：
1. 错在哪一步？  —— 看每个事件的 tool / arguments / result
2. 慢在哪一步？  —— 看 latency_ms
3. 烧了多少 token？—— 看 usage

为什么要「结构化 dict」而不是 print：
print 是给人看的，机器没法筛选、统计；结构化之后能存 JSON、
进日志系统、做面板、写告警，也能喂给 eval 复现问题。
"""
import json
from datetime import datetime, timezone


class Trace:
    """一次 agent 交互的完整轨迹。事件是扁平 dict，字段见各 record 调用处。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.on_event = None  # 每次 record 时同步回调，供 SSE 流式推送

    def record(self, **event) -> None:
        """记一个事件。统一补时间戳，谁调用都不会漏。"""
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    def to_json(self) -> str:
        """导出成 JSON，方便存文件或接日志系统。"""
        return json.dumps(self.events, ensure_ascii=False, indent=2)

    def summary(self) -> str:
        """人读的总结：几次 LLM / 工具调用、token 用量。"""
        llm_events = [e for e in self.events if e["type"] == "llm"]
        tool_events = [e for e in self.events if e["type"] == "tool"]
        prompt = sum((e.get("usage") or {}).get("prompt_tokens", 0) for e in llm_events)
        completion = sum((e.get("usage") or {}).get("completion_tokens", 0) for e in llm_events)
        return (
            f"LLM 调用 {len(llm_events)} 次、工具调用 {len(tool_events)} 次；"
            f"tokens: prompt={prompt}, completion={completion}"
        )
