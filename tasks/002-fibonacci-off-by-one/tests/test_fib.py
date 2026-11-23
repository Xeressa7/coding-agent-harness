from fib import fibonacci


def test_known_values():
    assert [fibonacci(n) for n in range(8)] == [0, 1, 1, 2, 3, 5, 8, 13]


def test_larger_value_is_still_correct():
    assert fibonacci(15) == 610
