# src/whatfrom/collect/tagparse.py
"""태그 이름에서 버전·배포판·변형을 읽는 규칙.

DB를 모른다. 이름에 없는 값은 추측하지 않고 None으로 둔다. 같은 이미지의 다른
태그에서 값을 물려받는 일은 derive.resolve_aliases가 한다.
"""

import re
from dataclasses import dataclass

DEBIAN_CODENAMES = frozenset(
    "buzz rex bo hamm slink potato woody sarge etch lenny squeeze wheezy jessie "
    "stretch buster bullseye bookworm trixie forky duke sid".split()
)
UBUNTU_CODENAMES = frozenset(
    "warty hoary breezy dapper edgy feisty gutsy hardy intrepid jaunty karmic lucid "
    "maverick natty oneiric precise quantal raring saucy trusty utopic vivid wily "
    "xenial yakkety zesty artful bionic cosmic disco eoan focal groovy hirsute impish "
    "jammy kinetic lunar mantic noble oracular plucky questing resolute stonking".split()
)
# 배포판이 아닌 변형 단어. 이름에 나온 순서대로 이어 붙인다.
VARIANT_WORDS = frozenset(
    "slim jre jdk minimal perl otel 32bit windowsservercore nanoserver".split()
)
WINDOWS_WORDS = frozenset({"windowsservercore", "nanoserver"})
# 리포지토리 자체가 배포판인 공식 이미지. 이름에 배포판이 드러나지 않는다.
# 리포지토리 이름을 쓰는 곳은 이 표 하나뿐이다.
OS_REPOSITORIES = frozenset({"debian", "ubuntu", "alpine"})

VERSION_TOKEN = re.compile(r"^\d[0-9A-Za-z._]*$")
DATE_TOKEN = re.compile(r"^\d{8}(\.\d+)?$")
JAVA8_BUILD = re.compile(r"^b\d+$")
ALPINE_TOKEN = re.compile(r"^alpine(\d+\.\d+)?$")
ALPINE_VERSION = re.compile(r"^\d+\.\d+$")
UBI_TOKEN = re.compile(r"^ubi(\d+)$")
LTSC_TOKEN = re.compile(r"^ltsc\d{4}$")
# windowsservercore-1809처럼 ltsc 없이 적힌 Windows Server 버전.
WINDOWS_RELEASE = re.compile(r"^\d{4}$")
MAJOR_MINOR = re.compile(r"^\d+(?:\.\d+)?")


@dataclass(frozen=True)
class TagFacts:
    """이름에서 읽은 값. 없으면 None이다. variant가 None이면 변형 단어가 없다는 뜻이다."""

    version: str | None = None
    distribution: str | None = None
    codename: str | None = None
    variant: str | None = None


def major_minor(version: str | None) -> str | None:
    """버전 앞부분의 숫자 성분 최대 두 개. 8u502-b07은 8, 3.15.0rc2는 3.15다."""
    if version is None:
        return None
    match = MAJOR_MINOR.match(version)
    return match.group(0) if match else None


def parse_tag(tag: str, repository: str) -> TagFacts:
    tokens = tag.split("-")
    version: str | None = None
    distribution: str | None = None
    codename: str | None = None
    variants: list[str] = []

    index = 0
    if tokens and VERSION_TOKEN.match(tokens[0]) and not DATE_TOKEN.match(tokens[0]):
        version = tokens[0]
        index = 1
        # temurin 8의 8u502-b07. 빌드 번호까지가 버전이다.
        if index < len(tokens) and JAVA8_BUILD.match(tokens[index]):
            version = f"{version}-{tokens[index]}"
            index += 1

    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in DEBIAN_CODENAMES:
            distribution, codename = "debian", token
        elif token in UBUNTU_CODENAMES:
            distribution, codename = "ubuntu", token
        elif alpine := ALPINE_TOKEN.match(token):
            distribution, codename = "alpine", alpine.group(1)
            # temurin 8은 alpine-3.24처럼 버전을 떼어 적는다.
            if codename is None and index < len(tokens) and ALPINE_VERSION.match(tokens[index]):
                codename = tokens[index]
                index += 1
        elif ubi := UBI_TOKEN.match(token):
            distribution, codename = "ubi", ubi.group(1)
        elif LTSC_TOKEN.match(token) or (
            distribution == "windows" and WINDOWS_RELEASE.match(token)
        ):
            codename = token
        elif token in VARIANT_WORDS:
            variants.append(token)
            if token in WINDOWS_WORDS:
                distribution = "windows"
        # 날짜, 줄기 별칭(latest·lts·stable·mainline), 데비안 스위트, 개발 줄기(tip·edge),
        # 프리릴리스 표시(rc)는 값을 주지 않는다. 모르는 토큰도 무시한다.

    if repository in OS_REPOSITORIES:
        distribution = repository
        if repository == "alpine" and codename is None and version is not None:
            # alpine:3.24.1의 릴리스 브랜치는 3.24다. alpine:3처럼 성분이 하나면 모른다.
            parts = version.split(".")
            if len(parts) >= 2:
                codename = ".".join(parts[:2])

    return TagFacts(
        version=version,
        distribution=distribution,
        codename=codename,
        variant="-".join(variants) if variants else None,
    )
