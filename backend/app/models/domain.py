"""T007: 领域模型。金额一律 Decimal，限制两位小数。"""
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import OrderStatus, RefundStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Address(BaseModel):
    receiver: str = Field(min_length=1, max_length=50)
    phone: str = Field(pattern=r"^\d{6,20}$")
    province: str = Field(min_length=1, max_length=50)
    city: str = Field(min_length=1, max_length=50)
    district: str = Field(min_length=1, max_length=50)
    detail: str = Field(min_length=1, max_length=200)


class Order(BaseModel):
    order_id: str = Field(min_length=1)
    status: OrderStatus
    amount: Decimal = Field(gt=0)
    address: Address
    # 政策检索的品类过滤依据；缺失时只匹配通配条款，不用品类不明的条款强行回答
    product_type: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("amount")
    @classmethod
    def _two_decimal_places(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("amount must have at most 2 decimal places")
        return v


class LogisticsTrack(BaseModel):
    node: str
    at: datetime


class Logistics(BaseModel):
    order_id: str
    current_node: str
    estimated_delivery: datetime | None = None
    tracks: list[LogisticsTrack] = Field(default_factory=list)


class Refund(BaseModel):
    refund_id: str
    order_id: str
    amount: Decimal = Field(gt=0)
    status: RefundStatus = RefundStatus.SUCCESS
    request_id: str
    created_at: datetime = Field(default_factory=_now)
