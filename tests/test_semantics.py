from datetime import date
from schemas import QueryRequest
from semantics import prepare_turn, resolve_period
import pytest
from pydantic import ValidationError


def test_revenue_requires_business_basis_and_time():
    plan = prepare_turn(QueryRequest(question="销售额是多少"))
    assert plan["status"] == "clarification_required"
    assert {x["field"] for x in plan["clarification"]} == {"metric_id", "period"}


def test_clarification_can_be_resolved_in_separate_turns():
    first = prepare_turn(QueryRequest(question="已支付收入是多少"))
    second = prepare_turn(
        QueryRequest(question="按订单金额", metric_id="order_amount"),
        {"pending": first["pending"]},
    )
    assert second["status"] == "clarification_required"
    final = prepare_turn(
        QueryRequest(question="全部时间", period="all_time"),
        {"pending": second["pending"]},
    )
    assert final["status"] == "ready"
    assert final["semantics"]["metric_id"] == "order_amount"
    assert "已支付收入" in final["question"]


def test_month_and_calendar_day_boundaries():
    assert resolve_period("last_month", date(2026, 1, 4)) == {
        "start": "2025-12-01",
        "end": "2026-01-01",
    }
    assert resolve_period("this_month", date(2024, 2, 29)) == {
        "start": "2024-02-01",
        "end": "2024-03-01",
    }
    assert resolve_period("last_7_days", date(2026, 11, 4)) == {
        "start": "2026-10-29",
        "end": "2026-11-05",
    }


def test_followup_preserves_metric_and_replaces_time_scope():
    conversation = {
        "history": [{"question": "订单总额", "answer": "old", "topic": "订单总额"}],
        "slots": {
            "metric_id": "order_amount",
            "period": "all_time",
            "date_range": None,
        },
    }
    plan = prepare_turn(
        QueryRequest(question="只看上个月"), conversation, today=date(2026, 11, 4)
    )
    assert plan["semantics"]["metric_id"] == "order_amount"
    assert plan["semantics"]["date_range"] == {
        "start": "2026-10-01",
        "end": "2026-11-01",
    }
    assert "订单总额" in plan["question"]


def test_missing_context_and_new_topic():
    assert (
        prepare_turn(QueryRequest(question="只看上个月"))["status"]
        == "clarification_required"
    )
    prior = {
        "history": [{"question": "订单总额", "answer": "old"}],
        "slots": {"metric_id": "order_amount", "period": "last_month"},
    }
    fresh = prepare_turn(QueryRequest(question="用户总数是多少", new_topic=True), prior)
    assert fresh["semantics"]["metric_id"] == "user_count"
    assert fresh["semantics"]["period"] == "all_time"


def test_explicit_question_dates_are_not_overridden_by_all_time():
    result = prepare_turn(QueryRequest(question="2026年1月订单总额"))
    assert result["semantics"]["date_range"] == {
        "start": "2026-01-01",
        "end": "2026-02-01",
    }


def test_invalid_date_range_is_rejected():
    with pytest.raises(ValidationError):
        QueryRequest(
            question="orders", date_range={"start": "2026-02-01", "end": "2026-01-01"}
        )
    with pytest.raises(ValidationError):
        QueryRequest(
            question="orders",
            period="all_time",
            date_range={"start": "2026-01-01", "end": "2026-02-01"},
        )


@pytest.mark.parametrize(
    "question", ["订单金额最高是多少", "平均订单金额是多少", "列出每笔订单金额"]
)
def test_detail_or_extrema_queries_are_not_forced_into_sum(question):
    result = prepare_turn(QueryRequest(question=question))
    assert result["semantics"]["metric_id"] is None


def test_natural_month_overrides_previous_month():
    prior = {
        "history": [{"question": "订单总额", "answer": "old"}],
        "slots": {
            "metric_id": "order_amount",
            "period": "custom",
            "date_range": {"start": "2026-01-01", "end": "2026-02-01"},
        },
    }
    result = prepare_turn(QueryRequest(question="改为2026年2月"), prior)
    assert result["semantics"]["date_range"] == {
        "start": "2026-02-01",
        "end": "2026-03-01",
    }


def test_new_unsupported_business_intent_needs_new_confirmation():
    prior = {
        "history": [{"question": "订单总额", "answer": "old"}],
        "slots": {
            "metric_id": "order_amount",
            "metric_confirmed": True,
            "period": "all_time",
        },
    }
    result = prepare_turn(QueryRequest(question="那已支付收入呢"), prior)
    assert result["status"] == "clarification_required"
    assert result["clarification"][0]["field"] == "metric_id"


def test_standalone_grouped_query_does_not_need_prior_conversation():
    assert prepare_turn(QueryRequest(question="按月统计订单金额"))["status"] == "ready"
