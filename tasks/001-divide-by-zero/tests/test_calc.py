from calc import divide


def test_divide_by_zero_returns_zero():
    assert divide(4, 0) == 0


def test_divide_normal_case_is_unaffected():
    assert divide(10, 2) == 5
