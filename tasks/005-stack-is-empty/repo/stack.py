class Stack:
    """Basit bir LIFO yığın."""

    def __init__(self) -> None:
        self._items: list[int] = []

    def push(self, item: int) -> None:
        self._items.append(item)

    def pop(self) -> int:
        return self._items.pop()

    def peek(self) -> int:
        return self._items[-1]

    def is_empty(self) -> bool:
        return self._items is None
