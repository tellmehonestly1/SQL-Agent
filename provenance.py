"""Render result claims from database cells, never from model-authored numbers."""

import hashlib
import json
from langchain_core.messages import AIMessage, ToolMessage
from sql_policy import validate_sql


def render_cells(columns, rows):
    citations = []
    lines = []
    for row_index, row in enumerate(rows[:10]):
        cells = []
        for column_index, value in enumerate(row[:10]):
            citation_id = f"q1:r{row_index}:c{column_index}"
            citations.append(
                {
                    "id": citation_id,
                    "query_id": "q1",
                    "row": row_index,
                    "column": column_index,
                    "value": value,
                }
            )
            display = "NULL（空值，不等同于0）" if value is None else str(value)
            if len(display) > 160:
                display = display[:160] + "…（完整值见 data）"
            cells.append(f"{columns[column_index]}={display} [{citation_id}]")
        lines.append("；".join(cells))
    answer = (
        "查询完成，未找到符合条件的数据。"
        if not rows
        else "查询结果：\n" + "\n".join(lines)
    )
    if len(rows) > 10 or len(columns) > 10:
        answer += "\n这里只展示前10行、前10列，完整结果见 data。"
    return answer, citations


def evidence_answer(state):
    calls = {}
    evidence = None
    for message in state["messages"]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["name"] == "run_sql":
                    calls[call["id"]] = call["args"]["sql"]
        elif isinstance(message, ToolMessage) and message.tool_call_id in calls:
            evidence = (
                message.tool_call_id,
                calls[message.tool_call_id],
                json.loads(message.content),
            )
    if evidence is None:
        raise ValueError("missing_query_evidence")
    _, sql, data = evidence
    if not data.get("ok") or data.get("truncated"):
        raise ValueError("incomplete_query_evidence")
    columns, rows = data["columns"], data["rows"]
    digest = hashlib.sha256(
        json.dumps(
            [columns, rows], ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()
    answer, citations = render_cells(columns, rows)
    return {
        "status": "completed",
        "answer": answer,
        "data": {"columns": columns, "rows": rows, "truncated": False},
        "sources": [
            {
                "query_id": "q1",
                "sql": validate_sql(sql),
                "queried_at": data.get("queried_at"),
                "result_digest": digest,
                "row_count": len(rows),
            }
        ],
        "citations": citations,
        "semantics": state.get("semantics", {}),
        "answer_mode": "database_rendered",
    }


def citations_valid(answer):
    try:
        data = answer["data"]
        digest = hashlib.sha256(
            json.dumps(
                [data["columns"], data["rows"]],
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        text, citations = render_cells(data["columns"], data["rows"])
        return (
            not data["truncated"]
            and answer["sources"][0]["query_id"] == "q1"
            and digest == answer["sources"][0]["result_digest"]
            and answer["answer"] == text
            and answer["citations"] == citations
        )
    except (KeyError, IndexError, TypeError):
        return False
