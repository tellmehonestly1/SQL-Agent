"""Fresh execution state: conversation context never supplies old SQL evidence."""

from langchain_core.messages import HumanMessage, AIMessage


def initial(question, history=None, semantics=None):
    messages = []
    for turn in (history or [])[-8:]:
        messages.extend(
            [HumanMessage(turn["question"][:2000]), AIMessage(turn["answer"][:1000])]
        )
    messages.append(HumanMessage(question))
    return {
        "question": question,
        "context": "",
        "messages": messages,
        "tool_count": 0,
        "repair_count": 0,
        "tokens": 0,
        "trace": [],
        "semantics": semantics or {},
    }
