from palindrome import is_palindrome


def test_simple_lowercase_palindrome():
    assert is_palindrome("anna") is True


def test_mixed_case_palindrome():
    assert is_palindrome("Anna") is True


def test_palindrome_with_spaces_and_case():
    assert is_palindrome("Madam Im Adam") is True


def test_non_palindrome():
    assert is_palindrome("python") is False
