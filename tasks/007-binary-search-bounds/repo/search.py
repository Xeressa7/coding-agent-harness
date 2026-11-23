def binary_search(items: list[int], target: int) -> int:
    """Sıralı bir listede target'ın indeksini arar; yoksa -1 döner."""
    low, high = 0, len(items) - 2
    while low <= high:
        mid = (low + high) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
