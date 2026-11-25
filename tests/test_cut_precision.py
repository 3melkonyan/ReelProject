"""Tiny guard overruns must remain visibly greater than their configured limit."""

import math
import re

import pytest

from reel.insights import cut_summary


def reason(percent, limit, code="over_max_ratio"):
    return cut_summary(
        {"cuts": [], "no_cut_code": code, "no_cut_details": {"percent": percent, "limit": limit}}
    )["no_cuts_reason"]


@pytest.mark.parametrize(
    "code,percent,limit,expected",
    [
        (
            "over_max_ratio",
            40.0001,
            40,
            "Ծրագրված կտրումը 40.0001% էր, թույլատրելին՝ 40%․ ոչինչ չի կտրվել։",
        ),
        (
            "unclear",
            30.0001,
            30,
            "Դասի 30.0001%-ը հնարավոր չեղավ հստակ դասակարգել (սահմանը՝ 30%)․ ոչինչ չի կտրվել։",
        ),
    ],
)
def test_tiny_overrun_stays_distinct_in_each_guard_message(code, percent, limit, expected):
    assert reason(percent, limit, code) == expected


@pytest.mark.parametrize("limit", [40.0, 30.0, 1e-100])
def test_adjacent_floats_remain_distinguishable_without_changing_their_value(limit):
    percent = math.nextafter(limit, math.inf)
    shown = re.search(
        r"կտրումը ([0-9.eE+-]+)% էր, թույլատրելին՝ ([0-9.eE+-]+)%", reason(percent, limit)
    )
    assert shown is not None
    first, second = map(float, shown.groups())
    assert first > second and first == percent and second == limit


@pytest.mark.parametrize("percent,limit", [(1e-20, 0), (1e-100, 1e-101)])
def test_scientific_fallback_preserves_exponent_zeros_and_integer_limits(percent, limit):
    text = reason(percent, limit)
    assert text == f"Ծրագրված կտրումը {percent}% էր, թույլատրելին՝ {limit}%․ ոչինչ չի կտրվել։"


def test_known_legacy_note_uses_the_same_adaptive_precision():
    data = cut_summary({"cuts": [], "auto_note": "plan cut 40.0001% (> 40.0000%) — nothing cut"})
    assert (
        data["no_cuts_reason"]
        == "Ծրագրված կտրումը 40.0001% էր, թույլատրելին՝ 40%․ ոչինչ չի կտրվել։"
    )


def test_ordinary_percentages_keep_existing_two_decimal_readability():
    assert (
        reason(45.1264, 40.1234)
        == "Ծրագրված կտրումը 45.13% էր, թույլատրելին՝ 40.12%․ ոչինչ չի կտրվել։"
    )
