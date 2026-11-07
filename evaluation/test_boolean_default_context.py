"""Bounded preservation checks must follow Python's lazy expression semantics."""
from __future__ import annotations

import pytest

from runtime.default_behavior import compare, definitions, run


@pytest.mark.parametrize("expression, expected", [
    ("0 and 1 / 0", ("int", "0")),
    ("7 or 1 / 0", ("int", "7")),
    ("None or value", ("int", "9")),
    ("value and 'ready'", ("str", "'ready'")),
    ("0 or None or value", ("int", "9")),
    ("9 > 10 > 1 / 0", ("bool", "False")),
    ("0 < value < 10", ("bool", "True")),
    ("0 < value < 1 / 0", ("exception", "ZeroDivisionError", "division by zero")),
])
def test_short_circuit_returns_the_operand_not_a_coerced_boolean(expression, expected):
    function = definitions(f"def select(value):\n return {expression}\n")["select"]
    assert run(function, (9,)) == expected


def test_boolean_range_guard_preserves_omitted_calls_with_existing_bounds():
    before = "def aggregate(values):\n return sum(values)\n"
    after = ("def aggregate(values, rate=0):\n"
             " if rate < 0 or rate > 1:\n  raise ValueError('range')\n"
             " value = sum(values)\n return value if rate == 0 else value * (1 - rate)\n")
    result = compare(before, after)
    assert result["status"] == "passed_bounded_comparison", result
    assert len(result["cases"]) == 12
    assert all(case["matched"] for case in result["cases"])
    assert compare(before, after.replace("return value if", "return value + 1 if"))["status"] == "failed"


def test_reached_unsupported_boolean_operand_is_still_unavailable():
    assert compare("def f(x):\n return x\n",
                   "def f(x, flag=1):\n return flag and open('unowned')\n")["status"] == "unavailable"
