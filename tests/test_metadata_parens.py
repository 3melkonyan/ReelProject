"""Chapter/title subjects keep real parentheses and never show a half-open one (live job 3)."""
from reel.stages.metadata import _balance, _grounded_fallback, _topic_variants, _trim


def balanced(s):
    return s.count("(") == s.count(")")


def test_closing_parenthesis_of_the_topic_is_kept():
    assert _topic_variants("Low-Level Memory Management (Stack)")[0] == "Low-Level Memory Management (Stack)"


def test_truncated_parenthetical_is_dropped_not_left_open():
    for v in _topic_variants("Low-Level Memory Management (Stack and Heap Allocation Strategies Explained)"):
        assert balanced(v) and len(v) <= 48 + 30, v


def test_trim_and_balance_edges():
    assert _trim("(Stack) ") == "Stack"
    assert _trim(", Closures (Scope)") == "Closures (Scope)"
    assert _balance("Memory Management (Stack and He") == "Memory Management"


def test_grounded_chapters_have_balanced_parentheses():
    plan = [{"t": 0.0, "topic": "Versioning and Context"},
            {"t": 900.0, "topic": "Low-Level Memory Management (Stack)"},
            {"t": 1800.0, "topic": "Arrow Functions and Higher-Order Functions"}]
    m = _grounded_fallback(plan, [(0.0, 3000.0)])
    assert all(balanced(c["title"]) for c in m["chapters"]) and balanced(m["description"])
    assert m["chapters"][1]["title"] == "Low-Level Memory Management-ը (Stack)"
