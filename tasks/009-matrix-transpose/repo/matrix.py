def transpose(matrix: list[list[int]]) -> list[list[int]]:
    """Bir matrisin devriğini (satır/sütunların yer değiştirdiği hâlini) döner."""
    rows = len(matrix)
    return [[matrix[i][j] for i in range(rows)] for j in range(rows)]
