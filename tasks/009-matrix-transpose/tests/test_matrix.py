from matrix import transpose


def test_square_matrix():
    assert transpose([[1, 2], [3, 4]]) == [[1, 3], [2, 4]]


def test_non_square_more_columns_than_rows():
    assert transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]


def test_non_square_more_rows_than_columns():
    assert transpose([[1, 2], [3, 4], [5, 6]]) == [[1, 3, 5], [2, 4, 6]]
