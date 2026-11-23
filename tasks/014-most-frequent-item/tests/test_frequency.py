from frequency import most_frequent


def test_returns_the_most_common_item():
    assert most_frequent(["a", "b", "b", "c"]) == "b"


def test_most_common_is_not_alphabetically_largest():
    assert most_frequent(["a", "a", "a", "z"]) == "a"


def test_single_item():
    assert most_frequent(["x"]) == "x"
