"""Explicit, versioned metric definitions and deterministic clarification rules."""

from datetime import datetime, timedelta, timezone, date
import re

TZ = timezone(timedelta(hours=8))
METRICS = {
    "order_amount": {
        "name": "订单金额",
        "expression": "SUM(orders.amount)",
        "unit": "元",
        "definition": "全部订单的记录金额，不等同于已支付收入；当前无支付、退款、状态字段。",
        "time_column": "orders.created_at",
    },
    "order_count": {
        "name": "订单数量",
        "expression": "COUNT(orders.id)",
        "unit": "笔",
        "definition": "全部订单记录数量，不自动剔除零金额订单。",
        "time_column": "orders.created_at",
    },
    "user_count": {
        "name": "用户数量",
        "expression": "COUNT(users.id)",
        "unit": "人",
        "definition": "用户记录数量；时间条件按用户创建时间。",
        "time_column": "users.created_at",
    },
    "ordering_users": {
        "name": "下单用户数",
        "expression": "COUNT(DISTINCT orders.user_id)",
        "unit": "人",
        "definition": "有订单的去重用户数；不等同于登录或访问活跃人数。",
        "time_column": "orders.created_at",
    },
}
FOLLOWUP = re.compile(
    r"^(只看|只统计|那|再|按|改成|改为|上个月|这个|这些|其中|它|继续|仅看)"
)


def clock_date():
    return datetime.now(TZ).date()


def resolve_period(period, today):
    first = today.replace(day=1)
    if period == "all_time":
        return None
    if period == "last_month":
        return {
            "start": (first - timedelta(days=1)).replace(day=1).isoformat(),
            "end": first.isoformat(),
        }
    if period == "this_month":
        return {
            "start": first.isoformat(),
            "end": (today + timedelta(days=1)).isoformat(),
        }
    return {
        "start": (today - timedelta(days=6)).isoformat(),
        "end": (today + timedelta(days=1)).isoformat(),
    }


