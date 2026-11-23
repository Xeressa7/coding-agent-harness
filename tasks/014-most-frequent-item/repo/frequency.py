def most_frequent(items: list[str]) -> str:
    """Listede en sık geçen elemanı döner."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return max(counts)
