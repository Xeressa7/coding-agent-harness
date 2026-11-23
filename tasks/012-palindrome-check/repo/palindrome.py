def is_palindrome(text: str) -> bool:
    """Metnin, boşluk ve büyük/küçük harf farkı gözetmeden palindrom olup olmadığını söyler."""
    return text == text[::-1]
