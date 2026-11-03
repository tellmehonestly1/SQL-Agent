"""Public analysis request; identity is supplied by server authentication only."""

from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator

MetricId = Literal["order_amount", "order_count", "user_count", "ordering_users"]
Period = Literal["all_time", "last_month", "this_month", "last_7_days"]


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("end must be after start (exclusive)")
        return self


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    session_id: UUID | None = None
    metric_id: MetricId | None = None
    period: Period | None = None
    date_range: DateRange | None = None
    new_topic: bool = False

    @model_validator(mode="after")
    def one_time_specification(self):
        if self.period and self.date_range:
            raise ValueError("Specify period or date_range, not both")
        return self
