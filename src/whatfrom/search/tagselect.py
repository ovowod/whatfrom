# src/whatfrom/search/tagselect.py
"""후보로 쓸 태그를 고르는 규칙.

DB를 모른다. 값만 받아 값을 돌려준다.
"""

import re
from dataclasses import dataclass
from datetime import datetime

# 프리릴리스 표기. 파이썬 이미지는 PEP 440을 따라 3.15.0a8, 3.15.0b1처럼
# 숫자 뒤에 알파벳이 바로 붙는다. rc만 걸러서는 알파와 베타를 놓친다.
# -m03처럼 마일스톤 빌드를 쓰는 리포(redis)도 있고, ubuntu는 devel이라는
# 이름으로 개발 브랜치를 낸다.
PRERELEASE = re.compile(r"[0-9](a|b|rc)[0-9]|-rc|-m[0-9]+|^devel$")

# 마이너 이동 별칭. 3.14와 3.14-slim은 통과하고
# latest, 3, 3.14.7-slim은 걸린다.
MINOR_ALIAS = re.compile(r"^(\d+)\.(\d+)(?:-.+)?$")


@dataclass(frozen=True)
class TagRef:
    """태그 선택에 필요한 것만 담는다. DB 모델을 그대로 넘기지 않는다."""

    id: int
    tag: str
    manifest_digest: str | None
    last_pushed_at: datetime | None


def select_tags(tags: list[TagRef], limit: int) -> list[TagRef]:
    """후보로 쓸 태그를 고른다.

    프리릴리스를 빼고, 마이너 이동 별칭 형태만 남기고, 같은 이미지를 가리키는
    태그를 접은 뒤 버전 내림차순으로 자른다.

    프리릴리스 제외는 이름뿐 아니라 digest로도 전파된다. ubuntu의 26.10처럼
    이름만 봐서는 안정 버전 같아도, devel처럼 이름으로 걸러진 프리릴리스와
    같은 digest를 가리키면 함께 빠진다. digest가 None인 태그는 전파의
    대상도, 근거도 되지 않는다.

    tags는 한 리포지토리의 태그여야 한다. 폴백 로직이 리포 단위로 동작하므로
    여러 리포의 태그를 섞어 넘기면 결과가 뒤섞인다.

    이동 별칭이 하나도 없는 리포(debian:bookworm처럼 숫자 버전이 없는 경우)는
    기존 동작인 푸시 시각 내림차순으로 돌아간다. 리포의 태그가 전부
    프리릴리스라 빼고 나면 하나도 안 남는 경우에는, 뺐던 프리릴리스를 그대로
    후보로 돌려준다. 후보가 0개인 것보다는 프리릴리스라도 있는 편이 낫고,
    태그에 딸린 푸시 시각과 digest가 있으니 모델이 그것을 릴리스 후보라고
    설명할 수 있다. 어떤 리포도 후보가 0개가 되지 않게 한다.
    """
    stable = _exclude_prerelease(tags)
    if not stable:
        return _by_recent_push(tags)[:limit]

    versions: dict[int, tuple[int, int]] = {}
    aliases: list[TagRef] = []
    for ref in stable:
        match = MINOR_ALIAS.match(ref.tag)
        if match is not None:
            versions[ref.id] = (int(match.group(1)), int(match.group(2)))
            aliases.append(ref)
    if not aliases:
        return _by_recent_push(stable)[:limit]

    folded = _fold_by_digest(aliases)
    # 버전은 내림차순, 이름 길이와 사전순은 오름차순이라 버전 쪽 부호를 뒤집는다.
    folded.sort(key=lambda ref: (-versions[ref.id][0], -versions[ref.id][1], len(ref.tag), ref.tag))
    return folded[:limit]


def _exclude_prerelease(tags: list[TagRef]) -> list[TagRef]:
    """이름으로 걸러진 프리릴리스와, 그것과 같은 digest를 가리키는 태그를 뺀다.

    ubuntu의 devel과 26.10처럼 개발 브랜치가 안정 버전 이름으로도 배포될 수
    있다. 이름만으로는 26.10을 걸러낼 수 없으니, devel의 digest를 함께 가진
    태그도 프리릴리스로 취급해 뺀다. digest가 None이면 동일성을 알 수
    없으므로 전파의 대상으로도, 근거로도 쓰지 않는다.
    """
    named_ids = {ref.id for ref in tags if PRERELEASE.search(ref.tag)}
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
