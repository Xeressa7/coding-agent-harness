def fibonacci(n: int) -> int:
    """n. Fibonacci sayısını döner (fibonacci(0) == 0, fibonacci(1) == fibonacci(2) == 1, ...)."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return b
