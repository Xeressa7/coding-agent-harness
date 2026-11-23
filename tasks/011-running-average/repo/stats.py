def running_averages(values: list[float]) -> list[float]:
    """Her indekse kadarki elemanların (kümülatif) ortalamasını sırayla bir liste olarak döner."""
    result = []
    total = 0.0
    for v in values:
        total += v
        result.append(total / len(values))
    return result
