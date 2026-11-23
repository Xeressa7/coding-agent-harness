from flatten import flatten


def test_one_level_nesting():
    assert flatten([1, [2, 3], 4]) == [1, 2, 3, 4]


def test_deeply_nested():
    assert flatten([1, [2, [3, 4], 5], [[6]]]) == [1, 2, 3, 4, 5, 6]


def test_already_flat():
    assert flatten([1, 2, 3]) == [1, 2, 3]
