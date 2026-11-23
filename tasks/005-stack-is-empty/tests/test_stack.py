from stack import Stack


def test_new_stack_is_empty():
    assert Stack().is_empty() is True


def test_stack_with_items_is_not_empty():
    s = Stack()
    s.push(1)
    assert s.is_empty() is False


def test_is_empty_after_popping_everything():
    s = Stack()
    s.push(1)
    s.pop()
    assert s.is_empty() is True


def test_push_pop_peek_still_work():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.peek() == 2
    assert s.pop() == 2
    assert s.peek() == 1
