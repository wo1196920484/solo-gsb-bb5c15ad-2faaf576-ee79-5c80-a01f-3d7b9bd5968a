"""HTTP 请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class ReconcileEntry(BaseModel):
    """单笔流水。

    ``time`` 接受 ISO 8601 带时区时间（如 ``2026-09-17T08:00:00Z``），
    服务端统一归一化为 UTC；不带时区的时间会被拒绝（HTTP 422）。
    ``amount`` 为非零带符号整数，单位：分。
    """

    id: str = Field(min_length=1)
    time: datetime
    amount: int

    @field_validator("time", mode="before")
    @classmethod
    def _parse_aware_time(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("time 必须是合法的 ISO 8601 时间")
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("time 必须带有时区信息（UTC 时间，例如 2026-09-17T08:00:00Z）")
            return parsed
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("time 必须带有时区信息（UTC 时间，例如 2026-09-17T08:00:00Z）")
            return value
        return value

    @field_validator("time")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @field_validator("amount")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("amount 必须是非零带符号整数（单位：分）")
        return value


class ReconcileRequest(BaseModel):
    left: list[ReconcileEntry] = Field(max_length=12)
    right: list[ReconcileEntry] = Field(max_length=12)


class ReconcileGroup(BaseModel):
    left_ids: list[str]
    right_ids: list[str]
    # 核销金额（带符号，两侧金额和相等），单位：分
    amount: int


class ReconcileResponse(BaseModel):
    # 组编码（升序左侧 ID, 升序右侧 ID），按编码升序排列
    groups: list[ReconcileGroup]
    # 核销金额绝对值总和，单位：分
    total_amount: int
    unmatched_left_ids: list[str]
    unmatched_right_ids: list[str]


class ErrorResponse(BaseModel):
    detail: str
