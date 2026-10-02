from whatfrom.core.contracts import split_image


def test_split_image_splits_at_the_first_colon() -> None:
    assert split_image("python:3.13-slim") == ("python", "3.13-slim")
    assert split_image("a:b:c") == ("a", "b:c")


def test_split_image_returns_none_without_a_colon() -> None:
    assert split_image("python") is None
