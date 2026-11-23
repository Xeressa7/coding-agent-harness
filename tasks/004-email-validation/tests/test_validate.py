from validate import is_valid_email


def test_valid_addresses():
    assert is_valid_email("user@example.com") is True
    assert is_valid_email("a.b@sub.example.org") is True


def test_missing_at_or_empty_parts():
    assert is_valid_email("userexample.com") is False
    assert is_valid_email("@example.com") is False
    assert is_valid_email("user@") is False


def test_multiple_at_signs():
    assert is_valid_email("user@@example.com") is False
    assert is_valid_email("us@er@example.com") is False


def test_domain_without_dot():
    assert is_valid_email("user@examplecom") is False
