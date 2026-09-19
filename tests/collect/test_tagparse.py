# tests/collect/test_tagparse.py
"""태그 이름 파서. 실제 수집된 태그 모양에서 고른 테이블 주도 테스트다."""

import pytest

from whatfrom.collect.tagparse import TagFacts, major_minor, parse_tag

# (리포지토리, 태그, 버전, 배포판, 코드네임, 변형)
CASES = [
    # 버전 표기
    ("python", "3.14.7", "3.14.7", None, None, None),
    ("python", "3.14", "3.14", None, None, None),
    ("node", "24.21.0", "24.21.0", None, None, None),
    ("eclipse-temurin", "21.0.12_8-jdk", "21.0.12_8", None, None, "jdk"),
    ("postgres", "18.6", "18.6", None, None, None),
    ("golang", "1.27.1", "1.27.1", None, None, None),
    ("python", "3.15.0rc2", "3.15.0rc2", None, None, None),
    ("postgres", "18beta1", "18beta1", None, None, None),
    ("golang", "1.27rc1", "1.27rc1", None, None, None),
    ("eclipse-temurin", "8u502-b07-jre", "8u502-b07", None, None, "jre"),
    ("python", "3.15-rc", "3.15", None, None, None),
    ("python", "3.15-rc-slim", "3.15", None, None, "slim"),
    # 데비안·우분투 코드네임
    ("python", "3.14-slim-bookworm", "3.14", "debian", "bookworm", "slim"),
    ("python", "3.14-trixie", "3.14", "debian", "trixie", None),
    ("node", "24-trixie-slim", "24", "debian", "trixie", "slim"),
    ("node", "22-bullseye", "22", "debian", "bullseye", None),
    ("redis", "7.4-bookworm", "7.4", "debian", "bookworm", None),
    ("postgres", "18-trixie", "18", "debian", "trixie", None),
    ("eclipse-temurin", "17-jdk-noble", "17", "ubuntu", "noble", "jdk"),
    ("eclipse-temurin", "17-jre-jammy", "17", "ubuntu", "jammy", "jre"),
    ("eclipse-temurin", "21-resolute", "21", "ubuntu", "resolute", None),
    ("nginx", "stable-trixie", None, "debian", "trixie", None),
    ("python", "slim-trixie", None, "debian", "trixie", "slim"),
    # alpine
    ("python", "3.14-alpine3.24", "3.14", "alpine", "3.24", None),
    ("python", "3.14-alpine", "3.14", "alpine", None, None),
    ("eclipse-temurin", "21-jre-alpine", "21", "alpine", None, "jre"),
    ("eclipse-temurin", "8u502-b07-jre-alpine-3.24", "8u502-b07", "alpine", "3.24", "jre"),
    ("nginx", "mainline-alpine3.22-perl", None, "alpine", "3.22", "perl"),
    ("nginx", "stable-alpine-slim", None, "alpine", None, "slim"),
    ("nginx", "1.31-alpine-otel", "1.31", "alpine", None, "otel"),
    ("golang", "tip-20260905-alpine3.23", None, "alpine", "3.23", None),
    ("postgres", "alpine", None, "alpine", None, None),
    # 변형과 그 밖의 베이스
    ("python", "slim", None, None, None, "slim"),
    ("eclipse-temurin", "25-jdk", "25", None, None, "jdk"),
    ("redis", "5-32bit-buster", "5", "debian", "buster", "32bit"),
    ("redis", "6.0-rc1-32bit-buster", "6.0", "debian", "buster", "32bit"),
    ("eclipse-temurin", "21-jre-ubi9-minimal", "21", "ubi", "9", "jre-minimal"),
    ("eclipse-temurin", "21-ubi10-minimal", "21", "ubi", "10", "minimal"),
    (
        "python",
        "3.14-windowsservercore-ltsc2025",
        "3.14",
        "windows",
        "ltsc2025",
        "windowsservercore",
    ),
    ("python", "3.9-windowsservercore-1809", "3.9", "windows", "1809", "windowsservercore"),
    ("eclipse-temurin", "17-jdk-nanoserver", "17", "windows", None, "jdk-nanoserver"),
    ("golang", "1.27-nanoserver-ltsc2022", "1.27", "windows", "ltsc2022", "nanoserver"),
    # 값을 주지 않는 별칭
    ("python", "latest", None, None, None, None),
    ("node", "lts", None, None, None, None),
    ("nginx", "stable", None, None, None, None),
    ("nginx", "mainline", None, None, None, None),
    ("node", "current", None, None, None, None),
    ("node", "krypton", None, None, None, None),
    ("node", "lts-krypton", None, None, None, None),
    ("ubuntu", "rolling", None, "ubuntu", None, None),
    # 개발 줄기, 스위트, 날짜
    ("golang", "tip", None, None, None, None),
    ("golang", "tip-20260905-bookworm", None, "debian", "bookworm", None),
    ("alpine", "edge", None, "alpine", None, None),
    ("debian", "testing", None, "debian", None, None),
    ("debian", "rc-buggy-20250908", None, "debian", None, None),
    ("debian", "oldstable-20260623", None, "debian", None, None),
    ("debian", "sid-20260316-slim", None, "debian", "sid", "slim"),
    # OS 베이스 리포지토리
    ("debian", "13", "13", "debian", None, None),
    ("debian", "13.6-slim", "13.6", "debian", None, "slim"),
    ("debian", "trixie-slim", None, "debian", "trixie", "slim"),
    ("debian", "trixie-20260824", None, "debian", "trixie", None),
    ("ubuntu", "24.04", "24.04", "ubuntu", None, None),
    ("ubuntu", "noble-20250415.1", None, "ubuntu", "noble", None),
    ("ubuntu", "jammy", None, "ubuntu", "jammy", None),
    ("alpine", "3.24", "3.24", "alpine", "3.24", None),
    ("alpine", "3.24.1", "3.24.1", "alpine", "3.24", None),
    ("alpine", "3", "3", "alpine", None, None),
    ("alpine", "20260805", None, "alpine", None, None),
    ("alpine", "latest", None, "alpine", None, None),
]


@pytest.mark.parametrize(
    ("repository", "tag", "version", "distribution", "codename", "variant"), CASES
)
def test_parse_tag(repository, tag, version, distribution, codename, variant):
    assert parse_tag(tag, repository) == TagFacts(version, distribution, codename, variant)


def test_there_are_at_least_fifty_cases():
    """로드맵 F6의 완료 판정이다."""
    assert len(CASES) >= 50


def test_a_debian_codename_outside_an_os_repository_still_names_debian():
    """리포지토리 이름으로 분기하는 곳은 OS 베이스 리포지토리 규칙 하나뿐이다."""
    assert parse_tag("3.13-bookworm", "some-new-image").distribution == "debian"


def test_an_unknown_token_is_ignored_rather_than_guessed():
    assert parse_tag("3.14-mystery", "python") == TagFacts(version="3.14")


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.14.7", "3.14"),
        ("24.21.0", "24.21"),
        ("21.0.12_8", "21.0"),
        ("8u502-b07", "8"),
        ("3.15.0rc2", "3.15"),
        ("18beta1", "18"),
        ("24.04", "24.04"),
        ("13", "13"),
        (None, None),
    ],
)
def test_major_minor(version, expected):
    assert major_minor(version) == expected
