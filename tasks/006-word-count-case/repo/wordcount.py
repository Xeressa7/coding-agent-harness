def word_count(text: str) -> dict[str, int]:
    """Metindeki her kelimenin kaç kez geçtiğini sayar (büyük/küçük harf duyarsız olmalı)."""
    counts: dict[str, int] = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    return counts
