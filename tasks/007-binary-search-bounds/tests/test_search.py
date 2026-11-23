from search import binary_search


def test_finds_last_element():
    assert binary_search([1, 2, 3, 4, 5], 5) == 4


def test_finds_first_and_middle():
    items = [1, 3, 5, 7, 9, 11]
    assert binary_search(items, 1) == 0
    assert binary_search(items, 7) == 3


def test_missing_value_returns_minus_one():
    assert binary_search([1, 2, 3], 10) == -1


def test_single_element_list():
    assert binary_search([42], 42) == 0
