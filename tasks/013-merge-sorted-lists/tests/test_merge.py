from merge import merge_sorted


def test_interleaved_lists():
    assert merge_sorted([1, 3, 5], [2, 4, 6]) == [1, 2, 3, 4, 5, 6]


def test_one_list_exhausts_first():
    assert merge_sorted([1, 2], [3, 4, 5, 6]) == [1, 2, 3, 4, 5, 6]


def test_second_list_has_smaller_values():
    assert merge_sorted([5, 6, 7], [1, 2, 3]) == [1, 2, 3, 5, 6, 7]


def test_empty_list():
    assert merge_sorted([], [1, 2, 3]) == [1, 2, 3]
