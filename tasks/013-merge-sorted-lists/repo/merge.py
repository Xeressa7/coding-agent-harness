def merge_sorted(a: list[int], b: list[int]) -> list[int]:
    """İki sıralı listeyi tek bir sıralı listede birleştirir."""
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] < b[j]:
            result.append(a[i])
            i += 1
        else:
            result.append(a[i])
            i += 1
    result.extend(a[i:])
    result.extend(b[j:])
    return result
