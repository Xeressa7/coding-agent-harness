from dedupe import dedupe


def test_preserves_first_occurrence_order():
    assert dedupe([3, 1, 3, 2, 1, 4]) == [3, 1, 2, 4]


def test_no_duplicates_is_unchanged():
    assert dedupe([5, 6, 7]) == [5, 6, 7]


def test_empty_list():
    assert dedupe([]) == []
