"""核销引擎单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine import Entry, reconcile

UTC = timezone.utc
T0 = datetime(2026, 9, 17, 0, 0, 0, tzinfo=UTC)


def e(id_: str, amount: int, offset_hours: float = 0) -> Entry:
    return Entry(id=id_, time=T0 + timedelta(hours=offset_hours), amount=amount)


def codes(result) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    return [(g.left_ids, g.right_ids) for g in result.groups]


def test_one_to_one_match() -> None:
    result = reconcile([e("L1", 100)], [e("R1", 100)])
    assert result.total_amount == 100
    assert codes(result) == [(("L1",), ("R1",))]
    assert result.unmatched_left_ids == []
    assert result.unmatched_right_ids == []


def test_one_to_many_single_against_two() -> None:
    # L1 100 = R1 40 + R2 60
    result = reconcile([e("L1", 100)], [e("R1", 40), e("R2", 60)])
    assert result.total_amount == 100
    assert codes(result) == [(("L1",), ("R1", "R2"))]


def test_one_to_many_two_against_single_and_sorted_ids() -> None:
    # L2 40 + L1 60 = R1 100；ID 必须升序输出
    result = reconcile([e("L2", 40), e("L1", 60)], [e("R1", 100)])
    assert result.total_amount == 100
    assert codes(result) == [(("L1", "L2"), ("R1",))]


def test_one_against_three() -> None:
    result = reconcile([e("L1", 100)], [e("R1", 20), e("R2", 30), e("R3", 50)])
    assert result.total_amount == 100
    assert codes(result) == [(("L1",), ("R1", "R2", "R3"))]


def test_maximize_total_chooses_bigger_pair() -> None:
    # 右侧只有 R1 一笔，L1=50、L2=100 都能与它一对一（互斥），取金额更大的 L2
    result = reconcile(
        [e("L1", 50), e("L2", 100)],
        [e("R1", 100), e("R2", 50, 50)],
    )
    # R2 超出 48h 时间窗无法参与 => 只能在 L1/L2 与 R1 间二选一
    assert result.total_amount == 100
    assert codes(result) == [(("L2",), ("R1",))]
    assert result.unmatched_left_ids == ["L1"]
    assert result.unmatched_right_ids == ["R2"]


def test_pair_vs_group_prefers_higher_total() -> None:
    # 方案 A（两个 1:1，互斥）：L1-R1=50、L2-R2=50 => 100
    # 方案 B（1 对 2）：L3=100 与 R1+R2=100 => 100，且会占用 R1 R2
    # 同值时比较字典序：A 的组编码列表第一个是 (("L1",),("R1",))，
    # B 是 (("L3",),("R1","R2")) => A 更小。
    result = reconcile(
        [e("L1", 50), e("L2", 50), e("L3", 100)],
        [e("R1", 50), e("R2", 50)],
    )
    assert result.total_amount == 100
    assert codes(result) == [
        (("L1",), ("R1",)),
        (("L2",), ("R2",)),
    ]

    # 让一对二方案金额更大，则应选一对二
    result2 = reconcile(
        [e("L1", 50), e("L2", 50), e("L3", 120)],
        [e("R1", 50), e("R2", 70)],
    )
    assert result2.total_amount == 120
    assert codes(result2) == [(("L3",), ("R1", "R2"))]


def test_tie_break_lexicographic_group_code() -> None:
    # L1 10 配 R1 10；L2 10 也配 R1 10 —— 两个互斥最优解金额相同，
    # 取左侧 ID 字典序更小的 L1
    result = reconcile(
        [e("L1", 10), e("L2", 10)],
        [e("R1", 10)],
    )
    assert result.total_amount == 10
    assert codes(result) == [(("L1",), ("R1",))]
    assert result.unmatched_left_ids == ["L2"]


def test_time_window_boundary_48h() -> None:
    # 恰好 48 小时：合法
    result = reconcile([e("L1", 10, 0)], [e("R1", 10, 48)])
    assert result.total_amount == 10
    assert codes(result) == [(("L1",), ("R1",))]

    # 超过 48 小时：不合法
    result2 = reconcile([e("L1", 10, 0)], [e("R1", 10, 48.0001)])
    assert result2.total_amount == 0
    assert result2.groups == []
    assert result2.unmatched_left_ids == ["L1"]
    assert result2.unmatched_right_ids == ["R1"]


def test_mixed_signs_in_group_rejected() -> None:
    # 金额和相等（-50 = -100 + 50）但组内异号，必须拒绝
    result = reconcile([e("L1", -50)], [e("R1", -100), e("R2", 50)])
    assert result.groups == []
    assert result.total_amount == 0

    # 正数配正数才合法，负数不会跨号匹配
    result2 = reconcile([e("L1", 50)], [e("R1", -50)])
    assert result2.groups == []


def test_negative_amount_match() -> None:
    result = reconcile(
        [e("L1", -100)],
        [e("R1", -40), e("R2", -60)],
    )
    assert result.total_amount == 100
    assert codes(result) == [(("L1",), ("R1", "R2"))]
    assert result.groups[0].amount == -100


def test_positive_and_negative_independent() -> None:
    result = reconcile(
        [e("L1", 100), e("L2", -100)],
        [e("R1", 100), e("R2", -100)],
    )
    assert result.total_amount == 200
    assert codes(result) == [
        (("L1",), ("R1",)),
        (("L2",), ("R2",)),
    ]


def test_no_possible_match() -> None:
    result = reconcile([e("L1", 100)], [e("R1", 99)])
    assert result.groups == []
    assert result.total_amount == 0
    assert result.unmatched_left_ids == ["L1"]
    assert result.unmatched_right_ids == ["R1"]


def test_each_entry_used_at_most_once() -> None:
    # L1=100 与 R1+R2、R2+R3 都能成组；同时 L2=60 与 R2 等有多种解，
    # 结果中每笔流水最多出现一次。
    result = reconcile(
        [e("L1", 100), e("L2", 60), e("L3", 40)],
        [e("R1", 40), e("R2", 60), e("R3", 40)],
    )
    used_left: list[str] = []
    used_right: list[str] = []
    for g in result.groups:
        used_left.extend(g.left_ids)
        used_right.extend(g.right_ids)
    assert len(used_left) == len(set(used_left))
    assert len(used_right) == len(set(used_right))


def test_empty_sides() -> None:
    result = reconcile([], [])
    assert result.groups == []
    assert result.total_amount == 0


def test_group_time_span_uses_all_members() -> None:
    # 单笔侧时间在 50h 处，导致整组跨度 > 48h，即使多笔侧内部跨度很小
    result = reconcile(
        [e("L1", 100, 50)],
        [e("R1", 40, 1), e("R2", 60, 2)],
    )
    assert result.groups == []
