"""FastAPI 入口：批次资金流水核销 API。"""

from __future__ import annotations

from collections import Counter

from fastapi import FastAPI, HTTPException

from .engine import Entry, reconcile
from .schemas import ReconcileRequest, ReconcileResponse

app = FastAPI(
    title="批次资金流水核销 API",
    description=(
        "两侧各最多 12 笔流水，按同号、48 小时时间窗、金额和相等的规则，"
        "求解核销金额绝对值总和最大的互斥核销组集合。"
    ),
    version="1.0.0",
)


@app.get("/health", tags=["meta"], summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/reconcile",
    response_model=ReconcileResponse,
    tags=["reconcile"],
    summary="批次资金流水核销",
    responses={400: {"description": "存在重复流水 ID"}},
)
def reconcile_batch(request: ReconcileRequest) -> ReconcileResponse:
    id_counts = Counter(entry.id for entry in request.left)
    id_counts.update(entry.id for entry in request.right)
    duplicates = sorted(id_ for id_, count in id_counts.items() if count > 1)
    if duplicates:
        raise HTTPException(
            status_code=400,
            detail=f"流水 ID 在批次内必须唯一，重复 ID: {', '.join(duplicates)}",
        )

    left = [Entry(id=entry.id, time=entry.time, amount=entry.amount) for entry in request.left]
    right = [Entry(id=entry.id, time=entry.time, amount=entry.amount) for entry in request.right]
    result = reconcile(left, right)

    return ReconcileResponse(
        groups=[
            {
                "left_ids": list(group.left_ids),
                "right_ids": list(group.right_ids),
                "amount": group.amount,
            }
            for group in result.groups
        ],
        total_amount=result.total_amount,
        unmatched_left_ids=result.unmatched_left_ids,
        unmatched_right_ids=result.unmatched_right_ids,
    )
