import copy
import json
from langchain_core.messages import AIMessage, ToolMessage
from provenance import evidence_answer, citations_valid
from graph.nodes import _after_agent


def state(rows=None):
    data = {
        "ok": True,
        "columns": ["total"],
        "rows": rows if rows is not None else [["599.20"]],
        "truncated": False,
        "queried_at": "2026-11-04T00:00:00Z",
    }
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "one",
                        "name": "run_sql",
                        "args": {"sql": "SELECT SUM(amount) FROM orders"},
                    }
                ],
            ),
            ToolMessage(content=json.dumps(data), tool_call_id="one"),
        ]
    }


def test_answer_replaces_invented_model_numbers_with_cells():
    result = _after_agent(state(), AIMessage(content="收入999999元，增长200%"), 0)
    answer = result["structured_answer"]
    assert "999999" not in answer["answer"] and "200%" not in answer["answer"]
    assert "599.20" in answer["answer"]
    assert answer["sources"][0]["queried_at"]
    assert citations_valid(answer)


def test_tampering_is_detected():
    answer = evidence_answer(state())
    changed = copy.deepcopy(answer)
    changed["data"]["rows"][0][0] = "999"
    assert not citations_valid(changed)
    changed = copy.deepcopy(answer)
    changed["citations"][0]["value"] = "999"
    assert not citations_valid(changed)


def test_empty_null_and_preview_keep_actual_data():
    empty = evidence_answer(state([]))
    assert empty["data"]["rows"] == [] and empty["citations"] == []
    null = evidence_answer(state([[None]]))
    assert null["citations"][0]["value"] is None and "不等同于0" in null["answer"]
    many = evidence_answer(state([[i] for i in range(20)]))
    assert len(many["data"]["rows"]) == 20 and len(many["citations"]) == 10


def test_missing_citations_or_extra_claims_are_rejected():
    answer = evidence_answer(state())
    changed = copy.deepcopy(answer)
    changed["citations"] = []
    assert not citations_valid(changed)
    changed = copy.deepcopy(answer)
    changed["answer"] += "另外收入999元"
    assert not citations_valid(changed)
