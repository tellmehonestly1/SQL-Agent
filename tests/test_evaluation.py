import json
from decimal import Decimal
from pathlib import Path
from langchain_core.messages import AIMessage, ToolMessage
from eval import rows_equal, final_query_result
from sql_policy import validate_sql


def test_no_scalar_coincidence():
    assert not rows_equal([[1], [2], [3]], [(3,)])
    assert not rows_equal([[3, 999]], [(3,)])


def test_preserves_duplicates_null_and_order():
    assert not rows_equal([[1], [2]], [(1,), (1,)])
    assert rows_equal([[None], [1]], [(1,), (None,)])
    assert not rows_equal([[None]], [(0,)])
    assert not rows_equal([[2], [1]], [(1,), (2,)], ordered=True)
    assert rows_equal([[2], [1]], [(1,), (2,)])


def test_decimal_tolerance_without_changing_text_identity():
    assert rows_equal([["119.300000"]], [(Decimal("119.30"),)])
    assert not rows_equal([["001"]], [("1",)])
    assert not rows_equal([["NaN"]], [(Decimal("1"),)])


def test_final_execution_only():
    result = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "a", "name": "run_sql", "args": {"sql": "SELECT 3"}}
                ],
            ),
            ToolMessage(
                content=json.dumps({"ok": True, "rows": [[3]]}), tool_call_id="a"
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "b", "name": "run_sql", "args": {"sql": "SELECT 4"}}
                ],
            ),
            ToolMessage(
                content=json.dumps({"ok": True, "rows": [[4]]}), tool_call_id="b"
            ),
        ]
    }
    sql, data = final_query_result(result)
    assert sql == "SELECT 4" and data["rows"] == [[4]]


def test_all_reference_queries_pass_policy_and_chinese_survives():
    cases = json.loads(
        (Path(__file__).parents[1] / "evaluation/cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(cases) == 30
    assert "用户" in cases[0]["question"]
    for case in cases:
        validate_sql(case["reference_sql"])
