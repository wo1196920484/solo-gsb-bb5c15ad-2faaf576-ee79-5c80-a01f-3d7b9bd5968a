"""FastAPI 接口集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _entry(id_: str, amount: int, time: str = "2026-09-17T00:00:00Z") -> dict:
    return {"id": id_, "time": time, "amount": amount}


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_reconcile_one_to_many() -> None:
    response = client.post(
        "/reconcile",
        json={
            "left": [_entry("L1", 100)],
            "right": [
                _entry("R2", 60, "2026-09-17T05:00:00Z"),
                _entry("R1", 40, "2026-09-17T03:00:00Z"),
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["groups"] == [
        {"left_ids": ["L1"], "right_ids": ["R1", "R2"], "amount": 100}
    ]
    assert body["total_amount"] == 100
    assert body["unmatched_left_ids"] == []
    assert body["unmatched_right_ids"] == []


def test_duplicate_id_returns_400() -> None:
    response = client.post(
        "/reconcile",
        json={
            "left": [_entry("X", 100), _entry("L2", 50)],
            "right": [_entry("X", 100)],
        },
    )
    assert response.status_code == 400
    assert "X" in response.json()["detail"]


def test_duplicate_id_within_same_side_returns_400() -> None:
    response = client.post(
        "/reconcile",
        json={"left": [_entry("X", 100), _entry("X", 100)], "right": []},
    )
    assert response.status_code == 400


def test_zero_amount_rejected_422() -> None:
    response = client.post(
        "/reconcile",
        json={"left": [_entry("L1", 0)], "right": []},
    )
    assert response.status_code == 422


def test_naive_time_rejected_422() -> None:
    response = client.post(
        "/reconcile",
        json={
            "left": [{"id": "L1", "time": "2026-09-17 00:00:00", "amount": 100}],
            "right": [],
        },
    )
    assert response.status_code == 422


def test_offset_time_normalized_to_utc() -> None:
    # 北京时间 +08:00 的 08:00 即 UTC 00:00，与右侧 UTC 48h 边界
    response = client.post(
        "/reconcile",
        json={
            "left": [{"id": "L1", "time": "2026-09-17T08:00:00+08:00", "amount": 10}],
            "right": [{"id": "R1", "time": "2026-09-19T00:00:00Z", "amount": 10}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["total_amount"] == 10


def test_too_many_entries_rejected_422() -> None:
    response = client.post(
        "/reconcile",
        json={
            "left": [_entry(f"L{i}", 1) for i in range(13)],
            "right": [],
        },
    )
    assert response.status_code == 422


def test_empty_batch_ok() -> None:
    response = client.post("/reconcile", json={"left": [], "right": []})
    assert response.status_code == 200
    assert response.json() == {
        "groups": [],
        "total_amount": 0,
        "unmatched_left_ids": [],
        "unmatched_right_ids": [],
    }


def test_unmatched_ids_returned_sorted() -> None:
    response = client.post(
        "/reconcile",
        json={
            "left": [_entry("L2", 10), _entry("L1", 99)],
            "right": [_entry("R1", 10), _entry("R2", 50)],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["groups"] == [{"left_ids": ["L2"], "right_ids": ["R1"], "amount": 10}]
    assert body["unmatched_left_ids"] == ["L1"]
    assert body["unmatched_right_ids"] == ["R2"]
