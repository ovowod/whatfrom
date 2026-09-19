# src/whatfrom/core/versions.py
"""버전 문자열의 앞뒤 관계. 수집(collect.derive)과 채점(eval.scoring)이 같은 경계를 쓴다."""

# 3.14.7의 ".", 21.0.12_8의 "_", 8u502의 "u", 8u502-b07과 3.13-slim의 "-".
VERSION_BOUNDARIES = "._u-"


def extends_version(base: str, detailed: str) -> bool:
    """detailed가 base와 같거나, base 뒤에 경계를 두고 더 적은 것인가.

    3.14 → 3.14.7, 21.0.12 → 21.0.12_8, 8 → 8u502-b07, 3.13 → 3.13-slim은 이어진다.
    3.1 → 3.14.7, 8 → 80은 이어지지 않는다. 숫자가 바로 붙으면 다른 버전이다.
    """
    if detailed == base:
        return True
    return detailed.startswith(base) and detailed[len(base)] in VERSION_BOUNDARIES
