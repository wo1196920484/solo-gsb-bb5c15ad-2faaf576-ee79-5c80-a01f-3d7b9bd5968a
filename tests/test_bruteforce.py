"""随机小实例：引擎结果与独立暴力求解器对拍（含字典序并列规则）。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from itertools import combinations

from app.engine import Entry, reconcile

UTC = timezone.utc
T0 = datetime(2026, 9, 17, 0, 0, 0, tzinfo=UTC)
SHAPES = ((1, 1), (1, 2), (1, 3), (2, 1), (3, 1))


def _brute_force(left: list[Entry], right: list[Entry]) -> tuple[int, list[tuple[tuple[str, ...], tuple[str, ...]]]]:
    """独立实现：枚举合法候选 + 递归枚举全部互斥组合。"""

    # 独立生成候选，不依赖引擎内部实现
    candidates: list[tuple[int, tuple[str, ...], tuple[str, ...], int]] = []
    for ls_count, rs_count in SHAPES:
        for lcombo in combinations(range(len(left)), ls_count):
            for rcombo in combinations(range(len(right)), rs_count):
                lm = [left[i] for i in lcombo]
                rm = [right[i] for i in rcombo]
                members = lm + rm
                amounts = [m.amount for m in members]
                if not all(a > 0 for a in amounts) and not all(a < 0 for a in amounts):
                    continue
                if sum(m.amount for m in lm) != sum(m.amount for m in rm):
                    continue
                if max(m.time for m in members) - min(m.time for m in members) > timedelta(hours=48):
                    continue
                mask = sum(1 << i for i in lcombo)
                mask |= sum(1 << (len(left) + i) for i in rcombo)
                candidates.append(
                    (
                        mask,
                        tuple(sorted(m.id for m in lm)),
                        tuple(sorted(m.id for m in rm)),
                        abs(sum(m.amount for m in lm)),
                    )
                )

    candidates.sort(key=lambda c: (c[1], c[2]))

    best_key = (0, [])

    def search(idx: int, used: int, value: int, picked: list) -> None:
        nonlocal best_key
        if value > best_key[0] or (value == best_key[0] and picked < best_key[1]):
            best_key = (value, list(picked))
        for j in range(idx, len(candidates)):
            mask, lids, rids, weight = candidates[j]
            if mask & used == 0:
                picked.append((lids, rids))
                search(j + 1, used | mask, value + weight, picked)
                picked.pop()

    search(0, 0, 0, [])
    return best_key[0], best_key[1]


def _random_instance(rng: random.Random) -> tuple[list[Entry], list[Entry]]:
    sides: list[list[Entry]] = []
    uid = 0
    # 时间集中在 60 小时窗口内，使 48h 限制有区分度
    for _ in range(2):
        entries = []
        for _ in range(rng.randint(0, 5)):
            uid += 1
            sign = rng.choice((1, -1))
            amount = sign * rng.randint(1, 40)
            entry = Entry(
                id=f"X{uid:03d}",
                time=T0 + timedelta(minutes=rng.randint(0, 60 * 60)),
                amount=amount,
            )
            entries.append(entry)
        sides.append(entries)
    return sides[0], sides[1]


def test_random_instances_match_brute_force() -> None:
    rng = random.Random(20260917)
    for seed in range(400):
        rng.seed(1000 + seed)
        left, right = _random_instance(rng)
        result = reconcile(left, right)
        expected_value, expected_codes = _brute_force(left, right)
        actual_codes = [(g.left_ids, g.right_ids) for g in result.groups]
        assert result.total_amount == expected_value, f"seed={seed}"
        assert actual_codes == expected_codes, f"seed={seed}"
