def dedupe(items: list[int]) -> list[int]:
    """Yinelenenleri çıkarır, ilk görüldükleri sırayı korur."""
    return list(set(items))
