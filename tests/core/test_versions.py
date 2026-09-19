# tests/core/test_versions.py
import pytest

from whatfrom.core.versions import extends_version


@pytest.mark.parametrize(
    ("base", "detailed", "expected"),
    [
        ("3.14", "3.14", True),
        ("3.14", "3.14.7", True),
        ("21.0.12", "21.0.12_8", True),
        ("8", "8u502", True),
        ("8u502", "8u502-b07", True),
        ("8", "8u502-b07", True),
        ("3.13", "3.13-slim", True),
        ("3.1", "3.14.7", False),
        ("8", "80", False),
        ("2", "20-alpine", False),
        ("3.14.7", "3.14", False),
    ],
)
def test_extends_version(base, detailed, expected):
    assert extends_version(base, detailed) is expected
