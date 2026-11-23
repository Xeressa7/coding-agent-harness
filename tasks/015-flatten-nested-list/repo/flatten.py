def flatten(nested: list) -> list:
    """İç içe (herhangi bir derinlikte) listeleri düzleştirip tek bir düz listeye çevirir."""
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(item)
        else:
            result.append(item)
    return result
