from temperature import celsius_to_fahrenheit


def test_freezing_point():
    assert celsius_to_fahrenheit(0) == 32


def test_boiling_point():
    assert celsius_to_fahrenheit(100) == 212


def test_negative_temperature():
    assert celsius_to_fahrenheit(-40) == -40
