# src/whatfrom/search/tagselect.py
"""후보로 쓸 태그를 고르는 규칙.

DB를 모른다. 값만 받아 값을 돌려준다. 리포지토리 이름으로 분기하지 않는다.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# 프리릴리스. 파이썬은 PEP 440 표기라 3.15.0a8, 3.15.0b1처럼 숫자 뒤에 알파벳이
# 바로 붙는다. redis는 -m03 마일스톤, postgres는 19beta3, golang은 tip,
# debian은 sid·testing·backports, alpine은 edge로 개발 판을 낸다.
# 이름 앞에 오는 단어는 경계까지 맞춘다. testing 같은 단어가 다른 이름 중간에
# 우연히 들어가는 경우를 피한다.
PRERELEASE = re.compile(
    r"[0-9](a|b|rc)[0-9]|-rc|-m[0-9]+|beta|alpha|backports"
    r"|^(devel|tip|sid|testing|unstable|experimental|rc-buggy|edge)(-|$)"
)
# alpine의 20260805는 edge와 같은 이미지를 가리키는 날짜 스냅샷이다.
DATE_SNAPSHOT = re.compile(r"^\d{8}(\.\d+)?$")
# Linux 컨테이너만 추천한다. 이름에 이 단어가 든 태그는 어떤 경로로도 후보가 되지 않는다.
# 일반 태그 안에 섞인 Windows 변종은 태그 선택과 무관하게 플랫폼 목록에 남는다.
WINDOWS_ONLY = re.compile(r"windowsservercore|nanoserver")
VERSION = re.compile(r"^\d+(?:\.\d+)*")
# 가장 최근에 푸시된 줄기보다 이만큼 넘게 뒤처진 줄기는 지원이 끝난 것으로 본다.
SUPPORT_GAP = timedelta(days=60)
MAX_LINE_DEPTH = 3


@dataclass(frozen=True)
class TagRef:
    """태그 선택에 필요한 것만 담는다. DB 모델을 그대로 넘기지 않는다."""

    id: int
    tag: str
    manifest_digest: str | None
    last_pushed_at: datetime | None


def select_tags(tags: list[TagRef], limit: int) -> list[TagRef]:
    """후보로 쓸 태그를 고른다.

    tags는 한 리포지토리의 태그여야 한다. 줄기 단위와 지원 여부를 리포지토리
    안에서 상대적으로 판단하기 때문이다.

    1. Windows 전용 태그를 뺀다. 되살리지 않는다.
    2. 프리릴리스와 날짜 스냅샷, 그리고 이들과 같은 digest를 가리키는 태그를 뺀다.
       이것만 남는 리포지토리는 이것들로 폴백한다. 후보가 없는 것보다 낫다.
    3. 별칭 태그가 있는 릴리스 줄기를 찾고, 지원이 끝난 줄기를 뺀다. 지원 줄기 안에서도
       SUPPORT_GAP 넘게 뒤처진 변형 별칭은 뺀다.
    4. 줄기를 최신순으로 번갈아 돌며 limit까지 채운다. 한 줄기로 몰아 채우면
       최신 줄기의 변형만 들어가 LTS 줄기가 빠진다.
    5. 줄기가 없으면 최근 푸시 순으로 고른다.
    """
    linux = [ref for ref in tags if not WINDOWS_ONLY.search(ref.tag)]
    stable = _exclude_prerelease(linux)
    if not stable:
        return _by_recent_push(_fold_by_digest(linux))[:limit]

    lines = _supported_release_lines(stable)
    if not lines:
        return _by_recent_push(_fold_by_digest(stable))[:limit]
    return _interleave(lines, limit)


def _exclude_prerelease(tags: list[TagRef]) -> list[TagRef]:
    """이름으로 걸러진 태그와, 그것과 같은 digest를 가리키는 태그를 뺀다.

    ubuntu의 devel과 26.10처럼 개발 브랜치가 안정 버전 이름으로도 배포된다.
    digest가 None이면 동일성을 알 수 없으므로 전파의 대상으로도, 근거로도 쓰지 않는다.
    """
    named_ids = {
        ref.id for ref in tags if PRERELEASE.search(ref.tag) or DATE_SNAPSHOT.match(ref.tag)
    }
    prerelease_digests = {
        ref.manifest_digest
        for ref in tags
        if ref.id in named_ids and ref.manifest_digest is not None
    }
    return [
        ref
        for ref in tags
        if ref.id not in named_ids
        and not (ref.manifest_digest is not None and ref.manifest_digest in prerelease_digests)
    ]


def _line_of(tag: str, depth: int) -> str | None:
    """태그 버전의 앞 depth개 성분. 25.0.4_7-jdk의 1자리 줄기는 25다."""
    match = VERSION.match(tag)
    if match is None:
        return None
    parts = match.group(0).split(".")
    if len(parts) < depth:
        return None
    return ".".join(parts[:depth])


def _is_alias(tag: str, line: str) -> bool:
    """줄기를 따라 움직이는 별칭인가. 24와 24-alpine은 맞고, 24.1과 24-1은 아니다."""
    if tag == line:
        return True
    if not tag.startswith(line + "-"):
        return False
    rest = tag[len(line) + 1 :]
    return bool(rest) and not rest[0].isdigit()


def _line_depth(tags: list[TagRef]) -> int | None:
    """줄기 단위(자릿수)를 정한다. 지원 여부는 보지 않는다.

    별칭 줄기가 둘 이상인 가장 짧은 자릿수를 쓰고, 없으면 하나라도 있는 가장 짧은
    자릿수를 쓴다. python은 메이저 3 하나뿐이라 마이너 단위가 되고, node는 26·24·22가
    있어 메이저 단위가 된다. 지원 판정을 여기에 섞으면, 지원 줄기가 하나뿐인
    리포지토리가 폴백으로 떨어져 끝난 줄기까지 되살아난다.
    """
    alias_lines: dict[int, set[str]] = {}
    for depth in range(1, MAX_LINE_DEPTH + 1):
        found: set[str] = set()
        for ref in tags:
            line = _line_of(ref.tag, depth)
            if line is not None and _is_alias(ref.tag, line):
                found.add(line)
        alias_lines[depth] = found
    for needed in (2, 1):
        for depth in range(1, MAX_LINE_DEPTH + 1):
            if len(alias_lines[depth]) >= needed:
                return depth
    return None


def _supported_release_lines(tags: list[TagRef]) -> list[list[TagRef]]:
    """지원 중인 줄기별 후보 목록. 줄기는 버전 내림차순, 줄기 안은 이름 짧은 순."""
    depth = _line_depth(tags)
    if depth is None:
        return []

    latest_push: dict[str, datetime | None] = {}
    aliases: dict[str, list[TagRef]] = {}
    for ref in tags:
        line = _line_of(ref.tag, depth)
        if line is None:
            continue
        if ref.last_pushed_at is not None:
            current = latest_push.get(line)
            if current is None or ref.last_pushed_at > current:
                latest_push[line] = ref.last_pushed_at
        else:
            latest_push.setdefault(line, None)
        if _is_alias(ref.tag, line):
            aliases.setdefault(line, []).append(ref)

    supported = _supported({line: latest_push.get(line) for line in aliases})
    kept_aliases = [
        ref
        for line in supported
        for ref in _drop_stale_variants(aliases[line], latest_push.get(line))
    ]
    folded = _fold_by_digest(kept_aliases)
    by_line: dict[str, list[TagRef]] = {}
    for ref in folded:
        line = _line_of(ref.tag, depth)
        assert line is not None
        by_line.setdefault(line, []).append(ref)

    ordered = sorted(
        by_line, key=lambda line: [int(part) for part in line.split(".")], reverse=True
    )
    return [sorted(by_line[line], key=lambda ref: (len(ref.tag), ref.tag)) for line in ordered]


def _supported(latest_push: dict[str, datetime | None]) -> set[str]:
    """가장 최근 줄기보다 SUPPORT_GAP 넘게 뒤처진 줄기를 뺀다.

    푸시 시각을 아는 줄기가 하나라도 있으면, 시각을 모르는 줄기는 뺀다. 판단할 근거가
    없는 줄기를 되살리지 않는다. 모든 줄기의 시각을 모르면 판정 자체를 건너뛴다.
    """
    known = [pushed for pushed in latest_push.values() if pushed is not None]
    if not known:
        return set(latest_push)
    newest = max(known)
    return {
        line
        for line, pushed in latest_push.items()
        if pushed is not None and newest - pushed <= SUPPORT_GAP
    }


def _drop_stale_variants(refs: list[TagRef], line_latest: datetime | None) -> list[TagRef]:
    """줄기 안에서 SUPPORT_GAP 넘게 뒤처진 변형 별칭을 뺀다.

    줄기는 계속 푸시돼도 그 안의 변형은 빌드가 끊길 수 있다. 실측(2026-09-17)에서
    줄기 단위 판정만으로는 redis 후보 20개 중 14개가 이런 정체된 변형이었다
    (6-buster 1,785일, 7-alpine3.15 1,581일 등). 줄기 안에 푸시 시각을 아는 태그가
    하나도 없으면(line_latest가 None) 판정을 건너뛴다.
    """
    if line_latest is None:
        return refs
    return [
        ref
        for ref in refs
        if ref.last_pushed_at is not None and line_latest - ref.last_pushed_at <= SUPPORT_GAP
    ]


def _interleave(lines: list[list[TagRef]], limit: int) -> list[TagRef]:
    queues = [list(line) for line in lines]
    picked: list[TagRef] = []
    while len(picked) < limit and any(queues):
        for queue in queues:
            if queue and len(picked) < limit:
                picked.append(queue.pop(0))
    return picked


def _fold_by_digest(refs: list[TagRef]) -> list[TagRef]:
    """같은 digest를 가리키는 태그 중 이름이 짧은 것만 남긴다.

    3.14-alpine3.24가 3.14-alpine에 흡수된다. 길이가 같으면 사전순 앞선 것.
    digest가 None이면 동일성을 알 수 없으므로 접지 않고 각각 남긴다.
    """
    best: dict[str, TagRef] = {}
    unknown: list[TagRef] = []
    for ref in refs:
        if ref.manifest_digest is None:
            unknown.append(ref)
            continue
        current = best.get(ref.manifest_digest)
        if current is None or (len(ref.tag), ref.tag) < (len(current.tag), current.tag):
            best[ref.manifest_digest] = ref
    return list(best.values()) + unknown


def _by_recent_push(refs: list[TagRef]) -> list[TagRef]:
    """푸시 시각 내림차순. 시각이 없는 것은 맨 뒤로."""
    dated = [ref for ref in refs if ref.last_pushed_at is not None]
    undated = [ref for ref in refs if ref.last_pushed_at is None]
    # 두 번 정렬한다. 파이썬 정렬이 안정적이라 시각이 같으면 이름순이 유지된다.
    dated.sort(key=lambda ref: ref.tag)
    dated.sort(key=lambda ref: ref.last_pushed_at, reverse=True)
    undated.sort(key=lambda ref: ref.tag)
    return dated + undated
