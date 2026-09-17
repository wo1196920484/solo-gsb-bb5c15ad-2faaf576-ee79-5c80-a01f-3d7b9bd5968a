"""批次资金流水核销引擎（纯 Python，无框架依赖）。

规则
----
1. 两侧流水（左 / 右），每侧最多 12 笔；每笔含全批次唯一 ID、UTC 时间、
   非零带符号整数金额（单位：分）。
2. 一个核销组只能是：
   - 一对一：左 1 笔，右 1 笔；
   - 一对多：一侧 1 笔，另一侧 2 或 3 笔。
3. 合法核销组必须同时满足：
   - 组内所有金额同号（同为正或同为负）；
   - 组内最早与最晚流水时间差不超过 48 小时；
   - 两侧金额之和相等。
4. 每笔流水最多属于一个核销组。在所有合法候选中，选出核销金额绝对值
   总和最大的互斥集合。
5. 最优解并列时，取“组编码按升序排列后的列表”字典序最小者；
   组编码 = (升序左侧 ID 列表, 升序右侧 ID 列表)。

求解思路
--------
合法候选组最多约七千个，本质是一个候选组间的带权最大独立集问题
（权重 = 组金额绝对值，冲突 = 共享流水）。规模仅 24 个“顶点”（流水），
采用位掩码记忆化 DP：

- 固定一个分支位，枚举“该笔不核销”与“选择包含该笔的候选组”；
- 按各流水出现在候选组中的度数（静态冲突度）对流水位重排，
  令度数最小的位成为最低位，显著压缩可达状态数；
- 正数流水与负数流水不可能同组，拆成两个独立宇宙分别求解；
- 重建阶段按组编码升序贪心挑出仍可属于某最优解的最小组，
  自然得到字典序最小的最优组编码列表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from typing import Sequence

MAX_ENTRIES_PER_SIDE = 12
MAX_TIME_SPAN = timedelta(hours=48)

# 允许的组形状：(左侧笔数, 右侧笔数)
_GROUP_SHAPES = ((1, 1), (1, 2), (1, 3), (2, 1), (3, 1))


@dataclass(frozen=True)
class Entry:
    """单笔流水（time 为归一化到 UTC 的感知时间）。"""

    id: str
    time: datetime
    # 非零带符号整数，单位：分
    amount: int


@dataclass(frozen=True)
class MatchedGroup:
    """一个核销结果组（ID 均已升序）。"""

    left_ids: tuple[str, ...]
    right_ids: tuple[str, ...]
    # 带符号金额：两侧金额和相等，取任一侧即可
    amount: int


@dataclass(frozen=True)
class ReconcileOutput:
    """核销结果。"""

    groups: list[MatchedGroup]
    total_amount: int
    unmatched_left_ids: list[str]
    unmatched_right_ids: list[str]


@dataclass(frozen=True)
class _Subset:
    """枚举过程中某一侧的一个子集（1~3 笔）。"""

    mask: int
    ids: tuple[str, ...]
    total: int
    # 子集内金额同号时为 1 / -1，否则为 0
    sign: int
    tmin: datetime
    tmax: datetime


@dataclass(frozen=True)
class _Candidate:
    """一个合法候选核销组。"""

    mask: int
    left_ids: tuple[str, ...]
    right_ids: tuple[str, ...]
    amount: int
    weight: int


def _sign(value: int) -> int:
    return 1 if value > 0 else -1


def _build_subsets(entries: Sequence[Entry], offset: int) -> dict[int, list[_Subset]]:
    """生成某一侧所有大小为 1/2/3 的子集及其金额、时间、同号信息。"""

    by_size: dict[int, list[_Subset]] = {1: [], 2: [], 3: []}
    n = len(entries)
    for size in (1, 2, 3):
        if size > n:
            continue
        for combo in combinations(range(n), size):
            members = [entries[i] for i in combo]
            amounts = [m.amount for m in members]
            times = [m.time for m in members]
            signs = {_sign(a) for a in amounts}
            sign = next(iter(signs)) if len(signs) == 1 else 0
            mask = 0
            for i in combo:
                mask |= 1 << (offset + i)
            by_size[size].append(
                _Subset(
                    mask=mask,
                    ids=tuple(sorted(m.id for m in members)),
                    total=sum(amounts),
                    sign=sign,
                    tmin=min(times),
                    tmax=max(times),
                )
            )
    return by_size


def _generate_candidates(left: Sequence[Entry], right: Sequence[Entry]) -> list[_Candidate]:
    """枚举全部合法候选组。每侧 <=12 笔，最坏约 7k 个组合。"""

    left_subsets = _build_subsets(left, 0)
    right_subsets = _build_subsets(right, len(left))

    candidates: list[_Candidate] = []
    for left_size, right_size in _GROUP_SHAPES:
        if left_size > len(left) or right_size > len(right):
            continue
        for ls in left_subsets[left_size]:
            for rs in right_subsets[right_size]:
                # 组内金额必须同号
                if ls.sign == 0 or rs.sign == 0 or ls.sign != rs.sign:
                    continue
                # 两侧金额和必须相等
                if ls.total != rs.total:
                    continue
                # 组内时间跨度不超过 48 小时
                tmin = ls.tmin if ls.tmin < rs.tmin else rs.tmin
                tmax = ls.tmax if ls.tmax > rs.tmax else rs.tmax
                if tmax - tmin > MAX_TIME_SPAN:
                    continue
                candidates.append(
                    _Candidate(
                        mask=ls.mask | rs.mask,
                        left_ids=ls.ids,
                        right_ids=rs.ids,
                        amount=ls.total,
                        weight=abs(ls.total),
                    )
                )
    return candidates


def _solve_universe(candidates: Sequence[_Candidate]) -> tuple[list[_Candidate], int]:
    """在一组同号候选上求最优互斥组合。

    候选组金额同号（调用方按正 / 负拆分），因此任意两个候选要么互斥
    （共享流水），要么可同时选择。
    """

    if not candidates:
        return [], 0

    # 参与候选的流水位（至少出现在一个候选组中）；孤立的流水必为未匹配
    universe = 0
    for cand in candidates:
        universe |= cand.mask

    # 每个流水位的静态冲突度：它出现在多少个候选组中
    degree: dict[int, int] = {}
    for cand in candidates:
        mask = cand.mask
        while mask:
            bit = mask & -mask
            degree[bit.bit_length() - 1] = degree.get(bit.bit_length() - 1, 0) + 1
            mask ^= bit

    # 度数小的位重排到低位（分支位），同度数按原索引降序，确定性
    ordered_bits = sorted(degree, key=lambda i: (degree[i], -i))
    new_position = {old: pos for pos, old in enumerate(ordered_bits)}

    def remap(mask: int) -> int:
        local = 0
        while mask:
            bit = mask & -mask
            local |= 1 << new_position[bit.bit_length() - 1]
            mask ^= bit
        return local

    local_candidates = sorted(
        ((remap(cand.mask), cand.weight) for cand in candidates),
        key=lambda item: item[0],
    )

    # 以候选组最低位为索引（DP 只在分支到该位时扫描它们）
    by_lowest_bit: dict[int, list[tuple[int, int]]] = {}
    for local_mask, weight in local_candidates:
        bit_index = (local_mask & -local_mask).bit_length() - 1
        by_lowest_bit.setdefault(bit_index, []).append((local_mask, weight))

    full_local = (1 << len(ordered_bits)) - 1
    memo: dict[int, int] = {0: 0}

    def opt(mask: int) -> int:
        if mask in memo:
            return memo[mask]
        lowest_bit = mask & -mask
        i = lowest_bit.bit_length() - 1
        # 分支一：该笔流水不参与任何核销组
        best = opt(mask ^ lowest_bit)
        # 分支二：选择一个包含该笔、且完全包含在 mask 中的候选组
        for group_mask, weight in by_lowest_bit.get(i, ()):
            if group_mask & ~mask == 0:
                value = weight + opt(mask & ~group_mask)
                if value > best:
                    best = value
        memo[mask] = best
        return best

    total = opt(full_local)

    # 重建：组编码升序扫描，贪心挑出仍可属于某个最优解的最小组，
    # 得到字典序最小的最优组编码列表
    ordered_groups = sorted(candidates, key=lambda c: (c.left_ids, c.right_ids))
    local_masks = [remap(cand.mask) for cand in ordered_groups]

    chosen: list[_Candidate] = []
    remaining = full_local
    scan_start = 0
    while remaining:
        target = opt(remaining)
        picked_index = -1
        for idx in range(scan_start, len(ordered_groups)):
            local_mask = local_masks[idx]
            if local_mask & ~remaining != 0:
                continue
            if ordered_groups[idx].weight + opt(remaining & ~local_mask) == target:
                picked_index = idx
                break
        if picked_index < 0:
            # 最低位在任何最优解中都未匹配
            remaining ^= remaining & -remaining
        else:
            chosen.append(ordered_groups[picked_index])
            remaining &= ~local_masks[picked_index]
            # 已选组的编码是当前最小的，后续选中的组编码只会更大
            scan_start = picked_index + 1

    return chosen, total


def reconcile(left: Sequence[Entry], right: Sequence[Entry]) -> ReconcileOutput:
    """执行批次核销。调用方需保证两侧 ID 全局唯一、金额非零。"""

    candidates = _generate_candidates(left, right)

    # 合法组金额同号，正数笔与负数笔不可能出现在同一组中，拆成两个
    # 互不相交的宇宙分别求解（各自取字典序最小最优解，合并排序后仍为
    # 全局字典序最小）。
    positive_candidates = [c for c in candidates if c.amount > 0]
    negative_candidates = [c for c in candidates if c.amount < 0]

    selected: list[_Candidate] = []
    total_amount = 0
    for universe_candidates in (positive_candidates, negative_candidates):
        groups, value = _solve_universe(universe_candidates)
        selected.extend(groups)
        total_amount += value

    selected.sort(key=lambda c: (c.left_ids, c.right_ids))

    covered = 0
    for cand in selected:
        covered |= cand.mask

    n_left = len(left)
    unmatched_left = sorted(
        entry.id for i, entry in enumerate(left) if not ((covered >> i) & 1)
    )
    unmatched_right = sorted(
        entry.id
        for i, entry in enumerate(right)
        if not ((covered >> (n_left + i)) & 1)
    )

    return ReconcileOutput(
        groups=[
            MatchedGroup(left_ids=cand.left_ids, right_ids=cand.right_ids, amount=cand.amount)
            for cand in selected
        ],
        total_amount=total_amount,
        unmatched_left_ids=unmatched_left,
        unmatched_right_ids=unmatched_right,
    )
