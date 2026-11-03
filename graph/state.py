"""All budgets and traces are request-local; the compiled graph is reusable."""

import operator
from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    question: str
    context: str
    messages: Annotated[list[BaseMessage], add_messages]
    tool_count: int
    repair_count: int
    tokens: int
    trace: Annotated[list[dict], operator.add]

    semantics: dict
    structured_answer: dict
