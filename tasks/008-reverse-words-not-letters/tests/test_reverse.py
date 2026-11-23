from reverse import reverse_words


def test_reverses_word_order_not_letters():
    assert reverse_words("merhaba dunya") == "dunya merhaba"


def test_single_word_is_unchanged():
    assert reverse_words("python") == "python"


def test_three_words():
    assert reverse_words("bir iki uc") == "uc iki bir"
