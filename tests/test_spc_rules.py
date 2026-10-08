import pytest

from mcp_server.spc_rules import assess, imr_limits, western_electric


def test_imr_limits_textbook_example():
    lim = imr_limits([10, 12, 11, 13, 12])
    # MR = 2,1,2,1 → mean 1.5 → sigma = 1.5/1.128
    assert lim["cl"] == pytest.approx(11.6)
    assert lim["sigma"] == pytest.approx(1.5 / 1.128)
    assert lim["ucl"] - lim["cl"] == pytest.approx(2.66 * 1.5, rel=1e-3)


def test_rule1_point_beyond_3_sigma():
    v = western_electric([0, 0, 4, 0], cl=0, sigma=1)
    assert {(e["index"], e["rule"]) for e in v} == {(2, 1)}


def test_rule2_two_of_three_beyond_2_sigma():
    v = western_electric([0, 2.5, 0, 2.5], cl=0, sigma=1)
    assert (3, 2) in {(e["index"], e["rule"]) for e in v}


def test_rule4_eight_in_a_row_same_side():
    v = western_electric([0.5] * 8, cl=0, sigma=1)
    assert (7, 4) in {(e["index"], e["rule"]) for e in v}


def test_stable_process_is_in_control():
    values = [0.95, 0.94, 0.96, 0.95, 0.93, 0.96, 0.94, 0.95, 0.96, 0.94]
    assert assess(values)["in_control"] is True


def test_drop_after_baseline_is_detected():
    values = [0.95, 0.94, 0.96, 0.95, 0.93, 0.96, 0.94, 0.95, 0.80]
    result = assess(values, baseline=8)
    assert result["in_control"] is False
    assert result["violations"][-1]["index"] == 8


def test_needs_three_values():
    with pytest.raises(ValueError):
        imr_limits([1, 2])
