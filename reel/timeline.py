"""Interval math + mapping original time -> final (cut) video time."""
from __future__ import annotations

Interval = tuple[float, float]


def merge(intervals: list[Interval], gap: float = 0.0) -> list[Interval]:
    out: list[list[float]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def subtract(total: Interval, cuts: list[Interval]) -> list[Interval]:
    """Keep intervals = total minus cuts."""
    keep, cur = [], total[0]
    for s, e in merge(cuts):
        s, e = max(s, total[0]), min(e, total[1])
        if e <= s:
            continue
        if s > cur:
            keep.append((cur, s))
        cur = max(cur, e)
    if cur < total[1]:
        keep.append((cur, total[1]))
    return keep


def to_final(t: float, keep: list[Interval]) -> float:
    """Map an original timestamp into the cut video. Times inside a cut snap
    to the start of the next kept part."""
    acc = 0.0
    for s, e in keep:
        if t < s:
            return acc
        if t <= e:
            return acc + (t - s)
        acc += e - s
    return acc


def duration(keep: list[Interval]) -> float:
    return sum(e - s for s, e in keep)