def prepare_turn(req, conversation=None, today=None):
    today = today or clock_date()
    conversation = conversation or {}
    history = conversation.get("history", [])
    pending = conversation.get("pending") if not req.new_topic else None
    standalone = bool(
        re.search(r"(统计|查询|列出|计算).*(订单|用户)|用户总数|订单总额", req.question)
    )
    followup = (
        bool(FOLLOWUP.match(req.question.strip()))
        and not req.new_topic
        and not standalone
    )
    base = dict((pending or {}).get("slots", {}))
    if followup and history and not pending:
        base = dict(conversation.get("slots", {}))
    question = req.question
    original = pending["question"] if pending else question
    topic = (
        pending.get("topic", original)
        if pending
        else (
            history[-1].get("topic", history[-1]["question"])
            if followup and history
            else original
        )
    )
    # A clarification reply supplements the original request rather than replacing it.
    combined = (original + "；补充：" + question) if pending else question
    explicit_metric = req.metric_id
    total_amount = bool(
        re.search(r"订单总额|订单总金额|订单的总金额|订单金额(总和|合计)", question)
    )
    extrema = bool(re.search(r"最高|最低|平均|最大|最小|每笔|逐笔", question))
    if not explicit_metric:
        if total_amount or (
            not extrema
            and (
                "按订单金额" in question
                or "订单金额口径" in question
                or (pending and "订单金额" in question)
            )
        ):
            explicit_metric = "order_amount"
        elif "下单用户" in question and (
            pending or re.search(r"数|多少|口径", question)
        ):
            explicit_metric = "ordering_users"
        elif re.search(r"订单(总数|数量)|几笔订单|多少笔订单", question) or (
            pending and "全部订单" in question
        ):
            explicit_metric = "order_count"
        elif re.search(r"用户总数|用户数量", question):
            explicit_metric = "user_count"
    if explicit_metric:
        base["metric_id"] = explicit_metric
        base["metric_confirmed"] = True
    period = req.period
    if not period:
        if "上个月" in question:
            period = "last_month"
        elif "本月" in question or "这个月" in question:
            period = "this_month"
        elif "近7天" in question or "最近7天" in question:
            period = "last_7_days"
        elif any(x in question for x in ("全部时间", "全部历史", "所有时间")):
            period = "all_time"
    if req.date_range:
        base["date_range"] = {
            "start": req.date_range.start.isoformat(),
            "end": req.date_range.end.isoformat(),
        }
        base["period"] = "custom"
    elif period:
        base["resolved_on"] = today.isoformat()
        base["period"] = period
        base["date_range"] = resolve_period(period, today)
    elif month := re.search(r"(\d{4})年(1[0-2]|0?[1-9])月", question):
        year, number = int(month[1]), int(month[2])
        begin = date(year, number, 1)
        end = date(year + (number == 12), 1 if number == 12 else number + 1, 1)
        base["period"] = "custom"
        base["date_range"] = {"start": begin.isoformat(), "end": end.isoformat()}
    elif re.search(r"\d{4}[-年/]", question):
        base["period"] = "question_defined"
        base.pop("date_range", None)
    missing = []
    unsupported = any(
        x in combined for x in ("已支付", "净收入", "退款", "登录活跃", "有效订单")
    )
    ambiguous = bool(re.search("销售额|收入|GMV|活跃用户|有效订单", combined, re.I))
    confirmed_current = bool(
        explicit_metric or (pending and base.get("metric_confirmed"))
    )
    if unsupported and not confirmed_current:
        missing.append(
            {
                "field": "metric_id",
                "message": "当前数据没有支付、退款、状态或登录字段，请改选可计算的口径。",
                "options": ["order_amount", "order_count", "ordering_users"],
            }
        )
    elif ambiguous and not confirmed_current:
        missing.append(
            {
                "field": "metric_id",
                "message": "请确认业务口径；订单金额不等同于已支付收入，活跃用户也需要行为定义。",
                "options": ["order_amount", "order_count", "ordering_users"],
            }
        )
    vague_time = bool(re.search("最近|近期|这段时间", combined))
    need_time = (
        ambiguous and not re.search("总额|累计|全部|所有", combined)
    ) or vague_time
    if need_time and "period" not in base and not re.search(r"\d{4}[-年/]", combined):
        missing.append(
            {
                "field": "period",
                "message": "请确认时间范围，或提供左闭右开的 date_range。",
                "options": ["all_time", "last_month", "this_month", "last_7_days"],
            }
        )
    if followup and not history and not pending:
        missing.append(
            {
                "field": "question",
                "message": "没有可延续的查询，请先说明要查询的指标或数据。",
                "options": [],
            }
        )
    if missing:
        return {
            "status": "clarification_required",
            "answer": "请补充：" + "；".join(x["message"] for x in missing),
            "clarification": missing,
            "pending": {"question": original, "topic": topic, "slots": base},
            "slots": base,
            "topic": topic,
        }
    metric = METRICS.get(base.get("metric_id"))
    semantics = {
        "registry_version": "2.2.0",
        "metric_id": base.get("metric_id"),
        "metric": metric,
        "date_range": base.get("date_range"),
        "period": base.get(
            "period",
            "question_defined" if re.search(r"\d{4}[-年/]", combined) else "all_time",
        ),
        "timezone": "Asia/Shanghai",
        "resolved_on": base.get("resolved_on", today.isoformat()),
    }
    effective = topic + "\n本轮问题：" + question if followup or pending else question
    return {
        "status": "ready",
        "question": effective[:4000],
        "semantics": semantics,
        "slots": base,
        "topic": topic,
        "pending": None,
    }


def prompt_contract(semantics):
    import json

    if not semantics:
        return ""
    return (
        "\n本轮已确认的业务口径（优先于历史条件）："
        + json.dumps(semantics, ensure_ascii=False)
        + "。日期按 Asia/Shanghai，区间左闭右开，日期边界使用 +08:00 的零点时间戳；不得自行增加支付/退款/有效状态条件。"
    )
