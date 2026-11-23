from stats import running_averages


def test_known_sequence():
    assert running_averages([2, 4, 6]) == [2.0, 3.0, 4.0]


def test_single_value():
    assert running_averages([5]) == [5.0]


def test_all_same_values():
    assert running_averages([3, 3, 3, 3]) == [3.0, 3.0, 3.0, 3.0]
