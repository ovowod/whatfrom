# src/whatfrom/collect/derive.py
"""태그의 파생 컬럼을 채운다.

이름에서 읽은 값(tagparse)에, 같은 이미지를 가리키는 다른 태그의 값을 보탠다.
python:3.14는 이름에 배포판이 없지만 3.14-trixie와 같은 이미지다.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from whatfrom.collect.tagparse import TagFacts, major_minor, parse_tag
from whatfrom.core.models import ImageTag, ImageVariant
from whatfrom.core.versions import extends_version

# 한 태그의 Linux 플랫폼 (architecture, arch_variant, digest) 집합.
Fingerprint = frozenset[tuple[str, str, str]]


@dataclass(frozen=True)
class Derived:
    """image_tags의 파생 컬럼 다섯 개에 쓸 값."""

    language_version: str | None
    version_major_minor: str | None
    distribution: str | None
    distro_codename: str | None
    variant: str | None


@dataclass(frozen=True)
class Resolution:
    values: dict[str, Derived]
    # 물려받을 값이 엇갈려 None으로 남은 (태그, 컬럼) 칸 수와, 그런 칸이 있는 태그 수.
    conflict_fields: int
    conflict_tags: int


@dataclass(frozen=True)
class DeriveOutcome:
    repository: str
    total: int
    changed: int
    conflict_fields: int
    conflict_tags: int
    with_distribution: int


def _chain_end(versions: set[str]) -> str | None:
    """모든 버전이 한 사슬을 이루면 가장 상세한 것. 아니면 None."""
    for candidate in versions:
        if all(extends_version(other, candidate) for other in versions):
            return candidate
    return None


def _single(values: set[str]) -> tuple[str | None, bool]:
    """(물려받을 값, 엇갈렸는가). 값이 없으면 (None, False)."""
    if len(values) == 1:
        return next(iter(values)), False
    return None, len(values) > 1


def resolve_aliases(
    facts: Mapping[str, TagFacts], fingerprints: Mapping[str, Fingerprint]
) -> Resolution:
    """이름에서 읽은 값에 같은 이미지의 값을 보탠다. DB를 모른다.

    Linux 지문이 같은 태그를 같은 이미지로 본다. Windows 이미지를 함께 담은
    python:3.14는 manifest digest가 3.14-trixie와 달라도 Linux 쪽은 같다.
    지문이 없는 태그(Windows 전용, 플랫폼 정보가 없는 태그)는 묶지 않는다.

    이름에서 나온 값은 덮어쓰지 않는다. 버전만은 같은 이미지의 더 상세한 버전으로
    확장한다. 코드네임은 최종 배포판과 같은 배포판을 적은 태그에서만 가져온다.
    값이 엇갈리면 추측하지 않고 None으로 두고 충돌로 센다.
    변형은 정보가 전혀 없을 때만 full이다.
    """
    groups: dict[Fingerprint, list[str]] = {}
    for tag, fingerprint in fingerprints.items():
        if fingerprint and tag in facts:
            groups.setdefault(fingerprint, []).append(tag)

    values: dict[str, Derived] = {}
    conflict_fields = conflict_tags = 0
    for tag, own in facts.items():
        fingerprint = fingerprints.get(tag)
        group = [facts[other] for other in groups.get(fingerprint, [])] if fingerprint else []
        conflicts = 0

        distribution, codename, variant = own.distribution, own.codename, own.variant
        if distribution is None:
            distribution, clash = _single({f.distribution for f in group if f.distribution})
            conflicts += clash
        if codename is None:
            # 배포판마다 코드네임이 다르다. 같은 지문에 3.14-trixie가 있어도 3.14-alpine이
            # trixie를 가져가면 alpine trixie라는 없는 조합이 된다. 파서는 코드네임을 늘
            # 배포판과 함께 채우므로, 배포판을 모르면 여기서 가져올 코드네임도 없다.
            codename, clash = _single(
                {f.codename for f in group if f.codename and f.distribution == distribution}
            )
            conflicts += clash
        if variant is None:
            variant, clash = _single({f.variant for f in group if f.variant})
            conflicts += clash
            if variant is None and not clash:
                variant = "full"

        versions = {f.version for f in group if f.version}
        version = own.version
        if version is not None:
            version = _chain_end({v for v in versions if extends_version(version, v)}) or version
        elif versions:
            version = _chain_end(versions)
            conflicts += version is None

        values[tag] = Derived(version, major_minor(version), distribution, codename, variant)
        conflict_fields += conflicts
        conflict_tags += conflicts > 0

    return Resolution(values, conflict_fields, conflict_tags)


def derive_repository(session: Session, repository: str) -> DeriveOutcome:
    """리포지토리의 태그 전부에 파생 값을 채운다. 다시 실행하면 바뀌는 행이 없다.

    태그 수와 무관하게 SELECT 두 번과 많아야 UPDATE 한 번이다. 바뀐 행만 쓴다.
    """
    rows = session.execute(
        select(
            ImageTag.id,
            ImageTag.tag,
            ImageTag.language_version,
            ImageTag.version_major_minor,
            ImageTag.distribution,
            ImageTag.distro_codename,
            ImageTag.variant,
        ).where(ImageTag.repository == repository)
    ).all()

    platforms: dict[int, set[tuple[str, str, str]]] = {}
    for tag_id, architecture, arch_variant, digest in session.execute(
        select(
            ImageVariant.tag_id,
            ImageVariant.architecture,
            ImageVariant.arch_variant,
            ImageVariant.digest,
        )
        .join(ImageTag, ImageTag.id == ImageVariant.tag_id)
        .where(ImageTag.repository == repository, ImageVariant.os == "linux")
    ):
        platforms.setdefault(tag_id, set()).add((architecture, arch_variant, digest))

    resolution = resolve_aliases(
        {row.tag: parse_tag(row.tag, repository) for row in rows},
        {row.tag: frozenset(platforms.get(row.id, ())) for row in rows},
    )

    changed = []
    for row in rows:
        new = resolution.values[row.tag]
        current = Derived(
            row.language_version,
            row.version_major_minor,
            row.distribution,
            row.distro_codename,
            row.variant,
        )
        if new != current:
            changed.append({"id": row.id, **asdict(new)})
    if changed:
        session.execute(update(ImageTag), changed)

    return DeriveOutcome(
        repository=repository,
        total=len(rows),
        changed=len(changed),
        conflict_fields=resolution.conflict_fields,
        conflict_tags=resolution.conflict_tags,
        with_distribution=sum(v.distribution is not None for v in resolution.values.values()),
    )
