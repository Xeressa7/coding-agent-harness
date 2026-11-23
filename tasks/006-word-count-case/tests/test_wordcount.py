from wordcount import word_count


def test_case_insensitive_counting():
    result = word_count("The cat sat on the mat. THE cat ran.")
    assert result["the"] == 3
    assert result["cat"] == 2


def test_keys_are_lowercase():
    result = word_count("Hello Hello")
    assert "hello" in result and "Hello" not in result


def test_single_occurrence():
    result = word_count("unique word here")
    assert result == {"unique": 1, "word": 1, "here": 1}
